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
  codex53spark  gpt-5.3-codex-spark @ high via codex CLI, MAIN account. Exact
                                    route re-smoked live 2026-07-13 (rc=0,
                                    ROUTE_OK). `model_reasoning_summary=none`
                                    is pinned because Spark rejects detailed.

Spark xhigh is a second-pass ARBITER only for evidenced dangerous votes,
voice conflicts, low confidence, or missing quorum. It replaces Spark's own
high vote for that item; it is never counted as a fourth independent voice.

Each judge is told to REFUTE the card against live evidence (repos, git,
memory pages). Aggregation per item: `keep` needs ≥2/3 keep votes and zero
EVIDENCED dangerous verdicts; any dangerous verdict carrying a checkable
evidence_ref marks the item dangerous_wrong (one flaky judge without evidence
only casts a noise vote); fewer than 2 definitive votes = unresolved (loud,
gate-excluded). Fail-toward-reject throughout.

Transport reuses cli-council's `council.providers.invoke` (one fact, one
place for PTY/auth-preflight/prompt-file/extract quirks); the commission owns
only its judge-mode Provider profiles (tools ON — councils deliberately deny
them). Verdicts append to `events/m3_commission.jsonl` (resume-safe within an
exact round/config/phase: an ok tuple is never re-asked and transient attempts
are durably capped);
raw judge outputs to
`events/m3_commission_raw.jsonl`; the owner page to
`events/m3_commission_summary.md`. Everything under events/ is LOCAL material
(NFR-4). ZERO writes outside events/ — the commission marks, activation stays
its own owner-gated arc (NFR-2). A write-once per-round manifest freezes the
exact item universe before the first model call.
"""
import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import tempfile
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
MANIFEST_PREFIX = "m3_commission_manifest."
MANIFEST_SCHEMA = "m3_commission_manifest.v1"
JUDGE_CONFIG_VERSION = "m3-judge-config.v2"

VERDICT_ENUM = ("keep", "noise", "dangerous_wrong")
KEEP_QUORUM = 2          # of 3 voices
MIN_DEFINITIVE = 2       # below → unresolved, gate-excluded, loud
ACTIVATE_KEEP = 0.70     # D4 math, unchanged
KILL_KEEP = 0.50
DANGEROUS_BUDGET = 1     # frozen option (a), 2026-07-12
DEFAULT_ROUND_ID = "m3-d5-spark-high-v1"
DEFAULT_JUDGE_NAMES = ("gemini31", "grok45", "codex53spark")
DEFAULT_WINDOW_ITEMS = 5
VOICE_ERROR_CIRCUIT = 2
MAX_ATTEMPTS_PER_PHASE = 2
LOW_CONFIDENCE_THRESHOLD = 0.75
BASELINE_PHASE = "baseline"
ESCALATION_PHASE = "escalation"
SPARK_JUDGE = "codex53spark"
SPARK_ESCALATION_EFFORT = "xhigh"

JUDGE_MODELS = {
    "gemini31": "antigravity_cli/gemini-3.1-pro-high",
    "grok45": "grok_cli/grok-4.5",
    "codex53spark": "codex_cli/gpt-5.3-codex-spark",
}

JUDGE_EFFORTS = {
    "gemini31": "high",
    "grok45": "max",
    SPARK_JUDGE: "high",
}

# Per-voice call ceilings and parallelism (agy is the slowest house).
VOICE_TIMEOUT = {
    "gemini31": 600.0,
    "grok45": 600.0,
    "codex53spark": 600.0,
}
VOICE_PARALLEL = {
    # Antigravity and Spark are admitted here as attended single-shot routes;
    # do not turn either subscription CLI into an unbounded batch surface.
    "gemini31": 1,
    "grok45": 2,
    # Only single-call liveness is currently proven for Spark in this task.
    "codex53spark": 1,
}

_ROUND_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

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


def _validate_round_id(round_id):
    if not isinstance(round_id, str) or not _ROUND_ID_RE.fullmatch(round_id):
        raise ValueError(
            "round_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    return round_id


def _judge_model(judge_name):
    return JUDGE_MODELS.get(judge_name, judge_name)


def _judge_effort(judge_name, phase=BASELINE_PHASE):
    if judge_name == SPARK_JUDGE and phase == ESCALATION_PHASE:
        return SPARK_ESCALATION_EFFORT
    return JUDGE_EFFORTS.get(judge_name, "default")


def _judge_config(judge_name, phase=BASELINE_PHASE, effort=None):
    expected_effort = effort or _judge_effort(judge_name, phase)
    return ":".join((JUDGE_CONFIG_VERSION, _judge_model(judge_name),
                     expected_effort, phase))


def _judge_identity_matches(row, judge_name, phase, effort=None):
    expected_effort = effort or _judge_effort(judge_name, phase)
    return row.get("judge") == judge_name and \
        row.get("judge_model") == _judge_model(judge_name) and \
        row.get("judge_phase") == phase and \
        row.get("judge_effort") == expected_effort and \
        row.get("judge_config") == _judge_config(
            judge_name, phase, expected_effort)


def _roster_matches(row, active_judges):
    """A subset smoke is not evidence for a full-roster commission."""
    roster = row.get("roster")
    active = tuple(active_judges)
    return isinstance(roster, list) and len(roster) == len(active) and \
        set(roster) == set(active)


def _manifest_path(memory_system, round_id):
    return os.path.join(
        _events_dir(memory_system), f"{MANIFEST_PREFIX}{round_id}.json")


def _validate_manifest(manifest, lane, round_id, roster):
    if not isinstance(manifest, dict) or \
            manifest.get("schema") != MANIFEST_SCHEMA:
        raise RuntimeError("invalid M3 commission manifest schema")
    if manifest.get("lane") != lane or manifest.get("round_id") != round_id:
        raise RuntimeError("M3 commission manifest lane/round mismatch")
    if not _roster_matches({"roster": manifest.get("roster")}, roster):
        raise RuntimeError("M3 commission manifest roster mismatch")
    expected_configs = {
        name: _judge_config(name, BASELINE_PHASE) for name in roster}
    if manifest.get("judge_configs") != expected_configs:
        raise RuntimeError("M3 commission manifest judge config mismatch")
    expected_arbiter = _judge_config(
        SPARK_JUDGE, ESCALATION_PHASE) if SPARK_JUDGE in roster else None
    if manifest.get("arbiter_config") != expected_arbiter:
        raise RuntimeError("M3 commission manifest arbiter config mismatch")
    items = manifest.get("items")
    if not isinstance(items, list):
        raise RuntimeError("M3 commission manifest items must be a list")
    keys = [it.get("key") for it in items if isinstance(it, dict)]
    if len(keys) != len(items) or any(not key for key in keys) or \
            len(set(keys)) != len(keys):
        raise RuntimeError("M3 commission manifest item keys are invalid")
    if manifest.get("item_count") != len(items):
        raise RuntimeError("M3 commission manifest item_count mismatch")
    source_meta = manifest.get("source_meta") or {}
    source_policy = manifest.get("source_policy")
    if source_policy is not None and \
            source_policy != source_meta.get("miner_policy"):
        raise RuntimeError("M3 commission manifest source policy mismatch")
    return manifest


def _round_has_rows(memory_system, lane, round_id):
    path = os.path.join(_events_dir(memory_system), VERDICTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get("lane") == lane and \
                        row.get("round_id") == round_id:
                    return True
    except OSError:
        pass
    return False


def load_or_create_round_manifest(memory_system, lane, round_id, roster,
                                  items, source_meta=None):
    """Freeze exact judge inputs once per round, before any model call.

    The final path is created with an atomic hard-link from a fully fsynced
    private temp file. Existing manifests are never replaced. A legacy round
    that already has verdict rows but no manifest fails closed: freezing a
    moving corpus after calls would manufacture false audit integrity.
    """
    round_id = _validate_round_id(round_id)
    roster = tuple(roster)
    path = _manifest_path(memory_system, round_id)
    try:
        with open(path, encoding="utf-8") as f:
            return _validate_manifest(json.load(f), lane, round_id, roster)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read M3 commission manifest: {exc}")

    if _round_has_rows(memory_system, lane, round_id):
        raise RuntimeError(
            "commission round already has rows but no frozen manifest; "
            "start a new round-id")

    events = _events_dir(memory_system)
    os.makedirs(events, mode=0o700, exist_ok=True)
    frozen_source_meta = dict(source_meta or {})
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_at": _now_iso(),
        "lane": lane,
        "round_id": round_id,
        "roster": list(roster),
        "judge_configs": {
            name: _judge_config(name, BASELINE_PHASE) for name in roster},
        "arbiter_config": _judge_config(
            SPARK_JUDGE, ESCALATION_PHASE)
        if SPARK_JUDGE in roster else None,
        "item_count": len(items),
        "source_policy": frozen_source_meta.get("miner_policy"),
        "source_meta": frozen_source_meta,
        "items": list(items),
    }
    payload = (json.dumps(manifest, ensure_ascii=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(
        prefix=f".{MANIFEST_PREFIX}{round_id}.", dir=events)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = -1
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        try:
            dir_fd = os.open(events, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        with open(path, encoding="utf-8") as f:
            return _validate_manifest(json.load(f), lane, round_id, roster)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot finalize M3 commission manifest: {exc}")


def _append(memory_system, fname, row):
    if _LC is None:
        return False
    try:
        return bool(_LC._atomic_append_jsonl(
            Path(os.path.join(_events_dir(memory_system), fname)),
            _LC._compact_json(row)))
    except Exception:
        return False


def _judges(spark_effort="high"):
    """Judge-mode Provider profiles on cli-council's transport. Built lazily:
    the eidetic CI has no cli-council checkout, and tests patch _invoke_voice
    below this seam."""
    if spark_effort not in ("high", "xhigh"):
        raise ValueError("Spark effort must be high or xhigh")
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
        # MAIN codex account — owner-picked Spark daily route. Pin every
        # relevant knob so a user-editable default/profile cannot silently
        # change the judge identity. Read-only + ephemeral prevents a judge
        # from mutating evidence or polluting persistent Codex history; its
        # own hooks are disabled to avoid the memory system observing itself.
        "codex53spark": Provider(
            name="codex53spark", bin="codex", family="openai",
            argv=["codex", "-a", "never", "exec",
                  "--skip-git-repo-check", "-s", "read-only", "--ephemeral",
                  "--color", "never", "-m", "gpt-5.3-codex-spark",
                  "-c", f'model_reasoning_effort="{spark_effort}"',
                  "-c", 'model_reasoning_summary="none"',
                  "-c", "features.codex_hooks=false", "-"],
            timeout=VOICE_TIMEOUT["codex53spark"]),
    }


def _xhigh_arbiter():
    return _judges(spark_effort=SPARK_ESCALATION_EFFORT)[SPARK_JUDGE]


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
    ref = str(v.get("evidence_ref") or "").strip()
    if ref.lower() in ("", "none", "n/a", "null", "-"):
        return False
    targets = re.split(r"\s+(?:\||and)\s+", ref)
    if not targets:
        return False
    for target in targets:
        target = re.sub(r"\s+\(.*\)\s*$", "", target.strip())
        lowered = target.lower()
        if lowered.startswith("git:"):
            if not re.fullmatch(r"git:[0-9a-f]{7,64}", lowered):
                return False
            continue
        if lowered.startswith("memory:"):
            if not re.fullmatch(r"memory:[a-z0-9][a-z0-9._/-]{0,255}",
                                lowered):
                return False
            continue
        path = re.sub(r":\d+(?:-\d+)?$", "", target)
        if not path or any(ch.isspace() for ch in path) or \
                not (path.startswith(("/", "~/", "./", "../")) or
                     "/" in path or "." in os.path.basename(path)):
            return False
    return True


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


def build_escalation_prompt(item):
    baseline = item.get("_baseline_votes") or []
    reasons = item.get("_escalation_reasons") or []
    return build_prompt(item) + "\n\n" + (
        "ESCALATION PASS: you are the same OpenAI commission voice rerun at "
        "xhigh, not a fourth vote. Re-check the live evidence and replace "
        "your high-effort vote. Escalation reasons: "
        f"{json.dumps(reasons, ensure_ascii=False)}. Baseline commission "
        "votes (context only; verify them independently): "
        f"{json.dumps(baseline, ensure_ascii=False)}. "
        "Output ONLY the one JSON object from the schema above."
    )


def load_done(memory_system, lane, round_id=DEFAULT_ROUND_ID,
              active_judges=None, phase=BASELINE_PHASE,
              effort_overrides=None):
    """Ok (item_key, judge) pairs in this exact round/roster/config/phase."""
    round_id = _validate_round_id(round_id)
    active_roster = tuple(active_judges) \
        if active_judges is not None else None
    active = set(active_roster) if active_roster is not None else None
    efforts = dict(effort_overrides or {})
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
                        row.get("lane") == lane and \
                        row.get("round_id") == round_id and \
                        row.get("item_key") and row.get("judge") and \
                        _judge_identity_matches(
                            row, row.get("judge"), phase,
                            efforts.get(row.get("judge"))) and \
                        (active is None or (
                            row.get("judge") in active and
                            _roster_matches(row, active_roster))):
                    done.add((row["item_key"], row["judge"]))
    except OSError:
        pass
    return done


def _load_attempt_counts(memory_system, lane, round_id, active_judges,
                         phase, effort_overrides=None):
    roster = tuple(active_judges)
    active = set(roster)
    efforts = dict(effort_overrides or {})
    counts = {}
    path = os.path.join(_events_dir(memory_system), VERDICTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                judge = row.get("judge") if isinstance(row, dict) else None
                if isinstance(row, dict) and row.get("lane") == lane and \
                        row.get("round_id") == round_id and \
                        row.get("item_key") and judge in active and \
                        _roster_matches(row, roster) and \
                        _judge_identity_matches(
                            row, judge, phase, efforts.get(judge)):
                    pair = (row["item_key"], judge)
                    counts[pair] = counts.get(pair, 0) + 1
    except OSError:
        pass
    return counts


def _load_ok_rows(memory_system, lane, round_id, active_judges, phase,
                  effort_overrides=None):
    roster = tuple(active_judges)
    active = set(roster)
    efforts = dict(effort_overrides or {})
    rows = []
    path = os.path.join(_events_dir(memory_system), VERDICTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                judge = row.get("judge") if isinstance(row, dict) else None
                if isinstance(row, dict) and row.get("ok") and \
                        row.get("lane") == lane and \
                        row.get("round_id") == round_id and \
                        judge in active and _roster_matches(row, roster) and \
                        _judge_identity_matches(
                            row, judge, phase, efforts.get(judge)):
                    rows.append(row)
    except OSError:
        pass
    return rows


def judge_items(items, memory_system, judges=None, lane="main",
                parallel=None, round_id=DEFAULT_ROUND_ID,
                window_items=DEFAULT_WINDOW_ITEMS, phase=BASELINE_PHASE,
                effort_overrides=None, ledger_roster=None,
                prompt_builder=None,
                max_attempts=MAX_ATTEMPTS_PER_PHASE):
    """Judge bounded item windows with per-voice parallelism caps.

    A voice with repeated transport failures is circuit-broken for the rest of
    this process; its transient rows are re-asked on the next run. This keeps a
    provider outage from consuming the entire commission. → counters dict.
    """
    round_id = _validate_round_id(round_id)
    if phase not in (BASELINE_PHASE, ESCALATION_PHASE):
        raise ValueError("unknown judge phase")
    if not isinstance(window_items, int) or window_items < 1:
        raise ValueError("window_items must be a positive integer")
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    items = list(items)
    providers = judges if judges is not None else _judges()
    roster = tuple(ledger_roster if ledger_roster is not None else providers)
    if not set(providers).issubset(set(roster)):
        raise ValueError("active providers must be members of ledger_roster")
    efforts = {name: (effort_overrides or {}).get(
        name, _judge_effort(name, phase)) for name in providers}
    done = load_done(memory_system, lane, round_id=round_id,
                     active_judges=roster, phase=phase,
                     effort_overrides=efforts)
    attempts = _load_attempt_counts(
        memory_system, lane, round_id, roster, phase,
        effort_overrides=efforts)
    sems = {name: threading.BoundedSemaphore(
        (parallel or VOICE_PARALLEL).get(name, 2)) for name in providers}
    pairs = [(it["key"], name) for it in items for name in providers]
    capped = {pair for pair in pairs if pair not in done and
              attempts.get(pair, 0) >= max_attempts}
    counters = {"asked": 0, "ok": 0, "voice_error": 0, "parse_fail": 0,
                "skipped_done": sum(1 for pair in pairs if pair in done),
                "skipped_attempt_cap": len(capped),
                "skipped_circuit": 0, "windows": 0, "circuit_open": 0}
    make_prompt = prompt_builder or build_prompt

    def one(it, name):
        prov = providers[name]
        timeout = VOICE_TIMEOUT.get(name, 600.0)
        try:
            with sems[name]:
                ok, text = _invoke_voice(
                    name, prov, make_prompt(it), timeout)
        except Exception as exc:
            ok, text = False, f"{type(exc).__name__}: {exc}"
        identity = {
            "judge": name,
            "judge_model": _judge_model(name),
            "judge_effort": efforts[name],
            "judge_phase": phase,
            "judge_config": _judge_config(name, phase, efforts[name]),
        }
        escalation_meta = {}
        if phase == ESCALATION_PHASE:
            escalation_meta["escalation_reasons"] = list(
                it.get("_escalation_reasons") or [])
        _append(memory_system, RAW_FILE, {
            "ts": _now_iso(), "lane": lane, "round_id": round_id,
            "roster": list(roster), "item_key": it["key"],
            **identity, **escalation_meta,
            "ok": ok, "raw": (text or "")[:20000]})
        if not ok:
            _append(memory_system, VERDICTS_FILE, {
                "ts": _now_iso(), "lane": lane, "round_id": round_id,
                "roster": list(roster), "item_key": it["key"],
                **identity, **escalation_meta,
                "ok": False, "error": (text or "")[:300]})
            return "voice_error"
        v = parse_verdict(text)
        if v is None:
            _append(memory_system, VERDICTS_FILE, {
                "ts": _now_iso(), "lane": lane, "round_id": round_id,
                "roster": list(roster), "item_key": it["key"],
                **identity, **escalation_meta,
                "ok": False, "error": "parse_fail"})
            return "parse_fail"
        _append(memory_system, VERDICTS_FILE, {
            "ts": _now_iso(), "lane": lane, "round_id": round_id,
            "roster": list(roster), "item_key": it["key"],
            **identity, **escalation_meta,
            "ok": True, "verdict": v["verdict"],
            "confidence": v.get("confidence"),
            "evidence_ref": str(v.get("evidence_ref") or "")[:300],
            "evidence_note": str(v.get("evidence_note") or "")[:500],
            "reason": str(v.get("reason") or "")[:500]})
        return "ok"

    workers = sum((parallel or VOICE_PARALLEL).get(n, 2)
                  for n in providers)
    circuited = set()
    failure_streak = {name: 0 for name in providers}
    for start in range(0, len(items), window_items):
        window = items[start:start + window_items]
        eligible = [(it, name) for it in window for name in providers
                    if (it["key"], name) not in done and
                    (it["key"], name) not in capped]
        tasks = [(it, name) for it, name in eligible
                 if name not in circuited]
        counters["skipped_circuit"] += len(eligible) - len(tasks)
        if not tasks:
            continue
        counters["windows"] += 1
        statuses = {name: [] for name in providers}
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, workers)) as ex:
            futs = {ex.submit(one, it, name): name for it, name in tasks}
            for f in concurrent.futures.as_completed(futs):
                name = futs[f]
                counters["asked"] += 1
                try:
                    status = f.result()
                except Exception:
                    status = "voice_error"
                counters[status] += 1
                statuses[name].append(status)
        for name, voice_statuses in statuses.items():
            if not voice_statuses:
                continue
            if all(status == "voice_error" for status in voice_statuses):
                failure_streak[name] += len(voice_statuses)
            else:
                failure_streak[name] = 0
            if failure_streak[name] >= VOICE_ERROR_CIRCUIT:
                circuited.add(name)
        counters["circuit_open"] = len(circuited)
    return counters


def resolve_items(items, memory_system, lane="main",
                  round_id=DEFAULT_ROUND_ID, active_judges=None):
    """Aggregate baseline plus optional Spark-xhigh replacement votes."""
    round_id = _validate_round_id(round_id)
    active_roster = tuple(DEFAULT_JUDGE_NAMES if active_judges is None
                          else active_judges)
    baseline_rows = _load_ok_rows(
        memory_system, lane, round_id, active_roster, BASELINE_PHASE)
    escalation_rows = _load_ok_rows(
        memory_system, lane, round_id, active_roster, ESCALATION_PHASE,
        effort_overrides={SPARK_JUDGE: SPARK_ESCALATION_EFFORT})
    baseline_latest = {}
    for row in baseline_rows:
        baseline_latest[(row.get("item_key"), row.get("judge"))] = row
    escalation_latest = {}
    for row in escalation_rows:
        escalation_latest[(row.get("item_key"), row.get("judge"))] = row
    outcomes = []
    for it in items:
        baseline_votes = [baseline_latest[(it["key"], name)]
                          for name in active_roster
                          if (it["key"], name) in baseline_latest]
        selected = {row["judge"]: row for row in baseline_votes}
        escalation_vote = escalation_latest.get((it["key"], SPARK_JUDGE))
        if escalation_vote is not None and SPARK_JUDGE in active_roster:
            selected[SPARK_JUDGE] = escalation_vote
        votes = [selected[name] for name in active_roster if name in selected]
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
                         "baseline_votes": baseline_votes,
                         "escalation_vote": escalation_vote,
                         "evidenced_dangerous": ev_dangerous})
    return outcomes


def _confidence(row):
    value = row.get("confidence")
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def _normalized_vote(row):
    if row.get("verdict") == "dangerous_wrong" and not _evidenced(row):
        return "noise"
    return row.get("verdict")


def select_escalation_items(items, memory_system, lane="main",
                            round_id=DEFAULT_ROUND_ID,
                            active_judges=None,
                            confidence_threshold=LOW_CONFIDENCE_THRESHOLD):
    """Return item copies annotated for the bounded Spark-xhigh pass."""
    active_roster = tuple(DEFAULT_JUDGE_NAMES if active_judges is None
                          else active_judges)
    rows = _load_ok_rows(
        memory_system, lane, round_id, active_roster, BASELINE_PHASE)
    latest = {}
    for row in rows:
        latest[(row.get("item_key"), row.get("judge"))] = row
    selected = []
    for item in items:
        votes = [latest[(item["key"], name)] for name in active_roster
                 if (item["key"], name) in latest]
        reasons = []
        if any(row.get("verdict") == "dangerous_wrong" and _evidenced(row)
               for row in votes):
            reasons.append("evidenced_dangerous")
        if len(votes) < MIN_DEFINITIVE:
            reasons.append("missing_quorum")
        normalized = {_normalized_vote(row) for row in votes}
        if len(normalized) > 1:
            reasons.append("voice_conflict")
        if any(_confidence(row) is None or
               _confidence(row) < confidence_threshold for row in votes):
            reasons.append("low_confidence")
        if not reasons:
            continue
        escalated = dict(item)
        escalated["_escalation_reasons"] = reasons
        escalated["_baseline_votes"] = [{
            "judge": row.get("judge"),
            "verdict": row.get("verdict"),
            "confidence": row.get("confidence"),
            "evidence_ref": row.get("evidence_ref"),
            "reason": row.get("reason"),
        } for row in votes]
        selected.append(escalated)
    return selected


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


def apply_coverage_gate(gate, total_items):
    """A partial commission can show provisional math but cannot activate."""
    out = dict(gate)
    total = max(0, int(total_items))
    resolved = min(out.get("resolved", 0), total)
    out["total_items"] = total
    out["coverage_rate"] = round(resolved / total, 4) if total else 0.0
    out["partial"] = resolved < total
    if out["partial"] and resolved:
        out["provisional_verdict"] = out["verdict"]
        out["verdict"] = "smoke_only"
    return out


def render_summary(outcomes, gate, lane, rng=None,
                   round_id=DEFAULT_ROUND_ID, active_judges=None):
    rng = rng or random.Random(0)  # deterministic sampling for re-renders
    roster = tuple(DEFAULT_JUDGE_NAMES if active_judges is None
                   else active_judges)
    keeps = [o for o in outcomes if o["outcome"] == "keep"]
    noises = [o for o in outcomes if o["outcome"] == "noise"]
    dangers = [o for o in outcomes if o["outcome"] == "dangerous_wrong"]
    unresolved = [o for o in outcomes if o["outcome"] == "unresolved"]

    def block(o):
        it = o["item"]
        lines = [f"- [{it.get('kind')}] {it.get('claim')}"]
        for r in o["votes"]:
            lines.append(f"    · {r.get('judge')}@"
                         f"{r.get('judge_effort')}/"
                         f"{r.get('judge_phase')}: {r.get('verdict')}"
                         f" — {r.get('reason') or ''}")
        for r in o["evidenced_dangerous"]:
            lines.append(f"    ! evidence ({r.get('judge')}): "
                         f"{r.get('evidence_ref')} — "
                         f"{r.get('evidence_note') or ''}")
        return "\n".join(lines)

    coverage = (f"coverage: {gate['resolved']}/{gate['total_items']} "
                f"({gate['coverage_rate']:.0%})") \
        if "total_items" in gate else None
    if gate.get("partial") and gate.get("resolved"):
        gate_line = (
            "## GATE VERDICT: SMOKE_ONLY "
            f"(provisional math: {gate.get('provisional_verdict', '?').upper()}; "
            "full decision disabled until every item has quorum)")
        owner_line = (
            "OWNER: решения пока нет — это partial smoke; продолжить окна до "
            "полного quorum, затем применять D4.")
    else:
        gate_line = (
            f"## GATE VERDICT: {gate['verdict'].upper()} "
            f"(activate ≥{ACTIVATE_KEEP:.0%} keep AND dangerous ≤"
            f"{DANGEROUS_BUDGET}; kill <{KILL_KEEP:.0%}; between → iterate)")
        owner_line = (
            "OWNER: одно слово — «включай» (activate) или «не верю» (тогда "
            "iterate/разбор). Плюс правило D4: пост-фактум смена бюджета = "
            "новый раунд, не ретро-пропуск.")

    lines = [
        f"# M3 commission summary — lane {lane}",
        "",
        f"round: `{round_id}`",
        "roster: " + ", ".join(
            f"`{name}` ({_judge_model(name)}@"
            f"{_judge_effort(name, BASELINE_PHASE)})" for name in roster),
        "",
    ]
    if coverage:
        lines += [coverage, ""]
    lines += [
        f"items: {len(outcomes)} · resolved: {gate['resolved']} · "
        f"unresolved (quorum<{MIN_DEFINITIVE}, gate-excluded, loud): "
        f"{gate['unresolved']}",
        f"keep: {gate['keep']} ({gate['keep_rate']:.0%}) · noise: "
        f"{gate['noise']} · dangerous-wrong: {gate['dangerous_wrong']} "
        f"(budget (a): ≤{DANGEROUS_BUDGET}/round)",
        f"Spark xhigh escalations applied: "
        f"{sum(1 for o in outcomes if o.get('escalation_vote'))} "
        "(replaces Spark high; never a fourth quorum vote)",
        "",
        gate_line,
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
        owner_line,
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
    ap.add_argument("--round-id", default=DEFAULT_ROUND_ID,
                    help="stable append-only commission round identifier")
    ap.add_argument("--window-items", type=int, default=DEFAULT_WINDOW_ITEMS,
                    help="items per attended provider window (default: 5)")
    ap.add_argument("--summary-only", action="store_true",
                    help="recompute aggregation + summary from existing "
                         "verdict rows; no CLI calls")
    ap.add_argument("--no-escalation", action="store_true",
                    help="do not launch new Spark xhigh calls (existing "
                         "exact-round escalation rows still resolve)")
    args = ap.parse_args()
    try:
        round_id = _validate_round_id(args.round_id)
    except ValueError as exc:
        ap.error(str(exc))
    if args.window_items < 1:
        ap.error("--window-items must be a positive integer")
    ms = args.memory_system or os.environ.get(
        "EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))

    requested = [n.strip() for n in args.judges.split(",") if n.strip()] \
        if args.judges else list(DEFAULT_JUDGE_NAMES)
    if not requested:
        ap.error("--judges must name at least one judge")
    unknown = [n for n in requested if n not in DEFAULT_JUDGE_NAMES]
    if unknown:
        ap.error(f"unknown judges: {unknown}")
    requested_set = set(requested)
    names = [name for name in DEFAULT_JUDGE_NAMES
             if name in requested_set]

    collected, meta = _report.collect_lane_items(ms, args.lane)
    try:
        manifest = load_or_create_round_manifest(
            ms, args.lane, round_id, names, collected, source_meta=meta)
    except RuntimeError as exc:
        ap.error(str(exc))
    round_items = list(manifest["items"])
    items = round_items[:args.limit] if args.limit else round_items
    print(json.dumps({
        "stage": "collect",
        "source_items_now": meta.get("items"),
        "round_items": len(round_items),
        "manifest": _manifest_path(ms, round_id),
        "judged_now": len(items),
    }, ensure_ascii=False))

    if not args.summary_only:
        all_judges = _judges()
        judges = {n: all_judges[n] for n in names}
        counters = judge_items(items, ms, judges=judges, lane=args.lane,
                               round_id=round_id,
                               window_items=args.window_items)
        print(json.dumps({"stage": "judge", "round_id": round_id,
                          **counters}, ensure_ascii=False))
        if not args.no_escalation and tuple(names) == DEFAULT_JUDGE_NAMES:
            escalation_items = select_escalation_items(
                items, ms, lane=args.lane, round_id=round_id,
                active_judges=names)
            arbiter_counters = judge_items(
                escalation_items, ms,
                judges={SPARK_JUDGE: _xhigh_arbiter()},
                lane=args.lane, round_id=round_id,
                # One item/window makes the process-local failure circuit
                # effective before more xhigh quota is consumed.
                window_items=1,
                phase=ESCALATION_PHASE,
                effort_overrides={
                    SPARK_JUDGE: SPARK_ESCALATION_EFFORT},
                ledger_roster=names,
                prompt_builder=build_escalation_prompt)
            print(json.dumps({
                "stage": "escalation",
                "round_id": round_id,
                "candidates": len(escalation_items),
                **arbiter_counters,
            }, ensure_ascii=False))

    outcomes = resolve_items(items, ms, lane=args.lane, round_id=round_id,
                             active_judges=names)
    gate = apply_coverage_gate(gate_math(outcomes), len(round_items))
    summary = render_summary(outcomes, gate, args.lane, round_id=round_id,
                             active_judges=names)
    out_path = os.path.join(_events_dir(ms), SUMMARY_FILE)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(json.dumps({"stage": "gate", **gate, "summary": out_path},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
