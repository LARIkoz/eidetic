# Eidetic

Long-term memory for Claude Code that scales without manual maintenance.

Claude forgets between sessions. The built-in `MEMORY.md` has a 200-line platform limit — critical behavioral rules past line 200 are invisible to the agent. This system replaces that bottleneck with FTS5 full-text search, automatic context injection, and session signal extraction.

## What it does

**On every session start** (~200ms):

- Indexes all memory files across all projects into SQLite FTS5
- Assembles behavioral rules + project context + recent cross-project knowledge
- Writes to `~/.claude/rules/memory-context.md` (auto-loaded by Claude, no size cap)
- Tracks session count for phase-adaptive behavior hints

**During sessions:**

- `/memory-recall <query>` — search in isolated `context:fork` (search tokens don't pollute main context)
- Ranked results with compound scoring: `evidence_tier * source_weight * freshness`

**On session end** (async, ~5s):

- Extracts decisions, rules, failures from transcript via Haiku
- Searches FTS5 before creating new files — updates existing memories instead of duplicating (compounding)
- Tags agent-extracted signals with 0.5x weight (self-referential discount)

## Architecture

```
~/.claude/memory-system/
├── bin/
│   ├── index.sh + index_impl.py      # FTS5 indexer (dual-format frontmatter)
│   ├── search.sh + search_impl.py    # Search with compound ranking
│   ├── assemble_context.py           # Context assembly → rules/memory-context.md
│   ├── compound.py                   # Search-before-write + update existing
│   ├── lint.sh + lint_impl.py        # Orphan/stale/broken link/contradiction detection
│   ├── cleanup.sh + cleanup.py       # Stale memory archival (soft-delete)
│   ├── session_counter.py            # Session counter + phase-adaptive hints
│   ├── health.sh                     # System health check
│   └── rollback.sh                   # Emergency rollback (1 command, <5 seconds)
├── db/
│   ├── index.db                      # SQLite FTS5 database
│   └── sessions.db                   # Session counter

~/.claude/hooks/
├── smart-memory-inject.sh            # SessionStart → assemble + inject
└── session-signals.sh                # Stop → extract + compound + reindex

~/.claude/skills/memory-recall/
└── SKILL.md                          # context:fork recall skill

~/.claude/rules/
└── memory-context.md                 # Auto-loaded by Claude (generated)
```

## Install

```bash
git clone https://github.com/LARIkoz/engram.git
cd engram
bash install.sh
```

Requirements: `bash`, `python3`, `sqlite3` (all pre-installed on macOS/Linux). Zero pip installs.

## Uninstall

```bash
bash ~/.claude/memory-system/bin/rollback.sh
# Optionally: rm -rf ~/.claude/memory-system ~/.claude/skills/memory-recall
```

Rollback restores `settings.json` from backup, removes hooks, clears generated context. Memory files untouched.

## Key Design Decisions

### FTS5 over vectors

SQLite FTS5 with porter stemmer achieves 80% strict recall (95% real) on a 20-query benchmark against 418 English markdown files. Vector search (memsearch approach) is deferred until FTS5 drops below 80% — zero external deps vs. 400MB embedding model.

### Rules file injection over hook stdout

Claude Code hooks have a 10K character stdout cap. The system writes to `~/.claude/rules/memory-context.md` instead — Claude auto-loads all files in `rules/` without any cap. Same approach used by memsearch and claude-code-handoff.

### Self-referential discount (0.5x)

Memories created by the agent itself (`source: agent-extracted`) weigh 0.5x vs. user-created memories (1.0x). Prevents feedback loop: agent hallucination → memory → recall → reinforced hallucination. Borrowed from [claude-soul](https://github.com/DomDemetz/claude-soul).

### Compounding over appending

Before creating a new signal file, the system searches FTS5 for existing memories on the same topic. If found, it updates the existing file and appends to a `## History` section. Borrowed from Karpathy: "Humans abandon wikis because maintenance grows faster than value. LLMs don't get bored."

### Atomic writes

All file writes use `tempfile.mkstemp()` + `os.replace()` for crash safety. Hook serialization via `mkdir`-based lockfile (POSIX-atomic, macOS-compatible — no `flock` dependency).

### Compound ranking

```
score = fts5_relevance * evidence_weight * source_weight * freshness_weight

evidence:  validated=1.0  observed=0.7  hypothesis=0.4
source:    user-explicit=1.0  agent-extracted=0.5  system-generated=0.3
freshness: <30 days=1.0  >30 days=0.5  unknown=0.7
```

## Schema

```sql
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    project TEXT,
    name TEXT,
    type TEXT,                              -- feedback|project|user|reference
    evidence TEXT DEFAULT 'observed',       -- hypothesis|observed|validated
    source TEXT DEFAULT 'user-explicit',    -- user-explicit|agent-extracted|system-generated
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

## Frontmatter

The system handles both formats transparently:

```yaml
# Format A (root type)
---
name: my-memory
description: One-line searchable summary
type: feedback
---
# Format B (nested metadata)
---
name: my-memory
description: One-line searchable summary
metadata:
  type: feedback
  evidence: observed
  source: user-explicit
---
```

## Commands

```bash
# Search
~/.claude/memory-system/bin/search.sh "deployment decision" --limit 5
~/.claude/memory-system/bin/search.sh "rules" --type feedback --json

# Index
~/.claude/memory-system/bin/index.sh --full          # Rebuild from scratch
~/.claude/memory-system/bin/index.sh --incremental   # Only changed files

# Health
~/.claude/memory-system/bin/health.sh

# Lint
~/.claude/memory-system/bin/lint.sh     # Orphans, broken links, contradictions, large files

# Cleanup
~/.claude/memory-system/bin/cleanup.sh --report      # Show stale candidates
~/.claude/memory-system/bin/cleanup.sh --archive 10   # Archive top 10

# Session stats
python3 ~/.claude/memory-system/bin/session_counter.py "$(pwd)" stats
```

## Comparison with Alternatives

| Feature                       | Eidetic                            | [engram](https://github.com/Gentleman-Programming/engram) (3.7K) | [memsearch](https://github.com/zilliztech/memsearch) (1.8K) | [claude-mem](https://github.com/anthropics/claude-mem) (76K) | [memex](https://github.com/iamtouchskyer/memex) (192) | [remember-md](https://github.com/nicobailey/remember-md) (43) | Karpathy wiki | Obsidian |
| ----------------------------- | ---------------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------------------------------- | ------------- | -------- |
| Search                        | FTS5 (50ms)                        | Vector + BM25                                                    | Milvus + BM25                                               | SQLite + ChromaDB                                            | Zettelkasten + vector                                 | grep                                                          | N/A (concept) | Built-in |
| Auto-inject on session start  | Yes (rules/)                       | Yes (hooks)                                                      | Hint + skill                                                | MCP server                                                   | No                                                    | No                                                            | No            | No       |
| Signal extraction             | Haiku async                        | Manual                                                           | No                                                          | PostToolUse capture                                          | No                                                    | No                                                            | Concept only  | No       |
| Compounding (update existing) | Yes (FTS5 search-before-write)     | No                                                               | No                                                          | No                                                           | No                                                    | No                                                            | Concept only  | Manual   |
| Self-referential discount     | 0.5x agent-extracted               | No                                                               | No                                                          | No                                                           | No                                                    | No                                                            | No            | N/A      |
| Evidence tiers                | hypothesis/observed/validated      | No                                                               | No                                                          | No                                                           | No                                                    | Yes (epistemic)                                               | No            | No       |
| Contradiction detection       | contradicts/contradicted_by        | No                                                               | No                                                          | No                                                           | No                                                    | counter_evidence                                              | No            | No       |
| External deps                 | **Zero**                           | Node.js                                                          | Milvus, sentence-transformers                               | ChromaDB, bg worker                                          | Python libs                                           | None                                                          | N/A           | Electron |
| Claude Code native            | Hooks + skills + rules             | Hooks                                                            | Skill only                                                  | MCP                                                          | No                                                    | CLAUDE.md                                                     | N/A           | No       |
| Phase-adaptive                | Session counter + behavioral hints | No                                                               | No                                                          | No                                                           | No                                                    | No                                                            | No            | No       |
| Rollback                      | 1 command, 5 seconds               | No                                                               | Manual                                                      | Manual                                                       | Manual                                                | Manual                                                        | N/A           | N/A      |
| Agent-agnostic                | Claude Code only                   | Yes (multi-agent)                                                | Claude Code                                                 | Claude Code                                                  | Claude Code                                           | Claude Code                                                   | Any           | Any      |

### Why not engram? (3.7K stars)

Closest competitor. Agent-agnostic memory for coding assistants with vector + BM25 search. Advantages over Eidetic: works with multiple agents (Cursor, Copilot, etc.), larger community. Eidetic's advantages: zero deps (engram needs Node.js), compounding (engram creates new files, doesn't update existing), self-referential discount (engram treats all sources equally), phase-adaptive behavior, deeper Claude Code integration (rules/ injection, context:fork skill, async signal extraction).

### Why not memsearch? (1.8K stars)

Excellent architecture (Milvus + BM25 hybrid, context:fork isolation). But: file-lock bug ([#80](https://github.com/zilliztech/memsearch/issues/80)) breaks recall while watch runs; vector search is overkill for English markdown (FTS5 achieves 80%+ recall at zero cost); requires Milvus.

### Why not claude-mem? (76K stars)

Most popular, but heavy: background worker, ChromaDB, web UI. PostToolUse capture fires on every tool call — noise overwhelms signal. Eidetic extracts only at session end, only meaningful signals, compounds into existing files.

### Why not memex? (192 stars)

Zettelkasten-based, good principles. But no auto-injection, no signal extraction, no compounding, no quality tracking. Closer to a note-taking system than an active memory engine.

### Why not Obsidian?

Human browsing tool. Claude can't see the graph view, can't search through Obsidian API, doesn't receive auto-injected context. When agent utility conflicts with human browsing, agent wins — this is Eidetic's design principle.

### What we took from each

| Source                                                                                     | What we borrowed                                             | How we improved it                                                               |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| [Karpathy](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285) wiki concept | raw → curated pipeline, compounding, "LLM does maintenance"  | Working code with hooks integration, not just a concept                          |
| [Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten) (Luhmann)                       | Atomic notes, sparse index, no rigid categories              | Automated: agent splits, indexes, searches. Luhmann did it manually for 37 years |
| [claude-soul](https://github.com/DomDemetz/claude-soul)                                    | Evidence tiers, 0.5x self-referential discount, signal types | Integrated into Claude Code hooks, not a separate SDK                            |
| [memsearch](https://github.com/zilliztech/memsearch)                                       | FTS5 search, context:fork isolation                          | No Milvus dependency, no file-lock bugs                                          |
| [remember-md](https://github.com/nicobailey/remember-md)                                   | Epistemic schema, contradiction fields                       | Added compounding + active contradiction surfacing in lint                       |

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

## Phase-Adaptive Behavior

The session counter tracks sessions per project:

| Phase    | Sessions | Behavior hint                                     |
| -------- | -------- | ------------------------------------------------- |
| Novice   | < 10     | Explain decisions in detail, confirm assumptions  |
| Standard | 10-30    | Standard mode, explain non-obvious only           |
| Veteran  | 30+      | Be proactive, skip explanations, anticipate needs |

## Transition from built-in autoMemory

The system runs in parallel with Claude's built-in auto-memory (Phase A). After 5 stable sessions, you can disable auto-memory:

```json
// In ~/.claude/settings.json
"autoMemoryEnabled": false
```

The hook's fallback: if FTS5 index is missing, it writes `head -200 MEMORY.md` to the rules file — same as the old behavior.

## License

MIT
