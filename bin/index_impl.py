#!/usr/bin/env python3
"""AI Memory System v1 — FTS5 Indexer.

Scans memory directories, parses YAML frontmatter (both root and nested metadata formats),
splits by ## headings, and upserts into SQLite FTS5.

Zero external deps: python3 stdlib + sqlite3.
"""

import glob
import json
import os
import re
import sqlite3
import sys
import time

# Single source of truth: bin/constants.py. Literal fallback only for when the
# module is run somewhere constants.py is not importable (W3 dedup).
try:
    from constants import (
        EVIDENCE_WEIGHTS, SOURCE_WEIGHTS,
        MEMORY_CHUNK_MIGRATIONS, RELATION_EXPLICIT_COLUMNS, FORCED_REREAD_ON_ADD,
        MAX_CHUNK_CHARS,
    )
except ImportError:
    EVIDENCE_WEIGHTS = {"validated": 1.0, "observed": 0.7, "hypothesis": 0.4}
    SOURCE_WEIGHTS = {"user-explicit": 1.0, "agent-extracted": 0.5, "system-generated": 0.3, "imported": 0.3}
    MEMORY_CHUNK_MIGRATIONS = {
        "project": "ALTER TABLE memory_chunks ADD COLUMN project TEXT DEFAULT ''",
        "card_kind": "ALTER TABLE memory_chunks ADD COLUMN card_kind TEXT DEFAULT ''",
        "status": "ALTER TABLE memory_chunks ADD COLUMN status TEXT DEFAULT 'current'",
        "area": "ALTER TABLE memory_chunks ADD COLUMN area TEXT DEFAULT ''",
        "supersedes": "ALTER TABLE memory_chunks ADD COLUMN supersedes TEXT DEFAULT ''",
        "superseded_by": "ALTER TABLE memory_chunks ADD COLUMN superseded_by TEXT DEFAULT ''",
        "contradicts": "ALTER TABLE memory_chunks ADD COLUMN contradicts TEXT DEFAULT ''",
        "contradicted_by": "ALTER TABLE memory_chunks ADD COLUMN contradicted_by TEXT DEFAULT ''",
        "superseded_by_explicit": "ALTER TABLE memory_chunks ADD COLUMN superseded_by_explicit TEXT DEFAULT ''",
        "contradicted_by_explicit": "ALTER TABLE memory_chunks ADD COLUMN contradicted_by_explicit TEXT DEFAULT ''",
        "status_explicit": "ALTER TABLE memory_chunks ADD COLUMN status_explicit TEXT DEFAULT ''",
        "lifecycle": "ALTER TABLE memory_chunks ADD COLUMN lifecycle TEXT DEFAULT ''",
    }
    RELATION_EXPLICIT_COLUMNS = {"superseded_by_explicit", "contradicted_by_explicit"}
    FORCED_REREAD_ON_ADD = RELATION_EXPLICIT_COLUMNS | {"status_explicit"}
    MAX_CHUNK_CHARS = 6000

# Pure confidence-lifecycle algebra (spec §4–§5). Literal-import fallback keeps
# index_impl runnable if confidence.py is somehow unavailable (degrades to no
# lifecycle materialization — dark by default anyway).
try:
    import confidence as _confidence
except ImportError:  # pragma: no cover
    _confidence = None

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    project TEXT,
    name TEXT,
    type TEXT,
    evidence TEXT DEFAULT 'observed',
    source TEXT DEFAULT 'user-explicit',
    confidence REAL DEFAULT 0.7,
    last_verified TEXT,
    card_kind TEXT DEFAULT '',
    status TEXT DEFAULT 'current',
    area TEXT DEFAULT '',
    supersedes TEXT DEFAULT '',
    superseded_by TEXT DEFAULT '',
    contradicts TEXT DEFAULT '',
    contradicted_by TEXT DEFAULT '',
    superseded_by_explicit TEXT DEFAULT '',
    contradicted_by_explicit TEXT DEFAULT '',
    status_explicit TEXT DEFAULT '',
    lifecycle TEXT DEFAULT '',
    section_heading TEXT,
    content TEXT NOT NULL,
    description TEXT,
    mtime INTEGER,
    UNIQUE(path, section_heading)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    name, description, section_heading, content,
    content=memory_chunks,
    content_rowid=id,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memory_chunks_ai AFTER INSERT ON memory_chunks BEGIN
    INSERT INTO memory_fts(rowid, name, description, section_heading, content)
    VALUES (new.id, new.name, new.description, new.section_heading, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memory_chunks_ad AFTER DELETE ON memory_chunks BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, name, description, section_heading, content)
    VALUES ('delete', old.id, old.name, old.description, old.section_heading, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memory_chunks_au AFTER UPDATE ON memory_chunks BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, name, description, section_heading, content)
    VALUES ('delete', old.id, old.name, old.description, old.section_heading, old.content);
    INSERT INTO memory_fts(rowid, name, description, section_heading, content)
    VALUES (new.id, new.name, new.description, new.section_heading, new.content);
END;

