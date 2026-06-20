#!/usr/bin/env python3
"""AI Memory System v2.0 — Hybrid FTS5 + Vector Search.

Primary: FTS5 with compound ranking (fast, keyword-based).
Fallback: Vector search via fastembed when FTS5 returns < 3 results.
Merge: Reciprocal Rank Fusion (RRF) when both return results.

Core deps: python3 stdlib + sqlite3. Optional: fastembed (for vector search).
"""

import json
import hashlib
import importlib.util
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

STATUS_WEIGHTS = {
    "current": 1.0,
    "active": 1.0,
    "validated": 1.0,
    "resolved": 0.75,
    "fixed": 0.75,
    "superseded": 0.35,
    "deprecated": 0.35,
    "obsolete": 0.35,
    "archived": 0.25,
}
FRESHNESS_CUTOFF_DAYS = 30
MAX_LIMIT = 50
MAX_QUERY_TERMS = 8
# e5-large (v6) score calibration, measured 2026-05-31 over the live corpus:
#   true matches 0.80-0.85 | hard-negatives 0.81-0.84 (overlap => RANK decides, not
#   abs score) | off-topic garbage <=0.79. Floor 0.795 rejects garbage; 0.80 = confident;
#   high (0.86 > true max) is unreachable by vector alone => "high" requires FTS+vector
#   agreement (hybrid path). Both profiles share e5's range (cross-lingual is symmetric).
VECTOR_MIN_SIM = 0.795
VECTOR_MEDIUM_CONFIDENCE = 0.80
VECTOR_HIGH_CONFIDENCE = 0.86
MULTILINGUAL_VECTOR_MIN_SIM = 0.795
MULTILINGUAL_VECTOR_MEDIUM_CONFIDENCE = 0.80
MULTILINGUAL_VECTOR_HIGH_CONFIDENCE = 0.86
# Two-signal gate: e5 cosine in the [medium, high) band cannot separate a true
# cross-lingual match (~0.83) from topical garbage (~0.83) — measured. A
# vector-only result needs at least this many query content-tokens present in
# its text before it is allowed to reach "medium" confidence.
VECTOR_CORROBORATION_MIN = 2
# Third signal for the ambiguous band when lexical corroboration is impossible
# (a RU->EN paraphrase shares zero anchor tokens with its EN target). A
# multilingual cross-encoder joint-encodes (query, doc) and separates a true
# cross-lingual match from topical garbage where cosine + lexical cannot.
# Calibrated 2026-05-31 on the live corpus: 10 true queries scored >= -0.89,
# 8 plausible-but-absent garbage probes scored <= -1.66 (gap +0.775). tau -1.2
# keeps a 0.46 margin over garbage and 0.31 under the weakest true match.
CROSS_ENCODER_CONFIRM_MIN = -1.2
CROSS_ENCODER_MAX_CANDIDATES = 5
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
DETAIL_ID_PREFIX = "mem_"
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "our", "the",
    "but", "does", "do", "not", "should", "that", "this", "to", "use",
    "using", "was", "what", "where", "which", "who", "why", "with",
    "как", "где", "для", "или", "что", "это", "наш", "наша", "наше",
}


# Single source of truth: bin/constants.py. Literal fallback only for when the
# module is run somewhere constants.py is not importable (W3 dedup).
try:
    from constants import EVIDENCE_WEIGHTS, SOURCE_WEIGHTS, DRIFT_PENALTIES
except ImportError:
    EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
    SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}
    DRIFT_PENALTIES = {"broken_wikilink": 0.8, "age_stale": 0.5, "confidence_escalation": 0.3}


