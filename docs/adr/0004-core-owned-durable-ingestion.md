# ADR 0004: Keep durable ingestion Core-owned and receipt-driven

- Status: Accepted
- Date: 2026-07-14
- Owners: Eidetic Core maintainers
- Decision scope: durable external ingestion, write authority, recovery, and
  connector checkpoints

## Context

ADR 0003 separated Eidetic Core from the integration SDK and reserved
`eidetic.ingestion` as a future protocol. The first SDK extraction deliberately
ships no ingestion worker or write facade. That fail-closed state prevents a
connector from bypassing Core policy, file ownership, locking, provenance, or
truth-maintenance lifecycle.

Durable ingestion is different from Engine synchronization. Engine records are
rebuildable derived data selected by a logical `index_id`. An accepted
ingestion candidate changes Core-owned file truth and can trigger downstream
indexes, receipts, and lifecycle work. A timeout, process crash, duplicate
delivery, or conflicting replay therefore cannot be handled as an ordinary
best-effort SDK retry.

The boundary needs one decision before implementation: which component owns
admissibility and durable commit, what makes a submission idempotent, and what
evidence lets a connector checkpoint safely after an ambiguous failure.

This ADR refines the future-ingestion sketch in ADR 0003,
`core-sdk-contract-v1.md`, and `core-sdk-ownership.md`. It does not change the
already shipped `eidetic.engine` protocol.

## Decision

### Protocol and ownership

1. Durable ingestion is exposed only through a Core-owned local worker using
   protocol `eidetic.ingestion` version `1.x` over newline-delimited JSON.
   Ingestion and `eidetic.engine` remain separate typed surfaces even if they
   share framing, discovery, or process supervision.
2. The SDK owns connector workflow: discover, authorized fetch, normalize,
   validate, request preview, submit after explicit authorization, retrieve a
   receipt, and persist a source checkpoint. It never imports Core modules,
   resolves Core paths, evaluates admissibility, or writes Core storage.
3. Core owns capabilities, health, candidate validation, policy evaluation,
   preview binding, write authorization, locking, atomic file mutation,
   provenance, conflict handling, receipt reconciliation, and lifecycle
   scheduling.
4. Version 1 provides these operations:
   `capabilities`, `health`, `validate_candidate`, `preview_ingest`,
   `submit_ingest`, `get_receipt`, `query`, and `retrieve`.
5. `submit_ingest` is absent or returns `permission_denied` until every
   readiness gate in this ADR is satisfied and Core configuration explicitly
   enables a named ingestion scope. Validation and preview confer no write
   authority.
6. A write-enabled worker authenticates a Core-issued `connector_principal`
   from its session capability or equivalent Core-controlled local transport.
   The SDK cannot establish identity with `source_system`, another payload
   field, or a self-declared principal. Core, not the SDK, starts or unlocks a
   write-enabled session for that principal and scope.

### Candidate identity and normalization

The SDK sends a normalized, provider-neutral candidate containing:

- `source_system`, `source_object_id`, and `source_revision`;
- normalized title and body;
- source-created and source-updated timestamps when available;
- provenance references and source/content digests;
- the requested logical ingestion scope; and
- optional source-owned metadata allowed by the scope schema.

The candidate never contains a Core filesystem path, database path, table
name, internal row identifier, lock handle, provider route, credential, or
policy verdict. Core canonicalizes the candidate with a versioned algorithm and
computes the authoritative `candidate_digest`. A change to canonicalization or
idempotency semantics requires an ingestion protocol major version.

Core also computes a `source_claim` from the authenticated connector principal,
scope, source system, source object, and source revision. Under the scope lock,
the same claim and candidate digest collapses to the original receipt even if
independent deliveries used different idempotency keys. Before answering a
delivery that used a new key, Core appends and flushes an immutable alias from
that key to the canonical operation and candidate digest. The same claim with
a different digest is a conflict and never causes a second silent mutation.

### Validate, preview, and submit authority

