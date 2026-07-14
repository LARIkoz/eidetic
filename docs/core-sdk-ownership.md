# Eidetic Core / SDK ownership and dependency map

This inventory is the evidence companion to ADR 0003. It describes the local
topology audited on 2026-07-14. Installed paths are deployment facts, not public
request fields.

## Module-level dependency graph

```text
Claude hooks and MCP configs
  -> installed Core CLI / mcp_server.py
      -> Core lifecycle, search, compound, M1/M2/M3, file truth
          -> bin/engine.py
              -> private embed.py + rerank.py

YouGile REST + state DB + FTS/ranking
  -> eidetic_sdk YouGile adapter
      -> EngineClient
          -> Core-owned eidetic_engine_worker.py (persistent JSONL)
              -> bin/engine.py
                  -> Core-resolved derived vector index

Future external connector
  -> eidetic_sdk Connector protocol
      -> future Core-owned ingestion worker
          -> Core validation, policy, locking, atomic file write, receipt
```

No arrow points from Core to the SDK.

## Current public entry points and callers

| Core entry point | Current callers | Classification | Migration posture |
| --- | --- | --- | --- |
| `mcp_server.py` | Global Claude MCP config plus project `.mcp.json` files for topic bases | core | Keep unchanged; not SDK transport |
| `hooks/*.sh` and installed hook commands | Claude settings lifecycle, injection, passive stats, export, incremental index | core | Keep unchanged |
| `bin/search.sh` | Hooks, users, `rag-engine-ab/retrieve_all.py`, handoff helper | core | Stable CLI; optional future SDK client |
| `bin/remember.py` | Users and `eidetic-importer/import.py` | core | Stable Core write CLI; importer migration deferred until ingestion protocol exists |
| `bin/base.py` | Topic-base lifecycle and installed MCP setup | migration-only | Keep in Core during v1; re-evaluate orchestration pieces later |
| `bin/engine.py` API 1.1 | Core modules/tests and legacy machine-local YouGile scripts | core | Keep compatibility; external SDK adoption moves to worker protocol |
| `bin/mlx_embed.py` | Core embedder and `shared-rag/rag/embedder_bgem3.py` private bootstrap | migration-only | shared-rag must migrate separately; no move in this slice |
| `bin/eidetic_engine_worker.py` | `eidetic_sdk` only | shared-contract | New public Core worker; logical IDs only |

## External consumer inventory

| Consumer | Dependency observed | Owner | Action |
| --- | --- | --- | --- |
| Installed YouGile skill | Hard-coded installed `bin` import, direct Engine/Index calls, three MLX re-exec copies | YouGile + SDK | First vertical slice; keep YouGile fetch/chunk/ranking code in the skill |
| `eidetic-importer/import.py` | Invokes installed `remember.py` for durable writes | importer | Preserve; migrate only after ingestion protocol review |
| `rag-engine-ab/retrieve_all.py` | Invokes installed `search.sh` against topic bases | experiment | Preserve as legacy read CLI |
| `shared-rag/rag/embedder_bgem3.py` | Imports private installed `mlx_embed.py` | shared-rag | Record as known private caller; separate migration |
| `claude-setup/skills/handoff/bin/auto-cycle-handoff.sh` | Invokes installed index/search and reads derived counts | handoff skill | Preserve; Core CLI consumer |
| Project `.mcp.json` files | Launch installed or source `mcp_server.py` with topic-base roots | project owners | Preserve; MCP consumer, not SDK |
| Claude settings and global MCP config | Installed hooks, index/export/passive stats, Core MCP | Core deployment | Preserve unchanged |
| `ura/output/.../verify_final.py` | One-off direct installed private import | generated verification artifact | Example/legacy evidence; do not turn into supported API |

The source `claude-setup` YouGile skill did not yet contain the machine-local
semantic layer; the installed skill was the actual reference consumer. The
first extraction therefore promotes that layer into its canonical source while
replacing the hard-coded Core import.

## File ownership matrix

| Files or groups | Owner class | Rationale |
| --- | --- | --- |
| `CLAUDE.md`, `PROJECT_MAP.md`, install/update/rollback, hooks | core | Product/runtime governance and deployment |
| Memory schema docs, file parser/writer, compound, lifecycle, M1/M2/M3, migrations | core | Correctness, policy, durable file truth |
| `bin/embed.py`, `bin/rerank.py`, `bin/engine.py`, vector/search implementation | core | Model, hash, index, drift, and degradation invariants |
| `mcp_server.py`, `bin/base.py` | core / migration-only | Existing Core consumption and topic-base compatibility |
| `schemas/sdk/engine/v1/*` and contract docs | shared-contract, canonical in Core | Cross-repo protocol source of truth |
| `bin/eidetic_engine_worker.py` | shared-contract implementation, owned by Core | Enforces logical-index and storage boundary |
| `eidetic-sdk/src/eidetic_sdk/runtime.py`, transport and typed clients | sdk | Discovery, persistent process, negotiation, errors |
| SDK connector protocol, checkpoints, receipts, fake source, examples | sdk / example | Reusable integration lifecycle without Core policy |
| YouGile REST/state/chunk/ranking/formatting code | external application | Source-specific behavior stays outside both Core and generic SDK |
| YouGile SDK adapter and frozen fixtures | example / external application | First reference adoption and parity proof |
| Writer/Palmyra routes, model roster, penalties, prompts | core plus `shared_api_cache` | Provider policy is not an integration SDK concern |
| Future ingestion worker and receipt ledger | undecided until policy ADR | No executable submit surface in this extraction |

## Data flows

### Existing Core memory

```text
session/hooks or remember CLI
  -> Core validation/compound/policy
  -> atomic Markdown file truth
  -> derived FTS/code/vector/context state
  -> Core MCP/search consumers
```

### YouGile reference slice

```text
YouGile REST cache
  -> yougile_state.sqlite (source-owned; read-only to embedding)
  -> YouGile-owned chunk records
  -> eidetic_sdk EngineClient
  -> logical index id over Core JSONL worker
  -> Core-owned hash/embed/index operations
  -> separate rebuildable YouGile vector index
  -> YouGile vector recall + FTS + optional rerank
```

### Future ingestion

```text
discover -> authorized fetch -> normalize candidate
  -> Core validate -> Core preview -> owner/policy-approved submit
  -> Core atomic write -> Core provenance receipt
  -> SDK checkpoint records receipt only after terminal success
```

## Deployment dependencies

- Source repository: `eidetic`.
- Derived installed runtime: `EIDETIC_MEMORY_SYSTEM` or the default local
  runtime root.
- Installed metadata identifies Core release and source revision.
- The worker is launched by an SDK-selected Python interpreter and keeps one
  Engine process warm. MLX interpreter discovery is centralized in SDK runtime
  selection.
- Consumer index path resolution is Core-owned configuration. It is never
  transmitted through the protocol.
- YouGile state, vector data, checkpoints, and receipts remain separate from
  Core memory and from provider penalty state.

## Compatibility and rollback matrix

| SDK | Engine protocol | Core Engine API tested | Status |
| --- | --- | --- | --- |
| legacy/no SDK | none | 1.0/1.1 direct module | rollback-only |
| 0.1.x | `>=1.0,<2.0` | 1.1 | supported current |
| next SDK minor | `>=1.0,<2.0` unless contract changes | current plus previous supported Core | required before release |

Rollback switches the consumer to the legacy adapter. It does not rename the
Core repository, convert source state, rewrite evidence, or delete derived
indexes.
