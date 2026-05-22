#!/usr/bin/env python3
"""AI Memory System v1.3 — Context Assembly with Smart Compression.

Assembles memory-context.md for Claude auto-load:
1. ALL feedback memories (P3: never invisible) with tiered display + clustering
2. Project-relevant memories (by CWD match)
3. Recent cross-project memories (last 14 days)

v1.3: Tiered display, keyword clustering, adaptive budget.
Token budget: ~6000 tokens (~24K chars).
"""

import json
import os
import re
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

RULE_CLUSTERS = [
    {
        "id": "consilium-review",
        "label": "Consilium/Review Pipeline",
        "patterns": [r"consilium", r"consreview", r"synthesis[\s._-]audit", r"synth[\s._-]hallucin",
                     r"voice[\s._-]fail", r"voices[\s._-]fail", r"voices[\s._-]degrad", r"pipeline[\s._-]timings",
                     r"pipeline[\s._-]premature", r"redteam", r"red[\s._-]team", r"audit[\s._-]verdict",
                     r"synthesis[\s._-]invents", r"review[\s._-]agent[\s._-]rules"],
        "summary": ("Re-synth mandatory when AUDIT says ISSUES. Red-team mandatory for "
                     "design decisions. 4-tier post-processing (BLOCKER/IMPORTANT/VERIFY/NOISE). "
                     "Wait for .pipeline_complete sentinel. Synth invents convergences — "
                     "verify raw voices. Full concept+data+flagship models required."),
    },
    {
        "id": "model-routing",
        "label": "Model & CLI Routing",
        "patterns": [r"model[\s._-]routing", r"model[\s._-]split", r"model[\s._-]benchmark", r"model[\s._-]freshness",
                     r"model[\s._-]selection", r"codex[\s._-]cli", r"codex[\s._-]model", r"gemini[\s._-]cli",
                     r"grok.+subscription", r"grok.+not\s+api"],
        "summary": ("Opus 4.6[1m] orchestrator, 4.7 sub-agent only. Anthropic via subscription "
                     "(never OpenRouter). Gemini Acc2 only (lari0305). Grok subscription only. "
                     "Codex 4-layer customization. Sonnet via claude-batch."),
    },
]


DRIFT_PENALTIES = {
    "broken_wikilink": 0.8,
    "age_stale": 0.5,
    "confidence_escalation": 0.3,
}


def load_drift_findings(db_path):
    drift_path = db_path.replace("index.db", "drift_state.db")
    if not os.path.exists(drift_path):
        return {}
    try:
        conn = sqlite3.connect(drift_path)
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute("""
            SELECT path, drift_type FROM drift_findings
            WHERE resolved_at IS NULL AND first_seen > 1
        """).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return {}
    findings = {}
    for path, drift_type in rows:
        penalty = DRIFT_PENALTIES.get(drift_type, 0.5)
        if path not in findings or penalty < findings[path]:
            findings[path] = penalty
    return findings


def compound_weight(evidence, source, last_verified, drift_penalty=None):
    ev = EVIDENCE_WEIGHTS.get(evidence, 0.7)
    src = SOURCE_WEIGHTS.get(source, 1.0)
    if drift_penalty is not None:
        fr = drift_penalty
    else:
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


