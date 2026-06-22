#!/usr/bin/env python3
"""Eidetic value report — what the PASSIVE memory injection costs per session.

Phase 0 (cost side only). Reads db/inject_log.jsonl (written by assemble_context.py
on every SessionStart) and answers the owner's question "what does Eidetic cost?":
  - passive_tax / session = injected tokens (memory-context.md) + MEMORY.md loaded whole
  - breakdown by section (feedback / project / recent / handoff / drift)
  - MEMORY.md's share (the 75 KB index loaded every session — the compress target)
  - trend over the last N sessions, per project

It does NOT yet measure BENEFIT (did a card help) — that is Phase 1 (passive_stats.py).
So this report is the honest COST half: cheap, exact, no embedding, no transcript.

Usage:
  value_report.py            # full cost report
  value_report.py --summary  # one-line (for doctor.sh)
  value_report.py --json     # machine-readable
  value_report.py --tail N   # only the last N sessions
"""

import argparse
import json
import os
import sys

DEFAULT_LOG = os.path.expanduser("~/.claude/memory-system/db/inject_log.jsonl")
SESSION_VALUE = os.path.expanduser("~/.claude/memory-system/db/session_value.jsonl")


def load_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue  # skip a torn line, never crash the report
    return rows


def tax(row):
    """passive_tax = injected context tokens + MEMORY.md (loaded whole) in tokens."""
    return int(row.get("total_tokens", 0)) + int(row.get("memory_md_bytes", 0)) // 4


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def summarize(rows):
    if not rows:
        return {"sessions": 0}
    taxes = [tax(r) for r in rows]
    return {
        "sessions": len(rows),
        "first": rows[0].get("ts"),
        "last": rows[-1].get("ts"),
        "avg_tax": round(_avg(taxes)),
        "median_tax": round(_median(taxes)),
        "avg_inject": round(_avg([r.get("total_tokens", 0) for r in rows])),
        "avg_memory_md_tokens": round(_avg([r.get("memory_md_bytes", 0) // 4 for r in rows])),
        "avg_feedback": round(_avg([r.get("feedback_tokens", 0) for r in rows])),
        "avg_project": round(_avg([r.get("project_tokens", 0) for r in rows])),
        "avg_recent": round(_avg([r.get("recent_tokens", 0) for r in rows])),
        "avg_n_rules": round(_avg([r.get("n_rules", 0) for r in rows])),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--tail", type=int, default=0, help="only the last N sessions")
    ap.add_argument("--summary", action="store_true", help="one-line for doctor")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = load_rows(args.log)
    if args.tail and rows:
        rows = rows[-args.tail:]
    s = summarize(rows)

    if args.json:
        print(json.dumps(s, ensure_ascii=False))
        return 0

    if args.summary:
        if not s["sessions"]:
            print("value: no inject_log yet (telemetry not deployed or no sessions)")
        else:
            print(f"value(cost): {s['sessions']} sessions | passive_tax avg "
                  f"{s['avg_tax']}t/session (inject {s['avg_inject']}t + MEMORY.md "
                  f"{s['avg_memory_md_tokens']}t)")
        return 0

    if not s["sessions"]:
        print("=== Eidetic value report (cost) ===")
        print(f"No inject_log rows at {args.log}.")
        print("→ telemetry not deployed yet, or no SessionStart has run since deploy.")
        return 0

    md_share = (s["avg_memory_md_tokens"] / s["avg_tax"] * 100) if s["avg_tax"] else 0
    print("=== Eidetic value report — PASSIVE COST (Phase 0) ===")
    print(f"sessions: {s['sessions']}   window: {s['first']} → {s['last']}")
    print()
    print(f"passive_tax / session : avg {s['avg_tax']}t   median {s['median_tax']}t")
    print(f"  ├─ injected context : {s['avg_inject']}t  (memory-context.md, ~{s['avg_n_rules']} rules)")
    print(f"  │    ├─ feedback     : {s['avg_feedback']}t")
    print(f"  │    ├─ project      : {s['avg_project']}t")
    print(f"  │    └─ recent       : {s['avg_recent']}t")
    print(f"  └─ MEMORY.md (whole) : {s['avg_memory_md_tokens']}t  ← {md_share:.0f}% of tax, loaded EVERY session")
    print()
    # per-project breakdown
    by_proj = {}
    for r in rows:
        by_proj.setdefault(r.get("project", "?"), []).append(tax(r))
    if len(by_proj) > 1:
        print("by project (avg tax/session):")
        for proj, taxes in sorted(by_proj.items(), key=lambda kv: -_avg(kv[1])):
            print(f"  {round(_avg(taxes)):>6}t  ×{len(taxes):<3} {proj}")
        print()
    if md_share >= 50:
        print(f"FLAG: MEMORY.md is {md_share:.0f}% of every session's memory tax — top compress target.")

    # ---- BENEFIT side (Phase 1): referenced_k from session_value.jsonl ----
    sv = [r for r in load_rows(SESSION_VALUE) if r.get("n_cards")]
    print()
    if sv:
        from collections import Counter
        cnt = Counter()
        for r in sv:
            for s in r.get("referenced_slugs", []):
                cnt[s] += 1
        avg_ref = _avg([r.get("referenced_k", 0) for r in sv])
        avg_util = _avg([r.get("utilization", 0) for r in sv])
        print(f"BENEFIT — injected cards REFERENCED in real session work ({len(sv)} sessions):")
        print(f"  referenced / session : avg {avg_ref:.1f} cards   utilization avg {avg_util * 100:.1f}%")
        if cnt:
            print("  most-referenced      : " + ", ".join(f"{s}×{c}" for s, c in cnt.most_common(5)))
        print("  (LOWER BOUND: literal slug match only — paraphrased use is missed; not causation.)")
    else:
        print("BENEFIT: no measured sessions yet (session_value.jsonl empty) — fills at SessionEnd.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
