# Phase 1 Specification: Core Ingestion Contract With Writes Disabled

**Status:** candidate for pre-implementation review
**Phase:** 1
**Phase 0 dependency:** accepted at Core commit `3757e9a`
**Implementation authority:** none
**Primary repository:** Eidetic Core
**Protocol:** `eidetic.ingestion` `1.0`

## 1. Decision

Phase 1 adds a separate, Core-owned ingestion protocol shape and a future
validation/preview-only worker. It proves canonical schemas, deterministic
digests, complete document-manifest validation, privacy-safe visible-effect
preview, compatibility, and an exact no-write boundary.

The future Phase 1 worker may execute only:

- `capabilities`;
- `health`;
- `validate_candidate`;
- `validate_manifest`;
- `preview_ingest`; and
- `preview_manifest`.

The exact operation name `submit_ingest` is recognized only so it can return a
deterministic `permission_denied` response. It is not an executable capability.
No alias such as `submit`, `ingest`, `write`, or a case variant is recognized.

Phase 1 creates no approval record, authorization grant, ledger, staging file,
receipt, checkpoint, target file, derived index row, operation-log row, or
lifecycle event. It performs no LLM or provider call.

## 2. Product And Ownership Boundary

The Phase 1 data flow is:

```text
synthetic candidate or immutable document manifest
  -> Core-owned eidetic.ingestion JSONL worker
  -> strict schema and semantic validation in memory
  -> Core canonical candidate/manifest digests
  -> read-only logical target inspection
  -> exact owner-visible effect plus non-authorizing preview tokens
```

The dependency direction remains:

```text
future importer -> eidetic-sdk -> Core worker
future importer -> shared_api_cache for knowledge_atomization
Core -X-> eidetic-sdk / importer / shared_api_cache
```

Phase 1 does not use `shared_api_cache`. The future importer Phase 3 owns the
`knowledge_atomization` task call. Phase 1 therefore has no model family,
provider route, credential lane, worker concurrency, fallback, or penalty
state to select.

## 3. Explicit Non-goals

Phase 1 does not:

- implement `submit_ingest`, `get_receipt`, approval, grant, receipt, query, or
  retrieve behavior;
- create a `DurableWriteCoordinator`, ledger, stage, provenance marker, or
  durable target renderer;
- change `eidetic.engine`, MLX embedding, search, rerank, or source-corpus sync;
- change `eidetic-sdk` or initialize/change `eidetic-importer`;
- register or call `knowledge_atomization`;
- process a real book, chapter, private source, PDF, EPUB, URL, or provider
  response;
- deploy an installed worker without a separate deployment GO;
- release, tag, publish, push, or open a pull request;
- touch existing project/agent memory, topic bases, M3 state, or YouGile
  preview work; or
- decide future grant retrieval, receipt, journal, or durable-commit wire
  schemas beyond reserving incompatible field names.

## 4. Provider And Runtime Intake

This packet records the required provider-entrypoint intake even though the
phase is deliberately provider-free:

```text
project: Eidetic Core
source_artifact: synthetic JSON fixtures only
requested_work: canonical validation and visible-effect preview
input_modality: local UTF-8 JSONL
output_contract: eidetic.ingestion 1.0 JSONL responses
volume: bounded unit/contract fixtures
batch_or_single: single local request per JSONL line
quality_gate: P1-001 through P1-032
write_scope: none
failure_policy: fail closed; no durable or derived side effect
task_capability: none in Phase 1
selected_route: none
execution_readiness: provider execution prohibited
penalty_state: untouched
```

MLX remains a Core-owned Engine embedding backend and is outside this protocol.
It does not atomize documents and is not loaded by the Phase 1 worker.

## 5. Transport And Envelope

### 5.1 Framing

- Transport is local stdin/stdout using one UTF-8 JSON object per line.
- The maximum request line is 64 MiB including the newline.
- Blank lines, invalid UTF-8, a BOM, malformed JSON, duplicate object keys,
  non-standard `NaN`/`Infinity`, and trailing non-whitespace bytes fail as
  `invalid_request`.
