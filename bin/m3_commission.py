#!/usr/bin/env python3
"""M3 commission — machine marking for the D5 gates (brief-m3-commission-gate).

Owner decision 2026-07-12: the D5 MARKER is a 3-voice CLI commission instead
of hand-marking; the owner's role shrinks to one word over a summary page.
The D4 gate MATH is unchanged (activate ≥70% keep AND dangerous-wrong within
the frozen budget (a) ≤1/round; kill <50% keep; between → iterate).

Composition (owner-picked; Claude excluded — the mined transcripts are
Claude's own sessions, and independence holds pipeline-wide: miner =
dashscope/qwen3-coder-plus, quote gate = palmyra-x5, judges = Google + xAI +
OpenAI):

  gemini31  Gemini 3.1 Pro (High)   via agy-p (PTY wrapper, prompt on stdin)
  grok45    grok-4.5 @ max effort   via grok CLI (prompt-file, JSON output,
                                    file tools ENABLED for evidence checks)
  codex55   gpt-5.5 @ high          via codex CLI, MAIN account (the same
                                    account council voices run on — NOT spark,
                                    whose pool is separate by config). The
                                    owner picked "codex 5.3": retired for
                                    ChatGPT accounts at the 5.6 rollout
                                    (live 400, 07-12). Account #2 was tried
                                    first and is a free tier with its usage
                                    cap exhausted until Aug 8 (live-verified
                                    same day) — main it is.

Each judge is told to REFUTE the card against live evidence (repos, git,
memory pages). Aggregation per item: `keep` needs ≥2/3 keep votes and zero
EVIDENCED dangerous verdicts; any dangerous verdict carrying a checkable
evidence_ref marks the item dangerous_wrong (one flaky judge without evidence
only casts a noise vote); fewer than 2 definitive votes = unresolved (loud,
gate-excluded). Fail-toward-reject throughout.

Transport reuses cli-council's `council.providers.invoke` (one fact, one
place for PTY/auth-preflight/prompt-file/extract quirks); the commission owns
only its judge-mode Provider profiles (tools ON — councils deliberately deny
them). Verdicts append to `events/m3_commission.jsonl` (resume-safe: an
(item, judge) pair with an ok row is never re-asked); raw judge outputs to
`events/m3_commission_raw.jsonl`; the owner page to
`events/m3_commission_summary.md`. Everything under events/ is LOCAL material
(NFR-4). ZERO writes outside events/ — the commission marks, activation stays
its own owner-gated arc (NFR-2).
"""
import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import threading
from pathlib import Path

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_dark_report as _report  # noqa: E402  (collect_lane_items — one collection path)

try:
    import lifecycle_signals as _LC  # noqa: E402
except Exception:  # pragma: no cover
    _LC = None

COUNCIL_REPO = os.environ.get(
    "CLI_COUNCIL_REPO", os.path.expanduser("~/Documents/cursore/cli-council"))

VERDICTS_FILE = "m3_commission.jsonl"
RAW_FILE = "m3_commission_raw.jsonl"
SUMMARY_FILE = "m3_commission_summary.md"

VERDICT_ENUM = ("keep", "noise", "dangerous_wrong")
KEEP_QUORUM = 2          # of 3 voices
MIN_DEFINITIVE = 2       # below → unresolved, gate-excluded, loud
ACTIVATE_KEEP = 0.70     # D4 math, unchanged
KILL_KEEP = 0.50
DANGEROUS_BUDGET = 1     # frozen option (a), 2026-07-12

# Per-voice call ceilings and parallelism (agy is the slowest house).
VOICE_TIMEOUT = {"gemini31": 600.0, "grok45": 600.0, "codex55": 600.0}
VOICE_PARALLEL = {"gemini31": 2, "grok45": 2, "codex55": 2}

