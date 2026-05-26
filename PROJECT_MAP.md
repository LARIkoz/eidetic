# Eidetic Project Map

This maintainer-local file is the routing table for Eidetic artifacts. It
exists to prevent project state from drifting across the repo, the original
Kurdyuk run, the installed runtime, and human-facing projections. End users can
ignore this file unless they are working inside the maintainer workspace.

## Source Of Truth

| Layer | Canonical path | Role | Write policy |
| --- | --- | --- | --- |
| Product governance | `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/` | Original Kurdyuk run: brief, charter, spec, plans, final review, gate log | Product intent changes go here first |
| Runtime source | `~/Documents/cursore/eidetic/` | Public repo and local working tree for code, installer, README, CI, MCP | Code/docs changes go here, then commit and push |
| Installed runtime | `~/.claude/memory-system/` | Active local installation used by hooks and MCP | Derived from runtime source via `bin/update.sh` or `install.sh` |
| Memory corpus | `~/.claude/projects/*/memory/`, `~/.claude/agent-memory/`, `~/.claude/skills/` | Source markdown memories searched by Eidetic | Update through compounding or explicit curated memory edits |
| Derived databases | `~/.claude/memory-system/db/index.db`, `vectors.db`, `drift_state.db` | Search/vector/drift indexes | Rebuildable; never treat as product truth |
| Injected context | `~/.claude/rules/memory-context.md` | Auto-generated context loaded by Claude | Derived; regenerate, do not hand-edit |
| Human projection | `~/Documents/cursore/eidetic-vault/` | Obsidian-compatible export | Derived read-only projection; does not feed agent recall |
| Obsidian research project | `~/Documents/cursore/personal/ai-research/obsidian-claude-workflow/` | Human-facing research notes and experiments | Deferred unless explicitly working on Vault IA |

## Canonical Governance Files

Read these before changing product direction:

- Brief: `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/brief.md`
- Charter: `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/charter.md`
- Current run state: `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/state.md`
- Gate log: `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/gate-log.md`
- Current product TODO from original run: `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/todo.md`

The local repo has a shorter operational view in `CLAUDE.md` and `TODO.md`.
Those files must not contradict the canonical brief and charter.

## Current Local Repo Entrypoints

- `README.md` — public product description, install docs, changelog, roadmap.
- `CLAUDE.md` — runtime engineering invariants for agents working in this repo.
- `TODO.md` — short current-session backlog. Keep it aligned with the canonical run.
- `llms.txt` — compact public summary for LLM/index consumers.
- `output/handoff-2026-05-22-eidetic-stabilization-publish/` — latest repo stabilization handoff.

## Where To Put Changes

| Change type | Write here | Also update |
| --- | --- | --- |
| Change product purpose, scope, charter, or "agent vs human" priority | Canonical Kurdyuk run brief/charter | `PROJECT_MAP.md`, `README.md`, `TODO.md` if user-visible |
| Runtime code or CLI behavior | Runtime source repo | Tests, `README.md` changelog, MCP version if API-facing |
| Installed runtime refresh | `~/.claude/memory-system/` via `bash bin/update.sh` | `health.sh`, reindex/vector rebuild if needed |
| New memory/rule/decision learned from a session | Memory corpus | Search first; compound existing before create |
| Human Obsidian structure or vault IA | Personal Obsidian research project first | Repo only after agent recall quality remains intact |
| Handoff or cold-resume state | `output/handoff-*` or canonical run state | Include command evidence and next actions |

## Current Strategic Priority

Agent-facing memory quality is ahead of human-facing Obsidian work.

The charter says the agent is the primary consumer. Amendment A1 allows human
visibility through a read-only projection layer, but it must not affect core
recall, hooks, search, injection, or compounding. Therefore:

1. Keep Obsidian/Vault IA in maintenance mode unless explicitly requested.
2. Keep v4.2.21 as the current runtime baseline: card kind, status,
   supersession, drift diagnostics, recall regression coverage, lifecycle-aware
   age drift, and Stop-hook transcript safety must stay healthy before adding
   human-facing IA.
3. Treat v4.3 Lifecycle Signals and v5.0 Progressive Search as the completed
   agent-facing roadmap gates before distribution work. Lifecycle Phase B
   remains the next safe signal-expansion candidate and must stay
   metadata-only. v3.0 Task Planner remains planned but is not the automatic
   next gate unless explicitly routed.
4. Keep SkillOpt-style skill optimization as post-v6 research, not a current
   gate. It may propose patches to skills or agent instructions only after
   scored rollouts, train/validation/test separation, regression evidence, and
   qreview/consreview.
5. Treat weak/noisy retrieval as a product bug, not as acceptable "more context."

## Hook Write-Lock Exception

SessionStart and Stop hooks use the shared `fcntl` lock via
`bin/lock_runner.py` before runtime writes. The v4.3 Lifecycle Signals design is
the narrow exception: lifecycle event JSONL uses one bounded `O_APPEND` write per
event line with a conservative 512-byte cap, or the documented file-lock fallback
if a future larger cap is needed. This avoids dropping normal concurrent
`PostToolUse` events under the shared non-blocking lock. Do not apply this
exception to DB writes, `rules/memory-context.md`, or curated memory compounding.

## Cold Start Checklist

1. Read this file.
2. Read the canonical brief and charter from the Kurdyuk run.
3. Read `TODO.md` in this repo for the current local backlog.
4. Run `bash ~/.claude/memory-system/bin/health.sh`.
5. If changing runtime behavior, run relevant smokes and update the installed runtime.