CREATE TABLE IF NOT EXISTS index_meta (
    path TEXT PRIMARY KEY,
    mtime INTEGER
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- STEP 1B — evidence events (spec §3.2). A DERIVED projection rebuilt from each
-- card's `## Evidence` markdown on reindex (the markdown is the durable truth,
-- P1); confidence is the deterministic fold of these rows. Keyed by the card's
-- FILE PATH — the same per-file identity the fold uses — so two same-slug cards
-- under one project_hash cannot collide in the projection (audit F3);
-- project_hash + card_slug are retained for the §6 cross-project promotion query.
CREATE TABLE IF NOT EXISTS card_events (
    path          TEXT NOT NULL,
    card_slug     TEXT NOT NULL,
    project_hash  TEXT NOT NULL,
    ts            TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    actor_tier    INTEGER NOT NULL,
    session_id    TEXT,
    delta         REAL NOT NULL,
    note          TEXT,
    PRIMARY KEY (path, ts, event_type)
);
"""

# Durable stamp (NEW-1): written once the writer-back-fill file re-read has run,
# so the one-time *_explicit/status_explicit back-fill fires EXACTLY once and
# never again. The old content heuristic keyed on "back-fill column empty while
# the effective column is set" — but that is the LEGITIMATE steady state for a
# card superseded/contradicted BY PROPAGATION (own *_explicit empty), so it
# re-indexed every file on every incremental (rowid churn / vector thrash).
#
# STEP 1B (audit OBS-1): the key is BUMPED on every new back-fill state so an
# already-stamped store re-reads exactly ONCE to populate it, then stamps the new
# key. `backfill_v6b` added the `lifecycle` column + materialized `confidence`;
# `backfill_v6c` (audit F3, turn 11) repopulates `card_events` under its new
# per-`path` schema. A store lacking the current key gets one forced re-read.
BACKFILL_STAMP_KEY = "backfill_v6c"

# Explicit statuses that demote AT LEAST as hard as a propagated supersession
# (status weight <= superseded's 0.35). Only these override a propagated
# supersession in the derived `status` column — so an explicit `archived` stays
# archived, but an explicit PROMOTION/custom (validated / active / wip / …) or
# the default (current/'') yields to the supersession (NEW-2). Ordering:
# authoritative-demotion > propagated-supersession > explicit-promotion/custom
# > default 'current'.
DEMOTION_STATUSES_GE_SUPERSEDED = {"superseded", "deprecated", "obsolete", "archived"}

BASE_SCAN_DIRS = [
    os.path.expanduser("~/.claude/projects/*/memory/"),
    os.path.expanduser("~/.claude/projects/*/memory/signals/"),
    os.path.expanduser("~/.claude/agent-memory/"),
    os.path.expanduser("~/.claude/agent-memory/*/"),
]
SCAN_DIRS = list(BASE_SCAN_DIRS)

EXCLUDE_FILES = {"MEMORY.md", "BACKLOG.md"}
# The writer's migrations ARE the shared single source (constants.MEMORY_CHUNK_
# MIGRATIONS) — kept identical to the reader's (search_impl.ensure_agent_columns)
# so a column added on one path always exists on the other. RELATION_EXPLICIT_
# COLUMNS (also shared) drives the writer-only forced re-read below.
DERIVED_COLUMNS = MEMORY_CHUNK_MIGRATIONS


def parse_frontmatter(text):
    """Parse YAML frontmatter. Handles both root type: and nested metadata.type: formats."""
    meta = {
        "name": "",
        "description": "",
        "type": "reference",
        "evidence": "observed",
        "source": "user-explicit",
        "confidence": 0.7,
        "last_verified": "",
        "card_kind": "",
        "status": "",
        "area": "",
        "supersedes": "",
        "superseded_by": "",
        "contradicts": "",
        "contradicted_by": "",
    }

    if not text.startswith("---"):
        return meta, text

    end = text.find("\n---", 3)
    if end == -1:
        return meta, text

    fm_block = text[4:end]
    body = text[end + 4:].lstrip("\n")

    in_metadata = False
    for line in fm_block.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "metadata:":
            in_metadata = True
            continue

        if in_metadata and line.startswith("  "):
            key_val = stripped
        elif in_metadata and not line.startswith(" "):
            in_metadata = False
            key_val = stripped
        else:
            key_val = stripped

        m = re.match(r'^(\w[\w_-]*):\s*(.+)$', key_val)
        if not m:
            continue

        key, val = m.group(1), m.group(2).strip().strip('"').strip("'")

        if key == "name":
            meta["name"] = val
        elif key == "description":
            meta["description"] = val
        elif key == "type":
            meta["type"] = val
        elif key == "node_type":
            pass
        elif key == "evidence":
            meta["evidence"] = val
        elif key == "source":
            meta["source"] = val
        elif key == "confidence":
            try:
                meta["confidence"] = float(val)
            except ValueError:
                pass
        elif key == "last_verified":
            meta["last_verified"] = val
        elif key in ("card_kind", "kind", "memory_kind"):
            meta["card_kind"] = val
        elif key in ("status", "state"):
            meta["status"] = val
        elif key in ("area", "domain"):
            meta["area"] = val
        elif key == "supersedes":
            meta["supersedes"] = val.strip("[]")
        elif key == "superseded_by":
            meta["superseded_by"] = val.strip("[]")
        elif key == "contradicts":
            meta["contradicts"] = val.strip("[]")
        elif key == "contradicted_by":
            meta["contradicted_by"] = val.strip("[]")

    return meta, body


def file_mtime(filepath):
    """Use nanosecond mtime so rapid same-second edits are not skipped."""
    return os.stat(filepath).st_mtime_ns


def _split_h2(body, filepath):
    """The historical H2-only split (fence-aware, dedup counter). Preserved
    verbatim so the common case (every section ≤ MAX_CHUNK_CHARS) is byte-
    identical (spec-chunker FR-4)."""
    sections = []
    heading_counts = {}
    current_heading = os.path.basename(filepath).replace(".md", "")
    current_lines = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in body.split("\n"):
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            marker_char = marker[0]
            marker_len = len(marker)
            if in_fence and marker_char == fence_char and marker_len >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            elif not in_fence:
                in_fence = True
                fence_char = marker_char
                fence_len = marker_len
            current_lines.append(line)
            continue

        if not in_fence and line.startswith("## "):
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append((current_heading, content))
            raw_heading = line[3:].strip()
            heading_counts[raw_heading] = heading_counts.get(raw_heading, 0) + 1
            if heading_counts[raw_heading] > 1:
                current_heading = f"{raw_heading} ({heading_counts[raw_heading]})"
            else:
                current_heading = raw_heading
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append((current_heading, content))

    if not sections:
        sections.append((os.path.basename(filepath).replace(".md", ""), body.strip()))

    return sections


def _split_at_heading(content, prefix):
    """Fence-aware split at lines starting with `prefix` (e.g. '## ', '### ',
    '#### '). Returns [(heading_text_or_None, stripped_content), …] with
    empty-content segments dropped. A heading line inside a code fence is never a
    boundary (reuses the `in_fence` discipline). The leading None-heading segment
    is the prose before the first `prefix` heading."""
    segments = []
    cur_heading = None
    cur_lines = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in content.split("\n"):
        fm = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fm:
            mc = fm.group(1)[0]
            ml = len(fm.group(1))
            if in_fence and mc == fence_char and ml >= fence_len:
                in_fence = False
                fence_char = ""
                fence_len = 0
            elif not in_fence:
                in_fence = True
                fence_char = mc
                fence_len = ml
            cur_lines.append(line)
            continue
        if not in_fence and line.startswith(prefix):
            segments.append((cur_heading, "\n".join(cur_lines).strip()))
            cur_heading = line[len(prefix):].strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    segments.append((cur_heading, "\n".join(cur_lines).strip()))
    return [(h, t) for (h, t) in segments if t]


def _atomic_blocks(content):
    """Split content into atomic units that must not be broken: each paragraph
    (a run between blank lines) and each whole code fence is ONE unit — so a
    paragraph window never lands inside a fence or mid-paragraph (FR-3/AC-5)."""
    blocks = []
    cur = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    def flush():
        text = "\n".join(cur).strip()
        if text:
            blocks.append(text)

    for line in content.split("\n"):
        fm = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fm:
            mc = fm.group(1)[0]
            ml = len(fm.group(1))
            if not in_fence:
                flush()
                cur = [line]
                in_fence = True
                fence_char = mc
                fence_len = ml
            elif mc == fence_char and ml >= fence_len:
                cur.append(line)
                in_fence = False
                fence_char = ""
                fence_len = 0
                flush()
                cur = []
            else:
                cur.append(line)
            continue
        if in_fence:
            cur.append(line)
        elif line.strip() == "":
            flush()
            cur = []
        else:
            cur.append(line)
    flush()
    return blocks


def _paragraph_windows(breadcrumb, content):
    """FR-3 leaf-overflow fallback: pack atomic blocks into windows ≤
    MAX_CHUNK_CHARS, headed `<breadcrumb> (part N)`. A single unbreakable block
    larger than the ceiling becomes its own (bounded) oversized window."""
    blocks = _atomic_blocks(content)
    windows = []
    cur = []
    for block in blocks:
        if cur:
            if len("\n\n".join(cur + [block])) > MAX_CHUNK_CHARS:
                windows.append("\n\n".join(cur))
                cur = [block]
            else:
                cur.append(block)
        elif len(block) > MAX_CHUNK_CHARS:
            windows.append(block)  # unbreakable oversized block → its own window
        else:
            cur = [block]
    if cur:
        windows.append("\n\n".join(cur))
    return [(f"{breadcrumb} (part {i + 1})", w) for i, w in enumerate(windows)]


def _recursive_split(breadcrumb, content, current_level):
    """FR-1/FR-2: if `content` exceeds the ceiling, split at the SHALLOWEST
    heading level present that is deeper than `current_level` (H2→H3→H4, skipping
    absent levels), joining ancestor headings with ` › ` (depth ≤ 3). A leaf that
    is still oversized falls back to paragraph windows (FR-3)."""
    if len(content) <= MAX_CHUNK_CHARS:
        return [(breadcrumb, content)]
    for lvl in range(current_level + 1, 5):  # try H3 then H4 (H4 is the structural max)
        segs = _split_at_heading(content, "#" * lvl + " ")
        if any(h is not None for h, _ in segs):
            out = []
            for h, seg in segs:
                if h is None:  # leading prose before the first sub-heading
                    out.extend(_recursive_split(breadcrumb, seg, current_level))
                else:
                    out.extend(_recursive_split(breadcrumb + " › " + h, seg, lvl))
            return out
    return _paragraph_windows(breadcrumb, content)


def _apply_dedup(pairs):
    """Duplicate-heading counter applied AFTER breadcrumb construction (FR-2),
    preserving UNIQUE(path, section_heading)."""
    seen = {}
    out = []
    for heading, content in pairs:
        n = seen.get(heading, 0) + 1
        seen[heading] = n
        out.append((heading if n == 1 else f"{heading} ({n})", content))
    return out


def split_sections(body, filepath):
    """Split a markdown body into chunks, size-aware and recursive (spec-chunker).

    FR-4 fast path: when every H2 section is ≤ MAX_CHUNK_CHARS the output is
    byte-identical to the historical H2-only chunker. Otherwise each oversized
    section is split recursively (H2→H3→H4) with ` › ` breadcrumb headings, then
    paragraph-window fallback for an oversized leaf; a final dedup counter keeps
    section_heading unique per file.
    """
    base = _split_h2(body, filepath)
    if all(len(content) <= MAX_CHUNK_CHARS for _h, content in base):
        return base  # FR-4: common case unchanged, byte-for-byte

    basename = os.path.basename(filepath).replace(".md", "")
    pieces = []
    for heading, content in _split_at_heading(body, "## ") or [(None, body.strip())]:
        breadcrumb = heading if heading is not None else basename
        if len(content) <= MAX_CHUNK_CHARS:
            pieces.append((breadcrumb, content))
        else:
            pieces.extend(_recursive_split(breadcrumb, content, 2))
    if not pieces:
        pieces = [(basename, body.strip())]
    return _apply_dedup(pieces)


def detect_project(filepath):
    """Extract project slug from path like ~/.claude/projects/<slug>/memory/."""
    m = re.search(r'/\.claude/projects/([^/]+)/memory/', filepath)
    if m:
        return m.group(1)
    if "agent-memory" in filepath:
        return "__agent__"
    return None


def detect_relation_namespace(filepath, stored_project=""):
    """Fine-grained project key used for RELATION IDENTITY resolution only.

    Audit F5: `detect_project` collapses EVERY `agent-memory/<agent>/` card to
    `'__agent__'` and every skill/non-project card to `None → ''`, so a
    `contradicts:`/`supersedes:` declared in one agent's (or skill's) card could
    resolve to a same-slug card owned by a DIFFERENT agent/skill — the KEEP #1
    identity fix cannot isolate them when the project granularity itself is
    collapsed. This derives a per-agent / per-skill namespace from the path.

    SCOPE (deliberately narrow): this is consumed ONLY by compute_relation_state
    to decide which cards a declaration may bind to. It does NOT change the
    stored `project` column, ranking, area inference, or context injection —
    broadening those would alter behavior beyond the audited relation-isolation
    fix, so the sharpening is confined here.
    """
    m = re.search(r'/\.claude/agent-memory/([^/]+)/', filepath)
    if m:
        return "__agent__:" + m.group(1)
    m = re.search(r'/\.claude/skills/([^/]+)/', filepath)
    if m:
        return "skill:" + m.group(1)
    return stored_project or ""


def _slug_text(*parts):
    return " ".join(str(p or "").lower() for p in parts)


def _has_term(text, *terms):
    """Match lifecycle/kind terms as words, not substrings inside other words."""
    for term in terms:
        pattern = r"(^|[^a-z0-9])" + re.escape(term.lower()) + r"($|[^a-z0-9])"
        if re.search(pattern, text):
            return True
    return False


def infer_card_kind(meta, filepath):
    """Infer a stable memory kind without depending on folder taxonomy."""
    explicit = (meta.get("card_kind") or "").strip().lower()
    if explicit:
        return explicit

    typ = (meta.get("type") or "").strip().lower()
    name = meta.get("name") or os.path.basename(filepath).replace(".md", "")
    text = _slug_text(name, meta.get("description"))

    if typ == "feedback":
        return "rule"
    if typ == "user":
        return "profile"
    if typ == "code":
        return "code"
    if _has_term(text, "handoff"):
        return "handoff"
    if _has_term(text, "todo", "next-session"):
        return "todo"
    if _has_term(text, "state", "status"):
        return "status"
    if _has_term(text, "bug", "regression"):
        return "bug"
    if _has_term(text, "decision", "decided"):
        return "decision"
    if _has_term(text, "synthesis", "synthesized"):
        return "synthesis"
    if _has_term(text, "research", "study"):
        return "research"
    if typ == "reference" or _has_term(text, "reference", "skill"):
        return "reference"
    return "finding"


def infer_status(meta, filepath):
    """Infer lifecycle status. Explicit frontmatter is the ONLY demotion signal.

    Name/description keyword inference was removed (2026-06-25): it silently mis-demoted
    CURRENT cards whose title or description merely MENTIONED a lifecycle word. A finding
    ABOUT a fix ("...Fixed 2026-06-25") ranked as `resolved` (0.75x); any card with the
    word "archive" in its name/description ranked as `archived` (0.25x) — _slug_text folds
    in the description, so prose collided. Real archival is now set EXPLICITLY via
    frontmatter `status:` (curate archive --apply) + `superseded_by`, so the keyword
    fallback is legacy and net-harmful. A card is `current` unless it declares otherwise.
    """
    explicit = (meta.get("status") or "").strip().lower()
    if explicit:
        return explicit
    if (meta.get("superseded_by") or "").strip():
        return "superseded"
    return "current"


def infer_area(meta, filepath, project):
    explicit = (meta.get("area") or "").strip()
    if explicit:
        return explicit
    return project or ""


def _ensure_schema_meta(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)"
    )


def backfill_stamp_present(conn):
    """True once the writer-back-fill file re-read has completed (durable stamp).

    This is the ONLY signal that gates the one-time *_explicit/status_explicit
    back-fill (NEW-1). It must never be inferred from column contents: a card
    superseded/contradicted BY PROPAGATION legitimately has an empty *_explicit
    while its effective column is set, so a content heuristic would fire forever.
    """
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (BACKFILL_STAMP_KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0] == "done")


def set_backfill_stamp(conn):
    """Record that the back-fill re-read ran, so it never fires again. Called at
    the END of a writer run (run_incremental / run_full), after every file has
    been read and *_explicit/status_explicit populated from frontmatter."""
    try:
        _ensure_schema_meta(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, 'done')",
            (BACKFILL_STAMP_KEY,),
        )
    except sqlite3.OperationalError:
        pass


def migrate_schema(conn):
    """Add v2.6 columns to existing derived DBs."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    for column, statement in DERIVED_COLUMNS.items():
        if column not in existing:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    _ensure_schema_meta(conn)
    # One-time forced re-read to back-fill *_explicit/status_explicit from real
    # frontmatter (a legacy or reader-upgraded DB has them empty). Gated SOLELY
    # by the durable stamp (NEW-1): when it is absent we invalidate stored mtimes
    # so the next incremental reloads every file; set_backfill_stamp() then marks
    # it done at the end of that writer run, so it fires exactly once — never on
    # a store whose *_explicit are correctly empty because relations propagate.
    if not backfill_stamp_present(conn):
        try:
            conn.execute("UPDATE index_meta SET mtime = -1")
        except sqlite3.OperationalError:
            pass


