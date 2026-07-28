#!/usr/bin/env python3
"""Miner v3 — the session-transcript knowledge miner (two lanes, one prompt).

Mines a Claude-Code session transcript for candidates of four kinds:

  * `recall` (asked-for OR assistant-volunteered, D6) → the CONSOLIDATION lane:
    the producer (`m3_producer_driver.resolve_sources`) retrieves `/memory/`
    spans itself and the judge verifies claim ⊨ span — a miner confabulation
    cannot launder a citation by construction.
  * `decision` / `finding` / `rule` (D3, ADR-0001) → the ACQUISITION lane
    (`m3_acquisition`, dark day-1): candidate = {claim, transcript_quote};
    the quote is mechanically verified against the SAME transcript, then the
    judge checks claim ⊨ quote. No quote → the candidate dies pre-judge.

The miner never asserts sources in either lane; the worst a bad candidate can
do is burn one judge call and get rejected. Transcript reading follows the
session-signals.sh discipline: parse the JSONL tail, keep only real
user/assistant TEXT turns (never raw tool output), strip injected
<system-reminder> blocks, bound every turn and the total. Extraction goes
through the shared SDK (`structured_classification` task, wrapper-object JSON
per the batch-array collapse guard); the model is told to copy, not to
compose. Dark by default: the driver flag (EIDETIC_M3_DRIVER) gates the only
production caller; running this module by hand just prints candidates.
"""
import argparse
import json
import os
import re
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_judge  # noqa: E402  (_norm_tokens — the one normalization dialect, NFR-6)

TAIL_BYTES = 2 * 1024 * 1024      # spans past tool-dump walls (session-signals lesson)
MAX_TURNS = 30                    # last N user/assistant text turns
TURN_CAP = 2000                   # chars per turn in the prompt
MAX_CANDIDATES = 4                # TOTAL across kinds — no per-lane quota (brief §3)
MIN_ANSWER_CHARS = 80
MIN_CLAIM_CHARS = 40
MIN_QUOTE_CHARS = 40
MIN_QUOTE_TOKENS = 6              # the judge's verbatim-gate floor (_quote_ok)
KIND_RECALL = "recall"
ACQ_KINDS = ("decision", "finding", "rule")
MINER_POLICY_VERSION = "m3-miner-v4-durable-acquisition"

# A cheap fail-toward-miss rail for the exact classes that made the first D5
# commission unsafe.  The model prompt remains the broad semantic selector;
# this deterministic layer prevents an obvious recommendation/status/live
# route from becoming a persistent card even when the extractor ignores it.
_NEXT_STEP_RE = re.compile(
    r"(?:\bnext\s+(?:step|stage|session)\b|"
    r"\bwhat\s+to\s+do\s+next\b|"
    r"следующ\w*\s+(?:этап|сесси\w*|шаг)|"
    r"план\s+следующ\w*\s+сесси\w*|"
    r"дальше\s+(?:будет|—|-|:))",
    re.IGNORECASE,
)
_RECOMMENDATION_RE = re.compile(
    r"(?:\brecommend(?:ation|ed|ing|s)?\b|"
    r"\bpropos(?:e|ed|al|ing)\b|"
    r"\bsuggest(?:ed|ion|ing|s)?\b|"
    r"\bрекоменд\w*\b|\bпредлаг\w*\b|\bпредлож\w*\b|"
    r"\bсовет(?:ую|уем|овать)\b)",
    re.IGNORECASE,
)
_TASK_STATUS_RE = re.compile(
    r"(?:\bstatus\s*[:=]?\s*(?:completed|done|finished|pending|awaiting)\b|"
    r"\b(?:completed|finished)\s+(?:successfully|today)\b|"
    r"\b(?:выполнен\w*|заверш[её]н\w*|закрыт\w*)\b)",
    re.IGNORECASE,
)
_CONFIG_RE = re.compile(
    r"\b(?:config(?:uration)?|settings?|parameters?|options?|"
    r"конфиг\w*|настройк\w*|параметр\w*|опци\w*|"
    r"model_[a-z0-9_]+|reasoning[._][a-z0-9_]+)\b",
    re.IGNORECASE,
)
_MODEL_ROUTE_RE = re.compile(
    r"(?:\bmodel(?:\b|_)|\bмодел\w*|\bprovider\b|\bпровайдер\w*|"
    r"\broute\b|\bмаршрут\w*|\bversion\b|\bверси\w*|"
    r"\bfeature\s+flag\b|\bфлаг\w*|\bbase\b|\bбаз\w*|"
    r"\bgpt-[a-z0-9._-]+|\bcodex\b|\bspark\b)",
    re.IGNORECASE,
)
_MODEL_AVAILABILITY_RE = re.compile(
    r"\b(?:unavailable|available|unsupported|dead|works?|"
    r"недоступ\w*|доступ\w*|не\s+поддерж\w*|умер\w*|работает|работают)\b",
    re.IGNORECASE,
)

