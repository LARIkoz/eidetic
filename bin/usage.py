#!/usr/bin/env python3
"""Eidetic usage telemetry — which memory cards actually get surfaced.

Eidetic tracks what it LEARNS (op-log writes, signals) but not what it USES. This
records the READ side: every time a search surfaces a card in a confident result,
one append-only line lands in usage.log. From that, usage_stats.py answers "which
cards are pulled the most" and "which are dead weight" (never surfaced → prune).

Design (matches eidetic's existing patterns):
  - APPEND-ONLY JSONL. Atomic O_APPEND writes don't corrupt under the owner's many
    parallel sessions — unlike a shared SQLite writer, which would contend.
  - FAIL-OPEN. Any error is swallowed: usage logging must never break search.
  - PRIVACY. The raw query is NEVER written — only a short hash — so the log is
    safe in a public tool. Card identity is its (path, section), not text.
  - CONFIDENT-ONLY. Logs cards in a medium+ confidence result set (a card buried in
    low-confidence noise was not usefully surfaced), capped at the top few per search.

Opt out: EIDETIC_USAGE_LOG=off. Default on (a cheap append).
"""

import hashlib
import json
import os
import time

TOP_K_LOG = 5  # at most this many surfaced cards logged per search (bounds volume)
_CONFIDENT = ("medium", "high")


def usage_log_path(db_path):
    """usage.log next to index.db (alongside vectors.db / drift_state.db).

    EIDETIC_USAGE_LOG_PATH overrides the destination — used by the doctor canary
    to verify the logger FIRES against a TEMP log (so a health check never writes
    to the real usage.log and poisons the dead-card / top-used telemetry), and by
    tests for isolation. Empty/unset ⇒ the default next-to-db path (unchanged)."""
    override = os.environ.get("EIDETIC_USAGE_LOG_PATH", "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "usage.log")


def _query_hash(query):
    return hashlib.sha256((query or "").encode("utf-8", "replace")).hexdigest()[:12]


def log_surfaced(results, query, db_path, best_confidence):
    """Append one JSONL line per confidently-surfaced card. Fail-open, privacy-safe.

    `results` is the final ranked list; `best_confidence` gates logging to the
    case where the user actually saw a confident answer (the honest "used" signal)."""
    try:
        if os.environ.get("EIDETIC_USAGE_LOG", "on").strip().lower() == "off":
            return
        if not results or best_confidence not in _CONFIDENT:
            return
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        qh = _query_hash(query)
        rows = []
        for rank, r in enumerate(results[:TOP_K_LOG], 1):
            rows.append(json.dumps({
                "ts": ts,
                "path": r.get("path", ""),
                "section": r.get("section", ""),
                "rank": rank,
                "confidence": r.get("confidence"),
                "qh": qh,
            }, ensure_ascii=False))
        if not rows:
            return
        with open(usage_log_path(db_path), "a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
    except Exception:
        pass  # telemetry must never break search
