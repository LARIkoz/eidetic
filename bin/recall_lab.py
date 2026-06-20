#!/usr/bin/env python3
"""Cross-lingual recall@k lab for Eidetic.

Measures whether a query in one language finds the memory written in another,
and which query strategy wins. Read-only: shells out to search_impl.py against
the live index, fuses nothing into the DB.

Like recall_smoke.py this is OPERATOR-facing. The built-in battery is a tiny
generic skeleton so the tool runs out of the box; the REAL signal comes from
your own probes supplied via --battery probes.json. Keep that file OUT of this
public repo — it encodes your private corpus (project / provider / file names).

Battery JSON = a list of objects:
    {"name": "...", "query_ru": "...", "query_en": "...", "target": "..."}
`target` is a substring of the expected result's `path` — the memory file that
SHOULD rank at the top. `query_ru` is the native-language query; `query_en` is
its English translation (for the translate / dual-query strategies). Any two
languages work; the field names are historical.

Strategies compared:
  baseline_RU        native-language query only
  translate_EN       English translation only
  dualquery_RRF      run both, fuse by Reciprocal Rank Fusion
  dualquery_MINRANK  run both, fuse by best-rank-across-the-two-lists

Per strategy: recall@k (target within top k), found@limit, medium+ confidence.

Usage:
    recall_lab.py [--battery FILE] [--db PATH] [-k 5] [--limit 50] [--json]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent

# Generic skeleton — REPLACE via --battery with your own private probes. These
# probe Eidetic's own indexed concepts so the tool runs out of the box; they are
# NOT a meaningful cross-lingual benchmark on their own.
DEFAULT_BATTERY = [
    {"name": "drift_detection",
     "query_ru": "обнаружение устаревшей памяти дрейф",
     "query_en": "stale memory drift detection", "target": "drift"},
    {"name": "compounding",
     "query_ru": "обновлять существующую память вместо дублей",
     "query_en": "update existing memory instead of duplicates", "target": "compound"},
]

STRATEGIES = ["baseline_RU", "translate_EN", "dualquery_RRF", "dualquery_MINRANK"]


def search(db, query, limit):
    cmd = [sys.executable, str(BIN / "search_impl.py"), str(db), query,
           "--limit", str(limit), "--json-object"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout).get("results", [])
    except Exception:
        return []


def rank_and_conf(results, target):
    for i, r in enumerate(results, 1):
        if target in (r.get("path") or ""):
            return i, r.get("confidence")
    return None, None


def _fuse_rrf(la, lb, k=60):
    """Reciprocal Rank Fusion: a doc's score = sum of 1/(k+rank) across lists."""
    score = {}
    for lst in (la, lb):
        for i, r in enumerate(lst, 1):
            p = r.get("path") or ""
            score[p] = score.get(p, 0.0) + 1.0 / (k + i)
    return sorted(score, key=lambda p: score[p], reverse=True)


def _fuse_minrank(la, lb):
    """Fuse by each doc's BEST rank across the two lists (tie-break: sum)."""
    pos = {}
    for lst in (la, lb):
        for i, r in enumerate(lst, 1):
            p = r.get("path") or ""
            pos.setdefault(p, []).append(i)
    return sorted(pos, key=lambda p: (min(pos[p]), sum(pos[p])))


def _fused_rank(fused_paths, target):
    for i, p in enumerate(fused_paths, 1):
        if target in p:
            return i
    return None


def run(battery, db, k, limit):
    rows = []
    counts = {s: {"recall_at_k": 0, "found": 0, "conf_ok": 0} for s in STRATEGIES}
    for probe in battery:
        ru, en, tgt = probe["query_ru"], probe["query_en"], probe["target"]
        res_ru, res_en = search(db, ru, limit), search(db, en, limit)
        cells = {}
        for s in STRATEGIES:
            if s == "baseline_RU":
                rk, cf = rank_and_conf(res_ru, tgt)
            elif s == "translate_EN":
                rk, cf = rank_and_conf(res_en, tgt)
            elif s == "dualquery_RRF":
                rk, cf = _fused_rank(_fuse_rrf(res_ru, res_en), tgt), None
            else:
                rk, cf = _fused_rank(_fuse_minrank(res_ru, res_en), tgt), None
            cells[s] = {"rank": rk, "confidence": cf}
            if rk is not None:
                counts[s]["found"] += 1
                if rk <= k:
                    counts[s]["recall_at_k"] += 1
                if cf in ("high", "medium"):
                    counts[s]["conf_ok"] += 1
        rows.append({"name": probe["name"], "cells": cells})
    return rows, counts


def _print_table(rows, counts, n, k):
    hdr = f"{'case':24} | " + " | ".join(f"{s:>17}" for s in STRATEGIES)
    print(hdr)
    print("-" * len(hdr))
    for row in rows:
        cells = []
        for s in STRATEGIES:
            c = row["cells"][s]
            rk, cf = c["rank"], c["confidence"]
            cells.append(f"#{rk}/{cf or '-'}" if rk else "MISS")
        print(f"{row['name']:24} | " + " | ".join(f"{c:>17}" for c in cells))
    print("-" * len(hdr))
    print(f"\nSUMMARY (n={n}):")
    for s in STRATEGIES:
        c = counts[s]
        print(f"  {s:18}  recall@{k}={c['recall_at_k']}/{n}   "
              f"found={c['found']}/{n}   medium+_conf={c['conf_ok']}/{n}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--battery", help="JSON file of probes (see module docstring); "
                    "default = the tiny generic skeleton")
    ap.add_argument("--db", default=os.path.expanduser("~/.claude/memory-system/db/index.db"))
    ap.add_argument("-k", type=int, default=5, help="recall@k cutoff (default 5)")
    ap.add_argument("--limit", type=int, default=50, help="search depth (default 50)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"ERROR: index not found: {db}", file=sys.stderr)
        return 2
    if args.battery:
        with open(os.path.expanduser(args.battery), encoding="utf-8") as f:
            battery = json.load(f)
    else:
        battery = DEFAULT_BATTERY

    rows, counts = run(battery, db, args.k, args.limit)
    if args.json:
        print(json.dumps({"k": args.k, "n": len(battery), "rows": rows,
                          "summary": counts}, indent=2, ensure_ascii=False))
    else:
        _print_table(rows, counts, len(battery), args.k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