_SYSREM_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_LOCALCMD_RE = re.compile(r"<local-command-[^>]*>.*?</local-command-[^>]*>", re.DOTALL)

SYSTEM = """You mine a coding-session transcript for knowledge worth filing into a personal memory wiki. You are invoked by a script; your entire output is parsed as JSON.

Every candidate carries a "kind": "recall" | "decision" | "finding" | "rule".

=== kind "recall" — remembered knowledge got USED (consolidation) ===
A recall candidate exists in either form:
(a) ASKED-FOR: the USER asked about past facts (what happened, what was decided, how something is configured, why something was done, what the state of X is — not a request to write/change code now, not chit-chat) AND the ASSISTANT answered by STATING SPECIFIC remembered facts — names, numbers, dates, versions, decisions, causes — asserted as established knowledge.
(b) VOLUNTEERED: the ASSISTANT stated remembered facts UNPROMPTED — no user question about the past; it recited prior knowledge to orient the work ("as decided earlier…", "по памяти это…", "the standing rule is…"). The facts must be asserted as ALREADY-ESTABLISHED (prior sessions, standing decisions, known configuration) — NOT knowledge the assistant just produced in this session.
NOT recall (either form): plans, proposals, questions back, work narration ("I ran/fixed/created…"), or facts the assistant visibly derived right there by reading files / running commands in the shown exchange.

GROUNDED-SUBSET RULE (the filing gate rejects the WHOLE answer if ANY fact is unsupported — extract ONLY the memory-grounded subset):
An assistant response often MIXES recalled knowledge with session-derived facts. You MUST separate them:
- KEEP: facts the assistant states as prior knowledge, configuration, decisions, architecture, rules — how things ARE or WERE, established before this session.
- DROP: facts the assistant obtained THIS SESSION by reading files, running commands, checking tool output, or reasoning from in-session observations ("I checked and found…", "The output shows…"). Also drop: plans, proposals, current-session work narration.
If only 1-2 recalled facts survive the filter, that is fine — a short recalled_answer that passes the gate is worth more than a long mixed one that gets rejected.

recall fields:
- recall_query: asked-for → the user's actual question, condensed, in the user's own language. Volunteered → a retrieval query YOU formulate naming the topic of the recalled facts (what one would search the wiki with to find them), in the language of those facts.
- recalled_answer: ONLY the memory-grounded facts the ASSISTANT stated, near-verbatim, condensed to 1-5 factual sentences. Facts that appear only in a [USER] message (a pasted status, log, or the user's own summary) are NOT recall evidence — never build recalled_answer from them. Drop greetings, hedges, markdown, tables.

=== kinds "decision" / "finding" / "rule" — NEW knowledge born this session (acquisition) ===
Emit when the ASSISTANT's own message ESTABLISHED something new and stated it:
- "decision": a choice was made and stated — what was chosen (include the why when stated).
- "finding": a fact was established — a root cause found, a measurement taken, a behavior verified, a bug identified.
- "rule": a standing rule/constraint/policy was stated as how things work from now on.
acquisition fields:
- claim: the knowledge, near-verbatim, condensed to 1-3 sentences, SELF-CONTAINED (a reader without the transcript must understand it — name the system/component, never "it"/"this").
- transcript_quote: a VERBATIM CONTIGUOUS substring copied character-for-character from ONE ASSISTANT message in the excerpt that states this claim — same language, same punctuation, same markdown; no paraphrase, no stitching from two places. At least 10 words. If you cannot copy such a fragment exactly, DO NOT emit the candidate.
Only ASSISTANT messages are quotable — never [USER] messages (pasted logs and documents live there).
Prefer claims NOT contradicted or corrected later in the excerpt; when a claim was corrected, use only the CORRECTED version.
NOT acquisition: hypotheses and hedged statements — a sentence carrying a probability hedge ("probably", "likely", "might", "скорее всего", "видимо", "похоже") is NEVER a finding; plans/intentions ("I'll do X"), options considered but not chosen, routine work narration, knowledge already established before this session (that is recall).

DURABILITY GATE (mandatory; persistent memory is not a task tracker or a live
status cache): emit acquisition only when the knowledge should remain useful
and accurate AFTER the current task/session/incident is over.
- NEVER turn a recommendation, proposed next step/stage/session, or work plan
  into a decision. "I recommend X next" is still a plan unless the excerpt
  contains an explicit acceptance; task ordering itself is not durable memory.
- NEVER emit ticket/task completion, work-in-progress, pending approvals,
  unpushed commits, current counts, deploy progress, or other point-in-time
  operational status.
- NEVER emit the current base model/config/provider route, model availability,
  quota, temporary workaround, running dev server, or similar live state that
  can change outside this transcript.
- A durable root cause, architectural choice, or standing rule MAY be emitted
  when it remains true after the incident; copy the durable mechanism, not the
  transient status that exposed it.

PUSH/PULL BOUNDARY (mandatory): personal acquisition is not an importer.
- NEVER emit a restatement or summary whose subject is a book, external document,
  web page, RAG result, methodology corpus, or attached topic base. That material
  remains in its source-owned PULL corpus.
- A project-owned decision or finding that APPLIES external material may be emitted
  only when the quoted assistant text states the concrete project outcome itself;
  capture that outcome, not the external source's general knowledge.

STRICT COPY RULES (violating any = do not emit the candidate):
- NEVER add facts, merge from your own knowledge, sharpen numbers, or resolve vagueness — copy the assistant's assertion or skip it.
- If unsure whether a fact is recalled vs derived in-session: drop that fact from recall (keep the rest).
- If unsure whether knowledge is new vs pre-established: emit acquisition ONLY with an exact quote; no exact quote → skip.

Output ONLY this JSON object, no other text:
{"candidates": [{"kind": "recall", "recall_query": "...", "recalled_answer": "..."}, {"kind": "decision", "claim": "...", "transcript_quote": "..."}]}
At most 4 candidates TOTAL across all kinds — pick the most valuable. {"candidates": []} when the session has none (most sessions have none)."""


