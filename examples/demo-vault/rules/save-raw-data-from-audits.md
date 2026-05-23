---
type: rule
title: Always save all raw data from audit/smoke/quality checks
aliases: ["save-raw-data-from-audits"]
tags: ["rule"]
---

# Always save all raw data from audit/smoke/quality checks

> User requires preserving complete raw outputs from quality audits, smoke tests, and any validation — not just aggregated summaries.

**Why:** Raw data enables re-analysis, cross-checking, debugging false positives/negatives. Summaries lose signal. Once raw is gone, re-running costs time and API credits.

**How to apply:** Every audit/smoke/quality check must write per-item raw output (full LLM response, scores, reasoning) to a file before aggregating. Save to run folder or `/tmp/` rescue. Include in handoff `tmp_rescue` if session ends.

## Details

Always save all raw data from quality audits, smoke tests, and validation runs — not just summaries or aggregated scores.

Related: [[smoke-test-incrementally]], [[dual-smoke-tests]].

_Confidence: high · Source: my-project_