PROMPT = """You are one independent judge on a 3-model commission verifying knowledge cards mined from coding-session transcripts before they enter a persistent memory wiki. Your job is to try to REFUTE the card against the LIVE evidence on this machine.

EVIDENCE ROOTS (read-only): {home}/Documents/cursore (project repos + git history), {home}/.claude/projects/*/memory (memory wiki pages). If your tools allow, READ the relevant files or run git log/git show in the card's project repo to check the claim. If you cannot access tools, judge from internal consistency and say so in evidence_note.

CARD (lane {lane}):
- kind: {kind}
- project: {project_slug}
- recorded at (transcript mtime): {transcript_mtime}
- claim: {claim}
- verbatim quote from the session: {quote}
- re-wordings of the same knowledge: {rewordings}

Verdicts:
- "keep" — survives your refutation attempt; specific, useful, still accurate.
- "noise" — true-but-useless, trivial, near-duplicate, or unverifiable-but-harmless.
- "dangerous_wrong" — contradicted by current evidence, stale-presented-as-current, or misleading without its time anchor. REQUIRES evidence_ref (a file path, file:line, git:<sha>, or memory:<slug>) you actually checked; without a checkable ref your dangerous vote is counted as noise.

Rules: a claim can be true FOR ITS TIME (see recorded-at) yet dangerous TODAY if it reads as current state. Uncertain → prefer noise (burying a boring card is cheap; polluting memory is expensive).

Output ONLY one JSON object, no markdown fence, no prose:
{{"verdict": "keep|noise|dangerous_wrong", "confidence": 0.0, "evidence_ref": "<ref or none>", "evidence_note": "<what you checked>", "reason": "<one sentence>"}}"""


def _events_dir(memory_system):
    root = memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))
    return os.path.join(str(root), "events")


def _now_iso():
    return _LC._recorded_at() if _LC else ""


def _append(memory_system, fname, row):
    if _LC is None:
        return False
    try:
        return bool(_LC._atomic_append_jsonl(
            Path(os.path.join(_events_dir(memory_system), fname)),
            _LC._compact_json(row)))
    except Exception:
        return False


def _judges():
    """Judge-mode Provider profiles on cli-council's transport. Built lazily:
    the eidetic CI has no cli-council checkout, and tests patch _invoke_voice
    below this seam."""
    sys.path.insert(0, COUNCIL_REPO)
    try:
        from council.providers import Provider
    except Exception as exc:  # pragma: no cover — operator box always has it
        raise RuntimeError(
            f"cli-council not importable from {COUNCIL_REPO} "
            f"(set CLI_COUNCIL_REPO): {exc!r}")

    def grok_json(stdout):
        try:
            return json.loads(stdout).get("text", "")
        except Exception:
            return stdout

    return {
        # agy-p supplies the PTY raw `agy -p` needs; prompt on stdin (no
        # {prompt} slot) — the council.toml-proven shape.
        "gemini31": Provider(
            name="gemini31", bin="agy-p", family="google",
            argv=["agy-p", "--model", "Gemini 3.1 Pro (High)"],
            timeout=VOICE_TIMEOUT["gemini31"]),
        # grokbuild shape MINUS the --deny tool blocks: the commission WANTS
        # file tools for evidence checks (councils deny them because there is
        # nothing to read there). Web/subagents/plan stay off — evidence is
        # local and cost is bounded.
        "grok45": Provider(
            name="grok45", bin="grok", uses_prompt_file=True, family="xai",
            argv=["grok", "-m", "grok-4.5", "--effort", "max",
                  "--prompt-file", "{prompt_file}", "--output-format", "json",
                  "--disable-web-search", "--no-subagents", "--no-plan",
                  "--no-alt-screen"],
            extract=grok_json,
            auth_check=["grok", "models"],
            auth_fail_marker="not authenticated",
            timeout=VOICE_TIMEOUT["grok45"]),
        # MAIN codex account — pinned model+effort (a judge voice must not
        # float on a user-editable default). NOT spark (separate personal
        # pool); NOT gpt-5.3-codex (retired for ChatGPT accounts at the 5.6
        # rollout — live 400, 2026-07-12); NOT acc#2 (free tier, usage cap
        # exhausted until Aug 8 — live-verified the same day). Parallelism 2:
        # the councils' proven safe concurrent load on this account.
        "codex55": Provider(
            name="codex55", bin="codex", family="openai",
            argv=["codex", "exec", "--skip-git-repo-check",
                  "-c", 'model="gpt-5.5"',
                  "-c", "model_reasoning_effort=high", "-"],
            timeout=VOICE_TIMEOUT["codex55"]),
    }


