# Awesome Lists — Submission Targets

## Top targets (by stars)

| Repo                                    | Stars | Category          | Entry to add                  |
| --------------------------------------- | ----- | ----------------- | ----------------------------- |
| ComposioHQ/awesome-claude-skills        | 61K   | Claude Skills     | Memory / Knowledge Management |
| hesreallyhim/awesome-claude-code        | 44K   | Claude Code tools | Hooks / Memory                |
| VoltAgent/awesome-claude-code-subagents | 20K   | Subagents         | Memory recall skill           |
| travisvn/awesome-claude-skills          | 13K   | Claude Skills     | Memory                        |
| rohitg00/awesome-claude-code-toolkit    | 1.8K  | Toolkit           | Hooks + Skills                |

## Entry text (adapt per list format)

### One-liner

- [Eidetic](https://github.com/LARIkoz/eidetic) — Long-term memory for Claude Code. FTS5 search + auto context injection + signal extraction + compounding. Zero external deps.

### With description

- [Eidetic](https://github.com/LARIkoz/eidetic) — Long-term memory system that replaces the 200-line MEMORY.md limit with FTS5 full-text search (50ms, 400+ files), automatic context injection via `~/.claude/rules/` (59 behavioral rules always visible), session signal extraction with compounding (updates existing memories instead of duplicating), evidence tiers with self-referential discount (0.5x for agent-created). Zero external deps — bash + python3 + sqlite3. Rollback = 1 command.

## PR commands

```bash
# Fork + clone + add entry + PR for each:

# 1. ComposioHQ/awesome-claude-skills (61K)
gh repo fork ComposioHQ/awesome-claude-skills --clone
# Add entry to appropriate section
# gh pr create --title "Add Eidetic — long-term memory for Claude Code"

# 2. hesreallyhim/awesome-claude-code (44K)
gh repo fork hesreallyhim/awesome-claude-code --clone
# Add entry under Hooks or Memory section
# gh pr create --title "Add Eidetic — FTS5 memory system with hooks + skills"

# 3. VoltAgent/awesome-claude-code-subagents (20K)
gh repo fork VoltAgent/awesome-claude-code-subagents --clone
# Add entry for memory-recall context:fork subagent
# gh pr create --title "Add Eidetic memory-recall subagent (context:fork)"
```

## Reddit targets

| Subreddit         | Post type   | Relevance                                      |
| ----------------- | ----------- | ---------------------------------------------- |
| r/ClaudeAI        | Show & Tell | Primary — Claude Code users                    |
| r/ChatGPTCoding   | Tool share  | AI coding tools community                      |
| r/LocalLLaMA      | Tool share  | Technical audience, appreciates zero-deps      |
| r/MachineLearning | Project     | Academic audience, Karpathy/Zettelkasten angle |

## Also post to

- Claude Code Discord (if exists)
- Anthropic community forums
- dev.to article (longer format, tutorial-style)
- LinkedIn post (professional audience)
