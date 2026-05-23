---
type: rule
title: Dual smoke tests (direct + RED Team) before any code change
aliases: ["dual-smoke-tests"]
tags: ["rule"]
---

# Dual smoke tests (direct + RED Team) before any code change

> Before any DB UPDATE, schema change, or code modification — run TWO rounds of smoke tests. Round 1 = direct verification of the assumption. Round 2 = adversarial RED Team angle that attacks the assumption from a critic's perspective.

**Why:** Round 1 alone confirms the claim within its own framing. RED Team probes WHETHER the framing itself is wrong. Without Round 2, "100% genres corroboration" can be a tautology (genres derived from category), "30/30 random looks correct" can be sample-bias on a pre-filtered slice. Two rounds catch what one misses.

**How to apply:** At every Path A/B/C step before UPDATE; at every code change with non-trivial blast radius; before commit when the change could be wrong in non-obvious ways. Document both rounds in handoff/findings — they're the audit trail when something later breaks.

## Details

Before any DB UPDATE, schema change, code modification, or destructive action — run **two distinct rounds of smoke tests**:

**Round 1 — Direct verification.** Sample the data. Count the scope. Cross-corroborate with adjacent signals. Answer: "does the assumption hold on a representative slice?"

**Round 2 — RED Team adversarial.** Attack the assumption from a critic's angle. Look for: tautologies (signals derived from each other and not independent), edge cases the rule misses, cross-platform/cross-cohort divergence, stale data, dev/group consistency violations, false positives my regex/filter wouldn't catch, sample bias from the filter itself.

**Anchor incident:** Round 1 said "100% genres-say-game" → looked airtight. Round 2 RED Team revealed `genres = ["GAME_X"]` is just JSON-formatted `category` — same field, no independent signal. A later round then revealed the real story: the 11,009 cohort was platform-only because one platform scrape didn't propagate `is_game` from category, while the other platform already did. Without RED Team round, we'd have committed believing 3 signals converged when really only 2 did.

Related: [[smoke-test-incrementally]], [[consilium-redteam-mandatory]].

_Confidence: high · Source: my-project_