def ensure_agent_columns(conn):
    """Add v2.6 derived columns when searching an older index.db."""
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    except sqlite3.OperationalError:
        return
    migrations = {
        "project": "ALTER TABLE memory_chunks ADD COLUMN project TEXT DEFAULT ''",
        "card_kind": "ALTER TABLE memory_chunks ADD COLUMN card_kind TEXT DEFAULT ''",
        "status": "ALTER TABLE memory_chunks ADD COLUMN status TEXT DEFAULT 'current'",
        "area": "ALTER TABLE memory_chunks ADD COLUMN area TEXT DEFAULT ''",
        "supersedes": "ALTER TABLE memory_chunks ADD COLUMN supersedes TEXT DEFAULT ''",
        "superseded_by": "ALTER TABLE memory_chunks ADD COLUMN superseded_by TEXT DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in existing:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.commit()


def _load_drift_data(db_path):
    drift_path = db_path.replace("index.db", "drift_state.db")
    if not os.path.exists(drift_path):
        return {}
    try:
        dc = sqlite3.connect(drift_path)
        dc.execute("PRAGMA busy_timeout=2000")
        rows = dc.execute("""
            SELECT path, drift_type, detail, first_seen, detected_at
            FROM drift_findings
            WHERE resolved_at IS NULL
        """).fetchall()
        dc.close()
    except sqlite3.OperationalError:
        return {}
    result = {}
    for path, drift_type, detail, first_seen, detected_at in rows:
        entry = result.setdefault(path, {"penalty": None, "findings": []})
        penalized = int(first_seen or 0) > 1
        penalty = DRIFT_PENALTIES.get(drift_type, 0.5)
        if penalized and (entry["penalty"] is None or penalty < entry["penalty"]):
            entry["penalty"] = penalty
        entry["findings"].append({
            "type": drift_type,
            "detail": detail or "",
            "first_seen": int(first_seen or 0),
            "detected_at": detected_at or "",
            "penalized": penalized,
            "penalty": penalty if penalized else None,
        })
    return result


def _load_drift_map(db_path):
    return {
        path: data["penalty"]
        for path, data in _load_drift_data(db_path).items()
        if data.get("penalty") is not None
    }


def compute_freshness(last_verified):
    """Fresh (<30d) = 1.0, stale = 0.5, unknown = 0.7."""
    if not last_verified:
        return 0.7
    try:
        lv = datetime.fromisoformat(str(last_verified).replace("Z", "+00:00"))
        now = datetime.now(lv.tzinfo) if lv.tzinfo else datetime.now()
        if now - lv < timedelta(days=FRESHNESS_CUTOFF_DAYS):
            return 1.0
        return 0.5
    except (ValueError, TypeError):
        return 0.7


def compute_status_weight(status, superseded_by=""):
    normalized = (status or "current").strip().lower()
    if superseded_by:
        return min(STATUS_WEIGHTS.get(normalized, 1.0), STATUS_WEIGHTS["superseded"])
    return STATUS_WEIGHTS.get(normalized, 1.0)


def _normalize_limit(limit):
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return 10


def _stable_detail_id(path, section):
    """Deterministic chunk selector stable across index rebuild rowids."""
    normalized = os.path.expanduser(path or "")
    material = json.dumps(
        [normalized, section or ""],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return DETAIL_ID_PREFIX + hashlib.sha256(material).hexdigest()[:16]


def _short_path(path):
    return (path or "").replace(os.path.expanduser("~"), "~")


def _snippet(content, max_chars=200):
    text = (content or "")[:max_chars].replace("\n", " ").strip()
    if content and len(content) > max_chars:
        text += "..."
    return text


def _base_result(row, drift_info=None, drift_penalty=None, freshness=None, status_weight=None):
    section = row["section_heading"] or ""
    content = row["content"] or ""
    return {
        "detail_id": _stable_detail_id(row["path"], section),
        "path": row["path"],
        "project": row["project"] or "",
        "name": row["name"] or "",
        "type": row["type"] or "",
        "card_kind": row["card_kind"] or "",
        "status": row["status"] or "current",
        "area": row["area"] or "",
        "supersedes": row["supersedes"] or "",
        "superseded_by": row["superseded_by"] or "",
        "section": section,
        "snippet": _snippet(content),
        "content_chars": len(content),
        "evidence": row["evidence"] or "observed",
        "source": row["source"] or "user-explicit",
        "freshness": freshness if freshness is not None else compute_freshness(row["last_verified"]),
        "status_weight": status_weight if status_weight is not None else compute_status_weight(row["status"], row["superseded_by"]),
        "drift_penalty": drift_penalty,
        "drift_findings": (drift_info or {}).get("findings", []),
    }


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
        row["card_kind"] or "",
        row["status"] or "",
        row["area"] or "",
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
            c.card_kind, c.status, c.area, c.supersedes, c.superseded_by,
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
    strong_keyword = any(
        r.get("match") in ("phrase", "and") and r.get("match_quality", 0) >= 1.0
        for r in results[:3]
    )
    if strong_keyword:
        return False
    has_phrase = any(r.get("match") == "phrase" for r in results[:3])
    if not has_phrase:
        return True
    return False


def _cap_confidence(level, max_level):
    if CONFIDENCE_ORDER[level] <= CONFIDENCE_ORDER[max_level]:
        return level
    return max_level


def _result_text(result):
    """Highest-signal text already carried on a result (no DB call): the doc
    side for both lexical corroboration and cross-encoder rerank."""
    return " ".join((
        result.get("name") or "",
        result.get("section") or "",
        result.get("snippet") or "",
    )).strip()


def _lexical_corroboration(result, query_tokens):
    """Count distinct query content-tokens present as WHOLE WORDS in the
    candidate's text.

    The second signal for the ambiguous e5 vector-only band: a true match shares
    anchor tokens (proper nouns, identifiers, numbers) with the query, while
    topical garbage shares <=1 generic token. Word-level (same \\w+ tokenizer as
    the query), NOT substring — so a query token like "flow" does not falsely
    corroborate inside "workflow" (which leaked garbage past the gate).
    """
    if not query_tokens:
        return 0
    text_tokens = set(re.findall(r"\w+", _result_text(result).lower(), flags=re.UNICODE))
    return sum(1 for token in query_tokens if token in text_tokens)


def _ambiguous_vector(result):
    """True when a vector-only hit sits in the [medium, high) band — the zone
    where cosine alone cannot tell a true cross-lingual match from garbage."""
    if (result.get("match") or "") != "vector":
        return False
    vector_score = float(result.get("vector_score") or 0)
    profile = result.get("vector_profile") or "strict"
    medium = (
        MULTILINGUAL_VECTOR_MEDIUM_CONFIDENCE
        if profile == "multilingual"
        else VECTOR_MEDIUM_CONFIDENCE
    )
    high = (
        MULTILINGUAL_VECTOR_HIGH_CONFIDENCE
        if profile == "multilingual"
        else VECTOR_HIGH_CONFIDENCE
    )
    return medium <= vector_score < high


def _classify_confidence(result, query_tokens=None):
    """Classify retrieval confidence separately from ranking score.

    RRF and compound scores are ranking mechanics, not user-facing certainty.
    Keep this conservative: exact/all-term FTS can be high, vector-only needs
    a much stronger similarity before it is treated as actionable recall.
    """
    match = result.get("match") or ""
    match_quality = float(result.get("match_quality") or 0)
    vector_score = float(result.get("vector_score") or 0)
    vector_profile = result.get("vector_profile") or "strict"
    vector_medium = (
        MULTILINGUAL_VECTOR_MEDIUM_CONFIDENCE
        if vector_profile == "multilingual"
        else VECTOR_MEDIUM_CONFIDENCE
    )
    vector_high = (
        MULTILINGUAL_VECTOR_HIGH_CONFIDENCE
        if vector_profile == "multilingual"
        else VECTOR_HIGH_CONFIDENCE
    )
    source = result.get("source") or ""
    freshness = float(result.get("freshness") or 0.7)
    status = (result.get("status") or "current").lower()
    superseded_by = result.get("superseded_by") or ""

    level = "low"
    reason = "weak lexical/vector match"

    if match == "phrase":
        level = "high" if match_quality >= 1.0 else "medium"
        reason = "exact phrase match"
    elif match == "and":
        if match_quality >= 1.0:
            level = "high"
            reason = "all query terms matched"
        elif match_quality >= 0.7:
            level = "medium"
            reason = "most query terms matched"
    elif match == "or":
        if match_quality >= 0.8:
            level = "medium"
            reason = "broad keyword match"
    elif match == "hybrid":
        if match_quality >= 1.0 and vector_score >= vector_medium:
            level = "high"
            reason = "keyword and vector agree"
        elif match_quality >= 0.7 or vector_score >= vector_medium:
            level = "medium"
            reason = "partial keyword/vector agreement"
    elif match == "vector":
        if vector_score >= vector_high:
            level = "high"
            reason = "strong semantic match"
        elif vector_score >= vector_medium:
            # Ambiguous e5 band: require a second signal before trusting a
            # vector-only hit as actionable. Lexical corroboration is the cheap
            # signal; cross-lingual paraphrases share no tokens, so a
            # cross-encoder logit (attached by the salvage pass) is the
            # fallback. Without either, flagged low so garbage stays suppressed.
            ce_score = result.get("ce_score")
            if _lexical_corroboration(result, query_tokens) >= VECTOR_CORROBORATION_MIN:
                level = "medium"
                reason = "semantic match + lexical corroboration"
            elif ce_score is not None and ce_score >= CROSS_ENCODER_CONFIRM_MIN:
                level = "medium"
                reason = "semantic match + cross-encoder confirmation"
            else:
                level = "low"
                reason = "semantic-only in ambiguous band; no lexical corroboration"

    if source == "agent-extracted":
        level = _cap_confidence(level, "medium")
        reason += "; agent-extracted source"
    if freshness < 0.6:
        level = _cap_confidence(level, "medium")
        reason += "; stale/drift-penalized"
    if status in {"superseded", "deprecated", "obsolete", "archived"} or superseded_by:
        level = _cap_confidence(level, "medium")
        reason += f"; status={status or 'superseded'}"

    return level, reason


def _cross_encoder_salvage(results, query, query_tokens):
    """Rescue true cross-lingual hits the bi-encoder + lexical gate suppressed.

    Runs only when the whole result set is about to be reported as "no confident
    results" yet a vector candidate sits in the ambiguous band. Loads the
    multilingual cross-encoder lazily (1GB ONNX) and only for those candidates,
    so confident queries never pay for it. A logit >= CROSS_ENCODER_CONFIRM_MIN
    promotes the hit to "medium"; everything else is left untouched.
    """
    eligible = [
        r for r in results
        if r.get("confidence") == "low" and _ambiguous_vector(r)
    ][:CROSS_ENCODER_MAX_CANDIDATES]
    if not eligible:
        return

    try:
        rerank_path = os.path.join(os.path.dirname(__file__), "rerank.py")
        spec = importlib.util.spec_from_file_location("eidetic_rerank", rerank_path)
        if spec is None or spec.loader is None:
            return
        rerank = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rerank)
        ce_scores = rerank.scores(query, [_result_text(r) for r in eligible])
    except Exception:
        return  # degrade to pre-rerank behaviour, never crash search

    if len(ce_scores) != len(eligible):
        return
    for result, ce_score in zip(eligible, ce_scores):
        result["ce_score"] = round(ce_score, 4)
        level, reason = _classify_confidence(result, query_tokens)
        result["confidence"] = level
        result["confidence_reason"] = reason


def _annotate_confidence(results, query=None):
    query_tokens = _tokenize_query(query) if query else None
    for result in results:
        level, reason = _classify_confidence(result, query_tokens)
        result["confidence"] = level
        result["confidence_reason"] = reason
        result.setdefault("retrieval_score", result.get("score", 0))
    # Cross-lingual salvage: only when nothing reached "medium" by the cheap
    # signals — the 1GB cross-encoder must not load on confident queries.
    if query and _best_confidence(results) == "low":
        _cross_encoder_salvage(results, query, query_tokens)
    return results


def _best_confidence(results):
    if not results:
        return "low"
    return max(
        (r.get("confidence", "low") for r in results),
        key=lambda level: CONFIDENCE_ORDER.get(level, 0),
    )


def _search_response(query, limit, type_filter, results):
    best = _best_confidence(results)
    no_confident = CONFIDENCE_ORDER.get(best, 0) < CONFIDENCE_ORDER["medium"]
    return {
        "query": query,
        "type_filter": type_filter,
        "limit": limit,
        "result_count": len(results),
        "best_confidence": best,
        "no_confident_results": no_confident,
        "message": "No confident results; inspect weak candidates before using as memory." if no_confident else "",
        "results": results,
    }


def _is_broad_query(query, results):
    terms = _tokenize_query(query)
    return len(terms) <= 2 and len(results) > 1


def _print_full_results(query, results):
    for i, r in enumerate(results, 1):
        source_tag = r.get("match") if r.get("match") in ("vector", "hybrid") else "fts5"
        extra = ""
        if r.get("retrieval_score") != r.get("score"):
            extra = f" retrieval={r['retrieval_score']}"
        print(f"\n--- [{i}] score={r['score']}{extra} confidence={r['confidence']} ({r['evidence']}/{r['source']}) [{source_tag}] ---")
        print(f"  Confidence: {r['confidence_reason']}")
        print(f"  Detail id: {r['detail_id']}")
        print(f"  File: {_short_path(r['path'])}")
        if r["name"]:
            print(f"  Name: {r['name']}")
        print(f"  Type: {r['type']}  Kind: {r.get('card_kind') or '?'}  Status: {r.get('status') or 'current'}  Section: {r['section']}")
        if r.get("superseded_by"):
            print(f"  Superseded by: {r['superseded_by']}")
        if r.get("drift_findings"):
            drift = ", ".join(
                f"{d.get('type')}:{d.get('detail')}"
                for d in r["drift_findings"][:3]
            )
            print(f"  Drift: {drift}")
        print(f"  {r['snippet']}")


def _print_brief_results(query, results, auto_broad=False):
    if auto_broad:
        print(f"Compact broad-query results for: {query}")
    else:
        print(f"Compact results for: {query}")
    print("Use --full for snippets or --detail <detail_id> for full content.")
    for i, r in enumerate(results, 1):
        title = r.get("name") or os.path.basename(r.get("path") or "")
        section = f" — {r['section']}" if r.get("section") else ""
        kind = r.get("card_kind") or "?"
        print(
            f"[{i}] score={r['score']} confidence={r['confidence']} "
            f"type={r.get('type') or '?'} kind={kind} status={r.get('status') or 'current'} "
            f"id={r['detail_id']}"
        )
        print(f"    {title}{section}")
        print(f"    {_short_path(r.get('path') or '')}")


def _detail_row_payload(row):
    base = _base_result(row)
    base["content"] = row["content"] or ""
    return base


def _detail_lookup(conn, selector, section=None):
    selector = (selector or "").strip()
    if not selector:
        return []

    if selector.startswith(DETAIL_ID_PREFIX):
        rows = conn.execute("""
            SELECT path, project, name, type, evidence, source, confidence,
                   last_verified, card_kind, status, area, supersedes,
                   superseded_by, section_heading, content, description, mtime
            FROM memory_chunks
            ORDER BY path, section_heading
        """).fetchall()
        result = []
        for row in rows:
            if _stable_detail_id(row["path"], row["section_heading"] or "") != selector:
                continue
            if section is not None and (row["section_heading"] or "") != section:
                continue
            result.append(row)
        return result

    candidates = []
    expanded = os.path.expanduser(selector)
    candidates.append(selector)
    candidates.append(expanded)
    if not os.path.isabs(expanded):
        candidates.append(os.path.abspath(expanded))
    seen = set()
    unique = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)

    rows = []
    for path in unique:
        if section is None:
            rows.extend(conn.execute("""
                SELECT path, project, name, type, evidence, source, confidence,
                       last_verified, card_kind, status, area, supersedes,
                       superseded_by, section_heading, content, description, mtime
                FROM memory_chunks
                WHERE path = ?
                ORDER BY section_heading
            """, (path,)).fetchall())
        else:
            rows.extend(conn.execute("""
                SELECT path, project, name, type, evidence, source, confidence,
                       last_verified, card_kind, status, area, supersedes,
                       superseded_by, section_heading, content, description, mtime
                FROM memory_chunks
                WHERE path = ? AND COALESCE(section_heading, '') = ?
                ORDER BY section_heading
            """, (path, section)).fetchall())
        if rows:
            break
    return rows


