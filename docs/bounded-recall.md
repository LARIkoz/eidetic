# Bounded recall and index maintenance

The October 7 outage left lexical handoff rows committed while an indexer loaded
an ONNX model under both the index lock and the shared compute lock. Searches
also waited on the shared lock in their final CPU-accounting checkpoint.

Search now retains a model-free lexical answer first. Optional translation,
vector retrieval and reranking run in an isolated process group with a 30-second
wall deadline (`EIDETIC_SEARCH_TIMEOUT`, finite, positive, at most 300 seconds).
Timeout/failure returns lexical results with `retrieval_mode=lexical` and an
explicit `degraded_reason`. Confidence and lifecycle gates are unchanged: no
lexical match is not evidence that a memory does not exist. A fallback is not
proof that cross-lingual semantic search is healthy.

Incremental CLI indexing commits lexical rows and relation metadata, closes the
connection and releases its lock before launching semantic maintenance. That
worker reacquires the canonical index lock without waiting and has a 30-second
deadline (`EIDETIC_MAINTENANCE_TIMEOUT`, same validation). The default worker batch is one card. It acknowledges only
completed batches. Exceptions and governor deferrals retain queued cards.
M3 session-bound producer work stays in the original invocation; it is never
replayed as a semantic queue entry. Existing direct Python callers can retain
inline maintenance; the production CLI defaults to the separate worker.

The shared 20% CPU/GPU policy remains enabled. Ordinary lock admission waits at most two
seconds (`EIDETIC_RESOURCE_WAIT_SECONDS`, 0 through 30); compute is refused when
busy or when admission cooldown would exceed that wait. Debt is persisted before
cooldown. Completed model work cools outside the shared lock; CPU-only
checkpoints wait for CPU debt, not GPU debt. Admission rechecks under the lock
after every wait. Supervised model workers allow up to 30 seconds of admission
within their overall deadline. New CLI reads apply CPU-only backpressure before
retrieval; final settlement preserves ready results without waiting for cooldown.
Unpaid CPU checkpoints append to `.resource-budget-deferred.jsonl`; the next
state settlement consumes rows with a durable byte offset, without truncating
the journal. A timed-out process group receives TERM then KILL and is reaped.
Since native code may not unwind accounting on cancellation, model slots write
an atomic per-worker activity receipt. Recovery charges only an unfinished slot,
never time merely waiting for admission. Guardian and supervisor observations
share a recovery identity; settlement applies only the larger observation rather
than summing duplicate charges. Unmeasurable active CPU work is charged
conservatively. Receipts also record settled CPU, so the supervisor charges only
the measured remainder rather than charging a failed worker twice.

All source memories remain authoritative. No model, confidence threshold,
vector geometry, corpus, source file or existing memory store is replaced.

The optional background hook is `hooks/semantic-maintenance.sh`. It keeps the
existing single-flight lock, bounds index-lock admission to five seconds, and
does not use Darwin `taskpolicy -b` (which can starve model file reads). The
local Stop-hook command invokes this script instead of an inline background-I/O
shell. Install and update register one asynchronous hook, migrating the old inline
launcher and preserving unrelated hooks. M1/M2 activation remains opt-in through
the user's settings. The same worker makes bounded vector catch-up attempts.
SessionStart uses `index.sh --lexical-only` to commit and queue without waiting
for semantic maintenance, then bounds its vector refresh separately.
Timeouts preserve the prior vector error and report incomplete progress; only
a successful vector refresh clears the failure marker.

## Cancellation and review follow-up

A separate process-group guard enforces the worker deadline even if its calling
supervisor receives SIGKILL. Supervisor SIGTERM is handled cooperatively.
Abnormal exits recover unfinished model receipts; idle admission waits incur no GPU charge.
Workers settle final CPU in `finally`, including resource deferrals. Optional
worker launch failures do not turn an already-committed lexical update into a
failed write. Legacy JSON-list search emits fallback warnings on stderr.

Supervised workers set `EIDETIC_RETURN_COMPLETED_COMPUTE=1`: a completed model
operation returns its result before post-work cooldown, while the next compute
admission must still pay the persisted shared debt. This prevents killing a
ready result just because its cooldown exceeds the remaining worker deadline.
Standalone callers retain the existing post-work wait behavior. This is not a
budget bypass: a new process sees the same CPU/GPU deadlines.

Deferred-charge appends are fsynced and start with a newline, isolating subsequent
records from a short write. A malformed record remains in the append-only file
and receives one conservative maximum-worker-window charge once framed; an
unfinished tail defers work without advancing the offset. The consumed offset
is committed with that debt. No ledger truncation or state reset is performed.

The bounded workflow covers ordinary recall and incremental maintenance. An
explicit full vector rebuild remains an operator maintenance operation, outside
these interactive deadlines. The inherited M1/M2 hooks still suppress some
non-budget errors; this patch detects budget deferrals and escaping exceptions,
not every historical fail-open path. That limitation is separate from the fixed
lock starvation and retained in the repair report.

## Resumable vector catch-up

Incremental vector writes now commit each configured compute microbatch (one
chunk under the default budget), rather than waiting for 64 chunks. Interruption
preserves completed vectors and the next run embeds only the remaining suffix.
A focused regression proves commit, interruption, and resume. Full rebuild
atomicity is unchanged; this is an incremental progress fix.
