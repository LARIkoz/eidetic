
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-5.3.1-blue.svg)](CHANGELOG.md)
[![Claude Code](https://img.shields.io/badge/Claude_Code-hooks%20%2B%20skills%20%2B%20rules-purple.svg)](#how-it-works)



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

One command. The **core** (FTS search, injection, drift, vault export) needs **zero pip installs** and works immediately; semantic / cross-lingual search adds one optional dependency — see [Dependencies](#dependencies).

---

## What It Does

| Problem                                               | How Eidetic solves it                                                              |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------- |
| MEMORY.md caps at 200 lines (only a fraction visible) | Smart compression: **all 160 rules** in same token budget                          |
| Keyword search only                                   | **Hybrid FTS5 + vector** search (e5-large, ~100 languages)                         |
| Forgets between sessions                              | **Auto-extracts** decisions, failures, patterns at session end                     |
| Knowledge piles up as duplicate files                 | **Compounds** — updates existing memories instead of creating new ones             |
| Stale memories poison the agent                       | **Drift detection** — flags broken wikilinks, age staleness, confidence escalation |
| Agent-created memories reinforce hallucinations       | **Self-referential discount** — agent-extracted = 0.5x weight                      |
| All memories treated equally                          | **Evidence tiers** — validated > observed > hypothesis                             |
| Can't search code                                     | **Tree-sitter** parses functions/classes into searchable chunks                    |
| Good answers die in the chat log                      | **Promote** — file a synthesized answer back as one typed page (Karpathy's wiki)   |
| Vector search fails silently (the 16-day outage)      | **Loud self-heal** — a failed embed surfaces a warning + logs, never goes dark     |

---

## How It Works

```
                SESSION START (~350ms warm)
                        |
                Reindex (FTS5) + Code Index (tree-sitter) + Vector Embed
                        |   └─ W5 loud self-heal: a failed embed warns + logs (embed-last.log),
                        |      never silently goes dark; high vector lag is flagged
                Assemble Context (~200 rules + project + recent + drift diagnostics)
                        |
                Write to ~/.claude/rules/ (auto-loaded, no size cap)
                        |
                Agent starts KNOWING everything


                     MID-SESSION
                        |
                READ   /memory-recall "query"  or  MCP memory_search
                        |    FTS5 + e5 vector (forced for non-English) + cross-encoder -> RRF merge
                        |    Calibrated confidence (two-signal gate) + drift warnings
                        |
                WRITE  remember.py promote "<title>"   (file an answer back as a typed page)
                             └─ search-before-write: a re-promote appends ## Update, never duplicates


                     SESSION END (~5s, async)
                        |
                Extract signals (Sonnet via claude-batch or `claude --print`; Codex fallback)
                        |
                Compound: update existing memory OR create new
                        |
                Tag: agent-extracted = 0.5x   ·   record the op on log.md (greppable timeline)
```

<img width="1074" height="1082" alt="image" src="https://github.com/user-attachments/assets/24e70c71-55a8-4d64-a819-050e9107120e" />

### Compound Ranking


Every result is ranked by:

```
score = relevance x evidence x source x freshness x status

evidence:   validated = 1.0    observed = 0.7    hypothesis = 0.4
source:     user-explicit = 1.0  agent-extracted = 0.5  system = 0.3  imported = 0.3
freshness:  < 30 days = 1.0    > 30 days = 0.5    (a drift finding overrides: stale 0.5x, broken link 0.8x)
status:     current = 1.0      resolved/fixed = 0.75   superseded/deprecated = 0.35   archived = 0.25
```

Keyword hits also carry a match-quality factor. A validated, recent, current, human-created memory always outranks an old, unverified, agent-extracted, or superseded guess.

---

## Install

```bash
git clone https://github.com/LARIkoz/eidetic.git
cd eidetic
bash install.sh
```

See [Dependencies](#dependencies) for what each search tier needs. Rollback: `bash ~/.claude/memory-system/bin/rollback.sh`

## Dependencies

Eidetic is **tiered** — the core needs nothing extra; the headline semantic search needs one pip install.

| Tier                                                                              | Requires                                                                                                      | Without it                                                |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| **Core** — FTS5 search, auto-injection, drift, compounding, Obsidian vault export | `bash` + `python3` + `sqlite3` (preinstalled on macOS/Linux)                                                  | — works fully                                             |
| **Semantic / cross-lingual search** (the e5 hybrid layer)                         | `pip install fastembed` + ~2.2 GB e5-large ONNX model (auto-downloads to `~/.cache/fastembed` on first index) | falls back to FTS keyword-only                            |
| **Cross-encoder rerank salvage**                                                  | `fastembed` (pulls a small reranker, lazily)                                                                  | cross-lingual matches that share no words stay suppressed |
| **Code search**                                                                   | `pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-bash`                          | code functions/classes not indexed                        |

> ⚠️ `install.sh` installs the **core only** — it does **not** pip-install fastembed / tree-sitter. A fresh install is FTS-only until you `pip install fastembed`. The e5 model cache must be **persistent** (`~/.cache/fastembed`); a temp-dir cache gets purged by the OS and silently disables vector search.

**Run the doctor any time** to see which tiers are active and what's missing — including _why_ the wiki/vault isn't being created:

```bash
bash ~/.claude/memory-system/bin/doctor.sh
```

It checks deps, index, memory files on disk, vectors + lag, model-cache location, hooks, the wiki/vault, **the op-log + promote/compound deployment state, card-kind distribution, and a failed-embed log (W5)** — with a fix hint for every ⚠️/❌.

Platform: macOS / Linux (uses `fcntl` file locks).

### Updates

Background update check every 6 hours. When available:

```
Eidetic update available (a1b2c3d). Run: bash ~/.claude/memory-system/bin/update.sh
```

Updates preserve databases, rules, and hooks — only code files are replaced.

### MCP Server

**Secondary / optional.** Claude Code itself doesn't need MCP — it uses the hooks + rules + recall-skill path above. The MCP server is for _other_ editors that lack Claude Code's hook system (Cursor, Windsurf, Cline, any MCP-compatible agent), exposing the same memory store as on-demand tools:

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

160 feedback rules in ~6,800 tokens. Previously only ~57 fit in MEMORY.md. Keyword clustering groups related rules; tiered display shows important rules in full, low-priority as name only.

### Hybrid Search (v2.0, v5.1)

FTS5 for keywords (~50ms). Vector search (multilingual-e5-large, 1024-dim) as fallback for semantic queries. Cross-language by design — a query in one language finds notes written in another (e5 covers ~100 languages). v5.1 replaced the old MiniLM-384 embedder with e5-large: cross-lingual paraphrase recall@3 went **25% → 67%** (measured on RU→EN). Results merged via Reciprocal Rank Fusion. If every candidate is weak, reports `No confident results` instead of surfacing noise.

**Two-signal confidence gate (v5.1).** e5 compresses scores, so a true cross-lingual match (~0.83 cosine) is indistinguishable from topical noise (~0.83) by cosine alone. A vector-only hit reaches actionable confidence only with lexical corroboration (shared query anchors) — high recall, no false confidence. A model/dim stamp on the vector store also guards against silent embedder drift.

Progressive search keeps broad queries compact. Use `--detail <id>` to fetch full content when a candidate looks relevant.

### Code-Aware Search (v2.2)

Tree-sitter parses `.py`, `.js`, `.ts`, `.tsx`, `.sh` — every function and class becomes searchable by name or purpose.

### Knowledge Compounding

Before creating a new memory, searches for existing ones on the same topic. Found? Updates it, adds history. Not found? Creates new file. 50 sessions = 50 refined rules, not 500 duplicate files.

### File Answers Back — Promote (v5.3)

A good synthesized answer shouldn't die in the chat log. **Promote** files it back as one **typed page** — `echo "<answer>" | python3 ~/.claude/memory-system/bin/remember.py "<title>"`. Search-before-write means a re-promote on the same topic appends a dated `## Update` section instead of duplicating, and a new page gets `## Related` wikilinks to its neighbours. It's the deliberate, mid-session companion to the end-of-session signal capture, and it's the same write-path a future importer reuses. Every write also lands on a greppable **op-log** — `grep '^## \[' log.md` is the whole timeline.

This is Eidetic implementing [Karpathy's **LLM Wiki**](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) end-to-end: compounding pages, an explicit **maintenance contract** ([docs/MEMORY-SCHEMA.md](docs/MEMORY-SCHEMA.md)), typed pages (`synthesis` / `concept` / `entity`), and an op-log — with auto-extraction and drift detection layered on top, so the wiki maintains itself instead of rotting.

### Session-End Auto-Capture (configurable)

At session end a small LLM pulls `Decision:/Rule:/Worked:/Failed:/Knowledge:` signals from the transcript and compounds them (`source: agent-extracted`, 0.5× weight). The model defaults to **Sonnet** for quality, and the runner degrades across whatever CLI is installed — `claude-batch → claude --print → codex-batch → codex exec` — so **any install with the `claude` _or_ `codex` CLI captures signals**, not just one setup.

Tune via env: `EIDETIC_SIGNAL_CLAUDE_MODEL` (default `sonnet`; set `=haiku` to economize) and `EIDETIC_SIGNAL_CODEX_CLI_MODEL` (otherwise Codex's own default model). For deliberate, full-quality capture, use **Promote** (above).

### Obsidian Vault Export (v4.0)

Eidetic turns your memory into a browsable **Obsidian wiki** — a `HOME.md` hub, folders by type (rules / projects / references), resolved `[[wikilinks]]`, and an auto-generated map of content.

> **The vault is a read-only window, not the engine.** Search, injection, compounding, and drift detection run on your `.md` files + the FTS5 / vector indexes — _nothing ever reads the vault back_. Delete it and Eidetic works identically; it regenerates on the next export. The vault is for **you** to browse what the agent knows; the agent never runs on it.

**Where it lives:** `~/Documents/eidetic-vault/` by default (pass a path to override). The installer wires a Stop hook + nightly refresh, so the vault stays current automatically — or rebuild it any time:

```bash
eidetic export-vault                 # → ~/Documents/eidetic-vault/  (default)
eidetic export-vault ~/my-vault/     # custom location
```

**Open it in Obsidian:** _Open folder as vault_ and point at that directory (it also auto-registers on first export). `eidetic doctor` prints the vault's location and page count.

A quality gate filters your memory files down to a validated subset; optional `--polish` rewrites cards into human-readable prose via an LLM.

---

## Performance

| Metric                   | Value                                                                                         |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| Session start (warm)     | **~350ms**                                                                                    |
| Session start (cold)     | ~15s (e5-large ONNX load)                                                                     |
| FTS reindex (1083 files) | ~0.3s                                                                                         |
| Full vector embed (7.8K) | ~1h one-time (e5 CPU); incremental = seconds                                                  |
| FTS5 search              | ~50ms                                                                                         |
| Vector query (e5)        | ~32ms                                                                                         |
| Signal extraction        | 1 Sonnet call/session (Claude subscription; `EIDETIC_SIGNAL_CLAUDE_MODEL=haiku` to economize) |
| Index size               | 31MB (FTS5) + 35MB (vectors, 1024-dim)                                                        |
| External dependencies    | **zero for core** (e5 model optional)                                                         |

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
| Want largest community, web UI, multi-agent             | claude-mem (large community project)                                           |
| Need shared memory across Cursor + Claude + Copilot     | [engram](https://github.com/Gentleman-Programming/engram)                      |
| Already using Obsidian, want simple integration         | [lucasrosati's setup](https://github.com/lucasrosati/claude-code-memory-setup) |
| Need heavy multilingual semantic search                 | [memsearch](https://github.com/zilliztech/memsearch)                           |

---

## Design Philosophy

Inspired by [Luhmann's Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten), [Tiago Forte's Second Brain](https://www.buildingasecondbrain.com/), and Karpathy's wiki-for-LLMs idea — both his [AI wiki concept](https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285) and the [**LLM Wiki**](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern Eidetic now implements end-to-end (compounding pages, an explicit schema, an op-log, typed pages). Per Karpathy, _"humans abandon wikis because maintenance grows faster than value; LLMs don't get bored"_ — so Eidetic's value curve climbs with use instead of decaying.

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
- **Protected-type guard** — a promotion never appends agent-authored content into a user-validated feedback/profile card, and never clobbers it
- **Loud self-heal (W5)** — a failed session-start embed lands in `embed-last.log` + surfaces a one-line warning (no more silent vector outages); high vector lag is flagged too
- **Graceful degradation** — missing index falls back to `head -200 MEMORY.md`
- **Rollback** — one command, <5 seconds

---

## Roadmap

**Shipped**

- **v5.3.1** — `imported` low-trust source tier (`0.3`, readies the Wave-1 importer) · `EIDETIC_SIGNAL_SKIP_CLAUDE` kickout-safe extraction route
- **v5.3** — **promote** (file answers back as typed pages — Karpathy's LLM Wiki) · greppable **op-log** · typed `card_kind` (synthesis/concept/entity) · **loud embed self-heal** (no silent outages) · explicit [memory schema](docs/MEMORY-SCHEMA.md) · `doctor` covers it all
- **v5.2** — cross-encoder rerank salvage · persistent model cache · embed/export concurrency locks · fenced-code-safe vault export · `doctor` self-check
- **v5.1** — e5-large embedder + two-signal precision gate + model-drift guard (cross-lingual recall@3 25% → 67%)
- **v5.0** — progressive search (+ v5.0.1 lifecycle Phase B)
- **v4.0–4.3** — Obsidian vault export + LLM polish · lifecycle signals
- **v2.x** — hybrid search (v2.0) · code search via tree-sitter (v2.2) · drift detection (v2.5)
- **v1.x** — FTS5 + signals + compounding (v1.0) · token compression 2.17× (v1.3)

**Next** — distribution: pip package, docs polish.

**Planned — v6 (truth-maintenance)** — supersession + contradiction detection as a typed-edge graph: memory that resolves its own contradictions and doesn't rot. Plus session-transcript search.

Full version history: [CHANGELOG.md](CHANGELOG.md)

---

## License

MIT
