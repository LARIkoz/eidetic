#!/usr/bin/env python3
"""Eidetic v2.0 — Vector embeddings for hybrid search.

Generates embeddings for all memory chunks using fastembed (ONNX).
Stores in vectors.db alongside index.db. Used as fallback when FTS5 returns < 3 results.

v5.2: switched to multilingual model for cross-language search (RU queries → EN memories).
v6:   multilingual-e5-large (1024d). RU/fuzzy recall@3 25%->67% vs MiniLM-384 (measured).
      e5 REQUIRES "query: "/"passage: " prefixes — added in search() and embed_texts().
"""

import json
import hashlib
import os
import sqlite3
import sys
import time

# --- Embedding profiles: model + dim + retrieval prefixes ---------------------
# The embedder is config-driven so an English-only corpus can opt into a smaller,
# faster model. Selection order: env EIDETIC_EMBED_PROFILE, else the
# `.embed_profile` file at the memory-system root, else "multilingual" (the
# default — zero behaviour change vs the hardcoded e5 setup).
#
# Switching profiles changes model+dim, so the vectors.db model/dim stamp
# mismatches on the next run: search degrades LOUDLY to FTS and the guard/doctor
# prompt `index.sh --full`, which rebuilds + restamps under the new profile.
#
# Prefixes are model-specific and getting them wrong quietly halves recall, so
# each profile carries its own: e5 needs "query: "/"passage: "; bge-en uses an
# asymmetric query instruction and no passage prefix.
PROFILES = {
    "multilingual": {
        "model": "intfloat/multilingual-e5-large", "dim": 1024,
        "query_prefix": "query: ", "passage_prefix": "passage: ",
    },
    "english": {
        "model": "BAAI/bge-small-en-v1.5", "dim": 384,
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "passage_prefix": "",
    },
}


