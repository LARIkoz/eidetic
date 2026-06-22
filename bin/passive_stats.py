#!/usr/bin/env python3
"""Eidetic passive_stats — did the injected memory get REFERENCED in the session?

Phase 1 BENEFIT proxy (LEXICAL, no embedding — per the 2026-06-22 value audit).
At SessionEnd, reads the saved transcript + the matching inject_log row (what was
injected at SessionStart) and counts how many injected card SLUGS literally appear
in the session's OWN work — assistant text/reasoning + tool calls + tool results —
NOT in the injected context block (that would be self-reference, the audit's MINOR-2).

  referenced_k is a LOWER BOUND on "the card helped": a rule applied by paraphrase
  without naming its slug is missed. It is the honest, low-false-positive signal the
  audit kept (literal reference) over embedding-cosine (invalid + a 2GB fan-cost).
  It does NOT prove causation — see the spec's §7 attribution caveat.

Writes one append-only row to db/session_value.jsonl. FAIL-OPEN (never breaks the
SessionEnd hook), privacy-safe (slugs + counts only, never raw transcript text).

Usage:
  passive_stats.py --transcript /path/to/session.jsonl   # SessionEnd hook passes this
  passive_stats.py                                        # else: newest chat-log
"""

import argparse
import glob
import json
import os
import re
import sys
import time

# Phase 2 (collect-only, UNVERIFIED per audit I1): correction signals in USER turns.
# Used ONLY for a directional corrections/session trend — never for any automated
# pruning/compression decision until an LLM classifier confirms precision.
CORRECTION_RE = re.compile(
    r"долб[оа]ёб|уже сделано|уже есть|нет,?\s*не так|\bне то\b|я же (?:сказал|просил)|"
    r"откати|отмени|неверно|ты сломал|это не то|зачем ты|"
    r"\bwrong\b|that'?s not|\bi said\b|undo that|revert that|you broke|not what i",
    re.IGNORECASE)
SYSREM_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

DB_DIR = os.path.expanduser("~/.claude/memory-system/db")
INJECT_LOG = os.path.join(DB_DIR, "inject_log.jsonl")
SESSION_VALUE = os.path.join(DB_DIR, "session_value.jsonl")
CHAT_LOGS = os.path.expanduser("~/.claude/chat-logs")


def _iter_jsonl(path):
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except Exception:
                continue  # a torn line never crashes the run


def parse_transcript(path):
    """Return (session_id, project, work_text_lowercased).

    work_text = the session's OWN output only — assistant text/thinking/tool_use +
    the tool_results of its calls. We deliberately SKIP attachments, queue-ops, and
    user free-text: those carry the injected memory-context / MEMORY.md, and counting
    a slug there would be self-reference (a card 'referenced' merely because injected)."""
    sid = None
    cwd = None
    parts = []        # the session's OWN work (assistant + tool results) -> referenced_k
    user_parts = []   # user free-text turns -> corrections (NEVER mixed into work)
    for o in _iter_jsonl(path):
        sid = sid or o.get("sessionId")
        cwd = cwd or o.get("cwd")
        typ = o.get("type")
        if typ not in ("assistant", "user"):
            continue
        msg = o.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            (parts if typ == "assistant" else user_parts).append(content)
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if typ == "assistant" and bt in ("text", "thinking"):
                parts.append(b.get(bt) or b.get("text") or "")
            elif typ == "assistant" and bt == "tool_use":
                parts.append(json.dumps(b.get("input") or {}, ensure_ascii=False))
            elif typ == "user" and bt == "tool_result":
                c = b.get("content")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    for cc in c:
                        if isinstance(cc, dict) and cc.get("type") == "text":
                            parts.append(cc.get("text") or "")
            elif typ == "user" and bt == "text":
                user_parts.append(b.get("text") or "")
    proj = os.path.basename((cwd or "").rstrip("/")) or None
    return sid, proj, "\n".join(parts).lower(), "\n".join(user_parts)


def find_inject_row(session_id, project):
    """The inject_log row for this session: exact session_id, else newest for project."""
    rows = list(_iter_jsonl(INJECT_LOG))
    if session_id:
        for r in reversed(rows):
            if r.get("session_id") and r.get("session_id") == session_id:
                return r
    if project:
        for r in reversed(rows):
            if r.get("project") == project:
                return r
    return rows[-1] if rows else None


def newest_chatlog():
    files = glob.glob(os.path.join(CHAT_LOGS, "session-2*.jsonl"))
    files += glob.glob(os.path.join(CHAT_LOGS, "session-live.json"))
    return max(files, key=os.path.getmtime) if files else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", help="session transcript JSONL (SessionEnd passes this)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        if os.environ.get("EIDETIC_VALUE_TELEMETRY", "on").strip().lower() == "off":
            return 0
        path = args.transcript or os.environ.get("CLAUDE_TRANSCRIPT_PATH") or newest_chatlog()
        if not path or not os.path.exists(path):
            return 0
        sid, proj, work, user_text = parse_transcript(path)
        inj = find_inject_row(sid, proj)
        if not inj:
            return 0
        slugs = [s for s in (inj.get("slugs") or []) if s]
        n = len(slugs)
        # Literal lower-bound match. Require slug length >= 6 to avoid accidental
        # substring hits from short generic tokens.
        referenced = sorted({s for s in slugs if len(s) >= 6 and s.lower() in work})
        # Phase 2 (UNVERIFIED, directional): correction signals in user turns, with
        # injected-context stripped. Privacy: store only the count + the generic
        # trigger words matched, NEVER the raw user text.
        clean_user = SYSREM_RE.sub("", user_text)
        triggers = sorted({m.group(0).lower() for m in CORRECTION_RE.finditer(clean_user)})
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session_id": sid or "",
            "project": proj or inj.get("project") or "unknown",
            "n_cards": n,
            "referenced_k": len(referenced),
            "utilization": round(len(referenced) / n, 4) if n else 0,
            "referenced_slugs": referenced,
            "corrections_n": len(CORRECTION_RE.findall(clean_user)),
            "correction_triggers": triggers,
        }
        fd = os.open(SESSION_VALUE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        if not args.quiet:
            print(f"session_value: {len(referenced)}/{n} injected cards referenced "
                  f"({row['utilization']*100:.0f}% util) — {row['project']}")
    except Exception:
        pass  # fail-open: telemetry must never break the SessionEnd hook
    return 0


if __name__ == "__main__":
    sys.exit(main())
