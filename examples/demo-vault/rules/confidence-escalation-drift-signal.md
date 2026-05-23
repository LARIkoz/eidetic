---
type: rule
title: Confidence escalation without new data = drift signal
aliases: ["confidence-escalation-drift-signal"]
tags: ["rule"]
---

# Confidence escalation without new data = drift signal

> When I raise confidence on a recommendation across iterations without new evidence, and user pushes back 2+ times asking to reconsider, that is a recalibration signal — not a prompt for more detail.

## Details

## Rule

If I find myself raising confidence on a recommendation across re-asks (e.g. 0.75 → 0.85) without new data in between — **stop and recalibrate downward**, not escalate detail.

User pushback like "think again" / "are you sure" = signal that my **calibration is off**, not that my explanation was insufficient. Adding more arguments for the same answer doubles down on miscalibration.

## Why

- User decides fast. If they ask twice to reconsider, they already see a hole I'm missing.
- Observed failure mode, twice on different topics in same session:
  - **Episode 1 (migration scope):** proposed migration → offered "compromise compressed inline" → escalated to "full inline verbatim" → each iteration raised confidence without new data. Took two user pushbacks to notice the agent was fixing an unmeasured problem.
  - **Episode 2 (backlog system):** proposed v1 with todo file → rejected → v2 tiered system with decay → pushback → v3 with tags → pushback → overcorrected to "do nothing" on user's low-confidence signal → user had to pull back to v4. Same drift on a storage system that was NOT measurably broken, just ad-hoc.

## How to apply

- On second user "think again" on same topic: **explicitly state confidence, identify unverified assumptions, and consider "do nothing" as a live option** — especially when previous fix is <24h old and untested.
- Distinguish "theoretical gap" (something COULD be wrong) from "measured gap" (something demonstrably failed). Only the second justifies architectural change.
- If the proposed fix sits on top of another untested fix — default to waiting for signal, not stacking.
- Failure criterion beats empirical intuition. Define "how will we know the current fix failed" before adding a new one.

## Anti-pattern to avoid

Iterative answer: "rec A (0.75) → user asks again → rec A' more detailed (0.85) → user asks again → rec A'' with even more defense". This is not better reasoning — it is a recalibration miss.

Related: [[defend-correct-answer]] (the inverse — don't flip-flop on a calibrated answer just because user reasks).

_Confidence: high · Source: my-project_
