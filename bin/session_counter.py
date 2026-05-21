#!/usr/bin/env python3
"""AI Memory System v2 — Session Counter + Phase-Adaptive Hints.

Tracks session count per project. Returns behavioral hints based on experience level:
  <10 sessions: explain more, confirm assumptions
  10-30: standard mode
  >30: be proactive, skip explanations, anticipate needs

Counter persisted in sessions.db (SQLite).
"""

import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.expanduser("~/.claude/memory-system/db/sessions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_log (
    id INTEGER PRIMARY KEY,
    project TEXT NOT NULL,
    started_at TEXT NOT NULL,
    cwd TEXT
);
"""

HINTS = {
    "novice": (
        "Session experience: LOW (<10 sessions in this project). "
        "Explain decisions in detail. Confirm assumptions before acting. "
        "Show options when unsure."
    ),
    "standard": (
        "Session experience: MODERATE (10-30 sessions). "
        "Standard operating mode. Explain non-obvious decisions only."
    ),
    "veteran": (
        "Session experience: HIGH (30+ sessions). "
        "Be proactive. Skip explanations for established patterns. "
        "Anticipate needs. Flag only novel situations."
    ),
}


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def detect_project(cwd):
    cwd = cwd.rstrip("/")
    return cwd.replace("/", "-").lstrip("-") or "unknown"


def record_session(conn, project, cwd):
    conn.execute(
        "INSERT INTO session_log (project, started_at, cwd) VALUES (?, ?, ?)",
        (project, datetime.now().isoformat(), cwd),
    )
    conn.commit()


def get_count(conn, project):
    row = conn.execute(
        "SELECT COUNT(*) FROM session_log WHERE project = ?",
        (project,),
    ).fetchone()
    return row[0] if row else 0


def get_hint(count):
    if count < 10:
        return HINTS["novice"], "novice"
    elif count <= 30:
        return HINTS["standard"], "standard"
    else:
        return HINTS["veteran"], "veteran"


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    mode = sys.argv[2] if len(sys.argv) > 2 else "record-and-hint"

    conn = init_db()
    project = detect_project(cwd)

    if mode == "record-and-hint":
        record_session(conn, project, cwd)
        count = get_count(conn, project)
        hint, phase = get_hint(count)
        print(f"SESSION #{count} ({phase}) | {hint}")
    elif mode == "hint-only":
        count = get_count(conn, project)
        hint, phase = get_hint(count)
        print(f"SESSION #{count} ({phase}) | {hint}")
    elif mode == "count":
        count = get_count(conn, project)
        print(count)
    elif mode == "stats":
        rows = conn.execute(
            "SELECT project, COUNT(*), MAX(started_at) FROM session_log GROUP BY project ORDER BY COUNT(*) DESC"
        ).fetchall()
        for proj, cnt, last in rows:
            _, phase = get_hint(cnt)
            print(f"  {cnt:3d} sessions  {phase:8s}  {proj}  (last: {last[:10]})")

    conn.close()


if __name__ == "__main__":
    main()
