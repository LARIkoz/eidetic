---
type: reference
title: Voice — tier system
aliases: ["voice-tier-system"]
tags: ["reference"]
---

# Voice — tier system

_Reference:_ Tier system — how effort changes pipeline behavior.

## Details

# Tier System — How Effort Changes Pipeline Behavior

**Tiers control:**

- Which phases run
- Whether audit / redteam fire
- Model inference effort / reasoning depth

### Tier: `light`

**When to use:** Quick pass, minimal spend, unblock decision.
**Voices:** All 7 consilium OR all 5 review voices fire.
**Effort on voices:** `high` / `xhigh` (unchanged).

| Phase                      | Status | Details                                      |
| -------------------------- | ------ | -------------------------------------------- |
| **Phase 0 (Voices)**       | RUN    | All configured voices fire in parallel       |
| **Phase 1 (Synthesis)**    | RUN    | Opus 4.7 xthinking synthesizer               |
| **Phase 2 (Audit)**        | SKIP   | `# AUDIT SKIPPED (tier=light)` stub written  |
| **Phase 2.5 (Mechanical)** | RUN    | Deterministic URL fetch, attribution, schema |
| **Phase 3.5 (Redteam)**    | SKIP   | Gemini G3 Pro adversarial skipped            |

**Footer output:** `Audit: SKIPPED (tier=light)`

### Tier: `standard` (DEFAULT)

**When to use:** Production review, balanced cost / depth, normal path.
**Voices:** All configured voices fire.
**Effort on voices:** `high` / `xhigh` (per voice definition).

| Phase                      | Status            | Details                                                   |
| -------------------------- | ----------------- | --------------------------------------------------------- |
| **Phase 0 (Voices)**       | RUN               | All 7 consilium OR 5 review voices                        |
| **Phase 1 (Synthesis)**    | RUN               | Opus 4.7 xthinking                                        |
| **Phase 2 (Audit)**        | RUN               | Codex 5.4 xth+detailed (acc#2)                            |
| **Phase 2.5 (Mechanical)** | RUN               | URL cross-check, attribution, schema norm                 |
| **Phase 3.5 (Redteam)**    | RUN (review only) | **Gemini G3 Pro** adversarial; skipped for consilium mode |

**Redteam details:**

- Model: `gemini-3-pro-preview` (standard tier), `gemini-3.1-pro-preview` (deep tier)
- Account: acc#2 (`GEMINI_CLI_HOME=$HOME/.gemini2`) with auto-fallback to acc#1
- Timeout: 600s via perl alarm
- Output contract: `## Overall:` line + findings with `HOLDS / WEAK / REFUTED` verdicts
- Fail-soft: on quota / auth / timeout, writes stub `# REDTEAM MISSING — ...` and continues

**Footer output:** `Audit: [overall verdict]`, `Redteam: [overall verdict if triggered]`

Related: [[voice-system-architecture]], [[voice-pipeline-timing]], [[voice-redteam]], [[voice-auditor]].

_Confidence: high · Source: my-project_