- The worker emits exactly one response line for every nonblank request line
  that can yield a request identifier. For an unparseable envelope it emits a
  sanitized response with `request_id="unknown"`.
- stdout contains protocol responses only. Sanitized diagnostics may use
  stderr but must contain no candidate body, support span, physical path,
  credential, provider, or personal identity.

### 5.2 Request envelope

```json
{
  "protocol": "eidetic.ingestion",
  "version": "1.0",
  "request_id": "caller-generated-id",
  "operation": "capabilities",
  "payload": {}
}
```

Envelope fields are exact and `additionalProperties` is false. `request_id`
is an opaque 1-128 character printable identifier and is never an idempotency
key.

### 5.3 Response envelope

```json
{
  "protocol": "eidetic.ingestion",
  "version": "1.0",
  "request_id": "caller-generated-id",
  "ok": true,
  "result": {},
  "error": null
}
```

`ok=true` means the read-only protocol operation completed. It never means that
content was approved, accepted, written, or checkpoint-eligible. `ok=false`
requires `result=null` and a sanitized error.

## 6. Compatibility

- The first supported version set is exactly `{"1.0"}`.
- A request for `1.0` is accepted.
- `1.1`, any other unlisted minor, `0.x`, and `2.x` fail with
  `incompatible_version` until that exact version is shipped and listed.
- A future `1.1` worker must retain a frozen `1.0` fixture and accept both
  versions, or the change is a protocol-major change.
- Required-field changes, removed operations, canonicalization changes,
  digest-field changes, preview-binding changes, or changed authorization/
  idempotency meaning require protocol `2.0`.
- An additive optional field may ship in a new listed minor only when older
  request/response fixtures remain byte-semantically compatible.

The worker reports the current version, exact supported versions, Core build
identity, schema-manifest digest, canonicalization identity, preview policy,
and render policy in `capabilities` before any candidate operation.

## 7. Canonical Schema Set

The future implementation owns exactly these Core-canonical files:

```text
schemas/sdk/ingestion/v1/
  contract-manifest.json
  request.schema.json
  response.schema.json
  capabilities.schema.json
  health.schema.json
  identity.schema.json
  source-support.schema.json
  candidate.schema.json
  chunk-outcome.schema.json
  document-manifest.schema.json
  validation-result.schema.json
  preview.schema.json
  error.schema.json
```

`contract-manifest.json` lists the protocol, exact supported versions,
transport, worker path, executable operations, blocked operations, schema file
names, canonicalization identity, digest domains, privacy flags, and
`submit_available=false`.

Every schema uses JSON Schema draft 2020-12, a stable local `$id`, explicit
required fields, bounded strings/arrays, and `additionalProperties=false` at
every protocol-owned object level. The worker may implement validators with
the Python standard library, but parity tests must prove that runtime behavior
matches the canonical schemas.

The SDK repository must discover/read these schemas from a selected Core
checkout or installed contract directory. It must not copy them.

## 8. Canonicalization And Digests

### 8.1 Input normalization

Core applies only these declared normalizations:

1. Decode strict UTF-8; lone surrogates and invalid sequences are rejected.
2. Normalize text-bearing values to Unicode NFC.
3. Convert CRLF and bare CR line endings to LF in `body`, `claim_text`, and
   `span_text`.
4. Reject NUL and disallowed C0/C1 control characters. Tab and LF are allowed
   only in text-bearing fields.
5. Require identifier and title fields to have no leading/trailing whitespace.
6. Preserve body, claim, and span whitespace after the declared Unicode and
   line-ending normalization. Core does not silently rewrite wording, enums,
   IDs, offsets, or ordering.

Identifiers use explicit ASCII patterns. Timestamps are RFC 3339 UTC strings.
Protocol objects contain no floating-point numbers; counts and offsets are
non-negative integers.

### 8.2 Canonical JSON

`eidetic.canonical-json.v1` is:

