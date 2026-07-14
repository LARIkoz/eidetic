# Eidetic Core integration contracts v1

ADR 0003 defines two typed surfaces. Only the Engine surface is executable in
the first extraction. ADR 0004 accepts the Core-owned durable-ingestion policy
boundary; the ingestion surface remains non-executable and fail-closed until
all ADR 0004 readiness gates pass.

## Engine protocol

- Protocol identifier: `eidetic.engine`
- Current version: `1.0`
- Transport: UTF-8 JSON Lines over local stdin/stdout
- Process model: long-lived Core worker, one response for every request
- Canonical schemas: `schemas/sdk/engine/v1/`

### Request envelope

```json
{
  "protocol": "eidetic.engine",
  "version": "1.0",
  "request_id": "caller-generated-id",
  "operation": "capabilities",
  "payload": {}
}
```

### Response envelope

```json
{
  "protocol": "eidetic.engine",
  "version": "1.0",
  "request_id": "caller-generated-id",
  "ok": true,
  "result": {},
  "error": null
}
```

An error response sets `ok` to `false`, `result` to `null`, and returns a
sanitized error object with `code`, `message`, `retryable`, and optional public
details. Tracebacks, filesystem paths, credentials, raw source content, and
provider identifiers are never returned.

### Engine operations

| Operation | Side effect | Promise |
| --- | --- | --- |
| `capabilities` | none | Protocol/Core/Engine versions, operations, model identity, session identity |
| `health` | none | Runtime and logical-index availability without creating an index |
| `reconcile` | none | Compare source-owned records with a derived index through a strict read-only connection |
| `sync` | derived index only | Incrementally embed missing/changed records; an explicit `force` rebuilds the supplied scope; delete orphans only for an explicit complete snapshot |
| `search` | none | Drift-guarded vector hits for a logical index; empty hits carry explicit degradation state |
| `rerank` | none | Scores aligned to caller document IDs; unavailability is explicit |

`index_id` is an opaque logical identifier matching
`^[a-z][a-z0-9._-]{0,63}$`. It is not a path. Core resolves it using
Core-owned deployment configuration.

Each record contains:

- `record_id`: stable source-owned chunk identity;
- `object_id`: source object identity used to fold hits;
- `title`: display/context title;
- `kind`: source-owned category such as `task` or `chat`;
- `text`: normalized text to embed.

Core computes the canonical content hash and internal row identity. Requests do
not contain embeddings, database row IDs, table names, or lock handles.

### Freshness receipts

`reconcile` and `sync` return a provider-neutral receipt containing:

- receipt and logical index identities;
- complete or partial snapshot scope;
- deterministic source digest;
- expected and indexed counts;
- missing, changed, orphaned, upserted, and deleted counts;
- `fresh`, `stale`, or `degraded` status;
- Engine API/model stamp and UTC observation time;
- a bounded repair operation when not fresh.

A complete snapshot is fresh only when missing, changed, and orphaned counts
are all zero. A partial snapshot never authorizes deletion. Repeating a sync
with the same record identities and content is idempotent: it produces zero new
upserts and the same source digest.

### Timeouts and retries

- Default request timeout: 120 seconds for metadata/read operations.
- Sync timeout is caller-configured and may be longer; timeout does not imply
  that a write was rolled back, so the SDK must reconcile before retrying.
- Retry only `core_busy`, `timeout`, and explicitly retryable
  `engine_unavailable` errors.
- `invalid_request`, `incompatible_version`, and `index_not_found` are not
  retried without a changed request or operator action. Durable-ingestion
  terminal outcomes such as `policy_rejected` and `idempotency_conflict` enter
  owner resolution rather than this error retry path.
- The Core build lock serializes index writers. A lock conflict returns
  `core_busy` and never falls back to an unlocked write.

### Privacy and provenance

The protocol is local stdio and does not create a network listener. Logs and
errors contain logical IDs and counts, not raw record text. Source text is sent
only to the local Core worker for embedding and is not copied to the Core memory
store. Receipts contain no credentials or provider routes.

## Stable transport and precondition error taxonomy

The executable Engine surface and future ingestion surface share the envelope.
The retry action below never authorizes a durable-ingestion resubmit with a new
idempotency key. Unknown ingestion outcomes reconcile through `get_receipt`
first.

