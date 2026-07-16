# Phase 1 Requirements: Core Contract With Writes Disabled

**Status:** packet drafted; pre-implementation review and separate GO required
**Phase 0 dependency:** accepted at Core commit `3757e9a`
**Implementation authority:** none
**Primary repository:** Eidetic Core

## Packet Documents

- `phase-1-spec.md` defines the normative protocol, canonicalization, data
  model, operation semantics, no-write boundary, and implementation allowlist.
- This file is the normative requirements-to-evidence index.
- `phase-1-test-plan.md` defines exact fixture classes, test IDs, commands,
  no-write snapshots, compatibility checks, and installed-runtime evidence.
- `phase-1-worktree-inventory.md` freezes the source branch, accepted base,
  packet paths, and all pre-existing excluded M3/preview worktree state.
- `phase-1-review.md` is created only after a fresh pre-implementation review
  binds a verdict to the exact specification commit.

## Objective

Create the canonical `eidetic.ingestion` v1 contract and an executable
validation/preview-only Core worker while durable submission remains absent or
returns `permission_denied`.

Phase 1 proves contract shape, canonicalization, privacy, compatibility, and
exact visible-effect preview. It does not implement grants, ledger, staging,
`DurableWriteCoordinator`, receipts, or file mutation.

The repository boundary is deliberately three-part:

- Eidetic Core owns protocol semantics, canonical contract sources,
  validation, target-state inspection, preview, future authorization, durable
  mutation, receipts, recovery, storage, search, and Core-local MLX embedding;
- `eidetic-sdk` is a separate repository that may consume a pinned immutable
  Core contract artifact and provide typed transport/session orchestration, but
  never imports Core internals or writes Core storage directly; and
- a separate future `eidetic-importer` owns document decoding, OCR, chunking,
  LLM/MLX atomization, repair, provider routing, and source checkpoints while
  calling Core only through `eidetic-sdk`.

Core never imports either downstream component. Phase 1 changes Core only and
does not publish or modify an SDK or importer release.

## Proposed File Scope For Phase 1 Review

The later Phase 1 GO may authorize only a reviewed subset of:

- `contracts/ingestion/v1/*`
- `bin/eidetic_ingestion_worker.py`
- `tests/test_ingestion_contract.py`
- `tests/test_ingestion_worker.py`
- `tests/test_ingestion_no_write.py`
- `tests/fixtures/ingestion/v1/**/*.json`
- `install.sh` for additive contract/worker deployment
- `docs/eidetic-llm-wiki-ingestion/phase-1-review.md`

No SDK, importer, M3, hook, YouGile, provider, memory-card, derived-index, or
installed-runtime file is in scope unless a Phase 1 packet names it explicitly.
The reviewed specification currently authorizes no ADR/Core ownership rewrite;
any semantic change to those accepted documents invalidates the packet and
requires a new review.

## Requirements-To-Evidence Matrix

