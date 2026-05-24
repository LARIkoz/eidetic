# Eidetic — Agent Instructions

You are working with Eidetic, a long-term memory system for Claude Code.

## Project Map First

Before changing product direction, roadmap, docs, or runtime behavior, read
`PROJECT_MAP.md`. It links the repo to the canonical Kurdyuk-run brief and
charter:

- `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/brief.md`
- `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/charter.md`

Do not treat this repo's README/TODO as a replacement for those governance
files. The repo may summarize them, but must not contradict them.

## Quick Setup (run this)

```bash
bash install.sh
```

This installs everything: FTS5 index, hooks, recall skill, session counter. Zero pip installs needed.

## What happens after install

- **Every session start:** Your context is enriched with 59+ behavioral rules + project context + recent cross-project knowledge. This is automatic — you don't need to do anything.
- **Every session end:** Your decisions, rules, and failures are extracted from the transcript and compounded into existing memory files.
- **Mid-session:** Use `/memory-recall <query>` to search all memories in an isolated context.

## If you're migrating from built-in auto-memory

The system runs in parallel with MEMORY.md auto-load (Phase A). Nothing breaks. After 5 stable sessions, the user can disable auto-memory in settings.json.

**Do NOT:**

- Delete or modify existing MEMORY.md files
- Disable auto-memory without user confirmation
- Manually create memories without checking FTS5 first (use compound.py)

## Architecture (for modifications)

```
~/.claude/memory-system/bin/   — all scripts (index, search, assemble, compound, lint, cleanup)
~/.claude/memory-system/db/    — SQLite databases (index.db, sessions.db)
~/.claude/hooks/               — SessionStart + Stop hooks
~/.claude/skills/memory-recall/ — context:fork recall skill
~/.claude/rules/memory-context.md — auto-generated, auto-loaded by Claude
```

## Key invariants

1. **Files are truth.** index.db is derived. Delete it → reindex → identical results.
2. **Feedback rules always visible.** If a type=feedback memory exists, it MUST appear in memory-context.md. Truncation of feedback rules is a P3 violation.
3. **Compound before create.** Before writing a new memory, search FTS5 for existing memory on same topic. Update existing → don't duplicate.
4. **Agent-extracted = 0.5x weight.** Memories you create are discounted vs. user-created ones. This prevents hallucination reinforcement loops.
5. **Atomic writes.** Always use tempfile + os.replace(). Never write directly to a file that other hooks might read.
6. **Lock before write.** Hooks must use the current runtime lock protocol before file/DB writes. SessionStart currently uses `~/.claude/memory-system/.memory.pid`; update `PROJECT_MAP.md` and hook docs if this changes.
7. **Weak recall is not memory.** Programmatic consumers must use `--json-object` or MCP `memory_search` and honor `no_confident_results=true`.
8. **Lifecycle matters.** `card_kind`, `status`, `supersedes`, and `superseded_by` affect recall. Current cards should outrank resolved/superseded/archived cards.

## Commands you can run

```bash
~/.claude/memory-system/bin/search.sh "query" --limit 5    # Search
~/.claude/memory-system/bin/search.sh "query" --json-object # Agent-safe structured search
~/.claude/memory-system/bin/index.sh --incremental          # Reindex
~/.claude/memory-system/bin/health.sh                       # Health check
~/.claude/memory-system/bin/lint.sh                         # Find issues
~/.claude/memory-system/bin/cleanup.sh --report             # Stale candidates
```

## Troubleshooting

- **Hook not firing:** Check `~/.claude/settings.json` — hooks must be registered under SessionStart and Stop.
- **Search returns nothing:** Run `index.sh --full` to rebuild.
- **memory-context.md empty:** Check if `~/.claude/rules/` directory exists. Check `health.sh` output.
- **Rollback everything:** `bash ~/.claude/memory-system/bin/rollback.sh` — 5 seconds, restores pre-install state.
