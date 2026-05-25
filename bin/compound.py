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

def default_memory_system():
    installed_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if os.path.exists(os.path.join(installed_root, ".installed.json")):
        return installed_root
    return os.path.expanduser("~/.claude/memory-system")


MEMORY_SYSTEM = os.path.expanduser(
    os.environ.get("EIDETIC_MEMORY_SYSTEM") or default_memory_system()
)
DB_PATH = os.path.join(MEMORY_SYSTEM, "db", "index.db")
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


def is_compound_candidate(path):
    """A returned exact FTS match is enough; FTS5 rank magnitudes are corpus-scale dependent."""
    return bool(path and "/memory/" in path and "SKILL.md" not in path)


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


def _markdown_headings(content):
    in_fence = False
    offset = 0
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            offset += len(line)
            continue
        if not in_fence:
            m = re.match(r'^(##)\s+(.+?)\s*$', line.rstrip("\r\n"))
            if m:
                yield offset, offset + len(line), m.group(2).strip()
        offset += len(line)


def _history_section_bounds(content):
    headings = list(_markdown_headings(content))
    for idx, (_start, end, title) in enumerate(headings):
        if title == "History":
            next_start = headings[idx + 1][0] if idx + 1 < len(headings) else None
            return end, next_start
    return None


def update_existing(filepath, signal_text):
    """Update existing memory file: append to History section. Does NOT update last_verified."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False

    history_entry = f"- {TODAY}: {signal_text.strip()}\n"

    history_bounds = _history_section_bounds(content)
    if history_bounds:
        _history_end, next_heading_start = history_bounds
        if next_heading_start is not None:
            before = content[:next_heading_start].rstrip()
            after = content[next_heading_start:].lstrip("\n")
            content = before + "\n" + history_entry + "\n" + after
        else:
            content = content.rstrip() + "\n" + history_entry
    else:
        content = content.rstrip() + f"\n\n## History\n\n{history_entry}"

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
    sanitized = cwd.rstrip("/").replace("/", "-").lstrip("-")

    memory_dir = None
    projects_dir = os.path.expanduser("~/.claude/projects/")
    if os.path.isdir(projects_dir):
        for d in os.listdir(projects_dir):
            if d == sanitized or d == "-" + sanitized:
                candidate = os.path.join(projects_dir, d, "memory")
                if os.path.isdir(candidate):
                    memory_dir = candidate
                    break
        if not memory_dir:
            for d in os.listdir(projects_dir):
                if sanitized.endswith(d.lstrip("-")) and len(d) > 10:
                    candidate = os.path.join(projects_dir, d, "memory")
                    if os.path.isdir(candidate):
                        memory_dir = candidate
                        break

    if not memory_dir:
        memory_dir = MEMORY_SYSTEM
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
                if is_compound_candidate(path):
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
