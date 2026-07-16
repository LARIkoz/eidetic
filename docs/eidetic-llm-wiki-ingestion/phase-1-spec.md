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

The exact operation names `submit_ingest` and `retrieve_candidate_grant` are
recognized only so they can return deterministic `permission_denied`
responses. They are not executable capabilities. No alias such as `submit`,
`ingest`, `write`, `grant`, `retrieve_grant`, or a case variant is recognized.

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

The repositories and release surfaces remain distinct:

| Component | Owns | Must not own |
| --- | --- | --- |
| Eidetic Core | protocol semantics, canonical contract sources, validation, logical target state, visible-effect preview, future authorization/write/receipt/recovery, storage/search, and existing Core-local MLX embedding | SDK transport/orchestration, document decoding, OCR, LLM atomization, provider routing, or importer checkpoints |
| `eidetic-sdk` | typed public-protocol client, transport adapters, version/artifact compatibility checks, retry/session orchestration, and non-authorizing reference journaling | Core internals/storage paths, policy decisions, grant minting, durable target mutation, canonical contract-source ownership, document parsing, or model/provider execution |
| future `eidetic-importer` | PDF/EPUB/Markdown/web decoding, OCR, normalization, chunking, LLM or local-MLX atomization, repair, provider routing, manifest construction, and source checkpoints | Core target paths, Core policy/authorization, direct durable writes, or a private protocol fork |

Core publishes the protocol seam as a versioned immutable contract artifact.
The SDK consumes an exact artifact version and digest; it does not depend on a
Core checkout, Git submodule, repository-relative path, or runtime import of
Core Python code. The importer depends on the SDK, never on Core internals. A
separate `eidetic-contracts` repository is intentionally not introduced in
Phase 1; Core remains the protocol authority until independent multi-language
governance creates a demonstrated need for such a split.

Phase 1 does not use `shared_api_cache`. The future importer Phase 3 owns the
`knowledge_atomization` task call. Phase 1 therefore has no model family,
provider route, credential lane, worker concurrency, fallback, or penalty
state to select.

## 3. Explicit Non-goals

Phase 1 does not:

- execute `submit_ingest`, `retrieve_candidate_grant`, `get_receipt`, approval,
  grant, receipt, query, or other retrieve behavior; Phase 1 freezes only the
  disabled submit and reserved grant-retrieval wire shapes;
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
- decide future receipt, journal, or durable-commit result schemas beyond the
  exact disabled submit inputs and exact unconsumed-grant retrieval shape
  required by the accepted manifest contract.

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
- Every physical input line, including a blank line, malformed final line
  without LF, invalid UTF-8 line, or over-limit line, produces exactly one
  response line. A blank or unparseable line returns sanitized
  `invalid_request` with `request_id="unknown"`. EOF with zero pending bytes
  produces no response.
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
identity, contract-manifest/artifact digest, canonicalization identity, preview
policy, and visible-effect policy in `capabilities` before any candidate
operation.

## 7. Canonical Schema Set

The future implementation owns exactly these Core-canonical files:

```text
contracts/ingestion/v1/
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
  authorization-reference.schema.json
  grant-retrieval.schema.json
  error.schema.json
```

`contract-manifest.json` lists the protocol, exact supported versions,
transport, installed executable identity, six executable operations, exact
blocked operations `submit_ingest` and `retrieve_candidate_grant`, the 15
contract file names, canonicalization identity, digest domains, artifact
identity rules, privacy flags, and `submit_available=false`.

Every schema uses JSON Schema draft 2020-12, a stable local `$id`, explicit
required fields, bounded strings/arrays, and `additionalProperties=false` at
every protocol-owned object level. The worker may implement validators with
the Python standard library, but parity tests must prove that runtime behavior
matches the canonical schemas.

The canonical source tree is Core-only. A separately authorized Core release
packages these exact bytes plus `contract-manifest.json` and a SHA-256 checksum
as immutable artifact `eidetic-ingestion-contract-1.0`. The artifact identity
is the contract-manifest digest; repackaging different bytes under the same
identity is forbidden.