- schema and semantic validation first;
- normalized values only;
- UTF-8 without BOM;
- object keys sorted by Unicode code point;
- arrays preserved in their declared order;
- JSON separators `,` and `:` with no insignificant whitespace;
- non-ASCII characters emitted as UTF-8, not `\u` escapes unless JSON requires
  escaping; and
- a final byte sequence with no trailing newline.

The implementation must not depend on locale, hash-map iteration order, Python
minor version, filesystem path, process identity, timestamp, or provider data.

### 8.3 Domain-separated digests

All digests are lower-case 64-character SHA-256 hex. Core computes:

```text
candidate_digest = SHA256(
  "eidetic.ingestion/v1/candidate\0" || canonical_candidate_bytes
)

content_digest = SHA256(
  "eidetic.ingestion/v1/card-content\0" || canonical_content_bytes
)

chunk_ledger_digest = SHA256(
  "eidetic.ingestion/v1/chunk-ledger\0" || canonical_chunk_outcome_bytes
)

manifest_digest = SHA256(
  "eidetic.ingestion/v1/document-manifest\0" || canonical_manifest_bytes
)

source_span_digest = SHA256(
  "eidetic.ingestion/v1/source-span\0" || normalized_span_utf8
)

rendered_content_digest = SHA256(
  "eidetic.ingestion/v1/rendered-content\0" || rendered_content_utf8
)

absent_target_state_digest = SHA256(
  "eidetic.ingestion/v1/target-state\0absent"
)

preview_binding_digest = SHA256(
  "eidetic.ingestion/v1/preview-binding\0" || canonical_preview_binding_bytes
)
```

`canonical_content_bytes` contains exactly `title`, `body`, `card_kind`, and
`evidence`; it excludes the caller-declared `content_digest` and therefore is
not self-referential. Caller-declared `content_digest`, `chunk_ledger_digest`,
candidate digests in a manifest, and source-span digests are recomputed where
Core has the bytes and must match. `source_digest` is structurally validated
but cannot be recomputed without the importer-owned source snapshot; the
preview states this limit.

The request candidate never contains `candidate_digest`, and the request
manifest never contains its own `manifest_digest`. Core returns those
authoritative values beside the normalized object. A frozen importer bundle
may store that returned value externally, but inserting it into the canonical
object is invalid.

The reported schema-manifest digest is computed over the path-sorted 13-file
canonical contract set as `<relative-path> NUL <exact-file-bytes> LF`. No schema
file contains that aggregate digest, so the bundle identity is non-circular.

### 8.4 Non-circular candidate/manifest binding

The canonical candidate includes document identity and pipeline revision but
does not include `manifest_digest`. Otherwise a manifest digest that includes
candidate digests would be circular.

The manifest contains ordered entries with the full candidate and its declared
candidate digest. Core recomputes each candidate digest, then computes the
manifest digest over the verified ordered entries. A future manifest approval
and each exact candidate grant bind both digests. `manifest_entry_id` belongs
to the manifest entry, not to the candidate's standalone semantic identity.

## 9. Candidate Contract

The normative shape is equivalent to:

```json
{
  "candidate_version": "1.0",
  "scope_id": "imported-book-pilot",
  "source": {
    "source_system": "document-importer",
    "source_object_id": "book-key:logical-card-id",
    "source_revision": "revision-identity",
    "expected_predecessor_revision": null,
    "source_digest": "64-lowercase-hex"
  },
  "document": {
    "document_key": "stable-document-key",
    "document_revision": "immutable-document-revision",
    "pipeline_revision": "provider-neutral-pipeline-revision"
  },
  "logical_card_id": "source-scoped-logical-id",
  "content": {
    "title": "Specific title",
    "body": "Self-contained atomic knowledge.",
    "card_kind": "concept",
    "evidence": "observed",
    "content_digest": "64-lowercase-hex"
  },
  "claims": [
    {
      "claim_id": "claim-1",
      "claim_text": "One independently supportable claim.",
      "support": [
        {
          "support_id": "support-1",
          "document_revision": "immutable-document-revision",
          "chunk_id": "chunk-0001",
          "section_id": "chapter-1",
          "page": null,
          "start_char": 10,
          "end_char": 52,
          "span_text": "Exact source span visible to the owner.",
          "span_digest": "64-lowercase-hex"
        }
      ]
    }
  ],
  "lineage": {
    "action": "create",
    "predecessor_logical_card_ids": []
  }
}
```

