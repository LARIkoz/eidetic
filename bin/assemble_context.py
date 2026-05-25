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
STATUS_WEIGHTS = {
    "current": 1.0,
    "active": 1.0,
    "validated": 1.0,
    "resolved": 0.75,
    "fixed": 0.75,
    "superseded": 0.35,
    "deprecated": 0.35,
    "obsolete": 0.35,
    "archived": 0.25,
}
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


try:
    from constants import DRIFT_PENALTIES
except ImportError:
    DRIFT_PENALTIES = {"broken_wikilink": 0.8, "age_stale": 0.5, "confidence_escalation": 0.3}


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


def ensure_agent_columns(conn):
    """Add v2.6 derived columns when context assembly sees an older DB."""
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    except sqlite3.OperationalError:
        return
    migrations = {
        "card_kind": "ALTER TABLE memory_chunks ADD COLUMN card_kind TEXT DEFAULT ''",
        "status": "ALTER TABLE memory_chunks ADD COLUMN status TEXT DEFAULT 'current'",
        "area": "ALTER TABLE memory_chunks ADD COLUMN area TEXT DEFAULT ''",
        "supersedes": "ALTER TABLE memory_chunks ADD COLUMN supersedes TEXT DEFAULT ''",
        "superseded_by": "ALTER TABLE memory_chunks ADD COLUMN superseded_by TEXT DEFAULT ''",
    }
    for column, statement in migrations.items():
        if column not in existing:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.commit()


def status_weight(status, superseded_by=""):
    normalized = (status or "current").strip().lower()
    if superseded_by:
        return min(STATUS_WEIGHTS.get(normalized, 1.0), STATUS_WEIGHTS["superseded"])
    return STATUS_WEIGHTS.get(normalized, 1.0)


def compound_weight(evidence, source, last_verified, drift_penalty=None, status="current", superseded_by=""):
    ev = EVIDENCE_WEIGHTS.get(evidence, 0.7)
    src = SOURCE_WEIGHTS.get(source, 1.0)
    st = status_weight(status, superseded_by)
    if drift_penalty is not None:
        fr = drift_penalty
    else:
        fr = 0.7
        if last_verified:
            try:
                lv = datetime.fromisoformat(str(last_verified).replace("Z", "+00:00"))
                now = datetime.now(lv.tzinfo) if lv.tzinfo else datetime.now()
                fr = 1.0 if (now - lv).days < FRESHNESS_DAYS else 0.5
            except (ValueError, TypeError):
                pass
    return ev * src * fr * st


def fetch_drift_diagnostics(db_path, limit=8):
    """Return a bounded diagnostics block for active drift findings."""
    drift_path = db_path.replace("index.db", "drift_state.db")
    if not os.path.exists(drift_path):
        return "", 0
    try:
        conn = sqlite3.connect(drift_path)
        conn.execute("PRAGMA busy_timeout=2000")
        counts = conn.execute("""
            SELECT drift_type, COUNT(*), SUM(CASE WHEN first_seen > 1 THEN 1 ELSE 0 END)
            FROM drift_findings
            WHERE resolved_at IS NULL
            GROUP BY drift_type
            ORDER BY COUNT(*) DESC
        """).fetchall()
        rows = conn.execute("""
            SELECT path, drift_type, detail, first_seen, detected_at
            FROM drift_findings
            WHERE resolved_at IS NULL
            ORDER BY first_seen DESC, detected_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return "", 0

    if not counts:
        return "", 0

    parts = ["## Memory Drift Diagnostics\n\n"]
    summary = ", ".join(
        f"{kind}={count} ({penalized or 0} penalized)"
        for kind, count, penalized in counts
    )
    parts.append(f"- Active findings: {summary}\n")
    parts.append("- Treat penalized/stale memories as candidates to verify, not as source-of-truth.\n")
    for path, drift_type, detail, first_seen, detected_at in rows:
        short_path = path.replace(os.path.expanduser("~"), "~")
        marker = "penalized" if int(first_seen or 0) > 1 else "baseline"
        parts.append(
            f"- {drift_type} [{marker}, seen={first_seen}] {short_path}: {detail or ''} ({detected_at or 'unknown'})\n"
        )
    parts.append("\n")
    text = "".join(parts)
    return text, len(text)


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
    names_line = "  _Rules: " + ", ".join(sorted(member_names)) + "_\n"
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
               c.evidence, c.source, c.last_verified, c.status, c.superseded_by,
               MIN(c.id) as first_id
        FROM memory_chunks c
        WHERE c.type = 'feedback'
        GROUP BY c.path
        ORDER BY c.name
    """).fetchall()

    clustered = {}  # cluster_id -> list of (weight, name, desc)
    individual = []  # (weight, name, desc)

    for row in rows:
        path, name, desc, content, evidence, source, lv, status, superseded_by, _ = row
        w = compound_weight(evidence, source, lv, status=status, superseded_by=superseded_by)
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
            result.append("<!-- feedback budget exceeded; remaining rules kept name-only by invariant -->\n")
            for _, rest_name, _ in individual[i:]:
                result.append(f"- {rest_name}\n")
                used += len(rest_name) + 3
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
               c.evidence, c.source, c.last_verified, c.type, c.card_kind,
               c.status, c.superseded_by,
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
        path, name, desc, heading, content, evidence, source, lv, typ, card_kind, status, superseded_by, rank = row
        key = (path, heading)
        if key in seen:
            continue
        seen.add(key)

        dp = drift_map.get(path)
        w = compound_weight(evidence, source, lv, drift_penalty=dp, status=status, superseded_by=superseded_by)
        status_tag = "" if (status or "current") == "current" else f", status={status}"
        short_path = path.replace(os.path.expanduser("~"), "~")
        snippet = content[:500] if len(content) > 500 else content
        text = f"**{name or heading}** ({card_kind or typ}{status_tag}) — {short_path}\n{snippet}\n\n"
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
    mtime_seconds = "CASE WHEN c.mtime > 10000000000 THEN c.mtime / 1000000000 ELSE c.mtime END"

    query = """
        SELECT DISTINCT c.path, c.name, c.description, c.section_heading, c.content,
               c.evidence, c.source, c.last_verified, c.type, c.project,
               c.card_kind, c.status, c.superseded_by
        FROM memory_chunks c
        WHERE {mtime_seconds} > ? AND c.type != 'feedback'
    """.format(mtime_seconds=mtime_seconds)
    params = [cutoff]

    if exclude_project:
        query += " AND (c.project IS NULL OR c.project NOT LIKE ? ESCAPE '\\')"
        params.append(f"%{_escape_like(exclude_project[-60:])}%")

    query += f" ORDER BY {mtime_seconds} DESC LIMIT 30"

    rows = conn.execute(query, params).fetchall()

    chunks = []
    seen = set()
    for row in rows:
        path, name, desc, heading, content, evidence, source, lv, typ, proj, card_kind, status, superseded_by = row
        key = (path, heading)
        if key in seen:
            continue
        seen.add(key)

        dp = (drift_map or {}).get(path)
        w = compound_weight(evidence, source, lv, drift_penalty=dp, status=status, superseded_by=superseded_by)
        stale_tag = " [drift]" if dp else ""
        status_tag = "" if (status or "current") == "current" else f", status={status}"
        short_path = path.replace(os.path.expanduser("~"), "~")
        snippet = content[:300] if len(content) > 300 else content
        text = f"**{name or heading}**{stale_tag} ({card_kind or typ}{status_tag}, {proj or 'cross-project'}) — {short_path}\n{snippet}\n\n"
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


