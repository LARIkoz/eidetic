# Eidetic Memory System

Personal memory engine: markdown cards in `/memory/`, indexed + injected into agent sessions.
The M3 loop auto-files usage-proven knowledge back into the store, gated by an entailment judge.

## Language

**Memory card**:
One markdown file in `/memory/` holding one fact. The store's only unit — "wiki pages" filed
by M3 are memory cards too; there is one store with two writers (the agent by hand, M3 automatically).
_Avoid_: wiki page (as if a separate store), note

**Acquisition**:
NEW knowledge entering the store. Today this is the agent's manual card-writing habit; M3 v3
adds transcript-grounded auto-acquisition (see [ADR-0001](docs/adr/0001-transcript-grounded-acquisition.md)).
_Avoid_: capture, mining (when consolidation is meant)

**Consolidation**:
Re-filing knowledge that ALREADY exists in the store and proved useful in a session (a recall
happened — asked-for or volunteered) as a new, better-shaped memory card. This is what M3 does.
_Avoid_: acquisition, expansion

**Volunteered recall**:
The assistant stating remembered knowledge unprompted, without a user question about the past.
Counts as recall evidence for consolidation, same as question-answered recall.

**Self-attestation**:
Grounding a claim in the assistant's own in-session statement rather than in the existing store.
Verifies faithful copy, not truth. Acquisition cards are self-attested and trust-marked for it.

**Acquisition card**:
A memory card auto-filed from new session knowledge under self-attestation: origin-marked,
starts below consolidation trust, never displaces an existing card on its own.

**Miner**:
The session-end extractor (`m3_recall_miner`) that proposes candidates from a transcript.
Its proposed spans are never trusted unverified: consolidation spans are retrieved from the
store by the producer; acquisition quotes are mechanically checked against the transcript.

**Producer**:
The grounding step (`m3_producer_driver`) that resolves the spans a candidate is judged
against. The miner proposes; the producer grounds.

**Judge**:
The claim-entailment gate (`m3_judge`, LLM): files a candidate only when its claim is entailed
by the producer-resolved spans, verbatim-quote-verified. Fail-toward-reject.