| ID | Requirement | Required evidence |
| --- | --- | --- |
| P1-001 | Core owns one canonical ingestion v1 contract manifest and immutable artifact identity. | contract parse test plus manifest/artifact digest golden |
| P1-002 | Protocol uses local UTF-8 JSONL framing with strict request/response envelopes. | framing and malformed-envelope tests |
| P1-003 | Executable operations are limited to `capabilities`, `health`, `validate_candidate`, `validate_manifest`, `preview_ingest`, and `preview_manifest`. | capability fixture and unsupported-operation tests |
| P1-004 | Exact blocked `submit_ingest` and reserved `retrieve_candidate_grant` requests deterministically return `permission_denied`; neither can produce `INTENT`, file, ledger, grant, receipt, checkpoint, or other state. | blocked-operation schema fixtures, denial tests, and filesystem/state snapshot |
| P1-005 | Capabilities report protocol/build/contract-artifact identities, six executable operations, both blocked operations, and `submit_available=false`. | exact capability golden and installed-worker smoke |
| P1-006 | No SDK request can enable a scope or grant write permission. | forged configuration/scope field tests |
| P1-007 | Candidate canonicalization is versioned and deterministic. | byte-for-byte canonical digest goldens |
| P1-008 | Candidate canonicalization rejects Core paths, database/table identifiers, lock handles, provider/model routes, credentials, grants, and policy verdicts. | one negative fixture per prohibited class |
| P1-009 | Candidate identity includes stable source system/object/revision, expected predecessor where present, always-present nullable source-created/source-updated timestamps, source/content digests, logical scope, and allowed provenance. | schema and semantic validation fixtures |
| P1-010 | Same source claim/revision with a different digest is visible as a conflict candidate, never silently normalized as retry. | same-revision/different-digest golden |
| P1-011 | Manifest canonicalization binds source, pipeline, chunk-ledger, complete candidate set, candidate digests, identities, lineage actions, scope, policy, and completeness assertion. | manifest digest goldens and mutation matrix |
| P1-012 | Missing chunk outcomes/candidates/support references, support offsets outside their referenced chunk, cross-chunk spans, or unresolved identities invalidate the manifest. | incomplete-manifest and support-containment negative fixtures |
| P1-013 | Any policy-relevant manifest/candidate mutation changes the digest and invalidates the previous preview. | field-by-field mutation tests |
| P1-014 | `validate_candidate` and `validate_manifest` perform no durable or derived write. | before/after filesystem, database, ledger, and op-log snapshot |
| P1-015 | `preview_ingest` derives a canonical logical target identity/state, action, and structured owner-visible effect with exact digests, without a path or write authority. | target/action matrix, frozen visible-effect fixture, and no-write snapshot |
| P1-016 | `preview_manifest` returns aggregate counts plus every candidate-level visible effect. | complete manifest preview golden |
| P1-017 | Summary-only preview cannot represent approval; candidate detail and source locators remain inspectable. | response-schema requirements and omission tests |
| P1-018 | Manifest preview states explicitly that V1 commits candidates independently and can finish partially. | schema/documentation golden |
| P1-019 | Preview tokens are bound to exact candidate/manifest digest, principal, scope, policy, ordered logical target states, canonical visible effect, session, nonce, and expiry but confer no authority. | canonical binding golden plus token-binding mutation and expiry tests |
| P1-020 | No owner approval record or exact candidate grant is minted/retrieved in Phase 1; the reserved future retrieval wire shape preserves exact unconsumed candidate-grant invariants without becoming executable. | reserved-schema parity, deterministic denial, capability, and state-snapshot tests |
| P1-021 | Errors are sanitized and distinguish invalid request, incompatible version, permission denied, preview stale, target/source conflict, busy, timeout, and internal error without claiming a durable result. | error-taxonomy fixtures |
| P1-022 | Responses/logs expose no Core physical path, raw source snapshot, provider internal, credential, or personal identity data. | static scan and runtime response/log scan |
| P1-023 | Source text used for validation/preview is not copied into Core receipts, SDK journals, provider cache, or durable card storage. | filesystem/content canary scan |
| P1-024 | The disabled worker imports no SDK/importer/provider module; Core contains no downstream dependency, and document/LLM/MLX atomization remains importer-owned. | static dependency and responsibility-boundary tests |
| P1-025 | Canonical contract sources remain in Core; SDK compatibility uses a pinned immutable Core artifact/digest rather than a Core checkout, submodule, relative path, or copied canonical schema tree. | artifact manifest test and cross-repository static test |
| P1-026 | Current and immediately previous supported protocol fixtures have an explicit compatibility rule; incompatible major versions fail before validation/preview. | compatibility matrix tests |
| P1-027 | The logical imported-book pilot scope resolves internally and remains physically isolated from current writer roots. | sanitized capability/config fixture plus writer-root negative test |
| P1-028 | Preview never exposes the private pilot target path. | canary path rejection/response scan |
| P1-029 | Worker timeout/close/restart before submit leaves no durable ingestion state and does not imply acceptance. | process lifecycle tests |
| P1-030 | Reviewed Core source, immutable contract artifact, and installed contract/worker/build identities match after separately authorized packaging/deployment. | stable deployment evidence IDs and byte/digest comparison |
| P1-031 | Existing Core, Engine, memory, hook, and M3 behavior remains unchanged. | full regression plus scoped diff audit |
| P1-032 | No pre-existing M3/preview work appears in the Phase 1 staged allowlist or commit. | `git diff --cached --name-only` allowlist evidence |

