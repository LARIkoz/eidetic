#!/usr/bin/env python3
"""AI Memory System v1 — Context Assembly.

Assembles memory-context.md for Claude auto-load:
1. ALL feedback memories (P3: never invisible)
2. Project-relevant memories (by CWD match)
3. Recent cross-project memories (last 14 days)

Token budget: ~6000 tokens (~24K chars).
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

TOKEN_BUDGET_CHARS = 24000
FEEDBACK_BUDGET_RATIO = 0.5
PROJECT_BUDGET_RATIO = 0.3
RECENT_BUDGET_RATIO = 0.2

EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}
FRESHNESS_DAYS = 30


def compound_weight(evidence, source, last_verified):
    ev = EVIDENCE_WEIGHTS.get(evidence, 0.7)
    src = SOURCE_WEIGHTS.get(source, 1.0)
    fr = 0.7
    if last_verified:
        try:
            lv = datetime.fromisoformat(last_verified)
            fr = 1.0 if (datetime.now() - lv).days < FRESHNESS_DAYS else 0.5
        except (ValueError, TypeError):
            pass
    return ev * src * fr


def detect_project_slug(cwd):
    """Extract project slug from CWD to match against indexed project field."""
    cwd = cwd.rstrip("/")
    sanitized = cwd.replace("/", "-").lstrip("-")
    return sanitized


def fetch_feedback(conn, budget_chars):
    """Fetch ALL feedback memories. P3: never invisible.

    Strategy: one entry per FILE (not per chunk). Shows name + description +
    first 300 chars of body. This guarantees all 120+ feedback rules fit
    within budget. For full content, agent uses /memory-recall skill.
    """
    rows = conn.execute("""
        SELECT c.path, c.name, c.description, c.content,
               c.evidence, c.source, c.last_verified,
               MIN(c.id) as first_id
        FROM memory_chunks c
        WHERE c.type = 'feedback'
        GROUP BY c.path
        ORDER BY c.name
    """).fetchall()

    entries = []
    for row in rows:
        path, name, desc, content, evidence, source, lv, _ = row
        w = compound_weight(evidence, source, lv)
        short_path = path.replace(os.path.expanduser("~"), "~")
        display_name = name or os.path.basename(path).replace(".md", "")

        if desc:
            text = f"- **{display_name}**: {desc}\n"
        else:
            snippet = content[:150].replace("\n", " ").strip()
            text = f"- **{display_name}**: {snippet}...\n"
        entries.append((w, text, short_path))

    entries.sort(key=lambda x: x[0], reverse=True)

    result = []
    used = 0
    for w, text, _ in entries:
        if used + len(text) > budget_chars and used > 0:
            result.append(f"_...and {len(entries) - len(result)} more feedback rules "
                          f"(use /memory-recall to search)_\n")
            break
        result.append(text)
        used += len(text)

    return "".join(result), used


def fetch_project(conn, cwd, budget_chars):
    """Fetch project-relevant memories by matching project slug."""
    slug = detect_project_slug(cwd)

    rows = conn.execute("""
        SELECT DISTINCT c.path, c.name, c.description, c.section_heading, c.content,
               c.evidence, c.source, c.last_verified, c.type,
               memory_fts.rank AS fts_rank
        FROM memory_chunks c
        LEFT JOIN memory_fts ON memory_fts.rowid = c.id
        WHERE c.project LIKE ? AND c.type != 'feedback'
        ORDER BY c.mtime DESC
        LIMIT 50
    """, (f"%{slug[-60:]}%",)).fetchall()

    chunks = []
    seen = set()
    for row in rows:
        path, name, desc, heading, content, evidence, source, lv, typ, rank = row
        key = (path, heading)
        if key in seen:
            continue
        seen.add(key)

        w = compound_weight(evidence, source, lv)
        short_path = path.replace(os.path.expanduser("~"), "~")
        snippet = content[:500] if len(content) > 500 else content
        text = f"**{name or heading}** ({typ}) — {short_path}\n{snippet}\n\n"
        chunks.append((w, text))

    chunks.sort(key=lambda x: x[0], reverse=True)

    result = []
    used = 0
    for w, text in chunks:
        if used + len(text) > budget_chars and used > 0:
            break
        result.append(text)
        used += len(text)

    return "".join(result), used


def fetch_recent(conn, budget_chars, exclude_project=None):
    """Fetch recent cross-project memories (last 14 days)."""
    cutoff = int((datetime.now() - timedelta(days=14)).timestamp())

    query = """
        SELECT DISTINCT c.path, c.name, c.description, c.section_heading, c.content,
               c.evidence, c.source, c.last_verified, c.type, c.project
        FROM memory_chunks c
        WHERE c.mtime > ? AND c.type != 'feedback'
    """
    params = [cutoff]

    if exclude_project:
        query += " AND (c.project IS NULL OR c.project NOT LIKE ?)"
        params.append(f"%{exclude_project[-60:]}%")

    query += " ORDER BY c.mtime DESC LIMIT 30"

    rows = conn.execute(query, params).fetchall()

    chunks = []
    seen = set()
    for row in rows:
        path, name, desc, heading, content, evidence, source, lv, typ, proj = row
        key = (path, heading)
        if key in seen:
            continue
        seen.add(key)

        w = compound_weight(evidence, source, lv)
        short_path = path.replace(os.path.expanduser("~"), "~")
        snippet = content[:300] if len(content) > 300 else content
        text = f"**{name or heading}** ({typ}, {proj or 'cross-project'}) — {short_path}\n{snippet}\n\n"
        chunks.append((w, text))

    chunks.sort(key=lambda x: x[0], reverse=True)

    result = []
    used = 0
    for w, text in chunks:
        if used + len(text) > budget_chars and used > 0:
            break
        result.append(text)
        used += len(text)

    return "".join(result), used


def main():
    db_path = sys.argv[1]
    rules_file = sys.argv[2]
    cwd = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    slug = detect_project_slug(cwd)

    feedback_budget = int(TOKEN_BUDGET_CHARS * FEEDBACK_BUDGET_RATIO)
    project_budget = int(TOKEN_BUDGET_CHARS * PROJECT_BUDGET_RATIO)
    recent_budget = int(TOKEN_BUDGET_CHARS * RECENT_BUDGET_RATIO)

    feedback_text, feedback_used = fetch_feedback(conn, feedback_budget)
    project_text, project_used = fetch_project(conn, cwd, project_budget)

    leftover = TOKEN_BUDGET_CHARS - feedback_used - project_used
    recent_budget = max(recent_budget, leftover)
    recent_text, recent_used = fetch_recent(conn, recent_budget, slug)

    total_chars = feedback_used + project_used + recent_used
    total_tokens = total_chars // 4

    stats = conn.execute("SELECT COUNT(DISTINCT path), COUNT(*) FROM memory_chunks").fetchone()

    output = []
    output.append("# Memory Context (auto-generated)\n")
    output.append(f"_Assembled: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                  f"{stats[0]} files, {stats[1]} chunks indexed | "
                  f"~{total_tokens} tokens_\n\n")

    if feedback_text:
        output.append("## Behavioral Rules (type=feedback) — ALWAYS APPLY\n\n")
        output.append(feedback_text)

    if project_text:
        output.append("## Project Context\n\n")
        output.append(project_text)

    if recent_text:
        output.append("## Recent Cross-Project\n\n")
        output.append(recent_text)

    import tempfile
    os.makedirs(os.path.dirname(rules_file), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(rules_file), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(output))
        os.replace(tmp_path, rules_file)
    except Exception:
        os.unlink(tmp_path)
        raise

    conn.close()

    print(f"Memory context updated: {total_tokens} tokens, "
          f"{feedback_used // 4}t feedback + {project_used // 4}t project + "
          f"{recent_used // 4}t recent")


if __name__ == "__main__":
    main()
