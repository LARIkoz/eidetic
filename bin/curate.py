#!/usr/bin/env python3
"""Eidetic lifecycle curation — READ-ONLY candidate finders (never mutates).

Promoting or archiving a memory is owner-gated (T1): this tool only PROPOSES,
joining signals the human can't easily join by hand, and prints the exact
command to run. It writes nothing — not a memory file, not the drift DB.

  promote-candidates : cards search keeps PULLING (proven useful by real recall)
                       that are NOT yet type:feedback/user, so they reach you
                       only on demand. Promoting one to feedback moves it to the
                       PUSH channel — injected every session instead of waited
                       for. Prints the `remember.py --update ... --type feedback`
                       line. Review each: only GENERAL rules belong in feedback,
                       not project-specific findings.

  archive-candidates : cards drift_check already flagged age_stale AND search has
                       never pulled AND that are not protected (feedback/user
                       never qualify — the age thresholds already exempt them).
                       These are cold weight in the always-loaded tier. Archive =
                       move to MEMORY-ARCHIVE.md (still indexed for PULL, just not
                       auto-loaded). The move stays manual; this only lists them.

Reuses usage_stats.compute (pull telemetry + per-card type) and the drift_state.db
that drift_check already maintains. Zero-dep: stdlib + sqlite3.
"""

import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_stats  # noqa: E402  (stdlib-only)

# Cards already delivered by injection — never a pull-promote target, and never
# an archive target (they are the push channel). Mirrors usage_stats.PUSH_TYPES.
PROTECTED_TYPES = usage_stats.PUSH_TYPES


def _by_path_pull(c):
    """Fold surfaced (path, section) chunks up to the file: a card's promote
    signal is its WHOLE file's pull history, not one section."""
    agg, idx = c["agg"], c["idx"]
    by_path = {}
    for k, v in agg.items():
        if v["count"] <= 0 or k not in idx:
            continue
        path = usage_stats._unkey(k)[0]
        d = by_path.setdefault(path, {"surf": 0, "qh": set(), "name": idx[k][0],
                                      "kind": idx[k][1]})
        d["surf"] += v["count"]
        d["qh"] |= v["qhashes"]
    return by_path


def promote_candidates(db_path, min_hits=2):
    c = usage_stats.compute(db_path)
    types = c["types"]
    out = []
    for path, d in _by_path_pull(c).items():
        if types.get(path, "") in PROTECTED_TYPES:
            continue                       # already push-delivered
        if len(d["qh"]) < min_hits:
            continue                       # not pulled by enough distinct searches
        out.append({
            "path": path, "name": d["name"] or os.path.basename(path),
            "type": types.get(path, "") or "?", "kind": d["kind"] or "?",
            "surfacings": d["surf"], "distinct_queries": len(d["qh"]),
        })
    out.sort(key=lambda r: (r["distinct_queries"], r["surfacings"]), reverse=True)
    return out


def archive_candidates(db_path):
    """age_stale (per drift_check) AND never pulled AND not protected."""
    drift_db = os.path.join(os.path.dirname(os.path.abspath(db_path)), "drift_state.db")
    stale = {}
    if os.path.exists(drift_db):
        try:
            dc = sqlite3.connect(f"file:{drift_db}?mode=ro", uri=True)
            try:
                for path, detail, seen in dc.execute(
                    "SELECT path, detail, first_seen FROM drift_findings "
                    "WHERE drift_type='age_stale' AND resolved_at IS NULL"
                ):
                    stale[path] = (detail or "", int(seen or 1))
            finally:
                dc.close()
        except sqlite3.Error:
            pass

    c = usage_stats.compute(db_path)
    pulled_paths = {usage_stats._unkey(k)[0]
                    for k, v in c["agg"].items() if v["count"] > 0}
    types = c["types"]

    out = []
    for path, (detail, seen) in stale.items():
        if path in pulled_paths:
            continue                       # search still pulls it — keep
        if types.get(path, "") in PROTECTED_TYPES:
            continue                       # protected (defensive; age already exempts)
        out.append({
            "path": path, "type": types.get(path, "") or "?",
            "stale": detail, "times_flagged": seen,
        })
    out.sort(key=lambda r: r["times_flagged"], reverse=True)
    return out