def _escape_like(s):
    """Escape SQL LIKE wildcards in a string."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _match_cluster(name, desc):
    """Match a feedback rule name+desc against cluster patterns. Returns cluster id or None."""
    text = f"{name} {desc}".lower()
    for cluster in RULE_CLUSTERS:
        for pattern in cluster["patterns"]:
            if re.search(pattern, text, re.IGNORECASE):
                return cluster["id"]
    return None


def _format_cluster(cluster_def, member_names):
    """Format a cluster as a compact block with summary + member list."""
    lines = [f"- **[{cluster_def['label']}]** ({len(member_names)} rules): "
             f"{cluster_def['summary']}\n"]
    names_line = "  _Rules: " + ", ".join(sorted(member_names)[:8])
    if len(member_names) > 8:
        names_line += f", +{len(member_names) - 8} more"
    names_line += "_\n"
    lines.append(names_line)
    return "".join(lines)


def fetch_feedback(conn, budget_chars):
    """Fetch ALL feedback memories with v1.3 smart compression.

    P3: never invisible. Three compression strategies:
    1. Clustering: group related rules (consilium, model-routing) into compact blocks
    2. Tiered display: top=full desc, mid=name+50chars, low=name-only
    3. Overflow: name-only for anything that doesn't fit
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

    clustered = {}  # cluster_id -> list of (weight, name, desc)
    individual = []  # (weight, name, desc)

    for row in rows:
        path, name, desc, content, evidence, source, lv, _ = row
        w = compound_weight(evidence, source, lv)
        display_name = name or os.path.basename(path).replace(".md", "")
        display_desc = desc or (content[:150].replace("\n", " ").strip() if content else "")

        cid = _match_cluster(display_name, display_desc)
        if cid:
            clustered.setdefault(cid, []).append((w, display_name, display_desc))
        else:
            individual.append((w, display_name, display_desc))

    result = []
    used = 0

    clustered_displayed = 0
    for cluster_def in RULE_CLUSTERS:
        members = clustered.get(cluster_def["id"])
        if not members or len(members) < 3:
            individual.extend(members or [])
            continue
        block = _format_cluster(cluster_def, [name for _, name, _ in members])
        if used + len(block) <= budget_chars:
            result.append(block)
            used += len(block)
            clustered_displayed += len(members)
        else:
            individual.extend(members)

    individual.sort(key=lambda x: x[0], reverse=True)
    n = len(individual)
    top_cutoff = max(1, int(n * 0.4)) if n > 0 else 0
    mid_cutoff = max(top_cutoff + 1, int(n * 0.7)) if n > 1 else 0

    for i, (w, name, desc) in enumerate(individual):
        if i < top_cutoff:
            capped = desc[:500] + ("..." if len(desc) > 500 else "")
            text = f"- **{name}**: {capped}\n"
        elif i < mid_cutoff:
            short = desc[:60].rstrip() + "..." if len(desc) > 60 else desc
            text = f"- **{name}**: {short}\n" if short else f"- **{name}**\n"
        else:
            text = f"- {name}\n"

        if used + len(text) > budget_chars:
            remaining = n - i
            result.append(f"_...and {remaining} more feedback rules "
                          f"(use /memory-recall to search)_\n")
            break
        result.append(text)
        used += len(text)

    total_rules = clustered_displayed + n
    return "".join(result), used, total_rules


def fetch_project(conn, cwd, budget_chars, drift_map=None):
    """Fetch project-relevant memories by matching project slug."""
    slug = detect_project_slug(cwd)
    drift_map = drift_map or {}

    rows = conn.execute("""
        SELECT DISTINCT c.path, c.name, c.description, c.section_heading, c.content,
               c.evidence, c.source, c.last_verified, c.type,
               memory_fts.rank AS fts_rank
        FROM memory_chunks c
        LEFT JOIN memory_fts ON memory_fts.rowid = c.id
        WHERE c.project LIKE ? ESCAPE '\\' AND c.type != 'feedback'
        ORDER BY c.mtime DESC
        LIMIT 50
    """, (f"%{_escape_like(slug[-60:])}%",)).fetchall()

    chunks = []
    seen = set()
    for row in rows:
        path, name, desc, heading, content, evidence, source, lv, typ, rank = row
        key = (path, heading)
        if key in seen:
            continue
        seen.add(key)

        dp = drift_map.get(path)
        w = compound_weight(evidence, source, lv, drift_penalty=dp)
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


def fetch_recent(conn, budget_chars, exclude_project=None, drift_map=None):
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
        query += " AND (c.project IS NULL OR c.project NOT LIKE ? ESCAPE '\\')"
        params.append(f"%{_escape_like(exclude_project[-60:])}%")

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

        dp = (drift_map or {}).get(path)
        w = compound_weight(evidence, source, lv, drift_penalty=dp)
        stale_tag = " ⚠stale" if dp else ""
        short_path = path.replace(os.path.expanduser("~"), "~")
        snippet = content[:300] if len(content) > 300 else content
        text = f"**{name or heading}**{stale_tag} ({typ}, {proj or 'cross-project'}) — {short_path}\n{snippet}\n\n"
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
    drift_map = load_drift_findings(db_path)

    project_budget = int(TOKEN_BUDGET_CHARS * PROJECT_BUDGET_RATIO)
    recent_budget = int(TOKEN_BUDGET_CHARS * RECENT_BUDGET_RATIO)

    project_text, project_used = fetch_project(conn, cwd, project_budget, drift_map)
    recent_text, recent_used = fetch_recent(conn, recent_budget, slug, drift_map)

    feedback_budget = TOKEN_BUDGET_CHARS - project_used - recent_used
    feedback_text, feedback_used, feedback_total = fetch_feedback(conn, feedback_budget)

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
          f"{feedback_used // 4}t feedback ({feedback_total} rules) + "
          f"{project_used // 4}t project + {recent_used // 4}t recent")


if __name__ == "__main__":
    main()
