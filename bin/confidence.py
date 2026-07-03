#!/usr/bin/env python3
"""Eidetic v6 STEP 1B — evidence-driven confidence lifecycle (pure algebra).

Deterministic, no-LLM confidence fold + authority gate (spec §4–§5). This module
is PURE: it holds the constants, the cold-start table (§3.4), the managed/exempt
scope rule (§2.3), the deterministic event fold with the authority gate (§4.4),
and the `conf_w` ranking weight (§5.2). It never touches the DB or the model —
`index_impl` materializes the fold into `memory_chunks.confidence`, and
`search_impl`/`assemble_context` read `conf_w` behind the Phase-A dark flag.

Nothing here is public API; it is an internal v6 rail.
"""

# --- constants (spec §4.2, §5.2, §5.3) ---------------------------------------
CONF_MIN = 0.05          # clamp floor — a card is never made unretrievable by the lifecycle
CONF_MAX = 0.95          # clamp ceiling — always leaves headroom below an exempt card
CONF_FLOOR = 0.35        # conf_w = CONF_FLOOR + (1-CONF_FLOOR)*confidence
INJECT_GATE = 0.55       # managed feedback injected in ALWAYS-APPLY iff confidence >= this
OBSERVED_CAP = 0.10      # cumulative agent `observed` gain until a tier>=2 event (§4.2)
DECAY_FLOOR = 0.55       # decay never takes a card below this (§4.3)
DECAY_STEP = 0.10        # one `decayed` event subtracts this (only if confidence > DECAY_FLOOR)

# Cold-start confidence for a managed card at migration (§3.4).
COLD_FEEDBACK_INJECTED = 0.70
COLD_USER_EXPLICIT = 0.80
COLD_AGENT_EXTRACTED = 0.40

# event_type -> (actor_tier floor, base signed delta) — spec §4.1. The event's
# stored actor_tier wins when present (a `contradicted` can be tier 3 user OR
# tier 2 test); this table is the base delta + the minimum tier.
EVENT_SPECS = {
    "observed":         {"tier": 1, "delta": +0.05},
    "verified_by_test": {"tier": 2, "delta": +0.15},
    "confirmed":        {"tier": 3, "delta": +0.20},
    "contradicted":     {"tier": 2, "delta": -0.30},
    "corrected":        {"tier": 3, "delta": -0.40},
    "decayed":          {"tier": 2, "delta": -DECAY_STEP},  # synthetic silence event (§4.3)
}
EVENT_TYPES = frozenset(EVENT_SPECS)

ACTOR_TIERS = {"user-explicit": 3, "test": 2, "verification": 2, "agent-repetition": 1,
               "agent": 1, "agent-extracted": 1}

# Scope (§2.3): who carries the lifecycle.
EXEMPT_TYPES = {"user", "reference"}
EXEMPT_KINDS = {"concept", "entity"}


def clamp(x):
    return max(CONF_MIN, min(CONF_MAX, x))


def is_managed(type_, source, card_kind):
    """§2.3 scope table. feedback (any source) is managed; agent-extracted
    non-exempt cards are managed; user profiles, reference/concept/entity, and
    imported cards are exempt (conf_w = 1.0)."""
    t = (type_ or "").strip().lower()
    s = (source or "").strip().lower()
    k = (card_kind or "").strip().lower()
    if t == "feedback":
        return True
    if t in EXEMPT_TYPES:
        return False
    if k in EXEMPT_KINDS:
        return False
    if s == "imported":
        return False
    if s == "agent-extracted":
        return True
    return False  # user-explicit non-feedback etc. — not in the §2.3 managed table


def lifecycle_label(type_, source, card_kind):
    return "managed" if is_managed(type_, source, card_kind) else "exempt"


def cold_start_confidence(type_, source, card_kind, injected=True):
    """§3.4 cold-start. Only meaningful for managed cards. `feedback` wins by
    specificity (the first §3.4 row) regardless of source; then user-explicit,
    then agent-extracted. Exempt cards have no lifecycle (caller uses conf_w=1.0)."""
    t = (type_ or "").strip().lower()
    s = (source or "").strip().lower()
    if t == "feedback":
        return COLD_FEEDBACK_INJECTED
    if s == "user-explicit":
        return COLD_USER_EXPLICIT
    if s == "agent-extracted":
        return COLD_AGENT_EXTRACTED
    return COLD_FEEDBACK_INJECTED  # conservative default (managed but untyped)


