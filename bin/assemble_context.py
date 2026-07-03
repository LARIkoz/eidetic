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

# Rule clustering compresses a group of same-topic feedback cards into ONE block in
# the injected context. This ships EMPTY on purpose. LESSON (privacy): never hardcode
# the maintainer's — or any user's — personal operating rules, account names, or
# model-routing here. assemble_context writes ~/.claude/rules/memory-context.md, so any
# string baked into a cluster "summary" is injected verbatim into EVERY install's
# context as an "ALWAYS APPLY" rule the moment >=3 of that user's feedback cards match
# the patterns. Personal clusters belong in a LOCAL, git-ignored config loaded at
# runtime, never in this public source file.
def _load_rule_clusters():
    """Optional per-user rule clusters; ships EMPTY (no personal content in-repo).

    Loaded from $EIDETIC_RULE_CLUSTERS, else <memory-system>/rule_clusters.json (a
    local, git-ignored file). Each entry needs id, label, patterns (list of regex), and
    summary. Absent or malformed -> [] so every feedback card is listed individually via
    the tiered path in fetch_feedback (nothing is ever hidden — P3 never-invisible).
    """
    path = os.environ.get("EIDETIC_RULE_CLUSTERS") or os.path.join(
        os.environ.get("EIDETIC_MEMORY_SYSTEM") or os.path.expanduser("~/.claude/memory-system"),
        "rule_clusters.json")
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data
            if isinstance(c, dict) and c.get("id") and c.get("label")
            and isinstance(c.get("patterns"), list) and c.get("summary")]


RULE_CLUSTERS = _load_rule_clusters()


# Single source of truth: bin/constants.py. Literal fallback only for when the
# module is run somewhere constants.py is not importable (W3 dedup).
try:
    from constants import EVIDENCE_WEIGHTS, SOURCE_WEIGHTS, DRIFT_PENALTIES, DECLARED_DRIFT_TYPES
except ImportError:
    EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
    SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3}
    DRIFT_PENALTIES = {"broken_wikilink": 0.8, "age_stale": 0.5, "confidence_escalation": 0.3,
                       "contradicted": 0.4, "unresolved_relation": 1.0, "relation_claim": 1.0}
    DECLARED_DRIFT_TYPES = {"contradicted"}

DRIFT_PENALTY_FLOOR = 0.1


def _drift_finding_penalized(drift_type, first_seen):
    """One predicate for penalize + diagnostics, so the report can never say
    "0 penalized" while a penalty is applied. Heuristic findings penalize from
    the 2nd detection (grace gate); DECLARED relations immediately; penalty-1.0
    diagnostic types never (they change nothing by construction)."""
    if DRIFT_PENALTIES.get(drift_type, 0.5) >= 1.0:
        return False
    return int(first_seen or 0) > 1 or drift_type in DECLARED_DRIFT_TYPES


def load_drift_findings(db_path):
    drift_path = db_path.replace("index.db", "drift_state.db")
    if not os.path.exists(drift_path):
        return {}
    try:
        conn = sqlite3.connect(drift_path)
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute("""
            SELECT path, drift_type, first_seen FROM drift_findings
            WHERE resolved_at IS NULL
        """).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return {}
    # Distinct penalized types per card COMPOUND (multiply, floored) — same
    # rule as search_impl._load_drift_data, so injection and search agree.
    types_by_path = {}
    for path, drift_type, first_seen in rows:
        if _drift_finding_penalized(drift_type, first_seen):
            types_by_path.setdefault(path, set()).add(drift_type)
    findings = {}
    for path, types in types_by_path.items():
        penalty = 1.0
        for drift_type in types:
            penalty *= DRIFT_PENALTIES.get(drift_type, 0.5)
        findings[path] = max(DRIFT_PENALTY_FLOOR, penalty)
    return findings


