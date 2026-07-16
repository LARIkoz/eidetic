# Book Manifest Contract For Eidetic Ingestion

**Status:** Phase 0 decision baseline
**Date:** 2026-07-16
**Implementation:** absent; Phase 1 remains write-disabled
**Binding authority:** ADR 0004 remains authoritative for candidate write,
receipt, and recovery semantics

## Purpose

A book or comparable document can produce hundreds of related candidate cards.
ADR 0004 intentionally defines safe durability around one candidate and one
target. This companion contract groups document review and audit without
turning the group into an atomic multi-target transaction or reusable write
authority.

The first product slice produces two separate layers:

1. a complete, rebuildable source-corpus logical index; and
2. source-scoped, durable atomic cards accepted individually by Core.

Source-index success never implies durable-card acceptance.

## Ownership

The importer owns source acquisition, immutable source retention, parsing,
chunking, provider-neutral atomization, grounding, consolidation, manifest
construction, and source-specific checkpoints.

The SDK owns typed protocol transport, compatibility, preview orchestration,
private pending/receipt/checkpoint state, and aggregate import-session
reconciliation.

Core owns canonical schemas and digests, principal/scope authentication,
validation, visible-effect preview, approval records, exact candidate grants,
policy, target resolution, durable mutation, receipts, recovery, conflicts, and
lifecycle actions.

`shared_api_cache` owns atomization task routing, provider execution, response
cache, key/account lanes, and penalty evidence. Provider/model routes are never
part of the Core protocol.

## Source And Derivation Identity

The importer must distinguish:

- `document_key`: stable source-scoped identity across revisions;
- `document_revision`: identity of immutable normalized source content;
- `source_digest`: digest of canonical source bytes;
- `pipeline_revision`: provider-neutral digest of parser, normalizer, chunker,
  candidate schema, prompt policy, support policy, and consolidation policy;
- `atomization_run_id`: one derivation attempt, not semantic identity;
- `manifest_digest`: Core-canonical digest of the complete proposed import; and
- `logical_card_id`: source-scoped identity of one proposed knowledge unit.

The first slice does not merge logical identities across different documents.
Title, slug, model wording, semantic similarity, or a claim hash alone cannot
establish logical identity.

An exact retry uses the frozen source snapshot, chunk ledger, candidate bytes,
and manifest. It does not call the LLM again. Re-atomizing the same source
creates a new run and manifest, then requires an owner-visible diff and new
approval.

## Immutable Source Retention

Durable cards cannot depend on a rebuildable vector index for provenance. The
importer therefore retains a private content-addressed source snapshot and
resolvable source locators for the lifetime required by the accepted cards or
an explicitly reviewed retention policy.

Every proposed factual claim references one or more source locators containing
document revision, chapter/section, page when available, canonical offsets,
span digest, and exact span text available in the owner preview.

If the retained source later becomes unavailable, Core records a provenance
degradation/lifecycle event. A surviving vector record cannot conceal the loss.

Source snapshots and raw provider responses remain outside Core receipts and
the SDK pending journal.

## Chunk Accounting

Every normalized source range belongs to one deterministic chunk record. Each
chunk reaches exactly one explicit processing class:

- candidates produced and accounted for;
- repair required;
- failed; or
- owner-reviewed no durable knowledge.

The first slice has no automatic `TERMINAL_NO_KNOWLEDGE` transition. An empty,
malformed, schema-invalid, or truncated atomizer response is repair/failure
evidence. Only the owner may mark a successfully parsed chunk as containing no
durable knowledge, with a recorded reason in the manifest.

## Candidate And Manifest Completeness

Each candidate uses ADR 0004's normalized provider-neutral envelope plus:

- document and manifest identity;
- source-scoped logical card identity;
- normalized claims;
- claim-level source-support references;
- pipeline policy identity; and
- proposed lineage action, if any.

The manifest binds at least:

- source/document/pipeline identity and digests;
- complete chunk-ledger digest;
- complete candidate set and candidate digests;
- candidate source locations and logical identities;
- proposed create/update/supersede/archive/alternate actions;
- repair, failure, unresolved, and owner-reviewed no-knowledge rows;
- requested logical Core scope;
- expected principal, policy identity, and target-state bindings; and
- a completeness assertion.

A missing chunk outcome, unresolved candidate identity, invalid claim support,
or candidate not included in the manifest makes the manifest invalid for
approval.

Any policy-relevant change creates a new manifest digest and invalidates the
old preview and approval. This includes a changed source, candidate, claim,
support reference, identity, lineage action, scope, policy binding, or candidate
set.

## Validate And Preview

The future `eidetic.ingestion` surface adds read-only document operations:

- `validate_manifest`; and
- `preview_manifest`.

They complement candidate-level `validate_candidate` and `preview_ingest`.
They cannot enable a scope, mint write authority, mutate a target, consume a
grant, or advance a checkpoint.

