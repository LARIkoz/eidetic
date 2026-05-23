# X/Twitter post

**Post 1 (main):**

Built a Second Brain for Claude Code.

Problem: MEMORY.md has a 200-line limit. 67 of my 124 rules were invisible.

Solution:

- FTS5 + vector hybrid search (18/20 recall)
- 124/124 rules always visible (smart compression)
- Drift detection — stale memories auto-demoted
- Zero core deps

One command install. MIT.

github.com/LARIkoz/eidetic

**Post 2 (thread — what's unique):**

What no other Claude Code memory tool does:

1. Compounding — updates existing knowledge, doesn't just append
2. Drift detection — checks if referenced files still exist every 24h
3. Self-referential discount — agent-created memories = 0.5x weight
4. Auto-updates — background version check at session start

Compared with claude-mem (76K stars), engram (3.7K), memsearch (1.8K) — none does all 4.

**Post 3 (thread — design lesson):**

Key lesson: never modify markdown files from hooks.

Race conditions with concurrent writers. YAML format corruption. Self-reference loops (drift check flags its own results as stale).

Keep source of truth in markdown. Keep derived state in SQLite. Keep them separate.

This single rule prevented 3 blockers we found in code review.

**Post 4 (thread — v4.0 vault export):**

New in v4.0: project your AI memory into Obsidian.

`eidetic export-vault ~/my-vault/`

- 500+ raw files -> ~120 validated knowledge notes (quality gate filters agent junk)
- Templates: Rule Cards, Status Cards, Quick References — not a raw dump
- Auto-MOC, verified wikilinks, color-coded graph
- Delta mode for incremental updates
- Demo vault in repo: 55 notes, 126 wikilinks

First CLI to do this. Still zero deps.

**Tags:** #ClaudeCode #AIMemory #LLM #Anthropic #DevTools #SecondBrain #Obsidian
**Mention:** @AnthropicAI @obsdmd