def _clean(text):
    text = _SYSREM_RE.sub("", text or "")
    text = _LOCALCMD_RE.sub("", text)
    return text.strip()


def durability_reject_reason(cand):
    """Return a stable reason for an obviously non-durable acquisition card.

    This is intentionally narrow: semantic selection stays in the extractor
    prompt, while these high-precision patterns fail toward missing an
    ephemeral card instead of polluting persistent memory.
    """
    kind = (cand.get("kind") or "").strip().lower()
    if kind not in ACQ_KINDS:
        return None
    text = " ".join((str(cand.get("claim") or ""),
                     str(cand.get("transcript_quote") or "")))
    if _NEXT_STEP_RE.search(text):
        return "next_step_or_recommendation"
    if _RECOMMENDATION_RE.search(text):
        return "recommendation_or_proposal"
    if _TASK_STATUS_RE.search(text):
        return "task_or_ticket_status"
    if _CONFIG_RE.search(text) and _MODEL_ROUTE_RE.search(text):
        return "live_config_or_route"
    if _MODEL_ROUTE_RE.search(text) and _MODEL_AVAILABILITY_RE.search(text):
        return "live_model_or_route_availability"
    return None


def _parse_turn_bytes(raw, max_turns=MAX_TURNS):
    text = bytes(raw).decode("utf-8", errors="replace")
    turns = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        rtype = rec.get("type")
        if rtype not in ("user", "assistant"):
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        parts = []
        if isinstance(content, str):
            parts = [content]
        elif isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
        text = _clean("\n".join(p for p in parts if p))
        if not text:
            continue  # tool_use / tool_result-only rows are never extractor input
        turns.append((rtype, text))
    return turns[-max_turns:]


def read_turns_snapshot(raw, tail_bytes=TAIL_BYTES, max_turns=MAX_TURNS):
    """Parse turns from one immutable byte snapshot using the normal tail policy."""
    raw = bytes(raw)
    if len(raw) > tail_bytes:
        raw = raw[-tail_bytes:]
        newline = raw.find(b"\n")
        raw = raw[newline + 1:] if newline >= 0 else b""
    return _parse_turn_bytes(raw, max_turns=max_turns)


