#!/usr/bin/env python3
"""AI Memory System v1 — FTS5 Indexer.

Scans memory directories, parses YAML frontmatter (both root and nested metadata formats),
splits by ## headings, and upserts into SQLite FTS5.

Zero external deps: python3 stdlib + sqlite3.
"""

import glob
import os
import re
import sqlite3
import sys
import time

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

SCAN_DIRS = [
    os.path.expanduser("~/.claude/projects/*/memory/"),
    os.path.expanduser("~/.claude/projects/*/memory/signals/"),
    os.path.expanduser("~/.claude/agent-memory/"),
    os.path.expanduser("~/.claude/agent-memory/*/"),
]

EXCLUDE_FILES = {"MEMORY.md", "BACKLOG.md"}
DERIVED_COLUMNS = {
    "card_kind": "ALTER TABLE memory_chunks ADD COLUMN card_kind TEXT DEFAULT ''",
    "status": "ALTER TABLE memory_chunks ADD COLUMN status TEXT DEFAULT 'current'",
    "area": "ALTER TABLE memory_chunks ADD COLUMN area TEXT DEFAULT ''",
    "supersedes": "ALTER TABLE memory_chunks ADD COLUMN supersedes TEXT DEFAULT ''",
    "superseded_by": "ALTER TABLE memory_chunks ADD COLUMN superseded_by TEXT DEFAULT ''",
}


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


def split_sections(body, filepath):
    """Split markdown body by ## headings into chunks."""
    sections = []
    heading_counts = {}
    current_heading = os.path.basename(filepath).replace(".md", "")
    current_lines = []

    for line in body.split("\n"):
        if line.startswith("## "):
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
    if _has_term(text, "research", "study"):
        return "research"
    if typ == "reference" or _has_term(text, "reference", "skill"):
        return "reference"
    return "finding"


def infer_status(meta, filepath):
    """Infer lifecycle status. Explicit frontmatter wins."""
    explicit = (meta.get("status") or "").strip().lower()
    if explicit:
        return explicit
    if (meta.get("superseded_by") or "").strip():
        return "superseded"

    name = meta.get("name") or os.path.basename(filepath).replace(".md", "")
    text = _slug_text(name, meta.get("description"))

    if _has_term(text, "superseded"):
        return "superseded"
    if _has_term(text, "deprecated"):
        return "deprecated"
    if _has_term(text, "obsolete"):
        return "obsolete"
    if _has_term(text, "archive", "archived"):
        return "archived"
    if _has_term(text, "resolved", "fixed", "closed"):
        return "resolved"
    return "current"


def infer_area(meta, filepath, project):
    explicit = (meta.get("area") or "").strip()
    if explicit:
        return explicit
    return project or ""


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


def collect_files():
    """Collect all .md files from scan dirs."""
    files = []
    seen = set()
    for pattern in SCAN_DIRS:
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
    conn.commit()
    return conn


def index_file(conn, filepath, meta, body):
    """Index a single file's sections into the database."""
    project = detect_project(filepath)
    card_kind = infer_card_kind(meta, filepath)
    status = infer_status(meta, filepath)
    area = infer_area(meta, filepath, project)
    mtime = int(os.path.getmtime(filepath))
    sections = split_sections(body, filepath)

    conn.execute("DELETE FROM memory_chunks WHERE path = ?", (filepath,))

    for heading, content in sections:
        conn.execute(
            """INSERT INTO memory_chunks
               (path, project, name, type, evidence, source, confidence,
                last_verified, card_kind, status, area, supersedes,
                superseded_by, section_heading, content, description, mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                filepath, project, meta["name"], meta["type"],
                meta["evidence"], meta["source"], meta["confidence"],
                meta["last_verified"], card_kind, status, area,
                meta["supersedes"], meta["superseded_by"], heading,
                content, meta["description"], mtime,
            ),
        )

    conn.execute(
        "INSERT OR REPLACE INTO index_meta (path, mtime) VALUES (?, ?)",
        (filepath, mtime),
    )


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
        mtime = int(os.path.getmtime(filepath))

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
        except Exception as e:
            print(f"WARN: skip {filepath}: {e}", file=sys.stderr)

    removed = 0
    for old_path in existing_for_cleanup:
        if old_path not in current_paths:
            conn.execute("DELETE FROM memory_chunks WHERE path = ?", (old_path,))
            conn.execute("DELETE FROM index_meta WHERE path = ?", (old_path,))
            removed += 1

    conn.commit()
    if force_backfill:
        print("Lifecycle metadata backfill: reindexed existing memory files")
    return indexed, skipped, removed


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--incremental"
    db_path = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
        "~/.claude/memory-system/db/index.db"
    )

    t0 = time.time()
    files = collect_files()
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
