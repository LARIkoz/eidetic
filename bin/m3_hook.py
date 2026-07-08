#!/usr/bin/env python3
"""Stop-hook entry for the M3 auto-file loop (DARK by default).

Called from hooks/session-signals.sh with the transcript path, ONLY when
EIDETIC_M3_DRIVER=on (and filing additionally requires EIDETIC_M3_AUTOFILE=on
inside the gate — two independent locks). One bounded line of JSON to stdout
per run (goes to events/m3_driver.log via the hook's redirect); never raises.

The target memory dir is the transcript's own project
(~/.claude/projects/<slug>/memory) — a recall filed where it was recalled.
project_slug for producer minting is left empty for now: FR-3 graduation is a
later arc; nothing is minted from auto-filed pages yet.
"""
import json
import os
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)


def main(argv):
    if len(argv) < 2 or not os.path.isfile(argv[1]):
        print(json.dumps({"m3_driver": "skip", "reason": "no_transcript"}))
        return 0
    if os.environ.get("EIDETIC_M3_DRIVER", "").strip().lower() not in ("1", "on", "true", "yes"):
        print(json.dumps({"m3_driver": "skip", "reason": "dark"}))
        return 0
    transcript = argv[1]
    try:
        import m3_producer_driver as drv
        import m3_recall_miner as miner

        slug = os.path.basename(os.path.dirname(transcript))
        memory_dir = os.path.expanduser(f"~/.claude/projects/{slug}/memory")
        ms = os.environ.get("EIDETIC_MEMORY_SYSTEM",
                            os.path.expanduser("~/.claude/memory-system"))
        idb = os.path.join(ms, "db", "index.db")
        if not (os.path.isdir(memory_dir) and os.path.isfile(idb)):
            print(json.dumps({"m3_driver": "skip", "reason": "no_store_or_memdir"}))
            return 0
        cands, meta = miner.mine_transcript(transcript)
        if not cands:
            print(json.dumps({"m3_driver": "ran", "mined": 0, "meta": meta}))
            return 0
        outcomes, judge_active = drv.drive(idb, cands, memory_dir=memory_dir)
        tally = {}
        for o in outcomes:
            tally[o.get("action")] = tally.get(o.get("action"), 0) + 1
        print(json.dumps({"m3_driver": "ran", "mined": len(cands),
                          "judge_active": judge_active, "tally": tally,
                          "meta": meta}, ensure_ascii=False))
    except Exception as exc:  # a Stop hook must never break the session close
        print(json.dumps({"m3_driver": "error", "error": repr(exc)[:200]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
