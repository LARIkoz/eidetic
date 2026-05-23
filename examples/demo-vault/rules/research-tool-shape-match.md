---
type: rule
title: Research tool shape-match before firing
aliases: ["research-tool-shape-match"]
tags: ["rule"]
---

# Research tool shape-match before firing

> Trigger keyword match ≠ tool fit. Before firing max-research / iterative-research / direct-fetch — verify task shape, not just user words.

**Why:** A bug post-mortem — user asked for a corpus mapping. The agent fired `max-research` on the trigger word "deep research". Mismatch: PPX returned a meta-response, $0.06 burned, 4 of 5 tools failed or empty, pipeline killed at T+1min.

## Details

Trigger keywords ("deep research", "research") activate `max-research` skill, but **trigger ≠ shape match**. Before firing any research orchestration skill, run shape check:

- Goal = inventory enumeration (list all artifacts), not claim verification
- Source landscape = 1 author, 1 primary domain, 1 channel — no multi-source triangulation possible
- Output shape needed = table of materials, not HIGH/MED/LOW claims

**How to apply** — before firing a research skill, ask 3 questions:

1. **Goal shape:** is this **claim verification** ("is X true?") or **inventory enumeration** ("list all X")?
   - Verification → `max-research` (5-tool triangulation)
   - Inventory → direct fetch (Exa `/contents` + Gemini CLI on domain + WebFetch)

2. **Source landscape:** ≥3 independent sources expected, or 1 primary author/domain?
   - Multi-source → `max-research`
   - Single-source → direct fetch (5-tool sweep adds cost without signal)

3. **Project length:** single-shot or multi-week iterative?
   - Single → `/research` (max-research)
   - Multi-week with verifiable IDs (laws, PMIDs, standards, court cases) → `iterative-research`
   - Multi-week without verifiable IDs (corpus mapping, methodology decomposition) → bespoke direct-fetch flow, no skill

**Routing decision tree:**

```
User asks for research →

  Single-author corpus inventory?
    YES → Exa /contents + Gemini CLI on canonical domain + WebFetch on individual URLs.
          NO max-research, NO iterative-research.

  Multi-week regulatory/legal/medical with verifiable IDs?
    YES → iterative-research skill.

  Single-shot multi-source claim verification?
    YES → max-research (default tier — based on stakes).

  Non-English-heavy single-domain content?
    YES → Gemini CLI primary (best non-English) + Exa /contents fallback.
          max-research weak here (Exa/PPX skewed English).
```

**Anti-patterns (what NOT to do):**

- ❌ Fire max-research on inventory queries because user said "deep research"
- ❌ Treat "deep research" as a deterministic mapping to max-research — it's a hint, not a command
- ❌ Pass `--addons gemini31pro,serpapi` because skill docs list them — verify wiring first
- ❌ Stuff PROMPT.md with orchestration meta-instructions — that's for the script, not the LLMs

Related: [[deep-research-all-tools-required]], [[decide-from-context]], [[exa-over-webfetch]].

_Confidence: high · Source: my-project_
