#!/usr/bin/env python3
"""Eidetic v6 M3 — auto-file recalled answers (claim-support gated, no laundering).

M3 closes Karpathy's loop — "good answers get filed back into the wiki as new
pages" — but ONLY through a claim-support gate WITH TEETH, at the agent cold-start
confidence (0.40, below the 0.55 injection gate), so filing can NEVER launder an
unverified claim into a trusted, injected card.

Turn-1 CORE (the laundering-critical pipeline):
  * FR-1 dedup — probe the vector index (M1/M2's v1.1 door, S2); a top neighbor
    ≥ M3_DEDUP_MIN is a near-duplicate → route the answer to M2 (update), file NO
    new page. On an FTS-only store the door returns [] → no semantic dedup.
  * FR-2 claim-support gate WITH TEETH — split the answer into material claim
    sentences; score each against ITS cited span(s) (LLM-free deterministic
    span-overlap by default; a cross-encoder may be registered). ANY material
    claim below M3_SUPPORT_MIN, or NO cited sources at all ⇒ the WHOLE answer is
    REJECTED (no page, no event). Fail toward REJECT.
  * FR-3 file — a supported, non-duplicate answer is written as a new typed page
    (source=agent-extracted, managed lifecycle) with confidence EXACTLY 0.40 and
    an EMPTY `## Evidence` log: fold(0.40, []) == 0.40 (NOT 0.45 — NO synthetic
    `observed` seed is written). The filing act mints NO promoting event; the page
    is recall-only until a LATER genuine tier-≥2 event (FR-4, deferred to turn 2).
  * FR-7 dark-safe — M3 files/gates/emits ONLY when EIDETIC_CONFIDENCE_EVENTS is
    on AND the M3 activation flag is set. With either off M3 is a COMPLETE no-op
    (no page, no event) so the confidence dark-run zero-diff holds. M3 is NOT
    wired into the ingest indexer: it is driven by the (turn-2) session-end /
    recall-path producer of the input contract below.

REUSE (no new door / no ENGINE_API delta, "1.1"): M1's v1.1 door via
`m1_contradiction.neighbors_via_door` (S2); the typed-page writer
(`remember.build_card` / `_atomic_write`, `compound.resolve_memory_dir`); the M2
hand-off (`m2_synthesis.process_trigger`); the op-log. NO LLM anywhere in the
scoring path. `embed.py`/`rerank.py` stay unforked.

--- Input contract (the turn-1 seam) ---------------------------------------
M3 consumes a TYPED PROVENANCE RECORD (a plain dict), NOT a transcript:

    {"answer_text": str,                       # the synthesized recalled answer
     "sources": [{"card_id": str,              # a cited source card
                  "span": str}, ...],          # the cited chunk text (ground truth)
     "recall_query": str,                      # what was asked
     "session_id": str}                        # the originating session

The CONSUMER (this module: gate + dedup + file) is LIVE. The PRODUCER (a
session-end hook mining the transcript, or the recall/answer path writing this
record) is a documented seam, DEFERRED to turn 2 — so `file_recalled_answer` is
driven directly (like M2's `process_trigger`). An answer with NO traceable
sources is rejected outright (FR-2): fail toward reject.
"""

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evidence as _EV  # noqa: E402  (events_enabled — the dark rail)
import m1_contradiction as _M1  # noqa: E402  (neighbors_via_door — the S2 door)
import remember as _REM  # noqa: E402  (build_card / _atomic_write / target_slug)

try:
    import compound as _COMPOUND
except Exception:  # pragma: no cover
    _COMPOUND = None

try:
    import oplog as _OPLOG
except Exception:  # pragma: no cover
    _OPLOG = None

try:
    from constants import (M3_DEDUP_MIN, M3_DEDUP_MIN_DEFAULT, M3_NEIGHBORS,
                           M3_SUPPORT_MIN)
except ImportError:  # pragma: no cover
    M3_NEIGHBORS = 8
    M3_DEDUP_MIN = {"multilingual": 0.85, "english": 0.60}
    M3_DEDUP_MIN_DEFAULT = 0.85
    M3_SUPPORT_MIN = 0.5

# The page M3 files: agent cold-start terms (spec §3.4). type=project +
# source=agent-extracted + a non-exempt kind ⇒ MANAGED ⇒ cold_start 0.40.
_FILE_KIND = "synthesis"
_FILE_TYPE = "project"
_FILE_SOURCE = "agent-extracted"
_FILE_EVIDENCE = "hypothesis"  # legacy evidence-weight (unverified); NOT a confidence event

