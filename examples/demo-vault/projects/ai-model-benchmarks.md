---
type: project
title: AI Model Benchmarks repo
aliases: ["ai-model-benchmarks"]
tags: ["project"]
---

# AI Model Benchmarks repo

_Status:_ Open-source LLM benchmark reference — 119 models, 55 benchmarks, capabilities auto-synced, daily CI, portal live. v1.1 shipped. 3 PRs pending.

> Public repo + portal aggregating LLM benchmarks for routing decisions.

## Details

- **Code:** local checkout
- **Repo:** public GitHub repo
- **Portal:** GitHub Pages, auto-updated daily
- **Status:** v1.1 SHIPPED. 5 review cycles, Codex 9/10.

### Data

- 119 models, 55 benchmarks, 26 embeddings
- 97/119 with auto capabilities (OpenRouter daily sync)
- 15 top models with manual data (cutoff, caching, effective context)
- Affiliation tracking on 14 benchmarks (high/medium/low risk)
- All 55 benchmarks have description + URL

### Anti-staleness

| Layer                  | Auto          | Detection                 |
| ---------------------- | ------------- | ------------------------- |
| Pricing                | Daily CI      | validate.py >7d           |
| Capabilities           | Daily CI      | Auto-refreshed            |
| Portal + llms-full.txt | Daily CI      | Regenerated               |
| Scores                 | Manual weekly | validate.py by volatility |
| Manual caps            | Manual qtrly  | validate.py >90d          |

### CI pipeline

`sync_capabilities → fetch_prices → generate_portal → validate → commit`

### PRs pending

- 3 awesome-list PRs (combined ~120K stars)
- 1 fork pending

### Growth strategy

- HN "Show HN" = highest ROI (300-400 stars)
- Target: 1000 stars by mid-year

### Skill integration

- Repo = source of truth.
- Skill `model-benchmarks-reference` = snapshot + pipeline routing
- Sync: `generate_md.py → skill`

### Next

- [ ] Apply 13 empty-benchmark research
- [ ] Check PR merge status
- [ ] HN "Show HN"
- [ ] Full portal content audit (5 cycles)
- [ ] Sync MODEL_BENCHMARKS.md from repo

Related: [[model-benchmarks-reference]], [[seo-geo-for-public-repos]].

_Confidence: high · Source: my-project_
