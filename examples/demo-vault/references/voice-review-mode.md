---
type: reference
title: Voice — review mode
aliases: ["voice-review-mode"]
tags: ["reference"]
---

# Voice — review mode

_Reference:_ REVIEW mode — 5 top voices (code review, bug-finding), pipeline review scope guard.

## Details

# REVIEW Mode — 5 Top Voices (Code Review, Bug-Finding)

**Purpose:** Deep, xthinking-level review for ship-blocking decisions.
**Effort level:** `xhigh` (extended thinking enabled)
**Use case:** Pre-release code review, architecture audit, catching edge cases

### Voice array definition

```bash
REVIEW_VOICES=(
  "v-sonnet46|claude|claude-sonnet-4-6|Anthropic|xhigh"
  "v-opus46|claude|claude-opus-4-6|Anthropic|xhigh"
  "v-opus47|claude|claude-opus-4-7|Anthropic|xhigh"
  "v-codex53|codex2|gpt-5.3-codex|OpenAI|xhigh"
  "v-codex55|codex2|gpt-5.5|OpenAI|xhigh"
)
```

### Per-voice details

| Label          | Provider  | Model             | Family    | Invoke type      | Effort  | Context | Notes                              |
| -------------- | --------- | ----------------- | --------- | ---------------- | ------- | ------- | ---------------------------------- |
| **v-sonnet46** | Anthropic | claude-sonnet-4-6 | Anthropic | `claude`         | `xhigh` | 200K    | Fast xthinking, strong for bugs    |
| **v-opus46**   | Anthropic | claude-opus-4-6   | Anthropic | `claude`         | `xhigh` | 200K    | Deep reasoning, slower             |
| **v-opus47**   | Anthropic | claude-opus-4-7   | Anthropic | `claude`         | `xhigh` | 200K    | Latest, best reasoning depth       |
| **v-codex53**  | OpenAI    | gpt-5.3-codex     | OpenAI    | `codex2` (acc#2) | `xhigh` | ???     | Code-level verification, xthinking |
| **v-codex55**  | OpenAI    | gpt-5.5           | OpenAI    | `codex2` (acc#2) | `xhigh` | ???     | Latest Codex, xthinking + detailed |

**Phase 0 timing:** Same as consilium — 3s stagger, ~5-15 min for all 5 voices sequentially.

**Tone contract:** Review mode injects a scope guard before firing:

```markdown
## Pipeline Review Scope Guard

Review the target for ALL material bugs, contradictions, regressions, security issues,
data-loss risks, race conditions, edge cases, and architecture blind spots.

If the user supplied focal points, treat them as optional hints, not as the boundary
of the review. Do not let focal points suppress unrelated bug search.

At least 30% of your analysis must scan outside the supplied focal points.
```

Related: [[voice-system-architecture]], [[voice-consilium-mode]], [[voice-synthesizer]], [[voice-redteam]].

_Confidence: high · Source: my-project_