Durable ingestion is a three-phase operation:

1. `validate_candidate` checks schema, size, provenance shape, requested scope,
   and current policy without writing durable content.
2. `preview_ingest` returns the proposed visible effect and a short-lived
   `preview_token` bound to the candidate digest, scope, policy identity and
   version, expected target state, and expiry. It does not reserve a path or
   authorize a later changed payload.
3. `submit_ingest` requires the unchanged candidate, a valid preview token, an
   opaque Core-issued authorization grant for that exact preview, and an
   idempotency key. Core revalidates identity, authorization, policy, and target
   state under the write lock before committing.

The grant is minted only by a Core-owned local approval surface after explicit
owner approval, or by a Core-configured unattended policy that already names
the authenticated principal and scope. It is bound to the principal, scope,
candidate digest, preview token, policy identity and version, expected target
state, expiry, and a unique nonce. It is integrity-protected, never derived
from an SDK assertion, and consumed once when Core durably records the
operation intent. Missing, forged, cross-principal, expired, or replayed grants
fail closed. A consumed grant remains reserved only for recovery of its
original idempotency key and cannot authorize another operation.

Expired previews or grants, changed policy, changed target state, changed
scope, or a candidate digest mismatch fail closed. The caller must obtain a
new preview and approval; Core never silently refreshes authorization during
submit.

### Idempotency and terminal receipts

Every submission has a caller-generated `idempotency_key` scoped to the
connector identity and ingestion scope. Core durably binds it to the canonical
candidate digest and an operation identifier before acknowledging success.

The SDK owns a durable state machine separate from the source checkpoint:

1. `PENDING`: before its first submit attempt, append and flush the mapping from
   connector principal, scope, source object and revision, and candidate digest
   to the original idempotency key.
2. `RECEIPT_DURABLE`: after submit or `get_receipt` returns a terminal delivery
   resolution, persist and flush the complete resolution before changing a
   source checkpoint or clearing pending state.
3. `CHECKPOINT_COMMITTED`: only for a checkpoint-eligible receipt, durably apply
   the source checkpoint and record this transition. If the journal and
   checkpoint cannot share one transaction, recovery verifies and completes
   the two idempotently in that order.
4. `RESOLUTION_PENDING`: for conflict or rejection, preserve the receipt and
   original key until an explicit owner resolution; do not advance the normal
   checkpoint.

The original key remains recoverable through `CHECKPOINT_COMMITTED` or owner
resolution. Only then may protected raw key material be compacted; its digest,
canonical receipt reference, and transition evidence remain durable. Sensitive
grant material is neither logged nor placed in the journal or checkpoint.

- The same key and same digest returns the same terminal receipt without a
  second content mutation.
- A different key for the same source claim and digest creates a durable
  delivery alias before Core returns the canonical outcome. A lost alias
  response is recoverable with `get_receipt` using that different key.
- The same key with a different digest returns `idempotency_conflict`.
- A timeout or lost response is an unknown outcome, not permission to submit a
  new key. After restart, the SDK recovers the pending mapping and calls
  `get_receipt` with its authenticated principal, scope, and original key;
  knowledge of a Core-generated operation identifier is optional.
- The SDK advances its normal source checkpoint only when a terminal receipt
  has `checkpoint_eligible=true`, which is limited to an accepted durable
  commit or an idempotent replay of that accepted commit. Retryable failures,
  conflicts, and rejections never advance it.
- A terminal conflict or rejection closes that idempotency key but remains in
  a separate durable resolution/dead-letter state until an owner explicitly
  resolves or skips it. Terminal does not by itself mean checkpoint-eligible.

Core separates one canonical operation receipt from per-delivery resolution
receipts. The canonical receipt includes a stable receipt and operation
identifier, canonical idempotency-key digest, candidate/content/source digests,
logical scope, policy identity and version, durable object identity, terminal
outcome, `checkpoint_eligible`, conflict or rejection classification, and
commit timestamp. A delivery resolution binds the requested key digest to the
canonical receipt identifier and candidate digest and says whether it is the
canonical key or an alias. `get_receipt` returns the same durable resolution
for either form of key. Receipts exclude raw credentials, provider routing,
private Core paths, and source content not needed for audit.

