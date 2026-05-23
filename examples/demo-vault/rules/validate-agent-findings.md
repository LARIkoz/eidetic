---
type: rule
title: Validate agent diagnostic findings before implementing
aliases: ["validate-agent-findings"]
tags: ["rule"]
---

# Validate agent diagnostic findings before implementing

> Agent-found bugs must be verified with DB queries — 3/7 findings were false positives (by design features).

**Why:** Agents see code in isolation and do not know design decisions. They find "anomalies" that are actually intentional trade-offs.

**How to apply:** After receiving agent findings — verify each with a DB query (real impact), read the code (by design or bug), only then create a task list.

## Details

When running diagnostic agents to analyze code or data — ALWAYS validate findings before implementing.

In one session: 10 agents found 7 "bugs", of which 3 turned out to be by design:

- "1 representative per group excludes 873 keywords" = feature (saves API credits)
- "market_exists fires 98%" = normal for keywords with vol≥10
- "no cleanup_status filter in Phase 4a" = did not let through a single garbage keyword in practice

Related: [[silent-failures-are-not-ok]], [[smoke-test-incrementally]].

_Confidence: high · Source: my-project_