def check_evidence_divergence(conn):
    """§3.2 doctor check: report any card whose `## Evidence` markdown (the
    durable truth) disagrees with its `card_events` projection.

    The projection is rebuilt from the markdown on every reindex, so markdown
    always WINS and a divergence self-heals — but a transient (edited-not-yet-
    reindexed) or tampered divergence is surfaced LOUDLY here. Returns a list of
    human-readable problems (empty when consistent). Candidates = every path in
    the projection plus every card that carries an indexed `## Evidence` section.
    """
    problems = []
    try:
        proj = {}
        for path, ts, et in conn.execute("SELECT path, ts, event_type FROM card_events"):
            proj.setdefault(path, set()).add((ts, et))
        candidates = set(proj)
        for (path,) in conn.execute(
                "SELECT DISTINCT path FROM memory_chunks WHERE section_heading = 'Evidence'"):
            candidates.add(path)
    except sqlite3.OperationalError:
        return problems  # pre-1B DB without card_events — nothing to check
    for path in sorted(candidates):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                _meta, body = parse_frontmatter(f.read())
            src = {(e["ts"], e["event_type"]) for e in parse_evidence_events(body)}
        except OSError:
            src = set()  # file gone but the projection still has rows → divergence
        got = proj.get(path, set())
        if src != got:
            problems.append(
                f"{path}: card_events diverges from `## Evidence` "
                f"(markdown-only={len(src - got)}, projection-only={len(got - src)}) "
                "— markdown wins; run index.sh --full to rebuild the projection"
            )
    return problems