HANDOFF_MAX_AGE_MINUTES = 30
HANDOFF_MAX_CHARS = 3000


def fetch_fresh_handoff(cwd, slug):
    """Find the most recent handoff state.md for this project (< 30 min old)."""
    import glob as _glob

    candidates = []

    memory_pattern = os.path.expanduser(f"~/.claude/projects/*{slug[-40:]}*/memory/**/state.md")
    for f in _glob.glob(memory_pattern, recursive=True):
        candidates.append(f)

    output_handoff_pattern = os.path.join(cwd, "output", "handoff-*", "state.md")
    candidates.extend(_glob.glob(output_handoff_pattern))

    for handoff_dir in [os.path.join(cwd, "handoff"), os.path.join(cwd, ".kurdyuk-lite/runs")]:
        if os.path.isdir(handoff_dir):
            for root, dirs, files in os.walk(handoff_dir):
                if "state.md" in files:
                    candidates.append(os.path.join(root, "state.md"))

    if not candidates:
        return "", 0

    now = time.time()
    best = None
    best_mtime = 0
    for path in candidates:
        try:
            mtime = os.path.getmtime(path)
            age_min = (now - mtime) / 60
            if age_min < HANDOFF_MAX_AGE_MINUTES and mtime > best_mtime:
                best = path
                best_mtime = mtime
        except OSError:
            continue

    if not best:
        return "", 0

    try:
        with open(best, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(HANDOFF_MAX_CHARS)
    except OSError:
        return "", 0

    age_min = int((now - best_mtime) / 60)
    short_path = best.replace(os.path.expanduser("~"), "~")
    text = f"**Handoff** ({age_min}m ago) — {short_path}\n{content}\n\n"
    return text, len(text)


def main():
    db_path = sys.argv[1]
    rules_file = sys.argv[2]
    cwd = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_agent_columns(conn)

    slug = detect_project_slug(cwd)
    drift_map = load_drift_findings(db_path)
    drift_text, drift_used = fetch_drift_diagnostics(db_path)

    handoff_text, handoff_used = fetch_fresh_handoff(cwd, slug)

    project_budget = int(TOKEN_BUDGET_CHARS * PROJECT_BUDGET_RATIO)
    recent_budget = int(TOKEN_BUDGET_CHARS * RECENT_BUDGET_RATIO)

    if handoff_used > 0:
        project_budget = max(1000, project_budget - handoff_used // 2)

    project_text, project_used = fetch_project(conn, cwd, project_budget, drift_map)
    recent_text, recent_used = fetch_recent(conn, recent_budget, slug, drift_map)

    feedback_budget = max(1000, TOKEN_BUDGET_CHARS - project_used - recent_used - handoff_used - drift_used)
    feedback_text, feedback_used, feedback_total = fetch_feedback(conn, feedback_budget)

    total_chars = feedback_used + project_used + recent_used + handoff_used + drift_used
    total_tokens = total_chars // 4

    stats = conn.execute("SELECT COUNT(DISTINCT path), COUNT(*) FROM memory_chunks").fetchone()

    output = []
    output.append("# Memory Context (auto-generated)\n")
    output.append(f"_Assembled: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                  f"{stats[0]} files, {stats[1]} chunks indexed | "
                  f"~{total_tokens} tokens_\n\n")

    if drift_text:
        output.append(drift_text)

    if feedback_text:
        output.append("## Behavioral Rules (type=feedback) — ALWAYS APPLY\n\n")
        output.append(feedback_text)

    if handoff_text:
        output.append("## Recent Handoff (cold-start priority)\n\n")
        output.append(handoff_text)

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
