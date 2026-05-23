---
type: reference
title: Voice — pipeline timing and gotchas
aliases: ["voice-pipeline-timing"]
tags: ["reference"]
---

# Voice — pipeline timing and gotchas

_Reference:_ Example /consreview invocations, measured pipeline timing, known issues.

## Details

# Example: Running /consreview with Tiers

### Standard (recommended)

```bash
bash bin/synthesize_consilium.sh /tmp/review-*/ --mode review --tier standard
```

**Result:** 5 voices (xhigh) → Opus synth → Codex audit → mechanical check → Gemini redteam. ~20 min.

### Light (quick unblock)

```bash
bash bin/synthesize_consilium.sh /tmp/review-*/ --mode review --tier light
```

**Result:** 5 voices (xhigh) → Opus synth. NO audit, NO redteam. ~8 min.

### Standard without redteam

```bash
bash bin/synthesize_consilium.sh /tmp/review-*/ --mode review --tier standard --no-redteam
```

**Result:** 5 voices → Opus synth → Codex audit → mechanical. ~13 min, no Gemini.

### Strict mode (fail fast if audit finds issues)

```bash
bash bin/synthesize_consilium.sh /tmp/review-*/ --mode review --tier standard --strict
```

**Result:** Same, but exit code 2 if `MECHANICAL_AUDIT.md` shows FAIL / CRASH / ISSUES.

# Voice Pipeline Timing (Measured)

Measured timings on a 1100-line Python script review:

- Voices: ~6 min
- Synth: ~3-4 min
- Audit: ~2-3 min
- Mechanical: instant
- Redteam: ~5 min
- **Total without redteam: ~11-13 min**
- **Total with redteam: ~18 min**

# Key Gotchas & Known Issues

### G1: Premature completion check

`nohup ... &` + Bash `run_in_background: true` = task-notification arrives before real finish.
**Fix:** Always use `wait_for_completion.sh` to poll `.pipeline_complete` sentinel.

### G2: Anthropic rate limit

3 parallel Anthropic voices (sonnet + opus46 + opus47) under one auth → throttle.
**Fix:** Ensure ≥ 3 voices for synth; if 2+ Anthropic fail, ok to proceed with N-1.

### G3: Mechanical audit crash

Rare: `MECHANICAL_AUDIT.md` truncated mid-table.
**Fix:** If `## Overall mechanical verdict` missing, treat audit as incomplete; fall back to `AUDIT_VERDICT.md`.

### G4: Redteam silently skipped

Phase 3.5 doesn't fire even with `--mode review --tier standard`.
**Causes:** Codex acc#2 timeout, Gemini auth fail, template not built.
**Workaround:** Don't rely on redteam; verify manually via `codex exec` if critical.

### B31: Preflight / runtime voice mismatch

`preflight-consilium.sh` reports 7 alive voices while runtime only fires 3 (Anthropic paused).
**Fix:** After every run, verify actual voice count from footer or `ls v-*.md`.

### B32: Consilium verdict accounting

For decision consilium (not review), voices may not provide `SHIP|FIX|REWORK` enums.
**Fix:** If `SYNTHESIS.md` shows all voices `INVALID`, manually re-synth from `v-*.md` files.

Related: [[voice-system-architecture]], [[voice-tier-system]], [[voice-summary-table]].

_Confidence: high · Source: my-project_
