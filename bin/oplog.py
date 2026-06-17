#!/usr/bin/env python3
"""Eidetic op-log — a greppable, append-only timeline of memory operations.

Karpathy's "LLM Wiki" keeps a chronological `log.md` next to the index so the
maintainer (and the LLM) can see what happened and when. Eidetic spans many
project memory dirs, so this is ONE global log at `<memory-system>/log.md`,
each line tagged with the project. It is the human/agent-facing audit trail,
not a runtime index — nothing reads it back for search.

Greppable by design:
  grep '^## \\[' log.md            # whole timeline
  grep -A3 'promote' log.md        # every promote op with its detail
  grep '2026-06-18' log.md         # one day

Zero-dep (stdlib only). Importable (`append_op(...)`) and CLI:
  oplog.py <op> <title> [--project P] [--detail D] [--count N]
"""

import argparse
import fcntl
import os
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
LOG_PATH = os.path.join(MEMORY_SYSTEM, "log.md")

# Known operation verbs. Free-form is allowed, but these are the canonical set
# so the timeline stays greppable with a stable vocabulary.
KNOWN_OPS = {"ingest", "promote", "compound", "query", "lint", "index", "drift", "export"}


def _project_slug(project):
    """Turn a cwd/path into a short stable tag; pass-through for plain slugs."""
    if not project:
        return None
    p = project.rstrip("/")
    if "/" not in p:
        return p
    return os.path.basename(p) or p


def append_op(op, title, project=None, detail=None, count=None, log_path=None):
    """Append one operation entry to the op-log. Append-only, flock-guarded.

    Returns the log path written. Never raises on a busy lock — it blocks
    briefly (LOCK_EX) because entries are tiny; a missing dir is created.
    """
    op = (op or "op").strip()
    title = (title or "").strip().replace("\n", " ")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    target = log_path or LOG_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)

    lines = [f"## [{stamp}] {op} — {title}" if title else f"## [{stamp}] {op}"]
    slug = _project_slug(project)
    if slug:
        lines.append(f"- project: {slug}")
    if count is not None:
        lines.append(f"- count: {count}")
    if detail:
        lines.append(f"- detail: {str(detail).replace(chr(10), ' ')}")
    entry = "\n".join(lines) + "\n\n"

    # Append under flock so concurrent writers (Stop hook + a manual promote)
    # never interleave. os.write is unbuffered and we fsync BEFORE releasing the
    # lock, so the lock actually covers the on-disk append — a buffered file
    # object would flush on close, AFTER the lock is already gone.
    data = entry.encode("utf-8")
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return target


def main(argv=None):
    ap = argparse.ArgumentParser(description="Append an entry to the Eidetic op-log.")
    ap.add_argument("op", help="operation verb (ingest/promote/compound/query/lint/index/drift/export)")
    ap.add_argument("title", help="short title for the entry")
    ap.add_argument("--project", default=None, help="project path or slug")
    ap.add_argument("--detail", default=None, help="optional one-line detail")
    ap.add_argument("--count", default=None, help="optional count (e.g. cards touched)")
    args = ap.parse_args(argv)
    path = append_op(args.op, args.title, project=args.project, detail=args.detail, count=args.count)
    print(path, file=sys.stderr)


if __name__ == "__main__":
    main()
