---
type: rule
title: Consilium red-team round mandatory for design decisions
aliases: ["consilium-redteam-mandatory"]
tags: ["rule"]
---

# Consilium red-team round mandatory for design decisions

> Multi-model consilium without an adversarial round produces confident hallucinations via groupthink — red-team round catches them.

**Why:** Single-round format gives models a brief that primes collaborative agreement. Round 2 adversarial explicitly asks "attack round 1, find hallucinations, steelman opposition" — breaks the frame.

## Details

Single-round consilium (N models giving opinion on same brief) is insufficient for high-stakes design decisions. Models converge on shared confident conclusions via different reasoning paths — classic groupthink — and propagate hallucinations between each other.

**Documented case (bid signal consilium):**

Round 1 (6 models, collaborative): unanimous "skip $ data, build bidders_per_keyword" with 70-80% strategic signal claim.

Round 2 (3 red-team models, adversarial) found:

- 6 concrete hallucinations, including:
  - False empirical claim "bid data updates daily" (actually weekly)
  - Factual errors — items mis-labeled as belonging to wrong product family
  - Fabricated precision "70-80% strategic signal" (no source)
  - Wrong API facts — "API requires active campaign" (actually has free benchmark tier)
- Groupthink: all 6 models reached same conclusion via different reasoning paths, no steelmanning
- Critical blind spots missed: temporal dynamics, coverage bias, trial sampling quality
- Strongest counter-position never steelmanned

**How to apply:**

- For any design decision with 3+ viable paths or architectural ambiguity — run 2-round consilium:
  - Round 1: 4-6 models, collaborative brief
  - Round 2: 2-3 different models (or same with adversarial prompt), given round 1 responses as context, explicit instructions to attack, find hallucinations, steelman counter-positions
  - Synthesize ONLY after round 2
- Search-augmented reasoning models are particularly strong red-team voices (catch factual errors pure-reasoning models miss)
- Reasoning models tend to produce concrete validation tests — use for empirical verification
- Cost: ~2x consilium budget. Time: ~2x latency. Value: catches false-confidence that would cost 10-40h engineering on unvalidated signals.

**Template prompt for round 2:**

```
You are being asked to ATTACK the round-1 synthesis. Your job: find hallucinations,
unverified claims, blind spots, wrong conclusions. Do NOT just agree — identify at
least 3 weaknesses. Sections: (1) HALLUCINATION HUNT, (2) GROUPTHINK DETECTION,
(3) WHAT ROUND 1 MISSED, (4) STRONGEST COUNTER-POSITION (steelman), (5) CONCRETE
SQL/EMPIRICAL TEST, (6) VERDICT (ENDORSE / ENDORSE-WITH-CAVEAT / REWORK / REJECT).
```

Related: [[consilium-4-tier-postprocessing]], [[consilium-synth-hallucinations]], [[voice-redteam]].

_Confidence: high · Source: my-project_
