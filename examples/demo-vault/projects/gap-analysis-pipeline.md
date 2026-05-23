---
type: project
title: Gap Analysis Pipeline
aliases: ["gap-analysis-pipeline"]
tags: ["project"]
---

# Gap Analysis Pipeline

_Status:_ Niche discovery for mobile apps — finds categories with demand and weak competition. SQLite DB ~20GB, multi-model classification pipeline, ongoing quality work.

> Pipeline that ingests App Store data, classifies apps into niches, scores each niche for demand vs competition, surfaces gaps.

## Details

**Status:** ACTIVE. Phase 7 in flight. ~352 candidate niches in current run.

**Data:**

- ~1.2M app rows (iOS + Android)
- ~60M keyword rows
- ~5.75M app↔keyword edges
- SQLite WAL, ~19-20GB on disk

**Pipeline:**

1. **Ingest:** App Store scrape (top charts, category browse, keyword search)
2. **Enrich:** revenue estimates, download estimates, ASO data
3. **Classify:** multi-model batch classification into intent niches
4. **Cluster:** Louvain on app-keyword graph + revenue signal
5. **Score:** demand (search volume × CPC) vs competition (top-app concentration)
6. **Gap detect:** rank niches by demand/competition asymmetry
7. **Audit:** consilium review on top-50 candidates, manual review on top-10

### Cost-shape per phase

| Phase    | Cost driver            | Notes                                              |
| -------- | ---------------------- | -------------------------------------------------- |
| Ingest   | API credits            | Cached aggressively, ~$50 / full reingest          |
| Classify | LLM tokens             | ~$3 / 1K apps, parallel batches (see related rule) |
| Score    | CPU                    | ~10 min on M1 Max                                  |
| Audit    | Operator time + tokens | Consilium ~$5 per audit run                        |

### Open issues

- 87% of niches marked `inactive` after Phase 5 — need to relax classification threshold
- Only 1,312 niches with centroid embedding — embedder coverage gap
- Phase 5.10 gap detection has O(N·niches) bottleneck on >50K candidate apps

### Active workstreams

- v6 spec: anchor-classify model
- Long-tail aggregation strategy
- Quality coherence pipeline (merge/rename audit)

Related: [[competitor-teardown-tool]], [[parallel-batch-llm]], [[db-operations-backup-locking]], [[bulk-sqlite-ram-disk]].

_Confidence: high · Source: my-project_
