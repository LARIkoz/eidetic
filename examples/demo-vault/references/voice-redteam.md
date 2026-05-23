---
type: reference
title: Voice — redteam
aliases: ["voice-redteam"]
tags: ["reference"]
---

# Voice — redteam

_Reference:_ Redteam (Phase 3.5) — Gemini Adversarial.

## Details

# Redteam (Phase 3.5) — Gemini Adversarial

**Only runs:** `--mode review --tier standard` (or `critical` / `deep`) && `--no-redteam` not set.

**Purpose:** Catch family-correlated bias in voices (5 voices = 3 Anthropic + 2 OpenAI ≈ echo chamber).

**Execution:**

```bash
echo "Y" | GEMINI_CLI_HOME="$REDTEAM_GEMINI_HOME" GOOGLE_GENAI_USE_GCA=true \
  perl -e 'alarm 600; exec @ARGV' \
  gemini \
    -p "Adversarial code-review redteam task. Read the bundle from stdin and respond ONLY with the markdown specified by the template; no preamble." \
    -m "$REDTEAM_GEMINI_MODEL" \
    --yolo \
    -o text \
    < "$REDTEAM_BUNDLE"
```

**Account routing:**

```bash
REDTEAM_GEMINI_HOME="$HOME/.gemini2"  # acc#2 (primary, Google AI Pro)
[ "${GEMINI_ACCOUNT:-primary}" = "alternate" ] && REDTEAM_GEMINI_HOME="$HOME/.gemini"  # acc#1 fallback
```

**Model selection by tier:**

- `standard` → `gemini-3-pro-preview`
- `critical` → `gemini-3-pro-preview`
- `deep` → `gemini-3.1-pro-preview`

**Timeout:** 600s (perl alarm, no native timeout in gemini CLI).

**Fail-soft:** On timeout / quota / auth:

- Writes stub: `# REDTEAM MISSING — Gemini [reason] ([bytes]b)`
- Pipeline continues (does not block)

**Output contract:** `REDTEAM_VERDICT.md` with:

- `## Overall:` line (identifies convergences vs red-flagged inventions)
- Per-finding: `HOLDS` / `WEAK` / `REFUTED` verdict
- `## Missed-claims sweep` — issues no voice raised (high-signal output, always review)

Related: [[voice-system-architecture]], [[voice-auditor]], [[voice-synthesizer]], [[consilium-redteam-mandatory]], [[consilium-synth-hallucinations]].

_Confidence: high · Source: my-project_
