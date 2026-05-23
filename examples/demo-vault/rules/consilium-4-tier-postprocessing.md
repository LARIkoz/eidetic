---
type: rule
title: Consilium post-processing — 4-tier verification
aliases: ["consilium-4-tier-postprocessing"]
tags: ["rule"]
---

# Consilium post-processing — 4-tier verification

> After consilium, classify every finding into BLOCKER/IMPORTANT/VERIFY-DEEPER/NOISE before applying. Catches hallucinations and inflated convergence counts. Empirical smoke tests = mini-research step.

**Why:** A 6-voice, 4-family consilium produced 3 fully confirmed findings, 3 disproven claims, 4 unverified claims, plus noise. Without post-processing, 3 false claims would have entered the canonical plan.

## Details

Consilium raw output requires structured post-processing before integration into plan. DO NOT apply findings blindly.

**How to apply — 4-tier classification:**

| Tier              | Definition                                     | Action                            | Example                                   |
| ----------------- | ---------------------------------------------- | --------------------------------- | ----------------------------------------- |
| **BLOCKER**       | Finding that changes plan structure if true    | Must verify before plan update    | "Component X is NOT DEFER — has full API" |
| **IMPORTANT**     | Valuable addition, low hallucination risk      | Add to plan with source tag       | "Provider prefix = unique, zero FP"       |
| **VERIFY DEEPER** | Plausible but unconfirmed — needs smoke test   | Run mini-research before trusting | "Vendor X has separate auth endpoint"     |
| **NOISE**         | Hallucinated, inflated, or too niche to matter | Discard                           | "Pre-classifier collapses 70% of cases"   |

**Verification methods (the "smoke test" mini-research):**

1. **Endpoint probe** (30sec/claim): `curl -s -o /dev/null -w "%{http_code}" --max-time 10 <endpoint> -H "Authorization: Bearer test"` — confirms endpoint exists and auth type
2. **Format check** (1min/claim): analyze actual values — length, charset, hex-vs-mixed
3. **Search-based check** (2min/claim): find real-world examples to confirm format
4. **Error body inspection** (30sec/claim): read HTTP error response — reveals auth type (Bearer vs HMAC vs SigV3)

**Total time:** 10-20 minutes for 10-12 claims. Cost: zero (all free probes).

**Integration into orchestration pipeline:**

```
consilium voices → SYNTHESIS.md + AUDIT_VERDICT.md
  → IF AUDIT = ISSUES: read raw v-*.md
  → 4-tier classification (this step)
  → Smoke tests on VERIFY-DEEPER items
  → Update plan with CONFIRMED items only
  → Tag VERIFY-DEEPER items with ⚠️
  → Discard NOISE
```

**Key lesson:** AUDIT catches synthesis-level problems (undercounts, inventions). 4-tier catches claim-level problems (hallucinated formats, wrong auth types). Both are needed. AUDIT alone is not enough — it flags ISSUES but doesn't verify individual claims empirically.

**Anti-pattern:** trusting consilium convergence counts as truth. "3 voices say X" ≠ "X is true". 3 voices claimed a provider uses mixed-case keys — all wrong (empirically: hex-only, 83/83 keys).

Related: [[consilium-redteam-mandatory]], [[consilium-synth-hallucinations]], [[smoke-test-incrementally]].

_Confidence: high · Source: my-project_
