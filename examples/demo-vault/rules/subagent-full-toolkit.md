---
type: rule
title: Subagent must use full toolkit
aliases: ["subagent-full-toolkit"]
tags: ["rule"]
---

# Subagent must use full toolkit

> When dispatching researcher subagent, explicitly list all tools to use — Exa API, GitHub API, web search. Don't assume it will figure it out.

**Why:** A researcher agent for one skill only read local files (`~/.claude/skills/`) and wrote from memory. It had access to Exa (via curl) and GitHub API (via gh CLI) but wasn't told to use them. Result: skill was good but missed latest techniques that web research would have found.

**How to apply:** Every researcher subagent prompt must include all available tools explicitly.

## Details

When dispatching a researcher subagent, explicitly tell it to use all available tools — not just local files.

- "Use Exa API (curl to api.exa.ai with EXA_KEY from keys.env) for web research"
- "Use GitHub API (gh search code, gh api) for code examples"
- "Read local skills in ~/.claude/skills/ for existing knowledge"
- "Combine all sources into output"

Don't assume the subagent will discover available tools on its own. It follows instructions literally.

Related: [[deep-research-all-tools-required]].

_Confidence: high · Source: my-project_
