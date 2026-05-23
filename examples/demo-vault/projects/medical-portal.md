---
type: project
title: Medical Knowledge Portal
aliases: ["medical-portal"]
tags: ["project"]
---

# Medical Knowledge Portal

_Status:_ Patient-facing portal for a rare condition. ~389 glossary terms, ~1470 tooltips, 22 review cycles, multi-model deep research.

> A patient-education portal for a specific rare condition — glossary, evidence-tier badges, prognosis tables, drug-interaction warnings.

## Details

**Status:** v5.0 LIVE.

- Static site on GitHub Pages
- ~389 glossary terms, ~1470 tooltips
- 22 review cycles, 3 medical AI models in deep research

### Notable findings this session

- Reframe: broader autoimmune topic → narrow rare-condition focus
- 22 review cycles: Sonnet, Codex, Gemini, Opus red-team, Qwen, OpenRouter (DeepSeek R1, Grok 4.20, GPT-5.4)
- **17 evidence-RCT badges downgraded** (reviews / mechanisms / consensus ≠ RCT)
- Pregnancy / breastfeeding safety overhaul (+6 supplements, new BF section)
- Critical drug-interaction warnings (low-dose naltrexone + opioids, curcumin + anticoagulants, piperine + P-gp)
- Scope disclaimers (related-disease data ≠ this-disease data)
- Prognosis factors table (9 rows), deep research synthesis from 3 medical AI models
- 6 new glossary entries, 0 missing keys after full audit
- "Meta-content check" rule added to CLAUDE.md

### Key decisions

- Portal refocused on the specific rare condition (was a general topic)
- Evidence-RCT = strictly RCT only. Reviews / mechanisms / consensus → ev-obs (observational)
- Prognosis: use ranges from multi-model consensus, not single studies
- For one related risk factor: honestly labeled as "extrapolated from related disease" (all 3 research models agreed: no data for this specific condition)
- Review cycle: Opus strict audit rejects ~34% of sub-agent findings as noise
- Multi-model review: DeepSeek R1 + Grok 4.20 = best medical review combo
- Qwen: content filter blocks medical text > 50K. Use code-only extraction
- Gemini CLI: both accounts rate-limited. OpenRouter as fallback

### Next

- PMID audit (76 sources)
- Anti-CarP antibody research integration
- Vegetarian / dietary content
- Breastfeeding detail expansion
- Print styles

Related: [[pmid-hallucination]], [[consilium-redteam-mandatory]].

_Confidence: high · Source: medical_
