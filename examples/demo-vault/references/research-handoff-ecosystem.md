---
type: reference
title: Handoff ecosystem research
aliases: ["research-handoff-ecosystem"]
tags: ["reference"]
---

# Handoff ecosystem research

_Reference:_ Social research on AI-coding handoff ecosystem — tools, techniques, metrics, vector memory. 14 agents, 30+ sources.

## Details

# Handoff Ecosystem Research

## Key findings

### Tool landscape

- **handoff submodule** (Sonovore) — git submodule, UserPromptSubmit injects live-handoff on EVERY message (~200-500 tokens/turn), PreCompact emergency dump. NOT npm.
- **memsearch** (zilliztech, 1.8K stars) — Milvus + markdown-first, BGE-M3 local, `context:fork` for search isolation. **File-lock bug** breaks recall while watch runs. Clean architecture but needs library-only usage, not plugin.
- **claude-mem** (76.7K stars) — SQLite FTS5 + ChromaDB, PostToolUse captures actions, web UI. Heavy (bg worker).
- **ccrider** — Go, SQLite FTS5, preserves sessions beyond 30-day delete, MCP mode for agent access.
- **handoff CLI** — Rust, similar pipeline to a framework (Feature → Spec → Design → Tasks) but provider-agnostic via clipboard. No charter / gates.
- **continues** — 16 AI agents, 240 cross-tool paths, parses native formats.
- **Rudel** — ClickHouse analytics, 1573 sessions analyzed. Skills usage 4% → 61%.
- **Codex CLI** — full transcript replay as resume, `/fork` for branching, encrypted server-side compaction, NO hooks.

### Academic

- Toby Ord half-life: Claude 3.7 Sonnet = 59 min (50% success). 80% success = tasks < 15 min. arXiv:2505.05115.
- Stanford "Lost in the Middle": 30%+ accuracy drop for mid-context info (NeurIPS 2023).
- "Cognition 35 min" NOT confirmed — community telephone game.

### Hooks technical

- 29 hook events, 5 handler types in Claude Code v2.1.141+
- PreCompact CAN block compaction (exit 2)
- Context % NOT in hooks — StatusLine has `used_percentage`
- Hook stdout capped at 10K chars
- Auto-compaction at ~75-80% — handoff threshold must be < 70%

## Approved improvements

| #                              | Item                            | Status                                         | Notes                                        |
| ------------------------------ | ------------------------------- | ---------------------------------------------- | -------------------------------------------- |
| H4                             | cleanupPeriodDays: 99999        | **SHIPPED**                                    | In settings.json                             |
| H1                             | PreCompact structured template  | APPROVED                                       | Add freshness check (state.md < 5min = skip) |
| H3                             | Session metrics hooks           | APPROVED                                       | Include session_id + transcript path         |
| PostCompact file re-read       | APPROVED                        | 3 files × 2K within 10K cap                    |                                              |
| FTS5 memory search             | **Pipelined as a separate run** | Full pipeline brief → charter → spec → plan OK |                                              |
| Vector upgrade (memsearch lib) | DEFERRED                        | Only if FTS5 insufficient                      |                                              |

## AI Memory System project — SHIPPED

**Status:** v1.0 - v1.2 delivered. 421 files indexed, 59 rules auto-injected, MCP server live.

Delivered: FTS5 search + smart-inject + signal extraction + compounding + serendipity + MCP server + session counter + lint + cleanup + rollback.

Reviews: review R1 (9 fixes) + R2 (SHIP-WITH-EDITS) + triple review (18 voices, 4 HIGH fixed).

Promotion: 4 PR to awesome-lists (122K stars), competitive research (40 repos).

Roadmap: v1.3 compress → v2.0 vector → v2.2 code-aware → v3.0 dashboard.

Related: [[voice-system-architecture]], [[claude-code-context-optimization]].

_Confidence: high · Source: my-project_
