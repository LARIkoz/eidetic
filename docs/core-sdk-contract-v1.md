# Eidetic Core integration contracts v1

ADR 0003 defines two typed surfaces. Only the Engine surface is executable in
the first extraction. The ingestion surface is a design contract and remains
fail-closed until a separate policy review.

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
- `invalid_request`, `incompatible_version`, `index_not_found`,
  `policy_rejected`, and `idempotency_conflict` are not retried without a
  changed request or operator action.
- The Core build lock serializes index writers. A lock conflict returns
  `core_busy` and never falls back to an unlocked write.

### Privacy and provenance

The protocol is local stdio and does not create a network listener. Logs and
errors contain logical IDs and counts, not raw record text. Source text is sent
only to the local Core worker for embedding and is not copied to the Core memory
store. Receipts contain no credentials or provider routes.

## Stable error taxonomy

| Code | Retryable | Meaning |
| --- | --- | --- |
| `invalid_request` | no | Envelope, field, record, or operation is invalid |
| `incompatible_version` | no | Protocol major or required capability is unsupported |
| `unsupported_operation` | no | Operation is outside the selected typed surface |
| `index_not_found` | no | Read requested for an absent logical derived index |
| `core_busy` | yes | Core-owned build lock is held |
| `engine_unavailable` | conditional | Model/runtime cannot complete the requested operation |
| `stale_index` | no | Read is explicitly degraded because stamps or source state are stale |
| `timeout` | yes | Caller deadline expired; reconcile before retrying a sync |
| `policy_rejected` | no | Future ingestion candidate was rejected by Core policy |
| `idempotency_conflict` | no | Future ingestion key was reused for a different candidate |
| `permission_denied` | no | Requested operation is not authorized by Core configuration |
| `internal_error` | no | Sanitized unexpected Core failure |

## Compatibility guarantees

Protocol `MAJOR.MINOR` is negotiated independently from Core product and Engine
API versions. Minor releases may add optional operations/fields. A required
field, removed operation, changed interpretation, or changed idempotency/write
semantics requires a major bump. SDK compatibility is checked before any index
is opened.

Core owns the schemas. SDK tests point to a selected Core checkout or installed
contract manifest; the SDK repository does not carry a forked schema copy.

## Future ingestion protocol

The future protocol identifier is `eidetic.ingestion`; its first major version
must provide separate operations:

1. `capabilities`
2. `health`
3. `validate_candidate`
4. `preview_ingest`
5. `submit_ingest` with an idempotency key
6. `query` / `retrieve`
7. `get_receipt`

The SDK connector stages are discover, authorized fetch, normalize, submit,
persist checkpoint plus terminal Core receipt, retry only retryable errors, and
surface conflicts/permanent failures. Core alone evaluates admissibility,
locks, writes atomically, records policy identity, and returns the terminal
receipt.

No executable ingestion worker or write stub ships in this extraction. That is
an intentional fail-closed state, not an unavailable feature hidden behind a
success response.
