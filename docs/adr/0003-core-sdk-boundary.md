# ADR 0003: Separate Eidetic Core from the integration SDK

- Status: Accepted
- Date: 2026-07-14
- Owners: Eidetic Core maintainers
- Decision scope: repository boundary, public integration contract, first extraction

## Context

The existing `eidetic` repository contains the local memory product, its
correctness-critical lifecycle, and an Engine API used by in-repository code.
The first external Engine consumer, YouGile semantic search, currently imports
`engine.py` from the installed runtime by modifying `sys.path`. It also repeats
MLX interpreter discovery in three scripts. Other external callers use the
installed MCP server or Core command-line tools.

The current Engine API is intentionally low level. Its `open_index(path)` and
`Index` methods expose a filesystem location and vector-store primitives. That
surface remains useful inside Core and for legacy callers, but it cannot become
the Core-to-SDK contract: the SDK boundary must not expose Core paths, SQLite
layout, private modules, or write authority.

An orchestration run was attempted before this decision. It completed only
three of nine voices and did not reach quorum. The raw outputs agreed that MCP
should remain an agent-facing surface and ingestion writes must remain in Core,
but disagreed on the Engine transport. The repository abort conditions resolve
that disagreement: any path-based `Index` contract is rejected.

## Decision

1. The existing `eidetic` repository is documented as **Eidetic Core** and is
   not renamed.
2. A separate local repository, `eidetic-sdk`, owns reusable connector and
   application integration machinery. Core never imports it.
3. The first SDK contract is `eidetic.engine` protocol `1.0`, transported as
   newline-delimited JSON over a long-lived local subprocess. The worker is
   shipped and owned by Core; the process lifecycle and typed client are owned
   by the SDK.
4. Requests identify derived indexes by a validated logical `index_id`. Public
   requests and responses never contain a Core root, database path, table name,
   raw embedding, internal numeric row identifier, or lock handle. Core alone
   resolves logical identities to storage.
5. The Engine and ingestion contracts are separate typed surfaces even if they
   later share framing and discovery. Engine v1 supports capabilities, health,
   read-only reconciliation, incremental sync, vector search, and reranking.
6. The future `eidetic.ingestion` protocol is specified now but is not exposed
   as an executable write facade in this extraction. Its eventual Core-owned
   lifecycle is capabilities, health, validate, preview, submit with an
   idempotency key, query/retrieve, and provenance receipt. Until that policy
   review is complete, submission fails closed because no submission command
   exists.
7. MCP remains a Core consumption interface for agents and topic bases. It is
   not the SDK transport in v1.
8. `bin/engine.py` remains public Engine API `1.1` for existing Core and legacy
   callers. The new worker may use it internally. SDK consumers do not import
   `engine.py`, any other `bin` module, or `Index` directly.
9. Palmyra, Writer, exact model identifiers, judge prompts, provider routing,
   credentials, penalty state, M3 policy, schemas, and migrations stay outside
   `eidetic-sdk`. Provider execution remains behind Core and
   `shared_api_cache` policy.

## Ownership and dependency direction

```text
external source or application
        |
        v
   eidetic-sdk
        |
        | eidetic.engine v1 JSONL / future eidetic.ingestion v1 JSONL
        v
   eidetic Core worker
        |
        v
Core-owned engine, policy, storage, lifecycle, and receipts
```

The Core repository contains no import or runtime reference to `eidetic_sdk`.
The SDK contains no Core implementation module and no copy of Core schemas.
Machine-readable schemas are canonical in Core and are consumed by cross-repo
tests from a selected Core checkout or installed contract manifest.

## Compatibility

- Engine protocol versions use `MAJOR.MINOR`.
- SDK `0.1.x` supports Engine protocol `>=1.0,<2.0` and currently tested Core
  Engine API `1.1`.
- A protocol major mismatch is rejected before an index is opened.
- Minor additions are backward compatible. Removal, semantic change, or a new
  required field requires a major bump.
- The current and immediately previous supported SDK/Core pair must pass the
  same canonical contract fixtures before default adoption.

## Data and write authority

- YouGile owns REST access, source state, chunking, FTS, hybrid ranking, and
  result formatting.
- Core owns content hashing, embedding, index schema, build locking, drift
  stamps, vector search semantics, and storage resolution.
- The SDK owns runtime discovery, process reuse, compatibility checks, error
  mapping, connector checkpoints, and freshness receipt handling.
- Engine sync may update only a separately rebuildable derived index selected
  by logical identity. It never writes YouGile state or Core memory.
- Future ingestion submit is Core-only and fail-closed. The SDK can never make
  an admissibility decision or directly write a memory file or Core index.

## Rollout and rollback

1. Freeze docs, schemas, and worker contract in Core.
2. Create the local SDK repository and deterministic fixtures.
3. Run the YouGile adapter against read-only source state and a separate derived
   index; preserve the legacy adapter behind an explicit rollback switch.
4. Adopt the SDK path only after ranking, degradation, freshness, and
   reconciliation tests pass.
5. Remove legacy imports only after all known callers have a migration status.

Rollback restores the legacy YouGile adapter and removes the SDK package from
the consumer environment. Core memory, source state, evidence, and existing
indexes require no conversion and are not deleted.

## Consequences

The subprocess boundary adds framing and lifecycle code, but it removes Python
module/path coupling and makes storage authority enforceable. A persistent
worker preserves warm model reuse. Engine sync payloads are larger than direct
SQLite access; batching can be added additively without weakening the boundary.

External ingestion remains deliberately incomplete. This is safer than
shipping a stub that appears to authorize writes before idempotency, policy,
locking, provenance, and audit behavior are reviewed together.
