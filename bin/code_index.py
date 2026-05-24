#!/usr/bin/env python3
"""Eidetic v2.2 — Code-Aware Parsing via Tree-sitter.

Extracts functions, classes, and top-level constructs from code files,
indexes them as FTS5 chunks alongside memory files. Project-scoped:
only indexes code in CWD project, not all projects.

Supported: .py, .js, .ts, .sh
"""

import os
import sqlite3
import sys
import time

LANGUAGE_MAP = {}

def _init_languages():
    global LANGUAGE_MAP
    if LANGUAGE_MAP:
        return
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_python as tspython
        LANGUAGE_MAP[".py"] = Language(tspython.language())
    except ImportError:
        pass
    try:
        from tree_sitter import Language
        import tree_sitter_javascript as tsjs
        LANGUAGE_MAP[".js"] = Language(tsjs.language())
        LANGUAGE_MAP[".ts"] = Language(tsjs.language())
    except ImportError:
        pass
    try:
        from tree_sitter import Language
        import tree_sitter_bash as tsbash
        LANGUAGE_MAP[".sh"] = Language(tsbash.language())
    except ImportError:
        pass


EXTRACT_TYPES = {
    ".py": ["function_definition", "class_definition"],
    ".js": ["function_declaration", "class_declaration", "arrow_function", "method_definition"],
    ".ts": ["function_declaration", "class_declaration", "arrow_function", "method_definition"],
    ".sh": ["function_definition"],
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".kurdyuk-lite",
             "dist", "build", ".next", ".cache", "coverage"}
MAX_FILE_SIZE = 500_000


def ensure_agent_columns(conn):
    """Keep code indexing compatible with DBs created before v2.6."""
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
            conn.execute(statement)
    conn.commit()


def find_code_files(project_dir, extensions=None):
    if extensions is None:
        extensions = {".py", ".js", ".ts", ".sh"}
    files = []
    for root, dirs, fnames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in fnames:
            ext = os.path.splitext(fname)[1]
            if ext in extensions:
                path = os.path.join(root, fname)
                if os.path.getsize(path) < MAX_FILE_SIZE:
                    files.append(path)
    return files


def extract_entities(filepath, source_bytes, language):
    from tree_sitter import Parser

    ext = os.path.splitext(filepath)[1]
    target_types = EXTRACT_TYPES.get(ext, [])
    if not target_types:
        return []

    parser = Parser(language)
    tree = parser.parse(source_bytes)

    entities = []
    _walk(tree.root_node, target_types, source_bytes, filepath, entities, ext)
    return entities


def _walk(node, target_types, source, filepath, entities, ext):
    if node.type in target_types:
        name = _extract_name(node, ext)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        body = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if len(body) > 2000:
            body = body[:2000] + "..."

        entities.append({
            "name": name,
            "type": node.type,
            "start_line": start_line,
            "end_line": end_line,
            "body": body,
            "language": ext.lstrip("."),
        })

    for child in node.children:
        _walk(child, target_types, source, filepath, entities, ext)


def _extract_name(node, ext):
    for child in node.children:
        if child.type == "identifier" or child.type == "property_identifier":
            return child.text.decode("utf-8", errors="replace")
        if child.type == "name":
            return child.text.decode("utf-8", errors="replace")
    return f"anonymous_{node.start_point[0]}"


def index_code(conn, project_dir, project_slug=None):
    ensure_agent_columns(conn)
    _init_languages()
    if not LANGUAGE_MAP:
        print("No tree-sitter languages available", file=sys.stderr)
        return 0

    if project_slug is None:
        project_slug = project_dir.rstrip("/").replace("/", "-").lstrip("-")

    abs_dir = os.path.abspath(project_dir).rstrip("/") + "/"
    conn.execute(
        "DELETE FROM memory_chunks WHERE source = 'code-index' AND path LIKE ?",
        (abs_dir + "%",)
    )
    conn.commit()

    files = find_code_files(project_dir, set(LANGUAGE_MAP.keys()))
    total = 0
    t0 = time.time()

    for filepath in files:
        ext = os.path.splitext(filepath)[1]
        lang = LANGUAGE_MAP.get(ext)
        if not lang:
            continue

        try:
            with open(filepath, "rb") as f:
                source = f.read()
        except Exception:
            continue

        entities = extract_entities(filepath, source, lang)
        mtime = int(os.path.getmtime(filepath))

        for ent in entities:
            section = f"{ent['language']}:{ent['name']} L{ent['start_line']}-{ent['end_line']}"
            conn.execute("""
                INSERT INTO memory_chunks
                    (path, project, name, type, evidence, source,
                     card_kind, status, area, section_heading, content,
                     description, mtime)
                VALUES (?, ?, ?, 'code', 'observed', 'code-index',
                        'code', 'current', ?, ?, ?, ?, ?)
            """, (
                filepath,
                project_slug,
                ent["name"],
                project_slug,
                section,
                ent["body"],
                f"{ent['type']} in {os.path.basename(filepath)}",
                mtime,
            ))
            total += 1

    conn.commit()
    elapsed = time.time() - t0
    print(f"Indexed {total} code entities from {len(files)} files in {elapsed:.1f}s")
    return total


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: code_index.py <index.db> <project_dir> [--slug <slug>]")
        sys.exit(1)

    db_path = sys.argv[1]
    project_dir = sys.argv[2]
    slug = None
    if len(sys.argv) > 4 and sys.argv[3] == "--slug":
        slug = sys.argv[4]

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    index_code(conn, project_dir, slug)
    conn.close()
