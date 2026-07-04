#!/usr/bin/env python3
"""Eidetic v6 M1 — semantic contradiction detection (on the confidence rails).

Karpathy-M1 (contradiction). On ingest of a card C, retrieve C's vector-neighbors
through the Engine API v1.1 door, gate candidates by a recall-oriented cosine
floor (`M1_CANDIDATE_MIN`, decoupled from the compound duplicate line), run an
OPTIONAL confirmer that DEFAULTS to no_contradiction under ANY uncertainty/error
(fail-closed), and — only on a CONFIRMED conflict with a valid lower-authority
loser — append ONE typed `contradicted` event (tier-2, Δ−0.30) to the LOSING
card's `## Evidence` (spec §8). The down-rank rides that event; there is NO
separate/sticky penalty column, M1 never touches `superseded_by` (that is M2),
and the automated `contradicted` is ALWAYS tier-2 so the fold's tier-3 authority
gate can never let it nuke a user card below its high-water mark (spec §4.4).

Dark-safe: writes go through `evidence.append_event`, gated behind
`EIDETIC_CONFIDENCE_EVENTS` (default OFF). With the flag off M1 computes and can
diagnose candidates but writes nothing and changes no ranking.

Nothing here is public API; it is an internal v6 rail. The confirmer and the
neighbor source are INJECTABLE so tests are hermetic and deterministic.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import confidence as _C  # noqa: E402
import evidence as _EV  # noqa: E402
import index_impl as _IDX  # noqa: E402

try:
    from constants import M1_NEIGHBORS, M1_CANDIDATE_MIN, M1_CANDIDATE_MIN_DEFAULT
except ImportError:  # pragma: no cover
    M1_NEIGHBORS = 8
    M1_CANDIDATE_MIN = {"multilingual": 0.58, "english": 0.38}
    M1_CANDIDATE_MIN_DEFAULT = 0.58

# Card source → authority rank for loser selection (spec §4.4). Only
# user-explicit carries a tier-3 high-water mark in the fold (user_authored).
_SOURCE_AUTHORITY = {"user-explicit": 3, "agent-extracted": 2,
                     "system-generated": 1, "imported": 1}

# The automated confirmer's actor tier is ALWAYS 2 (test/verification). tier-3 is
# reserved for a USER-STATED conflict — a tier-3 from a non-user confirmer would
# re-anchor the fold floor below the hwm and bypass the authority cap (FR-5/AC-5).
AUTOMATED_ACTOR = "test"   # → tier 2 via confidence.ACTOR_TIERS
AUTOMATED_TIER = 2


def candidate_min(profile):
    """Profile-aware candidate-gate floor (S3); unknown profile → stricter end."""
    return M1_CANDIDATE_MIN.get((profile or "").strip().lower(), M1_CANDIDATE_MIN_DEFAULT)


def _authority(source):
    return _SOURCE_AUTHORITY.get((source or "").strip().lower(), 2)


def _record_from_file(path):
    """Build a card record from its file (frontmatter + body). None if unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    meta, body = _IDX.parse_frontmatter(text)
    return _record(path, meta, body)


def _record(path, meta, body):
    card_kind = _IDX.infer_card_kind(meta, path)
    return {
        "path": path,
        "name": meta.get("name") or "",
        "slug": _IDX._card_slug(meta, path),
        "project": _IDX.detect_relation_namespace(path, _IDX.detect_project(path) or ""),
        "type": meta.get("type") or "",
        "source": meta.get("source") or "",
        "card_kind": card_kind,
        "last_verified": meta.get("last_verified") or "",
        "authority": _authority(meta.get("source")),
        "text": body or "",
        "events": _IDX.parse_evidence_events(body or ""),
    }


def pick_loser(a, b):
    """Deterministic loser/winner (spec §4.4): higher authority wins; tie → newer
    (by last_verified) wins; tie → normalized_slug (larger slug loses)."""
    if a["authority"] != b["authority"]:
        return (a, b) if a["authority"] < b["authority"] else (b, a)
    if a["last_verified"] != b["last_verified"]:
        return (a, b) if a["last_verified"] < b["last_verified"] else (b, a)
    return (a, b) if a["slug"] >= b["slug"] else (b, a)


