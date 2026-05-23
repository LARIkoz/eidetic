---
type: rule
title: PMID hallucination — verify every citation
aliases: ["pmid-hallucination"]
tags: ["rule"]
---

# PMID hallucination — verify every citation

> AI agents hallucinate ~60% of PMIDs. Always verify via PubMed API before using in patient-facing content.

**Why:** Models generate plausible-looking 7-8 digit numbers that correspond to completely unrelated papers (chemistry, plant biology, unrelated studies instead of the intended topic).

## Details

AI models (including sub-agents, Codex, GPT-5.4) hallucinate approximately 60% of PubMed IDs when asked to cite sources.

**How to apply:**

1. NEVER trust PMIDs from any AI model output.
2. ALWAYS batch-verify via PubMed API: `eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=XXXXX&retmode=json`
3. Check title matches expected topic.
4. If wrong: search PubMed for correct paper via `esearch.fcgi?db=pubmed&term=KEYWORDS`.
5. Use `subprocess.run(["curl", ...])` for fetches (Python `urllib` gets 403 from Cloudflare on some endpoints).

In one audit session: 20 out of 33 PMIDs from agents were invalid. All replaced with verified ones.

Related: [[medical-portal]], [[validate-agent-findings]].

_Confidence: high · Source: medical_
