#!/usr/bin/env python3
"""Eidetic v6 STEP 1B — evidence event emission (append-under-lock).

The deterministic writer-path API that lands typed lifecycle events on a card's
`## Evidence` section (spec §3.2, §4.5). The markdown is the durable truth; the
`card_events` projection + `confidence` fold are rebuilt from it on reindex.

Rules honoured here:
  * DUAL-WRITE = ONE append to `## Evidence` (append-only, human-auditable).
  * Deterministic actor tier + Δ annotation per event_type — an LLM may suggest
    WHICH card a signal maps to, but never the Δ (§4.5). The fold recomputes the
    effective delta; the written Δ is decorative/audit.
  * Concurrency-safe: the append runs under a non-blocking flock so two sessions
    ending together cannot interleave a half-written line (risk #6), and an
    identical (ts, event_type) line is de-duped rather than double-applied.

Nothing here is public API; it is an internal v6 rail.
"""

import fcntl
import os
import re
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import confidence as _C
except ImportError:  # pragma: no cover
    _C = None

# event_type -> (actor label written to the line, actor_tier). Deterministic.
_ACTOR_FOR = {
    "observed": ("agent-extracted", 1),
    "verified_by_test": ("test", 2),
    "confirmed": ("user-explicit", 3),
    "corrected": ("user-explicit", 3),
    "contradicted": ("user-explicit", 3),  # tier 2 when test-sourced (pass actor="test")
    "decayed": ("system", 2),
}
_ACTOR_TIER = {"agent-extracted": 1, "agent": 1, "test": 2, "verification": 2,
               "user-explicit": 3, "system": 2}


def _base_delta(event_type):
    if _C is not None:
        return _C.EVENT_SPECS.get(event_type, {"delta": 0.0})["delta"]
    return {"observed": 0.05, "verified_by_test": 0.15, "confirmed": 0.20,
            "corrected": -0.40, "contradicted": -0.30, "decayed": -0.10}.get(event_type, 0.0)


def format_line(event_type, actor, ts, session_id=None, delta=None, note=""):
    """Render one `## Evidence` line (parsed back by index_impl.parse_evidence_events)."""
    if delta is None:
        delta = _base_delta(event_type)
    parts = [ts, event_type, actor]
    if session_id:
        parts.append(f"sess={session_id}")
    parts.append(f"Δ{'+' if delta >= 0 else ''}{delta:.2f}")
    if note:
        clean = str(note).replace("\n", " ").replace('"', "'").strip()
        parts.append(f'"{clean}"')
    return "- " + " · ".join(parts)


def _insert_into_evidence(content, line):
    """Return content with `line` appended to the `## Evidence` section (created
    at the end if absent). Idempotent: if an identical (ts · event_type) line
    already exists, returns content unchanged."""
    ts_type = " · ".join(line[2:].split(" · ")[:2])  # "<ts> · <event_type>"
    for existing in content.splitlines():
        st = existing.strip()
        if st.startswith("- ") and st[2:].startswith(ts_type):
            return content  # de-dup (risk #6)

    lines = content.splitlines(keepends=True)
    # find the `## Evidence` heading and the end of its block (next `## ` or EOF)
    ev_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().lower() == "## evidence":
            ev_idx = i
            break
    if ev_idx is None:
        body = content.rstrip("\n")
        return body + f"\n\n## Evidence\n\n{line}\n"
    end = len(lines)
    for j in range(ev_idx + 1, len(lines)):
        if lines[j].lstrip().startswith("## "):
            end = j
            break
    block = "".join(lines[ev_idx:end]).rstrip("\n")
    rest = "".join(lines[end:])
    new_block = block + f"\n{line}\n"
    prefix = "".join(lines[:ev_idx])
    if rest:
        new_block += "\n"
    return prefix + new_block + rest


def append_event(card_path, event_type, actor=None, session_id=None, note="",
                 ts=None, delta=None):
    """Append one typed event to a card's `## Evidence` under an exclusive lock.

    Returns True if a line was written, False on a no-op (unknown type, missing
    file, lock contention, or a de-duped identical event). Never raises.
    """
    if event_type not in _ACTOR_FOR:
        return False
    if not card_path or not os.path.exists(card_path):
        return False
    if actor is None:
        actor = _ACTOR_FOR[event_type][0]
    ts = ts or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    line = format_line(event_type, actor, ts, session_id=session_id, delta=delta, note=note)

    lock_path = card_path + ".evlock"
    lock_fd = None
    acquired = False
    try:
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            return False  # another writer holds it — the caller may retry
        with open(card_path, "r", encoding="utf-8") as f:
            content = f.read()
        new_content = _insert_into_evidence(content, line)
        if new_content == content:
            return False  # de-duped
        d = os.path.dirname(card_path)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".evtmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp, card_path)
        return True
    except Exception:
        return False
    finally:
        if lock_fd is not None:
            lock_fd.close()
        # Only remove the lock file if WE held it — never delete a lock another
        # writer currently owns (that would break mutual exclusion under contention).
        if acquired:
            try:
                os.unlink(lock_path)
            except OSError:
                pass


# --- typed emitters (deterministic tier + Δ; the writer-path entry points) ----
def observed(card_path, session_id=None, note=""):
    """Agent re-derived the pattern (tier 1). §4.5 bullet 1."""
    return append_event(card_path, "observed", session_id=session_id, note=note)


def confirmed(card_path, session_id=None, note=""):
    """User re-affirmed the behavior (tier 3)."""
    return append_event(card_path, "confirmed", session_id=session_id, note=note)


def corrected(card_path, session_id=None, note=""):
    """User corrected the behavior (tier 3, the strongest negative)."""
    return append_event(card_path, "corrected", session_id=session_id, note=note)


def verified_by_test(card_path, session_id=None, note=""):
    """A test the card is tied to passed (tier 2). §4.5 bullet 3."""
    return append_event(card_path, "verified_by_test", session_id=session_id, note=note)


def decayed(card_path, note="age_stale silence"):
    """Synthetic silence event (§4.3). Emitted by the indexer, not a user/test."""
    return append_event(card_path, "decayed", note=note)


def main(argv=None):
    argv = argv or sys.argv
    if len(argv) < 3:
        print("Usage: evidence.py <card_path> <event_type> [actor] [note]", file=sys.stderr)
        return 1
    ok = append_event(argv[1], argv[2],
                      actor=argv[3] if len(argv) > 3 else None,
                      note=argv[4] if len(argv) > 4 else "")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