def _would_lower(loser):
    """True iff a tier-2 `contradicted` would actually lower the loser's confidence
    — i.e. it is NOT gated at the loser's tier-3 high-water mark (spec §4.4). A
    gated event has no ranking effect, so M1 surfaces a relation_claim instead."""
    cold = _C.cold_start_confidence(loser["type"], loser["source"], loser["card_kind"])
    ua = (loser["source"] or "").strip().lower() == "user-explicit"
    before, _f = _C.fold_confidence(cold, loser["events"], user_authored=ua)
    after, _f2 = _C.fold_confidence(
        cold, list(loser["events"]) + [{"event_type": "contradicted", "actor_tier": AUTOMATED_TIER}],
        user_authored=ua)
    return after < before - 1e-12


def _already_contradicted(loser_path, winner_slug):
    """Idempotence guard (FR-8): a `contradicted` event for this (loser, winner)
    pair already on the loser's `## Evidence` (content-keyed, NOT ts-keyed)."""
    rec = _record_from_file(loser_path)
    if rec is None:
        return False
    for ev in rec["events"]:
        if ev["event_type"] == "contradicted" and winner_slug in (ev.get("note") or ""):
            return True
    return False


def _note_for(winner):
    return f"conflicts with {winner['slug']}"


def _persist_relation_claim(index_db_path, loser, winner):
    """Durably record an authority-capped conflict as a `relation_claim` finding in
    the drift store (drift_type penalty 1.0 — visible in drift/lint, NEVER applied
    to ranking), the SAME surface as an authority-refused declared relation. Best-
    effort + dark-safe: no-op if EIDETIC_CONFIDENCE_EVENTS is off or no db path.
    Returns True iff a finding was written. Never raises into the pipeline."""
    if not index_db_path or not _EV.events_enabled():
        return False
    try:
        import drift_check
        drift_db = drift_check.get_drift_db_path(index_db_path)
        conn = drift_check.init_drift_db(drift_db)
        try:
            detail = (f"contradicted-claim by={winner['slug']} "
                      f"(m1; below authority; not penalized)")
            drift_check.write_findings(
                conn, [(loser["path"], loser.get("type") or None,
                        "relation_claim", detail)])
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"WARN: M1 relation_claim persist skipped: {e}",
              file=__import__("sys").stderr)
        return False


# --- FR-3 production confirmer -----------------------------------------------
# The confirmer OWNS precision (the candidate gate is deliberately permissive,
# ROADMAP risk #1 = false-positive poison). No NLI model / LLM is installable on
# this host (no torch/transformers/ollama; the S5 reranker's ONNX is absent →
# engine.rerank is SOFT-unavailable here), so precision rests on a HIGH-PRECISION
# deterministic opposition detector: it fires ONLY on an explicit opposing signal
# over a SHARED FRAME (same subject) — never on mere topical overlap. The S5
# cross-encoder is wired as an optional same-topic corroboration (deploy-gated,
# threshold to be calibrated when the reranker is provisioned); a stronger
# NLI/LLM judge can be registered via register_confirmer(). Fail-closed at every
# edge: no signal / any error / model doubt → no_contradiction.
import re as _re

_STOP = frozenset(
    "the a an is are was were be been to of in on for and or by with as at from that this "
    "it its their our your we use uses using should must will would can may per via than then "
    "default now always set sets get gets has have had do does not-a".split())

# One side explicitly negates a shared clause the other asserts.
_NEG_CUES = frozenset(
    "not no never without none cannot cant dont doesnt isnt arent wasnt werent wont neither "
    "nor stop stopped disable disabled off false removed remove deprecated".split())