# Op-log verbs (greppable, stable vocabulary).
OP_FILED = "autofile_filed"
OP_REJECTED = "autofile_rejected"
OP_DEDUPED = "autofile_deduped"
OP_PROMOTED = "autofile_promoted"

# A genuine tier-≥2 promoting signal (FR-4). The affirmation `kind` → the typed
# event; NEVER minted by the filing act — only by a REAL in-session signal.
_AFFIRMATION_EVENT = {
    "user_affirmation": "confirmed",       # tier-3, +0.20 (user explicitly affirmed)
    "test_pass": "verified_by_test",       # tier-2, +0.15 (an in-session test passed)
}


# --- activation (dark-safe, FR-7) --------------------------------------------
def m3_enabled():
    """M3 activation switch — dormant by default (like M2's EIDETIC_M2_SYNTHESIS).
    A STRICTER gate ON TOP of the EIDETIC_CONFIDENCE_EVENTS dark rail: M3 CREATES
    knowledge-tier pages (higher-consequence than event metadata), so it stays a
    complete no-op until BOTH flags are on. Flip only after the mini-conveyor
    converges precision. Dark-safe holds either way."""
    return os.environ.get("EIDETIC_M3_AUTOFILE", "").strip().lower() in (
        "1", "on", "true", "yes")


def _active():
    """FR-7: file/gate/emit ONLY when the confidence-events rail is on AND M3 is
    activated. Either off ⇒ M3 is a COMPLETE no-op (the zero-diff invariant)."""
    return _EV.events_enabled() and m3_enabled()


# --- thresholds (profile-aware where it matters) -----------------------------
def _profile():
    try:
        import engine
        return (engine.profile() or "").strip().lower()
    except Exception:
        return "multilingual"


def dedup_min():
    """FR-1 near-duplicate cosine floor (profile-aware; unknown → stricter end)."""
    return M3_DEDUP_MIN.get(_profile(), M3_DEDUP_MIN_DEFAULT)


def support_min():
    """FR-2 claim-support floor for the default overlap scorer (∈[0,1])."""
    return M3_SUPPORT_MIN


# --- claim splitting + materiality (deterministic) ---------------------------
_STOPWORDS = frozenset("""
the a an and or but is are was were be been being it its it's of to in on at for
by with from as that this these those into onto over under out up down off then
than so not no yes do does did has have had will would can could should may might
must we you they he she i me my our your their his her them us if else when while
which who whom whose what where why how all any some each every both few more most
other such only own same too very just also here there now
""".split())

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
# identifier-preserving word (keeps code-ish `_ . / + -` and digits together, but
# never captures TRAILING punctuation — `Yes.` → `Yes`, `port_9999` → `port_9999`).
_WORD_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_./+-]*[A-Za-z0-9])?")

# A1a — negation / polarity markers. Read off the RAW text (the tokenizer strips
# `not`/`no` as stopwords and splits `doesn't`→`doesn`,`t`, so a raw scan is the
# only faithful polarity read). `n't` catches doesn't/isn't/won't/can't/…
_NEG_WORD_RE = re.compile(r"\b(not|no|never|without|cannot|none|neither|nor)\b", re.I)

# N4 — curated lexicon of common TECHNICAL antonyms. A pure token gate CANNOT
# catch arbitrary lexical-antonym contradictions (that needs the opt-in
# cross-encoder); this catches the COMMON ones and the rest is a DECLARED residual
# (see M3-PROGRESS Fix R2 Breaks-when). Each side lists surface forms so a claim
# asserting one side over a span asserting the other (over otherwise-overlapping
# content) fails-closed. NOT a frozenset so a test can revert-verify by emptying it.
_ANTONYM_PAIRS = [
    ({"enabled", "enable", "enables", "enabling"},
     {"disabled", "disable", "disables", "disabling"}),
    ({"allow", "allows", "allowed", "allowing"},
     {"deny", "denies", "denied", "denying", "block", "blocks", "blocked", "blocking"}),
    ({"access"}, {"refresh"}),
    ({"read", "reads", "reading", "readonly"},
     {"write", "writes", "writing", "writable"}),
    ({"sync", "synchronous", "synchronously"},
     {"async", "asynchronous", "asynchronously"}),
    ({"primary"}, {"replica", "secondary", "standby"}),
    ({"on"}, {"off"}),
    ({"true"}, {"false"}),
    ({"valid"}, {"invalid"}),
    ({"accept", "accepts", "accepted", "accepting"},
     {"reject", "rejects", "rejected", "rejecting"}),
    ({"grant", "grants", "granted", "granting"},
     {"revoke", "revokes", "revoked", "revoking"}),
    ({"open", "opens", "opened"}, {"closed", "close", "closes"}),
    ({"success", "succeeds", "succeeded", "successful"},
     {"failure", "fails", "failed", "failing"}),
    ({"inbound"}, {"outbound"}),
    ({"encrypt", "encrypts", "encrypted", "encryption"},
     {"decrypt", "decrypts", "decrypted", "decryption"}),
]

