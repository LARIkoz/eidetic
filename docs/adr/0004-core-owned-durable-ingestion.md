# ADR 0004: Keep durable ingestion Core-owned and receipt-driven

- Status: Accepted
- Date: 2026-07-14
- Owners: Eidetic Core maintainers
- Decision scope: durable external ingestion, write authority, recovery, and
  connector checkpoints
- Clarified: 2026-07-14 — terminal wire outcomes, principal continuity,
  revision lineage, candidate-aware receipt recovery, durable-replace recovery,
  and owner-resolution cursors

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
7. `connector_principal` is a stable Core-owned logical identity, not a session
   credential or process identifier. Session credentials authenticate to that
   identity. Core-controlled credential rotation preserves the same principal
   identity and historical idempotency namespace. Revocation blocks new
   submissions but does not make historical receipts unreachable: Core provides
   an audited operator-recovery path without allowing the SDK to reassign or
   alias a principal.

### Candidate identity and normalization

The SDK sends a normalized, provider-neutral candidate containing:

- `source_system`, `source_object_id`, `source_revision`, and an expected
  predecessor revision when the source exposes lineage;
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

Core computes a revision-independent `source_object_claim` from the stable
connector principal, scope, source system, and source object. A `source_claim`
adds the source revision. The Core-owned scope schema defines how revisions are
ordered or related; the SDK cannot assert that one revision supersedes another.
For opaque revisions, an expected predecessor that matches Core's last durable
source-object receipt is necessary but never sufficient for unattended submit:
it proves serialization, not freshness. Unattended submit is allowed only when
the Core-owned scope schema can independently verify successor ordering or
equivalent source evidence. Otherwise even a matching predecessor requires
explicit owner approval, and an older, unrelated, or unprovable revision fails
closed as a conflict.

Under the source-object claim lock, the same source claim and candidate digest
collapses to the original operation even if independent deliveries used
different idempotency keys. Before answering a delivery that used a new key,
Core durably records an immutable alias from that key to the canonical
operation and candidate digest. This applies to terminal operations and to a
recoverable `incomplete_operation`; a lost alias response remains discoverable
through `get_receipt`. The same source claim with a different digest, or a
lineage regression for the same source-object claim, is a conflict and never
causes a second silent mutation.

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
state, expected source-object lineage state, expiry, and a unique nonce. It is
integrity-protected, never derived from an SDK assertion, and consumed once
when Core durably records the operation intent. Missing, forged,
cross-principal, expired, or replayed grants fail closed. A consumed grant
remains reserved only for recovery of its original idempotency key and cannot
authorize another operation.

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
5. `RESOLUTION_COMMITTED`: retrieve and persist a Core-issued owner-resolution
   record from the receipt view before applying its exact action. A replacement
   returns to preview/submit with a new authorization decision. An explicit
   skip may set
   `cursor_advance_eligible=true` for exactly one source object and revision;
   after persisting that receipt, the SDK may advance an ordered source cursor
   and must durably record the transition. A skip never changes the content
   outcome to accepted and never sets `checkpoint_eligible=true`.

The original key remains recoverable through `CHECKPOINT_COMMITTED` or owner
resolution. Only then may protected raw key material be compacted; its digest,
canonical receipt reference, and transition evidence remain durable. Sensitive
grant material is neither logged nor placed in the journal or checkpoint.

- The same key and same digest returns the same terminal receipt without a
  second content mutation.
- A different key for the same source claim and digest creates a durable
  delivery alias before Core returns the canonical outcome. A lost alias
  response is recoverable with `get_receipt` using that different key.
- The same key with a different digest durably appends and returns an
  `idempotency_conflict` resolution for that attempted digest. It never rebinds
  or overwrites the key's original candidate binding.
- A timeout or lost response is an unknown outcome, not permission to submit a
  new key. After restart, the SDK recovers the pending mapping and calls
  `get_receipt` with its authenticated principal, scope, original key, and
  candidate digest; knowledge of a Core-generated operation identifier is
  optional.
- The SDK advances its normal source checkpoint only when a terminal receipt
  has `checkpoint_eligible=true`, which is limited to an accepted durable
  commit or an idempotent replay of that accepted commit. Retryable failures,
  conflicts, and rejections never advance it.
- A terminal conflict or rejection closes that key-and-candidate delivery
  attempt but never releases or rebinds the key's canonical candidate binding.
  The attempt remains in a separate durable resolution/dead-letter state until
  an owner explicitly resolves or skips it. Terminal does not by itself mean
  checkpoint-eligible.
- `cursor_advance_eligible` is a separate Core-issued owner-resolution signal,
  never an SDK inference. It can unblock an ordered source cursor after an
  explicit skip without claiming that Core accepted or wrote the candidate.