# Unambiguous antonym pairs (opposite members ⇒ opposing claim on a shared frame).
_ANTONYMS = [
    {"enabled", "disabled"}, {"enable", "disable"}, {"true", "false"}, {"on", "off"},
    {"allow", "deny"}, {"allowed", "denied"}, {"required", "optional"}, {"always", "never"},
    {"increase", "decrease"}, {"increased", "decreased"}, {"add", "remove"},
    {"added", "removed"}, {"include", "exclude"}, {"included", "excluded"},
    {"valid", "invalid"}, {"active", "inactive"}, {"present", "absent"},
    {"accept", "reject"}, {"grant", "revoke"}, {"granted", "revoked"}, {"open", "closed"},
    {"success", "failure"}, {"sync", "async"}, {"synchronous", "asynchronous"},
    {"public", "private"}, {"ascending", "descending"}, {"before", "after"},
]

# Curated mutually-exclusive term sets: two DIFFERENT members on a shared frame is
# an opposing claim (a "primary datastore is X" can be exactly one). Deliberately
# small + high-confidence (the flagship Postgres↔MySQL case); extend at deploy.
_EXCLUSIVE_SETS = [
    {"postgres", "postgresql", "mysql", "mariadb", "sqlite", "mongodb", "mongo",
     "oracle", "mssql", "cassandra", "dynamodb", "cockroachdb"},
]


def _toks(s):
    return _re.findall(r"[a-z0-9]+", (s or "").lower())


def _content(toks):
    return {t for t in toks if t not in _STOP}


def _nums(toks):
    return {t for t in toks if any(ch.isdigit() for ch in t)}


def _frame_overlap(sa, sb, *, strong=False, ignore=frozenset()):
    """Jaccard of the two content-token frames ≥ threshold — the two statements
    are about the SAME subject, so the opposing token is a real conflict, not two
    unrelated sentences that happen to share one polarity word."""
    sa = set(sa) - ignore
    sb = set(sb) - ignore
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= (0.5 if strong else 0.34)


def opposition(a_text, b_text):
    """Return a reason string if (a, b) is an explicit 'same entity, opposite
    claim' pair, else None. HIGH PRECISION: every branch requires a shared frame
    plus a distinct opposing signal on each side."""
    ta, tb = _toks(a_text), _toks(b_text)
    ca, cb = _content(ta), _content(tb)
    if not ca or not cb:
        return None
    # 1. antonym pair — each side carries a DISTINCT member of the pair.
    for pair in _ANTONYMS:
        a_side, b_side = ca & pair, cb & pair
        if (a_side - b_side) and (b_side - a_side) and \
                _frame_overlap(ca - pair, cb - pair):
            return f"antonym:{sorted(pair)}"
    # 2. mutually-exclusive set — different, non-overlapping members on a frame.
    for st in _EXCLUSIVE_SETS:
        a_mem, b_mem = ca & st, cb & st
        if a_mem and b_mem and not (a_mem & b_mem) and \
                _frame_overlap(ca - st, cb - st):
            return f"exclusive:{sorted(a_mem)}!={sorted(b_mem)}"
    # 3. negation asymmetry — exactly one side negates a strongly-shared frame.
    na, nb = bool(set(ta) & _NEG_CUES), bool(set(tb) & _NEG_CUES)
    if na != nb and _frame_overlap(ca - _NEG_CUES, cb - _NEG_CUES, strong=True):
        return "negation_asymmetry"
    # 4. numeric-slot conflict — same frame, different numeric value.
    numa, numb = _nums(ta), _nums(tb)
    if numa and numb and numa != numb and \
            _frame_overlap(ca, cb, strong=True, ignore=numa | numb):
        return "numeric_conflict"
    return None


# Cross-encoder corroboration is OFF by default: the S5 reranker is not provisioned
# on this host and its same-topic threshold is uncalibrated, so shipping it active
# would risk SUPPRESSING true contradictions on an unmeasured cut. Enable at deploy
# (with the reranker installed) via EIDETIC_M1_CROSS_ENCODER=on after calibrating
# _CE_SAME_TOPIC_MIN. It can only DOWNGRADE a verdict (never create one).
_CE_SAME_TOPIC_MIN = 0.0  # deploy-calibrate; jina-reranker-v2 relevance logit


