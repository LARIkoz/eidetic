🇬🇧 **English** · [🇷🇺 Русский](README.ru.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) [![Version](https://img.shields.io/badge/version-5.13.0-blue.svg)](CHANGELOG.md) [![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md) [![Agents: Claude · Codex · Gemini](https://img.shields.io/badge/agents-Claude%20%C2%B7%20Codex%20%C2%B7%20Gemini-8A63D2.svg)](#works-with-any-agent)

# Eidetic

**Long-term memory for AI coding agents — that knows when its memories go bad.**

> [What is it?](#what-is-it) · [Why](#why-eidetic) · [Install](#install) · [How to use it](#how-to-use-it) · [How it works](#how-it-works) · [Features](#features) · [Compare](#comparison)

---

## What is it?

Eidetic gives an AI coding agent **long-term memory** that lives in plain Markdown files and is searched with hybrid FTS5 + vector search. One engine, **two kinds of memory**:

- **Personal memory (PUSH)** — your own decisions, rules, and project context. **The agent writes its own memory:** at every session end a small LLM pulls the decisions, rules, and lessons out of the transcript and files them as cards (`agent-extracted`, 0.5× weight) — your memory grows from just working. It **auto-injects** into every Claude Code session (recallable on demand from any MCP agent too) and **compounds** — updating existing notes instead of piling up duplicates.
- **Topic bases (PULL)** — an external corpus (API docs, a methodology, a book) you turn into an isolated base and **attach only to the projects that need it**.

What makes it different from every other memory tool: it **detects when memories go stale** and down-ranks them — so more memory doesn't quietly make the agent _worse_ (that's [why](#why-eidetic) it exists). Claude Code-native via zero-config hooks; works with Codex, Gemini, Cursor, Cline, and any MCP agent.

_Lineage: [Luhmann's Zettelkasten](https://en.wikipedia.org/wiki/Zettelkasten), [Tiago Forte's Second Brain](https://www.buildingasecondbrain.com/), and [Karpathy's **LLM Wiki**](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — implemented end-to-end (compounding pages, a typed schema, an op-log, typed pages), with drift detection layered on top. More in [Design philosophy](#design-philosophy)._

---

## Why Eidetic?

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

That's the **Day 60 problem** — after 500+ memory files, stale knowledge actively hurts the agent. More memory = worse performance. No existing tool detects this. **Eidetic solves both.**

---

## Install

```bash
git clone https://github.com/LARIkoz/eidetic.git && cd eidetic && bash install.sh
```

One command. `install.sh` asks 3 quick questions (Enter = sensible default) and wires the Claude Code hooks. The **core** (FTS5 search, auto-injection, drift, vault) needs **zero pip installs** and works immediately; semantic search adds one — `pip install fastembed` (see [Dependencies](#dependencies)). Piped / CI / agent installs stay non-interactive via env or the defaults.

**Prefer your agent install it?** Hand it the repo link + **[AGENTS.md](AGENTS.md)** (or a ready prompt in **[docs/prompts.md](docs/prompts.md)**) — it runs the whole thing end-to-end. Verify any time with `bash ~/.claude/memory-system/bin/doctor.sh`; roll back with `bash ~/.claude/memory-system/bin/rollback.sh`.

---

## How to use it

Eidetic has **two kinds of memory**, used differently.

### Personal memory (PUSH) — your own knowledge

On Claude Code it's **automatic**: the installer wires hooks, so your accumulated rules / decisions / context **auto-inject** at the start of every session and new signals are **captured** at the end. You mostly just work. To act on it explicitly:

- **Recall** — `/memory-recall "<query>"` (or the MCP `memory_search` tool from any agent).
- **File an answer back** — `echo "<answer>" | python3 ~/.claude/memory-system/bin/remember.py "<title>"` promotes a good synthesized answer into a typed page (re-promoting the same topic appends an `## Update`, never duplicates).
- **Browse** — `eidetic export-vault` turns your memory into a browsable Obsidian wiki.

### Topic bases (PULL) — an external corpus you attach per project

A **topic base** is an isolated corpus (API docs, a methodology, a book) you query on demand and attach only where needed — never polluting your personal recall. Same engine, separate index, its own git repo.

```bash
python3 ~/.claude/memory-system/bin/base.py init   acme                  # → ~/eidetic-bases/acme-base/
python3 ~/.claude/memory-system/bin/base.py index  acme                  # build FTS + e5 vectors
python3 ~/.claude/memory-system/bin/base.py attach acme --scope project  # attach to a project over MCP
```

- **Full guide + agent contract:** **[docs/topic-bases.md](docs/topic-bases.md)** — storage model, ingest scenarios, **routing (how the agent knows when to reach for a base)**, API-doc gotchas.
- The bundled **`/eidetic-base`** skill builds one for you from a source (scrape → pages → index → verify → attach).
- A base is a **separate repo** outside the eidetic tree, so your corpora stay private even though eidetic is public.

### Copy-paste agent prompts

Don't memorize commands — hand your agent a ready prompt from **[docs/prompts.md](docs/prompts.md)**: _install Eidetic_ · _build a topic base from a site / book / API_ · _attach a base to a project_.

---

## What it does (at a glance)

| Problem                                               | How Eidetic solves it                                                                                                |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| MEMORY.md caps at 200 lines (only a fraction visible) | Smart compression: **all 160 rules** in same token budget                                                            |
| Keyword search only                                   | **Hybrid FTS5 + vector** search (e5-large, ~100 languages)                                                           |
| Forgets between sessions                              | **Auto-extracts** decisions, failures, patterns at session end                                                       |
| Knowledge piles up as duplicate files                 | **Compounds** — updates existing memories instead of creating new ones                                               |
| Stale memories poison the agent                       | **Drift detection** — flags broken wikilinks, age staleness, confidence escalation                                   |
| Agent-created memories reinforce hallucinations       | **Self-referential discount** — agent-extracted = 0.5x weight                                                        |
| All memories treated equally                          | **Evidence tiers** — validated > observed > hypothesis                                                               |
| Can't search code                                     | **Tree-sitter** parses functions/classes into searchable chunks                                                      |
| Good answers die in the chat log                      | **Promote** — file a synthesized answer back as one typed page (Karpathy's wiki)                                     |
| Vector search fails silently (the 16-day outage)      | **Loud self-heal** — a failed embed surfaces a warning + logs, never goes dark                                       |
| Non-English queries miss English-written memories     | **Cross-lingual translation** — translate the query → dual-query → min-rank fuse (opt-in; 5/8→7/8 recall@3)          |
| No idea which memories actually get used              | **Usage telemetry** — logs which cards surface; flags **dead cards** to prune                                        |
| "Healthy" hides a silently-broken embedder/translator | **Functional doctor** — a canary embeds→searches→asserts rank, and verifies the translator + usage logger really run |

---

## How it works

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
                        |    non-English query? -> translate to English (opt-in: Apple NMT / Opus-MT) -> dual-query
                        |    FTS5 + e5 vector (forced for non-English) + cross-encoder -> RRF merge (min-rank)
                        |    Calibrated confidence (two-signal gate) + drift warnings
                        |    └─ confident hits logged -> usage stats (top cards · dead cards to prune)
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

### Compound ranking

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

## Features

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

At session end a small LLM pulls `Decision:/Rule:/Worked:/Failed:/Knowledge:` signals from the transcript and compounds them (`source: agent-extracted`, 0.5× weight). It runs on **whichever CLI you have** — Claude or Codex — degrading `claude-batch → claude --print → codex-batch → codex exec`, so any install with the `claude` _or_ `codex` CLI captures signals. Pick the capture model to match your CLI:

| Your CLI   | Capture model (default → cheaper/safer)               | Override env                                      |
| ---------- | ----------------------------------------------------- | ------------------------------------------------- |
| **Claude** | `sonnet` → `haiku`                                    | `EIDETIC_SIGNAL_CLAUDE_MODEL`                     |
| **Codex**  | `gpt-5.5` → `gpt-5.3-codex-spark` (subscription-safe) | `EIDETIC_SIGNAL_CODEX_MODEL` / `_CODEX_CLI_MODEL` |

Gemini / Grok agents recall via MCP but don't run extraction yet. For deliberate, full-quality capture, use **Promote** (above).

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

## Works with any agent

Eidetic is **agent-agnostic at the memory layer** — one `.md` store + FTS5 + e5 vector index; only the _integration surface_ changes per agent:

- **Claude Code** — zero-config. `install.sh` wires SessionStart/Stop hooks, so memory **auto-injects** at the start of every session and signals **auto-capture** at the end. The deepest, hands-off experience.
- **Codex · Gemini · Cursor · Cline — any MCP agent** — point the agent at the bundled **MCP server** for on-demand recall + write-back (session-end signal capture also runs on the `codex` CLI, not just `claude`). Add it to your agent's MCP config:

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

7 tools: `memory_search`, `memory_search_detail`, `memory_serendipity`, `memory_health`, `memory_reindex`, `memory_lint`, `export_vault`. Claude Code doesn't need this — it uses the zero-config hook path above.

No lock-in to one agent. (Auto-injection is the Claude Code hook path; other agents recall on demand through MCP.)

---

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

---

## Performance

| Metric               | Value                                                   |
| -------------------- | ------------------------------------------------------- |
| Background / idle    | **0 CPU** — event-driven, no daemon                     |
| Session start (warm) | **~350ms**                                              |
| FTS5 search          | **~50ms**                                               |
| Core dependencies    | **zero** — no Docker, npm, or pip (e5 vectors optional) |

<sub>Cold start ~15s (one-time e5 load) · vector query ~32ms · FTS reindex ~0.3s · full embed ~1h one-time, incremental in seconds · index ~66MB · signal extraction = 1 LLM call/session.</sub>

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

_Not your use-case? The [40-repo competitive analysis](https://github.com/LARIkoz/eidetic/releases/tag/v2.2.0) covers the alternatives (claude-mem, engram, memsearch, …)._

---

## Design philosophy

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

**Shipped** — v1.0 → v5.6: FTS5 + signals + compounding, hybrid e5 search, code search, drift detection, promote / op-log / typed pages, cross-lingual translation, usage telemetry, functional doctor. Full per-version detail in [CHANGELOG.md](CHANGELOG.md).

**Next** — distribution: pip package, docs polish.

**Planned — v6 (truth-maintenance)** — supersession + contradiction detection as a typed-edge graph: memory that resolves its own contradictions and doesn't rot. Plus session-transcript search.

Full version history: [CHANGELOG.md](CHANGELOG.md)

---

## License

MIT
