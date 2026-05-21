#!/usr/bin/env python3
"""AI Memory System v2.0 — Hybrid FTS5 + Vector Search.

Primary: FTS5 with compound ranking (fast, keyword-based).
Fallback: Vector search via fastembed when FTS5 returns < 3 results.
Merge: Reciprocal Rank Fusion (RRF) when both return results.

Core deps: python3 stdlib + sqlite3. Optional: fastembed (for vector search).
"""

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}
FRESHNESS_CUTOFF_DAYS = 30


def compute_freshness(last_verified):
    """Fresh (<30d) = 1.0, stale = 0.5, unknown = 0.7."""
    if not last_verified:
        return 0.7
    try:
        lv = datetime.fromisoformat(last_verified)
        if datetime.now() - lv < timedelta(days=FRESHNESS_CUTOFF_DAYS):
            return 1.0
        return 0.5
    except (ValueError, TypeError):
        return 0.7


def search(db_path, query, limit=10, type_filter=None, output_json=False):
    """Search FTS5 index with compound ranking."""
    if not os.path.exists(db_path):
        print("ERROR: Index not found. Run: ~/.claude/memory-system/bin/index.sh --full", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row

    sanitized = re.sub(r'[*(){}[\]^~:+\-]', ' ', query)
    sanitized = sanitized.replace('"', '""')
    words = [w for w in sanitized.split() if w.upper() not in ("AND", "OR", "NOT", "NEAR")]
    fts_query = '"' + " ".join(words) + '"' if words else query

    sql = """
        SELECT
            c.id, c.path, c.project, c.name, c.type,
            c.evidence, c.source, c.confidence, c.last_verified,
            c.section_heading, c.content, c.description,
            memory_fts.rank AS fts_rank
        FROM memory_fts
        JOIN memory_chunks c ON memory_fts.rowid = c.id
        WHERE memory_fts MATCH ?
    """
    params = [fts_query]

    if type_filter:
        sql += " AND c.type = ?"
        params.append(type_filter)

    sql += " ORDER BY memory_fts.rank LIMIT ?"
    params.append(limit * 3)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        if "fts5" in str(e).lower() or "no such" in str(e).lower():
            print(f"ERROR: Search failed: {e}", file=sys.stderr)
            sys.exit(1)
        raise

    results = []
    for row in rows:
        ev_w = EVIDENCE_WEIGHTS.get(row["evidence"], 0.7)
        src_w = SOURCE_WEIGHTS.get(row["source"], 1.0)
        fr_w = compute_freshness(row["last_verified"])
        raw_rank = abs(row["fts_rank"])
        compound = raw_rank * ev_w * src_w * fr_w

        snippet = row["content"][:200].replace("\n", " ").strip()
        if len(row["content"]) > 200:
            snippet += "..."

        results.append({
            "path": row["path"],
            "project": row["project"] or "",
            "name": row["name"] or "",
            "type": row["type"] or "",
            "section": row["section_heading"] or "",
            "snippet": snippet,
            "evidence": row["evidence"] or "observed",
            "source": row["source"] or "user-explicit",
            "freshness": fr_w,
            "score": round(compound, 4),
            "fts_rank": round(raw_rank, 4),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    vector_db = db_path.replace("index.db", "vectors.db")
    if len(results) < 3 and os.path.exists(vector_db):
        vec_results = _vector_search(vector_db, conn, query, limit, type_filter)
        results = _rrf_merge(results, vec_results, limit)

    if output_json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if not results:
            print(f"No results for: {query}")
            return

        for i, r in enumerate(results, 1):
            short_path = r["path"].replace(os.path.expanduser("~"), "~")
            source_tag = "hybrid" if r.get("vector_score") else "fts5"
            print(f"\n--- [{i}] score={r['score']} ({r['evidence']}/{r['source']}) [{source_tag}] ---")
            print(f"  File: {short_path}")
            if r["name"]:
                print(f"  Name: {r['name']}")
            print(f"  Type: {r['type']}  Section: {r['section']}")
            print(f"  {r['snippet']}")

    conn.close()


def _vector_search(vector_db, index_conn, query, limit, type_filter):
    try:
        from embed import search as vec_search
        vec_results = vec_search(vector_db, query, limit=limit * 2)
    except ImportError:
        return []
    except Exception:
        return []

    results = []
    for sim, chunk_id, path, name in vec_results:
        if sim < 0.4:
            continue
        row = index_conn.execute("""
            SELECT type, evidence, source, last_verified, content, section_heading, project
            FROM memory_chunks WHERE id = ?
        """, (chunk_id,)).fetchone()
        if not row:
            continue
        typ, evidence, source, lv, content, heading, project = row
        if type_filter and typ != type_filter:
            continue

        ev_w = EVIDENCE_WEIGHTS.get(evidence, 0.7)
        src_w = SOURCE_WEIGHTS.get(source, 0.5)
        fr_w = compute_freshness(lv)
        compound = sim * ev_w * src_w * fr_w

        snippet = content[:200].replace("\n", " ").strip() if content else ""
        if content and len(content) > 200:
            snippet += "..."

        results.append({
            "path": path,
            "project": project or "",
            "name": name or "",
            "type": typ or "",
            "section": heading or "",
            "snippet": snippet,
            "evidence": evidence or "observed",
            "source": source or "user-explicit",
            "freshness": fr_w,
            "score": round(compound, 4),
            "fts_rank": 0,
            "vector_score": round(sim, 4),
        })
    return results


def _rrf_merge(fts_results, vec_results, limit, k=60):
    scores = {}
    data = {}

    for rank, r in enumerate(fts_results):
        key = (r["path"], r["section"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        data[key] = r

    for rank, r in enumerate(vec_results):
        key = (r["path"], r["section"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        if key not in data:
            data[key] = r
        else:
            data[key]["vector_score"] = r.get("vector_score", 0)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for key, rrf_score in ranked[:limit]:
        entry = data[key]
        entry["score"] = round(rrf_score, 4)
        results.append(entry)
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: search.sh <query> [--limit N] [--type TYPE] [--json]", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    query = None
    limit = 10
    type_filter = None
    output_json = False

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
            i += 2
        elif arg == "--type" and i + 1 < len(sys.argv):
            type_filter = sys.argv[i + 1]
            i += 2
        elif arg == "--json":
            output_json = True
            i += 1
        elif query is None:
            query = arg
            i += 1
        else:
            query = (query or "") + " " + arg
            i += 1

    if not query:
        print("ERROR: No query provided", file=sys.stderr)
        sys.exit(1)

    search(db_path, query, limit, type_filter, output_json)


if __name__ == "__main__":
    main()
