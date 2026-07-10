#!/usr/bin/env python3
"""Eidetic promote — file a full synthesized answer back into the wiki as a card.

Karpathy's "LLM Wiki": "good answers can be filed back into the wiki as new
pages" so explorations compound instead of dying in chat. The Stop-hook +
compound.py already capture one-line `Decision:/Knowledge:` signals; THIS is the
deliberate, mid-session path for a rich multi-paragraph answer → one typed page,
with search-before-write dedup so a re-promote updates the page (it does not
duplicate it).

Reuses compound.py (FTS search, candidate gate, project memory-dir resolution)
and oplog.py (timeline). Zero-dep, stdlib only.

  echo "the answer body" | remember.py "Eidetic vs claude-mem: 6 unique moats" \\
      --kind synthesis --evidence observed --source agent-extracted

Dedup tiers (conservative — never merges a full page into an unrelated card):
  1. a card file with the same slug already exists  -> append an Update section
  2. FTS finds a same-slug card elsewhere           -> append an Update section
  3. otherwise                                       -> new typed card + Related links
"""

import argparse
import glob
import hashlib
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compound  # noqa: E402  (FTS search + candidate gate + dir resolution)
import oplog  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")

KNOWN_KINDS = {
    "synthesis", "concept", "entity", "research", "finding",
    "decision", "reference", "rule", "status", "todo", "bug", "handoff",
}
# card_kind -> frontmatter type (the harness's 4 buckets)
KIND_TO_TYPE = {
    "concept": "reference", "entity": "reference", "reference": "reference",
    "rule": "feedback",
}


# FR-7 (M3 v3): RU→Latin transliteration BEFORE the ASCII strip. An
# all-Cyrillic title used to collapse to empty → hash fallback
# (`synthesis-note-<hash>`), so paraphrased RU recall queries produced
# unreadable names and dodged same-slug dedup. Deterministic map ⇒ re-file
# idempotence holds (same title → same slug); pure-ASCII input is
# byte-identical to the old behavior. Non-RU non-ASCII (e.g. CJK) still
# falls through to the hash (distinct titles never collide into one card).
_RU2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text, maxlen=70):
    s = (text or "").lower()
    s = "".join(_RU2LAT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:maxlen].rstrip("-")
    if not s:
        # A title with no transliterable alphanumerics collapses to empty —
        # derive a stable slug from a hash so distinct non-ASCII titles get
        # distinct files instead of all merging into one "note" card.
        return "note-" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]
    return s


def target_slug(title, kind):
    base = slugify(title)
    if kind in KNOWN_KINDS and not base.startswith(kind + "-"):
        return f"{kind}-{base}"
    return base


