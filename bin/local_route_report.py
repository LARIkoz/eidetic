#!/usr/bin/env python3
"""Daily view of Eidetic's local-only route (owner D9): is Shimnachi carrying the load?

Reads events/session-signals.log and events/m3_driver.log and prints, per UTC day:
signal runs by route, M3 miner runs by proposal route and error, and judge verdicts
since the judge registered on the local v6 lane. Usage:
  python3 local_route_report.py [--days 7] [--memory-system ~/.claude/memory-system]
"""
import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--memory-system", default=os.path.expanduser(
        os.environ.get("EIDETIC_MEMORY_SYSTEM", "~/.claude/memory-system")))
    args = ap.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    events = os.path.join(args.memory_system, "events")

    signals = defaultdict(Counter)
    path = os.path.join(events, "session-signals.log")
    if os.path.exists(path):
        for line in open(path, errors="replace"):
            parts = line.split()
            if len(parts) < 2 or parts[0][:10] < since:
                continue
            fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            key = fields.get("route", "?") + ("/" + fields["status"] if "status" in fields else "")
            signals[parts[0][:10]][key] += 1

    miner = defaultdict(Counter)
    judge = Counter()
    local_judge_seen = False
    path = os.path.join(events, "m3_driver.log")
    if os.path.exists(path):
        for line in open(path, errors="replace"):
            if "REGISTERED judge=shimnachi/local prompt=m3-entailment-local-v6" in line:
                local_judge_seen = True
                judge["registered"] += 1
                continue
            if local_judge_seen and line.startswith("[m3_judge] "):
                m = re.match(r"\[m3_judge\] (VERDICT entailed=\w+ filed=\w+|\w+)", line)
                if m:
                    judge[m.group(1)] += 1
                continue
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = (rec.get("agent") or {}).get("ts") or rec.get("ts") or ""
            if rec.get("m3_driver") != "ran" or ts[:10] < since:
                continue
            meta = rec.get("meta") or {}
            route = (meta.get("proposal_provenance") or {}).get("route_id") or "none"
            miner[ts[:10]][route if not meta.get("error") else "error:" + str(meta["error"])[:40]] += 1

    print(f"Eidetic local route, since {since} (UTC)\n")
    print("Signals (session-signals.log):")
    for day in sorted(signals):
        print(f"  {day}  " + ", ".join(f"{k} {v}" for k, v in signals[day].most_common()))
    print("\nM3 miner runs by proposal route:")
    for day in sorted(miner):
        print(f"  {day}  " + ", ".join(f"{k} {v}" for k, v in miner[day].most_common()))
    print("\nM3 judge since it registered on shimnachi/local v6:")
    print("  " + (", ".join(f"{k} {v}" for k, v in judge.most_common()) or "not registered yet"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