### 9.1 Candidate bounds

- title: 1-256 characters;
- body: 1-16,384 characters;
- claims: 1-64 unique `claim_id` values;
- claim text: 1-4,096 characters;
- support references: 1-8 per claim;
- support span: 1-8,192 characters;
- candidate canonical bytes: at most 256 KiB; and
- all identity fields: bounded to 128 or 512 characters according to their
  schema role.

`card_kind` is exactly one of `concept`, `entity`, `finding`, `reference`, or
`research`. `evidence` is exactly `observed` or `hypothesis`. Invalid values are
rejected, never defaulted or case-folded. Core, not the candidate, later owns
the durable `source=imported` classification.

### 9.2 Source-support semantics

Each factual claim has at least one support reference. Core verifies:

- document revisions match the candidate;
- offsets are non-negative and `end_char > start_char`;
- span digests match the normalized span bytes;
- IDs are unique within their owning arrays; and
- no physical path, provider route, credential, raw grant, or policy verdict
  appears in a protocol-owned field.

Phase 1 does not possess the importer source snapshot and does not claim that a
span occurs at the asserted offset or semantically supports the claim. The
validation result explicitly reports
`source_snapshot_verified=false` and `semantic_support_verified=false`.
Importer Phase 3 must prove those properties before a real manifest may be
considered for durable submit.

### 9.3 Lineage shape

Allowed actions are `create`, `retain`, `update`, `split`, `merge`,
`supersede`, `archive`, and `alternate`.

- `create` has zero predecessors.
- `retain`, `update`, `supersede`, `archive`, and `alternate` have exactly one.
- `split` has exactly one predecessor; sibling outputs are reconciled by the
  manifest.
- `merge` has at least two unique predecessors.

No absent candidate implies deletion, archival, or supersession.

## 10. Document Manifest Contract

The document manifest contains:

- `manifest_version="1.0"`;
- logical scope and expected authenticated principal reference;
- immutable source/document/pipeline identity;
- requested Core policy identity/version/digest as an expectation, never as a
  caller-authored verdict;
- normalized source length and complete ordered chunk outcomes;
- caller-declared and Core-recomputed chunk-ledger digest;
- ordered candidate entries containing `manifest_entry_id`, full candidate,
  and declared candidate digest;
- rejected/repair/failure accounting;
- an explicit completeness assertion; and
- no grant, authorization, idempotency key, Core path, provider route, or raw
  source snapshot.

### 10.1 Chunk outcomes

Every normalized source character range is represented by exactly one ordered
chunk outcome with:

- `chunk_id`, source/document revision, start/end character offsets, section
  identity, optional page, and chunk-text digest;
- one state: `candidates`, `repair_required`, `failed`, or
  `owner_no_knowledge`;
- candidate entry IDs for `candidates`, and none for other states;
- a bounded reason code for repair/failure/no-knowledge; and
- an owner-review reference for `owner_no_knowledge`.

The ordered offsets must start at zero, be contiguous and non-overlapping, and
end at `normalized_text_length`. Duplicate/missing chunk IDs, gaps, overlaps,
unreferenced candidate entries, multiply referenced candidate entries, or a
candidate that cites an unknown chunk make the manifest invalid.

`owner_no_knowledge` is never inferred from zero candidates. It requires a
nonempty owner-review reference and a reason code. Empty, malformed, truncated,
or schema-invalid atomizer output belongs in `repair_required` or `failed`.

### 10.2 Manifest bounds

