#!/usr/bin/env python3
"""Stop-hook entry for the M3 loop (DARK by default) — v3: two lanes.

Called from hooks/session-signals.sh with the transcript path, ONLY when
EIDETIC_M3_DRIVER=on (and filing additionally requires EIDETIC_M3_AUTOFILE=on
inside the gate — two independent locks). One bounded line of JSON to stdout
per run (goes to events/m3_driver.log via the hook's redirect); never raises.

v3 kind-routing (spec FR-3): `recall` candidates flow into the existing
consolidation pipeline (m3_producer_driver.drive) unchanged; acquisition kinds
(decision/finding/rule) go through the DARK lane (m3_acquisition — quote gate →
judge → events/m3_acquisition_dark.jsonl, zero store writes until the D5 gate).
Both lanes pass the FR-8 seen-cache first: a candidate with a definitive
outcome this session is skipped BEFORE producer retrieval and the judge.

The target memory dir is the transcript's own project
(~/.claude/projects/<slug>/memory) — a recall filed where it was recalled.
project_slug for producer minting is left empty for now: FR-4 graduation is a
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
        import m3_acquisition as acq
        import m3_producer_driver as drv
        import m3_recall_miner as miner
        import m3_seen_cache as cache

        slug = os.path.basename(os.path.dirname(transcript))
        memory_dir = os.path.expanduser(f"~/.claude/projects/{slug}/memory")
        ms = os.environ.get("EIDETIC_MEMORY_SYSTEM",
                            os.path.expanduser("~/.claude/memory-system"))
        idb = os.path.join(ms, "db", "index.db")
        if not (os.path.isdir(memory_dir) and os.path.isfile(idb)):
            print(json.dumps({"m3_driver": "skip", "reason": "no_store_or_memdir"}))
            return 0
        cands, meta = miner.mine_transcript(transcript)
        sid = os.path.basename(transcript).rsplit(".", 1)[0]  # = miner's sid

        # FR-8: seen-cache — skip candidates already definitively judged this
        # session BEFORE producer retrieval and the judge.
        seen = cache.load_seen(ms, sid)
        fresh = []
        for c in cands:
            k = cache.candidate_key(c)
            if k in seen:
                continue
            fresh.append((c, k))
        meta["skipped_seen"] = len(cands) - len(fresh)

        if not fresh:
            print(json.dumps({"m3_driver": "ran", "mined": len(cands),
                              "meta": meta}, ensure_ascii=False))
            return 0

        recall = [(c, k) for c, k in fresh
                  if (c.get("kind") or miner.KIND_RECALL) == miner.KIND_RECALL]
        acq_cands = [(c, k) for c, k in fresh if c.get("kind") in miner.ACQ_KINDS]

        # Consolidation lane — unchanged pipeline. drive() is skipped entirely
        # when no recall candidates survived (no judge-probe burned).
        tally, judge_active = {}, False
        if recall:
            outcomes, judge_active = drv.drive(
                idb, [c for c, _ in recall], memory_dir=memory_dir)
            for (c, k), o in zip(recall, outcomes):
                action = o.get("action")
                tally[action] = tally.get(action, 0) + 1
                cache.record(ms, sid, k, miner.KIND_RECALL, action)

        # Acquisition lane — DARK (zero store writes; events/ only).
        acq_tally = {}
        if acq_cands:
            acq_tally, acq_outcomes = acq.process(
                transcript, [c for c, _ in acq_cands], memory_system=ms)
            for (c, k), outcome in zip(acq_cands, acq_outcomes):
                cache.record(ms, sid, k, c.get("kind"), outcome)

        print(json.dumps({"m3_driver": "ran", "mined": len(cands),
                          "judge_active": judge_active, "tally": tally,
                          "acq": acq_tally, "meta": meta}, ensure_ascii=False))
    except Exception as exc:  # a Stop hook must never break the session close
        print(json.dumps({"m3_driver": "error", "error": repr(exc)[:200]}))
    return 0


if __name__ == "__main__":
    _reexec_under_sdk_python()
    sys.exit(main(sys.argv))