def check_relation_schema(conn):
    """Surface a missing/malformed explicit-relation column instead of letting
    truth-maintenance silently no-op.

    The `*_explicit` columns are load-bearing: compute_relation_state SELECTs
    them, so if they are absent (a pre-migration or corrupted schema) the query
    raises OperationalError and the whole declared-relation propagation is
    skipped. That skip used to be swallowed silently — a contradiction/
    supersession would simply never take effect, with no warning. This returns
    a list of human-readable problems (empty when the schema is healthy) so the
    index-time path and any doctor/lint can WARN loudly.
    """
    try:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    except sqlite3.OperationalError as exc:
        return [f"memory_chunks table unreadable ({exc}) — run index.sh --full"]
    if not cols:
        return ["memory_chunks table missing — run index.sh --full"]
    problems = []
    for col in sorted(RELATION_EXPLICIT_COLUMNS):
        info = cols.get(col)
        if info is None:
            problems.append(
                f"missing relation column {col!r} — declared contradictions/"
                "supersession are disabled; run index.sh --full to migrate"
            )
            continue
        # PRAGMA table_info row = (cid, name, type, notnull, dflt_value, pk).
        col_type = (info[2] or "").upper()
        if col_type not in ("TEXT", ""):
            problems.append(
                f"relation column {col!r} has unexpected type {col_type!r} "
                "(expected TEXT); run index.sh --full to repair"
            )
    return problems