- maximum 2,000 candidate entries;
- maximum 20,000 chunk outcomes;
- maximum 64 MiB request line;
- every candidate must satisfy the standalone candidate bound; and
- all manifest entry, chunk, claim, and support IDs are unique in their
  relevant namespaces.

The manifest preserves candidate and chunk ordering in its digest. Reordering,
adding, removing, substituting, or changing any policy-relevant field changes
the digest and invalidates the previous preview.

## 11. Core-owned Principal, Scope, Policy, And Target Context

The worker receives its principal, scope catalog, policy catalog, and logical
target resolver from Core-owned startup configuration. A request can state an
expected principal/policy/scope only so Core can fail closed on mismatch; it
cannot create or modify them.

Phase 1 defaults every known scope to `preview_only` or `disabled` and reports:

```text
submit_available=false
write_authority=false
approval_available=false
grant_minting_available=false
```

The logical pilot scope is `imported-book-pilot`. Its configured target root
must be physically outside project memory, agent memory, topic-base roots, and
all current writer resolution rules. The physical root is never returned or
logged. If Core cannot prove the isolated mapping, the scope is unavailable
for preview and remains disabled.

Target inspection is read-only. An absent target is represented by a
domain-separated logical absent-state digest. The worker must not create the
target root or parent directories in order to inspect or preview it.

## 12. Operation Semantics

### 12.1 `capabilities`

Payload is empty. Result includes:

- protocol/current/supported versions;
- Core build and schema-manifest identities;
- canonicalization, preview-policy, and render-policy identities;
- six executable operations;
- `blocked_operations=["submit_ingest"]`;
- logical scopes with `disabled` or `preview_only` mode;
- sanitized principal reference/attestation class;
- privacy flags; and
- every no-write/no-approval/no-grant flag above.

It returns no model identity, physical path, credential, raw configuration, or
personal identity.

### 12.2 `health`

Payload may be empty or contain one logical `scope_id`. The operation reports
runtime/schema/policy readiness and read-only logical-scope availability. It
does not create a directory, open a writable database, initialize state, load
MLX, or repair anything.

### 12.3 `validate_candidate`

Payload contains one candidate. The operation returns `ok=true` with:

- `status=valid|invalid|conflict`;
- normalized candidate and authoritative digests only when structurally safe;
- ordered findings with stable codes and JSON-pointer locations;
- source-snapshot and semantic-support verification flags set to false in
  Phase 1; and
- `write_authority=false`.

Semantic invalidity is a validation result, not a traceback or unsanitized
envelope failure.

### 12.4 `validate_manifest`

Payload contains one complete manifest. Core recomputes every candidate,
content, support-span, chunk-ledger, and manifest digest it can prove. The
result includes candidate/chunk accounting and ordered findings. Missing or
inconsistent completeness evidence returns `status=invalid`.

### 12.5 `preview_ingest`

Core first performs candidate validation, then inspects the logical target
read-only and returns one exact visible effect:

- `create`, `retain`, `update`, `split`, `merge`, `supersede`, `archive`,
  `alternate`, `no_change`, or `conflict`;
- logical target identity and expected state digest, never a path;
- normalized visible card fields and owner-visible source-support spans;
- deterministic rendered-content digest under the reported render policy;
- current policy and principal bindings;
- explicit no-write/no-approval/no-grant flags; and
- a non-authorizing preview token only when `preview_status=ready`.

A conflict is owner-visible and produces no token. Preview never reserves a
target or changes its state.

### 12.6 `preview_manifest`

Core validates the complete manifest and returns:

- authoritative manifest/chunk/candidate digests;
- aggregate action, conflict, repair, failure, and no-knowledge counts;
- every candidate-level effect and source-support detail;
- a manifest preview token plus candidate preview tokens when the complete
  manifest is ready;
- an explicit statement that candidate commits are independent and a future
  session may finish partially; and
- no summary-only approval surface.

The candidate token inside a manifest preview binds both candidate and manifest
digests. If any candidate is invalid, unresolved, or conflicting, the manifest
preview is not ready and no manifest token is returned.