# The flat set of all antonym surface forms — excluded from "shared content" so the
# overlap that licenses an antonym veto is OTHER (non-antonym) content (N4 "over
# otherwise-overlapping content").
_ANTONYM_FORMS = frozenset().union(*[a | b for a, b in _ANTONYM_PAIRS]) if _ANTONYM_PAIRS else frozenset()

_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d)")     # N1a: 9,999 → 9999
_CODEISH_SPLIT_RE = re.compile(r"[/._+\-]")        # N1c: 8080/tcp → {8080, tcp}

# Clause boundaries for PRECISE negation scoping (N2): a `not` in a SUBORDINATE
# clause on non-overlapping content ("if not expired") must not veto a claim whose
# MAIN predicate agrees with the span. A negation vetoes only when it sits in a
# clause that shares content with the span — padding-proof (padding cannot move the
# negation out of the shared-predicate clause).
_CLAUSE_SPLIT_RE = re.compile(
    r"[,;:]|\b(?:if|when|unless|while|because|although|though|since|whenever|"
    r"whereas|provided|assuming|until|before|after)\b", re.I)

# A1b — directive-imperative verbs: a claim whose FIRST word is one of these is a
# short factual directive ("Delete all data.") and MUST be gated, not skipped.
_DIRECTIVE_VERBS = frozenset("""
use delete drop remove add set run enable disable install configure create update
call import export avoid stop start restart reset truncate insert replace prefer
choose pick apply ensure make keep switch migrate rename move copy grant revoke
""".split())


def _content_tokens(text):
    """Lowercase alnum tokens, length ≥ 3, minus stopwords (deterministic)."""
    return [t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text or ""))
            if len(t) >= 3 and t not in _STOPWORDS]


def _split_claims(text):
    """Split an answer into candidate claim sentences (deterministic)."""
    return [s.strip() for s in _SENT_SPLIT_RE.split((text or "").strip()) if s.strip()]


def _has_negation(text):
    """A1a polarity read on RAW text: an explicit negation marker present?"""
    low = (text or "").lower()
    return ("n't" in low) or bool(_NEG_WORD_RE.search(low))


def _salient_set(text):
    """A1a salient tokens (lowercased) that a supporting span MUST contain: numbers,
    ALLCAPS (JWT/SQL), internal-capital names (MongoDB/PostgreSQL), code-ish
    identifiers (port_9999/a.b), and NON-sentence-initial Title-case proper nouns.
    A sentence-initial ordinary Capitalized word (The/Use/Delete) is NOT salient."""
    out = set()
    toks = [m.group(0) for m in _WORD_RE.finditer(text or "")]
    for i, tok in enumerate(toks):
        has_digit = any(c.isdigit() for c in tok)
        allcaps = tok.isalpha() and tok.isupper() and len(tok) >= 2
        internal_cap = any(c.isupper() for c in tok[1:])
        codeish = ("_" in tok) or ("/" in tok) or ("+" in tok) or \
                  ("." in tok and any(c.isalnum() for c in tok)) or \
                  ("-" in tok and has_digit)
        title_case = len(tok) > 1 and tok[:1].isupper() and tok[1:].islower()
        if has_digit or allcaps or internal_cap or codeish or (title_case and i > 0):
            out.add(tok.lower())
    return out


def _is_directive(claim):
    """A1b: the claim's first word is a directive-imperative verb."""
    first = next((m.group(0).lower() for m in _WORD_RE.finditer(claim or "")), "")
    return first in _DIRECTIVE_VERBS


def _is_material(claim):
    """A MATERIAL claim is scored by the gate. Widened (A1b) so a SHORT factual or
    directive assertion is no longer skipped un-gated: material iff ≥3 content words
    OR it carries a salient token (proper noun / number / identifier) OR its first
    word is a directive imperative. Trivial filler ("Yes.", "Okay then.") stays
    non-material (nothing to support)."""
    if len(_content_tokens(claim)) >= 3:
        return True
    if _salient_set(claim):
        return True
    return _is_directive(claim)


# --- support scorer (LLM-free by default; a cross-encoder may be registered) --
def _strip_thousands(text):
    """N1a: drop thousands separators between digits (9,999 → 9999) so a formatting
    difference on a number does not false-reject an otherwise-supported claim."""
    return _THOUSANDS_RE.sub("", text or "")


def _word_set(text):
    """Lowercased identifier-preserving word set."""
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