| Code | Retry action | Meaning |
| --- | --- | --- |
| `invalid_request` | no | Envelope, field, record, or operation is invalid |
| `incompatible_version` | no | Protocol major or required capability is unsupported |
| `unsupported_operation` | no | Operation is outside the selected typed surface |
| `index_not_found` | no | Read requested for an absent logical derived index |
| `core_busy` | same request | Core-owned lock or recovery barrier is held |
| `engine_unavailable` | conditional | Model/runtime cannot complete the requested operation |
| `stale_index` | repair first | Read is explicitly degraded because stamps or source state are stale |
| `timeout` | reconcile first | Caller deadline expired; reconcile Engine sync or ingestion receipt before retrying |
| `permission_denied` | operator/config change | Requested operation or scope is not authorized by Core configuration |
| `preview_stale` | new preview/grant, same key | Ingestion preview, policy, lineage, or expected target binding is no longer current |
| `incomplete_operation` | Core reconciliation | A durable ingestion intent exists but cannot yet be finalized safely; the target remains blocked |
| `receipt_not_found` | new preview/grant, same key | After the recovery barrier, Core proved that no durable intent, receipt, alias, attempt resolution, conflicting key binding, or in-flight operation exists for the key and attempted candidate digest |
| `internal_error` | reconcile first | Sanitized unexpected Core failure; for ingestion it does not prove that no write occurred |

### Durable-ingestion terminal outcomes

`submit_ingest` returns `ok=true` with a durable delivery resolution whenever
the request reaches one of these terminal outcomes. Callers inspect `outcome`;
they must not interpret envelope success as content acceptance.

| Outcome | `checkpoint_eligible` | Meaning |
| --- | --- | --- |
| `accepted` | yes | Durable content commit or idempotent replay of that commit |
| `policy_rejected` | no | Core policy reached a terminal rejection for this keyed delivery |
| `idempotency_conflict` | no | The key or same-revision claim is bound to a different candidate digest |
| `target_conflict` | no | Target state, logical identity, or source revision lineage conflicts with the candidate |

Owner resolution is separate from submit outcome. An explicit Core-issued skip
can set `cursor_advance_eligible=true` for one source object/revision, but it
does not change the terminal outcome or set `checkpoint_eligible=true`.
`get_receipt` returns a receipt view whose original delivery resolution remains
byte-for-byte immutable and whose `owner_resolutions` collection is separately
append-only.

## Compatibility guarantees

Protocol `MAJOR.MINOR` is negotiated independently from Core product and Engine
API versions. Minor releases may add optional operations/fields. A required
field, removed operation, changed interpretation, or changed idempotency/write
semantics requires a major bump. SDK compatibility is checked before any index
is opened.

Core owns the schemas. SDK tests point to a selected Core checkout or installed
contract manifest; the SDK repository does not carry a forked schema copy.

## Future ingestion protocol

ADR 0004 is the binding decision for write authority, authorization grants,
idempotency aliases, prepared-commit recovery, and checkpoint eligibility.

The future protocol identifier is `eidetic.ingestion`; its first major version
must provide separate operations:

1. `capabilities`
2. `health`
3. `validate_candidate`
4. `preview_ingest`
5. `submit_ingest` with an idempotency key
6. `query` / `retrieve`
7. `get_receipt` with the original idempotency key and attempted candidate
   digest

Core authenticates sessions to a stable logical connector principal whose
idempotency namespace survives credential rotation. Core also maintains a
revision-independent source-object claim and validates source revision lineage;
an SDK assertion cannot make an older or unrelated revision current. For an
opaque revision, an expected-predecessor match is necessary but not sufficient
for unattended submit unless Core can independently verify successor ordering.

The SDK connector stages are discover, authorized fetch, normalize, validate,
preview/approval, then persist `PENDING` with the original idempotency key
before submit. Every terminal delivery resolution is persisted first as
`RECEIPT_DURABLE`. Only an accepted outcome with
`checkpoint_eligible=true` advances the normal source checkpoint and becomes
`CHECKPOINT_COMMITTED`; conflicts and rejections become
`RESOLUTION_PENDING` with the original key and receipt retained. A later
Core-issued owner-resolution record, retrieved from the append-only receipt
view, becomes `RESOLUTION_COMMITTED`; an explicit skip may separately authorize
cursor advancement without claiming content acceptance. Timeout, transport
loss, and `internal_error` require candidate-aware `get_receipt` with the
original key before any resubmit. Same-key/different-digest conflicts are
persisted per attempted digest without rebinding the original key. Core alone
evaluates admissibility, owns the ordered scope/claim/target locks, performs a
proven durable atomic replace, records policy identity, and returns durable
resolutions.

No executable ingestion worker or write stub ships in this extraction. That is
an intentional fail-closed state, not an unavailable feature hidden behind a
success response.
