#!/usr/bin/env python3
"""AI Memory System — Serendipity Links (Zettelkasten).

Given a search query, finds unexpected cross-project connections
that the user didn't ask for but might find valuable.

"The slip-box is designed to surprise you." — Luhmann

How it works:
1. Run normal FTS5 search for the query
2. For each top result, extract its [[wikilinks]] and key terms
3. Search FTS5 for those terms, EXCLUDING the original query's project
4. Return 1-3 "surprise" results from other projects

This surfaces connections like:
"You're working on key rotation → btw, there's a rate-limit finding
 in another project that connects."
"""

import os
import re
import sqlite3
import sys
from collections import Counter

def default_memory_system():
    installed_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if os.path.exists(os.path.join(installed_root, ".installed.json")):
        return installed_root
    return os.path.expanduser("~/.claude/memory-system")


MEMORY_SYSTEM = os.path.expanduser(
    os.environ.get("EIDETIC_MEMORY_SYSTEM") or default_memory_system()
)
DB_PATH = os.path.join(MEMORY_SYSTEM, "db", "index.db")


def extract_wikilinks(content):
    return re.findall(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', content)


def extract_key_terms(text, min_len=5, max_terms=8):
    words = re.findall(r'\b[a-zA-Z_-]{5,}\b', text.lower())
    stopwords = {
        "about", "after", "based", "before", "between", "called",
        "current", "default", "every", "example", "following",
        "found", "given", "having", "include", "should", "their",
        "these", "those", "through", "using", "which", "would",
    }
    filtered = [w for w in words if w not in stopwords]
    counts = Counter(filtered)
    return [w for w, _ in counts.most_common(max_terms)]


def search_fts5(conn, query, limit=5, exclude_project=None):
    sanitized = re.sub(r'[*()\[\]{}^~:+\-]', ' ', query)
    sanitized = sanitized.replace('"', '')
    words = [w for w in sanitized.split() if len(w) >= 3 and w.upper() not in ("AND", "OR", "NOT", "NEAR")]
    if not words:
        return []
    fts_query = " OR ".join(words[:4])

    sql = """
        SELECT c.path, c.name, c.project, c.section_heading, c.content,
               c.type, memory_fts.rank
        FROM memory_fts
        JOIN memory_chunks c ON memory_fts.rowid = c.id
        WHERE memory_fts MATCH ?
    """
    params = [fts_query]

    if exclude_project:
        sql += " AND (c.project IS NULL OR c.project != ?)"
        params.append(exclude_project)

    sql += " ORDER BY memory_fts.rank LIMIT ?"
    params.append(limit)

    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def find_serendipity(conn, query, current_project=None, max_surprises=3):
    primary = search_fts5(conn, query, limit=5)
    if not primary:
        return []

    seed_terms = set()
    seed_links = set()
    seed_names = set()

    for path, name, project, heading, content, typ, rank in primary:
        seed_links.update(extract_wikilinks(content))
        seed_terms.update(extract_key_terms(content))
        if name:
            seed_names.add(name)
            for part in name.replace("-", " ").replace("_", " ").split():
                if len(part) >= 4:
                    seed_terms.add(part.lower())

    if current_project is None and primary:
        current_project = primary[0][2]

    surprises = []
    seen_paths = {r[0] for r in primary}

    cross_project = []
    same_project = []

    for link in seed_links:
        results = search_fts5(conn, link, limit=5)
        for r in results:
            if r[0] not in seen_paths:
                seen_paths.add(r[0])
                if r[2] != current_project:
                    cross_project.append(r)
                else:
                    same_project.append(r)

    if len(cross_project) < max_surprises:
        term_query = " ".join(list(seed_terms)[:5])
        if term_query.strip():
            results = search_fts5(conn, term_query, limit=10)
            for r in results:
                if r[0] not in seen_paths:
                    seen_paths.add(r[0])
                    if r[2] != current_project:
                        cross_project.append(r)
                    elif len(same_project) < max_surprises:
                        same_project.append(r)

    surprises = cross_project[:max_surprises]
    if len(surprises) < max_surprises:
        surprises.extend(same_project[:max_surprises - len(surprises)])

    return surprises[:max_surprises]


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    if not query:
        print("Usage: serendipity.py <query>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[2] if len(sys.argv) > 2 else DB_PATH
    if not os.path.exists(db_path):
        print("No index found.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")

    cwd = os.getcwd()
    project = None
    sanitized_cwd = "-" + cwd.replace("/", "-").lstrip("-")
    for row in conn.execute("SELECT DISTINCT project FROM memory_chunks WHERE project IS NOT NULL"):
        if sanitized_cwd.endswith(row[0]) or row[0].endswith(cwd.split("/")[-1]):
            project = row[0]
            break

    surprises = find_serendipity(conn, query, current_project=project)

    if not surprises:
        print("No serendipity links found.")
        return

    print(f"Serendipity: {len(surprises)} unexpected connection(s)\n")
    for path, name, project, heading, content, typ, rank in surprises:
        short = path.replace(os.path.expanduser("~"), "~")
        snippet = content[:200].replace("\n", " ").strip()
        proj_label = project.split("-")[-1] if project else "cross-project"
        print(f"  [{proj_label}] {name or heading}")
        print(f"  {short}")
        print(f"  {snippet}...")
        print()

    conn.close()


if __name__ == "__main__":
    main()