def _antonym_cross(claim_words, span_words):
    """N4: the claim asserts one side of a curated antonym pair and the span the
    OTHER side (and not both on either) — a lexical-antonym contradiction."""
    for side_a, side_b in _ANTONYM_PAIRS:
        ca, cb = claim_words & side_a, claim_words & side_b
        sa, sb = span_words & side_a, span_words & side_b
        if ca and not cb and sb and not sa:
            return True
        if cb and not ca and sa and not sb:
            return True
    return False


def _shared_content(claim_content_set, span_content_set, pair_forms=frozenset()):
    """THE single relatedness anchor used by BOTH the negation and antonym vetoes:
    the content tokens shared between a claim and a cited span, minus ONLY the antonym
    forms of the PAIR UNDER TEST (`pair_forms`) — NEVER the flat `_ANTONYM_FORMS`
    union. Stripping the flat union is exactly the drift that let NS-B (antonym) and
    its twin S3 (negation) survive: a shared anchor that is itself a lexicon term (from
    some OTHER pair) must still count as relatedness. Unifying both paths on this one
    helper makes the two sites structurally unable to drift again. The negation path
    passes NO pair (it tests polarity, not a specific pair) ⇒ strips nothing."""
    return (claim_content_set & span_content_set) - pair_forms


def _antonym_contradicts(claim_words, claim_content_set, spans_tok):
    """COVERAGE-FREE, corroboration-aware antonym veto (LS4) — the antonym analog of
    the coverage-free negation veto, verbose-span-proof (a long cited chunk cannot
    dilute it). For each lexicon pair where the CLAIM asserts exactly ONE side: veto
    iff a TOPICALLY-RELATED cited span (shares content OTHER than THIS pair's own
    forms with the claim — NS-B: subtract only the pair under test, NOT the flat
    lexicon union, so a shared anchor that is itself a lexicon term still counts)
    carries the OPPOSITE side AND no related span carries the SAME side. The
    same-side corroboration keeps the legitimate multi-span case ("access" sentence
    with its access span + "refresh" sentence with its refresh span) filing; the
    relatedness guard stops an UNRELATED same/opposite-side span from mis-firing.

    `spans_tok`: list of (span_word_set, span_content_set)."""
    for side_a, side_b in _ANTONYM_PAIRS:
        ca, cb = bool(claim_words & side_a), bool(claim_words & side_b)
        if ca == cb:
            continue  # claim carries NEITHER or BOTH sides → no single-sided assertion
        claim_side, opp_side = (side_a, side_b) if ca else (side_b, side_a)
        pair_forms = side_a | side_b  # subtract ONLY this pair (NS-B), not the flat union
        agree = oppose = False
        for sw, sc in spans_tok:
            if not _shared_content(claim_content_set, sc, pair_forms):
                continue  # topically unrelated cited span — never counts
            if sw & claim_side:
                agree = True
            if sw & opp_side:
                oppose = True
        if oppose and not agree:
            return True
    return False


def _negation_on_shared(text, shared_tokens):
    """PRECISE negation scoping (N2): True iff a negation marker sits in a clause of
    `text` that contains a content token SHARED with the span. A subclause negation on
    non-overlapping content ("if not expired") returns False and does not veto;
    padding cannot move the negation out of the shared-predicate clause (NS4)."""
    if not shared_tokens:
        return False
    for clause in _CLAUSE_SPLIT_RE.split(text or ""):
        if clause and _has_negation(clause) and (set(_content_tokens(clause)) & shared_tokens):
            return True
    return False


def _coverage_pool(union_words):
    """N1c: the set a claim salient token may be covered by — each span word plus
    its code-ish sub-tokens (8080/tcp → {8080, tcp}), all thousands-normalized."""
    pool = set()
    for w in union_words:
        pool.add(w)
        for part in _CODEISH_SPLIT_RE.split(w):
            if part:
                pool.add(part)
    return pool


