#!/usr/bin/env python3
"""FR-1 — the LLM claim-entailment judge, wired into M3's `register_support` seam.

This is the ONLY change to the filing DECISION (SPEC §FR-1). It swaps M3's
default deterministic `_overlap_support` for an LLM entailment scorer that
returns 1.0 (file-eligible) / 0.0 (reject) per material claim, keeping the
existing "ANY material claim ≤ floor ⇒ reject the WHOLE answer" contract
(M3_SUPPORT_MIN=0.5, so 1.0 passes and 0.0 rejects, unchanged).

Measured earning of the loop (AC-0, 2026-07-08, kurdyuk output/karpathy-llm-wiki-spec):
writer/palmyra-x5 files 79.7% faithful at 0% noise / 0% partial (raw), 64.1% with
the verbatim-quote gate ON — both clear the ≥60/≤5/=0 kill criterion. mistral-small
(26.6%) does NOT. So the default route here is palmyra; the verbatim gate is ON by
default (the cheap, strong hallucination gate — see feedback-claim-support-verbatim-span-gate).

Soft-degrade (SPEC §NFR-4, AC §6.2): if the shared LLM SDK is absent (e.g. a box
without shared_api_cache), the judge does NOT register and M3 keeps its
deterministic overlap scorer — a missing model NEVER crashes the gate; the loop
just goes dark-effective (overlap files ~nothing, which is safe). When the judge
IS active, a persistent route error on a specific claim scores 0.0
(fail-toward-reject), never a silent overlap fallback (that would make one filing
decision half-LLM/half-lexical). Every LLM verdict is logged with its quoted span
so a human can audit what the wiki accreted.

Routing goes through the shared entrypoint (`strict_judge` task), never a raw
provider call or a top-up (absolute rule). Reversible: unregister / delete this
module.
"""
import os
import re
import sys
import time

# --- canonical judge prompt (SOURCE of truth; the eval-fixture keeps a frozen
#     copy for reproducibility) -------------------------------------------------
SYSTEM = """You are the filing gate of a personal memory wiki. Your ONLY job: decide whether a CLAIM is ENTAILED by the cited SOURCE SPANS.

ENTAILED (true) means: the spans, taken together, actually STATE the claim's content — the same entities, the same numbers/dates/versions, the same polarity (affirmed vs negated), the same attribution (who/what did it), the same direction (raised vs lowered, works vs broken). Paraphrase is fine. Translation between Russian and English is fine.

NOT ENTAILED (false) means ANY of:
- the spans are merely about the same topic but do not state the claim (subject echo);
- any key detail differs: a number, date, version, threshold, name, path, error code;
- the polarity or outcome is flipped (span says X failed / was not done — claim says X works / was done, or vice versa);
- the claim attributes the fact to a different actor, component, or cause;
- the claim adds facts the spans never state (extra conclusions, extra scope, "always/all/never" not in the source).

Judge ONLY against the spans. Your own knowledge, plausibility, or the claim's confident tone are IRRELEVANT. If you are not certain the spans state it, answer false.

Reply with ONLY one JSON object, no other text:
{"entailed": true|false, "quote": "<verbatim substring copied character-for-character from ONE span that states the claim's core content; empty string when entailed=false>"}

The quote must be a contiguous substring of one span, at least 6 consecutive words, copied exactly (same language as the span). If you cannot copy such a substring, you must answer entailed=false.

Examples:

CLAIM: After the incident review the retry budget went down to 3.
SPANS:
[1] The retry budget was lowered from 5 to 3 on 2026-03-14 after the incident review.
{"entailed": true, "quote": "retry budget was lowered from 5 to 3 on 2026-03-14 after the incident review"}

CLAIM: After the incident review the retry budget was raised to 5.
SPANS:
[1] The retry budget was lowered from 5 to 3 on 2026-03-14 after the incident review.
{"entailed": false, "quote": ""}

CLAIM: SSO login works for legacy tenants since the April patch.
SPANS:
[1] Checked on staging: SSO login does NOT work for legacy tenants; the April patch only fixed the redirect loop for new tenants.
{"entailed": false, "quote": ""}

CLAIM: Экспортер пишет CSV каждую ночь и загружает его в S3.
SPANS:
[1] The exporter writes CSV to the local reports directory. Upload is manual.
{"entailed": false, "quote": ""}

CLAIM: Пайплайн переехал на очередь Redis, воркеры читают из неё напрямую.
SPANS:
[1] Migration done: the pipeline now uses a Redis queue; workers consume directly from it (no more cron polling).
{"entailed": true, "quote": "the pipeline now uses a Redis queue; workers consume directly from it"}"""

# --- config -------------------------------------------------------------------
_JUDGE_PROVIDER = os.environ.get("EIDETIC_M3_JUDGE_PROVIDER", "writer")
_JUDGE_MODEL = os.environ.get("EIDETIC_M3_JUDGE_MODEL", "palmyra-x5")
# Verbatim-quote gate ON by default (AC-0 "verified" mode: 0 leak at 64% recall).
# Relax to raw-verdict (80% recall, still 0 leak on the eval) with =0.
_REQUIRE_QUOTE = os.environ.get("EIDETIC_M3_JUDGE_REQUIRE_QUOTE", "1").strip() not in ("0", "false", "")


