#!/usr/bin/env python3
"""Eidetic v2.0 — Vector embeddings for hybrid search.

Generates embeddings for all memory chunks using fastembed (ONNX, ~33MB model).
Stores in vectors.db alongside index.db. Used as fallback when FTS5 returns < 3 results.
"""

import json
import hashlib
import os
import sqlite3
import sys
import time

MODEL_NAME = "BAAI/bge-small-en-v1.5"
VECTOR_DIM = 384

_model = None


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL_NAME)
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
    conn.commit()
    return conn


def embedding_text(name, desc, content, heading):
    parts = [name or "", desc or "", heading or ""]
    body = (content or "")[:500]
    return " ".join(p for p in parts + [body] if p).strip() or "empty"


def content_hash(name, desc, content, heading):
    payload = "\0".join([name or "", desc or "", heading or "", content or ""])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def embed_texts(texts):
    import numpy as np

    model = get_model()
    embeddings = list(model.embed(texts))
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
        model = get_model()
        q_vec = np.array(list(model.embed([query]))[0], dtype=np.float32)

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


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: embed.py <index.db> <vectors.db> [--full|--search <query>]")
        sys.exit(1)

    index_db = sys.argv[1]
    vector_db = sys.argv[2]

    if len(sys.argv) > 3 and sys.argv[3] == "--full":
        run_full(index_db, vector_db)
    elif len(sys.argv) > 4 and sys.argv[3] == "--search":
        results = search(vector_db, sys.argv[4])
        for sim, cid, path, name in results:
            print(f"  {sim:.3f}  {name or path}")
    else:
        run_incremental(index_db, vector_db)