def migrate_feedback_user_inferred_statuses(conn):
    """Correct derived inactive statuses inferred from feedback/user wording."""
    try:
        rows = conn.execute("""
            SELECT DISTINCT path
            FROM memory_chunks
            WHERE type IN ('feedback', 'user')
              AND IFNULL(status, 'current') != 'current'
              AND IFNULL(superseded_by, '') = ''
        """).fetchall()
    except sqlite3.OperationalError:
        return

    for (path,) in rows:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                meta, _ = parse_frontmatter(f.read())
        except OSError:
            continue
        if (meta.get("status") or "").strip() or (meta.get("superseded_by") or "").strip():
            continue
        conn.execute("UPDATE memory_chunks SET status = 'current' WHERE path = ?", (path,))


def needs_lifecycle_backfill(conn):
    """Detect old rows that got v2.6 columns but skipped semantic inference."""
    try:
        row = conn.execute("""
            SELECT 1 FROM memory_chunks
            WHERE IFNULL(card_kind, '') = ''
            LIMIT 1
        """).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def memory_system_from_db(db_path):
    db_dir = os.path.dirname(os.path.abspath(os.path.expanduser(db_path)))
    if os.path.basename(db_dir) == "db":
        return os.path.dirname(db_dir)
    return os.path.expanduser(
        os.environ.get("EIDETIC_MEMORY_SYSTEM", "~/.claude/memory-system")
    )


def scan_dirs(memory_system=None):
    dirs = list(SCAN_DIRS)
    if memory_system:
        signals_dir = os.path.join(memory_system, "signals") + os.sep
        if signals_dir not in dirs:
            dirs.append(signals_dir)
    return dirs


def base_manifest(memory_system):
    """If `memory_system` is a topic base (has `.eidetic-base.json` at its root), return
    the parsed manifest dict; else None. A topic base scans ONLY its declared corpus_dirs
    — never `~/.claude` — so the personal memory is never pulled into a base index, and a
    base never auto-injects into your sessions (isolation invariant P1)."""
    if not memory_system:
        return None
    try:
        with open(os.path.join(memory_system, ".eidetic-base.json"), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) else None


def _collect_base_files(memory_system, manifest):
    """Topic base: collect .md RECURSIVELY from the manifest's corpus_dirs only
    (`docs/` nests: `api/`, `library/<book>/`…). corpus_dirs are relative to the base
    root (portable across clones). REPLACES BASE_SCAN_DIRS.

    P1 ISOLATION (load-bearing): a corpus_dir that resolves OUTSIDE the base root — via
    an absolute path, a `../` escape, or a symlink — is REFUSED (it would otherwise scan
    personal memory). Containment is enforced on the realpath of every corpus root AND
    every collected file, so a symlinked dir or file can never leak in."""
    base_real = os.path.realpath(memory_system)

    def _inside(path):
        rp = os.path.realpath(path)
        return rp == base_real or rp.startswith(base_real + os.sep)

    files, seen = [], set()
    for d in (manifest.get("corpus_dirs") or ["docs", "notes"]):
        root = d if os.path.isabs(d) else os.path.join(memory_system, d)
        if not _inside(root):
            print(f"WARN: corpus_dir {d!r} escapes the base root — skipped (P1 isolation)",
                  file=sys.stderr)
            continue
        for dirpath, _subdirs, fnames in os.walk(root):
            for f in fnames:
                if not f.endswith(".md") or f in EXCLUDE_FILES or f.endswith(".bak"):
                    continue
                fp = os.path.join(dirpath, f)
                if fp in seen or not _inside(fp):   # per-file realpath: blocks symlinked leaks
                    continue
                seen.add(fp)
                files.append(fp)
    return files


def collect_files(memory_system=None):
    """Collect all .md files. A topic base (manifest present) scans ONLY its corpus_dirs,
    recursively; the personal index keeps its existing non-recursive per-dir scan + skills."""
    manifest = base_manifest(memory_system)
    if manifest is not None:
        return _collect_base_files(memory_system, manifest)
    files = []
    seen = set()
    for pattern in scan_dirs(memory_system):
        for dirpath in glob.glob(pattern):
            if not os.path.isdir(dirpath):
                continue
            for f in os.listdir(dirpath):
                if not f.endswith(".md"):
                    continue
                if f in EXCLUDE_FILES:
                    continue
                if f.endswith(".bak"):
                    continue
                fullpath = os.path.join(dirpath, f)
                if fullpath not in seen:
                    seen.add(fullpath)
                    files.append(fullpath)

    skill_pattern = os.path.expanduser("~/.claude/skills/*/SKILL.md")
    for f in glob.glob(skill_pattern):
        if f not in seen:
            seen.add(f)
            files.append(f)

    return files


def init_db(db_path):
    """Initialize SQLite database with FTS5 schema."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DB_SCHEMA)
    _migrate_card_events(conn)
    migrate_schema(conn)
    migrate_feedback_user_inferred_statuses(conn)
    conn.commit()
    return conn


def _migrate_card_events(conn):
    """F3: an existing 1B store has a card_events table keyed by
    (project_hash, card_slug); drop it so the new per-`path` schema (from
    DB_SCHEMA) takes effect. Safe — card_events is a DERIVED projection rebuilt
    from the `## Evidence` markdown; the stamp bump forces a one-time re-read
    that repopulates it under the new key."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(card_events)")}
    except sqlite3.OperationalError:
        return
    if cols and "path" not in cols:
        conn.execute("DROP TABLE card_events")
        conn.executescript(DB_SCHEMA)  # re-create card_events (CREATE IF NOT EXISTS)


_EVIDENCE_LINE_RE = re.compile(r"^-\s+(.*\S)\s*$")


def parse_evidence_events(body):
    """Parse a card's `## Evidence` section into chronological events (spec §3.2).

    Line format: `- <ts> · <event_type> · <actor> · sess=<id> · Δ<signed> · "note"`.
    Only the typed `## Evidence` section is read — NEVER legacy `## History`
    date-lines (risk #5 double-count guard). The fold recomputes the effective
    delta from event_type + order; the parsed Δ is decorative/audit only.
    """
    events = []
    in_section = False
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped[3:].strip().casefold() == "evidence"
            continue
        if not in_section:
            continue
        m = _EVIDENCE_LINE_RE.match(stripped)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("·")]
        if len(parts) < 2:
            continue
        etype = parts[1].strip().lower()
        if _confidence is not None and etype not in _confidence.EVENT_TYPES:
            continue
        actor = parts[2].strip().lower() if len(parts) > 2 else ""
        tier = _confidence.ACTOR_TIERS.get(actor) if _confidence is not None else None
        session_id, delta, note = None, 0.0, ""
        for p in parts[3:]:
            if p.startswith("sess="):
                session_id = p[5:].strip() or None
            elif p.startswith("Δ"):
                try:
                    delta = float(p[1:].replace("+", "").strip())
                except ValueError:
                    pass
            elif p[:1] in ("\"", "'"):
                note = p.strip("\"'")
        events.append({"ts": parts[0], "event_type": etype, "actor_tier": tier,
                       "session_id": session_id, "delta": delta, "note": note})
    return events


