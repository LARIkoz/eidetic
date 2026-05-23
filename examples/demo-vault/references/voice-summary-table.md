---
type: reference
title: Voice — summary
aliases: ["voice-summary-table"]
tags: ["reference"]
---

# Voice — summary

_Reference:_ Summary — which voices fire where.

## Details

# Summary: Which Voices Fire Where?

```
/consilium mode
├─ 7 voices: opus47, opus46, sonnet46, codex55, qwen, deepseek-v4, mistral, grok
├─ Effort: high (not xhigh)
├─ Synth: Opus 4.7 xthinking
├─ Audit: Codex 5.4 xth+detailed (tier ≥ standard only)
├─ Redteam: NONE (redteam-consilium template not built yet)
└─ Total: ~10-15 min

/consreview mode (code review, ship-blocking)
├─ 5 voices: opus47, opus46, sonnet46, codex53, codex55
├─ Effort: xhigh (extended thinking, detailed reasoning)
├─ Synth: Opus 4.7 xthinking
├─ Audit: Codex 5.4 xth+detailed (tier ≥ standard only)
├─ Redteam: Gemini G3 Pro adversarial (tier ≥ standard only, skipped on --no-redteam)
└─ Total: ~12-25 min (with redteam: ~18-25 min)

--tier light
├─ Audit: SKIPPED
├─ Redteam: SKIPPED
└─ Result: ~8 min for review, ~10 min for consilium

--tier standard
├─ Audit: RUN (Codex 5.4)
├─ Redteam: RUN (Gemini G3 Pro, review mode only)
└─ Result: ~12-20 min review, ~10-15 min consilium
```

Related: [[voice-system-architecture]], [[voice-consilium-mode]], [[voice-review-mode]], [[voice-tier-system]], [[voice-pipeline-timing]].

_Confidence: high · Source: my-project_