The manifest preview contains aggregate create/update/lifecycle/conflict counts
and candidate-level visible effects. Summary-only approval is insufficient;
the owner must be able to inspect every candidate, claim, source span, target
identity, and lifecycle action.

## One Approval Action, Exact Candidate Grants

One Core-owned local owner action may approve the exact immutable manifest.
Core then creates one distinct grant record for each approved candidate
preview. Each grant remains bound to:

- authenticated connector principal;
- logical scope;
- manifest digest;
- candidate digest and candidate preview token;
- expected source-object lineage and target state;
- policy identity/version;
- expiry; and
- a unique candidate-specific nonce.

No manifest-wide opaque token can authorize arbitrary candidates. A candidate
cannot borrow another candidate's grant. Adding, removing, or substituting a
candidate requires a new manifest preview and owner approval.

Core durably retains the manifest approval record and each unconsumed exact
grant state. The SDK persists only non-authorizing approval/grant references in
its private journal, never raw grant material. While the approval remains valid,
the same authenticated principal may obtain the exact unconsumed candidate
grant from the Core-owned approval surface. Expiry before durable `INTENT`
requires a new preview and approval. Once `INTENT` exists, receipt recovery with
the original idempotency key governs the outcome; no replacement grant or new
key is allowed.

The Phase 1 schema packet must define the wire representation and retrieval
operation without weakening these invariants.

## Candidate-Level Commit And Partial Sessions

Every card keeps ADR 0004's independent:

- `PENDING` record and idempotency key;
- exact grant consumption;
- target lock and atomic replace;
- delivery resolution and receipt;
- conflict/rejection handling; and
- receipt-first checkpoint rule.

The document manifest is not the commit point. V1 does not promise
all-or-nothing mutation across card targets.

An import session derives aggregate state only from candidate receipt states.
Partial acceptance, rejection, conflict, or interruption is explicit and
resumable. `COMPLETE` means every candidate has an explicitly reconciled
terminal outcome; it does not mean every candidate was accepted.

A true atomic multi-target book transaction would require a separate ADR,
ordered multi-target prepared-commit protocol, and independent recovery proof.

## Lineage And Re-atomization

The same source-object claim and revision cannot silently bind to a different
candidate digest. Exact manifest replay returns existing outcomes. A changed
atomization creates a new derivation/manifest revision and an explicit diff.

The importer may propose retain, create, update, split, merge, supersede,
archive, or alternate actions. Core validates each action against source and
target lineage. A candidate missing from a new manifest is not implicitly
deleted or archived.

The first slice keeps imported cards in an isolated source-scoped namespace.
Cross-document canonicalization and automatic compounding into protected or
existing project cards are explicit non-goals.

## Compensating Rollback

Book rollback is not transaction reversal. It is an owner-reviewed set of
Core-owned compensating lifecycle candidates, each with its own preview, exact
grant, idempotency key, durable mutation, and receipt.

Rollback archives, supersedes, or otherwise deactivates the accepted imported
card according to the approved Core lifecycle policy. It never deletes or
rewrites the original card receipt, ledger, manifest, or source evidence.

If a target changed after import, rollback fails closed as a target/lineage
conflict and requires owner resolution. A book rollback cannot overwrite later
independent knowledge changes.

## Isolated Pilot Scope

The first writable scope is a new Core-private imported-book target class that
is physically outside:

- `~/.claude/projects/*/memory`;
- `~/.claude/agent-memory`;
- topic-base roots writable through `base.py` or MCP `<base>_add`; and
- any path accepted by current `remember.py`, `compound.py`, M1/M2/M3, curation,
  or legacy importer writers.

The public protocol exposes only a logical scope/target identity. Core resolves
the private location. The exact runtime path is an implementation/configuration
detail and must not enter requests, responses, logs, or SDK state.

Only the future `DurableWriteCoordinator` may mutate this target class. Core may
index it downstream for search, but derived indexing does not gain write
authority over durable files.

## Atomization Capability

Repeatable importer execution uses the provider-neutral task capability
`knowledge_atomization`. Before implementation or a live call, its owning
model-selection registry entry must define:

- structured candidate/claim/support output;
- task-local grounding and zero-output gates;
- admitted route/runtime contract;
- response-shape and truncation policy;
- volume and worker constraints; and
- loud failure/fallback behavior.

No Core or `eidetic-sdk` release depends on an exact atomization provider/model.

## Pilot Sequence

CI and early integration use synthetic, non-sensitive, offline fixtures. A live
pilot later requires a separate run GO bound to:

1. one chapter from a user-owned or public-domain text/Markdown source;
2. its exact source digest, data classification, retention decision, route
   approval, card/cost ceilings, and frozen evaluation questions; and then
3. a separate full-book GO after chapter acceptance.

This Phase 0 decision selects the source class and sequence, not a live source.
