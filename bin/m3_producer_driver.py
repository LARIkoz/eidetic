#!/usr/bin/env python3
"""FR-2 — the provenance-record producer / driver (the missing "turn-2 seam").

The M3 dry-run (2026-07-08) proved `file_recalled_answer` is called by NOTHING;
this is the driver that calls it. Design fork resolved AGAINST the SPEC's
recommended site (a):

  Site (a) "the memory-recall/answer path writes the record" — does NOT exist as
  a code path in the personal system. Recall = card INJECTION (assemble_context);
  the *answer* is synthesized by the agent in-session, not by a script. Building
  (a) needs a new agent-cooperation protocol (the agent must self-report which
  cards+spans it used every turn — its own confabulation surface).

  Site (b) "a session-end miner" — the infra ALREADY EXISTS and is hardened:
  `session-signals.sh` runs an LLM extraction over the transcript with explicit
  anti-confabulation rules. And (b)'s one documented downside ("inherits the
  confabulation risk") is exactly what the FR-1 judge now neutralizes: a
  miner-confabulated citation cannot pass claim⊨span entailment.

So (b), with a twist that removes the citation-confabulation risk entirely: the
miner proposes only {recalled_answer, recall_query}; this producer RETRIEVES the
source cards itself (same recipe as the dry-run's harvest) and the FR-1 judge
verifies each claim against the retrieved spans. The miner never asserts a
source; grounding is retrieved + independently judged.

Two dark flags gate this end-to-end:
  EIDETIC_M3_DRIVER   — this driver runs at all (default OFF).
  EIDETIC_M3_AUTOFILE — file_recalled_answer's own FR-7 dark gate (default OFF).
Both must be ON to file. Reversible: delete this module + its (unbuilt) hook.
"""
import os
import sqlite3
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_autofile  # noqa: E402
import m3_judge  # noqa: E402


def _driver_active():
    return os.environ.get("EIDETIC_M3_DRIVER", "").strip().lower() in ("1", "on", "true", "yes")


def _chunk_body(conn, path, section):
    row = conn.execute(
        "SELECT content FROM memory_chunks WHERE path=? AND IFNULL(section_heading,'')=? LIMIT 1",
        (path, section or ""),
    ).fetchone()
    return (row[0] or "") if row else ""


def resolve_sources(index_db_path, recall_query, *, k=3, max_span=900, conn=None):
    """Retrieve the cited-source spans for a recall query — the SAME recipe the
    dry-run used to harvest provenance (search_impl._run_query → top /memory/
    cards → chunk body). Returns [{card_id, path, section, span, score}], ≤ k.

    The producer grounds the answer itself; the miner supplies only the query.
    """
    import search_impl
    own = conn is None
    if own:
        conn = sqlite3.connect(index_db_path)
        conn.row_factory = sqlite3.Row
    try:
        try:
            results = search_impl._run_query(index_db_path, recall_query, max(k * 3, 8), None)
        except Exception:
            results = []
        sources = []
        for r in results:
            p = r.get("path") or ""
            if "/memory/" not in p or not os.path.isfile(p):
                continue
            body = _chunk_body(conn, p, r.get("section") or "")
            if not body.strip():
                continue
            sources.append({
                "card_id": r.get("name") or os.path.basename(p)[:-3],
                "path": p, "section": r.get("section") or "",
                "score": r.get("score"), "span": body[:max_span],
            })
            if len(sources) >= k:
                break
        return sources
    finally:
        if own:
            conn.close()


def build_provenance(index_db_path, answer_text, recall_query, *,
                     session_id, project_slug="", k=3, conn=None):
    """{answer_text, sources:[{card_id, span}], recall_query, session_id, project_slug}
    — the exact record shape file_recalled_answer expects (SPEC §FR-2)."""
    return {
        "answer_text": answer_text,
        "recall_query": recall_query,
        "sources": resolve_sources(index_db_path, recall_query, k=k, conn=conn),
        "session_id": session_id,
        "project_slug": project_slug,
    }


def drive(index_db_path, candidates, *, memory_dir=None, cwd=None,
          register_judge=True, k=3):
    """Register the FR-1 judge (soft-degrade if absent) and run every candidate
    through file_recalled_answer. `candidates` = [{answer_text, recall_query,
    session_id?, project_slug?}]. Returns (outcomes, judge_active).

    Honors the driver dark gate: if EIDETIC_M3_DRIVER is off, returns ([], False)
    without touching the store — the file gate (EIDETIC_M3_AUTOFILE) is a second,
    independent lock (both must be ON to file)."""
    if not _driver_active():
        return [], False
    judge_active = m3_judge.register(m3_autofile) if register_judge else False
    conn = sqlite3.connect(index_db_path)
    conn.row_factory = sqlite3.Row
    outcomes = []
    try:
        for cand in candidates:
            prov = build_provenance(
                index_db_path, cand.get("answer_text") or "",
                cand.get("recall_query") or "",
                session_id=cand.get("session_id") or "",
                project_slug=cand.get("project_slug") or "", k=k, conn=conn)
            try:
                o = m3_autofile.file_recalled_answer(
                    index_db_path, prov, memory_dir=memory_dir, cwd=cwd)
            except Exception as ex:  # never let one candidate kill the run
                o = {"action": "ERROR", "reason": repr(ex)[:200]}
            o["recall_query"] = cand.get("recall_query")
            o["n_sources"] = len(prov["sources"])
            outcomes.append(o)
    finally:
        conn.close()
    return outcomes, judge_active