Phase 2 SDK work may generate typed models from a pinned artifact and commit
the derived code with the artifact version/digest in its generated header. It
must not copy the canonical schema tree or require a Core checkout at build or
runtime. SDK CI fetches or receives the pinned immutable artifact explicitly
and runs conformance fixtures against it. Phase 1 neither publishes that
artifact nor changes the SDK repository.

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

Identifiers use explicit ASCII patterns. A protocol timestamp is either JSON
`null` where explicitly nullable or exactly `YYYY-MM-DDTHH:MM:SSZ`: UTC only,
four-digit year, two-digit components, second precision, no fractional seconds,
no offset spelling, and a real calendar instant. An importer with finer source
precision must convert it under its declared pipeline revision before building
the candidate. When both source timestamps are present,
`source_created_at <= source_updated_at`. Protocol objects contain no
floating-point numbers; counts and offsets are non-negative integers.

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

source_object_claim_digest = SHA256(
  "eidetic.ingestion/v1/source-object-claim\0" ||
  canonical_source_object_claim_bytes
)

logical_target_id = "ing1-" || source_object_claim_digest

target_state_digest = SHA256(
  "eidetic.ingestion/v1/target-state\0" || canonical_target_state_bytes
)

visible_effect_digest = SHA256(
  "eidetic.ingestion/v1/visible-effect\0" || canonical_visible_effect_bytes
)

preview_binding_digest = SHA256(
  "eidetic.ingestion/v1/preview-binding\0" || canonical_preview_binding_bytes
)

preview_token_digest = SHA256(
  "eidetic.ingestion/v1/preview-token\0" || opaque_preview_token_utf8
)

grant_token_digest = SHA256(
  "eidetic.ingestion/v1/grant-token\0" || opaque_grant_token_utf8
)