## 13. Non-authorizing Preview Tokens

Phase 1 preview tokens are opaque integrity references, not grants. A token is
bound to:

- protocol/canonicalization identity;
- worker session identity;
- authenticated principal reference;
- scope and policy identity/version/digest;
- candidate digest and optional manifest digest;
- expected target-state digest;
- rendered-content digest;
- expiry; and
- a per-token nonce.

The public `preview_binding_digest` is deterministic for the binding fields.
The opaque token uses an in-memory session secret and has a fixed test clock/
secret/nonce injection surface for golden fixtures. The token is not persisted,
cannot be supplied to any Phase 1 operation to obtain authority, and becomes
useless when the process exits.

Any changed candidate, manifest, scope, principal, policy, target state,
render policy, or expiry yields a different binding and invalidates the old
preview. Later grant semantics remain governed by ADR 0004 and require a
separate implementation phase.

## 14. Error And Finding Taxonomy

Nonterminal envelope errors are:

- `invalid_request`;
- `incompatible_version`;
- `unsupported_operation`;
- `permission_denied`;
- `preview_stale`;
- `source_conflict`;
- `target_conflict`;
- `core_busy`;
- `timeout`; and
- `internal_error`.

Every error has a bounded public message, `retryable`, and optional sanitized
details from a fixed allowlist. No error claims a durable result.

Stable validation finding classes include schema, normalization, size,
identity, digest, source-support, chunk coverage, candidate accounting,
lineage, principal, scope, policy, source conflict, target conflict, and
privacy-boundary violations. Findings never echo a secret/path canary or full
candidate/source text.

The exact `submit_ingest` operation always returns `permission_denied` after
envelope validation and before payload interpretation. It cannot allocate a
token, grant, key, target, file, directory, database, or log entry.

## 15. Exact No-write Invariant

For every operation, success, invalid result, conflict, exception, timeout,
process close, and restart, Phase 1 must leave unchanged:

- imported-book target files and parent directories;
- project, agent, and topic-base memory roots;
- ingestion ledgers, stages, approvals, grants, receipts, and operation state;
- Core operation/lifecycle logs;
- FTS/vector/code/Engine indexes;
- SDK/importer journals and checkpoints; and
- provider cache, key, balance, usage, and penalty state.

The worker may hold normalized values, preview bindings, HMAC material, and
target snapshots in process memory only. Temporary files are not needed and
are forbidden in the worker path.

Tests use isolated canary roots, pre/post tree and content digests, patched
write primitives, a nonexistent target parent, and process lifecycle probes.
Any unexpected `open` in write/append/create mode, `mkdir`, SQLite writable
connection, atomic replace, fsync, or subprocess/provider call fails the test.

## 16. Privacy And Protected State

- Synthetic non-sensitive fixtures are mandatory for Phase 1.
- Candidate bodies and bounded support spans may cross only local stdio for the
  explicit validation/preview request and response.
- The complete source snapshot never enters Core, logs, receipts, journals, or
  test artifacts.
- No raw grant, idempotency key, credential, provider/model route, balance,
  penalty state, personal identity, or machine path appears in public output.
- Errors and stderr use stable codes and counts rather than echoing rejected
  values.
- Phase 1 neither reads nor writes `keys.env`, shared API caches, provider
  ledgers, or `~/shared_api_cache/key_penalty.db`.
- No cleanup, compaction, migration, or recomputation of protected state is in
  scope.

## 17. Compatibility With Existing Core And SDK

- `eidetic.engine` schemas, worker, operations, MLX runtime selection, and
  indexes remain byte-for-byte outside the Phase 1 diff.
- Core imports no `eidetic_sdk`, importer, provider, or shared cache module.
- `eidetic-sdk` remains unchanged at its current release line; Phase 2 will add
  typed preview support only after Phase 1 acceptance.
- Existing `remember.py`, compound, M1/M2/M3, hooks, MCP, topic-base, and
  installer behavior remain unchanged except for a separately reviewed
  additive installer copy block if that file is authorized.
