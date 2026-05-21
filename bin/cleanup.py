#!/usr/bin/env python3
"""AI Memory System v2 — Cleanup / DELETE mechanism (Karpathy).

Identifies stale memory candidates for archival. Does NOT delete —
moves to archive/ and generates a review report for human confirmation.

"Three operations: add, update, delete." — Karpathy
"""

import glob
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime

DB_PATH = os.path.expanduser("~/.claude/memory-system/db/index.db")
ARCHIVE_DIR = os.path.expanduser("~/.claude/memory-system/archive/")
STALE_DAYS = 90
MIN_SIZE = 100

SCAN_DIRS = [
    os.path.expanduser("~/.claude/projects/*/memory/"),
    os.path.expanduser("~/.claude/projects/*/memory/signals/"),
    os.path.expanduser("~/.claude/agent-memory/"),
    os.path.expanduser("~/.claude/agent-memory/*/"),
    os.path.expanduser("~/.claude/memory-system/signals/"),
]
EXCLUDE = {"MEMORY.md", "BACKLOG.md"}
PROTECTED_TYPES = {"feedback", "user"}


def collect_files():
    files = {}
    for pattern in SCAN_DIRS:
        for dirpath in glob.glob(pattern):
            if not os.path.isdir(dirpath):
                continue
            for f in os.listdir(dirpath):
                if f.endswith(".md") and f not in EXCLUDE and not f.endswith(".bak"):
                    files[f.replace(".md", "")] = os.path.join(dirpath, f)
    return files


def get_type(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(500)
    except Exception:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = text[4:end]
    for line in fm.split("\n"):
        m = re.match(r'^\s*type:\s*(.+)$', line.strip())
        if m:
            return m.group(1).strip()
    return None


def count_inbound(files):
    inbound = {name: 0 for name in files}
    for name, path in files.items():
        try:
            with open(path, "r", errors="replace") as f:
                text = f.read()
            for link in re.findall(r'\[\[([^\]]+)\]\]', text):
                if link in inbound:
                    inbound[link] += 1
        except Exception:
            pass
    return inbound


def find_candidates(files, inbound):
    now = time.time()
    candidates = []

    for name, path in files.items():
        ftype = get_type(path)
        if ftype in PROTECTED_TYPES:
            continue

        age_days = (now - os.path.getmtime(path)) / 86400
        size = os.path.getsize(path)
        links_in = inbound.get(name, 0)

        reasons = []
        score = 0

        if age_days > STALE_DAYS:
            reasons.append(f"stale ({int(age_days)}d)")
            score += 2
        elif age_days > 60:
            reasons.append(f"aging ({int(age_days)}d)")
            score += 1

        if links_in == 0:
            reasons.append("orphan")
            score += 1

        if size < MIN_SIZE:
            reasons.append(f"tiny ({size}b)")
            score += 1

        if score >= 3:
            candidates.append((score, name, path, reasons, age_days, size))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates


def generate_report(candidates):
    print(f"=== Cleanup Report — {datetime.now().strftime('%Y-%m-%d')} ===\n")
    print(f"Candidates for archival: {len(candidates)}\n")

    if not candidates:
        print("No candidates found. Corpus is healthy.")
        return

    for score, name, path, reasons, age, size in candidates[:30]:
        short = path.replace(os.path.expanduser("~"), "~")
        print(f"  [{score}] {name}")
        print(f"      {short} ({size // 1024}KB, {int(age)}d)")
        print(f"      Reasons: {', '.join(reasons)}")
        print()

    if len(candidates) > 30:
        print(f"  ...and {len(candidates) - 30} more\n")

    print(f"Total: {len(candidates)} candidates")
    print(f"\nTo archive: cleanup.py --archive")
    print(f"Archive dir: {ARCHIVE_DIR}")


def do_archive(candidates, max_items=10):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    archived = 0

    for score, name, path, reasons, age, size in candidates[:max_items]:
        dest = os.path.join(ARCHIVE_DIR, os.path.basename(path))
        if os.path.exists(dest):
            dest = os.path.join(ARCHIVE_DIR, f"{name}_{int(time.time())}.md")
        try:
            shutil.move(path, dest)
            archived += 1
            print(f"  Archived: {name} → {dest}")
        except Exception as e:
            print(f"  FAILED: {name}: {e}")

    print(f"\nArchived {archived}/{min(max_items, len(candidates))} files")
    print(f"Run index.sh --full to update index")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--report"
    max_items = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    files = collect_files()
    inbound = count_inbound(files)
    candidates = find_candidates(files, inbound)

    if mode == "--archive":
        if not candidates:
            print("No candidates to archive.")
            return
        print(f"Archiving top {max_items} candidates...\n")
        do_archive(candidates, max_items)
    else:
        generate_report(candidates)


if __name__ == "__main__":
    main()