def _atomic_write(path, content):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    # Preserve an existing card's permissions; mkstemp creates 0600, so without
    # this an append_update would silently tighten a normal 0644 memory file.
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644  # new card: world-readable like a normal memory file
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_update(path, title, body):
    """Compound into an existing page: append a dated Update section. Idempotent
    on identical body (skips if this exact body block is already present)."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    block = f"\n\n## Update {TODAY} — {title}\n\n{body.rstrip()}\n"
    if body.strip() and body.strip() in content:
        return False  # already filed, no-op
    _atomic_write(path, content.rstrip() + block)
    return True


def find_same_slug_card(memory_dir, slug):
    """Same-slug card anywhere under THIS project's memory dir.

    Filesystem-scoped on purpose: a project-bounded glob never crosses into
    another project's memory (the global FTS index would), and it is exact and
    reliable — no FTS `limit`/keyword-length lossiness that could miss the card
    and create a duplicate.
    """
    matches = sorted(glob.glob(os.path.join(memory_dir, "**", f"{slug}.md"), recursive=True))
    return matches[0] if matches else None


def related_links(conn, title, body, limit=3):
    """Top compound-candidate neighbours, as [[wikilink]] names (compounding)."""
    if conn is None:
        return []
    kw = compound.extract_keywords(f"{title} {body}")
    seen, links = set(), []
    for path, name, _heading, _content, _rank in compound.search_fts5(conn, kw, limit=limit + 3):
        if not compound.is_compound_candidate(path):
            continue
        stem = name or (os.path.basename(path)[:-3] if path.endswith(".md") else os.path.basename(path))
        if stem and stem not in seen:
            seen.add(stem)
            links.append(stem)
        if len(links) >= limit:
            break
    return links


def build_card(title, body, kind, evidence, source, ftype, related):
    fm = [
        "---",
        f"name: {target_slug(title, kind)}",
        f'description: "{title.replace(chr(34), chr(39))}"',
        f"type: {ftype}",
        f"card_kind: {kind}",
        f"evidence: {evidence}",
        f"source: {source}",
        f"last_verified: {TODAY}",
        "---",
        "",
        f"# {title}",
        "",
        body.rstrip(),
        "",
    ]
    if related:
        fm.append("## Related")
        fm.append("")
        fm.extend(f"- [[{r}]]" for r in related)
        fm.append("")
    return "\n".join(fm)


def promote(title, body, kind="synthesis", evidence="observed",
            source="agent-extracted", ftype=None, cwd=None, link=True, update_path=None):
    cwd = cwd or os.getcwd()
    kind = (kind or "synthesis").strip().lower()
    ftype = ftype or KIND_TO_TYPE.get(kind, "project")
    body = (body or "").strip()
    if not body:
        raise ValueError("promote: empty body (provide answer text on stdin or --body)")

    conn = None
    if os.path.exists(compound.DB_PATH):
        conn = sqlite3.connect(compound.DB_PATH)
        conn.execute("PRAGMA busy_timeout=5000")

    slug = target_slug(title, kind)
    memory_dir = compound.resolve_memory_dir(cwd)
    path = os.path.join(memory_dir, f"{slug}.md")
    action = "new"

    try:
        # Resolve the write target (dedup), scoped to THIS project's memory dir.
        if update_path:
            existing = os.path.expanduser(update_path)
            if not compound.is_compound_candidate(existing):
                raise ValueError(
                    f"--update target is not a memory card (no /memory/ in path): {existing}")
        elif os.path.exists(path):
            existing = path
        else:
            existing = find_same_slug_card(memory_dir, slug)

        # Never let an agent-extracted promotion mutate a user-validated
        # feedback/profile card (mirrors compound.py's protected-type guard).
        if existing and os.path.exists(existing) and \
                compound._get_file_type(existing) in ("feedback", "user"):
            if update_path:
                raise ValueError(
                    f"refusing to append into protected (feedback/user) card: {existing}")
            existing = None  # fall through to a fresh card, leaving the rule intact

        if existing and os.path.exists(existing):
            changed = append_update(existing, title, body)
            path, action = existing, ("updated" if changed else "noop")
        else:
            related = related_links(conn, title, body) if link else []
            target = path
            if os.path.exists(target):
                # The slug path is occupied by a card we won't merge into (e.g. a
                # protected feedback/user rule the guard above nulled) — relocate
                # so a new promotion never clobbers it.
                suffix = hashlib.sha1((title + "\0" + body).encode("utf-8")).hexdigest()[:8]
                target = os.path.join(memory_dir, f"{slug}-{suffix}.md")
            _atomic_write(target, build_card(title, body, kind, evidence, source, ftype, related))
            path, action = target, "new"
    finally:
        if conn:
            conn.close()

    # Best-effort: the durable card write already succeeded, so a log failure
    # must not surface as a promote failure.
    try:
        oplog.append_op("promote", title, project=cwd, detail=f"{action} {kind}", count=1)
    except Exception:
        pass
    return path, action


def main(argv=None):
    ap = argparse.ArgumentParser(description="Promote a synthesized answer to a typed memory card.")
    ap.add_argument("title", help="one-line title for the card")
    ap.add_argument("--kind", default="synthesis", help="card_kind (synthesis/concept/entity/research/finding/...)")
    ap.add_argument("--evidence", default="observed", help="evidence tier (validated/observed/hypothesis)")
    ap.add_argument("--source", default="agent-extracted", help="provenance (user-explicit/agent-extracted/...)")
    ap.add_argument("--type", dest="ftype", default=None, help="frontmatter type override (project/reference/feedback/user)")
    ap.add_argument("--project", dest="cwd", default=None, help="cwd whose project memory dir to write into")
    ap.add_argument("--body", default=None, help="card body (default: read stdin)")
    ap.add_argument("--update", dest="update_path", default=None, help="force-append to this existing card path")
    ap.add_argument("--no-link", dest="link", action="store_false", help="do not add Related wikilinks")
    args = ap.parse_args(argv)

    body = args.body if args.body is not None else sys.stdin.read()
    path, action = promote(
        args.title, body, kind=args.kind, evidence=args.evidence, source=args.source,
        ftype=args.ftype, cwd=args.cwd, link=args.link, update_path=args.update_path,
    )
    print(f"{action}: {path}", file=sys.stderr)
    print(path)


if __name__ == "__main__":
    main()