def _active_profile(_config_path=None):
    name = os.environ.get("EIDETIC_EMBED_PROFILE", "").strip()
    if not name:
        cfg = _config_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".embed_profile")
        try:
            with open(cfg, encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            name = ""
    return name if name in PROFILES else "multilingual"


EMBED_PROFILE = _active_profile()
MODEL_NAME = PROFILES[EMBED_PROFILE]["model"]
VECTOR_DIM = PROFILES[EMBED_PROFILE]["dim"]
QUERY_PREFIX = PROFILES[EMBED_PROFILE]["query_prefix"]
PASSAGE_PREFIX = PROFILES[EMBED_PROFILE]["passage_prefix"]

# fastembed defaults its model cache to TMPDIR (/var/folders/.../T), which macOS
# periodically purges — silently evicting the ~2GB e5 weights and breaking all
# vector search until a manual reindex. Pin to a persistent, env-overridable cache.
FASTEMBED_CACHE = os.environ.get("FASTEMBED_CACHE_PATH") or os.path.expanduser("~/.cache/fastembed")

# Bumped whenever content_hash()'s formula changes. Stamped into vectors.db meta
# by run_full; the search-time guard treats a model-stamped db whose hash_scheme
# is missing or different as "stale hashes" and degrades LOUDLY to FTS (suggest
# reindex) instead of silently dropping every vector on a hash mismatch.
HASH_SCHEME = "trunc500-v2"

# The exact fastembed release the vectors were built with. A fastembed bump can
# silently change a model's pooling (e5 switched CLS->mean in 0.6+), producing a
# DIFFERENT embedding geometry under the SAME model/dim — so model+dim+hash_scheme
# cannot detect it and cosines across the two builds are meaningless. Stamped by
# run_full; the search-time guard degrades LOUDLY to FTS (suggest --full) when the
# live fastembed differs from the stamped one, instead of silently corrupting
# every cosine. Keep FASTEMBED_PIN in sync with install.sh + doctor.sh.
FASTEMBED_PIN = "0.8.0"


def _fastembed_version():
    """Live fastembed version, or None if fastembed is absent/unimportable."""
    try:
        import fastembed
        return getattr(fastembed, "__version__", None)
    except Exception:
        return None


_model = None
_swept_coreml = False


def _sweep_orphan_coreml_caches(max_age_s=7200, cap=500):
    """Self-heal the CoreML EP temp leak.

    onnxruntime's CoreMLExecutionProvider compiles the model to a
    ~1 GB `$TMPDIR/onnxruntime-*.mlmodelc` bundle on every process and never
    removes it — a killed embed (OOM on --full) leaks it for sure, and macOS's
    own tmp-reaper only runs on reboot after 3 idle days. On a box that embeds
    per-prompt this piled up to tens of thousands of dirs / hundreds of GB.

    Sweep orphans (not touched for >max_age_s, so an in-flight compile in a
    sibling worker is never hit) once per process, best-effort — embedding must
    never fail because cleanup did. Capped so a large backlog drains over
    several runs instead of blocking one embed on hundreds of rmtrees."""
    import shutil
    import tempfile
    now = time.time()
    removed = 0
    try:
        for entry in os.scandir(tempfile.gettempdir()):
            if removed >= cap:
                break
            name = entry.name
            if not (name.startswith("onnxruntime-") and name.endswith(".mlmodelc")):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if now - entry.stat().st_mtime <= max_age_s:
                    continue
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


def _embed_providers():
    """ONNX execution providers for fastembed. On Apple Silicon, CoreML (GPU/ANE)
    embeds e5-large ~5-10x faster than the CPU default — the difference between a
    slow Mac taking ~45 min and ~1 min for a full re-embed. Env-overridable
    (EIDETIC_EMBED_PROVIDERS, comma-separated); returns None elsewhere (CPU default)."""
    env = os.environ.get("EIDETIC_EMBED_PROVIDERS")
    if env is not None:
        return [p.strip() for p in env.split(",") if p.strip()] or None
    try:
        if sys.platform == "darwin" and os.uname().machine == "arm64":
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    except Exception:
        pass
    return None


def get_model():
    global _model, _swept_coreml
    if _model is None:
        if not _swept_coreml:
            _swept_coreml = True
            _sweep_orphan_coreml_caches()
        from fastembed import TextEmbedding
        providers = _embed_providers()
        try:
            _model = (TextEmbedding(MODEL_NAME, cache_dir=FASTEMBED_CACHE, providers=providers)
                      if providers else TextEmbedding(MODEL_NAME, cache_dir=FASTEMBED_CACHE))
        except Exception:
            # A provider (e.g. CoreML) failed to init or this fastembed lacks the
            # providers arg → fall back to the pure-CPU default; never block embedding.
            _model = TextEmbedding(MODEL_NAME, cache_dir=FASTEMBED_CACHE)
    return _model


def init_vector_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            chunk_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            name TEXT,
            section_heading TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            embedding BLOB NOT NULL,
            mtime INTEGER
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(vectors)")}
    for column, statement in {
        "section_heading": "ALTER TABLE vectors ADD COLUMN section_heading TEXT DEFAULT ''",
        "content_hash": "ALTER TABLE vectors ADD COLUMN content_hash TEXT DEFAULT ''",
    }.items():
        if column not in existing:
            conn.execute(statement)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_path ON vectors(path)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    return conn


def _vector_meta_ok(vec_conn):
    """Guard against silent model/dim drift.

    Vectors built by a different model or dimension produce meaningless cosines,
    and a dimension mismatch is skipped row-by-row in search() (shape guard) so
    the whole result set silently collapses to empty with no signal. Compare the
    vectors.db stamp (written by run_full) against this module's MODEL_NAME /
    VECTOR_DIM and warn loudly on mismatch instead of failing silently.

    Returns True when the stamp matches OR is absent (pre-stamp db — cannot
    verify, stay backward-compatible); False on a real mismatch.
    """
    try:
        meta = dict(vec_conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return True  # no meta table (old db) → cannot verify, do not block
    stored_model = meta.get("model")
    stored_dim = meta.get("dim")
    if not stored_model and not stored_dim:
        return True  # unstamped db → cannot verify
    mismatch = []
    if stored_model and stored_model != MODEL_NAME:
        mismatch.append(f"model {stored_model!r} != expected {MODEL_NAME!r}")
    if stored_dim and str(stored_dim) != str(VECTOR_DIM):
        mismatch.append(f"dim {stored_dim} != expected {VECTOR_DIM}")
    stored_scheme = meta.get("hash_scheme")
    # A model-stamped db with no/old hash_scheme carries content hashes from the
    # previous content_hash() formula; after the formula change they won't match
    # the query-time recompute and every vector would be silently dropped. Treat
    # it as a real mismatch so we degrade loudly and prompt a reindex.
    if stored_model and (not stored_scheme or stored_scheme != HASH_SCHEME):
        mismatch.append(
            f"hash_scheme {stored_scheme or 'none'} != expected {HASH_SCHEME} "
            "(content_hash formula changed — run index.sh --full)"
        )
    # fastembed pooling/geometry drift: same model+dim can embed differently after
    # a fastembed bump (e5 CLS->mean). Only flag when BOTH versions are known and
    # differ — an absent stamp is a pre-stamp db (backward-compatible), and no live
    # fastembed means vector search is unavailable anyway.
    stored_fev = meta.get("fastembed_version")
    live_fev = _fastembed_version()
    if stored_model and stored_fev and live_fev and stored_fev != live_fev:
        mismatch.append(
            f"fastembed {stored_fev} != live {live_fev} "
            "(embedder pooling/geometry may differ — run index.sh --full)"
        )
    if mismatch:
        print(
            "WARNING: vectors.db built by a different embedder ("
            + "; ".join(mismatch)
            + "); vector search suppressed (degrading to FTS). Reindex: "
            "~/.claude/memory-system/bin/index.sh --full",
            file=sys.stderr,
        )
        return False
    return True


def embedding_text(name, desc, content, heading):
    parts = [name or "", desc or "", heading or ""]
    body = (content or "")[:500]
    return " ".join(p for p in parts + [body] if p).strip() or "empty"


def content_hash(name, desc, content, heading):
    # Hash exactly what embedding_text() feeds the model (content truncated to
    # 500) — NOT the full content. Otherwise an edit BEYOND char 500, which does
    # not change the embedding, changes the hash and the query-time guard
    # (search_impl) silently drops an otherwise-valid vector. Keep [:500] in sync
    # with embedding_text(). Formula version is tracked by HASH_SCHEME.
    body = (content or "")[:500]
    payload = "\0".join([name or "", desc or "", heading or "", body])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def embed_texts(texts):
    import numpy as np

    model = get_model()
    # Indexed documents get the active profile's passage prefix (e5 "passage: ";
    # bge-en none) — fastembed does not add it.
    embeddings = list(model.embed([PASSAGE_PREFIX + t for t in texts]))
    return [np.array(e, dtype=np.float32).tobytes() for e in embeddings]


def run_full(index_db_path, vector_db_path):
    import shutil
    backup_path = vector_db_path + ".pre-reindex.bak"
    if os.path.exists(vector_db_path):
        shutil.copy2(vector_db_path, backup_path)

    index_conn = None
    vec_conn = None
    success = False
    total = 0
    t0 = time.time()

    try:
        index_conn = sqlite3.connect(index_db_path)
        index_conn.execute("PRAGMA journal_mode=WAL")
        index_conn.execute("PRAGMA busy_timeout=5000")

        vec_conn = init_vector_db(vector_db_path)

        rows = index_conn.execute("""
            SELECT id, path, name, description, content, section_heading, mtime
            FROM memory_chunks
        """).fetchall()

        vec_conn.execute("DELETE FROM vectors")
        vec_conn.commit()

        batch_size = 64
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            texts = []
            for _, path, name, desc, content, heading, mtime in batch:
                texts.append(embedding_text(name, desc, content, heading))

            blobs = embed_texts(texts)

            for j, (chunk_id, path, name, desc, content, heading, mtime) in enumerate(batch):
                digest = content_hash(name, desc, content, heading)
                vec_conn.execute(
                    """INSERT OR REPLACE INTO vectors
                       (chunk_id, path, name, section_heading, content_hash, embedding, mtime)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (chunk_id, path, name, heading or "", digest, blobs[j], mtime)
                )
            vec_conn.commit()
            total += len(batch)
        # Stamp which model/dim built this db -> detect silent model drift on next run.
        vec_conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('model',?)", (MODEL_NAME,))
        vec_conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('dim',?)", (str(VECTOR_DIM),))
        vec_conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('hash_scheme',?)", (HASH_SCHEME,))
        # Stamp the fastembed release too: a full rebuild embeds every chunk under
        # the CURRENT fastembed, so this records the geometry of the whole db. The
        # search-time guard degrades to FTS the moment the live fastembed differs.
        vec_conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('fastembed_version',?)", (_fastembed_version(),))
        vec_conn.commit()
        success = True
    except Exception as e:
        print(f"ERROR: vector reindex failed, restoring backup: {e}", file=sys.stderr)
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, vector_db_path)
        raise
    finally:
        if vec_conn is not None:
            vec_conn.close()
        if index_conn is not None:
            index_conn.close()
        if success and os.path.exists(backup_path):
            os.remove(backup_path)

    elapsed = time.time() - t0
    print(f"Embedded {total} chunks in {elapsed:.1f}s ({total/elapsed:.0f} chunks/s)")


def run_incremental(index_db_path, vector_db_path):
    index_conn = None
    vec_conn = None
    try:
        index_conn = sqlite3.connect(index_db_path)
        index_conn.execute("PRAGMA journal_mode=WAL")
        index_conn.execute("PRAGMA busy_timeout=5000")

        vec_conn = init_vector_db(vector_db_path)

        existing = {}
        for row in vec_conn.execute("SELECT chunk_id, path, section_heading, content_hash, mtime FROM vectors"):
            existing[row[0]] = {
                "path": row[1],
                "section_heading": row[2] or "",
                "content_hash": row[3] or "",
                "mtime": row[4],
            }

        rows = index_conn.execute("""
            SELECT id, path, name, description, content, section_heading, mtime
            FROM memory_chunks
        """).fetchall()

        current_ids = set()
        to_embed = []

        for chunk_id, path, name, desc, content, heading, mtime in rows:
            current_ids.add(chunk_id)
            digest = content_hash(name, desc, content, heading)
            prev = existing.get(chunk_id)
            if (
                prev is None
                or prev["path"] != path
                or prev["section_heading"] != (heading or "")
                or prev["content_hash"] != digest
                or prev["mtime"] != mtime
            ):
                to_embed.append((chunk_id, path, name, desc, content, heading, mtime))

        deleted = set(existing.keys()) - current_ids
        if deleted:
            vec_conn.executemany("DELETE FROM vectors WHERE chunk_id = ?",
                                [(cid,) for cid in deleted])

        if not to_embed:
            print(f"Vectors up to date ({len(existing)} chunks, {len(deleted)} deleted)")
            vec_conn.commit()
            return

        batch_size = 64
        total = 0
        t0 = time.time()

        for i in range(0, len(to_embed), batch_size):
            batch = to_embed[i:i + batch_size]
            texts = []
            for _, path, name, desc, content, heading, mtime in batch:
                texts.append(embedding_text(name, desc, content, heading))

            blobs = embed_texts(texts)

            for j, (chunk_id, path, name, desc, content, heading, mtime) in enumerate(batch):
                digest = content_hash(name, desc, content, heading)
                vec_conn.execute(
                    """INSERT OR REPLACE INTO vectors
                       (chunk_id, path, name, section_heading, content_hash, embedding, mtime)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (chunk_id, path, name, heading or "", digest, blobs[j], mtime)
                )
            vec_conn.commit()
            total += len(batch)

        elapsed = time.time() - t0
        print(f"Embedded {total} new/changed chunks in {elapsed:.1f}s, {len(deleted)} deleted")
    finally:
        if vec_conn is not None:
            vec_conn.close()
        if index_conn is not None:
            index_conn.close()


def search(vector_db_path, query, limit=5):
    import numpy as np

    vec_conn = init_vector_db(vector_db_path)
    try:
        if not _vector_meta_ok(vec_conn):
            return []  # drift detected + warned; fail safe to FTS-only
        model = get_model()
        # Search queries get the active profile's query prefix (e5 "query: "; bge
        # an asymmetric instruction) — fastembed does not add it.
        q_vec = np.array(list(model.embed([QUERY_PREFIX + query]))[0], dtype=np.float32)

        rows = vec_conn.execute(
            "SELECT chunk_id, path, name, section_heading, content_hash, embedding FROM vectors"
        ).fetchall()

        scores = []
        for chunk_id, path, name, heading, digest, blob in rows:
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape != q_vec.shape:
                continue
            sim = float(np.dot(q_vec, vec) / (np.linalg.norm(q_vec) * np.linalg.norm(vec) + 1e-8))
            scores.append((sim, chunk_id, path, name, heading or "", digest or ""))

        scores.sort(reverse=True)
        return scores[:limit]
    finally:
        vec_conn.close()


def _acquire_embed_lock(vector_db):
    """Serialize concurrent embed writers (run_full / run_incremental).

    The session-start hook (smart-memory-inject.sh) and a manual/cron reindex can
    both invoke embed.py against the same vectors.db; with no lock they race and
    SQLite raises 'database is locked', leaving a half-written index. Take a
    non-blocking exclusive lock so a second writer cleanly no-ops instead of
    corrupting the run. The returned fd must stay open for the writer's lifetime
    (flock auto-releases on process exit — no stale-lock cleanup needed).
    """
    import fcntl

    lock_path = os.path.join(
        os.path.dirname(os.path.abspath(vector_db)) or ".", ".eidetic-embed.lock"
    )
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def main(argv=None):
    argv = argv or sys.argv
    if len(argv) < 3:
        print("Usage: embed.py <index.db> <vectors.db> [--full|--search <query>]")
        return 1

    index_db = argv[1]
    vector_db = argv[2]

    # --search is a read-only path; never block it behind a running reindex.
    if len(argv) > 4 and argv[3] == "--search":
        results = search(vector_db, argv[4])
        for sim, cid, path, name, *_ in results:
            print(f"  {sim:.3f}  {name or path}")
        return 0

    lock_fd = _acquire_embed_lock(vector_db)
    if lock_fd is None:
        print(
            "embed.py: another embed run holds the lock; skipping (no-op).",
            file=sys.stderr,
        )
        return 0

    if len(argv) > 3 and argv[3] == "--full":
        run_full(index_db, vector_db)
    else:
        run_incremental(index_db, vector_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