Core separates one canonical operation receipt from per-delivery-attempt
resolutions. The canonical receipt includes a stable receipt and operation
identifier, canonical idempotency-key digest, candidate/content/source digests,
logical scope, policy identity and version, durable object identity, terminal
outcome, `checkpoint_eligible`, conflict or rejection classification, and
commit timestamp. A delivery resolution binds the requested key digest and
attempted candidate digest to the canonical receipt identifier and says whether
the key is canonical or an alias. A mismatched-digest conflict is a separate
immutable resolution keyed by principal, scope, key digest, and attempted
candidate digest; it references but never replaces the key's canonical binding.

`get_receipt` requires the original key and attempted candidate digest from the
SDK's durable `PENDING` record. It returns a `receipt_view` containing the
immutable original delivery resolution byte-for-byte plus an ordered,
append-only `owner_resolutions` collection. Core's local owner-resolution
surface appends those records; the SDK cannot mint them. This is the retrieval
channel for `RESOLUTION_COMMITTED` without mutating the original terminal
receipt. Canonical keys, aliases, and mismatched-digest attempts therefore all
recover the exact resolution for the caller's durable attempt identity.
Receipts exclude raw credentials, provider routing, private Core paths, and
source content not needed for audit.

### Wire-level outcomes

For `submit_ingest`, a request that reaches a durable terminal decision returns
`ok=true` with a complete delivery resolution in `result`, even when its
`outcome` is `policy_rejected`, `idempotency_conflict`, or `target_conflict`.
Here `ok` means that the protocol operation completed, not that content was
accepted. The SDK persists that resolution before acting on
`checkpoint_eligible` or moving to `RESOLUTION_PENDING`.

`ok=false` with `result=null` is reserved for envelope, compatibility,
authentication, stale-preview, busy, unresolved-recovery, transport, timeout,
or sanitized internal failures that did not produce a terminal delivery
resolution. `timeout`, `internal_error`, a lost response, or any other
unknown-outcome failure requires `get_receipt` with the original key and
attempted candidate digest before any resubmit. `receipt_not_found` is safe
evidence of no durable operation only after Core has acquired the scope
recovery barrier, excluded an in-flight operation, and found no `INTENT`,
terminal receipt, delivery alias, attempt resolution, or conflicting canonical
binding for that key. If the key is bound to another digest, Core instead
durably returns the candidate-specific `idempotency_conflict`. Only a true
not-found result lets the SDK obtain a fresh preview/grant and resubmit the
unchanged candidate with the same key.

### Atomic commit and recovery

Every Core path that can modify a target admitted to an ingestion scope must
use one Core `DurableWriteCoordinator`. The coordinator owns the per-target
lock, scope recovery barrier, and operation-bound provenance marker. Existing
MCP, `remember.py`, importer, or other writers that have not migrated to this
coordinator exclude their targets from an enabled ingestion scope.

Lock acquisition is globally ordered: enter the scope recovery barrier first,
then acquire the source-object claim lock, then the target lock. Multiple
claims or targets are acquired in canonical lexical order, and no code path may
acquire these locks in reverse. Delivery-key and source-claim lookup, alias
persistence, target mutation, and receipt finalization occur inside that
hierarchy. This makes claim collapse and per-target serialization one atomic
decision surface rather than independent races.

Before staging, Core computes an `operation_commitment_digest` from a canonical
encoding of the protocol major, operation identifier, authenticated-principal
fingerprint, scope, idempotency-key digest, source-object claim, source claim,
expected predecessor state, candidate digest, target identity, expected
pre-write digest, policy identity/version, preview-token digest, and
authorization-nonce digest. The field set explicitly excludes staged/final
byte digests, the `INTENT` entry digest or offset, timestamps, and receipt
identifiers. This makes the commitment non-circular: the staged marker and
later `INTENT` both carry the already computed value, while `INTENT` separately
records the resulting staged/final byte digest.

In this ADR, append or file `flush` means a durability barrier, not merely a
language-runtime buffer flush: write the complete bytes, flush userspace
buffers, call `fsync`/`fdatasync` or a proven platform equivalent, and durably
sync the parent directory when a ledger, stage, or target entry is created or
renamed. The stage and target must share a filesystem with a tested atomic
replace primitive. If the running platform/filesystem cannot prove these
primitives, `capabilities` reports durable submit unavailable and the scope
remains disabled; Core never downgrades to best-effort success.

Core performs a submission through the coordinator under that ordered hierarchy,
ending in one per-target lock:

1. recover every unresolved operation in the scope, then locate any existing
   idempotency-key, source-object-claim, or source-claim entry;
2. return the existing terminal delivery resolution or recoverable-incomplete
   status for a matching key/claim and digest, first appending and durably
   flushing a delivery alias for a matching new key, or durably append and
   return the attempt-specific conflict resolution for a mismatched digest,
   before applying current authorization or policy to a historical outcome;
3. for a new operation, revalidate principal, authorization grant, preview,
   policy, candidate digest, source-object lineage, source claim, and expected
   target state;
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
6. atomically replace the target with the staged file and complete the required
   target and containing-directory durability barriers;
7. append and flush `FILE_COMMITTED`, then append the provenance, canonical
   `TERMINAL` receipt, and delivery resolution; and
8. schedule rebuildable indexes and lifecycle work after file truth commits.

A crash after staging but before `INTENT` can leave an orphan staged file but
cannot mutate the target or consume the grant. Core quarantines or removes the
orphan only through a bounded, audited retention rule after the scope recovery
barrier proves that no `INTENT` references it. Staged content is Core-private,
permission-restricted, absent from logs and shared caches, and retained until
its operation reaches a terminal state or that audited orphan rule expires.

SDK pending journals and Core staging/ledger directories use private
permissions (`0700` directories and `0600` files or a stricter platform
equivalent). Journals contain no candidate body or grant. They retain raw
idempotency-key material only while recovery needs it, pseudonymize source
identifiers where checkpoint semantics permit, and preserve only key digests,
receipt references, and transition evidence after compaction.

The durable file is the source of truth. A derived-index failure cannot turn a
committed file into an ingestion failure or cause the SDK to write it again.
The terminal receipt records downstream work as pending, complete, or
degraded, and Core owns repair.

The ledger is an append-only state machine: `INTENT`, optional
`FILE_COMMITTED`, then `TERMINAL`, plus immutable delivery aliases,
attempt-specific conflict resolutions, and owner-resolution records. The
ledger and file write cannot be assumed to share a database transaction. Core
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
| `FILE_COMMITTED` without `TERMINAL`, target equals the recorded post-write digest, and marker matches | Finalize and return the accepted receipt from verified file truth without another file write. |
| `FILE_COMMITTED` without `TERMINAL`, target or marker does not match the recorded committed state | The replace was already durably acknowledged, so never replay the stage or return to an `INTENT` resume row. Append terminal `target_conflict`, quarantine the operation for audited owner recovery, and preserve all evidence without another file write. |
| `TERMINAL` | Return a receipt view embedding the original delivery resolution byte-for-byte for that protocol version plus any separately appended owner-resolution records. |

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
changed since preview, a stale or unrelated revision follows a newer durable
source-object receipt, or another candidate claims the same logical identity,
Core returns a terminal conflict with evidence sufficient for owner review.
Merge, replace, supersede, create-alternate, or accept-out-of-order behavior
requires a separate explicit policy decision and preview.

Terminal submit outcomes and nonterminal errors are structured and sanitized.
Version 1 distinguishes at least:

- non-retryable `invalid_request`, `incompatible_version`,
  `permission_denied`, and `preview_stale` errors;
- terminal delivery outcomes `accepted`, `policy_rejected`,
  `idempotency_conflict`, and `target_conflict`, returned through the durable
  resolution rather than an `ok=false` envelope;
- retryable `core_busy`; recoverable `incomplete_operation`, which blocks the
  target until Core reconciliation; and bounded transport or timeout failures
  that require receipt reconciliation before any resubmit; and
- sanitized `internal_error`, which does not imply that no write occurred; and
- `receipt_not_found`, which permits a fresh preview/grant and same-key
  resubmit only after the recovery-barrier proof defined above.

This ADR authorizes no new external model or provider dependency. If an
existing Core policy separately invokes an admitted provider route, that
execution remains governed by Core and shared provider-routing policy,
including the centralized `KeyPenaltyStore`; the SDK receives only a
provider-neutral outcome. An unavailable required policy route fails closed and
cannot be bypassed by the SDK. Provider names, credentials, balances, and
penalty state remain outside the ingestion protocol.

## Readiness gates

Executable `submit_ingest` remains disabled until all of these gates pass:

1. Canonical Core-owned JSON schemas, compatibility fixtures, and negative
   tests exist for every operation, nonterminal error, terminal outcome,
   preview, delivery resolution, receipt view, and owner-resolution record.
   Contract tests prove that terminal submit decisions use `ok=true`
   resolutions while
   unknown outcomes remain reconcilable through candidate-aware `get_receipt`.
