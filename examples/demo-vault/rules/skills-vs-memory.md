---
type: rule
title: "Skills = global source of truth, not memory"
aliases: ["skills-vs-memory"]
tags: ["rule"]
---

# Skills = global source of truth, not memory

> Knowledge about a tool (rules, gotchas, usage patterns) goes in SKILL.md, not in project memory. Skill = global, memory = project-scoped. Memory = pointers and project-specific context.

**Why:** Memory files are project-scoped. Feedback memory in one project is not visible from another project. But SKILL.md is loaded globally by description match from any project.

## Details

## Rule: SKILL.md = global source of truth

**Rule:** If knowledge about a tool is relevant from any project — write it directly in SKILL.md, not in memory.

**How to apply:**

- Behavioral rules (when to use, when NOT to use) → at the top of SKILL.md
- Gotchas, limits, tips → in SKILL.md
- Project memory → only pointers to skill ("research done, see SKILL.md") and project-specific context
- If you wrote feedback memory about a tool → duplicate the rule in SKILL.md
- **Re-verify volatile skills every 2 weeks** — check changelog, rate limits. On error — immediately.

## Global rule: quotas = main source of false bugs

**Rule:** Empty output, NOT FOUND, Error, exit 1 from ANY sub-agent → **first hypothesis = quota**, not a code bug.

**How to apply:**

- Got an error → `2>&1` for diagnostics, NOT `2>/dev/null`
- NOT FOUND → switch account, don't fix the code
- 2 fails in a row → STOP, fallback to a different agent/account
- Do NOT spam retries to the same account — makes the quota worse
- Every skill must have a fallback chain in the ⚠️ RULES block

Related: [[silent-failures-are-not-ok]], [[validate-agent-findings]].

_Confidence: high · Source: my-project_
