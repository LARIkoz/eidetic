#!/usr/bin/env python3
"""AI Memory System v2.0 — Hybrid FTS5 + Vector Search.

Primary: FTS5 with compound ranking (fast, keyword-based).
Fallback: Vector search via fastembed when FTS5 returns < 3 results.
Merge: Reciprocal Rank Fusion (RRF) when both return results.

Core deps: python3 stdlib + sqlite3. Optional: fastembed (for vector search).
"""

import json
import importlib.util
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}
FRESHNESS_CUTOFF_DAYS = 30
MAX_LIMIT = 50
MAX_QUERY_TERMS = 8
VECTOR_MIN_SIM = 0.55
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "our", "the",
    "but", "does", "do", "not", "should", "that", "this", "to", "use",
    "using", "was", "what", "where", "which", "who", "why", "with",
    "как", "где", "для", "или", "что", "это", "наш", "наша", "наше",
}


DRIFT_PENALTIES = {
    "broken_wikilink": 0.8,
    "age_stale": 0.5,
    "confidence_escalation": 0.3,
}


def _load_drift_map(db_path):
    drift_path = db_path.replace("index.db", "drift_state.db")
    if not os.path.exists(drift_path):
        return {}
    try:
        dc = sqlite3.connect(drift_path)
        dc.execute("PRAGMA busy_timeout=2000")
        rows = dc.execute("""
            SELECT path, drift_type FROM drift_findings
            WHERE resolved_at IS NULL AND first_seen > 1
        """).fetchall()
        dc.close()
    except sqlite3.OperationalError:
        return {}
    result = {}
    for path, drift_type in rows:
        penalty = DRIFT_PENALTIES.get(drift_type, 0.5)
        if path not in result or penalty < result[path]:
            result[path] = penalty
    return result


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


def _normalize_limit(limit):
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return 10


def _tokenize_query(query):
    """Return safe natural-language terms for FTS5 MATCH expressions."""
    terms = []
    seen = set()
    for raw in re.findall(r"\w+", query, flags=re.UNICODE):
        term = raw.lower()
        if len(term) < 2 or term in STOPWORDS:
            continue
        if term.upper() in ("AND", "OR", "NOT", "NEAR"):
            continue
        if term not in seen:
            seen.add(term)
            terms.append(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return terms


def _build_fts_queries(query):
    """Try exact phrase first, then all-term prefix search, then any-term recall."""
    terms = _tokenize_query(query)
    if not terms:
        return []

    queries = []
    if len(terms) > 1:
        queries.append(("phrase", '"' + " ".join(terms) + '"'))
    prefix_terms = [term + "*" for term in terms]
    queries.append(("and", " AND ".join(prefix_terms)))
    if len(terms) > 1:
        queries.append(("or", " OR ".join(prefix_terms)))
    return queries


def _row_match_quality(row, terms, strategy):
    haystack = " ".join([
        row["path"] or "",
        row["name"] or "",
        row["type"] or "",
        row["section_heading"] or "",
        row["description"] or "",
        row["content"] or "",
    ]).lower()
    coverage = sum(1 for term in terms if term in haystack) / max(1, len(terms))
    strategy_boost = {"phrase": 0.30, "and": 0.15, "or": 0.0}.get(strategy, 0.0)
    return coverage + strategy_boost


def _fetch_fts_rows(conn, query, limit, type_filter):
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

    if type_filter:
        sql += " AND c.type = ?"

    sql += " ORDER BY memory_fts.rank LIMIT ?"

    terms = _tokenize_query(query)
    rows = []
    seen_ids = set()
    target = limit * 3

    for strategy, fts_query in _build_fts_queries(query):
        params = [fts_query]
        if type_filter:
            params.append(type_filter)
        params.append(target)

        try:
            candidates = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            if "fts5" in str(e).lower() or "no such" in str(e).lower():
                print(f"ERROR: Search failed: {e}", file=sys.stderr)
                sys.exit(1)
            raise

        for row in candidates:
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            rows.append((row, strategy, _row_match_quality(row, terms, strategy)))

        if len(rows) >= target:
            break

    return rows


def _needs_vector(results, limit):
    if not results:
        return True
    if len(results) < min(3, limit):
        return True
    has_phrase = any(r.get("match") == "phrase" for r in results[:3])
    if not has_phrase:
        return True
    return False


def search(db_path, query, limit=10, type_filter=None, output_json=False):
    """Search FTS5 index with compound ranking."""
    if not os.path.exists(db_path):
        print("ERROR: Index not found. Run: ~/.claude/memory-system/bin/index.sh --full", file=sys.stderr)
        sys.exit(1)

    limit = _normalize_limit(limit)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row

    drift_map = _load_drift_map(db_path)
    rows = _fetch_fts_rows(conn, query, limit, type_filter)

    results = []
    for row, strategy, match_quality in rows:
        ev_w = EVIDENCE_WEIGHTS.get(row["evidence"], 0.7)
        src_w = SOURCE_WEIGHTS.get(row["source"], 1.0)
        dp = drift_map.get(row["path"])
        fr_w = dp if dp is not None else compute_freshness(row["last_verified"])
        raw_rank = abs(row["fts_rank"])
        compound = raw_rank * ev_w * src_w * fr_w * max(0.1, match_quality)

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
            "match": strategy,
            "match_quality": round(match_quality, 3),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    vector_db = db_path.replace("index.db", "vectors.db")
    has_phrase = any(r.get("match") == "phrase" for r in results[:3])
    if _needs_vector(results, limit) and os.path.exists(vector_db):
        vec_results = _vector_search(vector_db, conn, query, limit, type_filter, warn=not output_json)
        if vec_results:
            results = _rrf_merge(results, vec_results, limit, has_phrase=has_phrase)

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


def _vector_search(vector_db, index_conn, query, limit, type_filter, warn=False):
    try:
        embed_path = os.path.join(os.path.dirname(__file__), "embed.py")
        spec = importlib.util.spec_from_file_location("eidetic_embed", embed_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {embed_path}")
        embed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(embed)
        vec_results = embed.search(vector_db, query, limit=limit * 2)
    except ImportError as e:
        if warn:
            print(f"WARNING: vector search unavailable: {e}", file=sys.stderr)
        return []
    except Exception as e:
        if warn:
            print(f"WARNING: vector search failed: {e}", file=sys.stderr)
        return []

    best_per_path = {}
    for sim, chunk_id, path, name in vec_results:
        if sim < VECTOR_MIN_SIM:
            continue
        if path in best_per_path and best_per_path[path][0] >= sim:
            continue
        best_per_path[path] = (sim, chunk_id, name)

    results = []
    for path, (sim, chunk_id, name) in best_per_path.items():
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
            "match": "vector",
            "match_quality": round(sim, 3),
        })
    results.sort(key=lambda x: x["vector_score"], reverse=True)
    return results


def _rrf_merge(fts_results, vec_results, limit, k=60, has_phrase=False):
    scores = {}
    data = {}

    vec_boost = 1.0 if has_phrase else 1.5

    for rank, r in enumerate(fts_results):
        key = (r["path"], r["section"])
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        data[key] = r

    for rank, r in enumerate(vec_results):
        key = (r["path"], r["section"])
        scores[key] = scores.get(key, 0) + vec_boost / (k + rank + 1)
        if key not in data:
            data[key] = r
        else:
            data[key]["vector_score"] = r.get("vector_score", 0)
            data[key]["match"] = "hybrid"

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
            limit = _normalize_limit(sys.argv[i + 1])
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
