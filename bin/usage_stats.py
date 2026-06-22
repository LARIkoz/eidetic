#!/usr/bin/env python3
"""Report which memory cards Eidetic actually surfaces (usage telemetry).

This measures the PULL channel ONLY — on-demand search recall. It is BLIND to
the push channel: type:feedback/user cards reach every session by INJECTION
(memory-context.md), so a feedback rule that "never surfaced" is delivered every
session, not dead. Read every number here as "via search", never "in total".

Reads usage.log (+ usage_rollup.json) written by usage.py, aggregates per card,
joins index.db for names/kinds/type, and reports honestly:
  - TOP cards by surfacings (what search pulls the most)
  - WINDOW: the time span + how many distinct searches the numbers cover
  - never-pulled cards split into push-delivered (feedback/user — injected, NOT
    dead) vs pull-cold (long-tail; archive candidate only if also stale+unlinked)
  - COVERAGE %: distinct/indexed is flow-over-stock — with N searches it is
    structurally <= surfacings/indexed; meaningless until hundreds of searches.

Usage:
  usage_stats.py [--db PATH] [--top N] [--json]      # full report
  usage_stats.py --summary [--db PATH]               # one-line (doctor)
  usage_stats.py --rollup  [--db PATH]               # compact usage.log -> usage_rollup.json
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict


def _paths(db_path):
    d = os.path.dirname(os.path.abspath(db_path))
    return os.path.join(d, "usage.log"), os.path.join(d, "usage_rollup.json")


def _key(path, section):
    return (path or "") + "\x00" + (section or "")


def _unkey(k):
    path, _, section = k.partition("\x00")
    return path, section


def _empty():
    return {"count": 0, "first": None, "last": None,
            "best_rank": None, "sum_rank": 0, "n_rank": 0, "qhashes": set()}


def _fold_rank(e, rank):
    if isinstance(rank, int):
        e["best_rank"] = rank if e["best_rank"] is None else min(e["best_rank"], rank)
        e["sum_rank"] += rank
        e["n_rank"] += 1


def _fold_ts(e, ts):
    if ts:
        if e["first"] is None or ts < e["first"]:
            e["first"] = ts
        if e["last"] is None or ts > e["last"]:
            e["last"] = ts


def aggregate(rollup_path, log_path):
    """Fold the compacted rollup then the live log into one per-card aggregate."""
    agg = defaultdict(_empty)
    try:
        with open(rollup_path, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                e = agg[k]
                e["count"] += int(v.get("count", 0))
                _fold_ts(e, v.get("first"))
                _fold_ts(e, v.get("last"))
                if v.get("best_rank") is not None:
                    e["best_rank"] = (v["best_rank"] if e["best_rank"] is None
                                      else min(e["best_rank"], v["best_rank"]))
                e["sum_rank"] += int(v.get("sum_rank", 0))
                e["n_rank"] += int(v.get("n_rank", 0))
                e["qhashes"].update(v.get("qhashes", []))
    except (OSError, ValueError):
        pass
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                e = agg[_key(ev.get("path", ""), ev.get("section", ""))]
                e["count"] += 1
                _fold_ts(e, ev.get("ts"))
                _fold_rank(e, ev.get("rank"))
                if ev.get("qh"):
                    e["qhashes"].add(ev["qh"])
    except OSError:
        pass
    return agg


def _index_cards(db_path):
    """Every indexed card as (path, section) -> (name, card_kind)."""
    out = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            "SELECT DISTINCT path, COALESCE(section_heading,'') AS section, "
            "name, COALESCE(card_kind,'') AS card_kind FROM memory_chunks"
        ):
            out[_key(r["path"], r["section"])] = (r["name"] or "", r["card_kind"])
        conn.close()
    except sqlite3.Error:
        pass
    return out


# Cards reached every session by injection (memory-context.md) — they do NOT need
# to be pulled by search, so "never surfaced" is delivery, not death.
PUSH_TYPES = {"feedback", "user"}


def _types_by_path(db_path):
    """path -> frontmatter `type` (for the push-delivered vs pull-cold split).

    Defensive: an older or minimal index (e.g. a test fixture) may lack the
    `type` column — then every card reads as '' and the split degrades to
    all-pull-cold rather than crashing.
    """
    out = {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            for path, typ in conn.execute(
                "SELECT DISTINCT path, COALESCE(type,'') FROM memory_chunks"
            ):
                out[path] = typ or ""
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return out


def compute(db_path):
    log_path, rollup_path = _paths(db_path)
    agg = aggregate(rollup_path, log_path)
    idx = _index_cards(db_path)
    surfaced = {k for k, v in agg.items() if v["count"] > 0}
    live_surfaced = surfaced & set(idx)          # surfaced AND still indexed
    dead = [k for k in idx if k not in surfaced]  # indexed but never surfaced
    total_surfacings = sum(v["count"] for v in agg.values())

    # The real denominator: a "search" logs up to TOP_K rows under ONE query
    # hash, so distinct qh — NOT surfacings — is how many searches happened.
    # The window says over what span; both keep coverage% from being read as
    # "all time" when it is really "the last N searches".
    all_qh = set()
    first_ts = last_ts = None
    for v in agg.values():
        all_qh |= v["qhashes"]
        if v["first"] and (first_ts is None or v["first"] < first_ts):
            first_ts = v["first"]
        if v["last"] and (last_ts is None or v["last"] > last_ts):
            last_ts = v["last"]

    # Split never-pulled cards: push-delivered (injected every session, pull N/A)
    # vs pull-cold (only reachable by search, not yet hit).
    types = _types_by_path(db_path)
    push_dead = pull_dead = 0
    for k in dead:
        path, _ = _unkey(k)
        if types.get(path, "") in PUSH_TYPES:
            push_dead += 1
        else:
            pull_dead += 1

    return {
        "total_indexed": len(idx),
        "total_surfacings": total_surfacings,
        "distinct_surfaced": len(live_surfaced),
        "coverage_pct": round(len(live_surfaced) / len(idx) * 100, 1) if idx else 0.0,
        "dead_count": len(dead),
        "n_searches": len(all_qh),
        "window": (first_ts, last_ts),
        "push_delivered_dead": push_dead,
        "pull_cold_dead": pull_dead,
        "agg": agg, "idx": idx, "dead": dead, "types": types,
    }


def _name_of(idx, k):
    # A "card" is a (path, section) chunk, so the same file appears once per section.
    # Always show the section to keep those rows distinct in the report.
    name = idx.get(k, ("", ""))[0]
    path, section = _unkey(k)
    base = name or os.path.basename(path) or path
    return f"{base}{(' § ' + section) if section else ''}"


def report(db_path, top=15, json_out=False):
    c = compute(db_path)
    agg, idx = c["agg"], c["idx"]
    ranked = sorted(((k, v) for k, v in agg.items() if k in idx),
                    key=lambda kv: kv[1]["count"], reverse=True)[:top]

    if json_out:
        payload = {
            "total_indexed": c["total_indexed"],
            "total_surfacings": c["total_surfacings"],
            "distinct_surfaced": c["distinct_surfaced"],
            "coverage_pct": c["coverage_pct"],
            "dead_count": c["dead_count"],
            "n_searches": c["n_searches"],
            "window": c["window"],
            "push_delivered_dead": c["push_delivered_dead"],
            "pull_cold_dead": c["pull_cold_dead"],
            "top": [{
                "name": _name_of(idx, k), "kind": idx.get(k, ("", ""))[1],
                "surfacings": v["count"], "last": v["last"], "best_rank": v["best_rank"],
                "avg_rank": round(v["sum_rank"] / v["n_rank"], 1) if v["n_rank"] else None,
                "distinct_queries": len(v["qhashes"]),
            } for k, v in ranked],
            "pull_cold_sample": [_name_of(idx, k) for k in c["dead"]
                                 if c["types"].get(_unkey(k)[0], "") not in PUSH_TYPES][:20],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("=== Eidetic usage — PULL channel only (on-demand search recall) ===")
    if c["total_surfacings"] == 0:
        print(f"indexed cards: {c['total_indexed']}")
        print("\nNo searches logged yet — this reports search recall only; "
              "feedback/user cards still reach every session by injection.")
        return 0
    w0, w1 = c["window"]
    cap = (round(min(c["total_surfacings"], c["total_indexed"]) / c["total_indexed"] * 100, 1)
           if c["total_indexed"] else 0.0)
    fresh = round(c["distinct_surfaced"] / c["total_surfacings"] * 100) if c["total_surfacings"] else 0
    print(f"window : {(w0 or '?')[:16]} -> {(w1 or '?')[:16]}   "
          f"({c['n_searches']} searches, {c['total_surfacings']} surfacings)")
    print(f"pulled : {c['distinct_surfaced']} distinct cards   "
          f"({fresh}% of surfacings were a fresh card — diverse, not monopolized)")
    print("\nnote: search only. type:feedback/user cards reach every session by INJECTION")
    print(f"      (push) — a 'never pulled' rule is delivered, not dead. coverage "
          f"{c['coverage_pct']}% = distinct/indexed is")
    print(f"      flow/stock: at {c['n_searches']} searches it is structurally <= {cap}%; "
          f"meaningless until hundreds.")
    print(f"\nnever pulled: {c['dead_count']}")
    print(f"  +- push-delivered (feedback/user): {c['push_delivered_dead']:>5}   "
          f"<- injected EVERY session; pull N/A, NOT dead")
    print(f"  +- pull-only, not yet hit        : {c['pull_cold_dead']:>5}   "
          f"<- long-tail; archive candidate only if also stale+unlinked")
    print(f"\nTop {len(ranked)} surfaced cards:")
    print(f"  {'#':>3}  {'hits':>4}  {'avg_rk':>6}  {'last':16}  card")
    for i, (k, v) in enumerate(ranked, 1):
        avg = round(v["sum_rank"] / v["n_rank"], 1) if v["n_rank"] else "-"
        kind = idx.get(k, ("", ""))[1] or "?"
        print(f"  {i:>3}  {v['count']:>4}  {str(avg):>6}  {(v['last'] or '')[:16]:16}  "
              f"[{kind}] {_name_of(idx, k)[:60]}")
    pull_cold = [k for k in c["dead"] if c["types"].get(_unkey(k)[0], "") not in PUSH_TYPES]
    if pull_cold:
        print(f"\nColdest pull-only cards (never hit by search) — {len(pull_cold)} total, sample:")
        for k in pull_cold[:10]:
            print(f"    [{idx.get(k, ('', ''))[1] or '?'}] {_name_of(idx, k)[:70]}")
    return 0


def summary(db_path):
    """One line for the doctor."""
    c = compute(db_path)
    print(f"surfacings={c['total_surfacings']} distinct={c['distinct_surfaced']} "
          f"coverage={c['coverage_pct']}% dead={c['dead_count']} indexed={c['total_indexed']}")
    return 0


def rollup(db_path):
    """Compact usage.log into usage_rollup.json so the log never grows unbounded.
    Moves the live log aside first (atomic rename) so concurrent searches keep
    appending to a fresh log and nothing is lost."""
    log_path, rollup_path = _paths(db_path)
    snap = log_path + ".rollup-snap"
    moved = False
    try:
        os.replace(log_path, snap)
        moved = True
    except OSError:
        snap = log_path  # no live log to move; aggregate whatever exists
    agg = aggregate(rollup_path, snap if moved else log_path)
    out = {k: {"count": v["count"], "first": v["first"], "last": v["last"],
               "best_rank": v["best_rank"], "sum_rank": v["sum_rank"],
               "n_rank": v["n_rank"], "qhashes": sorted(v["qhashes"])}
           for k, v in agg.items() if v["count"] > 0}
    tmp = rollup_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, rollup_path)
    if moved and os.path.exists(snap):
        os.remove(snap)
    print(f"rolled up {len(out)} cards into {os.path.basename(rollup_path)}; usage.log reset")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.expanduser("~/.claude/memory-system/db/index.db"))
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--rollup", action="store_true")
    args = ap.parse_args(argv)

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"ERROR: index not found: {db}", file=sys.stderr)
        return 2
    if args.rollup:
        return rollup(db)
    if args.summary:
        return summary(db)
    return report(db, top=args.top, json_out=args.json)


if __name__ == "__main__":
    sys.exit(main())
