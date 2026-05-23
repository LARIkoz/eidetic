---
type: rule
title: Model freshness — verify before use
aliases: ["model-freshness-check"]
tags: ["rule"]
---

# Model freshness — verify before use

> SYSTEMIC BUG — models go stale in skills/configs. Mandatory check before use + update protocol.

## Details

## Problem

GPT, Gemini, OpenRouter models go stale in hardcoded configs (skills, CLAUDE.md, scripts, gemini-settings.json). The agent continues using old models without checking → quality loss, `ModelNotFoundError`, wasted money.

Real example: `gemini-3-pro-preview` and `gemini-3.1-pro-preview` gave `ModelNotFoundError` in headless `-p` mode, even though Gemini CLI interactively already resolved `pro` → `gemini-3-pro-preview`. Working models for sub-agent: `gemini-2.5-pro`, `gemini-2.5-flash`.

## Rule

**VERIFIED date in skill > 2 weeks → freshness check is MANDATORY before use.**

## Check protocol

### 1. Gemini models

```bash
echo "Y" | GOOGLE_GENAI_USE_GCA=true gemini -p "Reply: model_id=[your model ID]" -m <model-name> -o text 2>&1 | head -5
```

- If `ModelNotFoundError` → model not available in headless, use previous working one
- If OK → update SKILL.md, gemini-settings.json

### 2. Codex models

```bash
codex exec 'Reply with your model ID' -m <model-name> -s read-only 2>&1 | head -5
```

### 3. OpenRouter models

```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.loads(sys.stdin.read())['data'] if 'deepseek' in m['id'].lower()]"
```

## Where hardcoded models are stored (all must be updated)

1. `~/.claude/skills/gemini-cli/SKILL.md` — model table
2. `~/.claude/skills/codex-cli/SKILL.md` — Codex models
3. Local `gemini-settings.json` — customAliases
4. `~/.gemini/settings.json` — local copy
5. README and TROUBLESHOOTING command examples
6. Pipeline scripts — hardcoded model IDs in Python
7. CLAUDE.md — routing tables
8. Any consilium/openrouter helper — model ID lists

## When to check

- **Every time:** VERIFIED date > 14 days
- **On error:** `ModelNotFoundError`, 400, unexpected output → freshness check first
- **On new project:** before first sub-agent use
- **Monthly:** scheduled check of all skills with `stability: volatile`

## Why this is systemic

Models are updated every 1-4 weeks. Gemini 3.x already exists in interactive mode, but not yet available in headless. Codex models rotate without announcement. OpenRouter adds/removes models constantly. Without checking — silent degradation.

## How to apply

- Before launching sub-agent → check VERIFIED date in SKILL.md
- If > 14 days → run quick check (commands above)
- When updating → update ALL locations from the list, not just one
- After updating → update VERIFIED date in SKILL.md

Related: [[silent-failures-are-not-ok]], [[skills-vs-memory]].

_Confidence: high · Source: my-project_