def _cross_encoder_enabled():
    return (os.environ.get("EIDETIC_M1_CROSS_ENCODER", "").strip().lower()
            in ("1", "on", "true", "yes"))


def _ce_same_topic(a_text, b_text):
    """Optional same-topic corroboration via the S5 door. None if unavailable/
    disabled (→ skip corroboration); True/False if the reranker scored the pair."""
    if not _cross_encoder_enabled():
        return None
    try:
        import engine
        s = engine.rerank(a_text, [b_text])
    except Exception:
        return None
    if not s:  # SOFT-unavailable (no model) → cannot corroborate
        return None
    return s[0] >= _CE_SAME_TOPIC_MIN


def production_confirmer(a, b):
    """FR-3 confirmer: contradiction ONLY on an explicit deterministic opposition
    over a shared frame, optionally corroborated (never created) by the S5
    cross-encoder. Fail-closed: no opposition / any error / CE says off-topic →
    no_contradiction (via `uncertain`). Deterministic ⇒ reproducible AC fixtures."""
    try:
        reason = opposition(a.get("text", ""), b.get("text", ""))
    except Exception:
        return "no_contradiction"
    if not reason:
        return "no_contradiction"
    if _ce_same_topic(a.get("text", ""), b.get("text", "")) is False:
        return "uncertain"  # topically apart despite lexical opposition → NC upstream
    return "contradiction"


# Backward-compatible alias (turn-1 name); the fail-closed default is now the real
# production confirmer, not a stub.
_default_confirmer = production_confirmer


def process_card(card_path, meta, body, *, neighbors, confirmer=None, index_db_path=None):
    """Run M1 for one ingested card C against its `neighbors` (a list of hit dicts
    with at least {score, path}). `confirmer(a_record, b_record) ->
    {contradiction|no_contradiction|uncertain}` (default = production_confirmer).
    Returns a list of outcome dicts for diagnostics/tests. Writes a `contradicted`
    event on the loser ONLY on a confirmed conflict with a demotable loser AND when
    EIDETIC_CONFIDENCE_EVENTS is on (gated inside append_event). An authority-capped
    conflict persists a durable `relation_claim` diagnostic instead (when
    index_db_path is given)."""
    confirmer = confirmer or production_confirmer
    c = _record(card_path, meta, body)
    outcomes = []

    # dedup neighbors by path (best score), drop self, gate by cosine + same project
    best = {}
    for hit in (neighbors or []):
        p = hit.get("path")
        if not p or p == card_path:
            continue
        best[p] = max(best.get(p, -1.0), float(hit.get("score", 0.0)))
    floor = candidate_min(_profile_hint())

    for path, score in sorted(best.items()):
        if score < floor:
            continue
        n = _record_from_file(path)
        if n is None:
            continue
        if n["project"] != c["project"]:
            outcomes.append({"path": path, "action": "skip_cross_project"})
            continue  # FR-6: cross-project neighbors never contradict
        verdict = "no_contradiction"
        try:
            v = confirmer(c, n)
            if v == "contradiction":
                verdict = "contradiction"
        except Exception:
            verdict = "no_contradiction"  # fail-closed
        if verdict != "contradiction":
            outcomes.append({"path": path, "action": "no_contradiction"})
            continue

        loser, winner = pick_loser(c, n)
        if loser["path"] == card_path == winner["path"]:  # self-guard (FR-8)
            continue
        if _already_contradicted(loser["path"], winner["slug"]):
            outcomes.append({"loser": loser["path"], "winner": winner["slug"],
                             "action": "skip_idempotent"})
            continue
        if not _would_lower(loser):
            # authority cap: a tier-2 event cannot lower the loser below its
            # tier-3 hwm → emit NO confidence event; persist a DURABLE
            # relation_claim diagnostic instead so the dispute stays visible
            # (penalty 1.0, never ranks — the same surface as an authority-refused
            # declared relation, spec §4.4).
            persisted = _persist_relation_claim(index_db_path, loser, winner)
            outcomes.append({"loser": loser["path"], "winner": winner["slug"],
                             "action": "relation_claim",
                             "persisted": persisted})
            continue
        wrote = _EV.append_event(loser["path"], "contradicted", actor=AUTOMATED_ACTOR,
                                 note=_note_for(winner))
        outcomes.append({"loser": loser["path"], "winner": winner["slug"],
                         "action": "event" if wrote else "gated_off"})
    return outcomes


