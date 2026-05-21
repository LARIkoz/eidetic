# Eidetic

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/LARIkoz/eidetic/releases/tag/v1.0.0)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](#install)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hooks%20%2B%20skills%20%2B%20rules-purple.svg)](#how-it-works)

**Long-term memory for Claude Code that scales without manual maintenance.**

Claude Code forgets between sessions. `MEMORY.md` has a 200-line limit. Eidetic replaces it with FTS5 search, auto context injection, and session signal extraction. Zero external dependencies — `bash` + `python3` + `sqlite3`.

```bash
git clone https://github.com/LARIkoz/eidetic.git && cd eidetic && bash install.sh
```

---

## The Problem

Claude Code forgets everything between sessions. The built-in `MEMORY.md` auto-memory has a **200-line platform limit** — any behavioral rule past line 200 is invisible to the agent. The agent violates rules it cannot see, creating false confidence.

At a growth rate of 5-10 memory files per week, this problem doubles every 3 months.

**Concrete failure mode:** You create a rule "never suggest API top-up" at line 211 of MEMORY.md. The agent never sees it. It suggests API top-up. You add another rule. Now you have two invisible rules. The index file that's supposed to help the agent actually harms it — it promises knowledge the agent can't access.

Eidetic replaces this bottleneck with:

- **FTS5 full-text search** across all projects (50ms, 400+ files)
- **Automatic context injection** via `~/.claude/rules/` (no size cap)
- **Session signal extraction** (decisions, failures, patterns auto-captured)
- **Compounding** (updates existing knowledge instead of creating duplicates)

---

## How It Works

### Session Start (~200ms, automatic)

```
SessionStart hook fires
  → index.sh --incremental (reindex changed files, <50ms)
  → session_counter.py (record session, get phase hint)
  → assemble_context.py:
      1. Query ALL type=feedback memories (behavioral rules — ALWAYS included)
      2. Query project-relevant memories (matched by CWD)
      3. Query recent cross-project memories (last 14 days)
      4. Rank by compound score: evidence × source × freshness
      5. Pack within 6000-token budget
  → Write to ~/.claude/rules/memory-context.md
  → Claude auto-loads it (no cap, no hook stdout limit)
```

**Result:** Agent starts every session knowing all behavioral rules + project context + recent cross-project knowledge. No re-explaining.

### Mid-Session (on demand)

```
Agent needs past context
  → Invokes /memory-recall skill (context:fork — isolated context)
  → search.sh "deployment decision" runs in fork
  → FTS5 returns top-5 ranked results with snippets
  → Results returned to main context
  → Search tokens never pollute the main conversation
```

### Session End (~5s, async)

```
Stop hook fires (async — doesn't delay session end)
  → Read transcript from stdin JSON
  → Extract last 20 messages (~8KB)
  → claude -p "Extract decisions, rules, failures" --model haiku
  → For each signal:
      1. Search FTS5 for existing memory on same topic
      2. If match found → update existing file + append History section
      3. If no match → create new signals/YYYY-MM-DD.md
      4. Tag with source: agent-extracted (0.5x ranking weight)
  → Reindex incrementally
```

**Result:** Knowledge compounds automatically. The system improves what it already knows instead of growing an ever-expanding pile of files.

---

## Components

### FTS5 Search Index (`index_impl.py` + `search_impl.py`)

**Problem solved:** Finding memories without knowing where they are. "What did we decide about key rotation?" must work regardless of which project folder the file lives in.

**How it works:**

- Scans `~/.claude/projects/*/memory/`, `~/.claude/agent-memory/`, `~/.claude/skills/*/SKILL.md`
- Parses YAML frontmatter (handles both root `type:` and nested `metadata.type:` formats)
- Splits files by `##` headings into chunks (one chunk = one searchable unit)
- Stores in SQLite FTS5 with porter stemmer + unicode61 tokenizer
- Incremental reindex skips unchanged files (mtime check)

**Ranking:** Results ranked by compound score:

```
score = fts5_relevance × evidence_weight × source_weight × freshness_weight

evidence:   validated=1.0   observed=0.7   hypothesis=0.4
source:     user-explicit=1.0   agent-extracted=0.5   system-generated=0.3
freshness:  <30 days=1.0   >30 days=0.5   unknown=0.7
```

A validated user-created memory always outranks an unverified agent-extracted one.

**Performance:** 418 files → 2311 chunks in 0.6s (full), 40ms (incremental). Search: ~50ms. DB: 7.8MB.

### Context Assembly (`assemble_context.py`)

**Problem solved:** The 200-line MEMORY.md limit. 11 critical behavioral rules were invisible.

**How it works:**

- Claude Code auto-loads all files in `~/.claude/rules/` — no size cap
- The hook writes assembled context to `~/.claude/rules/memory-context.md`
- Budget allocation: 50% feedback rules, 30% project context, 20% recent cross-project
- Feedback rules shown as `name: description` (compact format — fits 59+ rules)
- If index is missing, falls back to `head -200 MEMORY.md` (same as old behavior)

**Why not hook stdout?** Claude Code hooks have a 10K character stdout cap. Context assembly needs ~24K characters. Same bypass used by memsearch and claude-code-handoff.

### Signal Extraction + Compounding (`session-signals.sh` + `compound.py`)

**Problem solved:** Knowledge loss between sessions. Useful decisions, patterns, and failures vanish after `/clear` or new session.

**Signal extraction:**

- Stop hook runs async (doesn't block session end)
- Reads transcript, extracts last 20 messages
- Haiku summarizes: decisions made, rules invoked/violated, what worked, what failed
- Cost: ~$0.002/session

**Compounding** (from [Karpathy](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285)):

- Before creating a new file, searches FTS5 for existing memory on same topic
- If existing memory found with high relevance → updates it + adds `## History` entry
- If no match → creates new `signals/YYYY-MM-DD.md`
- All agent-extracted content tagged `source: agent-extracted` (0.5x weight)

**Why 0.5x?** Self-referential discount from [claude-soul](https://github.com/DomDemetz/claude-soul). Without it: agent hallucinates → writes to memory → recalls hallucination → reinforces it. The discount ensures human-created knowledge always outranks agent-created knowledge in search ranking.

### Recall Skill (`SKILL.md`)

**Problem solved:** Mid-session search without polluting context.

- Defined as `context: fork` skill — runs in isolated subagent
- Agent invokes `/memory-recall <query>`
- Search tokens, API calls, and intermediate results stay in the fork
- Only the final summary enters the main conversation

### Lint (`lint_impl.py`)

**Problem solved:** Memory health visibility.

Detects:

- **Broken `[[wikilinks]]`** — link target doesn't exist
- **Orphan files** — zero inbound links
- **Large files** — >5KB with >3 sections (split candidates)
- **Contradiction pairs** — files with `contradicts:` / `contradicted_by:` frontmatter

### Cleanup (`cleanup.py`)

**Problem solved:** Unbounded corpus growth.

- Identifies candidates: orphan + stale (>90 days) + tiny (<100 bytes)
- Protected types: `feedback` and `user` memories never archived
- `--report` mode shows candidates without touching files
- `--archive` moves to `~/.claude/memory-system/archive/` (soft-delete, not hard)
- Human confirmation required — the system proposes, human decides

### Session Counter (`session_counter.py`)

**Problem solved:** Agent treats session 1 the same as session 50.

| Phase    | Sessions | Agent behavior                                                             |
| -------- | -------- | -------------------------------------------------------------------------- |
| Novice   | < 10     | Explain decisions in detail, confirm assumptions before acting             |
| Standard | 10-30    | Standard mode, explain non-obvious decisions only                          |
| Veteran  | 30+      | Be proactive, skip explanations for established patterns, anticipate needs |

Counter persisted in `sessions.db`. Phase hint injected into `memory-context.md` on every session start.

### Rollback (`rollback.sh`)

**Problem solved:** Fear of breaking the existing setup.

One command, <5 seconds:

```bash
bash ~/.claude/memory-system/bin/rollback.sh
```

What it does:

1. Restores `settings.json` from pre-install backup (removes hook registrations)
2. Removes memory system hooks
3. Clears generated `memory-context.md`
4. Memory files and index stay (harmless, deletable manually)

---

## Safety Features

- **Atomic writes** — all file writes use `tempfile.mkstemp()` + `os.replace()`. Crash mid-write doesn't corrupt files.
- **Hook serialization** — `mkdir`-based lockfile prevents SessionStart and Stop hooks from writing simultaneously. POSIX-atomic, macOS-compatible (no `flock` dependency).
- **Graceful degradation** — if FTS5 index is missing or corrupt, hook falls back to `head -200 MEMORY.md`. If Haiku call fails, signal extraction exits silently. No crash, no data loss.
- **Stale lock cleanup** — locks older than 30 seconds are automatically broken.
- **FTS5 query sanitization** — special characters (`*`, `+`, `-`, `NEAR`, `NOT`) stripped to prevent OperationalError. Queries wrapped in quotes for literal matching.

---

## Install

```bash
git clone https://github.com/LARIkoz/eidetic.git
cd eidetic
bash install.sh
```

**Requirements:** `bash`, `python3`, `sqlite3` — pre-installed on macOS and most Linux. Zero pip installs, zero npm, zero Docker.

**What install.sh does:**

1. Backs up current `settings.json` and hooks
2. Copies scripts to `~/.claude/memory-system/bin/`
3. Copies hooks to `~/.claude/hooks/`
4. Installs recall skill to `~/.claude/skills/memory-recall/`
5. Registers hooks in `settings.json`
6. Builds initial FTS5 index
7. Runs health check

---

## Data Model

### SQLite Schema

```sql
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    project TEXT,
    name TEXT,
    type TEXT,                              -- feedback | project | user | reference
    evidence TEXT DEFAULT 'observed',       -- hypothesis | observed | validated
    source TEXT DEFAULT 'user-explicit',    -- user-explicit | agent-extracted | system-generated
    confidence REAL DEFAULT 0.7,
    last_verified TEXT,
    section_heading TEXT,
    content TEXT NOT NULL,
    description TEXT,
    mtime INTEGER,
    UNIQUE(path, section_heading)
);

CREATE VIRTUAL TABLE memory_fts USING fts5(
    name, description, section_heading, content,
    content=memory_chunks, content_rowid=id,
    tokenize='porter unicode61'
);
```

### Frontmatter

The system handles both formats transparently (dual-format parser):

```yaml
# Format A — root type (243 files in test corpus)
---
name: my-memory
description: One-line searchable summary
type: feedback
evidence: validated
---
# Format B — nested metadata (29 files in test corpus)
---
name: my-memory
description: One-line searchable summary
metadata:
  type: feedback
  evidence: observed
  source: user-explicit
  last_verified: 2026-05-20
---
```

---

## Commands

```bash
# Search
~/.claude/memory-system/bin/search.sh "deployment decision" --limit 5
~/.claude/memory-system/bin/search.sh "rules" --type feedback --json

# Index
~/.claude/memory-system/bin/index.sh --full          # Rebuild from scratch (~0.6s)
~/.claude/memory-system/bin/index.sh --incremental   # Only changed files (~40ms)

# Health check
~/.claude/memory-system/bin/health.sh

# Lint
~/.claude/memory-system/bin/lint.sh

# Cleanup
~/.claude/memory-system/bin/cleanup.sh --report      # Show stale candidates
~/.claude/memory-system/bin/cleanup.sh --archive 10   # Archive top 10

# Session stats
python3 ~/.claude/memory-system/bin/session_counter.py "$(pwd)" stats

# Rollback
bash ~/.claude/memory-system/bin/rollback.sh
```

---

## Comparison with Alternatives

### Feature Matrix

| Feature                       | Eidetic                   | [engram](https://github.com/Gentleman-Programming/engram) | [memsearch](https://github.com/zilliztech/memsearch) | [claude-mem](https://github.com/anthropics/claude-mem) | [memex](https://github.com/iamtouchskyer/memex) | [remember-md](https://github.com/nicobailey/remember-md) |
| ----------------------------- | ------------------------- | --------------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------- | -------------------------------------------------------- |
| **Stars**                     | new                       | 3.7K                                                      | 1.8K                                                 | 76K                                                    | 192                                             | 43                                                       |
| **Search**                    | FTS5 (50ms)               | Vector + BM25                                             | Milvus + BM25                                        | SQLite + ChromaDB                                      | Zettelkasten + vector                           | grep                                                     |
| **Auto-inject**               | rules/ (no cap)           | hooks                                                     | hint + skill                                         | MCP server                                             | no                                              | no                                                       |
| **Signal extraction**         | Haiku async               | manual                                                    | no                                                   | PostToolUse                                            | no                                              | no                                                       |
| **Compounding**               | search-before-write       | no                                                        | no                                                   | no                                                     | no                                              | no                                                       |
| **Self-referential discount** | 0.5x                      | no                                                        | no                                                   | no                                                     | no                                              | no                                                       |
| **Evidence tiers**            | 3 tiers + compound weight | no                                                        | no                                                   | no                                                     | no                                              | epistemic schema                                         |
| **Contradiction detection**   | frontmatter fields + lint | no                                                        | no                                                   | no                                                     | no                                              | counter_evidence                                         |
| **Phase-adaptive**            | 3 phases by session count | no                                                        | no                                                   | no                                                     | no                                              | no                                                       |
| **External deps**             | **zero**                  | Node.js                                                   | Milvus, sentence-transformers                        | ChromaDB, bg worker                                    | Python libs                                     | none                                                     |
| **Rollback**                  | 1 command, 5s             | no                                                        | manual                                               | manual                                                 | manual                                          | manual                                                   |
| **Multi-agent**               | Claude Code only          | yes (Cursor, Copilot)                                     | Claude Code                                          | Claude Code                                            | Claude Code                                     | Claude Code                                              |

### Detailed Comparisons

#### vs engram (3.7K stars) — closest competitor

engram is agent-agnostic persistent memory with vector + BM25 search. Its main advantage: works with Cursor, Copilot, and other agents, not just Claude Code.

| engram advantage         | Eidetic advantage                                                       |
| ------------------------ | ----------------------------------------------------------------------- |
| Multi-agent support      | Zero deps (engram needs Node.js runtime)                                |
| Larger community         | Compounding — updates existing files, doesn't just append               |
| Vector search (semantic) | Self-referential discount — agent-created ≠ human-created               |
|                          | Phase-adaptive behavior                                                 |
|                          | Deeper Claude Code integration (rules/, context:fork, async extraction) |
|                          | Atomic writes + crash safety                                            |

**When to use engram:** You work with multiple AI coding agents (Cursor + Claude + Copilot) and need shared memory across all of them.

**When to use Eidetic:** You use Claude Code exclusively and want deeper integration, compounding, quality tracking, and zero dependency overhead.

#### vs memsearch (1.8K stars)

Excellent architecture: Milvus vector DB + BM25 hybrid search + Reciprocal Rank Fusion + `context:fork` isolation.

| memsearch advantage                         | Eidetic advantage                                                           |
| ------------------------------------------- | --------------------------------------------------------------------------- |
| Semantic search ("deploy" finds "shipping") | Zero deps (memsearch needs Milvus + sentence-transformers)                  |
| BGE-M3 embeddings                           | No file-lock bug ([#80](https://github.com/zilliztech/memsearch/issues/80)) |
|                                             | FTS5 achieves 80% recall — sufficient for English markdown                  |
|                                             | Signal extraction + compounding                                             |
|                                             | Evidence tiers + self-referential discount                                  |

**When to use memsearch:** Your corpus is multilingual or heavily semantic (synonyms matter more than keywords).

**When to use Eidetic:** Your memory files are in English markdown with descriptive names and frontmatter. FTS5 + porter stemmer handles this well without a 400MB embedding model.

#### vs claude-mem (76K stars)

Most popular solution. SQLite + ChromaDB + background worker + web UI.

| claude-mem advantage       | Eidetic advantage                                         |
| -------------------------- | --------------------------------------------------------- |
| Largest community          | Zero deps (claude-mem needs ChromaDB + background worker) |
| Web UI for browsing        | Extracts meaningful signals, not every tool call          |
| Rich capture (PostToolUse) | Compounding — doesn't create file-per-event               |
|                            | Self-referential discount                                 |
|                            | Phase-adaptive behavior                                   |
|                            | Single hook, no background process                        |

**When to use claude-mem:** You want a web UI to browse memories and don't mind running a background process.

**When to use Eidetic:** You want a lightweight system that captures signal (not noise), compounds knowledge, and runs entirely within Claude Code hooks.

#### vs Obsidian

Obsidian is a knowledge management tool for humans — graph view, plugins, markdown editor. Claude can't see the graph, can't search through Obsidian's API, doesn't receive auto-injected context.

**Design principle:** When agent utility conflicts with human browsing convenience, agent wins. The agent is the primary consumer of this system. The human benefits indirectly through better agent decisions.

#### vs Karpathy's wiki concept

Karpathy [described](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285) the ideal architecture: `raw/ → wiki/`, three operations (add/update/delete), "LLM does maintenance, humans abandon wikis because maintenance grows faster than value."

Eidetic implements this concept:

- **raw → curated**: session transcript → Haiku extraction → structured signal files
- **Update existing**: compounding logic searches before creating
- **LLM maintenance**: all indexing, searching, compounding done by the agent
- **Three operations**: add (signal extraction), update (compounding), delete (cleanup.sh)

Karpathy's gist is the idea. Eidetic is the implementation.

#### vs Zettelkasten (Luhmann)

Niklas Luhmann maintained 90,000 linked notes over 37 years. His principles: one idea per note, links more important than categories, sparse index (3,200 entries for 90,000 notes).

What we borrowed:

- **Atomic notes** — one topic per file, split large files
- **No rigid categories** — `type` field is a lightweight tag, not a folder taxonomy
- **Links over index** — `[[wikilinks]]` for cross-references, lint detects orphans
- **Sparse index** — FTS5 replaces the manual index entirely

What we automated: everything Luhmann did manually. He wrote, linked, and searched by hand. The agent does all of it in milliseconds.

### Verdict

No existing tool combines all five capabilities:

1. **Search** — finding memories without knowing where they are
2. **Injection** — getting relevant context into the agent automatically
3. **Extraction** — capturing knowledge from sessions without human effort
4. **Quality** — distinguishing validated knowledge from unverified hypotheses
5. **Compounding** — growing a knowledge base, not an archive

Each alternative solves 1-2 of these. Eidetic solves all five, with zero external dependencies, inside Claude Code's native hook/skill/rules system.

### What we took from each

| Source                                                                        | Borrowed                                  | Improved                                    |
| ----------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------- |
| [Karpathy](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285) | raw → curated pipeline, compounding       | Working code with hooks, not a gist         |
| [Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten)                    | Atomic notes, sparse index, no categories | Automated — agent splits, indexes, searches |
| [claude-soul](https://github.com/DomDemetz/claude-soul)                       | Evidence tiers, 0.5x discount, signals    | Integrated into hooks, not a separate SDK   |
| [memsearch](https://github.com/zilliztech/memsearch)                          | FTS5, context:fork                        | No Milvus, no file-lock bugs                |
| [remember-md](https://github.com/nicobailey/remember-md)                      | Epistemic schema, contradictions          | Active surfacing in lint + compounding      |

---

## Performance

| Metric                          | Target           | Actual          |
| ------------------------------- | ---------------- | --------------- |
| Full reindex (418 files)        | < 2 seconds      | 0.6 seconds     |
| Incremental reindex (0 changes) | < 500ms          | 40ms            |
| Search latency                  | < 100ms          | ~50ms           |
| Context assembly                | < 3 seconds      | 200ms           |
| Index size                      | < 10MB           | 7.8MB           |
| Signal extraction cost          | < $0.005/session | ~$0.002 (Haiku) |
| External dependencies           | 0                | 0               |

---

## Transition from Built-in Auto-Memory

Eidetic runs in parallel with Claude's built-in auto-memory (Phase A). This is safe — duplicate context causes no harm, and if the hook fails, MEMORY.md still works.

After 5 stable sessions, disable auto-memory:

```json
// In ~/.claude/settings.json
"autoMemoryEnabled": false
```

The hook's fallback: if FTS5 index is missing, it writes `head -200 MEMORY.md` to the rules file — identical to the old behavior.

---

## Roadmap

- **Vector search** — hybrid FTS5 + embeddings via RRF. Triggered when FTS5 recall drops below 80% (currently at threshold with 418 files).
- **Serendipity links** — surface unexpected connections between memories across projects. "You're working on key rotation → btw, there's a rate-limit finding in another project."
- **Multi-agent support** — extend hooks to work with Codex CLI and Gemini CLI alongside Claude Code.

---

## License

MIT
