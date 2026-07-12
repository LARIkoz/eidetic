#!/usr/bin/env python3
"""FR-1/FR-2/FR-4 — the M3 agent lane (DARK): mine sub-agent & workflow
transcripts through the FROZEN v3 instrument (spec-m3-agent-lane-plumbing).

FR-0 empirical record (2026-07-12, primary box): SubagentStop FIRES for both
plain Agent-tool subagents (agent_type=general-purpose) and Workflow agents
(agent_type=workflow-subagent); the payload carries `agent_transcript_path`
(absolute path to the agent's own JSONL) plus session/agent ids. Decision
(D1): parent-Stop enumeration with a backlog cursor stays the ONE v1
mechanism — it must exist anyway for after-last-Stop completions and burst
absorption; SubagentStop is recorded as a candidate LATER latency arc only,
and this module registers NO SubagentStop consumer.

Trigger: called from m3_hook.main() AFTER the two v3 lanes, gated by
EIDETIC_M3_AGENT_LANE (D7 — additive to EIDETIC_M3_DRIVER; secondary boxes
keep the lane inert). Scans ONLY the current parent transcript's project dir,
one glob covering both layouts:

    <project_dir>/*/subagents/**/agent-*.jsonl        agent_kind=plain
    .../subagents/workflows/wf_<id>/agent-*.jsonl     agent_kind=workflow

Eligibility (FR-1, all four required): mtime strictly past the explicit init
floor (D6 live-forward — the historical backlog is never mined) · no `done`
ledger row at that mtime, where a later mtime advance re-queues ONCE per
advance (delta 3 — eligibility reads the LATEST done row's mtime_ns) ·
quiescent ≥600 s (delta 4 — agents run ACROSS parent turns; a mid-write
transcript would mine truncated and, without delta 3, lose its tail) ·
newest-mtime-first ≤3 files per fire (D2 — freshness serves
staleness-at-filing ≈ 0).

State: events/m3_agent_files.jsonl — exactly one FIRST `init` row, written
ONLY by `--init-floor` under exclusive creation BEFORE the flag is enabled,
then one `done` row per definitively-processed file. Missing / duplicate /
non-first / malformed init ⇒ state_lost: zero enumeration, one loud status,
operator repair from preserved evidence — runtime NEVER invents a floor
(first enablement is an explicit deployment action, never inferred by a
drain). Malformed `done` lines are skipped and fail toward re-mine.

Candidates flow mine → recall-drop (delta 2: agent-file `recall` candidates
are DROPPED and counted `recall_dropped` — never live-filed, never
quote-gated; routing them live would activate an unmeasured population past
the gate) → FR-8 cache, scope = canonical `<project_slug>/<agent_file>` (the
ledger identity — bare stems are NOT guaranteed unique across projects) →
quote gate → judge → acknowledged dark row (events/m3_agent_dark.jsonl, D3)
→ acknowledged cache record → ledger `done` only when EVERY outcome is
definitive and EVERY append acknowledged (FR-3/FR-4). Concurrent same-project
drains may double-spend and duplicate rows — accepted: the FR-5 report's
key-dedup is the authoritative collapse (the gate counts knowledge items,
not rows). Zero writes outside events/ until the agent-D5 gate (NFR-2).
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from glob import glob as _glob
from pathlib import Path

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_acquisition as acq  # noqa: E402
import m3_recall_miner as miner  # noqa: E402
import m3_seen_cache as cache  # noqa: E402

try:
    import lifecycle_signals as _LC  # noqa: E402
except Exception:  # pragma: no cover
    _LC = None

LEDGER_FILE = "m3_agent_files.jsonl"
DARK_FILE = "m3_agent_dark.jsonl"
DRAIN_CAP = 3                    # D2 — files per Stop fire
QUIESCENCE_NS = 600 * 10 ** 9    # delta 4 — 600 s


def _events_dir(memory_system):
    root = memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))
    return os.path.join(str(root), "events")


def _ledger_path(memory_system):
    return os.path.join(_events_dir(memory_system), LEDGER_FILE)


def _now_iso():
    return _LC._recorded_at() if _LC else ""


def _iso_from_ns(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _append_ledger(memory_system, row):
    """Acknowledged ledger append — the returned bool is part of the FR-3
    contract (a false/failed `done` append is ledger_append_failed)."""
    if _LC is None:
        return False
    try:
        return bool(_LC._atomic_append_jsonl(
            Path(_ledger_path(memory_system)), _LC._compact_json(row)))
    except Exception:
        return False


def read_ledger(memory_system):
    """→ (mtime_floor_ns, done_map, "ok") | (None, {}, "state_lost").

    done_map: agent_file → MAX mtime_ns across that file's `done` rows
    (delta 3 — reading any earlier row would re-queue the file every fire,
    not once per advance). The init row must be the FIRST non-empty line and
    the ONLY `init` in the file; anything else about it — missing ledger,
    malformed first line, duplicate init — is state_lost (fail closed, D6).
    Malformed `done` lines are skipped: fail toward re-mine (NFR-3)."""
    try:
        with open(_ledger_path(memory_system), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return None, {}, "state_lost"  # missing ledger = missing init
    if not lines:
        return None, {}, "state_lost"
    floor = None
    done = {}
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
        except Exception:
            row = None
        if i == 0:
            if not (isinstance(row, dict) and row.get("type") == "init"
                    and isinstance(row.get("mtime_floor_ns"), int)):
                return None, {}, "state_lost"  # malformed / non-first init
            floor = row["mtime_floor_ns"]
            continue
        if not isinstance(row, dict):
            continue  # malformed done line → skip, fail toward re-mine
        if row.get("type") == "init":
            return None, {}, "state_lost"  # duplicate init
        if row.get("type") == "done" and row.get("agent_file") \
                and isinstance(row.get("mtime_ns"), int):
            af = row["agent_file"]
            if row["mtime_ns"] > done.get(af, -1):
                done[af] = row["mtime_ns"]
    return floor, done, "ok"


def init_floor(memory_system, now_ns=None):
    """The explicit write-once deployment step (D6): record mtime_floor_ns
    BEFORE the flag is enabled. Exclusive creation (O_EXCL) — a second or
    concurrent attempt refuses WITHOUT mutation. Runtime never calls this."""
    path = _ledger_path(memory_system)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    except OSError:
        pass
    now_ns = time.time_ns() if now_ns is None else now_ns
    row = {"type": "init", "ts": _now_iso(),
           "mtime_floor_ns": now_ns, "mtime_floor": _iso_from_ns(now_ns)}
    data = (_LC._compact_json(row) if _LC else
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"status": "refused", "reason": "already_initialized",
                "ledger": path}
    except OSError as exc:
        return {"status": "error", "error": repr(exc)[:200]}
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return {"status": "initialized", "mtime_floor_ns": now_ns,
            "mtime_floor": row["mtime_floor"], "ledger": path}


def _classify(project_dir, path):
    """→ (agent_file relpath, parent_session_id, agent_kind, workflow_id,
    stem). agent_file is project-dir-relative (stable across $HOME moves) —
    the ONE file-identity dialect shared by ledger, cache scope, and rows."""
    rel = os.path.relpath(path, project_dir)
    parts = rel.split(os.sep)
    parent_sid = parts[0] if parts else ""
    wf = next((p for p in parts if p.startswith("wf_")), None)
    stem = os.path.basename(path).rsplit(".", 1)[0]
    return rel, parent_sid, ("workflow" if wf else "plain"), wf, stem


def enumerate_eligible(project_dir, floor_ns, done_map, now_ns):
    """One directory walk (NFR-5) → (selected ≤DRAIN_CAP newest-mtime-first,
    counters). Eligibility = the four FR-1 rules; `eligible` counts pre-cap,
    `backlog` what this fire leaves behind."""
    pattern = os.path.join(project_dir, "*", "subagents", "**", "agent-*.jsonl")
    scanned = []
    for p in _glob(pattern, recursive=True):
        try:
            scanned.append((p, os.stat(p).st_mtime_ns))
        except OSError:
            continue  # vanished between glob and stat — never fatal
    eligible = []
    for p, m in scanned:
        if m <= floor_ns:
            continue  # D6: pre-deploy files stay dark forever
        rel = os.path.relpath(p, project_dir)
        if rel in done_map and m <= done_map[rel]:
            continue  # done at this mtime; a later advance re-queues (delta 3)
        if now_ns - m < QUIESCENCE_NS:
            continue  # warm — stays in the backlog (delta 4)
        eligible.append((p, m))
    eligible.sort(key=lambda t: t[1], reverse=True)
    counters = {
        "scanned": len(scanned),
        "eligible": len(eligible),
        "backlog": max(0, len(eligible) - DRAIN_CAP),
        "oldest_eligible_s": (
            int((now_ns - min(m for _, m in eligible)) / 1e9)
            if eligible else 0),
    }
    return eligible[:DRAIN_CAP], counters


def _drain_one(path, mtime_ns, project_dir, slug, memory_system):
    """Mine ONE agent file through the frozen instrument (FR-2). The recorded
    mtime_ns is the one pinned at enumeration — a tail written during the
    drain advances mtime past it and re-queues the file (delta 3).

    → {"tally", "skipped_seen", "recall_dropped", "judge_calls",
       "mined": bool, "done": bool}. `done` is written ONLY when every
    candidate reached a DEFINITIVE outcome AND every dark/cache/ledger append
    acknowledged success (FR-3/FR-4)."""
    rel, parent_sid, agent_kind, wf_id, stem = _classify(project_dir, path)
    scope = f"{slug}/{rel}"  # canonical (project_slug, agent_file) identity
    out = {"tally": {}, "skipped_seen": 0, "recall_dropped": 0,
           "judge_calls": 0, "mined": False, "done": False}

    def bump(key, n=1):
        out["tally"][key] = out["tally"].get(key, 0) + n

    if not os.path.isfile(path):  # deleted between select and drain
        bump("missing")
        return out
    try:
        cands, meta = miner.mine_transcript(
            path, session_id=stem, project_slug=slug)
    except Exception:
        bump("miner_error")
        return out
    if meta.get("error"):  # FR-2: transient even with candidates=[] — no done
        bump("miner_error")
        return out
    out["mined"] = True

    recalls = [c for c in cands
               if (c.get("kind") or miner.KIND_RECALL) == miner.KIND_RECALL]
    acq_cands = [c for c in cands if c.get("kind") in miner.ACQ_KINDS]
    out["recall_dropped"] = len(recalls)  # delta 2: dropped + counted, loud

    all_ok = True
    if acq_cands:
        seen = cache.load_seen(memory_system, scope)
        fresh = []
        for c in acq_cands:
            k = cache.candidate_key(c)
            if k in seen:
                out["skipped_seen"] += 1
                continue
            fresh.append((c, k))
        if fresh:
            extra = {
                "parent_session_id": parent_sid,
                "agent_kind": agent_kind,
                "workflow_id": wf_id,
                "agent_file": rel,
                "transcript_mtime": _iso_from_ns(mtime_ns),
            }
            stats = {"judge_calls": 0}
            tally, outcomes = acq.process(
                path, [c for c, _ in fresh], memory_system=memory_system,
                dark_file=DARK_FILE, extra_fields=extra, stats=stats)
            out["judge_calls"] = stats.get("judge_calls", 0)
            for k2, v in tally.items():
                bump(k2, v)
            for (c, key), oc in zip(fresh, outcomes):
                if oc in cache.DEFINITIVE:
                    # Acknowledged cache record AFTER the acknowledged dark
                    # append (order inherited, FR-3): a crash between the two
                    # leaves a re-judgeable cache MISS, never a cache hit
                    # hiding a missing dark row.
                    if not cache.record(memory_system, scope, key,
                                        c.get("kind"), oc):
                        bump("cache_record_failed")
                        all_ok = False
                else:
                    all_ok = False  # transient — the file retries later
    if all_ok:
        done_row = {"type": "done", "ts": _now_iso(), "agent_file": rel,
                    "session_id": stem, "project_slug": slug,
                    "mtime_ns": mtime_ns, "mtime": _iso_from_ns(mtime_ns),
                    "tally": dict(out["tally"])}
        if _append_ledger(memory_system, done_row):
            out["done"] = True
        else:
            bump("ledger_append_failed")  # no completion claimed; retry later
    return out


def drain(transcript, slug, memory_system):
    """The FR-4 entry, called from m3_hook after the two v3 lanes. Never
    raises past the caller's outer except; one file's failure never kills
    the drain (NFR-3). Returns the driver-line `agent` block."""
    block = {"status": "ran", "ts": _now_iso(), "project_slug": slug,
             "scanned": 0, "eligible": 0, "backlog": 0,
             "oldest_eligible_s": 0, "mined_files": 0, "tally": {},
             "skipped_seen": 0, "recall_dropped": 0, "judge_calls": 0}
    floor_ns, done_map, status = read_ledger(memory_system)
    if status != "ok":
        # D6 fail-closed: zero scanning, zero mining, no replacement floor —
        # total ledger loss must stay distinguishable from first enablement.
        block["status"] = "state_lost"
        return block
    project_dir = os.path.dirname(os.path.abspath(transcript))
    selected, counters = enumerate_eligible(
        project_dir, floor_ns, done_map, time.time_ns())
    block.update(counters)
    for path, mtime_ns in selected:
        try:
            r = _drain_one(path, mtime_ns, project_dir, slug, memory_system)
        except Exception:  # one file never kills the drain
            r = {"tally": {"error": 1}, "skipped_seen": 0,
                 "recall_dropped": 0, "judge_calls": 0,
                 "mined": False, "done": False}
        if r["mined"]:
            block["mined_files"] += 1
        block["skipped_seen"] += r["skipped_seen"]
        block["recall_dropped"] += r["recall_dropped"]
        block["judge_calls"] += r["judge_calls"]
        for k, v in r["tally"].items():
            block["tally"][k] = block["tally"].get(k, 0) + v
    return block


def main():
    ap = argparse.ArgumentParser(
        description="M3 agent lane — only --init-floor is a CLI action; "
                    "the drain runs from m3_hook (FR-4).")
    ap.add_argument("--init-floor", action="store_true",
                    help="write the ONE first init row (run BEFORE enabling "
                         "EIDETIC_M3_AGENT_LANE; a second attempt refuses)")
    ap.add_argument("--memory-system", default=None)
    args = ap.parse_args()
    ms = args.memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))
    if args.init_floor:
        res = init_floor(ms)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("status") == "initialized" else 1
    ap.error("nothing to do — pass --init-floor (the drain is hook-driven)")


if __name__ == "__main__":
    sys.exit(main())
