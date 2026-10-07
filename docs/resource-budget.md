# Shared resource budget

Eidetic defaults to a cooperative **20% CPU / 20% GPU average budget**. A user
may run many agent sessions and topic bases; they share one budget rather than
each taking 20%. The policy does not change embedding models, dimensions,
precision, retrieval thresholds, or the durable source cards.

## What is bounded

- Model loading, embedding, and reranking share an interprocess compute lock.
  The lock includes the cooldown, so another process cannot fill the rest
  periods with its own GPU work.
- CPU consumption is measured in process CPU seconds and normalized by the
  machine's logical CPU count. On a 10-core Mac, 20% is two CPU cores' worth of
  sustained work, equivalent to 200% in a per-process CPU display.
- GPU work is synchronized before its elapsed work window is charged. Since
  there is no hardware quota here, the entire window is conservatively treated
  as full GPU occupancy. At 20%, one second of work requires at least four
  seconds without further Eidetic model work. Small batches shorten bursts.
- Index file loops and vector scoring loops use CPU checkpoints, sharing the
  same CPU debt as model operations. Worker entry points settle the final tail.
- Numerical thread pools are limited to at most two threads, or fewer on small
  machines. Workers lower CPU scheduling priority to nice 10 as an additional
  responsiveness measure. Darwin background I/O is deliberately avoided: on a
  busy disk it can starve cold library imports while holding the model gate.

This is an **average cooperative limit**, not an instantaneous hardware quota
or a bound on the entire computer. Other applications may take GPU usage above
20%; short Eidetic compute bursts and CPU between checkpoints can also exceed
the average. Processes started before this update must finish or be restarted
before they use the new policy. Uninstrumented third-party computations are
outside this contract. The policy does not send stop/continue signals.

## Avoiding redundant work and excess memory

Indexing is serialized per canonical database path before scanning files or
loading models. A waiting incremental request scans again after acquiring the
lock, so a file added while it waited is not lost. A lock timeout exits with 75,
not a false successful index result.
The separate vector-writer lock returns the same retry exit code immediately
when another vector build is active, rather than reporting unperformed work as
success.

A true unchanged incremental run skips relation propagation and semantic hooks.
Relation propagation has its own durable retry marker: an interruption after
FTS commit cannot make an unchanged retry skip the unfinished relation update.
Pending ingest work is stored with the FTS update so an interrupted new indexer
can resume it even when the file modification times have not changed. This does
not retroactively recover work interrupted in older indexer versions, or make
M1/M2 multi-file updates transactional.

Each incremental CLI run drains at most one pending semantic card, leaving the
rest durably queued and reporting the remaining count. Later indexing requests
drain the oldest pending work first; an escaping hook error rotates the failed
batch to the tail without dropping it. `EIDETIC_INGEST_BATCH_SIZE` can change this
batch size (1–128). Install/update register an asynchronous Stop maintenance hook;
feature activation still follows the user's settings. FTS freshness is
independent of this semantic backlog. If there are no further indexing requests,
the backlog waits for the next run.

The deferred queue contains only M1/M2 work and respects
`EIDETIC_CONFIDENCE_EVENTS`. Disabled callers leave an existing queue intact and
do not enqueue new semantic work. M3 runs for the current changed cards in the
caller's session; it is never replayed under a later session. The queue can retry
exceptions that escape the hooks and interrupted runs, but cannot detect errors
that the existing M1/M2 hooks catch internally.

Neural work uses batches of one by default. MLX reusable cache is limited to
128 MiB; this is **not** a total process RAM cap and does not include model
weights. Vector rows are streamed instead of materializing a second full copy
of the vector database. Up to 128 short query embeddings are reused within a
process; search results themselves are never cached, preserving index freshness.
Full vector rebuilds publish vectors and geometry stamps in one SQLite
transaction. A failed rebuild rolls back to the previous committed index, also
when a WAL reader remains open; no live database is replaced from a file copy.

## Configuration

The per-user policy is `~/.claude/memory-system/.resource-budget.json`:

```json
{
  "enabled": true,
  "cpu_percent": 20,
  "gpu_percent": 20,
  "batch_size": 1,
  "threads": 2,
  "mlx_cache_mb": 128
}
```

Missing configuration uses these defaults. Invalid configuration or unavailable
budget locking raises an error rather than silently running uncapped. The CLI
refuses an invalid policy even for FTS-only work. The public Engine API keeps its
soft-search contract and reports unavailable neural search as an empty result.
`EIDETIC_MEMORY_SYSTEM` selects an index but does not split the resource budget.
`EIDETIC_RESOURCE_ROOT` redirects the policy and accounting root for isolated
tests; do not set a different root per production agent or topic base.

The stable `.resource-budget.lock` and `.resource-budget.state.json` beside the
policy contain only scheduling state. Deadlines survive an interrupted cooldown
and reset for a different OS boot. They are not knowledge or collected data.
Set `enabled` to `false` to disable cooperative pacing; the index serialization
and no-op optimization remain in place. Already-applied nice values persist for
the lifetime of their processes.

Fastembed uses the repository's supported pin (`0.8.0`), including its `threads`
and `providers` constructor arguments. A provider initialization failure falls
back to CPU with the same thread cap; unsupported older APIs do not silently
retry with unrestricted defaults. Cold model imports are accounted as CPU work,
separately from GPU inference windows.

## Validation

Run the scheduling, governor, and integration tests against temporary stores:

```bash
python3 -m unittest discover -s tests -p 'test_resource_budget.py'
python3 -m unittest discover -s tests -p 'test_index_scheduling.py'
python3 -m unittest discover -s tests -p 'test_compute_budget_integration.py'
```

The tests cover concurrent processes, lock release, deadline persistence,
unchanged indexing, retry after interruption, microbatch ordering, and completion
before GPU cooldown. A live rollout additionally requires a semantic recall
probe and a measured concurrent workload; synthetic timing is not evidence of
whole-machine GPU utilization.

On the rollout Mac (M1 Max, 10 logical CPU cores), two concurrent real MLX
workers produced six 1024-dimensional vectors in a 49.94-second observation:
aggregate process CPU was 1.50% of machine capacity, and synchronized GPU work
windows occupied 17.11% of elapsed time. The latter is a conservative scheduling
measurement, not per-process hardware GPU telemetry. Two comparison vectors had
cosine similarity 1.0 with the pre-change implementation. These small probes
check pacing and unchanged geometry, not corpus-wide retrieval quality.

MLX API references: [synchronize](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.synchronize.html),
[set_cache_limit](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_cache_limit.html).

On macOS the ledger uses `CLOCK_UPTIME_RAW`, the boot-relative clock shared
by system Python 3.9 and modern Python. Interpreter-relative monotonic offsets
are never persisted; existing modern-Python deadlines keep their clock domain.