def read_turns(transcript_path, tail_bytes=TAIL_BYTES, max_turns=MAX_TURNS):
    """Last `max_turns` real user/assistant TEXT turns → [(role, text)]."""
    size = os.path.getsize(transcript_path)
    with open(transcript_path, "rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            fh.readline()  # drop the partial line
        raw = fh.read()
    return _parse_turn_bytes(raw, max_turns=max_turns)


def build_excerpt(turns, turn_cap=TURN_CAP):
    lines = []
    for role, text in turns:
        tag = "USER" if role == "user" else "ASSISTANT"
        lines.append(f"[{tag}] {text[:turn_cap]}")
    return "\n\n".join(lines)


def _parse_candidates(text):
    """Wrapper-object JSON; balanced-brace fallback (nested braces in content)."""
    text = (text or "").strip()
    for attempt in (text,):
        try:
            obj = json.loads(attempt)
            if isinstance(obj, dict) and isinstance(obj.get("candidates"), list):
                return obj["candidates"]
        except Exception:
            pass
    for start in (m.start() for m in re.finditer(r"\{", text)):
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict) and isinstance(obj.get("candidates"), list):
                            return obj["candidates"]
                    except Exception:
                        pass
                    break
        else:
            continue
    return None


def _proposal_provenance(result):
    """Sanitized execution provenance for acquisition proposals.

    The shared SDK already records route decisions and attempts.  Persist only
    the selected provider/model/family and the successful exact route; never
    copy raw provider responses, key state, or account details into memory.
    Legacy/fake runtimes without those fields remain visibly incomplete and
    therefore cannot gain write authority in the acquisition-only loop.
    """
    decision = result.get("route_decision") if isinstance(result, dict) else None
    decision = decision if isinstance(decision, dict) else {}
    selected = decision.get("selected")
    selected = selected if isinstance(selected, dict) else {}
    attempts = result.get("route_attempts") if isinstance(result, dict) else None
    attempts = attempts if isinstance(attempts, list) else []
    successful = next(
        (row for row in reversed(attempts)
         if isinstance(row, dict) and row.get("ok") and row.get("route_id")),
        None,
    )
    selected_route_id = str(selected.get("route_id") or "")
    route_id = str((successful or {}).get("route_id") or selected_route_id)
    provider = str(selected.get("provider") or "")
    model = str(selected.get("model") or "")
    # `chat()` may visibly fail over.  The successful route attempt is the
    # executed identity; never stamp the initially selected route onto a
    # proposal produced by a different admitted fallback.
    if route_id and "/" in route_id and route_id != selected_route_id:
        provider, model = route_id.split("/", 1)
    elif route_id and (not provider or not model) and "/" in route_id:
        provider, model = route_id.split("/", 1)
    complete = bool(successful and route_id and provider and model)
    return {
        "status": "ok" if complete else "missing",
        "task": str(decision.get("task") or "structured_classification"),
        "route_id": route_id,
        "provider": provider,
        "model": model,
        "family": (str(selected.get("family") or "")
                   if not selected_route_id or route_id == selected_route_id else ""),
        "identity_state": "sdk_route_attested" if complete else "unresolved",
        "selected_route_id": selected_route_id,
        "route_attempt_count": len(attempts),
        "response_shape_status": str(
            (result.get("response_shape") or {}).get("status") or
            ("ok" if (result.get("response_shape") or {}).get("ok") else "")
        ),
    }


