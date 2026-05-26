# Eidetic

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.0.1-blue.svg)](CHANGELOG.md)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hooks%20%2B%20skills%20%2B%20rules-purple.svg)](#how-it-works)
[![MCP](https://img.shields.io/badge/MCP-Cursor%20%7C%20Windsurf%20%7C%20Cline-orange.svg)](#mcp-server)

**Long-term memory for Claude Code that knows when memories go bad.**

```
Session 1:  "Never mock the database in tests"
Session 2:  *mocks the database*
Session 50: *still mocking the database*
```

That's the **Day 1 problem** — Claude forgets everything between sessions. Every memory tool solves this.

But there's a harder problem nobody talks about:

```
Session 35: "Use validate_key() for all API checks"
            *validate_key() works great, gets saved to memory*

Session 50: *validate_key() was renamed to check_auth() two weeks ago*
            *Claude confidently injects the old rule*
            *Agent gets WORSE, not better, from its own memory*
```

That's the **Day 60 problem** — after 500+ memory files, stale knowledge actively hurts the agent. More memory = worse performance. No existing tool detects this.

Eidetic solves both.

```bash
git clone https://github.com/LARIkoz/eidetic.git && cd eidetic && bash install.sh
```

One command. Zero external dependencies for core. Works immediately.

---

## What It Does

| Problem                                               | How Eidetic solves it                                                              |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------- |
| MEMORY.md caps at 200 lines (57 of 124 rules visible) | Smart compression: **all 124 rules** in same token budget                          |
| Keyword search only                                   | **Hybrid FTS5 + vector** search, 50+ languages                                     |
| Forgets between sessions                              | **Auto-extracts** decisions, failures, patterns at session end                     |
| Knowledge piles up as duplicate files                 | **Compounds** — updates existing memories instead of creating new ones             |
| Stale memories poison the agent                       | **Drift detection** — flags broken wikilinks, age staleness, confidence escalation |
| Agent-created memories reinforce hallucinations       | **Self-referential discount** — agent-extracted = 0.5x weight                      |
| All memories treated equally                          | **Evidence tiers** — validated > observed > hypothesis                             |
| Can't search code                                     | **Tree-sitter** parses functions/classes into searchable chunks                    |

---

## How It Works

```
                SESSION START (~350ms warm)
                        |
                Reindex (FTS5) + Code Index (tree-sitter) + Vector Embed
                        |
                Assemble Context (124 rules + project + recent)
                        |
                Write to ~/.claude/rules/ (auto-loaded, no size cap)
                        |
                Agent starts KNOWING everything


                     MID-SESSION
                        |
                /memory-recall "query"  or  MCP memory_search
                        |
                FTS5 -> vector fallback -> RRF merge
                        |
                Results with confidence scores + drift warnings


                     SESSION END (~5s, async)
                        |
                Extract signals (Haiku primary, Codex fallback)
                        |
                Compound: update existing memory OR create new
                        |
                Tag: agent-extracted = 0.5x weight
```

### Compound Ranking

Every result is ranked by:

```
score = relevance x evidence x source x freshness

evidence:   validated = 1.0    observed = 0.7    hypothesis = 0.4
source:     user-created = 1.0  agent-extracted = 0.5  system = 0.3
freshness:  < 30 days = 1.0    > 30 days = 0.5
```

A validated, recent, human-created memory always outranks an old, unverified, agent-extracted guess.

---

## Install

```bash
git clone https://github.com/LARIkoz/eidetic.git
cd eidetic
bash install.sh
```

**Requirements:** `bash`, `python3`, `sqlite3` (pre-installed on macOS/Linux).

**Optional upgrades:**

```bash
pip install fastembed                    # semantic search (+33MB model)
pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-bash  # code search
```

Core works without pip installs — degrades to FTS5-only search and skips code indexing. Rollback: `bash ~/.claude/memory-system/bin/rollback.sh`

### Updates

Background update check every 6 hours. When available:

```
Eidetic update available (a1b2c3d). Run: bash ~/.claude/memory-system/bin/update.sh
```

Updates preserve databases, rules, and hooks — only code files are replaced.

### MCP Server

Works with Cursor, Windsurf, Cline, and any MCP-compatible agent:

```json
{
  "mcpServers": {
    "eidetic": {
      "command": "python3",
      "args": ["~/.claude/memory-system/mcp_server.py"]
    }
  }
}
```

7 tools: `memory_search`, `memory_search_detail`, `memory_serendipity`, `memory_health`, `memory_reindex`, `memory_lint`, `export_vault`.

---

## Key Features

### Drift Detection (v2.5)

The feature that makes Eidetic different. Three checks, 24h throttle, zero file mutations:

| Check                     | What it catches                                        | Threshold                              |
| ------------------------- | ------------------------------------------------------ | -------------------------------------- |
| **Wikilink drift**        | `[[validate-key]]` referenced but file renamed/deleted | Immediate                              |
| **Age staleness**         | Project memory untouched for 30+ days                  | 30d project, 60d status, 90d reference |
| **Confidence escalation** | 3+ agent-extracted updates, 0 human confirmation       | 3 events                               |

Drift findings penalize ranking: broken wikilink = 0.8x, stale = 0.5x, confidence escalation = 0.3x. Auto-resolve when the problem disappears.

### Smart Token Compression (v1.3)

124 feedback rules in 5,927 tokens. Previously only 57 fit in MEMORY.md. Keyword clustering groups related rules; tiered display shows important rules in full, low-priority as name only.

### Hybrid Search (v2.0)

FTS5 for keywords (50ms). Vector search as fallback for semantic queries. Multilingual — Russian queries find English rules via cross-language embeddings. Results merged via Reciprocal Rank Fusion. If every candidate is weak, reports `No confident results` instead of surfacing noise.

Progressive search keeps broad queries compact. Use `--detail <id>` to fetch full content when a candidate looks relevant.

### Code-Aware Search (v2.2)

Tree-sitter parses `.py`, `.js`, `.ts`, `.tsx`, `.sh` — every function and class becomes searchable by name or purpose.

### Knowledge Compounding

Before creating a new memory, searches for existing ones on the same topic. Found? Updates it, adds history. Not found? Creates new file. 50 sessions = 50 refined rules, not 500 duplicate files.

### Obsidian Vault Export (v4.0)

```bash
eidetic export-vault ~/my-vault/
```

Quality gate filters 500+ files to ~120 validated notes. Template formatting, verified wikilinks, auto-MOC, graph colors. Optional `--polish` for LLM-rewritten human-readable cards.

---

## Performance

| Metric                   | Value                          |
| ------------------------ | ------------------------------ |
| Session start (warm)     | **~350ms**                     |
| Session start (cold)     | ~11s (ONNX model load)         |
| Full reindex (522 files) | 0.6s                           |
| FTS5 search              | ~50ms                          |
| Hybrid search            | ~200ms                         |
| Signal extraction        | ~$0.002/session (Haiku)        |
| Index size               | 9.5MB (FTS5) + 5.9MB (vectors) |
| External dependencies    | **zero for core**              |

---

## Comparison

### What only Eidetic does

Based on [40-repo competitive analysis](https://github.com/LARIkoz/eidetic/releases/tag/v2.2.0) (May 2026):

| Feature                       | Why it matters                                            |
| ----------------------------- | --------------------------------------------------------- |
| **Drift detection**           | Catches stale memories before they poison the agent       |
| **Compounding**               | Updates existing knowledge instead of creating duplicates |
| **Self-referential discount** | Agent guesses can't reinforce themselves into "facts"     |
| **Evidence tiers**            | Proven rules always outrank unverified guesses            |
| **Code search**               | "Where is the rate-limit handler?" actually works         |
| **Zero-dep core**             | No Docker, no npm, no pip for basic usage                 |

### When to use what

| Your situation                                          | Best choice                                                                    |
| ------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Want Claude Code to remember AND detect stale knowledge | **Eidetic**                                                                    |
| Want largest community, web UI, multi-agent             | [claude-mem](https://github.com/anthropics/claude-mem) (78K stars)             |
| Need shared memory across Cursor + Claude + Copilot     | [engram](https://github.com/Gentleman-Programming/engram)                      |
| Already using Obsidian, want simple integration         | [lucasrosati's setup](https://github.com/lucasrosati/claude-code-memory-setup) |
| Need heavy multilingual semantic search                 | [memsearch](https://github.com/zilliztech/memsearch)                           |

---

## Design Philosophy

Inspired by [Luhmann's Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten), [Tiago Forte's Second Brain](https://www.buildingasecondbrain.com/), and [Karpathy's AI wiki concept](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285).

Core principles:

1. **Files are truth.** Indexes are derived. If the DB dies, rebuild from markdown in <1s.
2. **Every memory must be findable** without knowing where it is. Cross-project search is the default.
3. **Critical rules must never become invisible.** The system guarantees all behavioral rules reach the agent.
4. **The system maintains itself** — or it dies. The AI agent does all indexing, linking, searching, compounding. Human curates when they want to, not because they have to.

---

## Safety

- **Atomic writes** — `tempfile` + `os.replace()`, crash-safe
- **Backup/restore** — auto-backup before reindex, auto-restore on failure
- **Lock serialization** — `fcntl` lock file via `bin/lock_runner.py`
- **Anti-injection** — prefix-validated signal extraction blocks transcript noise from becoming memory
- **Graceful degradation** — missing index falls back to `head -200 MEMORY.md`
- **Rollback** — one command, <5 seconds

---

## Roadmap

**Shipped:** v1.0 FTS5 + signals + compounding, v1.3 token compression (2.17x), v2.0 hybrid search (30% -> 100% recall), v2.2 code search (tree-sitter), v2.5 drift detection, v4.0-4.2 Obsidian vault export + LLM polish, v4.3 lifecycle signals, v5.0 progressive search, v5.0.1 lifecycle Phase B.

**Next:** v5.1 Distribution (pip package, docs polish).

**Planned:** v3.0 Task Planner Bridge (sync to YouGile/Linear/GitHub Issues). v6+ Soul layer, HTML dashboard, bi-directional vault sync.

Full version history: [CHANGELOG.md](CHANGELOG.md)

---

## License

MIT