def _card_slug(meta, filepath):
    """Normalized slug for the confidence identity (spec §3.3)."""
    name = meta.get("name") or os.path.basename(_path_sans_md(filepath))
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").casefold()).strip("-")
    if slug.endswith(".md"):
        slug = slug[:-3]
    return slug or "unnamed"


def _write_card_events(conn, filepath, meta, card_kind, events):
    """Rebuild this card's `card_events` projection from its `## Evidence`
    markdown (P1: markdown is truth; DELETE-then-insert makes it order-independent
    and clears removed events every reindex). Tolerates the table's absence."""
    slug = _card_slug(meta, filepath)
    phash = detect_project(filepath) or ""
    try:
        # Scope by the FILE PATH (the fold's per-card identity), so a same-slug
        # sibling in the same project can never DELETE this card's events (F3).
        conn.execute("DELETE FROM card_events WHERE path = ?", (filepath,))
        for ev in events:
            tier = ev.get("actor_tier")
            if tier is None and _confidence is not None:
                tier = _confidence.EVENT_SPECS.get(ev["event_type"], {"tier": 1})["tier"]
            conn.execute(
                "INSERT OR REPLACE INTO card_events "
                "(path, card_slug, project_hash, ts, event_type, actor_tier, session_id, delta, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (filepath, slug, phash, ev["ts"], ev["event_type"], int(tier or 1),
                 ev.get("session_id"), float(ev.get("delta", 0.0) or 0.0),
                 ev.get("note", "")),
            )
    except sqlite3.OperationalError:
        pass  # legacy DB without card_events (reader path) — tolerate


def _lifecycle_and_confidence(conn, filepath, meta, card_kind, body):
    """STEP 1B dark materialization (spec §2.3, §3.4, §4): compute the card's
    lifecycle label and its DERIVED confidence (the deterministic fold of its
    `## Evidence` events over the cold-start). Also rebuild this card's
    `card_events` projection from the markdown (P1: markdown is truth).

    Managed cards: confidence = fold(cold_start, events). At migration (no
    `## Evidence` yet) events=[] → the §3.4 cold-start value exactly. Exempt
    cards keep their authored/default `confidence` (unused — conf_w = 1.0).
    """
    if _confidence is None:
        return "", meta["confidence"]
    lifecycle = _confidence.lifecycle_label(meta["type"], meta["source"], card_kind)
    events = parse_evidence_events(body)
    _write_card_events(conn, filepath, meta, card_kind, events)
    if lifecycle != "managed":
        return lifecycle, meta["confidence"]
    cold = _confidence.cold_start_confidence(meta["type"], meta["source"], card_kind)
    user_authored = (meta.get("source") or "") == "user-explicit"
    conf, _flags = _confidence.fold_confidence(cold, events, user_authored=user_authored)
    return lifecycle, conf