def _detail_response(selector, rows, section=None):
    found = bool(rows)
    return {
        "selector": selector,
        "section": section,
        "found": found,
        "result_count": len(rows),
        "no_confident_results": not found,
        "message": "" if found else "No memory detail matched the selector.",
        "results": [_detail_row_payload(row) for row in rows],
    }


def search_detail(db_path, selector, section=None, output_json=False, json_object=False):
    if not os.path.exists(db_path):
        print("ERROR: Index not found. Run: ~/.claude/memory-system/bin/index.sh --full", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    ensure_agent_columns(conn)
    rows = _detail_lookup(conn, selector, section)
    payload = _detail_response(selector, rows, section)
    conn.close()

    if output_json or json_object:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not rows:
        print(f"No memory detail matched: {selector}")
        return

    for i, result in enumerate(payload["results"], 1):
        print(f"\n--- Detail [{i}] id={result['detail_id']} ---")
        print(f"File: {_short_path(result['path'])}")
        if result.get("name"):
            print(f"Name: {result['name']}")
        print(f"Type: {result.get('type') or '?'}  Kind: {result.get('card_kind') or '?'}  Status: {result.get('status') or 'current'}  Section: {result.get('section') or ''}")
        print("")
        print(result["content"])


def _run_query(db_path, query, limit, type_filter, warn=False):
    """Full retrieval for one query string → annotated results list (own conn).

    Extracted from search() so the async dual-query can run a native and a
    translated query through the identical FTS+vector+RRF+confidence pipeline.
    Opens and closes its own sqlite connection (thread-safe for parallel calls).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    ensure_agent_columns(conn)
    try:
        drift_data = _load_drift_data(db_path)
        rows = _fetch_fts_rows(conn, query, limit, type_filter)

        has_non_ascii = any(ord(c) > 127 for c in query)
        if not rows and has_non_ascii:
            ascii_words = [w for w in query.split() if all(ord(c) < 128 for c in w) and len(w) >= 3]
            if ascii_words:
                rows = _fetch_fts_rows(conn, " ".join(ascii_words), limit, type_filter)
            if not rows and ascii_words:
                for aw in ascii_words:
                    rows = _fetch_fts_rows(conn, aw, limit, type_filter)
                    if rows:
                        break

        results = []
        for row, strategy, match_quality in rows:
            ev_w = EVIDENCE_WEIGHTS.get(row["evidence"], 0.7)
            src_w = SOURCE_WEIGHTS.get(row["source"], 1.0)
            status_w = compute_status_weight(row["status"], row["superseded_by"])
            drift_info = drift_data.get(row["path"], {})
            dp = drift_info.get("penalty")
            fr_w = dp if dp is not None else compute_freshness(row["last_verified"])
            raw_rank = abs(row["fts_rank"])
            compound = raw_rank * ev_w * src_w * fr_w * status_w * max(0.1, match_quality)

            result = _base_result(
                row,
                drift_info=drift_info,
                drift_penalty=dp,
                freshness=fr_w,
                status_weight=status_w,
            )
            result.update({
                "score": round(compound, 4),
                "retrieval_score": round(compound, 4),
                "fts_rank": round(raw_rank, 4),
                "match": strategy,
                "match_quality": round(match_quality, 3),
            })
            results.append(result)

        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]

        vector_db = db_path.replace("index.db", "vectors.db")
        has_phrase = any(r.get("match") == "phrase" for r in results[:3])
        force_vector = has_non_ascii
        if (force_vector or _needs_vector(results, limit)) and os.path.exists(vector_db):
            vec_results = _vector_search(
                vector_db, conn, query, limit, type_filter, drift_data,
                warn=warn,
                relaxed=force_vector,
            )
            if vec_results:
                results = _rrf_merge(results, vec_results, limit, has_phrase=has_phrase)

        return _annotate_confidence(results, query)
    finally:
        conn.close()


_translate_mod = None


def _load_translate_module():
    """Lazily load the sibling translate.py (file-path import, like embed/rerank)."""
    global _translate_mod
    if _translate_mod is not None:
        return _translate_mod or None
    try:
        path = os.path.join(os.path.dirname(__file__), "translate.py")
        spec = importlib.util.spec_from_file_location("eidetic_translate", path)
        if spec is None or spec.loader is None:
            _translate_mod = False
        else:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _translate_mod = mod
    except Exception:
        _translate_mod = False
    return _translate_mod or None


_usage_mod = None


def _load_usage_module():
    """Lazily load the sibling usage.py (read-side telemetry)."""
    global _usage_mod
    if _usage_mod is not None:
        return _usage_mod or None
    try:
        path = os.path.join(os.path.dirname(__file__), "usage.py")
        spec = importlib.util.spec_from_file_location("eidetic_usage", path)
        if spec is None or spec.loader is None:
            _usage_mod = False
        else:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _usage_mod = mod
    except Exception:
        _usage_mod = False
    return _usage_mod or None


def _log_usage(results, query, db_path):
    """Record confidently-surfaced cards (which memories actually get used).
    Fail-open: telemetry must never break search."""
    try:
        mod = _load_usage_module()
        if mod is not None:
            mod.log_surfaced(results, query, db_path, _best_confidence(results))
    except Exception:
        pass


def _resolve_query_translation(query):
    """Resolved backend name iff query-translation is ON, the query is
    cross-lingual, and a backend is available — else None (plain native search).
    Default config is "off", so this returns None and search is unchanged."""
    try:
        tr = _load_translate_module()
        if tr is None:
            return None
        configured = tr.active_backend()
        if configured == "off":
            return None
        if not tr.should_translate(query):
            return None
        return tr.resolve_backend(configured)
    except Exception:
        return None


def _fuse_dual(native, translated, limit):
    """Min-rank fusion of two annotated result lists (same as recall_lab's
    _fuse_minrank, dict-level): a doc's fused rank = its best rank across the two
    lists; carry the dict from whichever list ranked it better; native wins ties
    (it is the user's own-language hit). This keeps the cases native wins AND
    surfaces what only the translated query finds."""
    order = {}
    pick = {}
    for source in (native, translated):
        for rank, r in enumerate(source):
            key = (r.get("path"), r.get("section"))
            if key not in order or rank < order[key]:
                order[key] = rank
                pick[key] = r
            elif key not in pick:
                pick[key] = r
    ranked = sorted(order, key=lambda k: order[k])
    return [pick[k] for k in ranked][:limit]


def _translate_timeout():
    """Bounded extra wait for the translated query. A bad/zero/negative value
    falls back to the 8 s default — a config typo must never break native search."""
    try:
        t = float(os.environ.get("EIDETIC_TRANSLATE_TIMEOUT") or 8)
        return t if t > 0 else 8.0
    except (TypeError, ValueError):
        return 8.0


def _search_dual(db_path, query, limit, type_filter, backend, warn):
    """Run native and translated searches concurrently, fuse by min-rank.

    FAIL-OPEN by construction: the native search runs in THIS thread and is always
    returned. The translated query runs in a DAEMON thread, so a slow/wedged
    translator can neither delay process exit (the daemon dies with the process —
    no non-daemon join at interpreter shutdown) nor raise into the caller. A
    timeout, failure, or unavailable translator ⇒ native result only."""
    import threading

    box = {}

    def translated_job():
        try:
            tr = _load_translate_module()
            if tr is None:
                return
            english = tr.translate(query, "en", backend)
            if not english or english.strip().lower() == query.strip().lower():
                return
            box["res"] = _run_query(db_path, english, limit, type_filter, warn=False)
        except Exception:
            pass

    worker = threading.Thread(target=translated_job, daemon=True)
    worker.start()
    native = _run_query(db_path, query, limit, type_filter, warn=warn)  # anchor (main thread)
    worker.join(_translate_timeout())
    translated = box.get("res")
    if not translated:
        return native
    return _fuse_dual(native, translated, limit)


def search(db_path, query, limit=10, type_filter=None, output_json=False, json_object=False, output_mode="auto"):
    """Search FTS5 index with compound ranking."""
    if not os.path.exists(db_path):
        print("ERROR: Index not found. Run: ~/.claude/memory-system/bin/index.sh --full", file=sys.stderr)
        sys.exit(1)

    limit = _normalize_limit(limit)
    warn = not (output_json or json_object)

    backend = _resolve_query_translation(query)
    if backend:
        results = _search_dual(db_path, query, limit, type_filter, backend, warn)
    else:
        results = _run_query(db_path, query, limit, type_filter, warn=warn)

    _log_usage(results, query, db_path)  # read-side telemetry (fail-open, opt-out)

    if output_json or json_object:
        payload = _search_response(query, limit, type_filter, results) if json_object else results
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if not results:
            print(f"No results for: {query}")
            return

        if _best_confidence(results) == "low":
            print(f"No confident results for: {query}")
            print("Weak candidates suppressed. Rephrase, add --type, or use --json to inspect them.")
            return

        auto_broad = output_mode == "auto" and _is_broad_query(query, results)
        if output_mode == "brief" or auto_broad:
            _print_brief_results(query, results, auto_broad=auto_broad)
        else:
            _print_full_results(query, results)


def _vector_search(vector_db, index_conn, query, limit, type_filter, drift_data=None, warn=False, relaxed=False):
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

    min_sim = MULTILINGUAL_VECTOR_MIN_SIM if relaxed else VECTOR_MIN_SIM
    vector_profile = "multilingual" if relaxed else "strict"
    best_per_path = {}
    for sim, chunk_id, path, name, vector_heading, vector_hash in vec_results:
        if sim < min_sim:
            continue
        row = index_conn.execute("""
            SELECT path, type, evidence, source, last_verified, content, section_heading,
                   description,
                   project, card_kind, status, area, supersedes, superseded_by
            FROM memory_chunks WHERE id = ?
        """, (chunk_id,)).fetchone()
        if not row:
            continue
        (row_path, typ, evidence, source, lv, content, heading, desc, project, card_kind,
         status, area, supersedes, superseded_by) = row
        if row_path != path or (heading or "") != vector_heading:
            continue
        if not vector_hash:
            continue
        digest = embed.content_hash(name, desc, content, heading)
        if digest != vector_hash:
            continue
        if type_filter and typ != type_filter:
            continue

        ev_w = EVIDENCE_WEIGHTS.get(evidence, 0.7)
        src_w = SOURCE_WEIGHTS.get(source, 0.5)
        status_w = compute_status_weight(status, superseded_by)
        drift_info = (drift_data or {}).get(path, {})
        dp = drift_info.get("penalty")
        fr_w = dp if dp is not None else compute_freshness(lv)
        compound = sim * ev_w * src_w * fr_w * status_w

        row_dict = {
            "path": path,
            "project": project or "",
            "name": name or "",
            "type": typ or "",
            "evidence": evidence or "observed",
            "source": source or "user-explicit",
            "last_verified": lv,
            "card_kind": card_kind or "",
            "status": status or "current",
            "area": area or "",
            "supersedes": supersedes or "",
            "superseded_by": superseded_by or "",
            "section_heading": heading or "",
            "content": content or "",
            "description": desc or "",
        }
        result = _base_result(
            row_dict,
            drift_info=drift_info,
            drift_penalty=dp,
            freshness=fr_w,
            status_weight=status_w,
        )
        result.update({
            "score": round(compound, 4),
            "retrieval_score": round(compound, 4),
            "fts_rank": 0,
            "vector_score": round(sim, 4),
            "vector_profile": vector_profile,
            "match": "vector",
            "match_quality": round(sim, 3),
        })
        previous = best_per_path.get(path)
        if previous and previous["score"] >= result["score"]:
            continue
        best_per_path[path] = result
    results = list(best_per_path.values())
    results.sort(key=lambda x: x["score"], reverse=True)
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
            entry = data[key]
            entry["vector_score"] = max(entry.get("vector_score", 0), r.get("vector_score", 0))
            entry["vector_profile"] = r.get("vector_profile", entry.get("vector_profile", "strict"))
            entry["match"] = "hybrid"
            entry["match_quality"] = round(
                max(entry.get("match_quality", 0), r.get("match_quality", 0)),
                3,
            )
            entry["retrieval_score"] = round(
                max(entry.get("retrieval_score", entry.get("score", 0)),
                    r.get("retrieval_score", r.get("score", 0))),
                4,
            )

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for key, rrf_score in ranked[:limit]:
        entry = data[key]
        entry.setdefault("retrieval_score", entry.get("score", 0))
        entry["rrf_score"] = round(rrf_score, 4)
        entry["score"] = round(rrf_score, 4)
        results.append(entry)
    return results


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: search.sh <query> [--limit N] [--type TYPE] [--brief|--full] [--json|--json-object]\n"
            "       search.sh --detail <detail_id|path> [--section SECTION] [--json-object]",
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = sys.argv[1]
    query = None
    detail_selector = None
    detail_requested = False
    section = None
    limit = 10
    type_filter = None
    output_json = False
    json_object = False
    output_mode = "auto"

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
        elif arg == "--json-object":
            json_object = True
            i += 1
        elif arg == "--brief":
            output_mode = "brief"
            i += 1
        elif arg == "--full":
            output_mode = "full"
            i += 1
        elif arg in ("--detail", "--search-detail"):
            detail_requested = True
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                detail_selector = sys.argv[i + 1]
                i += 2
            else:
                detail_selector = ""
                i += 1
        elif arg == "--section" and i + 1 < len(sys.argv):
            section = sys.argv[i + 1]
            i += 2
        elif query is None:
            query = arg
            i += 1
        else:
            query = (query or "") + " " + arg
            i += 1

    if detail_requested:
        if not (detail_selector or "").strip():
            print("ERROR: detail selector is required", file=sys.stderr)
            sys.exit(1)
        search_detail(db_path, detail_selector, section, output_json, json_object)
        return

    if not query:
        print("ERROR: No query provided", file=sys.stderr)
        sys.exit(1)

    search(db_path, query, limit, type_filter, output_json, json_object, output_mode)


if __name__ == "__main__":
    main()
