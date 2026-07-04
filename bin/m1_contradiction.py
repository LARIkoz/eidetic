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


def _default_confirmer(a, b):
    """Fail-closed default (FR-3): with no wired NLI/LLM judge, never claim a
    contradiction. A real deterministic NLI/rerank confirmer is a turn-2 wiring;
    tests inject a deterministic confirmer. ANY doubt → no_contradiction."""
    return "no_contradiction"


def process_card(card_path, meta, body, *, neighbors, confirmer=None):
    """Run M1 for one ingested card C against its `neighbors` (a list of hit dicts
    with at least {score, path}). `confirmer(a_record, b_record) ->
    {contradiction|no_contradiction|uncertain}` (default fail-closed). Returns a
    list of outcome dicts for diagnostics/tests. Writes a `contradicted` event on
    the loser ONLY on a confirmed conflict with a demotable loser AND when
    EIDETIC_CONFIDENCE_EVENTS is on (the write is gated inside append_event)."""
    confirmer = confirmer or _default_confirmer
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
            # tier-3 hwm → surface a relation_claim, emit NO event (spec §4.4).
            outcomes.append({"loser": loser["path"], "winner": winner["slug"],
                             "action": "relation_claim"})
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


# --- ingest wiring (dormant until a real confirmer is registered) -----------
# M1's production confirmer (a deterministic NLI/rerank pass and/or a local
# LLM-judge, FR-3) is a turn-2 wiring. Until one is registered here, the ingest
# hook is a PURE NO-OP: it performs NO neighbor retrieval and NO writes, so
# enabling EIDETIC_CONFIDENCE_EVENTS alone incurs zero M1 cost and cannot change
# any card. Tests exercise process_card() directly with an injected confirmer.
_ACTIVE_CONFIRMER = None


def register_confirmer(fn):
    """Install the production confirmer (turn-2). None → dormant hook."""
    global _ACTIVE_CONFIRMER
    _ACTIVE_CONFIRMER = fn


def run_on_ingest(conn, index_db_path, changed_paths):
    """Ingest hook (spec FR-1/FR-7). Dark-safe: no-op unless
    EIDETIC_CONFIDENCE_EVENTS is on AND a real confirmer is registered AND a
    vectors.db exists. For each just-(re)indexed card, probe neighbors through
    the v1.1 door and run the M1 pipeline. Never raises into the indexer."""
    if _ACTIVE_CONFIRMER is None or not _EV.events_enabled():
        return  # dormant / dark → zero cost, zero writes
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
                             rec["text"], neighbors=hits, confirmer=_ACTIVE_CONFIRMER)
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
