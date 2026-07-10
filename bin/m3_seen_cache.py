#!/usr/bin/env python3
"""FR-8 — session-scoped judged-candidate cache (stop re-judging on every Stop).

The Stop hook fires many times per session and the 30-turn miner window keeps
re-containing the same exchange; each pass re-burned a judge call (observed
live: one claim judged 3x on consecutive Stops of one session), and the v3
acquisition dark lane would also re-LOG the same candidate — inflating the D5
report's yield counts. This cache keys candidates by
sha1(kind + normalized text), scoped to session_id, and is checked BEFORE
producer retrieval and the judge.

Only DEFINITIVE outcomes are cached — consolidation: filed / deduped_to_m2 /
rejected; dark acquisition: would_file / would_reject (a dark success is
definitive too — without it the same candidate re-judges every Stop, defeating
the cache). Transient failures (judge_unavailable, SDK/route errors, parse
fails) are NOT cached: retrying on the next Stop is correct. Cross-session
repeats are judged again by design (store and spans evolve; cost negligible).

Persistence: append-only `<memory-system>/events/m3_judged.jsonl` via
lifecycle_signals' shared O_APPEND single-write helper (the m3_filed.jsonl
pattern). Text normalization reuses the judge's `_norm_tokens` — one
normalization dialect, not two (NFR-6).
"""
import hashlib
import json
import os
import sys
from pathlib import Path

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_judge  # noqa: E402  (_norm_tokens — the one normalization dialect)

try:
    import lifecycle_signals as _LC  # noqa: E402
except Exception:  # pragma: no cover
    _LC = None

JUDGED_FILE = "m3_judged.jsonl"
DEFINITIVE = frozenset(
    {"filed", "deduped_to_m2", "rejected", "would_file", "would_reject"})


def candidate_key(cand):
    """sha1(kind + normalized candidate text) — spec §7: recall keys on
    query+answer; acquisition keys on claim alone (the quote may drift while
    the claim is the same knowledge)."""
    kind = (cand.get("kind") or "recall").strip().lower()
    if kind == "recall":
        text = (cand.get("recall_query") or "") + "\n" + (cand.get("recalled_answer") or "")
    else:
        text = cand.get("claim") or ""
    normalized = m3_judge._norm_tokens(text)
    return hashlib.sha1((kind + "\n" + normalized).encode("utf-8")).hexdigest()


def _judged_path(memory_system):
    root = memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))
    return os.path.join(str(root), "events", JUDGED_FILE)


def load_seen(memory_system, session_id):
    """The set of keys with a DEFINITIVE outcome for THIS session. Malformed
    lines are skipped (metadata, fail toward re-judge); a missing file ⇒ empty
    set. Never raises."""
    seen = set()
    if not session_id:
        return seen
    try:
        with open(_judged_path(memory_system), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("session_id") == str(session_id) and \
                        row.get("outcome") in DEFINITIVE and row.get("key"):
                    seen.add(row["key"])
    except OSError:
        return seen
    return seen


def record(memory_system, session_id, key, kind, outcome):
    """Append one outcome row IF it is definitive. Best-effort: returns bool,
    never raises (cache misses only cost a re-judge)."""
    if outcome not in DEFINITIVE or not session_id or not key or _LC is None:
        return False
    rec = {"ts": _LC._recorded_at(), "session_id": str(session_id),
           "key": key, "kind": str(kind or ""), "outcome": outcome}
    try:
        return _LC._atomic_append_jsonl(Path(_judged_path(memory_system)),
                                        _LC._compact_json(rec))
    except Exception:
        return False