### Atomic commit and recovery

Every Core path that can modify a target admitted to an ingestion scope must
use one Core `DurableWriteCoordinator`. The coordinator owns the per-target
lock, scope recovery barrier, and operation-bound provenance marker. Existing
MCP, `remember.py`, importer, or other writers that have not migrated to this
coordinator exclude their targets from an enabled ingestion scope.

Before staging, Core computes an `operation_commitment_digest` from a canonical
encoding of the protocol major, operation identifier, authenticated-principal
fingerprint, scope, idempotency-key digest, source claim, candidate digest,
target identity, expected pre-write digest, policy identity/version,
preview-token digest, and authorization-nonce digest. The field set explicitly
excludes staged/final byte digests, the `INTENT` entry digest or offset,
timestamps, and receipt identifiers. This makes the commitment non-circular:
the staged marker and later `INTENT` both carry the already computed value,
while `INTENT` separately records the resulting staged/final byte digest.

Core performs a submission through the coordinator under one per-target lock:

1. recover every unresolved operation in the scope, then locate any existing
   idempotency-key or source-claim entry;
2. return the original terminal receipt for a matching key/claim and digest,
   first appending and flushing a delivery alias for a matching new key, or
   return the appropriate conflict for a mismatched digest, before applying
   current authorization or policy to a historical outcome;
3. for a new operation, revalidate principal, authorization grant, preview,
   policy, candidate digest, source claim, and expected target state;
4. render the exact final bytes into a private, same-filesystem staging file,
   including a canonical provenance marker bound to the operation identifier,
   candidate digest, and `operation_commitment_digest`, then flush the staged
   file;
5. append and flush an `INTENT` entry that binds the operation, key, source
   claim, candidate digest, operation commitment, target identity, staging
   identity and digest, expected pre-write digest, deterministic post-write
   byte digest, policy identity, and authorization nonce; recording `INTENT`
   consumes the grant and is the durable prepared-commit decision for that
   operation;
6. atomically replace the target with the staged file and flush the containing
   directory where the platform supports it;
7. append and flush `FILE_COMMITTED`, then append the provenance, canonical
   `TERMINAL` receipt, and delivery resolution; and
8. schedule rebuildable indexes and lifecycle work after file truth commits.

A crash after staging but before `INTENT` can leave an orphan staged file but
cannot mutate the target or consume the grant. Core quarantines or removes the
orphan only through an audited cleanup rule. Staged content is Core-private,
permission-restricted, absent from logs and shared caches, and retained until
its operation reaches a terminal state.

The durable file is the source of truth. A derived-index failure cannot turn a
committed file into an ingestion failure or cause the SDK to write it again.
The terminal receipt records downstream work as pending, complete, or
degraded, and Core owns repair.

The ledger is an append-only state machine: `INTENT`, optional
`FILE_COMMITTED`, then `TERMINAL`, plus immutable delivery aliases. The ledger
and file write cannot be assumed to share a database transaction. Core
therefore verifies the staged or durable target bytes, the pre-write and
post-write digests, and the operation-bound marker recorded in `INTENT`.
Before any new mutation of that target, and whenever `get_receipt` observes an
unresolved operation, the coordinator applies this recovery table under the
same lock:

