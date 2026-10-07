# Maintenance reliability audit

This follow-up covers bounded recall, incremental indexing, shared scheduling,
and the install/update paths. Source cards remain authoritative; no source-memory
cleanup, ledger reset, model change, or destructive vector rebuild is required.

## Fixed behavior

| Trigger | Previous result | Current result |
|---|---|---|
| System Python 3.9 and modern Python share macOS scheduling state | Interpreter-relative clock made another process's deadline appear invalid | All processes use the same boot-relative `CLOCK_UPTIME_RAW`; existing modern-Python deadlines remain valid |
| Ledger retains a previous boot's long uptime | Old timestamps could be misclassified as damaged current work | Previous-boot records are consumed without imposing new debt |
| Worker exits after already accounting CPU | Supervisor could charge that CPU a second time | Activity receipts record settled CPU; recovery charges only the uncovered remainder |
| Reader reaches an unfinished journal tail | Partial record could be consumed and penalized repeatedly | Work defers without consuming the tail; the next append frames a genuinely torn record |
| Vector refresh reaches its deadline | Old failure marker was cleared without confirmed success | Prior error remains, incomplete progress is explicit, completed vector microbatches survive |
| Vector worker waits for shared admission | Two-second admission limit could abort a thirty-second maintenance window prematurely | Default admission wait follows the bounded worker window, while explicit user limits remain honored |
| New install or update | Local maintenance repair was not registered | One async Stop hook is installed; legacy owned launchers migrate, unrelated hooks and feature opt-outs remain intact |
| Session starts while semantic work is queued | Startup also waited for semantic processing | `index.sh --lexical-only` commits and queues, leaving semantic processing to bounded maintenance |
| Fresh installation has no settings file | Hook registration was skipped | Installer creates settings and registers hooks |
| Fresh Apple-Silicon install uses Python 3.9 | Default MLX bootstrap attempted incompatible dependencies | Defaults to the portable optional-vector/FTS route; explicit MLX bootstrap requires Python 3.12+ or an existing working MLX environment |
| M3 tests run after the default route changed | Old SDK stubs missed the local transport and could call a real service | Tests stub the actual local transport and assert pinned local mining with failover disabled |

Install and update share `bin/maintenance_hooks.py`. Vector refresh uses
`bin/vector_maintenance.py` from both the SessionStart and asynchronous Stop
paths; the updater uses the same bounded boundary. Optional maintenance retains
retry work rather than claiming successful processing. Exit 75 means deferred
work, including a partially completed vector catch-up.

Additional review fixes: the independent guard now cleans surviving descendants
on normal child exit as well as timeout, and reaps its direct worker before group
termination. Final supervised CPU settlement does not wait away a ready result;
new CLI reads apply bounded CPU admission before retrieval. The updater retains
an explicit refresh-pending marker so retrying the same deployed SHA resumes
failed derived work. Hook migration recognizes executable paths, retains local
feature overrides, and leaves unrelated similarly named commands intact.
Reinstallation retains model choices that were not explicitly changed. Managed
vector refreshes serialize both work and diagnostic publication.

## Validation

The final local suite passes 826 tests on each of Python 3.9 and Python 3.14
(16 and 14 environment-dependent skips respectively). Canonical live doctor
passes, and two current handoff pointers rank first in under 0.3 seconds.

The regression suite includes real subprocess cancellation, cross-interpreter
state reads, preserved accounting, repeated hook migration, feature opt-out,
timeout diagnostics and an actual updater run with isolated HOME and offline Git
transport. Fresh system-Python installation is also exercised separately.

The MLX bootstrap pins its direct and transitive package versions in
`requirements-mlx.txt`, captured from the working Python 3.12 Apple-Silicon
runtime. Existing environments are reused, not upgraded. Fresh locked MLX
bootstrap requires Python 3.12+; the portable FTS route still supports Python 3.9.
Pinned versions are not a claim of wheel-hash verification or an online fresh
MLX bootstrap test.

Model-free regression success does not establish full semantic coverage. Live
validation must separately check canonical recall, the backlog, aligned vector
coverage and the selected production engine. See [bounded recall](bounded-recall.md)
and [resource budget](resource-budget.md) for the operational contract.

## Remaining limits

- Pending work drains on subsequent hook/index requests; no permanent daemon or
  completion-time guarantee is introduced.
- Semantic search may reach its deadline under shared load. It returns explicitly
  degraded lexical results and preserves `no_confident_results` truthfulness.
- Generic errors swallowed inside existing M1/M2 code can still escape queue
  failure detection; resource deferrals and escaping errors are detected.
- Full vector rebuilds remain operator maintenance outside interactive deadlines.
- The optional ONNX cache is separate from the production MLX route. An incomplete
  ONNX download is not repaired by changing clocks or hooks.

Other review limits: a rare kill between committing budget state and publishing
its receipt can still conservatively overcharge work. Deduplication metadata
currently grows within a boot. Runtime publication is atomic per file, with new
backward-compatible dependencies copied before callers; it is not a single
atomic version switch. Semantic writers can hold the index lock for their bounded
window; a contending SessionStart now reports the deferred text refresh explicitly.
