# Transcript-grounded acquisition cards (M3 v3)

Status: accepted (2026-07-09, owner-grilled)

**Context.** M3's filing gate grounds every candidate against the existing `/memory/` corpus
(`m3_producer_driver.resolve_sources` filters to `/memory/` paths). That makes NEW-knowledge
candidates — decisions, findings, rules mined from a session — unfileable by construction:
there is no pre-existing span to entail them. Yet session knowledge on boxes without the
manual card-writing habit is genuinely lost today.

**Decision.** Acquisition-kind candidates are grounded against the SESSION TRANSCRIPT itself:
the miner proposes a claim plus a verbatim transcript quote; the producer mechanically verifies
the quote exists in the transcript (substring check — a quote that isn't there cannot exist);
the judge then checks claim ⊨ quote as usual. This verifies **faithful copy, not truth** — the
judge cannot catch an in-session statement that was simply wrong. That residual risk is priced
in via a trust contract on every acquisition card:

1. origin marker — `transcript / self-attested` + session id, always;
2. starting confidence below consolidation's 0.40;
3. never auto-supersedes an existing card — collisions go through M2 suggestion-only.

Clause 2 protects nothing until read-time confidence weighting is real: the
`EIDETIC_CONFIDENCE_RANKING` flag (dark since 1B) must be activated via its Phase-A A/B
protocol on any box where acquisition files. (The "0.55 injection gate" cited in some
docstrings does not exist at runtime — the only 0.55 is `confidence.DECAY_FLOOR`; those
docstrings are a documentation bug.)

**Considered options.** (1) Draft-queue confirmed next session — rejected: queues rot precisely
on the un-attended boxes this targets; an unconfirmed draft is the same lost knowledge plus
machinery. (2) Strengthening the manual habit via rules/hooks — rejected: does not cover the
actual failure mode (no owner watching), and manual cards are themselves judge-free
self-attestation, so the auto path with a mechanical copy-check is strictly stricter.

**Consequences.** Rollout is gated: 20-session dark run on the primary box → owner eyeball of
the would-file yield → activate at ≥70% keep-rate AND ≤1 dangerous-wrong (which M2 must catch);
kill below 50% keep-rate. Secondary boxes only after the primary gate passes and M2 +
confidence-ranking are live there. Consolidation (recall kinds, now including
assistant-volunteered recalls) keeps `/memory/` grounding unchanged and ships live in the same
miner revision while acquisition kinds run dark. Background sub-agent transcripts are out of
scope (separate plumbing; knowledge density unmeasured).