def _set_status_archived(path, value="archived"):
    """Set top-level frontmatter status (index_impl reads `status:`/`state:`,
    explicit wins). Atomic temp+rename. Returns 'archived' | 'already' | 'skip'
    (no frontmatter / unreadable). Reversible: set status back to 'current'."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "skip"
    if not text.startswith("---"):
        return "skip"                      # no frontmatter — don't guess, skip
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return "skip"
    key_re = re.compile(r"^(\s*)(status|state):\s*(.*)$")
    for i in range(1, close):
        m = key_re.match(lines[i])
        if m:
            if m.group(3).strip().lower() == value:
                return "already"           # idempotent
            lines[i] = f"{m.group(1)}{m.group(2)}: {value}"
            break
    else:
        lines.insert(close, f"status: {value}")
    tmp = path + ".curate-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)
    return "archived"


def _print_promote(rows, top):
    print(f"=== promote candidates ({len(rows)}) — search keeps pulling, not yet feedback (PULL -> PUSH) ===")
    if not rows:
        print("none — no non-feedback card has been pulled by enough distinct searches yet.")
        return
    print("review each: only GENERAL rules belong in feedback, not project findings.\n")
    print(f"  {'q':>2}  {'hits':>4}  {'type':9}  {'kind':9}  card")
    for r in rows[:top]:
        print(f"  {r['distinct_queries']:>2}  {r['surfacings']:>4}  {r['type']:9}  "
              f"{r['kind']:9}  {(r['name'] or '')[:48]}")
    if len(rows) > top:
        print(f"  ... (+{len(rows) - top} more — raise --top to see all)")
    print("\npromote one (owner-gated):")
    r = rows[0]
    print(f"  python3 bin/remember.py --update {r['path']} --type feedback")


def _print_archive(rows, top):
    print(f"=== archive candidates ({len(rows)}) — age_stale AND never pulled AND not protected ===")
    if not rows:
        print("none — every stale card is either still pulled, protected, or none are stale.")
        return
    print("archive = set frontmatter status:archived (drift_check stops flagging,\n"
          "search keeps the card). Reversible; --apply does it, plain run only lists.\n")
    print(f"  {'flagged':>7}  {'type':9}  {'stale':16}  path")
    for r in rows[:top]:
        print(f"  {r['times_flagged']:>7}  {r['type']:9}  {r['stale']:16}  "
              f"{r['path'].replace(os.path.expanduser('~'), '~')}")
    if len(rows) > top:
        print(f"  ... (+{len(rows) - top} more — raise --top to see all)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["promote", "archive"],
                    help="promote = pull->push candidates; archive = cold-storage candidates")
    ap.add_argument("--db", default=os.path.expanduser("~/.claude/memory-system/db/index.db"))
    ap.add_argument("--min-hits", type=int, default=2,
                    help="promote: min distinct searches that pulled the card (default 2)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--apply", action="store_true",
                    help="archive: set status:archived (dry-run unless --yes)")
    ap.add_argument("--yes", action="store_true",
                    help="archive --apply: actually write (else dry-run)")
    args = ap.parse_args(argv)

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"ERROR: index not found: {db}", file=sys.stderr)
        return 2

    if args.mode == "promote":
        _print_promote(promote_candidates(db, min_hits=args.min_hits), args.top)
        return 0

    rows = archive_candidates(db)
    if not args.apply:
        _print_archive(rows, args.top)             # plain run = list only
        return 0
    if not args.yes:
        print(f"DRY RUN — would set status:archived on {len(rows)} card(s); search keeps\n"
              f"them, drift stops flagging. Re-run with --apply --yes to write.\n")
        _print_archive(rows, args.top)
        return 0
    done = already = skipped = 0
    for r in rows:
        res = _set_status_archived(r["path"])
        done += res == "archived"
        already += res == "already"
        skipped += res == "skip"
        print(f"  {res:8} {r['path'].replace(os.path.expanduser('~'), '~')}")
    print(f"\narchived {done}, already {already}, skipped {skipped} (no frontmatter).")
    if done:
        print("re-index so drift skips them:  bash bin/index.sh --incremental")
        print("undo a card:  set its frontmatter status: back to current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