def _real_home():
    """The login home, immune to a clobbered $HOME (a sandbox run sets HOME to a
    fake dir, but the shared SDK still lives under the real user's home)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:
        return os.path.expanduser("~")


# Where shared_api_cache is importable from. Env override wins; else the real home.
_SHARED_ROOT = os.environ.get("EIDETIC_SHARED_ROOT") or os.path.join(_real_home(), "Documents/cursore")

_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)
_TOK_RE = re.compile(r"[\W_]+", re.UNICODE)


def _log(msg):
    """Loud, single-line, auditable (never silent — feedback-silent-failures-are-not-ok)."""
    try:
        root = os.environ.get("EIDETIC_MEMORY_SYSTEM", os.path.expanduser("~/.claude/memory-system"))
        d = os.path.join(root, "events")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "m3_judge.log"), "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except OSError:
        pass
    print(f"[m3_judge] {msg}", file=sys.stderr)


def _norm_tokens(text):
    return " ".join(t for t in _TOK_RE.split((text or "").casefold()) if t)


def _quote_ok(quote, spans):
    q = _norm_tokens(quote)
    if len(q.split()) < 6:
        return False
    return any(q in _norm_tokens(s) for s in spans)


def _parse_verdict(text):
    """First JSON object carrying `entailed`. Balanced scan tolerates braces in
    the quoted span (a non-greedy regex truncates on nested `{}` — the eval-harness
    parser bug we fixed 2026-07-08)."""
    text = (text or "").strip()
    try:
        obj = __import__("json").loads(text)
        if isinstance(obj, dict) and "entailed" in obj:
            return obj
    except Exception:
        pass
    import json as _json
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
                        obj = _json.loads(text[start:i + 1])
                        if isinstance(obj, dict) and "entailed" in obj:
                            return obj
                    except Exception:
                        pass
                    break
        else:
            continue
    return None


def _build_user(claim, spans):
    lines = ["CLAIM:", (claim or "").strip(), "", "SPANS:"]
    for i, s in enumerate(spans, 1):
        lines.append(f"[{i}] {(s or '').strip()}")
    return "\n".join(lines)


# --- SDK handle (lazy; absence ⇒ do-not-register soft-degrade) -----------------
_SDK = None


def _get_sdk():
    global _SDK
    if _SDK is not None:
        return _SDK
    if _SHARED_ROOT not in sys.path:
        sys.path.insert(0, _SHARED_ROOT)
    from shared_api_cache import get_sdk  # ImportError ⇒ caller soft-degrades
    _SDK = get_sdk()
    _SDK.assert_contract()
    return _SDK


def score(claim, spans):
    """`register_support` scorer: 1.0 (file-eligible) / 0.0 (reject this claim).

    Called only when the judge registered (SDK present). A persistent route error
    ⇒ 0.0 (fail-toward-reject), NEVER a silent lexical fallback."""
    spans = [s for s in (spans or []) if (s or "").strip()]
    if not spans:
        return 0.0
    try:
        sdk = _get_sdk()
        res = sdk.chat_for_route(
            provider=_JUDGE_PROVIDER, model=_JUDGE_MODEL,
            task="strict_judge", volume="bounded",
            allow_same_family_failover=False,
            system=SYSTEM, user=_build_user(claim, spans),
            max_tokens=400, temperature=0.0, timeout=90,
            retry_on_parse_fail=(_JUDGE_PROVIDER != "writer"),
        )
    except Exception as exc:  # infra/route error → reject this claim, loudly
        _log(f"ROUTE_ERROR claim={claim[:80]!r} err={exc!r}")
        return 0.0
    shape = res.get("response_shape") or {}
    if not shape.get("ok"):
        _log(f"PROVIDER_ERROR class={shape.get('provider_error_class')} claim={claim[:80]!r}")
        return 0.0
    verdict = _parse_verdict(res.get("content") or "")
    if verdict is None:
        _log(f"PARSE_FAIL claim={claim[:80]!r} raw={(res.get('content') or '')[:120]!r}")
        return 0.0
    ent = verdict.get("entailed")
    if isinstance(ent, str):
        ent = ent.strip().lower() == "true"
    quote = str(verdict.get("quote") or "")
    filed = bool(ent) and (not _REQUIRE_QUOTE or _quote_ok(quote, spans))
    # Auditable: every verdict, with the grounding quote (SPEC §NFR-4).
    _log(f"VERDICT entailed={bool(ent)} filed={filed} quote_ok={_quote_ok(quote, spans)} "
         f"claim={claim[:90]!r} quote={quote[:90]!r}")
    return 1.0 if filed else 0.0


def register(m3_autofile):
    """Activate the judge on an imported m3_autofile module. Returns True if the
    LLM route is reachable (registered), False if absent (M3 keeps overlap).

    Probes the route once so a dead pool (e.g. dashscope rotation_exhausted) is a
    LOUD do-not-register, not a per-claim surprise mid-run."""
    try:
        sdk = _get_sdk()
        probe = sdk.chat_for_route(
            provider=_JUDGE_PROVIDER, model=_JUDGE_MODEL,
            task="strict_judge", volume="bounded", allow_same_family_failover=False,
            system='Reply with ONLY this JSON object: {"entailed": false, "quote": ""}',
            user="ping", max_tokens=40, temperature=0.0, timeout=60,
            retry_on_parse_fail=(_JUDGE_PROVIDER != "writer"))
        if not (probe.get("response_shape") or {}).get("ok"):
            _log(f"DO_NOT_REGISTER route probe not ok "
                 f"({(probe.get('response_shape') or {}).get('provider_error_class')}) "
                 f"→ M3 keeps deterministic overlap")
            return False
    except Exception as exc:
        _log(f"DO_NOT_REGISTER SDK/route absent ({exc!r}) → M3 keeps deterministic overlap")
        return False
    m3_autofile.register_support(score)
    _log(f"REGISTERED judge={_JUDGE_PROVIDER}/{_JUDGE_MODEL} require_quote={_REQUIRE_QUOTE}")
    return True
