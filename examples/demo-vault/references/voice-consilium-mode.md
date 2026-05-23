---
type: reference
title: Voice — consilium mode
aliases: ["voice-consilium-mode"]
tags: ["reference"]
---

# Voice — consilium mode

_Reference:_ CONSILIUM mode — 7 diverse voices (multi-house opinion gathering).

## Details

# CONSILIUM Mode — 7 Diverse Voices (Multi-House Opinion Gathering)

**Purpose:** Collect perspectives from multiple reasoning models and research backends.
**Effort level:** `high` (cheaper than review, fast iteration)
**Use case:** Decision-making, architecture vetting, "what do different experts think?"

### Voice array definition

```bash
CONSILIUM_VOICES=(
  "v-opus47|claude|claude-opus-4-7|Anthropic|high"
  "v-opus46|claude|claude-opus-4-6|Anthropic|high"
  "v-sonnet46|claude|claude-sonnet-4-6|Anthropic|high"
  "v-codex55|codex2|gpt-5.5|OpenAI|high"
  "v-qwen235b|dashscope|qwen3-235b-a22b|Alibaba|default"
  "v-deepseek-v4|deepseek|deepseek-v4-flash|DeepSeek|default"
  "v-mistral|mistral|mistral-large-latest|Mistral|default"
  "v-grok|grok|-|xAI|standard"
)
```

### Per-voice details

| Label             | Provider             | Model                 | Family    | Invoke type      | Effort     | Notes                                            |
| ----------------- | -------------------- | --------------------- | --------- | ---------------- | ---------- | ------------------------------------------------ |
| **v-opus47**      | Anthropic            | claude-opus-4-7       | Anthropic | `claude`         | `high`     | Primary reasoning voice, 200K context            |
| **v-opus46**      | Anthropic            | claude-opus-4-6       | Anthropic | `claude`         | `high`     | Fallback reasoning, slightly older               |
| **v-sonnet46**    | Anthropic            | claude-sonnet-4-6     | Anthropic | `claude`         | `high`     | Faster, still strong for architecture            |
| **v-codex55**     | OpenAI Codex         | gpt-5.5               | OpenAI    | `codex2` (acc#2) | `high`     | Code-aware reasoning, reads files in review mode |
| **v-qwen235b**    | Alibaba DashScope    | qwen3-235b-a22b       | Alibaba   | `dashscope`      | `default`  | Large reasoning model via API                    |
| **v-deepseek-v4** | DeepSeek             | deepseek-v4-flash     | DeepSeek  | `deepseek`       | `default`  | Reasoning model, R1 variant of v4                |
| **v-mistral**     | Mistral              | mistral-large-latest  | Mistral   | `mistral`        | `default`  | Structural reasoning, 32K context                |
| **v-grok**        | xAI (via OpenRouter) | grok-4.20-beta:online | xAI       | `grok`           | `standard` | Web-aware, online search via OpenRouter          |

**Phase 0 timing:** All 7 voices fire **in parallel**, staggered 3s between Anthropic ones to avoid auth race. ~60-180s wall time per voice.

**Phase 0 orchestration:** Anthropic voices fire with 3s stagger to prevent rate-limit race:

```bash
if [ "$family" = "Anthropic" ] && [ "$_prev_family" = "Anthropic" ]; then
  sleep 3
fi
fire_voice "$label" "$invoke_type" "$model" "$PROMPT_FILE" "$DIR/${label}.md" "$effort" &
```

Related: [[voice-system-architecture]], [[voice-review-mode]], [[voice-synthesizer]], [[voice-tier-system]].

_Confidence: high · Source: my-project_