def _event_tier(ev):
    if ev.get("actor_tier") is not None:
        try:
            return int(ev["actor_tier"])
        except (TypeError, ValueError):
            pass
    return EVENT_SPECS.get(ev.get("event_type", ""), {"tier": 1})["tier"]


def fold_confidence(cold_start, events, user_authored=False):
    """Deterministic fold (spec §4). Returns (confidence, flags).

    `events` is chronological [{"event_type", "actor_tier"(optional)}]; the delta
    is recomputed from event_type + sequence (NOT trusted from any stored/edited
    value), so the fold is idempotent on replay and recomputes from the CURRENT
    event set every reindex. Rules:
      * `observed` (tier 1): additive only, diminishing +0.05/n and capped at
        +OBSERVED_CAP cumulatively until a tier>=2 event (§4.2) — repetition alone
        can never cross the gate.
      * positive tier-3 events raise the tier-3 high-water mark (hwm).
      * a tier-2 negative event may lower confidence only DOWN TO the hwm (never
        below) and surfaces a `relation_claim` flag; only a tier-3 event may push
        below the hwm (§4.4). tier-1 never lowers confidence.
      * `decayed`: only if confidence > DECAY_FLOOR, drop to max(DECAY_FLOOR, c-step).
      * clamp to [CONF_MIN, CONF_MAX] throughout.
    `user_authored` seeds the hwm to the cold start (a user-authored card's
    origin is a tier-3 anchor), so a later tier-2 contradiction cannot lower it.
    """
    conf = clamp(cold_start)
    tier3_hwm = conf if user_authored else None
    observed_gain = 0.0   # cumulative observed gain since the last tier>=2 event
    n_observed = 0        # consecutive observed since the last tier>=2 event
    flags = []

    for ev in events:
        etype = ev.get("event_type", "")
        if etype not in EVENT_SPECS:
            continue
        tier = _event_tier(ev)
        base = EVENT_SPECS[etype]["delta"]

        if etype == "decayed":
            if conf > DECAY_FLOOR:
                conf = max(DECAY_FLOOR, conf - DECAY_STEP)
            continue

        if etype == "observed":  # tier 1 — additive, diminishing + capped
            n_observed += 1
            inc = 0.05 / n_observed
            inc = min(inc, max(0.0, OBSERVED_CAP - observed_gain))
            observed_gain += inc
            conf = clamp(conf + inc)
            continue

        # non-observed event → the diminishing/cap window resets on tier>=2.
        if tier >= 2:
            observed_gain = 0.0
            n_observed = 0

        if base >= 0:  # positive higher-tier event
            conf = clamp(conf + base)
            if tier >= 3:
                tier3_hwm = conf if tier3_hwm is None else max(tier3_hwm, conf)
        else:          # negative event
            proposed = clamp(conf + base)
            if tier >= 3:
                conf = proposed                 # tier-3 may lower below the hwm
                tier3_hwm = conf                # a user correction re-anchors the floor
            else:                               # tier-2: gated at the hwm
                floor = tier3_hwm if tier3_hwm is not None else CONF_MIN
                gated = min(conf, max(proposed, floor))
                if proposed < floor:            # the gate actually bit — surface it
                    flags.append("relation_claim")
                conf = gated

    return conf, flags


def conf_weight(confidence, managed):
    """§5.2. Managed: CONF_FLOOR + (1-CONF_FLOOR)*confidence (∈[0.3825,0.9675],
    always < 1). Exempt: 1.0 (behavior unchanged)."""
    if not managed:
        return 1.0
    return CONF_FLOOR + (1.0 - CONF_FLOOR) * confidence


def injected(confidence, managed):
    """§5.3 within-type gate for managed cards (exempt always injected)."""
    if not managed:
        return True
    return confidence >= INJECT_GATE
