#!/usr/bin/env python3
"""Eidetic Engine API v1 — the public embedding / vector / search seam.

PUBLIC API. Changes to exported names/signatures/semantics follow ENGINE_API
versioning (see docs/engine.md). Everything else under bin/ is private and may
change without notice.

This module is a thin, ADDITIVE wrapper over the private `embed.py` (embedder +
vector store + drift-guarded search) and `rerank.py` (cross-encoder). It never
forks them; `git diff -- bin/embed.py bin/rerank.py` stays empty. Consumers —
in-repo (search/canary/coverage) and external (the task-tracker search skill) —
build on this door instead of the private internals.

Design contract (see docs/engine.md for the full table):
  * SOFT paths (`Index.search`, `rerank`) never raise on an environment problem
    (missing model / no fastembed / stamp drift): they return `[]` and print one
    stderr line. A read must degrade, never crash.
  * BUILD paths (`embed_passages`, `embed_query`) RAISE `EngineUnavailable` when
    the model/runtime is absent — a builder must never silently write nothing.
  * Zero new dependencies; fastembed / numpy stay lazy-optional exactly as today.
"""

import importlib.util
import os
import sys

ENGINE_API = "1.0"  # MAJOR.MINOR — see docs/engine.md breaking-change rules.


class EngineUnavailable(RuntimeError):
    """The embedding/runtime layer is unavailable for a BUILD operation.

    `.reason` is a short human-readable string. Raised only by build paths
    (`embed_passages` / `embed_query`); soft read paths never raise it.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = str(reason)


# --- private module loading (additive: load siblings by path, never edit them) ---
_embed_mod = None
_rerank_mod = None


def _load_sibling(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name + ".py")
    spec = importlib.util.spec_from_file_location("eidetic_" + name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load " + path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _embed():
    """The private embed module (import-safe: fastembed/numpy stay lazy)."""
    global _embed_mod
    if _embed_mod is None:
        _embed_mod = _load_sibling("embed")
    return _embed_mod


def _rerank():
    global _rerank_mod
    if _rerank_mod is None:
        _rerank_mod = _load_sibling("rerank")
    return _rerank_mod


# --- version + metadata ------------------------------------------------------
def require(major):
    """`require("1")` — no-op if ENGINE_API MAJOR matches, else EngineUnavailable."""
    have = ENGINE_API.split(".")[0]
    if str(major) != have:
        raise EngineUnavailable(
            f"ENGINE_API major {major!r} required, have {ENGINE_API!r}"
        )


def model_info():
    """Pure metadata; never loads the model; never raises.

    {"model": str, "dim": int, "hash_scheme": str, "fastembed": str|None,
     "engine_api": str}
    """
    emb = _embed()
    return {
        "model": emb.MODEL_NAME,
        "dim": emb.VECTOR_DIM,
        "hash_scheme": emb.HASH_SCHEME,
        "fastembed": emb._fastembed_version(),
        "engine_api": ENGINE_API,
    }


def configure(provider=None, threads=None):
    """Process-wide execution-provider policy for BOTH embedder and reranker.

    provider="cpu" pins CPUExecutionProvider — the long-lived-process-safe
    choice (CoreML recompiles/leaks at query time and OOMs the e5 embedder on
    some Apple-Silicon boxes). A specific provider string is passed through.
    provider=None leaves the current policy untouched (a fresh process therefore
    uses embed.py's platform default). threads sets the intra-op thread count for
    bulk CPU embedding (OMP_NUM_THREADS). Idempotent; affects SUBSEQUENT lazy
    model loads, so call it before the first embed/search.

    Replaces consumers' hand-rolled `_model` provider surgery. No model is
    loaded here.
    """
    if provider is not None:
        p = provider.strip().lower()
        if p == "cpu":
            os.environ["EIDETIC_EMBED_PROVIDERS"] = "CPUExecutionProvider"
        else:
            os.environ["EIDETIC_EMBED_PROVIDERS"] = provider
    if threads is not None:
        os.environ["OMP_NUM_THREADS"] = str(int(threads))


# --- canonical composition + hashing (model-free; never raise) ---------------
def embedding_text(name, desc, content, heading):
    """Canonical text composition fed to the embedder (wraps embed.py)."""
    return _embed().embedding_text(name, desc, content, heading)


def content_hash(name, desc, content, heading):
    """The SOLE content-hash authority (wraps embed.py). Model-free, never raises."""
    return _embed().content_hash(name, desc, content, heading)


# --- build-time embedding (RAISE on unavailable) -----------------------------
def embed_passages(texts):
    """Index-time embedding (passage prefix applied inside).

    Returns float32 blobs, one per text, each of dim model_info()["dim"].
    RAISES EngineUnavailable when the model/runtime is absent — a builder must
    never silently write nothing.
    """
    try:
        return _embed().embed_texts(list(texts))
    except EngineUnavailable:
        raise
    except Exception as exc:
        raise EngineUnavailable(f"embed_passages: model/runtime unavailable ({exc})")


def embed_query(text):
    """Query-time embedding (query prefix inside) → numpy float32 vector.

    Advanced use — Index.search embeds internally and stays soft. RAISES
    EngineUnavailable when the model/runtime is absent.
    """
    try:
        import numpy as np

        blobs = _embed().embed_query_texts([text])
        if not blobs:
            raise EngineUnavailable("embed_query: embedder returned nothing")
        return np.frombuffer(blobs[0], dtype=np.float32)
    except EngineUnavailable:
        raise
    except Exception as exc:
        raise EngineUnavailable(f"embed_query: model/runtime unavailable ({exc})")


# --- build lock (never raises) -----------------------------------------------
def acquire_build_lock(vectors_db_path):
    """Non-blocking exclusive build lock (wraps embed.py's flock).

    Returns a handle to hold for the build's lifetime, or None if another build
    already holds it. Never raises.
    """
    try:
        return _embed()._acquire_embed_lock(vectors_db_path)
    except Exception:
        return None


# --- the vector index --------------------------------------------------------
def open_index(path):
    """Open/create the vector index (schema + meta tables). Never raises on a
    fresh file."""
    return Index(path)


class Index:
    """A vector index (the `vectors.db` store), opened through the engine door.

    Storage field names are kept as-is (`path` / `name` / `section_heading`) so
    there is zero mapping layer and zero drift vs existing databases. Read those
    as: path = your stable key, section_heading = your kind label.
    """

    def __init__(self, path):
        self.path = path
        self._conn = _embed().init_vector_db(path)

    # context manager -------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # writes ----------------------------------------------------------
    def upsert(self, rows):
        """INSERT OR REPLACE by chunk_id. Returns rows written. sqlite errors
        propagate as-is (a build failure must be loud)."""
        written = 0
        for row in rows:
            self._conn.execute(
                "INSERT OR REPLACE INTO vectors "
                "(chunk_id, path, name, section_heading, content_hash, embedding, mtime) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["chunk_id"],
                    row["path"],
                    row.get("name", ""),
                    row.get("section_heading", ""),
                    row.get("content_hash", ""),
                    row["embedding"],
                    row.get("mtime", 0),
                ),
            )
            written += 1
        self._conn.commit()
        return written

    def delete(self, chunk_ids):
        cur = self._conn.executemany(
            "DELETE FROM vectors WHERE chunk_id = ?", [(c,) for c in chunk_ids]
        )
        self._conn.commit()
        return cur.rowcount if cur.rowcount is not None else 0

    def existing_hashes(self):
        """chunk_id -> content_hash — the incremental-build primitive."""
        return {
            cid: (h or "")
            for cid, h in self._conn.execute(
                "SELECT chunk_id, content_hash FROM vectors"
            )
        }

    def stamp(self):
        """Write model/dim/hash_scheme/fastembed_version meta stamps. Call after
        a build so search's drift guard can detect an embedder change."""
        emb = _embed()
        for key, value in (
            ("model", emb.MODEL_NAME),
            ("dim", str(emb.VECTOR_DIM)),
            ("hash_scheme", emb.HASH_SCHEME),
            ("fastembed_version", emb._fastembed_version()),
        ):
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value)
            )
        self._conn.commit()

    # reads -----------------------------------------------------------
    def search(self, query, limit=5):
        """Drift-guarded cosine search. SOFT: on a missing model or a
        model/dim/hash/fastembed drift vs the stamps, returns [] and prints one
        stderr line. Hit dicts: {score, chunk_id, path, name, section_heading,
        content_hash}."""
        try:
            hits = _embed().search(self.path, query, limit=limit)
        except Exception as exc:
            print(
                f"eidetic-engine: vector search unavailable "
                f"({type(exc).__name__}: {exc}); returning no vector hits",
                file=sys.stderr,
            )
            return []
        return [
            {
                "score": float(sim),
                "chunk_id": chunk_id,
                "path": path,
                "name": name or "",
                "section_heading": heading or "",
                "content_hash": digest or "",
            }
            for (sim, chunk_id, path, name, heading, digest) in hits
        ]

    def stats(self):
        """{"vectors": int, "by_kind": {section_heading: count}, "stamps": {...}}
        — a doctor primitive."""
        vectors = self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        by_kind = {
            (heading or ""): count
            for heading, count in self._conn.execute(
                "SELECT section_heading, COUNT(*) FROM vectors GROUP BY section_heading"
            )
        }
        try:
            stamps = dict(self._conn.execute("SELECT key, value FROM meta"))
        except Exception:
            stamps = {}
        return {"vectors": vectors, "by_kind": by_kind, "stamps": stamps}


# --- rerank (SOFT) -----------------------------------------------------------
def rerank(query, docs):
    """Multilingual cross-encoder scores aligned with `docs`. SOFT: returns []
    on unavailable (one stderr line, once per process). CPU-pinned by default;
    honors configure()."""
    return _rerank().scores(query, list(docs))
