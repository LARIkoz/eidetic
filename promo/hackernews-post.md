# Hacker News — Show HN

**Title:** Show HN: Eidetic – Second Brain for AI coding agents (FTS5+vector, drift detection, Obsidian vault export, zero core deps)

**Body:**

Claude Code's built-in memory (MEMORY.md) has a 200-line limit. After 60 sessions I had 500+ files and 124 behavioral rules — only 57 fit. The agent was violating rules it couldn't see.

Eidetic fixes this with three hooks and no configuration:

1. SessionStart (~350ms): indexes 500+ files into SQLite FTS5, assembles 124 rules + project context into ~/.claude/rules/ (auto-loaded, no size cap). Smart compression via keyword clustering — 2.17x more rules in the same token budget.

2. Stop (async): extracts decisions/rules/failures via Haiku (~$0.002/session). Searches existing memories before creating new files — compounds knowledge instead of duplicating.

3. Search: hybrid FTS5 + vector (optional fastembed, 33MB ONNX). Tiered query strategy (phrase -> AND-prefix -> OR-prefix) before vector fallback. 18/20 on our recall benchmark. Tree-sitter code-aware search optional.

v2.5 adds drift detection — nobody else does this. Checks wikilinks and memory age every 24h. Stale memories get demoted in ranking. Type-based thresholds: feedback rules never expire, project memories decay at 30 days. All in a separate SQLite DB (source markdown files are never modified by the system).

Design influenced by Zettelkasten (atomic notes, no categories), Tiago Forte's Second Brain (progressive quality), Karpathy's wiki concept (LLMs do the maintenance), and claude-soul (evidence tiers, self-referential discount).

Zero core dependencies — bash + python3 + sqlite3. Optional pip for vector/code search. Rollback = 1 command. Auto-updates via background git ls-remote check.

Performance: reindex 0.6s (522 files), search 50ms, hybrid 200ms, index 9.5MB.

Compared with claude-mem (76K stars, needs ChromaDB), engram (3.7K, Node.js), memsearch (1.8K, Milvus). None combines search + injection + extraction + quality + compounding + drift detection.

v4.0 adds Obsidian vault export — `eidetic export-vault ~/my-vault/` projects your AI memory into a real, browsable knowledge base. A quality gate filters 500+ raw files down to ~120 validated knowledge notes (agent junk and half-written drafts are dropped). Notes are rendered with templates — Rule Cards, Status Cards, Quick References — instead of dumping raw agent output. Auto-generated MOC, verified wikilinks, color-coded graph by note type, delta mode for incremental re-export. Demo vault shipped in `examples/demo-vault/` (55 notes, 126 wikilinks). As far as I can tell this is the first CLI that projects AI agent memory into Obsidian; optional Haiku polish is queued for v4.1, still zero deps for the core path.

https://github.com/LARIkoz/eidetic