_profile_cache = None


def _profile_hint():
    """The active embed profile via the door (S3), cached; 'multilingual' if the
    door is unavailable (the stricter end)."""
    global _profile_cache
    if _profile_cache is None:
        try:
            import engine
            _profile_cache = engine.profile()
        except Exception:
            _profile_cache = "multilingual"
    return _profile_cache


# --- ingest wiring (ACTIVE: production confirmer registered by default) ------
# Turn-2 activates the hook: the M1 pipeline runs on ingest with the
# production_confirmer, gated ONLY by EIDETIC_CONFIDENCE_EVENTS (default OFF, the
# single dark-safe off-switch) + a vectors.db. Deploy may register a STRONGER
# judge (NLI/LLM) via register_confirmer(); register_confirmer(None) restores the
# built-in production confirmer (NOT a no-op — precision now lives in the
# deterministic detector). With the flag OFF the hook returns immediately: zero
# retrieval, zero writes, zero diff.
_ACTIVE_CONFIRMER = None


def register_confirmer(fn):
    """Register a stronger deploy-time judge, or None to use the built-in
    production_confirmer. Either way the hook is ACTIVE (gated by the flag)."""
    global _ACTIVE_CONFIRMER
    _ACTIVE_CONFIRMER = fn


def active_confirmer():
    return _ACTIVE_CONFIRMER or production_confirmer


def run_on_ingest(conn, index_db_path, changed_paths):
    """Ingest hook (spec FR-1/FR-7). Dark-safe: no-op unless
    EIDETIC_CONFIDENCE_EVENTS is on. For each just-(re)indexed card, probe
    neighbors through the v1.1 door (SOFT [] on an FTS-only install ⇒ no-op) and
    run the M1 pipeline with the active confirmer. Never raises into the indexer."""
    if not _EV.events_enabled():
        return  # dark → zero cost, zero writes
    confirmer = active_confirmer()
    for path in changed_paths:
        try:
            rec = _record_from_file(path)
            if rec is None:
                continue
            try:
                import engine
                probe = engine.embedding_text(rec["name"], "", rec["text"], "")
            except Exception:
                probe = f"{rec['name']}\n{rec['text']}"
            hits = neighbors_via_door(index_db_path, probe, exclude_paths={path})
            if hits:
                process_card(path, {"name": rec["name"], "type": rec["type"],
                                    "source": rec["source"],
                                    "last_verified": rec["last_verified"]},
                             rec["text"], neighbors=hits, confirmer=confirmer,
                             index_db_path=index_db_path)
        except Exception as e:  # never break ingest on an M1 hiccup (fail-closed)
            print(f"WARN: M1 skipped {path}: {e}", file=__import__("sys").stderr)


def neighbors_via_door(index_db_path, probe_text, exclude_paths=()):
    """Retrieve neighbors through the v1.1 door (S2). SOFT: [] if no vectors.db /
    no model (FR-1 no-op on an FTS-only install). Never raises."""
    vectors_db = index_db_path.replace("index.db", "vectors.db")
    if not os.path.exists(vectors_db):
        return []
    try:
        import engine
        with engine.open_index(vectors_db) as idx:
            return idx.neighbors(probe_text=probe_text, limit=M1_NEIGHBORS,
                                 exclude_paths=set(exclude_paths))
    except Exception:
        return []