2. Ingestion scopes and write permission default to off, are inspectable in
   `capabilities`, and cannot be enabled by an SDK request. Tests reject
   missing, forged, expired, cross-principal, cross-scope, and replayed grants
   and prove one-time consumption under concurrency. Stable principal identity,
   credential rotation, revocation, and audited historical recovery are tested
   without changing the idempotency namespace.
3. Candidate canonicalization, source-object lineage, source-claim collapse,
   and idempotency conflict behavior have golden fixtures across the current
   and immediately previous supported SDK/Core pair. Tests reject same-revision
   digest changes and stale, unrelated, or falsely ordered revisions unless an
   explicit Core policy approves them. The non-circular operation-commitment
   field set has golden fixtures, and cross-operation marker substitution is
   rejected. Alias persistence is crash-tested before response, including an
   aliased incomplete operation, and `get_receipt` resolves canonical keys,
   aliases, and same-key/different-digest attempts without changing the
   canonical key binding.
4. SDK crash tests cover every transition from `PENDING` through
   `RECEIPT_DURABLE`, `CHECKPOINT_COMMITTED`, `RESOLUTION_PENDING`, or
   `RESOLUTION_COMMITTED`, including a lost response plus SDK restart and
   crashes on both sides of checkpoint or explicit-skip cursor commit. The
   original key remains recoverable for every unfinished state.
5. Crash-injection tests cover every boundary before and after staging-file
   flush, `INTENT`, atomic replace, directory flush, `FILE_COMMITTED`, terminal
   receipt, delivery alias, and lifecycle scheduling. The running
   platform/filesystem proves durable file and directory barriers plus atomic
   same-filesystem replace; otherwise submit remains unavailable.
6. Concurrent duplicate, conflicting-key, stale-preview, changed-policy,
   changed-target, same-source-claim, and out-of-order source-revision tests
   prove single-mutation behavior.
7. Every Core path able to touch an ingestion-enabled target uses the same
   `DurableWriteCoordinator`, ordered scope/claim/target lock hierarchy, and
   recovery barrier. Tests prove that the operation-bound marker, not matching
   content bytes alone, is required to attribute a commit and that no reverse
   lock acquisition can deadlock recovery.
8. Startup and on-demand reconciliation implement every recovery-table row,
   block later scope writes until unresolved operations are classified, and
   prove pre-`INTENT`, prepared, and post-commit policy-drift behavior without
   deleting evidence. Tests prove that any state with durable
   `FILE_COMMITTED` can finalize from matching committed truth or terminate as
   a conflict, but can never replay the stage or return to pre-write recovery.
9. Receipt and SDK tests prove that only accepted or idempotently replayed
   accepted outcomes are checkpoint-eligible; conflicts and rejections enter
   the resolution/dead-letter state until a Core-issued owner resolution is
   durable and appears in the append-only receipt view without mutating the
   original resolution. An explicit skip can set only
   `cursor_advance_eligible`; tests prove it cannot be forged, cannot imply
   acceptance, and is durable before cursor advancement.
10. Provenance, privacy, and secret scans prove that staged content, receipts,
    logs, errors, pending journals, and checkpoints contain no credentials,
    provider internals, exposed Core paths, or raw authorization grants beyond
    the private Core staging area required for the prepared operation. Private
    modes, minimal/pseudonymized journal identity, raw-key compaction, and
    bounded audited orphan-stage retention are verified.
11. Live `capabilities` reports the running Core build identity, protocol
    version, canonical schema-manifest digest, privacy-safe session/principal
    attestation for the stable logical principal, effective scopes for that
    principal, durable-replace capability, `submit_requires_core_grant=true`,
    and policy/configuration digest. End-to-end tests verify that exact live
    worker through the public SDK and match it to the reviewed installed
    bundle; source/file parity alone is not accepted as runtime evidence.
12. A synthetic connector passes end-to-end first. A single real source may be
    piloted only after rollback and owner-visible conflict handling are proven.

## Rollout and rollback

Rollout is additive: schemas and a disabled worker surface first, then
synthetic validation/preview, then synthetic submit under an isolated scope,
then one explicitly enabled real-source scope. Existing `remember.py`, MCP,
and importer write paths are not silently redirected during this rollout; a
real target remains excluded until every writer for it explicitly adopts the
shared coordinator.

Rollback disables the ingestion scope and rejects every new `INTENT`. Operations
with a durable `INTENT` continue through the recovery table to a terminal
resolution unless an emergency scope quarantine explicitly pauses them; scope
disable never silently cancels or rewrites a prepared decision. Rollback does
not delete committed Core files, receipts, ledgers, provenance, checkpoints,
or provider state. Any reversal of accepted content uses an existing Core-owned
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
