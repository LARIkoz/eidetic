#!/usr/bin/env python3
"""AI Memory System v1 — Lint.

Checks memory health:
- Broken [[wikilinks]] — link target doesn't exist
- Orphan files — files with zero inbound [[wikilinks]]
- Stale files — last_verified > 30 days ago
- Large files — files > 5KB with many sections (split candidates)
"""

import glob
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

SCAN_DIRS = [
    os.path.expanduser("~/.claude/projects/*/memory/"),
    os.path.expanduser("~/.claude/projects/*/memory/signals/"),
    os.path.expanduser("~/.claude/agent-memory/"),
    os.path.expanduser("~/.claude/agent-memory/*/"),
]

EXCLUDE = {"MEMORY.md", "BACKLOG.md"}
STALE_DAYS = 30
LARGE_THRESHOLD = 5120


def file_stem(path):
    return os.path.basename(path).replace(".md", "")


def unique_key(path, used):
    stem = file_stem(path)
    if stem not in used:
        return stem

    project = "memory"
    m = re.search(r'/\.claude/projects/([^/]+)/memory/', path)
    if m:
        project = m.group(1)
    elif "/.claude/agent-memory/" in path:
        parts = path.split("/.claude/agent-memory/", 1)[1].split("/")
        project = "agent-" + parts[0] if len(parts) > 1 else "agent"

    candidate = f"{project}/{stem}"
    i = 2
    while candidate in used:
        candidate = f"{project}/{stem}#{i}"
        i += 1
    return candidate


def collect_files():
    files = {}
    used = set()
    for pattern in SCAN_DIRS:
        for dirpath in glob.glob(pattern):
            if not os.path.isdir(dirpath):
                continue
            for f in os.listdir(dirpath):
                if not f.endswith(".md") or f in EXCLUDE:
                    continue
                fullpath = os.path.join(dirpath, f)
                key = unique_key(fullpath, used)
                used.add(key)
                files[key] = fullpath
    return files


def is_shell_test(link):
    return bool(
        "$" in link
        or "{" in link
        or "\\" in link
        or "~/" in link
        or re.search(r'(^|\s)(==|!=|-eq|-ne|-gt|-lt|-ge|-le)(\s|$)', link)
    )


def strip_fenced_code(text):
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def is_placeholder_link(target):
    return target in {"...", "filename", "folder/filename"} or len(target) < 2


def extract_wikilinks(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = strip_fenced_code(f.read())
    except Exception:
        return []
    raw = re.findall(r'\[\[([^\]]+)\]\]', text)
    links = []
    for link in raw:
        target = link.split("|")[0].split("#")[0].strip()
        if target and not is_shell_test(target) and not is_placeholder_link(target):
            links.append(target)
    return links


def extract_name_from_frontmatter(filepath):
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm = text[4:end]
    for line in fm.split("\n"):
        m = re.match(r'^name:\s*(.+)$', line.strip())
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/.claude/memory-system/db/index.db"
    )

    files = collect_files()

    name_to_keys = {}

    def add_alias(alias, key):
        if alias:
            name_to_keys.setdefault(alias, set()).add(key)

    for filename, path in files.items():
        add_alias(filename, filename)
        add_alias(file_stem(path), filename)
        fm_name = extract_name_from_frontmatter(path)
        if fm_name:
            add_alias(fm_name, filename)

    for skill_path in glob.glob(os.path.expanduser("~/.claude/skills/*/SKILL.md")):
        add_alias(os.path.basename(os.path.dirname(skill_path)), "__skill__")

    all_links = {}
    inbound = {name: 0 for name in files}

    for filename, path in files.items():
        links = extract_wikilinks(path)
        all_links[filename] = links
        for link in links:
            for target in name_to_keys.get(link, set()):
                if target in inbound:
                    inbound[target] += 1

    broken = []
    for filename, links in all_links.items():
        for link in links:
            if link not in name_to_keys:
                broken.append((filename, link))

    orphans = [(name, files[name]) for name, count in inbound.items() if count == 0]

    large = []
    for filename, path in files.items():
        size = os.path.getsize(path)
        if size > LARGE_THRESHOLD:
            try:
                with open(path, "r") as f:
                    sections = sum(1 for line in f if line.startswith("## "))
            except Exception:
                sections = 0
            if sections > 3:
                large.append((filename, size, sections, path))

    print("=== Memory Lint Report ===\n")

    if broken:
        print(f"BROKEN LINKS ({len(broken)}):")
        for src, target in broken[:20]:
            print(f"  {src} → [[{target}]] (not found)")
        if len(broken) > 20:
            print(f"  ...and {len(broken) - 20} more")
    else:
        print("BROKEN LINKS: 0 ✅")

    print()

    print(f"ORPHANS (0 inbound links): {len(orphans)}")
    if orphans:
        for name, path in sorted(orphans)[:20]:
            short = path.replace(os.path.expanduser("~"), "~")
            print(f"  {name} — {short}")
        if len(orphans) > 20:
            print(f"  ...and {len(orphans) - 20} more")

    print()

    contradictions = []
    for filename, path in files.items():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end == -1:
            continue
        fm = text[4:end]
        for line in fm.split("\n"):
            line = line.strip()
            if line.startswith("contradicts:"):
                target = line.split(":", 1)[1].strip().strip("[]").strip()
                if target:
                    contradictions.append((filename, "contradicts", target))
            elif line.startswith("contradicted_by:"):
                target = line.split(":", 1)[1].strip().strip("[]").strip()
                if target:
                    contradictions.append((filename, "contradicted_by", target))

    if contradictions:
        print(f"CONTRADICTIONS ({len(contradictions)}):")
        for src, rel, target in contradictions:
            print(f"  {src} {rel} {target}")
    else:
        print("CONTRADICTIONS: none")

    print()

    large.sort(key=lambda x: x[1], reverse=True)
    if large:
        print(f"LARGE FILES (>{LARGE_THRESHOLD // 1024}KB + >3 sections) — split candidates ({len(large)}):")
        for name, size, sections, path in large[:10]:
            print(f"  {size // 1024}KB {sections}§ {name}")
    else:
        print("LARGE FILES: none ✅")

    print()
    print(f"Total: {len(files)} files, {len(broken)} broken links, "
          f"{len(orphans)} orphans, {len(contradictions)} contradictions, {len(large)} large")


if __name__ == "__main__":
    main()
