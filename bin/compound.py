#!/usr/bin/env python3
"""AI Memory System v1 — Compounding Logic (FR4.1, Karpathy).

Reads signals from stdin, for each:
1. Search FTS5 for existing memory on same topic
2. If match found → update existing file + add History section
3. If no match → create new signal file
4. Source: agent-extracted (0.5x self-referential discount)

"Humans abandon wikis because maintenance grows faster than value. LLMs don't get bored."
"""

import os
import re
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.expanduser("~/.claude/memory-system/db/index.db")
TODAY = datetime.now().strftime("%Y-%m-%d")


def search_fts5(conn, query, limit=3):
    """Search FTS5 for existing memory on same topic."""
    sanitized = re.sub(r'[*()\[\]{}^~:+\-]', ' ', query)
    sanitized = sanitized.replace('"', '""')
    words = [w for w in sanitized.split() if len(w) > 2 and w.upper() not in ("AND", "OR", "NOT", "NEAR")]
    if not words:
        return []
    fts_query = '"' + " ".join(words[:6]) + '"'

    try:
        rows = conn.execute("""
            SELECT c.path, c.name, c.section_heading, c.content,
                   memory_fts.rank AS fts_rank
            FROM memory_fts
            JOIN memory_chunks c ON memory_fts.rowid = c.id
            WHERE memory_fts MATCH ?
            ORDER BY memory_fts.rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return rows
    except sqlite3.OperationalError:
        return []


def extract_keywords(signal_text):
    """Extract meaningful keywords from a signal for FTS5 search."""
    words = re.findall(r'\b[a-zA-Z_-]{4,}\b', signal_text)
    stopwords = {
        "that", "this", "with", "from", "have", "been", "were", "will",
        "would", "could", "should", "about", "their", "which", "when",
        "what", "more", "than", "very", "also", "just", "into", "only",
        "other", "some", "such", "because", "before", "after", "made",
        "decision", "rule", "worked", "failed", "knowledge",
    }
    keywords = [w for w in words if w.lower() not in stopwords]
    return " ".join(keywords[:10])


PROTECTED_TYPES = {"feedback", "user"}


def _get_file_type(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(500)
    except Exception:
        return None
    for line in head.split("\n"):
        m = re.match(r'^\s*type:\s*(\S+)', line.strip())
        if m:
            return m.group(1)
    return None


def update_existing(filepath, signal_text):
    """Update existing memory file: append to History section. Does NOT update last_verified."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False

    history_entry = f"- {TODAY}: {signal_text.strip()}\n"

    if "## History" in content:
        content = content.rstrip() + "\n" + history_entry
    else:
        content = content.rstrip() + f"\n\n## History\n\n{history_entry}"

    # Do NOT auto-bump last_verified — agent-extracted signals should not
    # inflate freshness of human-curated memories. Only explicit human
    # verification should update this field.
    if False and "last_verified:" in content:
        content = re.sub(
            r'last_verified:\s*\S+',
            f'last_verified: {TODAY}',
            content,
            count=1,
        )

    tmp = None
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(filepath), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, filepath)
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def create_signal_file(cwd, signals):
    """Create new signal file for signals without existing matches."""
    sanitized = "-" + cwd.replace("/", "-").lstrip("-")

    memory_dir = None
    projects_dir = os.path.expanduser("~/.claude/projects/")
    if os.path.isdir(projects_dir):
        for d in os.listdir(projects_dir):
            if sanitized.endswith(d) or d.endswith(sanitized.split("-")[-1]):
                candidate = os.path.join(projects_dir, d, "memory")
                if os.path.isdir(candidate):
                    memory_dir = candidate
                    break
        if not memory_dir:
            candidate = os.path.join(projects_dir, sanitized, "memory")
            if os.path.isdir(candidate):
                memory_dir = candidate

    if not memory_dir:
        memory_dir = os.path.expanduser("~/.claude/memory-system/")
        os.makedirs(memory_dir, exist_ok=True)

    signals_dir = os.path.join(memory_dir, "signals")
    os.makedirs(signals_dir, exist_ok=True)

    filepath = os.path.join(signals_dir, f"{TODAY}.md")

    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            existing = f.read()
        new_lines = [f"- {s.strip()}\n" for s in signals if s.strip() not in existing]
        if new_lines:
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=signals_dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(existing.rstrip("\n") + "\n" + "".join(new_lines))
            os.replace(tmp, filepath)
        return filepath

    content = f"""---
name: signals-{TODAY}
description: "Auto-extracted signals from session {TODAY}"
metadata:
  type: project
  evidence: observed
  source: agent-extracted
  last_verified: {TODAY}
---

# Session Signals — {TODAY}

"""
    for signal in signals:
        content += f"- {signal.strip()}\n"

    import tempfile
    fd, tmp = tempfile.mkstemp(dir=signals_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, filepath)

    return filepath


def main():
    cwd = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    raw = sys.stdin.read().strip()
    if not raw or raw.upper() == "EMPTY":
        return

    signals = [line.strip() for line in raw.split("\n") if line.strip()]
    if not signals:
        return

    conn = None
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

    compounded = 0
    new_signals = []

    for signal in signals:
        keywords = extract_keywords(signal)
        matched = False

        if conn and keywords:
            results = search_fts5(conn, keywords, limit=3)
            for path, name, heading, content, rank in results:
                if abs(rank) > 5.0 and "/memory/" in path and "SKILL.md" not in path:
                    file_type = _get_file_type(path)
                    if file_type in ("feedback", "user"):
                        continue
                    if update_existing(path, signal):
                        compounded += 1
                        matched = True
                        break

        if not matched:
            new_signals.append(signal)

    if new_signals:
        filepath = create_signal_file(cwd, new_signals)

    if conn:
        conn.close()

    total = compounded + len(new_signals)
    if total > 0:
        print(f"Signals: {compounded} compounded, {len(new_signals)} new", file=sys.stderr)


if __name__ == "__main__":
    main()
