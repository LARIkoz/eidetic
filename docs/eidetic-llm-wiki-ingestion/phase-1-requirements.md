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

## Proposed File Scope For Phase 1 Review

The later Phase 1 GO may authorize only a reviewed subset of:

- `schemas/sdk/ingestion/v1/*`
- `bin/eidetic_ingestion_worker.py`
- `tests/test_ingestion_contract.py`
- `tests/test_ingestion_worker.py`
- `tests/test_ingestion_no_write.py`
- `tests/fixtures/ingestion/v1/**/*.json`
- `install.sh` for additive schema/worker deployment
- `docs/eidetic-llm-wiki-ingestion/phase-1-review.md`

No SDK, importer, M3, hook, YouGile, provider, memory-card, derived-index, or
installed-runtime file is in scope unless a Phase 1 packet names it explicitly.
The reviewed specification currently authorizes no ADR/Core ownership rewrite;
any semantic change to those accepted documents invalidates the packet and
requires a new review.

## Requirements-To-Evidence Matrix

| ID | Requirement | Required evidence |
| --- | --- | --- |
| P1-001 | Core owns one canonical ingestion v1 schema manifest. | schema parse test plus manifest digest golden |
| P1-002 | Protocol uses local UTF-8 JSONL framing with strict request/response envelopes. | framing and malformed-envelope tests |
| P1-003 | Executable operations are limited to `capabilities`, `health`, `validate_candidate`, `validate_manifest`, `preview_ingest`, and `preview_manifest`. | capability fixture and unsupported-operation tests |
| P1-004 | `submit_ingest` is absent or deterministically returns `permission_denied`; it cannot produce `INTENT`, file, ledger, grant, receipt, or checkpoint state. | negative submit test plus filesystem/state snapshot |
| P1-005 | Capabilities report protocol/build/schema identities and `submit_available=false`. | exact capability golden and installed-worker smoke |
| P1-006 | No SDK request can enable a scope or grant write permission. | forged configuration/scope field tests |
| P1-007 | Candidate canonicalization is versioned and deterministic. | byte-for-byte canonical digest goldens |
| P1-008 | Candidate canonicalization rejects Core paths, database/table identifiers, lock handles, provider/model routes, credentials, grants, and policy verdicts. | one negative fixture per prohibited class |
| P1-009 | Candidate identity includes stable source system/object/revision, expected predecessor where present, source/content digests, logical scope, and allowed provenance. | schema and semantic validation fixtures |
| P1-010 | Same source claim/revision with a different digest is visible as a conflict candidate, never silently normalized as retry. | same-revision/different-digest golden |
| P1-011 | Manifest canonicalization binds source, pipeline, chunk-ledger, complete candidate set, candidate digests, identities, lineage actions, scope, policy, and completeness assertion. | manifest digest goldens and mutation matrix |
| P1-012 | Missing chunk outcomes, candidates, support references, or unresolved identities invalidate the manifest. | incomplete-manifest negative fixtures |
| P1-013 | Any policy-relevant manifest/candidate mutation changes the digest and invalidates the previous preview. | field-by-field mutation tests |
| P1-014 | `validate_candidate` and `validate_manifest` perform no durable or derived write. | before/after filesystem, database, ledger, and op-log snapshot |
| P1-015 | `preview_ingest` returns the exact proposed candidate visible effect without write authority. | frozen preview fixture and no-write snapshot |
| P1-016 | `preview_manifest` returns aggregate counts plus every candidate-level visible effect. | complete manifest preview golden |
| P1-017 | Summary-only preview cannot represent approval; candidate detail and source locators remain inspectable. | response-schema requirements and omission tests |
| P1-018 | Manifest preview states explicitly that V1 commits candidates independently and can finish partially. | schema/documentation golden |
| P1-019 | Preview tokens are bound to exact candidate/manifest digest, scope, policy, expected target state, and expiry but confer no authority. | token-binding mutation and expiry tests |
| P1-020 | No owner approval record or exact candidate grant is minted in Phase 1. | capability and state-snapshot test |
| P1-021 | Errors are sanitized and distinguish invalid request, incompatible version, permission denied, preview stale, target/source conflict, busy, timeout, and internal error without claiming a durable result. | error-taxonomy fixtures |
| P1-022 | Responses/logs expose no Core physical path, raw source snapshot, provider internal, credential, or personal identity data. | static scan and runtime response/log scan |
| P1-023 | Source text used for validation/preview is not copied into Core receipts, SDK journals, provider cache, or durable card storage. | filesystem/content canary scan |
| P1-024 | The disabled worker imports no SDK/importer/provider module and Core contains no SDK dependency. | static dependency test |
| P1-025 | Canonical schemas remain in Core and are not copied into `eidetic-sdk`. | cross-repository static test |
| P1-026 | Current and immediately previous supported protocol fixtures have an explicit compatibility rule; incompatible major versions fail before validation/preview. | compatibility matrix tests |
| P1-027 | The logical imported-book pilot scope resolves internally and remains physically isolated from current writer roots. | sanitized capability/config fixture plus writer-root negative test |
| P1-028 | Preview never exposes the private pilot target path. | canary path rejection/response scan |
| P1-029 | Worker timeout/close/restart before submit leaves no durable ingestion state and does not imply acceptance. | process lifecycle tests |
| P1-030 | Source and installed schema/worker/build identities match after separately authorized additive deployment. | byte/digest comparison and installed public-protocol smoke |
| P1-031 | Existing Core, Engine, memory, hook, and M3 behavior remains unchanged. | full regression plus scoped diff audit |
| P1-032 | No pre-existing M3/preview work appears in the Phase 1 staged allowlist or commit. | `git diff --cached --name-only` allowlist evidence |

## Canonical Schemas To Specify

The Phase 1 packet must propose canonical schemas for at least:

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
- sanitized nonterminal errors; and
- future submit/grant/receipt identifiers only to the extent required to prove
  that the disabled surface cannot accept them.

Full terminal receipt, approval-record, exact-grant, ledger, and recovery-state
schemas may be finalized in Phase 4, but Phase 1 cannot choose a representation
that contradicts ADR 0004 or the accepted manifest contract.

## Negative-Test Minimum

The review packet must enumerate exact fixtures for:

- malformed JSON and envelope/version mismatches;
- unknown/duplicate fields and invalid enum values;
- empty or oversized candidate/manifest fields;
- invalid Unicode/normalization and digest mismatches;
- missing source revision or unsupported lineage shape;
- Core path, database, table, lock, credential, provider route, grant, and raw
  authorization injection;
- incomplete chunk ledger or candidate set;
- candidate/manifest digest mutation after preview;
- changed policy, target state, scope, source lineage, or expiry;
- forged write/scope-enable fields;
- every submit spelling/alias while submit is disabled; and
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
the reviewed test plan names them and proves cleanup without touching protected
or append-only state.

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
  reviewed build and schema identities; and
- post-review must return `ACCEPT` before any SDK or importer Phase 2 work.

Phase 1 acceptance never authorizes `submit_ingest`, provider execution, source
processing, or a real ingestion scope.