def _overlap_support(claim, spans):
    """DEFAULT deterministic support scorer (NO LLM). Honest property (N4): it
    catches (a) explicit NEGATION markers, (b) salient-ENTITY/number changes, and
    (c) a curated set of common technical ANTONYMS — it does NOT do general
    entailment; ARBITRARY lexical-antonym contradictions are NOT detectable by this
    token gate and need the OPT-IN cross-encoder (`register_support`). Bias to
    REJECT throughout.

    Two phases — the CONTRADICTION veto is COVERAGE-FREE, so neither attacker claim
    padding (NS4) nor a naturally VERBOSE cited span (LS4) can disarm it:
      1. CONTRADICTION (safety): a NEGATION polarity mismatch whose marker scopes
         SHARED content (clause-precise, per span) OR a single-sided ANTONYM claim
         whose OPPOSITE side is carried by a topically-related span with no same-side
         corroboration (`_antonym_contradicts`) ⇒ support = 0.
      2. SUPPORT — salient-token coverage vs the UNION of ALL cited spans (N1b: a
         claim synthesized from several cited spans is the normal RAG case, so a
         salient token is covered if it appears in ANY span), then the content-word
         overlap floor (best qualifying span). A salient token in NO cited span ⇒ 0.
    A claim with no content words returns 1.0 (non-material — caller filters)."""
    claim = _strip_thousands(claim)
    spans = [_strip_thousands(s) for s in (spans or [])]
    claim_tokens = _content_tokens(claim)
    if not claim_tokens:
        return 1.0
    claim_neg = _has_negation(claim)
    claim_words = _word_set(claim)
    claim_content_set = set(claim_tokens)
    claim_salient = _salient_set(claim)

    # 1. CONTRADICTION veto — COVERAGE-FREE for BOTH negation and antonym, so neither
    # attacker claim-padding (NS4) nor a naturally VERBOSE cited span (LS4) can disarm
    # it. Per cited span:
    #   * NEGATION: a polarity mismatch vetoes iff the negation marker sits in a CLAUSE
    #     sharing content with the span (clause-precise).
    # then, across ALL cited spans:
    #   * ANTONYM: a single-sided antonym claim vetoes iff a topically-related span
    #     carries the OPPOSITE side and no related span corroborates the SAME side.
    spans_tok = [(_word_set(s), set(_content_tokens(s))) for s in spans]
    for span, (_sw, span_content) in zip(spans, spans_tok):
        if not span_content:
            continue
        if _has_negation(span) != claim_neg:  # NEGATION (clause-precise, coverage-free)
            neg_text = claim if claim_neg else span
            # NO pair under test → strip nothing (S3: the flat-union strip removed the
            # very lexicon token the negation scopes, disarming the veto).
            shared = _shared_content(claim_content_set, span_content)
            if _negation_on_shared(neg_text, shared):
                return 0.0
    if _antonym_contradicts(claim_words, claim_content_set, spans_tok):  # ANTONYM
        return 0.0

    # 2. SUPPORT — salient coverage vs the UNION, then best-of-span overlap.
    union_words = set()
    for span in spans:
        union_words |= _word_set(span)
    pool = _coverage_pool(union_words)
    if not claim_salient.issubset(pool):   # a salient token in NO cited span ⇒ fail
        return 0.0
    best = 0.0
    for span in spans:
        span_set = set(_content_tokens(span))
        if not span_set:
            continue
        covered = sum(1 for t in claim_tokens if t in span_set)
        best = max(best, covered / len(claim_tokens))
    return best


_ACTIVE_SUPPORT = None


def register_support(fn):
    """Install a support scorer `(claim, [spans]) -> float|None`. None restores the
    built-in deterministic overlap. A cross-encoder scorer (S5 `engine.rerank`)
    can be wired here — but the DEFAULT is LLM-free so BOTH legs are deterministic
    and a missing reranker never blocks the gate."""
    global _ACTIVE_SUPPORT
    _ACTIVE_SUPPORT = fn


def active_support():
    return _ACTIVE_SUPPORT or _overlap_support


# --- dedup door (S2) + M2 hand-off -------------------------------------------
def _default_neighbors(index_db_path, probe_text, exclude_paths=()):
    """FR-1 dedup via M1's v1.1 door (S2). SOFT: [] on an FTS-only store / no
    model — so M3 files a paraphrase there (semantic dedup is vector-only)."""
    return _M1.neighbors_via_door(index_db_path, probe_text, exclude_paths=exclude_paths)


def _iso_date():
    return datetime.now().strftime("%Y-%m-%d")


def _title_for(provenance):
    """The filed page's title: the recall query, else the first material claim,
    else the first line of the answer."""
    q = (provenance.get("recall_query") or "").strip()
    if q:
        return q
    for c in _split_claims(provenance.get("answer_text") or ""):
        if _is_material(c):
            return c
    tail = (provenance.get("answer_text") or "").strip()
    if not tail:
        return "recalled answer"          # whitespace/empty answer — never index [0]
    return tail.splitlines()[0][:80]


