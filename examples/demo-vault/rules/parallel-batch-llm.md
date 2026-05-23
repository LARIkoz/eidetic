---
type: rule
title: Parallel batch LLM = 100x speedup over sequential
aliases: ["parallel-batch-llm"]
tags: ["rule"]
---

# Parallel batch LLM = 100x speedup over sequential

> 30 items/batch, 6 parallel workers = 415 items in 15 sec. Sequential = hours. Use for classify, tagging, any bulk LLM task.

**Why:** LLM prompt can handle 30 item descriptions at once. JSON array response. 6 concurrent API calls.

**How to apply:** any bulk LLM task (classify, tagging, audit) → batch + parallel.

## Details

Sequential LLM calls (1 item → 1 API call → wait → next) = hours for 6K+ items.
Parallel batch (30 items per prompt, 6 ThreadPoolExecutor workers) = 15 seconds for 465 items.

**Guard:** batch > 50 items → JSON truncation risk. Keep ≤ 40 per batch.

Related: [[db-operations-backup-locking]] (for write-side throughput).

_Confidence: high · Source: my-project_