| Durable evidence | Recovery decision |
| --- | --- |
| No `INTENT` | This operation performed no file mutation; quarantine any orphan stage. A new submit needs a currently valid preview and grant. |
| `INTENT`, target equals pre-write digest, stage and marker valid | Replace was not observed. Resume the prepared operation with the same staged bytes and key. Grant/preview expiry or ordinary policy drift after durable `INTENT` does not cancel it. |
| `INTENT`, target equals pre-write digest, stage absent or invalid | Return `incomplete_operation`, keep the target blocked, and require audited Core recovery; do not invent success or start another mutation. |
| `INTENT`, target equals post-write digest and marker matches | The file commit occurred. Append missing `FILE_COMMITTED` and accepted `TERMINAL` evidence; later policy drift is annotated and never rewrites the accepted outcome. |
| `INTENT`, target or marker matches neither recorded state | Another mutation won or evidence diverged. Append terminal `target_conflict`; do not overwrite. |
| `FILE_COMMITTED` without `TERMINAL` | Finalize and return the accepted receipt from recorded evidence without another file write. |
| `TERMINAL` | Return the original receipt byte-for-byte for that protocol version. |

An absent target is represented by a canonical pre-write sentinel, so create
and replace operations use the same table. Durable `INTENT` is the v1 boundary
after which ordinary policy or token expiry applies only to new operations;
an emergency scope quarantine may pause recovery but cannot silently rewrite
the prepared decision. Recovery must produce the original terminal success, a
terminal conflict/rejection, or a retryable incomplete state safe to resume
with the same key. It must never invent success from an absent or mismatched
file, duplicate a committed mutation, or apply current policy retroactively to
a prepared or committed operation.

Ledgers, receipts, and provenance evidence are append-only. Repair appends a
reconciliation record; it does not delete or rewrite historical evidence.

### Conflict and error semantics

Core never silently overwrites a different durable object. If the target has
changed since preview or another candidate claims the same logical identity,
Core returns a terminal conflict with evidence sufficient for owner review.
Merge, replace, supersede, or create-alternate behavior requires a separate
explicit policy decision and preview.

Errors are structured and sanitized. Version 1 distinguishes at least:

- non-retryable `invalid_request`, `incompatible_version`,
  `permission_denied`, `policy_rejected`, `preview_stale`,
  `idempotency_conflict`, and `target_conflict`;
- retryable `core_busy`; recoverable `incomplete_operation`, which blocks the
  target until Core reconciliation; and bounded transport or timeout failures
  that require receipt reconciliation before any resubmit; and
- sanitized `internal_error`, which does not imply that no write occurred.

If ingestion later invokes an external model or provider, Core uses shared
provider routing and the centralized `KeyPenaltyStore`. Provider names,
credentials, balances, and penalty state remain outside the protocol and do
not alter the provider-neutral terminal outcome vocabulary.

## Readiness gates

Executable `submit_ingest` remains disabled until all of these gates pass:

1. Canonical Core-owned JSON schemas, compatibility fixtures, and negative
   tests exist for every operation, error, preview, and receipt.
2. Ingestion scopes and write permission default to off, are inspectable in
   `capabilities`, and cannot be enabled by an SDK request. Tests reject
   missing, forged, expired, cross-principal, cross-scope, and replayed grants
   and prove one-time consumption under concurrency.
3. Candidate canonicalization, source-claim collapse, and idempotency conflict
   behavior have golden fixtures across the current and immediately previous
   supported SDK/Core pair. The non-circular operation-commitment field set has
   golden fixtures, and cross-operation marker substitution is rejected. Alias
   persistence is crash-tested before response, and `get_receipt` resolves
   both canonical and aliased delivery keys.
4. SDK crash tests cover every transition from `PENDING` through
   `RECEIPT_DURABLE`, `CHECKPOINT_COMMITTED`, or `RESOLUTION_PENDING`, including
   a lost response plus SDK restart and crashes on both sides of checkpoint
   commit. The original key remains recoverable for every unfinished state.
5. Crash-injection tests cover every boundary before and after staging-file
   flush, `INTENT`, atomic replace, directory flush, `FILE_COMMITTED`, terminal
   receipt, delivery alias, and lifecycle scheduling.
6. Concurrent duplicate, conflicting-key, stale-preview, changed-policy,
   changed-target, and same-source-claim tests prove single-mutation behavior.