def _default_m2_handoff(index_db_path, provenance, hits, top, *, memory_dir=None, cwd=None):
    """FR-1 near-dup routing: hand the answer to M2 as a synthesis trigger against
    the near-duplicate page — reusing M2's `process_trigger` (dark-safe/dormant on
    its own flags). The synthetic trigger PATH is the slug M3 would have filed
    under; M2 reads only the passed meta+body and writes only to the NEIGHBOR
    pages, never this synthetic path. Best-effort — the FR-1 decision (no new page)
    holds even if M2 is dormant."""
    try:
        import m2_synthesis
        title = _title_for(provenance)
        slug = _REM.target_slug(title, _FILE_KIND)
        mdir = memory_dir or _resolve_dir(cwd)
        synth_trigger_path = os.path.join(mdir, slug + ".md")  # synthetic — not written
        meta = {"name": slug, "type": _FILE_TYPE, "source": _FILE_SOURCE,
                "last_verified": _iso_date()}
        return m2_synthesis.process_trigger(
            index_db_path, synth_trigger_path, meta,
            provenance.get("answer_text") or "", neighbors=hits)
    except Exception as e:  # pragma: no cover — best-effort hand-off
        print(f"WARN: M3→M2 hand-off skipped: {e}", file=sys.stderr)
        return None


# --- filing (FR-3) -----------------------------------------------------------
def _resolve_dir(cwd):
    if _COMPOUND is not None:
        return _COMPOUND.resolve_memory_dir(cwd or os.getcwd())
    return cwd or os.getcwd()


def _oneline(s, limit=240):
    """Collapse a span to one audit line (no newlines that could forge a heading)."""
    return " ".join((s or "").split())[:limit]


def _provenance_block(provenance, scores):
    """A user-visible `## Provenance` section (NOT `## Evidence` — the confidence
    fold parses only `## Evidence`, so this never seeds a promoting event). Records
    the recall query, session, cited source ids + CITED SPANS, and per-claim support
    scores (FR-5, AC-5). This is written ATOMICALLY as part of the page body, so a
    filed page ALWAYS carries its provenance (durable-truth, survives reindex)."""
    lines = ["## Provenance", "",
             f"_M3 auto-file · query=\"{(provenance.get('recall_query') or '').strip()}\" "
             f"· session={provenance.get('session_id') or ''} · {_iso_date()}_", "",
             "Cited sources:"]
    for s in provenance.get("sources") or []:
        cid = (s.get("card_id") or "").strip()
        span = _oneline(s.get("span") or "")
        label = cid or "(unattributed)"
        lines.append(f"- {label}: \"{span}\"" if span else f"- {label}")
    lines.append("")
    lines.append("Claim support (deterministic span-overlap):")
    for claim, sc in scores.items():
        lines.append(f"- {sc:.3f} · {_oneline(claim, 80)}")
    return "\n".join(lines)


def _log_path_for(index_db_path):
    """Hermetic op-log path (`<root>/log.md` from `<root>/db/index.db`). None ⇒ no
    db path ⇒ skip the op-log (NEVER fall back to the live global log)."""
    if not index_db_path:
        return None
    return os.path.join(os.path.dirname(os.path.dirname(index_db_path)), "log.md")


def _oplog(index_db_path, op, title, *, extra=None):
    log_path = _log_path_for(index_db_path)
    if _OPLOG is None or log_path is None:
        return
    bits = [f"op={op}", f"title={title}", f"date={_iso_date()}"]
    if extra:
        bits.append(extra)
    try:
        _OPLOG.append_op(op, title, detail=" ".join(bits), log_path=log_path)
    except Exception:  # pragma: no cover
        pass


def _file_new_page(index_db_path, provenance, title, scores, *, memory_dir=None, cwd=None,
                   m2_handoff=None, hits=None):
    """FR-3: write the answer as a new typed page at cold-start 0.40 with an EMPTY
    `## Evidence` log — build_card writes NO events and M3 appends NONE, so
    fold(0.40, []) == 0.40 (never 0.45). NO synthetic `observed` seed."""
    mdir = memory_dir or _resolve_dir(cwd)
    slug = _REM.target_slug(title, _FILE_KIND)
    path = os.path.join(mdir, slug + ".md")
    # FR-6 identity/collision: identity is (project_hash, normalized_slug). A
    # same-slug card ANYWHERE under THIS project's memory dir (recursive, incl.
    # subdirs — not just the exact path) is UPDATED via M2, NEVER clobbered. The
    # glob is project-scoped, so a same-slug card in a DIFFERENT project is
    # invisible here (cross-project isolation, the FIX2 #3 class). This routes the
    # 2nd file of the same answer to M2 too (idempotence, FR-8/AC-7).
    existing = _REM.find_same_slug_card(mdir, slug)
    if existing:
        (m2_handoff or _default_m2_handoff)(
            index_db_path, provenance, hits or [], {"path": existing, "score": 1.0},
            memory_dir=mdir, cwd=cwd)
        _oplog(index_db_path, OP_DEDUPED, title, extra=f"same_slug={existing}")
        return {"action": "deduped_to_m2", "neighbor": existing,
                "reason": "same_slug_collision"}

    answer = (provenance.get("answer_text") or "").strip()
    prov_block = _provenance_block(provenance, scores)
    # FR-5: a page whose provenance cannot be recorded is NOT filed (fail-closed).
    if "## Provenance" not in prov_block:  # pragma: no cover — defensive
        _oplog(index_db_path, OP_REJECTED, title, extra="reason=provenance_unrecordable")
        return {"action": "rejected", "reason": "provenance_unrecordable"}
    body = answer + "\n\n" + prov_block
    content = _REM.build_card(title, body, _FILE_KIND, _FILE_EVIDENCE, _FILE_SOURCE,
                              _FILE_TYPE, related=[])
    _REM._atomic_write(path, content)
    _oplog(index_db_path, OP_FILED, title, extra=f"path={path} conf=0.40")
    return {"action": "filed", "path": path, "confidence": 0.40, "support": scores}


