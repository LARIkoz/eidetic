---
type: rule
title: "Decide from context, don't multi-ask"
aliases: ["decide-from-context"]
tags: ["rule"]
---

# Decide from context, don't multi-ask

> When skills, keys.env, or memory contain the answer — find and use it. Asking the user for decisions already documented = friction.

**Why:** When launching an overnight multi-model deep-review, the agent asked "which Codex/Gemini account to use" instead of reading the `codex-cli` and `gemini-cli` skills which document the invocation patterns (`CODEX_HOME=~/.codex2`, `GEMINI_CLI_HOME=~/.gemini2`). User correction: "why can't you decide yourself". Same turn, also asked "full brief or trim?" when user had previously said "take everything".

## Details

When user says "do X" and X needs config (which account, which model, which scope), **search skills + keys.env + memory FIRST** and decide based on what you find. Only ask if genuinely ambiguous after searching.

**Anti-pattern:** Stacking 2 questions before launching ("acc switch?" + "brief scope?") — multi-question prompts are the failure mode: "1 recommendation + data, not menu of 5".

**How to apply:**

1. Identify what you need to decide (account, scope, model, etc.)
2. Search: `~/.claude/skills/<tool-name>/SKILL.md`, `keys.env`, project memory
3. If found → decide + announce ("using acc#2 via CODEX_HOME=~/.codex2 per skill")
4. If conflicting/missing → ask ONE focused question with your best guess: "I'm using X based on Y; correct?"
5. Never preface launch with 2+ open questions

Related: [[defend-correct-answer]] (don't reopen a decision just because user reasks).

_Confidence: high · Source: my-project_
