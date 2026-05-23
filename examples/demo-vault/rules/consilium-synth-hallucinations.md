---
type: rule
title: Consilium synth hallucinations + voice frame-bias
aliases: ["consilium-synth-hallucinations"]
tags: ["rule"]
---

# Consilium synth hallucinations + voice frame-bias

> Code-review cycle 2 caught synth inventing claims, mis-attributing convergences, and 5-voice frame-bias (mechanical accountants vs adversarial); REDTEAM verdict WEAK refuted 1 finding, demoted 2, identified 2 missed high-severity hallucinations.

**Why:** Synth pressures itself to produce a "recommendation paragraph" that synthesizes voices into a single narrative. To make the narrative coherent, it routinely invents global characterizations, rounds N-voice convergences up, misses 2-voice convergences when they don't fit the dominant theme, and reduces nuanced disagreements to "voices share the same finding set".

## Details

## Rule

**Consilium and code-review synth (Opus 4.7 xthinking) routinely invents global characterizations not present in voice files, mis-counts attributions, and misses convergences when voices have shared frame-bias. Always read AUDIT_VERDICT and REDTEAM_VERDICT (when present) before treating SYNTHESIS findings as actionable.**

1. Invents "global X is consistent" / "voices agree on Y" claims that no individual voice supports
2. Rounds N-voice convergences up by including voices that raised related-but-different issues
3. Misses 2-voice convergences when they don't fit the dominant theme
4. Reduces nuanced disagreements to "voices share the same finding set"

**REDTEAM (Gemini Pro adversarial) is the second line of defense** — it catches frame-bias and same-family echo chambers that synth + audit miss.

## How to apply

### When synth says "all voices agree" or "convergence is X voices"

1. **Verify by reading voice files directly** — cross-reference against the actual quoted lines, not the synth paraphrase
2. **Check for over-counting:** does each cited voice actually make the same claim? Or just adjacent claims?
3. **Check for under-counting:** are there 2-voice convergences synth folded into a different theme?

### When AUDIT_VERDICT says ISSUES

1. Do not apply raw SYNTHESIS
2. Apply audit's specific corrections (attribution counts, severity, removed inventions)
3. Add audit's missed convergences

### When REDTEAM_VERDICT says WEAK or has refuted findings

1. **HOLDS** — apply finding as-is
2. **WEAK** — demote one tier; consider self-mitigation or alternative reading
3. **REFUTED** — drop finding; another voice explicitly contradicted it
4. **MISSED by all voices** — investigate as separate finding (often the highest-signal output)

## Concrete incident

After cycle 1 review applied 11 fixes, ran cycle 2 to verify. SYNTHESIS = SHIP-WITH-EDITS, AUDIT = ISSUES, REDTEAM = WEAK.

**Synth inventions (caught by audit):** "SPEC is internally consistent" — invented as global synth claim. Contradicted by 3 voices on a SPEC/MANIFEST count mismatch.

**Synth attribution errors (caught by audit):** 4-voice template convergence overclaimed one voice (it said template exists in SPEC + has no usage in playbook; did NOT say SKILL.md missing it). Should have been split: 3 voices for SKILL.md missing template + separate 2-voice convergence for playbook missing usage.

**Synth missed convergence (caught by audit):** Two voices both raise that the playbook lacks a usable workflow reference. Synth folded into different convergence theme.

**REDTEAM caught what 5 voices missed (high-signal):** Two taxonomy hallucinations present in skill, no voice flagged. Frame-bias: "voices acted as mechanical accountants verifying Cycle 1 fixes, entirely missing two high-severity hallucinations".

**REDTEAM caught echo chambers:**

- **Same-family-only:** A count discrepancy flagged ONLY by one model family; the other family ignored
- **Same-family-only:** A cross-file contradiction flagged ONLY by the opposite model family

## Anti-patterns

- ❌ Treat SYNTHESIS as ground truth without reading AUDIT first
- ❌ Treat SYNTHESIS + AUDIT as ground truth without reading REDTEAM (when present)
- ❌ Ignore "REFUTED" findings — they are explicitly contradicted by other voices
- ❌ Apply "WEAK" findings without checking self-mitigation
- ❌ Assume voice convergence means voices made the SAME claim — often adjacent claims
- ❌ Trust same-family echo (3 same-family voices agreeing) without cross-family validation
- ❌ Forget that synth has narrative pressure → invents "all voices agree"

Related: [[consilium-redteam-mandatory]], [[consilium-4-tier-postprocessing]], [[voice-redteam]], [[voice-synthesizer]], [[voice-auditor]].

_Confidence: high · Source: my-project_