## Canonical Schemas To Specify

The Phase 1 packet must propose Core-owned canonical schemas for at least:

- protocol request and response envelopes;
- capabilities and health;
- candidate and claim-level source-support reference;
- document/source/pipeline identity;
- chunk accounting summary;
- immutable document manifest;
- candidate validation result;
- manifest validation result;
- candidate preview and preview token;
- manifest preview and candidate-effect rows;
- persisted non-authorizing approval/grant references and the reserved exact
  unconsumed candidate-grant retrieval request/result;
- sanitized nonterminal errors; and
- the exact disabled submit request shape only to the extent required to freeze
  its future authorization inputs and prove it cannot execute in Phase 1.

Full terminal receipt, approval-record, exact-grant, ledger, and recovery-state
schemas may be finalized in Phase 4, but Phase 1 cannot choose a representation
that contradicts ADR 0004 or the accepted manifest contract.

## Negative-Test Minimum

The review packet must enumerate exact fixtures for:

- malformed JSON and envelope/version mismatches;
- unknown/duplicate fields and invalid enum values;
- empty or oversized candidate/manifest fields;
- invalid Unicode/normalization and digest mismatches;
- absent, valid, malformed, and noncanonical source timestamps;
- missing source revision or unsupported lineage shape;
- Core path, database, table, lock, credential, provider route, grant, and raw
  authorization injection;
- incomplete chunk ledger or candidate set;
- a support span outside its referenced chunk, crossing a chunk boundary, or
  whose normalized character length disagrees with its offsets;
- candidate/manifest digest mutation after preview;
- changed policy, target state, scope, source lineage, or expiry;
- forged write/scope-enable fields;
- exact blocked submit/grant-retrieval requests, every unsupported alias, and
  denial before payload semantic interpretation;
- one sanitized response for every physical JSONL input line, including a
  blank or malformed line; and
- path/secret/canary leakage through stdout, stderr, logs, and errors.

## Exact No-Write Proof

Phase 1 post-review must compare before/after evidence for the validation and
preview tests and prove no creation or mutation of:

- imported-book target files;
- project/agent/topic-base memory files;
- ingestion ledgers, `INTENT` rows, stages, receipts, grants, or approval records;
- Core op-log or lifecycle evidence;
- derived vector/FTS indexes; and
- SDK/importer/provider state.

Temporary test fixtures inside isolated test directories are allowed only when
the reviewed test plan names them, creates them in the parent harness before
the no-write snapshot, and proves cleanup without touching protected or
append-only state. The worker child runs with pre-import write traps, bytecode
disabled, and disposable home/temp/cache roots that are also snapshotted.

## Review And Exit Gate

Before implementation:

- a fresh reviewer must return `CLEAN` on schemas, semantics, no-write proof,
  privacy, compatibility, and file allowlist;
- owner GO must bind the reviewed Phase 1 commit; and
- the dirty Core worktree must be isolated through a clean branch/worktree.

After implementation:

- all P1 requirements must map to reproducible evidence;
- P0/P1 findings must be zero;
- the installed worker, if deployment was separately authorized, must report the
  reviewed build and contract/artifact identities; and
- post-review must return `ACCEPT` before any SDK or importer Phase 2 work.

Phase 1 acceptance never authorizes `submit_ingest`, provider execution, source
processing, or a real ingestion scope.