def _invoke_voice(judge_name, provider, prompt, timeout):
    """The ONE seam tests patch. Production defers to council.providers.invoke
    (auth preflight, prompt-file, extract, loud failure classes)."""
    sys.path.insert(0, COUNCIL_REPO)
    from council.providers import invoke
    return invoke(provider, prompt, timeout=timeout)


def parse_verdict(text):
    """→ dict or None. Judges are told 'ONLY one JSON object' but wrap in
    fences/prose anyway — take the first {...} blob that parses and carries a
    valid verdict."""
    if not text:
        return None
    for m in re.finditer(r"\{.*?\}", text, re.DOTALL):
        try:
            d = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("verdict") in VERDICT_ENUM:
            return d
    return None


def _evidenced(v):
    ref = str(v.get("evidence_ref") or "").strip().lower()
    return ref not in ("", "none", "n/a", "null", "-")


def build_prompt(item):
    return PROMPT.format(
        home=os.path.expanduser("~"),
        lane=item.get("lane") or "?",
        kind=item.get("kind") or "?",
        project_slug=item.get("project_slug") or "?",
        transcript_mtime=item.get("transcript_mtime") or "unknown",
        claim=item.get("claim") or "",
        quote=item.get("quote") or "",
        rewordings=json.dumps(item.get("rewordings") or [],
                              ensure_ascii=False),
    )


def load_done(memory_system, lane):
    """(item_key, judge) pairs with an ok verdict row — never re-asked."""
    done = set()
    path = os.path.join(_events_dir(memory_system), VERDICTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get("ok") and \
                        row.get("lane") == lane and row.get("item_key") \
                        and row.get("judge"):
                    done.add((row["item_key"], row["judge"]))
    except OSError:
        pass
    return done


def judge_items(items, memory_system, judges=None, lane="main",
                parallel=None):
    """Fan (item × judge) out with per-voice parallelism caps; append one
    verdict row per completed ask. Resume-safe; a voice failure is a transient
    row (ok=false) and re-asked on the next run. → counters dict."""
    providers = judges if judges is not None else _judges()
    done = load_done(memory_system, lane)
    sems = {name: threading.BoundedSemaphore(
        (parallel or VOICE_PARALLEL).get(name, 2)) for name in providers}
    tasks = [(it, name) for it in items for name in providers
             if (it["key"], name) not in done]
    counters = {"asked": 0, "ok": 0, "voice_error": 0, "parse_fail": 0,
                "skipped_done": len(items) * len(providers) - len(tasks)}

    def one(it, name):
        prov = providers[name]
        timeout = VOICE_TIMEOUT.get(name, 600.0)
        with sems[name]:
            ok, text = _invoke_voice(name, prov, build_prompt(it), timeout)
        _append(memory_system, RAW_FILE, {
            "ts": _now_iso(), "lane": lane, "item_key": it["key"],
            "judge": name, "ok": ok, "raw": (text or "")[:20000]})
        if not ok:
            _append(memory_system, VERDICTS_FILE, {
                "ts": _now_iso(), "lane": lane, "item_key": it["key"],
                "judge": name, "ok": False, "error": (text or "")[:300]})
            return "voice_error"
        v = parse_verdict(text)
        if v is None:
            _append(memory_system, VERDICTS_FILE, {
                "ts": _now_iso(), "lane": lane, "item_key": it["key"],
                "judge": name, "ok": False, "error": "parse_fail"})
            return "parse_fail"
        _append(memory_system, VERDICTS_FILE, {
            "ts": _now_iso(), "lane": lane, "item_key": it["key"],
            "judge": name, "ok": True, "verdict": v["verdict"],
            "confidence": v.get("confidence"),
            "evidence_ref": str(v.get("evidence_ref") or "")[:300],
            "evidence_note": str(v.get("evidence_note") or "")[:500],
            "reason": str(v.get("reason") or "")[:500]})
        return "ok"

    if tasks:
        workers = sum((parallel or VOICE_PARALLEL).get(n, 2)
                      for n in providers)
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, workers)) as ex:
            futs = [ex.submit(one, it, name) for it, name in tasks]
            for f in concurrent.futures.as_completed(futs):
                counters["asked"] += 1
                try:
                    counters[f.result()] += 1
                except Exception:
                    counters["voice_error"] += 1
    return counters


