# Semantic maintenance and retry evidence

Lexical indexing, vector coverage, and M1/M2 completion are separate facts.
A successful FTS refresh does not imply that semantic maintenance finished.

## Completion rules

- A successful empty neighbor search or a low relevance score completes work.
- Disabled optional features remain disabled; installation never enables them.
- Model/runtime errors, stamp mismatches, unreadable cards, occupied card locks,
  and failed evidence writes keep only the affected path queued for retry.
- If semantic features are enabled, a missing vector store remains pending.
- Explicitly requested M1 cross-encoder corroboration must be available before
  a contradiction is confirmed; disabled corroboration stays optional.
- Removed neighbor files are skipped; permission and other read errors defer.
- M2 is skipped when M1 could not finish. A failed or uncertain contradiction
  check cannot authorize synthesis or supersession.
- New synthesis regions and supersession markers commit atomically with their
  evidence event under the card lock. Failed event writes leave the original
  card intact, even if the next discovery no longer selects that target.
- New synthesis revisions store a unique operation id inside the managed region.
  Repeated bodies after intervening edits have distinct identities. An unchanged
  region stays unchanged across midnight; the generated provenance date alone
  does not authorize another confidence nudge.
- A selected legacy region or supersession marker lacking its expected event
  can repair it on retry. This is not a historical recovery scan.
- Different events colliding on the existing second/type evidence key defer
  until retry; exact duplicate events remain idempotent.
- Only relevance-passed, classified, non-conflicting, non-superseded neighbors
  may contribute claims to another card's synthesis.
- M3 session-bound assertions are never replayed from this queue.

Fresh M1/M2 writes report `event_deferred` if their evidence event is incomplete;
auxiliary logs do not report those operations as completed. Equal-name M1 ties
use paths for a stable winner. Synthesis claims exclude evidence sections and
previous provenance headers.

Public Engine reads still return their existing soft fallback on unavailable
models. A scoped internal failure collector lets queue consumers distinguish
that fallback from a successful empty result; it does not expose a new Engine
API or copy card bodies into diagnostics.

## Diagnosis

`bin/doctor.sh` reports semantic pending work separately from vector coverage.
Its functional canary reports resource-budget deferrals as a retryable warning,
not a broken model or a reason to rebuild the vector store.
The read-only implementation diagnostic is:

```bash
python3 bin/maintenance_status.py ~/.claude/memory-system/db/index.db
```

Exit 0 means an empty queue, 1 means active pending work, 2 means status
unavailable, and 3 means the queue is paused for this caller because confidence
events are disabled in its environment. A disabled caller preserves another
client's pending work. Fresh disabled indexing does not enqueue semantic work.
The derived `schema_meta` record `semantic_maintenance_status_v1` retains failure
stage/class per pending path, the latest attempt, and the last time at least one path completed.
A successful later batch does not erase an earlier path's failure. No raw model
output, card body, or exception message is stored there. Older installations have
unknown completion timestamps until a path finishes; this is not reported as a
measured queue age. Progress/status and queue acknowledgements commit together.

## Recovery boundaries

Use subsequent bounded maintenance requests to resume work. Keep the configured
resource budget and selected model. Do not reset ledgers or rebuild vectors just
to erase a retry marker. A worker deadline can interrupt a batch before it writes
its diagnostic record; its queued paths still survive and the supervisor reports
the deferral. There is no permanent completion daemon or completion-time promise.
Best-effort auxiliary operation logs are not a transactional source of truth.
This repair does not reconstruct semantic work silently acknowledged by older
versions; any historical replay needs a separately scoped audit.

Tests exercise the real hooks with injected read/model/write failures, low-score
completion, feature opt-out, queue rotation, preserved diagnostics, atomic mutation/event writes, and
recovery across interrupted writes and changed discovery. The model-free tests establish these contracts;
live model availability and search quality require separate evidence.
