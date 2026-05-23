---
type: project
title: Cross-project Backlog
aliases: ["backlog"]
tags: ["project"]
---

# Cross-project Backlog

_Status:_ Single source of truth for ideas, scoping, and active workstreams at personal / framework / cross-project level. Project-tactical TODOs stay in each project's handoff.

> One backlog file for ideas and workstreams that don't belong to a single project. Per-project TODOs live in per-project handoffs.

## Details

Single source of truth for ideas and workstreams at **personal / framework / cross-project level**.
Project-tactical TODOs (bug fixes, feature tickets, scraper work) stay in each project's handoff — not here.

## Format

```
- [STATE] title (surfaced YYYY-MM-DD) — 1-line what/why [→ pointer if detailed notes]
```

## States

- **[IDEA]** — placeholder, not committed. If it resurfaces on its own, it matters.
- **[SCOPING]** — committed to scoping. Needs target / criteria / approach before active work.
- **[ACTIVE]** — committed to work. Concrete next steps exist.

## Rules

- **Capture:** 1 line is default. Separate `todo-*.md` file only when notes grow beyond ~10 lines.
- **Transitions:** IDEA → SCOPING → ACTIVE as commitment increases. Date stays the same (original surfaced).
- **Kill:** just delete the line. If it resurfaces, that's the signal.
- **Review:** on demand. No enforced decay, no session-start auto-scan.

---

## Active

- [ACTIVE] Hook translation pass — comments / output should match repo language policy
- [ACTIVE] Notify collaborators about a force-push on shared setup repo
- [ACTIVE] Roll out the workflow framework across 3 main projects
- [ACTIVE] Review cycle bug: voice validator rejects markdown verdict format
- [ACTIVE] Mechanical audit attribution heuristic produces false-positive failures
- [ACTIVE] Sub-agent sandbox denies network / write tools — research-agent dispatch broken

## Scoping

- [SCOPING] Brainstorm skill upgrade → Informed Brainstorm (research grounding + consilium sync)
- [SCOPING] Auto-handoff continuity — auto-resume after context pressure; 5 approaches identified
- [SCOPING] Framework methodology docs — concept.md + templates; create sample project
- [SCOPING] Memory migration: kurdyuk-lite slug → per-project slugs (~94 files in wrong slug)

## Ideas

- [IDEA] **Sub-agent spawn pooling via SendMessage** — Agent-tool spawns cost 86-265K cache_creation each. Pool via SendMessage reuses warm cache for same `subagent_type` on sequential related tasks.
- [IDEA] Parallel key-pool throughput — worker-per-key pattern on the rotation client. Biggest win: classify/consilium 100K items × 2s serial = 55h → 20 workers = ~3h.
- [IDEA] Gemini CLI env-leak hardening — mirror Codex `shell_environment_policy` approach for Gemini CLI.
- [IDEA] Claude sub-agent env restriction — restrict env access for `claude-batch` sub-agents via `~/.claude/settings.json` permissions model.
- [IDEA] Cron heartbeat for Claude CLI — deep research done. Verdict: adopt Anthropic plugin marketplace items (ralph-loop official + scheduler/context-guardian/c3poh-telegram/ai-guard community). Revisit on concrete pain.
- [IDEA] Hardware cooling investigation — paused. `batt` + pmset + ~$50 in monitoring hardware.
- [IDEA] Verify mobile-app engineering loop end-to-end on a real project.

Related: [[mobile-app-pipeline]], [[gap-analysis-pipeline]], [[handoff-requires-4-pass-verification]].

_Confidence: high · Source: my-project_