# --- FR-4 promoting events (genuine tier-≥2 only; never minted by filing) -----
def _norm_ref(ref):
    """Normalize an affirmation target (a slug, a path, or the raw query text) to a
    bare slug stem for the mis-attribution guard."""
    ref = (ref or "").strip()
    if ref.endswith(".md"):
        ref = os.path.basename(ref)[:-3]
    elif "/" in ref:
        ref = os.path.basename(ref)
    return ref


def _affirmation_targets(affirmation, filed_slug):
    """Mis-attribution guard (spec §8 Breaks-when): the affirmation MUST reference
    the just-filed page. Accepts the target given as the on-disk slug, the page
    path, or the raw recall query/title (which slugifies to the filed slug). An
    empty/ambiguous or DIFFERENT-page target ⇒ False (no lift)."""
    ref = _norm_ref(affirmation.get("target"))
    if not ref:
        return False
    if ref == filed_slug:
        return True
    return _REM.target_slug(ref, _FILE_KIND) == filed_slug


def _already_promoted(filed_path, event_type, session_id):
    """FR-8 EXPLICIT same-source guard (NOT the PK): `append_event` stamps a fresh
    `ts` each run, so the (path, ts, event_type) PK cannot dedup a re-run. This
    content-keys on (event_type, session_id) already present on `## Evidence`."""
    rec = _M1._record_from_file(filed_path)
    if rec is None:
        return False
    for ev in rec.get("events") or []:
        if ev.get("event_type") == event_type and ev.get("session_id") == session_id:
            return True
    return False


def _apply_affirmation(index_db_path, filed_path, affirmation, filed_slug, *,
                       session_id=None):
    """Emit ONE genuine tier-≥2 event on the just-filed page from a REAL in-session
    affirmation. Returns {"promoted": event_type|None, "reason": ...}. The filing
    act NEVER calls this without a real affirmation record, so no filing path mints
    a promoting event. Dark-safe (append_event is itself events-gated).

    AUTHENTICITY IS THE PRODUCER'S CONTRACT (audit A2): `affirmation.kind` is
    trusted as a REAL in-session user signal (`user_affirmation` = the user
    explicitly affirmed) or a REAL test pass (`test_pass`, sourced by the deferred
    producer from `lifecycle_signals` `command_class=test`, spec FR-4). The consumer
    trusts this typed record exactly as it trusts `sources` — verifying that a
    signal genuinely occurred is the producer's job, not the consumer's."""
    kind = (affirmation.get("kind") or "").strip()
    etype = _AFFIRMATION_EVENT.get(kind)
    if etype is None:
        return {"promoted": None, "reason": "unknown_kind"}
    if not _affirmation_targets(affirmation, filed_slug):
        return {"promoted": None, "reason": "mis_attributed"}
    sess = affirmation.get("session_id") or session_id
    if _already_promoted(filed_path, etype, sess):
        return {"promoted": None, "reason": "idempotent_skip"}
    ok = _EV.append_event(filed_path, etype, session_id=sess,
                          note=f"m3 affirmation {kind}")
    if not ok:
        return {"promoted": None, "reason": "not_written"}  # dark / contended / de-duped
    _oplog(index_db_path, OP_PROMOTED, filed_slug, extra=f"event={etype} sess={sess}")
    return {"promoted": etype, "session_id": sess}