def resolve_items(items, memory_system, lane="main"):
    """Aggregate verdict rows → per-item outcome (brief rules)."""
    rows = []
    path = os.path.join(_events_dir(memory_system), VERDICTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if isinstance(r, dict) and r.get("ok") and \
                        r.get("lane") == lane:
                    rows.append(r)
    except OSError:
        pass
    latest = {}
    for r in rows:  # later row wins per (item, judge) — re-judging supersedes
        latest[(r.get("item_key"), r.get("judge"))] = r
    outcomes = []
    for it in items:
        votes = [r for (k, _j), r in latest.items() if k == it["key"]]
        keeps = sum(1 for r in votes if r.get("verdict") == "keep")
        ev_dangerous = [r for r in votes
                        if r.get("verdict") == "dangerous_wrong"
                        and _evidenced(r)]
        if len(votes) < MIN_DEFINITIVE:
            outcome = "unresolved"
        elif ev_dangerous:
            outcome = "dangerous_wrong"
        elif keeps >= KEEP_QUORUM:
            outcome = "keep"
        else:
            outcome = "noise"
        outcomes.append({"item": it, "outcome": outcome, "votes": votes,
                         "evidenced_dangerous": ev_dangerous})
    return outcomes


def gate_math(outcomes):
    resolved = [o for o in outcomes if o["outcome"] != "unresolved"]
    keep = sum(1 for o in resolved if o["outcome"] == "keep")
    noise = sum(1 for o in resolved if o["outcome"] == "noise")
    dangerous = sum(1 for o in resolved if o["outcome"] == "dangerous_wrong")
    keep_rate = (keep / len(resolved)) if resolved else 0.0
    if not resolved:
        verdict = "no_data"
    elif keep_rate >= ACTIVATE_KEEP and dangerous <= DANGEROUS_BUDGET:
        verdict = "activate"
    elif keep_rate < KILL_KEEP:
        verdict = "kill"
    else:
        verdict = "iterate"
    return {"resolved": len(resolved), "keep": keep, "noise": noise,
            "dangerous_wrong": dangerous,
            "unresolved": len(outcomes) - len(resolved),
            "keep_rate": round(keep_rate, 4), "verdict": verdict}


def render_summary(outcomes, gate, lane, rng=None):
    rng = rng or random.Random(0)  # deterministic sampling for re-renders
    keeps = [o for o in outcomes if o["outcome"] == "keep"]
    noises = [o for o in outcomes if o["outcome"] == "noise"]
    dangers = [o for o in outcomes if o["outcome"] == "dangerous_wrong"]
    unresolved = [o for o in outcomes if o["outcome"] == "unresolved"]

    def block(o):
        it = o["item"]
        lines = [f"- [{it.get('kind')}] {it.get('claim')}"]
        for r in o["votes"]:
            lines.append(f"    · {r.get('judge')}: {r.get('verdict')}"
                         f" — {r.get('reason') or ''}")
        for r in o["evidenced_dangerous"]:
            lines.append(f"    ! evidence ({r.get('judge')}): "
                         f"{r.get('evidence_ref')} — "
                         f"{r.get('evidence_note') or ''}")
        return "\n".join(lines)

    lines = [
        f"# M3 commission summary — lane {lane}",
        "",
        f"items: {len(outcomes)} · resolved: {gate['resolved']} · "
        f"unresolved (quorum<{MIN_DEFINITIVE}, gate-excluded, loud): "
        f"{gate['unresolved']}",
        f"keep: {gate['keep']} ({gate['keep_rate']:.0%}) · noise: "
        f"{gate['noise']} · dangerous-wrong: {gate['dangerous_wrong']} "
        f"(budget (a): ≤{DANGEROUS_BUDGET}/round)",
        "",
        f"## GATE VERDICT: {gate['verdict'].upper()} "
        f"(activate ≥{ACTIVATE_KEEP:.0%} keep AND dangerous ≤"
        f"{DANGEROUS_BUDGET}; kill <{KILL_KEEP:.0%}; between → iterate)",
        "",
        "## Dangerous-wrong (each with judge evidence)",
        ""]
    lines += [block(o) for o in dangers] or ["(none)"]
    lines += ["", f"## Random keep samples ({min(10, len(keeps))} of "
              f"{len(keeps)})", ""]
    lines += [block(o) for o in rng.sample(keeps, min(10, len(keeps)))] \
        or ["(none)"]
    lines += ["", f"## Random noise samples ({min(10, len(noises))} of "
              f"{len(noises)})", ""]
    lines += [block(o) for o in rng.sample(noises, min(10, len(noises)))] \
        or ["(none)"]
    if unresolved:
        lines += ["", f"## Unresolved ({len(unresolved)}) — voices "
                  "failed/quorum not met; re-run resumes them", ""]
        lines += [f"- {o['item'].get('claim')[:120]}" for o in unresolved]
    lines += [
        "",
        "---",
        "OWNER: одно слово — «включай» (activate) или «не верю» (тогда "
        "iterate/разбор). Плюс правило D4: пост-фактум смена бюджета = новый "
        "раунд, не ретро-пропуск.",
        "NB: verbatim claims/quotes — LOCAL material, never commit (NFR-4).",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lane", choices=("main", "agent"), default="main")
    ap.add_argument("--memory-system", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="judge only the first N items (smoke)")
    ap.add_argument("--judges", default="",
                    help="csv subset/override, e.g. gemini31,grok45")
    ap.add_argument("--summary-only", action="store_true",
                    help="recompute aggregation + summary from existing "
                         "verdict rows; no CLI calls")
    args = ap.parse_args()
    ms = args.memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))

    items, meta = _report.collect_lane_items(ms, args.lane)
    if args.limit:
        items = items[:args.limit]
    print(json.dumps({"stage": "collect", **meta,
                      "judged_now": len(items)}, ensure_ascii=False))

    if not args.summary_only:
        judges = _judges()
        if args.judges:
            names = [n.strip() for n in args.judges.split(",") if n.strip()]
            unknown = [n for n in names if n not in judges]
            if unknown:
                ap.error(f"unknown judges: {unknown}")
            judges = {n: judges[n] for n in names}
        counters = judge_items(items, ms, judges=judges, lane=args.lane)
        print(json.dumps({"stage": "judge", **counters}, ensure_ascii=False))

    outcomes = resolve_items(items, ms, lane=args.lane)
    gate = gate_math(outcomes)
    summary = render_summary(outcomes, gate, args.lane)
    out_path = os.path.join(_events_dir(ms), SUMMARY_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(json.dumps({"stage": "gate", **gate, "summary": out_path},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
