---
type: reference
title: Voice — synthesizer
aliases: ["voice-synthesizer"]
tags: ["reference"]
---

# Voice — synthesizer

_Reference:_ Synthesizer (Phase 1).

## Details

# Synthesizer (Phase 1)

**Default synthesizer (both modes):**

```bash
SYNTH_LABEL="v-synth-opus47"
SYNTH_INVOKE="claude"
SYNTH_MODEL="claude-opus-4-7"
SYNTH_EFFORT="xhigh"
SYNTH_DESC="Opus 4.7 xthinking"
```

**Fallback:** If Anthropic is provider-paused, switches to:

```bash
SYNTH_LABEL="v-synth-codex55"
SYNTH_INVOKE="codex2"
SYNTH_MODEL="gpt-5.5"
SYNTH_EFFORT="xhigh"
SYNTH_DESC="Codex 5.5 xhigh (Anthropic paused)"
```

**Task:** Synthesize voice outputs into a unified verdict (SHIP / SHIP-WITH-EDITS / FIX / REWORK).

**Input bundle:**

- Original prompt
- All voice outputs
- (In redteam phase, also includes SYNTHESIS so redteam can cross-check convergences)

Related: [[voice-system-architecture]], [[voice-auditor]], [[voice-redteam]], [[consilium-synth-hallucinations]].

_Confidence: high · Source: my-project_
