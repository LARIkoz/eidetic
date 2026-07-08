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


def _reexec_under_sdk_python():
    """The miner + judge call the shared SDK (shared_api_cache → needs `dotenv`,
    `pydantic`, …). The session hook prepends the py3.12 `eidetic-mlx` venv to
    PATH for embedding; that venv is ISOLATED (include-system-site-packages=false)
    and lacks the SDK deps, so a bare `python3` here resolves to the venv and the
    SDK import fails with ModuleNotFoundError. Inverse of embed.py's mlx re-exec:
    when the current interpreter cannot load the SDK, re-exec under the first
    python3 that CAN (system/homebrew). M3 does not need mlx — source retrieval
    degrades to FTS-only. No-op when the current python already has the SDK, or
    when no capable python is found (then M3 runs and reports the sdk error
    loudly — no regression). Loop-guarded; opt out with EIDETIC_NO_M3_PY_REEXEC=1."""
    if os.environ.get("EIDETIC_M3_PY_REEXEC") or os.environ.get("EIDETIC_NO_M3_PY_REEXEC"):
        return
    try:
        import dotenv  # noqa: F401 — proxy: this interpreter can load the shared SDK
        return
    except Exception:
        pass
    import subprocess
    seen = {os.path.realpath(sys.executable)}
    for cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                 "/usr/bin/python3", os.path.join(sys.base_prefix, "bin", "python3")):
        try:
            if not cand or not os.path.exists(cand):
                continue
            rp = os.path.realpath(cand)
            if rp in seen:
                continue
            seen.add(rp)
            subprocess.run([cand, "-c", "import dotenv"], check=True, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        os.environ["EIDETIC_M3_PY_REEXEC"] = "1"
        try:
            os.execv(cand, [cand, os.path.abspath(__file__), *sys.argv[1:]])
        except Exception:
            return  # exec failed → fall through under the current interpreter


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
    _reexec_under_sdk_python()
    sys.exit(main(sys.argv))