grant_binding_digest = SHA256(
  "eidetic.ingestion/v1/grant-binding\0" || canonical_grant_binding_bytes
)
```

`canonical_content_bytes` contains exactly `title`, `body`, `card_kind`, and
`evidence`; it excludes the caller-declared `content_digest` and therefore is
not self-referential. `canonical_source_object_claim_bytes` contains exactly
the authenticated `principal_ref`, `scope_id`, `source_system`, and stable
`source_object_id`. Caller-declared `content_digest`, `chunk_ledger_digest`,
candidate digests in a manifest, and source-span digests are recomputed from
the bytes Core actually receives and must match.

`source_digest` and each caller-declared `chunk_text_digest` are structurally
validated but cannot be recomputed because Phase 1 never receives the complete
importer-owned source/chunk snapshot. Core recomputes the chunk-ledger digest
over the declared normalized outcome records; doing so proves ledger integrity,
not chunk-text truth. Every applicable validation/preview result therefore
returns all three exact flags:

```text
source_snapshot_verified=false
chunk_text_digests_verified=false
declared_coverage_only=true
```

It also returns `semantic_support_verified=false`. No Phase 1 response may
describe source/chunk bytes or support coverage as independently verified.

The request candidate never contains `candidate_digest`, and the request
manifest never contains its own `manifest_digest`. Core returns those
authoritative values beside the normalized object. A frozen importer bundle
may store that returned value externally, but inserting it into the canonical
object is invalid.

The reported contract-manifest/artifact digest is computed over the path-sorted
15-file canonical contract set as
`<relative-path> NUL <exact-file-bytes> LF`. No contract file contains that
aggregate digest, so the bundle identity is non-circular.

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
    "source_created_at": null,
    "source_updated_at": null,
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
          "end_char": 49,
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

`source_created_at` and `source_updated_at` are always present and independently
nullable. A source that does not expose either value sends `null`; omission,
empty strings, offsets, fractional seconds, or invented current time are
invalid. Both fields participate in canonical candidate and manifest digests.

### 9.2 Source-support semantics

Each factual claim has at least one support reference. Core verifies:

- document revisions match the candidate;
- offsets are non-negative and `end_char > start_char`;
- `end_char - start_char` equals the Unicode-scalar length of normalized
  `span_text`, not its UTF-8 byte length;
- span digests match the normalized span bytes;
- IDs are unique within their owning arrays; and
- no physical path, provider route, credential, raw grant, or policy verdict
  appears in a protocol-owned field.

Standalone candidate validation cannot prove that a referenced chunk exists or
that a span lies within it. Complete manifest validation additionally requires
`chunk.start_char <= support.start_char < support.end_char <= chunk.end_char`
for the exact referenced `chunk_id`. A source span that crosses a chunk boundary
must be represented as two or more support rows, one wholly contained in each
referenced chunk; a single cross-chunk row is invalid.

Phase 1 does not possess the importer source snapshot and does not claim that a
span occurs at the asserted offset or semantically supports the claim. The
validation result explicitly reports `source_snapshot_verified=false`,
`chunk_text_digests_verified=false`, `declared_coverage_only=true`, and
`semantic_support_verified=false`. Importer Phase 3 must prove those properties
before a real manifest may be considered for durable submit.

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

Within one manifest, `(document_key, logical_card_id)` maps one-to-one to one
`(source_system, source_object_id)` pair and therefore one Core-derived
`logical_target_id`. Duplicate aliases, one logical card mapped to multiple
source objects, or multiple logical cards mapped to the same source-object
claim are invalid. This rule is evaluated with the authenticated principal and
scope supplied by Core, not a caller-authored principal assertion.

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

`chunk_text_digest` is importer-declared evidence. Phase 1 validates its shape
and binds it into the recomputed chunk-ledger/manifest digests, but does not
claim to have recomputed it from absent chunk bytes.

The ordered offsets must start at zero, be contiguous and non-overlapping, and
end at `normalized_text_length`. Duplicate/missing chunk IDs, gaps, overlaps,
unreferenced candidate entries, multiply referenced candidate entries, or a
candidate that cites an unknown chunk make the manifest invalid.

Every candidate support row must also be wholly contained within the exact
referenced chunk range. A row outside that range or spanning two chunk outcomes
invalidates the manifest even when total chunk coverage is otherwise complete.

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

Target inspection is read-only. The worker must not create the target root or
parent directories in order to inspect or preview it.

### 11.1 Logical target identity and canonical state

Core derives `source_object_claim_digest` and `logical_target_id` exactly as in
Section 8.3 from the authenticated principal, configured scope, and candidate
source system/object. The request cannot choose either value. Lineage
predecessor IDs resolve within the candidate's `document_key` through the same
Core-owned resolver.

The resolver returns a path-free object with exactly these keys:

```json
{
  "target_state_version": "1.0",
  "logical_target_id": "ing1-64-lowercase-hex",
  "source_object_claim_digest": "64-lowercase-hex",
  "presence": "absent",
  "read_state": "not_found",
  "provenance_marker_state": "not_present",
  "source_revision": null,
  "candidate_digest": null,
  "content_digest": null,
  "lifecycle_state": null,
  "durable_object_digest": null
}
```

The canonical enums are:

- `presence=absent|present`;
- `read_state=not_found|readable|unreadable`;
- `provenance_marker_state=not_present|valid|missing|invalid`; and
- a non-null `lifecycle_state=active|superseded|archived|alternate`.

For `absent`, the exact combination is `not_found`, `not_present`, and null for
all five state-value fields. For a ready `present` target, the exact combination
is `readable`, `valid`, and non-null values for source revision, candidate,
content, lifecycle, and exact durable-object byte digest. A present target that
is unreadable or has a missing/invalid provenance marker remains representable
with only provable fields populated, but it is conflict-only and cannot produce
a ready preview or token. Every absent or present object is canonicalized and
hashed as `target_state_digest`; an absent digest therefore remains bound to
its logical target rather than being a global constant.

No physical path, inode, database key, or target bytes appear in this object.
The Core resolver may supply synthetic states directly in Phase 1 tests; the
worker does not define or write the future durable target format.

### 11.2 Deterministic action derivation

Core applies these rows in order after candidate/manifest validation and policy
matching:

| Condition | Effect | `mutation_expected` |
| --- | --- | --- |
| Ready present target has the exact candidate digest and source revision | `no_change` | false |
| `create`; target absent | `create` | true |
| `create`; target present with a different digest | `conflict` (`target_conflict`) | false |
| `retain`; ready present target, one matching predecessor, matching expected predecessor revision, and identical content digest | `retain` | false |
| `update`, `supersede`, or `archive`; ready present target, one matching predecessor, and matching expected predecessor revision | requested action | true |
| `alternate`; new target absent and its one predecessor resolves to a ready present target with matching expected predecessor revision | `alternate` | true |
| `split`; complete manifest context, every sibling output target absent or exact replay, and the one predecessor is ready/matching | `split` for each non-replay output | true |
| `merge`; complete manifest context, output target absent or exact replay, and every unique predecessor is ready/matching | `merge` for the non-replay output | true |
| Standalone `split`/`merge`, missing predecessor, target-state mismatch, or expected predecessor mismatch | `conflict` | false |

A same source claim/revision with a different candidate digest is
`source_conflict` before action derivation. An unreadable target, invalid/missing
marker, principal/scope/policy mismatch, ambiguous logical identity, or changed
predecessor state is `target_conflict`. No conflict returns a token. A manifest
sorts predecessor target bindings by `logical_target_id`, while candidate
effects remain in manifest-entry order.

### 11.3 Canonical owner-visible effect

Every ready non-conflict preview returns a `visible_effect` with exactly this
protocol-owned shape (array members retain candidate order):

```json
{
  "visible_effect_version": "1.0",
  "action": "create",
  "mutation_expected": true,
  "principal_ref": "core-principal-ref",
  "scope_id": "imported-book-pilot",
  "policy": {
    "policy_id": "core-policy-id",
    "policy_version": "policy-version",
    "policy_digest": "64-lowercase-hex"
  },
  "candidate_digest": "64-lowercase-hex",
  "manifest_digest": null,
  "target_bindings": [
    {
      "logical_target_id": "ing1-64-lowercase-hex",
      "target_state_digest": "64-lowercase-hex"
    }
  ],
  "visible_card": {
    "logical_card_id": "source-scoped-logical-id",
    "source": {
      "source_system": "document-importer",
      "source_object_id": "book-key:logical-card-id",
      "source_revision": "revision-identity",
      "source_created_at": null,
      "source_updated_at": null,
      "source_digest": "64-lowercase-hex"
    },
    "title": "Specific title",
    "body": "Self-contained atomic knowledge.",
    "card_kind": "concept",
    "evidence": "observed",
    "content_digest": "64-lowercase-hex",
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
            "end_char": 49,
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
}
```

`manifest_digest` is null for standalone preview and required for a manifest
candidate effect. `target_bindings` contains the output target and all lineage
predecessor targets, sorted by logical target ID, with no duplicate. The object
is canonicalized and hashed as `visible_effect_digest` under
`visible_effect_policy=eidetic.visible-effect-json.v1`.

This structured object is the exact Phase 1 owner-visible effect. It is not a
claim about future Markdown/file bytes and does not freeze a durable renderer.
A conflict instead returns `visible_effect=null`, a bounded conflict code and
the non-secret logical identities/digests Core could prove; it returns no
visible-effect digest or token.

## 12. Operation Semantics

### 12.1 `capabilities`

Payload is empty. Result includes:

- protocol/current/supported versions;
- Core build, contract-manifest, and immutable artifact identities;
- canonicalization, preview-policy, and visible-effect-policy identities;
- six executable operations;
- `blocked_operations=["submit_ingest","retrieve_candidate_grant"]` in that
  exact order;
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
- exact `source_snapshot_verified=false`,
  `chunk_text_digests_verified=false`, `declared_coverage_only=true`, and
  `semantic_support_verified=false` flags in Phase 1; and
- `write_authority=false`.

Semantic invalidity is a validation result, not a traceback or unsanitized
envelope failure.

### 12.4 `validate_manifest`

Payload contains one complete manifest. Core recomputes every candidate,
content, support-span, declared chunk-ledger, and manifest digest it can prove.
It never reports source/chunk snapshot verification. The result includes the
four exact verification flags, candidate/chunk accounting, support-to-chunk
containment, one-to-one logical identity accounting, and ordered findings.
Missing or inconsistent completeness evidence returns `status=invalid`.

### 12.5 `preview_ingest`

Core first performs candidate validation, then inspects the logical target
read-only and applies Sections 11.1-11.3. A ready result returns:

- `create`, `retain`, `update`, `split`, `merge`, `supersede`, `archive`,
  `alternate`, or `no_change` as the canonical action;
- the exact path-free canonical target binding objects;
- the complete canonical structured `visible_effect` and
  `visible_effect_digest`;
- normalized owner-visible card, claim, and bounded source-support detail;
- current principal/scope/policy bindings;
- explicit no-write/no-approval/no-grant flags; and
- a `preview_binding_digest`, `preview_token_digest`, and non-authorizing opaque
  preview token only when `preview_status=ready`.

A conflict returns `visible_effect=null`, a bounded owner-visible conflict
descriptor, and no visible-effect digest or token. Preview never reserves a
target or changes its state. Standalone `split` or `merge` is conflict-only
because exact sibling/predecessor reconciliation requires manifest context.

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

Each candidate effect uses Section 11's exact action and visible-effect rules.
The candidate token inside a manifest preview binds both candidate and manifest
digests. The manifest token additionally binds the ordered list of candidate
preview-binding digests in manifest-entry order. If any candidate is invalid,
unresolved, or conflicting, the manifest preview is not ready and no candidate
or manifest token is returned.

## 13. Non-authorizing Preview Tokens

Phase 1 preview tokens are integrity references, not grants. The exact
`canonical_preview_binding_bytes` object has these keys:

```json
{
  "preview_binding_version": "1.0",
  "binding_kind": "candidate",
  "protocol": "eidetic.ingestion",
  "protocol_version": "1.0",
  "canonicalization_identity": "eidetic.canonical-json.v1",
  "visible_effect_policy": "eidetic.visible-effect-json.v1",
  "worker_session_id": "ephemeral-session-id",
  "principal_ref": "core-principal-ref",
  "scope_id": "imported-book-pilot",
  "policy": {
    "policy_id": "core-policy-id",
    "policy_version": "policy-version",
    "policy_digest": "64-lowercase-hex"
  },
  "candidate_digest": "64-lowercase-hex",
  "manifest_digest": null,
  "target_bindings": [
    {
      "logical_target_id": "ing1-64-lowercase-hex",
      "target_state_digest": "64-lowercase-hex"
    }
  ],
  "visible_effect_digest": "64-lowercase-hex",
  "candidate_preview_binding_digests": [],
  "expires_at": "2030-01-01T00:05:00Z",
  "nonce": "base64url-nonce"
}
```

For `binding_kind=candidate`, `candidate_digest`, `target_bindings`, and
`visible_effect_digest` are non-null/nonempty; `manifest_digest` is null only
for standalone preview; and `candidate_preview_binding_digests` is empty. For
`binding_kind=manifest`, `candidate_digest` and `visible_effect_digest` are
null, `target_bindings` is empty, `manifest_digest` is required, and
`candidate_preview_binding_digests` is the nonempty manifest-entry-ordered list
of candidate binding digests. All fields are required, including nullable and
empty-array fields, so no omitted-default ambiguity exists.

The public `preview_binding_digest` is deterministic for these canonical
bytes. The opaque ASCII token representation is exactly
`epv1.<base64url-no-pad(canonical-bytes)>.<base64url-no-pad(HMAC-SHA256(session-secret,"eidetic.ingestion/v1/preview-token\\0" || canonical-bytes))>`.
The worker returns its domain-separated `preview_token_digest` beside it.
Production session secret, session ID, nonce, and clock are memory-only; tests
inject fixed values for exact goldens.

The token is not persisted, cannot be supplied to any executable Phase 1
operation to obtain authority, and becomes unverifiable when the process exits.
Any changed candidate, manifest, scope, principal, policy, ordered target state,
visible effect, session, expiry, or nonce changes the binding and invalidates
the old preview.

### 13.1 Reserved submit and exact-grant retrieval wire contract

`request.schema.json` has exactly eight operation discriminators: the six
executable operations plus schema-valid blocked `submit_ingest` and
`retrieve_candidate_grant`. This makes the blocked shapes reviewable without
making them dispatchable. Unknown aliases remain `unsupported_operation`.

The exact future `submit_ingest` payload has these seven required fields and
`additionalProperties=false`:

| Field | Exact type/meaning |
| --- | --- |
| `candidate` | one complete `candidate.schema.json` object |
| `candidate_digest` | lower-case 64-character SHA-256 hex, recomputed in a future write phase |
| `manifest_digest` | lower-case 64-character SHA-256 hex, or null only for a separately approved standalone candidate |
| `preview_token` | exact `epv1` ASCII token, 1-8,192 characters |
| `authorization_reference` | exact `authorization-reference.schema.json` object defined below |
| `grant_token` | opaque Core-issued ASCII token, 1-8,192 characters |
| `idempotency_key` | caller-generated printable ASCII identifier, 1-128 characters |

This shape freezes inputs only. Phase 1 never canonicalizes or submits it after
recognizing the blocked operation.

The exact reserved `retrieve_candidate_grant` payload is:

```json
{
  "scope_id": "imported-book-pilot",
  "approval_ref": "non-authorizing-approval-ref",
  "grant_ref": "non-authorizing-grant-ref",
  "manifest_digest": "64-lowercase-hex",
  "candidate_digest": "64-lowercase-hex",
  "candidate_preview_token_digest": "64-lowercase-hex"
}
```

The authenticated principal comes only from the Core-owned session. A future
successful result is reserved as:

```json
{
  "authorization_reference": {
    "approval_ref": "non-authorizing-approval-ref",
    "grant_ref": "non-authorizing-grant-ref",
    "grant_binding_digest": "64-lowercase-hex",
    "candidate_preview_token_digest": "64-lowercase-hex",
    "grant_token_digest": "64-lowercase-hex"
  },
  "grant_token": "opaque-core-grant-token",
  "expires_at": "2030-01-01T00:05:00Z",
  "consumption_state": "unconsumed"
}
```

The retrieval payload and result both have all displayed fields required and
`additionalProperties=false`. Scope uses the canonical scope-ID pattern;
approval/grant references are opaque printable ASCII strings of 1-256
characters; every digest is lower-case 64-character SHA-256 hex; the raw grant
token is 1-8,192 printable ASCII characters; and `expires_at` uses the exact
timestamp grammar from Section 8.1.

`authorization_reference.schema.json` is precisely the five-field object shown
in both wire examples. It is non-authorizing and safe for an SDK journal. The
Core-owned `canonical_grant_binding_bytes` behind its digest contains exactly:

```json
{
  "grant_binding_version": "1.0",
  "principal_ref": "core-principal-ref",
  "scope_id": "imported-book-pilot",
  "approval_ref": "non-authorizing-approval-ref",
  "grant_ref": "non-authorizing-grant-ref",
  "manifest_digest": "64-lowercase-hex",
  "candidate_digest": "64-lowercase-hex",
  "candidate_preview_token_digest": "64-lowercase-hex",
  "source_object_claim_digest": "64-lowercase-hex",
  "target_bindings": [
    {
      "logical_target_id": "ing1-64-lowercase-hex",
      "target_state_digest": "64-lowercase-hex"
    }
  ],
  "policy": {
    "policy_id": "core-policy-id",
    "policy_version": "policy-version",
    "policy_digest": "64-lowercase-hex"
  },
  "expires_at": "2030-01-01T00:05:00Z",
  "nonce": "base64url-candidate-nonce"
}
```

The Core approval record, not the request, binds the grant to the authenticated
principal, scope, manifest/candidate/preview digests, source-object claim,
ordered target states, policy, expiry, and unique candidate nonce. The SDK may
persist only `authorization_reference`, never `grant_token`.

A future repeated retrieval by the same authenticated principal returns the
same exact unconsumed grant record, token digest, token, and original expiry; it
does not mint, rotate, extend, or substitute authority. Missing, cross-principal,
cross-scope, mismatched, consumed, or post-`INTENT` retrieval fails closed with
`permission_denied`. Expiry before `INTENT` returns `preview_stale` and requires
a new preview/approval. After `INTENT`, only future receipt recovery with the
original idempotency key governs the outcome.

In Phase 1, both schema-valid blocked operations return `permission_denied`
after envelope/version and operation-specific structural schema validation but
before candidate canonicalization, payload semantic interpretation, target
inspection, token lookup/generation, or any allocation. Their result is always
null, and no success result above can be emitted. Executing either operation
requires a later reviewed protocol version and implementation phase.

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

Each exact, structurally schema-valid blocked operation always returns
`permission_denied` at the boundary defined in Section 13.1. Neither can
allocate a token, grant, key, target, file, directory, database, cache, or log
entry. A malformed blocked-operation envelope/payload remains
`invalid_request`; it is never partially interpreted.

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

The parent test harness creates all synthetic fixtures before the baseline
snapshot, then launches a child with an absolute bootstrap path using
`python3 -I -B -S`. It sets `PYTHONDONTWRITEBYTECODE=1` and disposable, initially
empty `HOME`, `TMPDIR`, and `XDG_CACHE_HOME`; those roots are part of the
snapshot, not a write allowlist. The bootstrap mode lives inside
`tests/test_ingestion_no_write.py`, installs write/network/subprocess/import
traps first, and only then loads the worker with `runpy.run_path`. No worker
module may be imported before the traps are active.

The before/after snapshot covers the Core source tree excluding `.git`, the
canonical contract tree, the selected source or installed worker/contract
tree, runtime root, all target/memory/index/SDK/importer/provider canaries, and
the disposable home/temp/cache roots. The child returns protocol bytes only in
captured pipes; the parent writes evidence outside all snapshotted roots only
after equality is proven. The identical bootstrap/snapshot path is mandatory
for a separately authorized installed-runtime smoke.

Any unexpected `open` in write/append/create/read-write mode, `os.open` write
flag, bytecode/cache/temp creation, `mkdir`, SQLite writable connection, atomic
replace, fsync, subprocess/network/provider call, or import of an SDK/importer/
provider/MLX module fails the test. This catches startup/import-time writes as
well as operation-time writes.

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
  typed preview support only after Phase 1 acceptance and only from a pinned
  immutable Core contract artifact/digest.
- The SDK has no Core checkout/submodule/path dependency, and Core has no SDK
  package dependency. Generated SDK types are derived release output, never a
  second canonical contract source.
- A future `eidetic-importer` remains a separate repository/package above the
  SDK boundary; book parsing, LLM/provider execution, and local MLX atomization
  do not enter Core or SDK Phase 1/2.
- Existing `remember.py`, compound, M1/M2/M3, hooks, MCP, topic-base, and
  installer behavior remain unchanged except for a separately reviewed
  additive installer copy block if that file is authorized.
- The legacy importer remains intact and cannot discover the new worker through
  an implicit redirect.

## 18. Future Implementation Allowlist

A later owner GO may authorize only this reviewed Core file set:

```text
contracts/ingestion/v1/*.json
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
ingestion contracts with existing atomic-install semantics; it may not deploy,
package an SDK, invoke an importer, or enable a scope during source
implementation tests.

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
4. Add canonical contracts, disabled worker, fixtures, and tests only.
5. Run source tests and no-write evidence.
6. Conduct post-implementation review.

### Installed runtime

No source implementation GO implies installed deployment. A separate
deployment GO must name the installed root, backup/parity commands, and exact
additive files. Installed capabilities must remain preview-only and
`submit_available=false`. Contract-artifact packaging is also separately
authorized: source bytes, artifact bytes, installed bytes, and reported digest
must match exactly before an SDK may pin that artifact.

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
- copy the canonical Core contract tree into `eidetic-sdk` or require a Core
  checkout/submodule/relative path there;
- load MLX or call an LLM/provider;
- weaken strict schema, digest, chunk completeness, or source-support shape;
- use the dirty primary worktree for implementation;
- capture M3/preview/YouGile changes; or
- install, publish, tag, push, or release without a separate GO.

## 22. Review Gate

Pre-implementation review must return `CLEAN` with no P0/P1 finding for:

- protocol and canonical contract completeness;
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
