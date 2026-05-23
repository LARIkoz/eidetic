---
type: reference
title: Model benchmarks reference
aliases: ["model-benchmarks-reference"]
tags: ["reference"]
---

# Model benchmarks reference

_Reference:_ `MODEL_BENCHMARKS.md` — reference file for model selection. Update weekly. MUST read BEFORE choosing any model.

## Details

**File:** `~/.claude/skills/openrouter-api/MODEL_BENCHMARKS.md`

**Contents:**

- MEGA-TABLE: 104 models across 4 tiers (Frontier / Mid / Budget / Free + 32 FREE sub-tiers)
- KING across 33+ categories: Quality #1/#2, Budget #1/#2, Free — including Web Scraping and Reverse Engineering
- FREE Task Routing: 17 use cases mapped to best free models
- Embedding Use-Case Routing: 15 use cases + decision tree
- Pipeline routing: consilium stacks, business logic, coding phase
- Quick Decision Matrix: "I need X → use Y"
- 43 benchmarks (active / planned / dead) + per-score freshness dates
- **Backed by JSON:** a public benchmarks repo (v1.0 PASS, source of truth)

**Why:** Choosing a model from memory = stale data, wrong prices, missed alternatives. This file = single source of truth.

**How to apply:**

- BEFORE choosing a model → read `MODEL_BENCHMARKS.md`. No exceptions.
- Rule is documented in `~/.claude/CLAUDE.md` → "Model Selection — ABSOLUTE RULE"
- Rule is duplicated in framework `CLAUDE.md` → Model routing section
- Update weekly: `Exa search benchmarks` + `curl OpenRouter /api/v1/models` + verified date
- New model → add immediately

**Related skills:** `openrouter-api` (file host), `grok-xai`, `minimax-ai`, `z-ai-glm`, `gemini-api`, `codex-cli`, `qwen-code-cli`.

Related: [[ai-model-benchmarks]], [[model-freshness-check]], [[claude-code-context-optimization]].

_Confidence: high · Source: my-project_