def _ensure_shimnachi_class_token(shared_root):
    """Export Eidetic's OWN shimnachi class token for the SDK's local lane.

    The shared runtime never picks a client class on a caller's behalf; each
    caller self-identifies. Explicit env always wins. Missing file is a silent
    no-op — the SDK then skips shimnachi and fails over to the API lanes.
    """
    if os.environ.get("SHIMNACHI_LOCAL_TOKEN", "").strip():
        return
    env_path = os.path.join(shared_root, "shimnachi-local", "runtime",
                            "clients", "eidetic.env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                if name in ("SHIMNACHI_LOCAL_TOKEN", "SHIMNACHI_LOCAL_URL"):
                    os.environ.setdefault(name, value.strip().strip('"').strip("'"))
    except OSError:
        pass


def mine_transcript(transcript_path, *, session_id=None, project_slug=""):
    """→ (candidates, meta). Recall candidates: {kind:"recall", recall_query,
    recalled_answer, session_id, project_slug}; acquisition candidates
    (decision/finding/rule): {kind, claim, transcript_quote, session_id,
    project_slug}.

    The accept + in-run-dedup path is KIND-AWARE (audit B1): a recall-only
    accept loop would silently drop 100% of acquisition candidates BEFORE kind
    routing and the dark lane would log a fake zero. meta carries loud per-kind
    raw/kept counters so an accept-drop is distinguishable from genuine zero
    yield."""
    meta = {"turns": 0, "excerpt_chars": 0, "raw_candidates": 0, "kept": 0,
            "raw_by_kind": {}, "kept_by_kind": {}, "dropped_unknown_kind": 0,
            "miner_policy": MINER_POLICY_VERSION,
            "proposal_provenance": {"status": "not_called"}, "error": None}
    turns = read_turns(transcript_path)
    meta["turns"] = len(turns)
    if not any(r == "assistant" for r, _ in turns):
        return [], meta
    excerpt = build_excerpt(turns)
    meta["excerpt_chars"] = len(excerpt)

    shared_root = os.environ.get("EIDETIC_SHARED_ROOT") or os.path.join(
        os.path.expanduser("~"), "Documents/cursore")
    if shared_root not in sys.path:
        sys.path.insert(0, shared_root)
    _ensure_shimnachi_class_token(shared_root)
    try:
        from shared_api_cache import get_sdk
        sdk = get_sdk()
        # task=memory_mining: the Eidetic-only chain with Anthropic lanes
        # excluded (owner directive 2026-07-28) and shimnachi/local as the
        # first failover. Never point this back at structured_classification —
        # that chain fails over to claude_batch.
        res = sdk.chat(task="memory_mining", volume="bounded",
                       system=SYSTEM, user=excerpt,
                       max_tokens=2000, temperature=0.0, timeout=120)
    except Exception as exc:  # SDK absent / route dead → no candidates, loudly
        meta["error"] = f"sdk:{exc!r}"[:200]
        return [], meta
    shape = res.get("response_shape") or {}
    if not shape.get("ok"):
        meta["error"] = f"provider:{shape.get('provider_error_class')}"
        return [], meta
    cands = _parse_candidates(res.get("content") or "")
    if cands is None:
        meta["error"] = "parse_fail"
        return [], meta
    meta["raw_candidates"] = len(cands)
    proposal_provenance = _proposal_provenance(res)
    meta["proposal_provenance"] = proposal_provenance

    out, seen = [], set()
    sid = session_id or os.path.basename(transcript_path).rsplit(".", 1)[0]
    for c in cands[:MAX_CANDIDATES]:
        if not isinstance(c, dict):
            continue
        kind = (c.get("kind") or "").strip().lower()
        if not kind and (c.get("recall_query") or c.get("recalled_answer")):
            kind = KIND_RECALL  # v2-shape tolerance: kindless recall candidate
        label = kind if (kind == KIND_RECALL or kind in ACQ_KINDS) else "unknown"
        meta["raw_by_kind"][label] = meta["raw_by_kind"].get(label, 0) + 1
        if label == "unknown":
            meta["dropped_unknown_kind"] += 1  # NFR-3: drop, count in meta
            continue

        accepted = None
        if kind == KIND_RECALL:
            q = (c.get("recall_query") or "").strip()
            a = (c.get("recalled_answer") or "").strip()
            if q and len(a) >= MIN_ANSWER_CHARS:
                key = (kind, q[:80], a[:120])
                if key not in seen:
                    seen.add(key)
                    accepted = {"kind": kind, "recall_query": q,
                                "recalled_answer": a}
        else:  # acquisition kinds
            claim = (c.get("claim") or "").strip()
            quote = (c.get("transcript_quote") or "").strip()
            # The judge's verbatim gate (_quote_ok) hard-rejects quotes under
            # MIN_QUOTE_TOKENS normalized tokens — enforce the same floor here
            # so a too-short quote dies at accept, not after burning a judge call.
            if (len(claim) >= MIN_CLAIM_CHARS and len(quote) >= MIN_QUOTE_CHARS
                    and len(m3_judge._norm_tokens(quote).split()) >= MIN_QUOTE_TOKENS):
                key = (kind, claim[:120], quote[:120])
                if key not in seen:
                    seen.add(key)
                    accepted = {"kind": kind, "claim": claim,
                                "transcript_quote": quote,
                                "miner_policy": MINER_POLICY_VERSION,
                                "proposal_provenance": proposal_provenance}
        if accepted is None:
            continue
        accepted["session_id"] = sid
        accepted["project_slug"] = project_slug
        out.append(accepted)
        meta["kept_by_kind"][kind] = meta["kept_by_kind"].get(kind, 0) + 1
    meta["kept"] = len(out)
    return out, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--project-slug", default="")
    args = ap.parse_args()
    cands, meta = mine_transcript(args.transcript, project_slug=args.project_slug)
    print(json.dumps({"meta": meta, "candidates": cands}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
