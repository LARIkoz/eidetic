#!/usr/bin/env python3
"""FR-2 front-end — the session-transcript recall miner (site (b), the twist).

Mines a Claude-Code session transcript for MEMORY-RECALL exchanges worth filing
into the wiki, emitting ONLY `{recall_query, recalled_answer}` candidates. It
NEVER asserts sources: the producer (`m3_producer_driver.resolve_sources`)
retrieves the cited spans itself and the FR-1 judge verifies claim ⊨ span —
so a miner confabulation cannot launder a citation by construction; the worst
a bad candidate can do is burn one judge call and get rejected.

Transcript reading follows the session-signals.sh discipline: parse the JSONL
tail, keep only real user/assistant TEXT turns (never raw tool output), strip
injected <system-reminder> blocks, bound every turn and the total. Extraction
goes through the shared SDK (`structured_classification` task, wrapper-object
JSON per the batch-array collapse guard); the model is told to copy, not to
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

TAIL_BYTES = 2 * 1024 * 1024      # spans past tool-dump walls (session-signals lesson)
MAX_TURNS = 30                    # last N user/assistant text turns
TURN_CAP = 2000                   # chars per turn in the prompt
MAX_CANDIDATES = 4
MIN_ANSWER_CHARS = 80

_SYSREM_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_LOCALCMD_RE = re.compile(r"<local-command-[^>]*>.*?</local-command-[^>]*>", re.DOTALL)

SYSTEM = """You mine a coding-session transcript for MEMORY-RECALL answers worth filing into a personal knowledge wiki. You are invoked by a script; your entire output is parsed as JSON.

A candidate exists ONLY when BOTH hold in the SAME exchange:
1. The USER asked about past facts: what happened, what was decided, how something is configured, why something was done, what the state of X is. (Not a request to write/change code now, not chit-chat, not "run this".)
2. The ASSISTANT answered by STATING SPECIFIC remembered facts — names, numbers, dates, versions, decisions, causes — asserted as established knowledge. NOT: plans, proposals, questions back, work narration ("I ran/fixed/created…"), or facts it visibly derived right there by reading files / running commands in the shown exchange.

GROUNDED-SUBSET RULE (the filing gate rejects the WHOLE answer if ANY fact is unsupported — so you must extract ONLY the memory-grounded subset):
An assistant response often MIXES recalled knowledge with session-derived facts. You MUST separate them:
- KEEP: facts the assistant states as prior knowledge, configuration, decisions, architecture, rules. These are claims about how things ARE or WERE — established before this session.
- DROP: facts the assistant obtained THIS SESSION by reading files, running commands, checking tool output, or reasoning from in-session observations ("I checked and found…", "The output shows…", "Looking at the file…", results of grep/read/bash shown in the transcript). Also drop: plans, proposals, current-session work narration.
If an exchange mixes both types, extract ONLY the recalled subset. If only 1-2 recalled facts survive the filter, that is fine — a short recalled_answer that passes the gate is worth more than a long mixed one that gets rejected.

STRICT COPY RULES (violating any = do not emit the candidate):
- recall_query = the user's actual question, condensed, in the user's own language.
- recalled_answer = ONLY the memory-grounded facts the assistant stated, near-verbatim, condensed to 1-5 factual sentences. Drop greetings, hedges, markdown, tables. NEVER add, merge from your own knowledge, sharpen numbers, or resolve vagueness — copy the assistant's assertion or skip it.
- If you are unsure whether a fact is recalled vs derived in-session: drop that fact (keep the rest).

Output ONLY this JSON object, no other text:
{"candidates": [{"recall_query": "...", "recalled_answer": "..."}]}
At most 4 candidates; {"candidates": []} when the session has none (most sessions have none)."""


def _clean(text):
    text = _SYSREM_RE.sub("", text or "")
    text = _LOCALCMD_RE.sub("", text)
    return text.strip()


def read_turns(transcript_path, tail_bytes=TAIL_BYTES, max_turns=MAX_TURNS):
    """Last `max_turns` real user/assistant TEXT turns → [(role, text)]."""
    size = os.path.getsize(transcript_path)
    with open(transcript_path, "rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            fh.readline()  # drop the partial line
        raw = fh.read().decode("utf-8", errors="replace")
    turns = []
    for line in raw.splitlines():
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


def mine_transcript(transcript_path, *, session_id=None, project_slug=""):
    """→ (candidates, meta). candidates = [{recall_query, recalled_answer,
    session_id, project_slug}]; meta carries loud counters for the caller."""
    meta = {"turns": 0, "excerpt_chars": 0, "raw_candidates": 0, "kept": 0,
            "error": None}
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
    try:
        from shared_api_cache import get_sdk
        sdk = get_sdk()
        res = sdk.chat(task="structured_classification", volume="bounded",
                       system=SYSTEM, user=excerpt,
                       max_tokens=1200, temperature=0.0, timeout=120)
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

    out, seen = [], set()
    sid = session_id or os.path.basename(transcript_path).rsplit(".", 1)[0]
    for c in cands[:MAX_CANDIDATES]:
        if not isinstance(c, dict):
            continue
        q = (c.get("recall_query") or "").strip()
        a = (c.get("recalled_answer") or "").strip()
        if not q or len(a) < MIN_ANSWER_CHARS:
            continue
        key = (q[:80], a[:120])
        if key in seen:
            continue
        seen.add(key)
        out.append({"recall_query": q, "recalled_answer": a,
                    "session_id": sid, "project_slug": project_slug})
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
