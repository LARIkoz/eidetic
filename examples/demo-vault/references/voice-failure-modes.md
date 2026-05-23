---
type: reference
title: Voice — failure modes and selection logic
aliases: ["voice-failure-modes"]
tags: ["reference"]
---

# Voice — failure modes and selection logic

_Reference:_ Other potential voices (available but not default), voice selection logic, failure-mode tiers.

## Details

# Other Potential Voices (Available but Not Default)

The system is extensible. These providers are wired into `fire_voice()` but not in the default `CONSILIUM_VOICES` array:

| Provider       | Type         | Models                                       | Notes                                                                              |
| -------------- | ------------ | -------------------------------------------- | ---------------------------------------------------------------------------------- |
| **Gemini**     | `gemini`     | gemini-3-pro-preview, gemini-3-flash-preview | Research tool + potential consilium voice                                          |
| **Grok**       | `grok`       | grok-4.20-beta:online                        | Currently only in consilium (online search via OpenRouter)                         |
| **Exa**        | `exa`        | (internal)                                   | Research backend, tier-scaled: light/standard → /search, deep → /research/v1 async |
| **Perplexity** | `perplexity` | sonar, sonar-pro, sonar-deep-research        | Research tool, tier-scaled                                                         |
| **GitHub**     | `gh`         | (git search)                                 | Research tool, tier-scaled (2 endpoints light, 6 standard, 6+ deep)                |

# Voice Selection Logic

### When to use `/consilium` (7 voices)

- **Decision-making:** "Should we go with approach A or B?"
- **Exploration:** "What do different experts think about this design?"
- **Quick vetting:** Want multiple perspectives, not exhaustive review.
- **Cost / time:** ~10-15 min for 7 voices, effort=high (cheaper than xhigh).

### When to use `/consreview` (5 voices, xhigh)

- **Code review:** Line-level bugs, architecture, edge cases.
- **Ship-blocking:** Pre-release audit, serious correctness questions.
- **Focal points:** "Review this PR before merge."
- **Cost / depth:** ~12-25 min, all voices xthinking, all 5 phases (redteam included).

### When to use `--tier light`

- **Unblock fast:** Quick decision, don't need audit / redteam.
- **Exploratory:** Idea vetting, not final approval.
- **Budget:** Minimal spend, fastest time-to-answer.

### When to use `--tier standard`

- **Production review:** Default setting, balanced cost / depth.
- **Ship-critical:** Audit + mechanical checks catch errors.
- **Security / compliance:** Redteam catches family-correlated blind spots.

# Voice Failure Modes & Auto-Recovery (Tier-Classified)

**PLAYBOOK.md** defines per-voice failures with auto-fix tier (see `~/.claude/skills/orchestration/PLAYBOOK.md`):

### Tier 1 (silent auto-fix)

- Anthropic auth expired → retry once
- Codex model ID wrong → swap model
- Key rotation on auth / credit failure
- Schema enum normalization

### Tier 2 (notify + wait + retry)

- Anthropic rate-limit hit → notify, wait, re-fire same voice
- Stale lock files → remove if PID dead & mtime > 5min
- Timeout edge cases → exponential backoff

### Tier 3 (block + ask user)

- Codex both accounts down → block, require `codex login --device-auth`
- OpenRouter 402 (zero balance) → block, ask user to add credits
- Browser OAuth timeout → block, ask user to re-auth

Related: [[voice-system-architecture]], [[voice-consilium-mode]], [[voice-review-mode]], [[voice-tier-system]].

_Confidence: high · Source: my-project_