- The legacy importer remains intact and cannot discover the new worker through
  an implicit redirect.

## 18. Future Implementation Allowlist

A later owner GO may authorize only this reviewed Core file set:

```text
schemas/sdk/ingestion/v1/*.json
bin/eidetic_ingestion_worker.py
tests/fixtures/ingestion/v1/**/*.json
tests/test_ingestion_contract.py
tests/test_ingestion_worker.py
tests/test_ingestion_no_write.py
install.sh                         # additive copy logic only, if explicitly included
docs/eidetic-llm-wiki-ingestion/phase-1-review.md
```

No existing Engine, memory writer, M3, hook, MCP, SDK, importer, provider, or
YouGile file is allowed. `install.sh` may only copy the new worker and canonical
ingestion schemas with existing atomic-install semantics; it may not deploy or
enable a scope during source implementation tests.

The implementation must occur in a clean dedicated worktree created from the
reviewed Phase 1 specification commit after GO. The current dirty Core worktree
must not be used for implementation.

## 19. Dirty-worktree Boundary

At packet preparation, the primary Core worktree contains pre-existing owner
changes in `bin/m3_judge.py`, `hooks/session-signals.sh`, and untracked M3
judge/schema/test/evaluation/planning files. Those files are unrelated owner
work. They must not be staged, stashed, reset, deleted, copied, or used as an
implementation base.

Before every specification or review commit:

```text
git diff --cached --name-only
-> docs/eidetic-llm-wiki-ingestion/* only
```

Before any later implementation commit, the staged path set must be a subset
of Section 18 and must contain no `m3`, hook, preview, or YouGile path.

## 20. Rollout, Deployment, And Rollback

### Source rollout

1. Commit and review this specification packet.
2. Receive owner GO bound to the reviewed specification commit.
3. Create a clean implementation worktree from that commit.
4. Add canonical schemas, disabled worker, fixtures, and tests only.
5. Run source tests and no-write evidence.
6. Conduct post-implementation review.

### Installed runtime

No source implementation GO implies installed deployment. A separate
deployment GO must name the installed root, backup/parity commands, and exact
additive files. Installed capabilities must remain preview-only and
`submit_available=false`.

### Rollback

Before deployment, rollback is a normal Git revert of Phase 1 implementation
commits. After separately authorized deployment, rollback restores the prior
installed bundle through the existing installer backup/rollback procedure.
Rollback must not delete protected memory/provider state or touch unrelated
installed writers.

## 21. Abort Conditions

Stop Phase 1 if implementation would:

- create any durable or derived state outside isolated test fixtures;
- add executable submit, approval, grant, receipt, query, or retrieval logic;
- make a preview token authorizing or persist it;
- expose or accept a Core path;
- let a request enable a principal, policy, scope, or writer;
- add a Core dependency on SDK/importer/provider code;
- copy Core schemas into `eidetic-sdk`;
- load MLX or call an LLM/provider;
- weaken strict schema, digest, chunk completeness, or source-support shape;
- use the dirty primary worktree for implementation;
- capture M3/preview/YouGile changes; or
- install, publish, tag, push, or release without a separate GO.

## 22. Review Gate

Pre-implementation review must return `CLEAN` with no P0/P1 finding for:

- protocol and schema completeness;
- canonicalization and non-circular digest semantics;
- candidate, support, chunk, manifest, and lineage rules;
- exact visible-effect preview and non-authorizing tokens;
- deterministic `submit_ingest` denial;
- no-write and privacy proof;
- current/future-minor compatibility;
- Core/SDK/importer/provider ownership boundaries;
- implementation file allowlist and clean-worktree plan; and
- test/evidence coverage in `phase-1-test-plan.md`.

Only after that review may the owner issue `GO Phase 1 implementation` bound to
the exact reviewed specification commit. Discussion, this packet, a CLEAN
review, or Phase 0 acceptance alone does not authorize implementation.
