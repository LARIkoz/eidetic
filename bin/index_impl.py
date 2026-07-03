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
        MEMORY_CHUNK_MIGRATIONS, RELATION_EXPLICIT_COLUMNS,
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
    }
    RELATION_EXPLICIT_COLUMNS = {"superseded_by_explicit", "contradicted_by_explicit"}

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
"""

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


def split_sections(body, filepath):
    """Split markdown body by ## headings into chunks."""
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


def detect_project(filepath):
    """Extract project slug from path like ~/.claude/projects/<slug>/memory/."""
    m = re.search(r'/\.claude/projects/([^/]+)/memory/', filepath)
    if m:
        return m.group(1)
    if "agent-memory" in filepath:
        return "__agent__"
    return None


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


def migrate_schema(conn):
    """Add v2.6 columns to existing derived DBs."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    added = set()
    for column, statement in DERIVED_COLUMNS.items():
        if column not in existing:
            try:
                conn.execute(statement)
                added.add(column)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    if added & RELATION_EXPLICIT_COLUMNS:
        # One-time forced re-read: invalidate stored mtimes (keep the rows so
        # deleted-file cleanup still works) so the next incremental run reloads
        # every file and fills the *_explicit columns from real frontmatter.
        try:
            conn.execute("UPDATE index_meta SET mtime = -1")
        except sqlite3.OperationalError:
            pass


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
    migrate_schema(conn)
    migrate_feedback_user_inferred_statuses(conn)
    conn.commit()
    return conn


def index_file(conn, filepath, meta, body):
    """Index a single file's sections into the database."""
    project = detect_project(filepath)
    card_kind = infer_card_kind(meta, filepath)
    status = infer_status(meta, filepath)
    area = infer_area(meta, filepath, project)
    mtime = file_mtime(filepath)
    sections = split_sections(body, filepath)

    conn.execute("DELETE FROM memory_chunks WHERE path = ?", (filepath,))

    for heading, content in sections:
        conn.execute(
            """INSERT INTO memory_chunks
               (path, project, name, type, evidence, source, confidence,
                last_verified, card_kind, status, area, supersedes,
                superseded_by, contradicts, contradicted_by,
                superseded_by_explicit, contradicted_by_explicit,
                section_heading, content, description, mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                filepath, project, meta["name"], meta["type"],
                meta["evidence"], meta["source"], meta["confidence"],
                meta["last_verified"], card_kind, status, area,
                meta["supersedes"], meta["superseded_by"],
                meta["contradicts"], meta["contradicted_by"],
                meta["superseded_by"], meta["contradicted_by"], heading,
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
               superseded_by_explicit, contradicted_by_explicit
        FROM memory_chunks
    """).fetchall()

    cards = {}
    by_key = {}
    for (path, project, name, source, evidence, mtime,
         supersedes, contradicts, sup_explicit, con_explicit) in rows:
        cards[path] = {
            "project": project or "",
            "name": name or "",
            "source": source or "user-explicit",
            "evidence": evidence or "observed",
            "mtime": mtime or 0,
            "supersedes": supersedes or "",
            "contradicts": contradicts or "",
            "superseded_by_explicit": (sup_explicit or "").strip(),
            "contradicted_by_explicit": (con_explicit or "").strip(),
        }
        stem = os.path.basename(_path_sans_md(path))
        by_key.setdefault((project or "", stem.casefold()), set()).add(path)
        if name:
            by_key.setdefault((project or "", name.casefold()), set()).add(path)

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
            removed += 1

    conn.commit()
    propagate_declared_relations(conn)
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