def affirm_filed_page(index_db_path, filed_path, affirmation, *, session_id=None):
    """Apply a genuine in-session affirmation to an already-filed page (the "later
    in the session" path). Dark-safe: a complete no-op unless M3 is active. Reads
    the page's own slug for the mis-attribution guard."""
    if not _active():
        return {"promoted": None, "reason": "dark"}
    rec = _M1._record_from_file(filed_path)
    if rec is None:
        return {"promoted": None, "reason": "unreadable"}
    return _apply_affirmation(index_db_path, filed_path, affirmation, rec["slug"],
                              session_id=session_id or affirmation.get("session_id"))


# --- the pipeline (FR-1/FR-2/FR-3/FR-7) --------------------------------------
def file_recalled_answer(index_db_path, provenance, *, memory_dir=None, cwd=None,
                         support_fn=None, neighbors_fn=None, m2_handoff=None,
                         affirmation=None):
    """File a recalled answer back into the wiki, gated. Returns an outcome dict
    with an "action" ∈ {noop, rejected, deduped_to_m2, filed}.

    Order (bias toward REJECT — the gate runs BEFORE any routing so an unsupported
    answer is never filed AND never handed to M2):
      1. dark gate (FR-7)          — off ⇒ complete no-op.
      2. FR-2 no sources           ⇒ reject outright.
      3. FR-2 claim-support gate   — ANY material claim below the floor ⇒ reject
                                     the whole answer (no page, no event).
      4. FR-1 dedup                — a near-duplicate ⇒ route to M2, no new page.
      5. FR-3 file                 — supported + novel ⇒ new page at 0.40.
    """
    # 1. FR-7 dark-safe.
    if not _active():
        return {"action": "noop", "reason": "dark"}

    answer = (provenance.get("answer_text") or "").strip()
    if not answer:
        # A3: log like every other reject path (consistency; no silent swallow).
        _oplog(index_db_path, OP_REJECTED, _title_for(provenance), extra="reason=empty_answer")
        return {"action": "rejected", "reason": "empty_answer"}

    # 2. FR-2: no traceable sources ⇒ reject outright (fail toward reject).
    spans = [(s.get("span") or "") for s in (provenance.get("sources") or [])
             if (s.get("span") or "").strip()]
    title = _title_for(provenance)
    if not spans:
        _oplog(index_db_path, OP_REJECTED, title, extra="reason=no_sources")
        return {"action": "rejected", "reason": "no_sources"}

    # 3. FR-2: claim-support gate WITH TEETH.
    support_fn = support_fn or active_support()
    material = [c for c in _split_claims(answer) if _is_material(c)]
    if not material:
        _oplog(index_db_path, OP_REJECTED, title, extra="reason=no_material_claims")
        return {"action": "rejected", "reason": "no_material_claims"}
    scores = {}
    for claim in material:
        try:
            sc = support_fn(claim, spans)
        except Exception:
            sc = None  # scorer error ⇒ fail toward REJECT
        if sc is None or sc < support_min():
            _oplog(index_db_path, OP_REJECTED, title,
                   extra=f"reason=unsupported_claim score={sc}")
            return {"action": "rejected", "reason": "unsupported_claim",
                    "claim": claim, "score": sc}
        scores[claim] = float(sc)

    # 4. FR-1: dedup via the S2 door.
    neighbors_fn = neighbors_fn or _default_neighbors
    try:
        import engine
        probe = engine.embedding_text(title, "", answer, "")
    except Exception:
        probe = f"{title}\n{answer}"
    try:
        hits = neighbors_fn(index_db_path, probe, ()) or []
    except Exception:
        hits = []
    top = hits[0] if hits else None
    if top is not None and float(top.get("score", 0.0)) >= dedup_min():
        (m2_handoff or _default_m2_handoff)(
            index_db_path, provenance, hits, top, memory_dir=memory_dir, cwd=cwd)
        _oplog(index_db_path, OP_DEDUPED, title,
               extra=f"neighbor={top.get('path')} score={float(top.get('score', 0.0)):.3f}")
        return {"action": "deduped_to_m2", "neighbor": top.get("path"),
                "score": float(top.get("score", 0.0))}

    # 5. FR-3: file supported + novel at cold-start 0.40 (empty ## Evidence).
    outcome = _file_new_page(index_db_path, provenance, title, scores,
                             memory_dir=memory_dir, cwd=cwd, m2_handoff=m2_handoff,
                             hits=hits)
    # FR-4: a GENUINE in-session affirmation (never minted by filing) may lift the
    # just-filed page across the gate. The filing act itself, with affirmation=None,
    # mints NOTHING — the turn-1 invariant is preserved.
    if outcome.get("action") == "filed" and affirmation:
        outcome["promotion"] = _apply_affirmation(
            index_db_path, outcome["path"], affirmation,
            _REM.target_slug(title, _FILE_KIND),
            session_id=provenance.get("session_id"))
    return outcome
