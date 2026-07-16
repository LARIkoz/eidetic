# Phase 0 GO: Eidetic LLM Wiki Ingestion

**Status:** granted
**Date:** 2026-07-16
**Phase:** Product and contract freeze
**Authorization type:** documentation only
**Core base commit:** `2e710d1`
**Approved bootstrap bundle digest:**
`1e4d70449e517a532c069053adc31fe2a0e96ecd5580e224c45ceb8aa2106ff5`
**Digest subject:**
`output/session-brief-2026-07-16-eidetic-llm-wiki-ingestion/bundle-manifest.sha256`

## Owner Confirmation

The workspace owner replied `го` after receiving the explicit Phase 0 defaults
and the statement that this GO authorizes documentation only. This record binds
that confirmation to the frozen bundle digest above.

## Accepted Phase 0 Defaults

1. One owner action may approve one immutable document manifest, but Core mints
   a distinct exact, single-use grant for every approved candidate preview.
2. Every candidate keeps its own idempotency key, commit, delivery resolution,
   and receipt. V1 is not an all-or-nothing multi-target book transaction.
3. Logical card identity is source-scoped in the first slice. Cross-book
   canonical merging is excluded.
4. The first writable pilot uses a physically isolated low-trust Core scope.
   Existing project memory, agent memory, and topic-base targets remain outside
   that scope.
5. Book rollback is a set of compensating Core lifecycle operations that append
   evidence. It does not delete cards, receipts, ledgers, or history.
6. A zero-card chunk cannot become terminal automatically. In the first slice,
   only an explicit owner-reviewed decision may mark a successfully parsed
   chunk as containing no durable knowledge; empty or malformed model output is
   repair/failure evidence.
7. Repeatable atomization uses a dedicated provider-neutral task capability,
   `knowledge_atomization`, routed through `shared_api_cache`. No exact provider
   or model is selected by this GO.
8. The product pilot sequence is one chapter, then one bounded non-sensitive
   text/Markdown book. The exact real source and evaluation set require a
   separate Phase 6 run GO; this Phase 0 GO authorizes no source processing.

## Documentation Mutation Allowlist

This GO authorizes edits only to:

- `docs/adr/0004-core-owned-durable-ingestion.md`
- `docs/core-sdk-contract-v1.md`
- `docs/core-sdk-ownership.md`
- `docs/eidetic-llm-wiki-ingestion/phase-0-go.md`
- `docs/eidetic-llm-wiki-ingestion/book-manifest-contract.md`
- `docs/eidetic-llm-wiki-ingestion/writer-inventory.md`
- `docs/eidetic-llm-wiki-ingestion/phase-1-requirements.md`
- `docs/eidetic-llm-wiki-ingestion/phase-0-review.md`

## Explicitly Not Authorized

- Python, shell, schema, worker, SDK, importer, test, hook, or runtime changes;
- LLM/provider calls, task-capability registry mutation, API spend, or key and
  penalty-state access;
- creation or initialization of the importer repository;
- live source acquisition, chunking, atomization, indexing, or ingestion;
- enabling an ingestion scope or implementing `submit_ingest`;
- installed-runtime deployment;
- SDK tagging, release, publication, or remote changes;
- M3 judge, lifecycle hook, YouGile preview, or systemic-work changes; and
- deletion, stashing, resetting, or sweeping of any pre-existing worktree state.

## Pre-existing Worktree Exclusions

At GO time the Core worktree already contained unrelated tracked modifications
in `bin/m3_judge.py` and `hooks/session-signals.sh`, plus untracked M3 judge,
schema, test, evaluation, and planning files. They are owner work outside this
phase and must not be staged or committed with the allowlist above.

## Invalidation Conditions

This GO is invalidated by any proposal that:

- adds executable write authority;
- introduces an atomic multi-target book transaction;
- changes candidate, manifest, grant, idempotency, or rollback semantics beyond
  the accepted defaults;
- selects a provider/model or expands the allowed source data class;
- touches a repository or file outside the allowlist; or
- weakens an ADR 0004 readiness gate.

Any invalidation requires a new reviewed packet and owner GO.
