---
type: rule
title: Smoke-test hypotheses incrementally
aliases: ["smoke-test-incrementally"]
tags: ["rule"]
---

# Smoke-test hypotheses incrementally

> User prefers small smoke tests on every hypothesis or step before committing to full runs — "more smoke tests at every step".

**Why:** User explicitly stated preference during a full-reclassify vs minimum-viable decision. Confirmed working style: hates committing to big scope on theoretical framing; wants empirical signal per step. Matches the pattern: prompt-validator fix → smoke canary → 2-voice review → then full 20-item gate; each step gated by smoke.

## Details

Prefer small smoke tests on hypotheses before full runs. On any multi-step plan, default to: run minimum viable slice → see real numbers → decide next step.

**How to apply:**

- For any multi-step plan with scope choice (e.g. 109 items vs 63K items), propose running the cheapest slice first to gather signal, even if operator leans toward ambitious path
- For any code change, smoke on 1-5 items before running on N=1000+
- For any migration/reclassify, propose 1K stage-gate before full N
- For any new hypothesis (axis discovery, prompt tweak, classifier rule), validate on 1-3 families before rolling to all 55+
- When proposing "Option A / B / C" style choices, prefer the one that produces smoke data in < 1 hour even if not the "final" answer
- Do NOT skip smoke with "it should work" logic. Always produce a number.

Related: [[dual-smoke-tests]], [[validate-agent-findings]], [[consilium-4-tier-postprocessing]].

_Confidence: high · Source: my-project_
