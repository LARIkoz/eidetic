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

## 2026-07-13 addendum — durable acquisition policy

The first machine-commission smoke found two dangerous classes in five cards:
a recommended next stage had been promoted to a decision, and a correct-at-the-time
base-model workaround had become stale live configuration. Entailment cannot reject either:
the assistant transcript really did state both assertions.

Acquisition therefore has a separate durability boundary. Recommendations, task/ticket
status, next-session plans, live model/config/provider routes, availability, quotas, deploy
progress, and similar point-in-time operational state fail toward miss before the entailment
judge. Durable root causes, architecture choices, and standing rules remain eligible. The
semantic rule is in the miner prompt and a narrow deterministic rail rejects the known
high-precision transient shapes without spending a judge call.

Prompt iterations are append-only rounds. Every acquisition candidate, dark row, agent done
row, report, and commission source manifest carries `miner_policy`; the seen-cache includes
that policy in acquisition identity. Reports and new commissions read only the active policy,
while prior rows remain preserved as evidence. Within a paraphrase cluster the newest wording
is the representative and earlier wording remains visible as provenance, so an early Stop fire
cannot outrank a later correction merely by arriving first.

The read side is outcome-aware in both lanes. A later definitive retry outranks an earlier
provider/parser transient, while contradictory definitive outcomes are surfaced and excluded
from the gate instead of inheriting the historical first-row-wins behavior.

A bounded isolated replay over the five cards from the failed first commission exposed two
additional extractor evasions: a recommendation without "next step" wording and a live model
parameter assignment framed as a rule. The deterministic rail therefore covers explicit
recommend/propose forms plus model/config field settings. On the final replay, all nine
extracted acquisition candidates were rejected before entailment (one next-step, one task
status, four recommendations/proposals, and three live config/route candidates); production
events were not mutated. This replay is regression evidence, not a replacement D5 sample:
the active policy remains `NO_DATA` until new organic durable candidates accumulate.
