# Eidetic

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-4.2.3-blue.svg)](#changelog)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hooks%20%2B%20skills%20%2B%20rules-purple.svg)](#how-it-works)
[![MCP](https://img.shields.io/badge/MCP-Cursor%20%7C%20Windsurf%20%7C%20Cline-orange.svg)](#mcp-server)

**A Second Brain for your AI coding agent.**

Inspired by [Tiago Forte's Building a Second Brain](https://www.buildingasecondbrain.com/), [Luhmann's Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten) (90,000 linked notes over 37 years), and [Karpathy's AI wiki concept](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285). Eidetic applies these ideas to AI agents: atomic knowledge cards, automatic linking, compounding over time, and quality-weighted recall.

Your AI agent forgets everything between sessions. Eidetic fixes that.

```
Session 1:  "Never mock the database in tests"
Session 2:  *mocks the database*
Session 3:  "I told you not to mock the database"
Session 4:  *mocks the database*
```

After Eidetic:

```
Session 1:  "Never mock the database in tests"
Session 2:  *rule auto-injected, follows it*
Session 50: *still follows it, plus 123 other rules you taught it*
```

```bash
git clone https://github.com/LARIkoz/eidetic.git && cd eidetic && bash install.sh
```

One command. Core has zero external dependencies and works immediately.
Optional semantic/vector search and code-aware indexing use small pip packages.

Maintainer-local project governance lives in [`PROJECT_MAP.md`](PROJECT_MAP.md).
End users can ignore it; it links this runtime repo to the canonical brief,
charter, installed runtime, source corpus, and human-facing projection in the
maintainer workspace.

---

## The Problem

Claude Code has a **200-line MEMORY.md limit**. Your 124 behavioral rules? Only 57 fit. The rest are invisible. The agent violates rules it literally cannot see.

Every new session starts from zero. Decisions, patterns, failures you taught it yesterday — gone. You re-explain. Again. And again.

### Why a longer MEMORY.md wouldn't help

Even if the limit were 10,000 lines — MEMORY.md is a flat file. A flat file can't:

| What you need                                                 | MEMORY.md                            | Eidetic                                                                 |
| ------------------------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------- |
| **Search** — "what did we decide about testing?"              | Read top to bottom, every time       | FTS5 + vector, 50ms, finds it across any project                        |
| **Relevance** — show rules for THIS project, not all projects | Everything dumped together           | Filters by CWD, ranks by project relevance                              |
| **Learning** — capture decisions from sessions automatically  | You manually edit after each session | Haiku extracts signals, compounds into existing memories                |
| **Quality** — distinguish proven rules from agent guesses     | All lines have equal weight          | Evidence tiers: validated > observed > hypothesis. Agent-created = 0.5x |
| **Freshness** — detect when a rule became outdated            | No way to know                       | Freshness decay: >30 days = lower rank. Drift detection (planned)       |
| **Code search** — "where is the rate limiter?"                | Not possible                         | Tree-sitter parses functions/classes into searchable chunks             |

A bigger MEMORY.md is a longer sticky note. Eidetic is a searchable, self-updating knowledge base with quality tracking.

## What Eidetic Does

| Without Eidetic             | With Eidetic                                     |
| --------------------------- | ------------------------------------------------ |
| 57 of 124 rules visible     | **124 of 124 rules visible** (smart compression) |
| Keyword search only         | **Hybrid FTS5 + vector search**                  |
| Only markdown memories      | **Code search** — find functions by meaning      |
| Forgets between sessions    | **Auto-extracts** decisions, failures, patterns  |
| Knowledge piles up as files | **Compounds** — updates existing knowledge       |
| Session 1 = Session 50      | **Phase-adaptive** — proactive after 30 sessions |

## How It Works

```
                    SESSION START (~350ms warm, ~11s first run*)
                                |
                    +-----------+-----------+
                    |           |           |
               Reindex     Code Index   Vector Embed
              (FTS5,40ms)  (tree-sitter) (fastembed)
                    |           |           |
                    +-----+-----+-----+-----+
                          |
                   Assemble Context
                   (124 rules + project + recent)
                          |
                   Write to ~/.claude/rules/
                   (auto-loaded, no size cap)
                          |
                   Agent starts KNOWING everything


                         MID-SESSION
                              |
                    /memory-recall "query"
                              |
                    context:fork (isolated search)
                              |
                    FTS5 → vector fallback → RRF merge
                              |
                    Results back to main context
                    (search tokens stay in fork)


                         SESSION END (~5s, async)
                              |
                    Extract signals (Haiku, ~$0.002)
                              |
                    Search existing memories
                              |
                    Update existing OR create new
                    (compound, don't duplicate)
                              |
                    Tag: agent-extracted = 0.5x weight
                    (can't reinforce own hallucinations)
```

---

## Key Features

### Smart Token Compression (v1.3)

124 feedback rules in 5927 tokens. Previously only 57 fit.

How: keyword clustering (24 related rules compressed into 1 block), tiered display (important = full text, low-priority = name only), adaptive budget allocation.

### Hybrid Search (v2.0)

FTS5 for keywords (50ms). Vector search as fallback when keyword quality is low. Results merged via Reciprocal Rank Fusion. Search output includes a conservative confidence label; if every candidate is weak, the CLI reports `No confident results` instead of surfacing random-looking vector neighbors as actionable memory.

| Query type                  | FTS5 only   | Hybrid                   |
| --------------------------- | ----------- | ------------------------ |
| "consilium audit"           | found       | found                    |
| "making AI remember things" | **nothing** | **memory-recall SKILL**  |
| "shrink prompt size"        | **nothing** | **context optimization** |
| **Historical benchmark**    | **30%**     | **100%**                 |

Powered by [fastembed](https://github.com/qdrant/fastembed) (ONNX, 33MB model). Optional — FTS5 works without it. Re-run the benchmark on your own corpus after changing ranking or vector thresholds.

### Code-Aware Search (v2.2)

Tree-sitter parses `.py`, `.js`, `.ts`, `.sh` — every function and class becomes searchable.

```bash
# Find a function by name or purpose
search.sh "fetch_feedback" --type code
# → assemble_context.py:L103-177, full function body
```

338 code entities indexed in 0.1s. Auto-reindexes your current project every session.

### Knowledge Compounding

Before creating a new memory, searches for existing ones on the same topic. If found — updates it, adds history. If not — creates new file.

Inspired by [Karpathy's wiki concept](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285): "LLMs should maintain a wiki that compounds, not an archive that piles up."

### Self-Referential Discount

Agent-extracted memories rank at **0.5x** weight. Human-created knowledge always outranks agent guesses. Without this, hallucinations write to memory, get recalled, and reinforce themselves.

From [claude-soul](https://github.com/DomDemetz/claude-soul) — the only memory system that implements this.

### Serendipity Links

After a search, Eidetic looks for unexpected cross-project connections via wikilinks and keyword overlap. Based on Luhmann's Zettelkasten principle: "The slip-box is designed to surprise you."

### Phase-Adaptive Behavior

| Phase    | Sessions | Behavior                                      |
| -------- | -------- | --------------------------------------------- |
| Novice   | < 10     | Detailed explanations, confirms before acting |
| Standard | 10-30    | Standard mode                                 |
| Veteran  | 30+      | Proactive, skips established patterns         |

### Obsidian Vault Export (v4.2)

Your AI worked 50 sessions. It learned 500 things. Now see them.

```bash
eidetic export-vault ~/my-vault/
# Export + LLM polish + open in Obsidian

eidetic export-vault ~/my-vault/ --synthesize
# Experimental: also generate topic pages

eidetic export-vault ~/my-vault/ --no-polish
# Fast export, no API calls
```

Opens in Obsidian with pre-configured graph colors, backlinks, and Maps of Content. Topic synthesis is experimental and off by default until the v4.3 Vault IA pass replaces the current topic model.

**What makes this different from "open memory folder in Obsidian":**

| Raw memory dump                 | Eidetic export                         |
| ------------------------------- | -------------------------------------- |
| 500+ files including debug logs | ~120 curated, validated notes          |
| Agent jargon, terse one-liners  | Human-readable cards + optional LLM polish |
| Flat list, no structure         | Folders by type + auto-MOC |
| Dangling wikilinks everywhere   | Links verified against export set      |
| No graph sense                  | Color-coded by type, hub nodes visible |

```bash
# Single project only
eidetic export-vault ~/my-vault/ --project gap-pipeline

# Incremental update (only changed files)
eidetic export-vault ~/my-vault/ --delta

# Fast scheduled/no-API update
eidetic export-vault ~/my-vault/ --delta --no-polish --no-open
```

The quality gate filters out operational files (handoff states, synth failures), files without metadata, and oversized monoliths. What passes: user-written rules, validated decisions, reference cards, project findings.

---

## Performance

| Metric                          | Value                                                             |
| ------------------------------- | ----------------------------------------------------------------- |
| Session start (warm)            | **~350ms**                                                        |
| Session start (cold, first run) | ~11s (fastembed ONNX model load)                                  |
| Full reindex (522 files)        | 0.6s                                                              |
| Incremental reindex             | 40ms                                                              |
| FTS5 search                     | ~50ms                                                             |
| Hybrid search (FTS5 + vector)   | ~200ms                                                            |
| Code index (143 files)          | 0.1s                                                              |
| Signal extraction cost          | ~$0.002/session                                                   |
| Index size                      | 9.5MB (FTS5) + 5.9MB (vectors)                                    |
| External dependencies           | **zero for core**; optional fastembed/tree-sitter for v2 features |

---

## Install

```bash
git clone https://github.com/LARIkoz/eidetic.git
cd eidetic
bash install.sh
```

**Requirements:** `bash`, `python3`, `sqlite3` (pre-installed on macOS/Linux).

Optional daily vault export can be enabled during install with:

```bash
EIDETIC_SETUP_CRON=1 bash install.sh
```

**Optional upgrades:**

```bash
pip install fastembed        # +33MB, enables semantic search (v2.0)
pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-bash  # code search (v2.2)
```

Core works without any pip installs. Without optional packages, Eidetic degrades to FTS5-only memory search and skips code indexing. Rollback in 5 seconds: `bash ~/.claude/memory-system/bin/rollback.sh`

### Updates

Eidetic checks for updates in the background at session start (every 6 hours). When an update is available, you'll see a one-line notice:

```
Eidetic update available (a1b2c3d). Run: bash ~/.claude/memory-system/bin/update.sh
```

Updates preserve your databases, rules, and hook registrations — only code files are replaced.

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

6 tools: `memory_search`, `memory_serendipity`, `memory_health`, `memory_reindex`, `memory_lint`, `export_vault`.

MCP `export_vault` defaults to no LLM calls to avoid surprise API usage and timeouts. Pass `polish=true` when you want the v4.1 enrichment path. `synthesize=true` remains available as an experimental topic-candidate path, but it is not recommended for normal vault exports until v4.3 Vault IA lands.

---

## Safety

- **Atomic writes** — `tempfile` + `os.replace()`. Crash mid-write = no corruption
- **Backup/restore** — full reindex creates backup, restores on failure
- **Lock serialization** — `mkdir`-based POSIX-atomic lock with stale cleanup
- **Graceful degradation** — missing index? Falls back to `head -200 MEMORY.md`
- **Anti-injection** — signal extraction prompt has safety rules against transcript content becoming memory
- **FTS5 sanitization** — special characters stripped, queries quoted
- **LIKE escape** — SQL wildcards in paths can't leak cross-project data
- **Rollback** — one command, <5 seconds, restores everything

---

## Compound Ranking

Every search result is ranked by:

```
score = relevance x evidence x source x freshness

evidence:   validated = 1.0    observed = 0.7    hypothesis = 0.4
source:     user-created = 1.0  agent-extracted = 0.5  system = 0.3
freshness:  < 30 days = 1.0    > 30 days = 0.5
```

A validated, recent, human-created memory always outranks an old, unverified, agent-extracted guess.

---

## Comparison

### What only Eidetic does

These features exist in no other Claude Code memory tool (as of May 2026, based on [40-repo competitive analysis](https://github.com/LARIkoz/eidetic/releases/tag/v2.2.0)):

| Unique feature                | What it means                                            | Why it matters                                                     |
| ----------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------ |
| **Compounding**               | Updates existing memories instead of creating duplicates | 50 sessions = 50 refined rules, not 500 files                      |
| **Self-referential discount** | Agent-extracted memories ranked 0.5x vs human-created    | Prevents hallucination → memory → recall → reinforcement loops     |
| **Evidence tiers**            | validated > observed > hypothesis, compound-weighted     | Search returns proven knowledge first, guesses last                |
| **Code search**               | Tree-sitter AST → searchable functions/classes           | "Where is the rate-limit handler?" actually works                  |
| **Phase-adaptive**            | Behavior changes at 10/30 sessions                       | Session 50 agent is proactive, session 1 agent explains everything |
| **Zero deps core**            | bash + python3 + sqlite3                                 | No Docker, no npm, no pip for basic usage. `install.sh` and done   |

### Full comparison

| Capability                   | Eidetic                            | [claude-mem](https://github.com/anthropics/claude-mem) | [engram](https://github.com/Gentleman-Programming/engram) | [memsearch](https://github.com/zilliztech/memsearch) | [lucasrosati](https://github.com/lucasrosati/claude-code-memory-setup) |
| ---------------------------- | ---------------------------------- | ------------------------------------------------------ | --------------------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------- |
|                              | **v2.5.0**                         | **76K stars**                                          | **3.7K stars**                                            | **1.8K stars**                                       | **684 stars**                                                          |
| Search                       | FTS5 + vector                      | SQLite + Chroma                                        | Vector + BM25                                             | Milvus + BM25                                        | Obsidian                                                               |
| Recall benchmark             | **100%**                           | —                                                      | —                                                         | ~95%                                                 | —                                                                      |
| Auto-inject on session start | **rules/ (no cap)**                | MCP                                                    | hooks                                                     | hint                                                 | Obsidian vault                                                         |
| Signal extraction            | Haiku async                        | PostToolUse                                            | manual                                                    | —                                                    | —                                                                      |
| Compounding                  | **yes**                            | —                                                      | —                                                         | —                                                    | —                                                                      |
| Self-ref discount            | **0.5x**                           | —                                                      | —                                                         | —                                                    | —                                                                      |
| Evidence tiers               | **3 tiers**                        | —                                                      | —                                                         | —                                                    | —                                                                      |
| Code search                  | **tree-sitter**                    | —                                                      | —                                                         | —                                                    | —                                                                      |
| Phase-adaptive               | **3 phases**                       | —                                                      | —                                                         | —                                                    | —                                                                      |
| Serendipity links            | **yes**                            | —                                                      | —                                                         | —                                                    | —                                                                      |
| Multi-agent (MCP)            | yes                                | yes                                                    | yes (Cursor, Copilot)                                     | yes                                                  | —                                                                      |
| Dependencies                 | **zero core; optional pip for v2** | ChromaDB, worker                                       | Node.js                                                   | Milvus, PyTorch                                      | Obsidian app                                                           |
| Rollback                     | **1 cmd, 5s**                      | manual                                                 | —                                                         | manual                                               | manual                                                                 |
| Drift detection              | **wikilink + age + confidence**    | —                                                      | —                                                         | —                                                    | —                                                                      |
| Token compression            | **2.17x** (57→124 rules)           | —                                                      | —                                                         | —                                                    | 71x (claimed)                                                          |
| Obsidian vault export        | **quality-filtered + templates**   | —                                                      | —                                                         | —                                                    | raw chat import                                                        |

### When to use what

| Your situation                                      | Best choice |
| --------------------------------------------------- | ----------- |
| Claude Code user who wants it to remember and learn | **Eidetic** |
| Need shared memory across Cursor + Claude + Copilot | engram      |
| Already using Obsidian, want simple integration     | lucasrosati |
| Need heavy multilingual semantic search             | memsearch   |
| Want largest community and web UI                   | claude-mem  |

---

## Philosophy: Second Brain for AI Agents

Humans have Second Brain systems — Zettelkasten, Obsidian, Notion. AI agents have nothing. They start every session with amnesia.

Eidetic applies Second Brain principles to AI agents:

| Second Brain concept                                               | Human tool                          | Eidetic equivalent                                                      |
| ------------------------------------------------------------------ | ----------------------------------- | ----------------------------------------------------------------------- |
| **Atomic notes** — one idea per card                               | Zettelkasten slip                   | One memory file per topic, split large files automatically              |
| **Linking over categories** — connections matter more than folders | `[[wikilinks]]` in Obsidian         | `[[wikilinks]]` + serendipity search across all projects                |
| **Progressive summarization** — content gets refined over time     | Highlight → bold → summary          | Compounding: raw signal → structured memory → updated with history      |
| **Capture everything, curate later** — inbox → refined knowledge   | Quick Capture → Projects            | Session signals (raw) → compound.py (refined) → quality-weighted        |
| **Spaced repetition** — resurface what matters                     | Anki flashcards                     | Freshness decay + drift detection (v2.5)                                |
| **The system maintains itself** — or it dies                       | Manual maintenance = abandoned wiki | Agent does all indexing, linking, searching, compounding. Human curates |

> "People don't abandon wikis because wikis are bad. They abandon them because maintenance grows faster than value." — [Karpathy](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285)

Eidetic solves this: the AI agent maintains its own knowledge base. Maintenance cost = zero for the human.

### Architecture principles

1. **Files are truth.** Indexes are derived. If the DB dies, rebuild from markdown in <1s.
2. **Every memory must be findable without knowing where it is.** Cross-project search is the default.
3. **Critical rules must never become invisible.** The system guarantees all behavioral rules reach the agent.

### Intellectual sources

| Source                                                                                  | What we borrowed                                  | What we added                                         |
| --------------------------------------------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------- |
| [Luhmann's Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten)                    | Atomic notes, sparse index, links over categories | Automated — agent splits, indexes, links, searches    |
| [Tiago Forte's Second Brain](https://www.buildingasecondbrain.com/)                     | Capture → Organize → Distill → Express (CODE)     | Applied to AI: signals → compound → inject → act      |
| [Karpathy's AI wiki](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285) | raw → curated pipeline, LLM does maintenance      | Working hooks, not a gist. Compounding, not appending |
| [claude-soul](https://github.com/DomDemetz/claude-soul)                                 | Evidence tiers, 0.5x self-ref discount, signals   | Integrated into hooks, not a separate SDK             |
| [memsearch](https://github.com/zilliztech/memsearch)                                    | FTS5, context:fork isolation                      | + vector hybrid, no Milvus, no file-lock bugs         |

**Obsidian-compatible today:** Memory files are markdown + `[[wikilinks]]` + YAML frontmatter. You can open `~/.claude/projects/` as an Obsidian vault, or use `eidetic export-vault` (v4.2) for a quality-filtered vault with templates, auto-MOCs, verified wikilinks, and optional LLM polish. Experimental topic synthesis is opt-in with `--synthesize`.

---

## Roadmap

### Done

- [x] **v1.0** — FTS5 search + auto-inject + signal extraction + compounding + evidence tiers + serendipity
- [x] **v1.1** — 12 bug fixes via consreview (6 voices), session counter, phase-adaptive
- [x] **v1.2** — MCP server (5 tools), works with Cursor/Windsurf/Cline
- [x] **v1.3** — Smart token compression (57 → 124 rules, 2.17x)
- [x] **v2.0** — Hybrid FTS5 + vector search (fastembed, historical 30% → 100% recall benchmark)
- [x] **v2.2** — Code-aware parsing (tree-sitter, 338 entities from 143 files)
- [x] **v2.2.2** — Auto-update system, search recall 12→18/20 (vector boost + dedup)
- [x] **v2.5** — Drift detection: wikilink validation, age-based staleness, confidence escalation. No competitor does this.
- [x] **v4.0** — Obsidian vault export: quality gate, template formatting, auto-MOC, wikilink resolution, delta tracking
- [x] **v4.1** — LLM polish, smart Sonnet/Haiku routing, MCP `export_vault`, plug-and-play Obsidian open
- [x] **v4.2** — LLM topic clustering and wiki-style topic synthesis
- [x] **v4.2.1** — Runtime hardening: non-interactive install, MCP export flags/timeouts, docs/version sync, CI export smoke
- [x] **v4.2.2** — Disable topic synthesis by default; keep it explicit/experimental pending v4.3 IA
- [x] **v4.2.3** — v2.6 foundations: confidence-aware search, stale-context health signal, operator recall smoke

### Next

- [ ] **v2.6 — Agent Memory Quality** — schema/status/card-kind cleanup, confidence-aware search, stale-context detection, recall regression suite
- [ ] **v3.0 — Task Planner Bridge** — sync memory signals to YouGile/Linear/GitHub Issues. Pluggable adapter.

### v5.0 (deferred)

- Vault IA — replace flat projects/references/topics with areas, card kinds, deterministic MOCs, and topic-candidate review gates
- Soul layer — personality profile, tension detection, decision style adaptation
- HTML dashboard — single-file knowledge graph (D3.js)
- Progressive summarization — memories mature via LLM distillation
- Bi-directional vault sync

---

## Changelog

### v4.2.3 (2026-05-24)

- Search results now include `confidence`, `confidence_reason`, `retrieval_score`, and `rrf_score` fields
- CLI search suppresses all-low-confidence result sets and reports `No confident results`
- `health.sh` reports stale `memory-context.md` when assembled counts no longer match `index.db`
- Added `bin/recall_smoke.py` for operator-corpus recall regression checks
- CI asserts confidence metadata in JSON search output

### v4.2.2 (2026-05-24)

- Topic synthesis is now opt-in via `--synthesize`; normal CLI exports no longer create `topics/`
- Existing `--no-synthesize` remains accepted as a compatibility no-op
- Documentation marks current topic synthesis as experimental pending v4.3 Vault IA
- MCP `synthesize=true` remains available for explicit experiments only

### v4.2.1 (2026-05-24)

- Installer stays non-interactive by default; daily vault export is opt-in via `EIDETIC_SETUP_CRON=1`
- MCP `export_vault` now exposes `polish`, `synthesize`, `polish_count`, `polish_model`, `force`, `all`, and `timeout`
- MCP export defaults to no LLM calls; CLI export keeps the enriched v4.2 path
- Version/docs synchronized to v4.2.x and CI now smokes no-LLM vault export

### v4.2.0 (2026-05-23)

- **Topic synthesis** — clusters exported notes into wiki-style topic pages
- LLM-based clustering replaced hardcoded topic keywords
- Opus synthesis for better topic pages; large clusters use top notes by weight with context caps
- `HOME.md` links synthesized topics when available

### v4.1.0 (2026-05-23)

- **LLM polish** — rewrites exported note bodies for human-readable Obsidian cards
- Smart model routing: Sonnet for complex notes, Haiku for simple notes
- MCP `export_vault` tool added
- Plug-and-play Obsidian registration/open on macOS
- Human-readable filenames with title preservation and collision handling
- Polish circuit breaker and idempotent re-run guard

### v4.0.0 (2026-05-23)

- **Obsidian vault export** — `eidetic export-vault ~/my-vault/`
- Quality gate: filters 500+ files down to ~120 validated knowledge notes
- Template formatting: Rule Cards, Status Cards, Quick References, Profile Cards + passthrough fallback
- Wikilink resolution: links verified against export set, dangling stripped, auto-aliases
- Auto-MOC per folder + HOME.md root index
- `.obsidian/` pre-config with graph colors (first export only, never overwrites)
- Delta mode: `.manifest.json` tracks SHA256, `--delta` skips unchanged
- `--project` with fuzzy match, `--all --force` for raw dump
- Reviewed: Murphy (M1-M21), Adversarial, Consilium (6 voices), Consreview (5 voices)

### v2.5.0 (2026-05-22)

- **Drift detection** — wikilink validation, type-based age thresholds, confidence escalation detection
- Separate `drift_state.db` (P1: index.db stays derived/rebuildable)
- Differential penalty: broken_wikilink=0.8x, age_stale=0.5x, confidence_escalation=0.3x
- Baseline mode: first detection = no penalty, penalty on second consecutive detection
- 24h throttle, auto-resolve when drift disappears, orphan pruning
- Drift-aware ranking in both search and context assembly
- Crash-safe full reindex via temp DB + `os.replace()`
- PID-based lock replaces TTL-based mkdir (macOS compatible, no race)
- 13 bugfixes from consilium (5 voices) + consreview (6 voices)
- Constants deduplication (`constants.py`), compound.py project matching fix
- Search recall improved to 18/20 (vector boost + per-path dedup + tiered FTS)

### v2.2.2 (2026-05-22)

- Auto-update system: version tracking, background update check every 6h, one-command update
- `bin/check-update.sh` — fast version check via `git ls-remote` (~200ms, runs at SessionStart)
- `bin/update.sh` — fetch latest, replace code files, preserve db/rules/hooks
- Install metadata at `~/.claude/memory-system/.installed.json`

### v2.2.1 (2026-05-22)

- Search recall hardening: phrase → AND-prefix → OR-prefix fallback instead of exact long-phrase only
- Vector fallback visibility: import via file path, warning on unavailable/failed vector search, safer merge behavior
- MCP hygiene: clamp invalid/negative limits, support `type_filter=code`, longer search timeout
- Lint fixes: basename collision handling, Bash `[[...]]` false-positive filtering, installed skill link aliases
- Backup/error-path hardening for `embed.py` and `index_impl.py`
- Docs clarify zero-dependency core vs optional v2 packages

### v2.2.0 (2026-05-21)

- Tree-sitter code parsing (.py/.js/.ts/.sh)
- 16 bug fixes (1 BLOCKER, 6 HIGH)
- 3 consreviews (18 voice reviews total)

### v2.0.0 (2026-05-21)

- Hybrid FTS5 + vector search (fastembed ONNX)
- Recall: 30% -> 100% on semantic queries
- Backup/restore for all reindex operations

### v1.3.0 (2026-05-21)

- Smart token compression: 57 -> 124 rules in same budget
- Keyword clustering, tiered display, adaptive budget

### v1.2.0 (2026-05-21)

- MCP server (5 tools, works with Cursor/Windsurf/Cline)
- GitHub repo, SEO/GEO, awesome-list PRs

### v1.0.0 (2026-05-20)

- FTS5 search, context assembly, signal extraction
- Compounding, evidence tiers, serendipity, phase-adaptive

---

## License

MIT
