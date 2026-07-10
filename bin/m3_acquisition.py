#!/usr/bin/env python3
"""FR-3 — the acquisition dark lane (ADR-0001): decision / finding / rule.

Route, per candidate: mechanical quote gate → judge (claim ⊨ quote, judged as
ONE unit — no `_split_claims`; a multi-sentence claim only partially supported
by its quote rejects WHOLE) → dark log. ZERO writes outside `events/` (NFR-2):
until the D5 gate passes, nothing here touches /memory/, the index DB, the
oplog, or confidence events.

Grounding is the SESSION TRANSCRIPT itself (self-attestation, CONTEXT.md): the
quote must be a contiguous verbatim substring of ONE assistant turn's cleaned
text, compared under the judge's own normalization (`m3_judge._norm_tokens` —
one dialect, not two, NFR-6; `_norm_tokens` casefold-token-rejoins the STRING,
so contiguity survives normalization). Cleaned text means `<system-reminder>`
and local-command blocks are already stripped by `read_turns` — injected
content is unquotable by construction. USER turns are NOT quotable (owner
gate 2026-07-10, spec §10 Q1: fail-toward-miss beats fail-toward-junk).

This checks faithful copy, not truth (ADR-0001) — the residual truth risk is
priced by the D4 trust contract at activation, not by this gate.

Fail-toward-reject at every layer (NFR-3): quote absent → reject, ZERO judge
calls burned; judge route dead → `judge_unavailable`, never `would_file` (no
silent overlap fallback for acquisition); one candidate's failure never kills
the run (hook contract).
"""
import os
import sys
from pathlib import Path

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_judge  # noqa: E402
import m3_recall_miner as _miner  # noqa: E402  (read_turns — the SAME reader that mined)

try:
    import lifecycle_signals as _LC  # noqa: E402
except Exception:  # pragma: no cover
    _LC = None

DARK_FILE = "m3_acquisition_dark.jsonl"
MIN_QUOTE_TOKENS = 6  # the judge's verbatim gate floor (_quote_ok) — one fact


def _events_dir(memory_system):
    root = memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))
    return os.path.join(str(root), "events")


def quote_in_assistant_turns(quote, turns):
    """Mechanical quote gate: normalized-contiguous substring of ONE assistant
    turn's cleaned text. `turns` = read_turns output [(role, text)]."""
    qn = m3_judge._norm_tokens(quote or "")
    if len(qn.split()) < MIN_QUOTE_TOKENS:
        return False
    return any(role == "assistant" and qn in m3_judge._norm_tokens(text)
               for role, text in turns or [])


def _append_dark(memory_system, record):
    """Dark-log append via the shared O_APPEND helper (the m3_filed.jsonl
    pattern). Best-effort — a log miss never blocks the lane."""
    if _LC is None:
        return False
    try:
        return _LC._atomic_append_jsonl(
            Path(os.path.join(_events_dir(memory_system), DARK_FILE)),
            _LC._compact_json(record))
    except Exception:
        return False


def _process_one(cand, turns, memory_system):
    """→ outcome ∈ would_file | would_reject | judge_unavailable | error.
    would_file / would_reject are DEFINITIVE (seen-cacheable, FR-8); the other
    two are transient — retried on the next Stop."""
    claim = (cand.get("claim") or "").strip()
    quote = (cand.get("transcript_quote") or "").strip()
    quote_ok = bool(claim) and quote_in_assistant_turns(quote, turns)
    judge = None  # null in the dark log = judge never called (quote gate failed)
    if quote_ok:
        judge = m3_judge.verdict(claim, [quote])
    would_file = bool(quote_ok and judge == "entailed")
    _append_dark(memory_system, {
        "ts": _LC._recorded_at() if _LC else "",
        "session_id": str(cand.get("session_id") or ""),
        "project_slug": str(cand.get("project_slug") or ""),
        "kind": str(cand.get("kind") or ""),
        "claim": claim,
        "transcript_quote": quote,
        "quote_ok": quote_ok,
        "judge": judge,
        "would_file": would_file,
    })
    if would_file:
        return "would_file"
    if not quote_ok or judge == "not_entailed":
        return "would_reject"
    if judge == "judge_unavailable":
        return "judge_unavailable"
    return "error"


def process(transcript_path, candidates, *, memory_system=None, turns=None):
    """Run acquisition candidates through the dark lane.

    Returns (tally, outcomes): tally for the hook's one-line JSON, outcomes
    aligned 1:1 with `candidates` for the FR-8 seen-cache. `turns` may be
    injected (tests); default = re-read the SAME transcript via the miner's
    own `read_turns` (identical cleaning — the quote is checked against
    exactly what was minable)."""
    tally = {}
    outcomes = []
    if turns is None:
        try:
            turns = _miner.read_turns(transcript_path)
        except Exception:
            turns = []
    for cand in candidates or []:
        try:
            outcome = _process_one(cand, turns, memory_system)
        except Exception:  # one candidate never kills the run (hook contract)
            outcome = "error"
        tally[outcome] = tally.get(outcome, 0) + 1
        outcomes.append(outcome)
    return tally, outcomes