7. Every Core path able to touch an ingestion-enabled target uses the same
   `DurableWriteCoordinator`, target lock, and recovery barrier. Tests prove
   that the operation-bound marker, not matching content bytes alone, is
   required to attribute a commit.
8. Startup and on-demand reconciliation implement every recovery-table row,
   block later scope writes until unresolved operations are classified, and
   prove pre-`INTENT`, prepared, and post-commit policy-drift behavior without
   deleting evidence.
9. Receipt and SDK tests prove that only accepted or idempotently replayed
   accepted outcomes are checkpoint-eligible; conflicts and rejections remain
   in the resolution/dead-letter state.
10. Provenance, privacy, and secret scans prove that staged content, receipts,
    logs, errors, pending journals, and checkpoints contain no credentials,
    provider internals, exposed Core paths, or raw authorization grants beyond
    the private Core staging area required for the prepared operation.
11. Live `capabilities` reports the running Core build identity, protocol
    version, canonical schema-manifest digest, privacy-safe session/principal
    attestation, effective scopes for that principal,
    `submit_requires_core_grant=true`, and policy/configuration digest.
    End-to-end tests verify that exact live worker through the public SDK and
    match it to the reviewed installed bundle; source/file parity alone is not
    accepted as runtime evidence.
12. A synthetic connector passes end-to-end first. A single real source may be
    piloted only after rollback and owner-visible conflict handling are proven.

## Rollout and rollback

Rollout is additive: schemas and a disabled worker surface first, then
synthetic validation/preview, then synthetic submit under an isolated scope,
then one explicitly enabled real-source scope. Existing `remember.py`, MCP,
and importer write paths are not silently redirected during this rollout; a
real target remains excluded until every writer for it explicitly adopts the
shared coordinator.

Rollback disables the ingestion scope and stops new submissions. It does not
delete committed Core files, receipts, ledgers, provenance, checkpoints, or
provider state. Any reversal of accepted content uses an existing Core-owned
reversible lifecycle operation and produces new evidence.

## Consequences

This design adds a ledger, preview binding, crash recovery, and more protocol
round trips. In return, connector retries cannot silently duplicate or
overwrite durable memory, policy remains enforceable at the write boundary,
and ambiguous failures are reconcilable without exposing Core storage.

Derived indexing becomes explicitly downstream of durable file truth. A
successful ingestion may therefore be returned with degraded downstream state;
Core, not the connector, repairs that state.

## Rejected alternatives

- **SDK writes files or calls `remember.py` directly.** This bypasses Core
  policy, atomicity, provenance, and recovery ownership.
- **One `ingest` call without preview.** Validation is not authorization, and a
  connector cannot safely infer the visible effect or target state.
- **Trust an SDK-supplied authorization assertion.** A connector cannot grant
  itself write authority; Core must authenticate the principal and issue the
  exact, integrity-protected, single-use grant.
- **Keep the idempotency key only in process memory.** A crash between submit
  and response would lose the only safe reconciliation handle and invite a
  duplicate key.
- **Return a canonical receipt for a new delivery key without persisting an
  alias.** A lost response would make that new key impossible to reconcile.
- **At-least-once submit with a new key after timeout.** This permits duplicate
  durable mutations when the response, rather than the write, was lost.
- **Attribute a commit from matching content bytes alone.** Existing writers or
  restored files can reproduce bytes; enabled targets require the shared
  coordinator and operation-bound provenance marker.
- **Clear pending SDK state before the source checkpoint is durable.** A crash
  in that gap loses the receipt/key needed to complete or audit the checkpoint.
- **Treat index success as the commit point.** Derived indexes are rebuildable
  and cannot define whether durable file truth exists.
- **Store raw source payloads or provider diagnostics in receipts.** This
  expands the privacy and credential surface without improving reconciliation.
- **Implement submit before crash and concurrency gates.** A disabled surface
  is safer than a nominal API whose success cannot be proven after failure.