def index_file(conn, filepath, meta, body):
    """Index a single file's sections into the database."""
    project = detect_project(filepath)
    card_kind = infer_card_kind(meta, filepath)
    status = infer_status(meta, filepath)
    area = infer_area(meta, filepath, project)
    mtime = file_mtime(filepath)
    sections = split_sections(body, filepath)
    lifecycle, confidence_value = _lifecycle_and_confidence(
        conn, filepath, meta, card_kind, body)

    conn.execute("DELETE FROM memory_chunks WHERE path = ?", (filepath,))

    for heading, content in sections:
        conn.execute(
            """INSERT INTO memory_chunks
               (path, project, name, type, evidence, source, confidence,
                last_verified, card_kind, status, area, supersedes,
                superseded_by, contradicts, contradicted_by,
                superseded_by_explicit, contradicted_by_explicit, status_explicit,
                lifecycle, section_heading, content, description, mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                filepath, project, meta["name"], meta["type"],
                meta["evidence"], meta["source"], confidence_value,
                meta["last_verified"], card_kind, status, area,
                meta["supersedes"], meta["superseded_by"],
                meta["contradicts"], meta["contradicted_by"],
                meta["superseded_by"], meta["contradicted_by"],
                (meta.get("status") or "").strip().lower(), lifecycle, heading,
                content, meta["description"], mtime,
            ),
        )

    conn.execute(
        "INSERT OR REPLACE INTO index_meta (path, mtime) VALUES (?, ?)",
        (filepath, mtime),
    )


def clear_indexed_file(conn, filepath, mtime):
    """Record an existing but now-empty memory file as having no indexable chunks."""
    conn.execute("DELETE FROM memory_chunks WHERE path = ?", (filepath,))
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (path, mtime) VALUES (?, ?)",
        (filepath, mtime),
    )


def _split_relation_targets(value):
    """`supersedes:`/`contradicts:` values → clean target slugs.

    Accepts comma-separated lists, optional [[wikilink]] brackets and quotes.
    """
    targets = []
    for raw in (value or "").split(","):
        target = raw.strip().strip("[]").strip('"').strip("'").strip()
        if target:
            targets.append(target)
    return targets


def _normalize_relation_target(target):
    """Match key for a declared target: drop a `.md` suffix, casefold."""
    t = (target or "").strip()
    if t.endswith(".md"):
        t = t[:-3]
    return t.casefold()


def _path_sans_md(path):
    return path[:-3] if path.endswith(".md") else path


def _card_label(path, name):
    stem = os.path.basename(path)
    return name or (stem[:-3] if stem.endswith(".md") else stem)


def _declarer_outranks(declarer, target):
    """Authority gate: may this declaration DOWN-RANK its target?

    A declaration down-ranks only when the declaring card is at least as new
    AND of source-tier >= the target's (user-explicit always tier-qualifies).
    An agent-extracted or hypothesis card can therefore never poison a
    user-explicit/validated card's ranking; the claim is still SURFACED as a
    non-penalizing drift finding (drift_check.check_relation_diagnostics).
    """
    if (declarer["mtime"] or 0) < (target["mtime"] or 0):
        return False
    if (declarer["source"] or "user-explicit") == "user-explicit":
        return True
    d_tier = (SOURCE_WEIGHTS.get(declarer["source"], 0.5)
              * EVIDENCE_WEIGHTS.get(declarer["evidence"], 0.7))
    t_tier = (SOURCE_WEIGHTS.get(target["source"], 0.5)
              * EVIDENCE_WEIGHTS.get(target["evidence"], 0.7))
    return d_tier >= t_tier


def compute_relation_state(conn):
    """Recompute the FULL declared-relation state from current declarers.

    Returns (updates, unresolved, gated):
      updates    — {(target_path, column): effective_value} for EVERY card and
                   both columns; '' when no live declarer and no explicit
                   frontmatter, so removed declarations CLEAR on the next run.
      unresolved — [(declarer_path, relation, target)] declared targets that
                   resolve to no card in the declarer's project (typo, deleted
                   target, or unqualified cross-project reference).
      gated      — [(target_path, column, label)] declarations refused by the
                   authority gate: surfaced, never applied to ranking.

    Scoping: BOTH a bare target slug/name AND a path-qualified target (contains
    '/') match only cards in the declarer's OWN project — cross-project same-slug
    or same-suffix cards can never contaminate each other. Identity is always
    (project, normalized name); a path qualifier only narrows WITHIN the project.
    """
    rows = conn.execute("""
        SELECT DISTINCT path, project, name, source, evidence, mtime,
               supersedes, contradicts,
               superseded_by_explicit, contradicted_by_explicit, status_explicit
        FROM memory_chunks
    """).fetchall()

    cards = {}
    by_key = {}
    for (path, project, name, source, evidence, mtime,
         supersedes, contradicts, sup_explicit, con_explicit, status_explicit) in rows:
        # Fine-grained namespace (per-agent, per-skill) for relation identity
        # only — see detect_relation_namespace (audit F5). Not the stored column.
        rel_project = detect_relation_namespace(path, project or "")
        cards[path] = {
            "project": rel_project,
            "name": name or "",
            "source": source or "user-explicit",
            "evidence": evidence or "observed",
            "mtime": mtime or 0,
            "supersedes": supersedes or "",
            "contradicts": contradicts or "",
            "superseded_by_explicit": (sup_explicit or "").strip(),
            "contradicted_by_explicit": (con_explicit or "").strip(),
            "status_explicit": (status_explicit or "").strip().lower(),
        }
        stem = os.path.basename(_path_sans_md(path))
        by_key.setdefault((rel_project, stem.casefold()), set()).add(path)
        if name:
            by_key.setdefault((rel_project, name.casefold()), set()).add(path)

    def resolve(declarer_path, target):
        declarer_project = cards[declarer_path]["project"]
        if "/" in target:
            # A path-qualified target is a MORE SPECIFIC name, not a license to
            # reach across projects: the identity is still (project, name). The
            # old bare `endswith("/" + cand)` matched a same-suffix card in EVERY
            # project, so `contradicts: notes/methodology.md` in one project
            # rank-nuked an identically-pathed card in another (the KEEP #1 bug).
            # Scope the suffix match to the declarer's own project.
            cand = _path_sans_md(os.path.expanduser(target.strip()))
            return {
                p for p in cards
                if cards[p]["project"] == declarer_project
                and (_path_sans_md(p) == cand
                     or _path_sans_md(p).endswith("/" + cand.lstrip("/")))
            }
        key = (declarer_project, _normalize_relation_target(target))
        return set(by_key.get(key, ()))

    desired = {path: {"superseded_by": set(), "contradicted_by": set()} for path in cards}
    unresolved = []
    gated = []

    for path, card in cards.items():
        for column, relation, value in (
            ("superseded_by", "supersedes", card["supersedes"]),
            ("contradicted_by", "contradicts", card["contradicts"]),
        ):
            for target in _split_relation_targets(value):
                target_paths = resolve(path, target) - {path}
                if not target_paths:
                    unresolved.append((path, relation, target))
                    continue
                label = _card_label(path, card["name"])
                for target_path in sorted(target_paths):
                    target_card = cards[target_path]
                    if target_card[column + "_explicit"]:
                        continue  # the target's own frontmatter wins
                    if _declarer_outranks(card, target_card):
                        desired[target_path][column].add(label)
                    else:
                        gated.append((target_path, column, label))

    updates = {}
    for path, card in cards.items():
        for column in ("superseded_by", "contradicted_by"):
            explicit = card[column + "_explicit"]
            updates[(path, column)] = explicit or ", ".join(sorted(desired[path][column]))
        # DERIVED status recompute (clear-when-removed), with a strict priority
        # order (audit F4 + NEW-2):
        #   1. an explicit AUTHORITATIVE DEMOTION (archived/deprecated/obsolete/
        #      superseded — weight <= superseded's) wins: archived stays archived;
        #   2. else a set EFFECTIVE superseded_by (own OR propagated from another
        #      card's `supersedes:`) → 'superseded' — so `status: current`
        #      (F4) AND an explicit PROMOTION/custom (validated/active/wip, NEW-2)
        #      no longer mask a real supersession in the derived column;
        #   3. else an explicit non-default status (promotion/custom) is kept;
        #   4. else 'current'. Reverts to (3)/'current' when the declaration is
        #      removed.
        explicit_status = card["status_explicit"]
        effective_superseded_by = updates[(path, "superseded_by")]
        if explicit_status in DEMOTION_STATUSES_GE_SUPERSEDED:
            derived_status = explicit_status
        elif effective_superseded_by:
            derived_status = "superseded"
        elif explicit_status and explicit_status != "current":
            derived_status = explicit_status
        else:
            derived_status = "current"
        updates[(path, "status")] = derived_status
    return updates, unresolved, gated


def propagate_declared_relations(conn):
    """Truth-maintenance slice: push declared `supersedes:`/`contradicts:`
    onto their TARGET cards (the target's file usually doesn't know).

    A card declaring `supersedes: X` marks every same-project card named/
    slugged X as `superseded_by` it (→ existing 0.35 status weight at search
    time); a card declaring `contradicts: X` marks X `contradicted_by` it
    (→ drift finding + 0.4 ranking penalty). AUTHORITATIVE: the whole-DB
    post-pass recomputes every target's state from the CURRENT declarers on
    every run (incremental included), so a removed/edited declaration clears
    its target and the penalty auto-resolves on the next drift run. A target's
    own explicit frontmatter always wins; below-authority declarations are
    gated (see _declarer_outranks) and only surfaced. Resolution is by
    declared name / file stem (or path suffix) within the declarer's project;
    semantic matching is v6.
    """
    try:
        updates, unresolved, gated = compute_relation_state(conn)
    except sqlite3.OperationalError as exc:
        # Never swallow this silently: a missing/malformed *_explicit column
        # disables ALL truth-maintenance, so SURFACE why before skipping.
        problems = check_relation_schema(conn) or [f"memory_chunks query failed ({exc})"]
        for problem in problems:
            print(
                f"WARN: declared-relation propagation skipped — {problem}",
                file=sys.stderr,
            )
        return
    for (path, column), value in updates.items():
        conn.execute(
            f"UPDATE memory_chunks SET {column} = ? "
            f"WHERE path = ? AND IFNULL({column}, '') != ?",
            (value, path, value),
        )
    for declarer_path, relation, target in unresolved:
        print(
            f"WARN: unresolved {relation} target {target!r} declared in "
            f"{declarer_path} — no matching card in its project",
            file=sys.stderr,
        )
    conn.commit()


def run_full(conn, files):
    """Full reindex: build temp DB then atomic swap via os.replace (B2: SIGKILL-safe)."""
    import shutil
    import tempfile

    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    if not db_path:
        raise RuntimeError("Cannot determine DB path")

    db_dir = os.path.dirname(db_path)
    fd, tmp_path = tempfile.mkstemp(dir=db_dir, suffix=".tmp.db")
    os.close(fd)

    try:
        tmp_conn = sqlite3.connect(tmp_path)
        tmp_conn.executescript(DB_SCHEMA)

        indexed = 0
        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                meta, body = parse_frontmatter(text)
                if body.strip():
                    index_file(tmp_conn, filepath, meta, body)
                    indexed += 1
            except Exception as e:
                print(f"WARN: skip {filepath}: {e}", file=sys.stderr)
        tmp_conn.commit()
        propagate_declared_relations(tmp_conn)
        # --full honestly re-read every file, so *_explicit/status_explicit are
        # correct — stamp the back-fill as done so incrementals never re-heal.
        set_backfill_stamp(tmp_conn)
        tmp_conn.commit()
        tmp_conn.close()

        conn.close()
        os.replace(tmp_path, db_path)

        return indexed
    except Exception as e:
        print(f"ERROR: full reindex failed: {e}", file=sys.stderr)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def run_incremental(conn, files):
    """Incremental reindex: only changed files."""
    existing = {}
    for row in conn.execute("SELECT path, mtime FROM index_meta"):
        existing[row[0]] = row[1]

    force_backfill = needs_lifecycle_backfill(conn)
    existing_for_cleanup = dict(existing)
    skip_existing = {} if force_backfill else existing

    current_paths = set()
    changed_cards = []
    indexed = 0
    skipped = 0

    for filepath in files:
        current_paths.add(filepath)
        mtime = file_mtime(filepath)

        if filepath in skip_existing and skip_existing[filepath] == mtime:
            skipped += 1
            continue

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            meta, body = parse_frontmatter(text)
            if body.strip():
                index_file(conn, filepath, meta, body)
                changed_cards.append(filepath)
                indexed += 1
            else:
                clear_indexed_file(conn, filepath, mtime)
                indexed += 1
        except Exception as e:
            print(f"WARN: skip {filepath}: {e}", file=sys.stderr)

    removed = 0
    for old_path in existing_for_cleanup:
        if old_path not in current_paths:
            conn.execute("DELETE FROM memory_chunks WHERE path = ?", (old_path,))
            conn.execute("DELETE FROM index_meta WHERE path = ?", (old_path,))
            # NEW-2: also drop the deleted/renamed card's event projection, else
            # orphan card_events rows make the doctor report a PERSISTENT
            # `## Evidence`↔card_events divergence on every incremental until a
            # --full (the projection is per-`path`, F3), and --full != --incr.
            conn.execute("DELETE FROM card_events WHERE path = ?", (old_path,))
            removed += 1

    conn.commit()
    propagate_declared_relations(conn)
    # The back-fill re-read (if migrate_schema forced one) has now run for every
    # file, so *_explicit/status_explicit are populated — stamp it done so the
    # next migrate_schema does NOT force a re-read again (NEW-1: no perpetual
    # re-index on stores that use propagated relations).
    set_backfill_stamp(conn)
    conn.commit()

    # M1 semantic contradiction detection (spec-m1-contradiction FR-1/FR-7). This
    # is DARK-SAFE and DORMANT: run_on_ingest is a pure no-op unless
    # EIDETIC_CONFIDENCE_EVENTS is on AND a production confirmer is registered
    # (a turn-2 wiring) AND a vectors.db exists — so it adds zero cost and cannot
    # change any card here. It never raises into the indexer.
    if changed_cards:
        try:
            import m1_contradiction
            row = conn.execute("PRAGMA database_list").fetchone()
            db_file = row[2] if row else ""
            if db_file:
                m1_contradiction.run_on_ingest(conn, db_file, changed_cards)
        except Exception as e:
            print(f"WARN: M1 hook skipped: {e}", file=sys.stderr)

    # M2 multi-page synthesis (spec-m2-synthesis FR-1/FR-9). DARK-SAFE: a complete
    # no-op unless EIDETIC_CONFIDENCE_EVENTS is on. Runs AFTER M1 so M1 owns
    # contradictions and M2 defers to it; M2 revises only its own sentinel-delimited
    # synthesis region on managed neighbors. Never raises into the indexer.
    if changed_cards:
        try:
            import m2_synthesis
            row = conn.execute("PRAGMA database_list").fetchone()
            db_file = row[2] if row else ""
            if db_file:
                m2_synthesis.run_on_ingest(conn, db_file, changed_cards)
        except Exception as e:
            print(f"WARN: M2 hook skipped: {e}", file=sys.stderr)

    if force_backfill:
        print("Lifecycle metadata backfill: reindexed existing memory files")
    return indexed, skipped, removed


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--incremental"
    db_path = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
        "~/.claude/memory-system/db/index.db"
    )

    t0 = time.time()
    files = collect_files(memory_system_from_db(db_path))
    conn = init_db(db_path)

    if mode == "--full":
        indexed = run_full(conn, files)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        elapsed = time.time() - t0
        total = conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0]
        print(f"Full index: {indexed} files, {total} chunks, {elapsed:.2f}s")
    else:
        indexed, skipped, removed = run_incremental(conn, files)
        elapsed = time.time() - t0
        total = conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0]
        print(
            f"Incremental: {indexed} indexed, {skipped} skipped, "
            f"{removed} removed, {total} chunks, {elapsed:.2f}s"
        )

    conn.close()


if __name__ == "__main__":
    main()