def ensure_agent_columns(conn):
    """Add v2.6 derived columns when context assembly sees an older DB."""
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    except sqlite3.OperationalError:
        return
    migrations = {
        "project": "ALTER TABLE memory_chunks ADD COLUMN project TEXT DEFAULT ''",
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
    fr = 0.7
    if last_verified:
        try:
            lv = datetime.fromisoformat(str(last_verified).replace("Z", "+00:00"))
            now = datetime.now(lv.tzinfo) if lv.tzinfo else datetime.now()
            fr = 1.0 if (now - lv).days < FRESHNESS_DAYS else 0.5
        except (ValueError, TypeError):
            pass
    if drift_penalty is not None:
        # Multiply, never replace — replacing let a mild penalty (0.8) overwrite
        # stale freshness (0.5) and up-rank rot. See search_impl.combine_freshness.
        fr *= drift_penalty
    return ev * src * fr * st


def fetch_drift_diagnostics(db_path, limit=8):
    """Return a bounded diagnostics block for active drift findings."""
    drift_path = db_path.replace("index.db", "drift_state.db")
    if not os.path.exists(drift_path):
        return "", 0
    try:
        conn = sqlite3.connect(drift_path)
        conn.execute("PRAGMA busy_timeout=2000")
        count_rows = conn.execute("""
            SELECT drift_type, first_seen
            FROM drift_findings
            WHERE resolved_at IS NULL
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

    if not count_rows:
        return "", 0

    # Penalized = the same predicate ranking uses (declared types penalize at
    # first_seen=1) — counting `first_seen > 1` alone reported "0 penalized"
    # while a 0.4x declared penalty was being applied.
    agg = {}
    for drift_type, first_seen in count_rows:
        total, penalized = agg.get(drift_type, (0, 0))
        agg[drift_type] = (
            total + 1,
            penalized + (1 if _drift_finding_penalized(drift_type, first_seen) else 0),
        )
    counts = sorted(agg.items(), key=lambda kv: kv[1][0], reverse=True)

    parts = ["## Memory Drift Diagnostics\n\n"]
    summary = ", ".join(
        f"{kind}={total} ({penalized} penalized)"
        for kind, (total, penalized) in counts
    )
    parts.append(f"- Active findings: {summary}\n")
    parts.append("- Treat penalized/stale memories as candidates to verify, not as source-of-truth.\n")
    for path, drift_type, detail, first_seen, detected_at in rows:
        short_path = path.replace(os.path.expanduser("~"), "~")
        marker = "penalized" if _drift_finding_penalized(drift_type, first_seen) else "baseline"
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


def fetch_feedback(conn, budget_chars, current_slug=""):
    """Fetch ALL feedback memories with v1.3 smart compression.

    P3: never invisible. Three compression strategies:
    1. Clustering: group related rules (consilium, model-routing) into compact blocks
    2. Tiered display: top=full desc, mid=name+50chars, low=name-only
    3. Overflow: name-only for anything that doesn't fit

    v5.2: project-aware discount — rules from other projects get 0.3x weight,
    pushing them to lower display tiers (name-only or hidden).
    """
    ensure_agent_columns(conn)
    rows = conn.execute("""
        SELECT c.path, c.name, c.description, c.content,
               c.evidence, c.source, c.last_verified, c.status, c.superseded_by,
               c.project,
               MIN(c.id) as first_id
        FROM memory_chunks c
        WHERE c.type = 'feedback'
        GROUP BY c.path
        ORDER BY c.name
    """).fetchall()

    clustered = {}  # cluster_id -> list of (weight, name, desc)
    individual = []  # (weight, name, desc)

    for row in rows:
        path, name, desc, content, evidence, source, lv, status, superseded_by, project, _ = row
        w = compound_weight(evidence, source, lv, status=status, superseded_by=superseded_by)
        if current_slug and project and current_slug not in (project or ""):
            w *= 0.3
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
    # Phase 1: every feedback card is injected (P3 never-invisible), so all paths count.
    feedback_slugs = sorted({os.path.basename(r[0]).replace(".md", "") for r in rows})
    return "".join(result), used, total_rules, feedback_slugs


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
        ORDER BY c.mtime DESC, c.path, c.section_heading
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
        chunks.append((w, text, path))

    chunks.sort(key=lambda x: x[0], reverse=True)

    result = []
    used = 0
    included = []
    for w, text, path in chunks:
        if used + len(text) > budget_chars and used > 0:
            break
        result.append(text)
        used += len(text)
        included.append(os.path.basename(path).replace(".md", ""))

    return "".join(result), used, sorted(set(included))


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

    # Total order: mtime alone ties for batch-written cards, making the LIMIT-30
    # cutoff pick an arbitrary subset. (path, section_heading) is the unique key.
    query += f" ORDER BY {mtime_seconds} DESC, c.path, c.section_heading LIMIT 30"

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
        chunks.append((w, text, path))

    chunks.sort(key=lambda x: x[0], reverse=True)

    result = []
    used = 0
    included = []
    for w, text, path in chunks:
        if used + len(text) > budget_chars and used > 0:
            break
        result.append(text)
        used += len(text)
        included.append(os.path.basename(path).replace(".md", ""))

    return "".join(result), used, sorted(set(included))


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

    project_text, project_used, project_slugs = fetch_project(conn, cwd, project_budget, drift_map)
    recent_text, recent_used, recent_slugs = fetch_recent(conn, recent_budget, slug, drift_map)

    feedback_budget = max(1000, TOKEN_BUDGET_CHARS - project_used - recent_used - handoff_used - drift_used)
    feedback_text, feedback_used, feedback_total, feedback_slugs = fetch_feedback(conn, feedback_budget, slug)

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

    # --- Value-telemetry Phase 0: append-only per-session COST row. -------------
    # Records what the passive injection COST this session (tokens by section +
    # the separately-loaded MEMORY.md size). Benefit proxies (which cards helped)
    # are Phase 1. FAIL-OPEN — telemetry must never break injection. PRIVACY — no
    # raw text / headings / card paths, only counts + project basename.
    try:
        if os.environ.get("EIDETIC_VALUE_TELEMETRY", "on").strip().lower() != "off":
            import json as _json
            mem_md_bytes = 0
            try:
                sanitized = cwd.rstrip("/").replace("/", "-").lstrip("-")
                for cand in (sanitized, "-" + sanitized):
                    mpath = os.path.expanduser(f"~/.claude/projects/{cand}/memory/MEMORY.md")
                    if os.path.exists(mpath):
                        mem_md_bytes = os.path.getsize(mpath)
                        break
            except Exception:
                pass
            _all_slugs = sorted(set(feedback_slugs) | set(project_slugs) | set(recent_slugs))
            row = {
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": os.environ.get("EIDETIC_SESSION_ID", ""),
                "project": os.path.basename(cwd.rstrip("/")) or "unknown",
                "total_tokens": total_tokens,
                "feedback_tokens": feedback_used // 4,
                "project_tokens": project_used // 4,
                "recent_tokens": recent_used // 4,
                "handoff_tokens": handoff_used // 4,
                "drift_tokens": drift_used // 4,
                "n_rules": feedback_total,
                "n_cards": len(_all_slugs),
                "slugs": _all_slugs,
                "memory_md_bytes": mem_md_bytes,
            }
            inj_log = os.path.join(os.path.dirname(os.path.abspath(db_path)), "inject_log.jsonl")
            _fd = os.open(inj_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(_fd, (_json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                os.close(_fd)
    except Exception:
        pass

    conn.close()

    print(f"Memory context updated: {total_tokens} tokens, "
          f"{feedback_used // 4}t feedback ({feedback_total} rules) + "
          f"{project_used // 4}t project + {recent_used // 4}t recent")


if __name__ == "__main__":
    main()
