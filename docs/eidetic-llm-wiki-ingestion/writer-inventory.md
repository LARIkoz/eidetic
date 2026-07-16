# Durable Writer Inventory For Imported-Book Scope

**Snapshot date:** 2026-07-16
**Reviewed Core base:** `2e710d1`
**Purpose:** determine whether a target may enter an ADR 0004 ingestion scope
**Decision:** use a physically isolated imported-book target for the first pilot

## Rule

ADR 0004 excludes a target from an enabled ingestion scope until every path
that can mutate that target uses the same Core `DurableWriteCoordinator`, lock
hierarchy, recovery barrier, and operation-bound provenance marker.

This inventory therefore distinguishes durable card truth from rebuildable
indexes, metadata/evidence stores, and external source state.

## Existing Project And Agent Memory Writers

| Writer or trigger | Durable effect | Current coordination | Pilot decision |
| --- | --- | --- | --- |
| `bin/remember.py` | creates cards and appends updates under a project memory directory | private atomic temp/replace plus `compound` directory resolution; no ingestion ledger | excluded from pilot target |
| `eidetic-importer/import.py` | invokes installed `remember.py` for every accepted prototype card | external subprocess; no SDK/Core ingestion contract | preserve as legacy rollback only; excluded |
| `bin/compound.py` | updates existing card content and creates/updates `memory/signals/*.md` | its own atomic replacements and project resolution | excluded |
| `bin/evidence.py` | rewrites a card to append typed evidence events | persistent per-card flock plus atomic replace | excluded |
| `bin/m1_contradiction.py` | may append contradiction evidence through `evidence.py` when explicitly enabled | M1 gate plus evidence writer lock | excluded |
| `bin/m2_synthesis.py` | may set `superseded_by`, append evidence, and replace a managed synthesis region | evidence card lock plus `remember._atomic_write` fallback | excluded |
| `bin/m3_autofile.py` | may create a new recalled-answer card and append affirmation evidence | reuses `remember`/`compound`/`evidence`; separate M3 gates | excluded |
| `bin/m3_hook.py` and `bin/m3_producer_driver.py` | trigger M3 acquisition/autofile paths against project memory | trigger/orchestration only; underlying writers above | excluded |
| `bin/curate.py --apply` | rewrites card frontmatter to archive or restore status | private temp/replace; no shared coordinator | excluded |
| direct owner/editor changes | may create, replace, rename, restore, or delete Markdown cards | outside Core operation ledger | excluded and treated as target divergence |
| backup, restore, sync, or migration tools aimed at project memory | may reproduce or replace bytes without an ingestion marker | tool-specific | excluded until individually inventoried |

These paths make existing project and agent memory unsuitable for the first
writable imported-book scope. A matching byte digest is not sufficient to
attribute a commit to ingestion.

## Topic-Base Writers

| Writer or trigger | Durable effect | Pilot decision |
| --- | --- | --- |
| `bin/base.py add` | creates a note or document in a topic-base root and reindexes it | topic-base roots excluded |
| MCP `<base>_add` | invokes `base.py add` after explicit user request | topic-base roots excluded |
| direct topic-base file edits or repository sync | changes durable topic-base documents | topic-base roots excluded |

The first imported-book target must not be mounted as a topic base or made
writable through the MCP curate-add surface.

## Lifecycle And Evidence Stores

These stores are durable evidence but are not the candidate card target. They
must remain append-only or use their existing reviewed semantics and must not be
confused with an ingestion receipt:

| Store/writer | State class | Relation to ingestion |
| --- | --- | --- |
| `bin/oplog.py` and callers | global operation timeline | downstream evidence only; not commit proof |
| `bin/lifecycle_signals.py` | metadata/event JSONL | signal evidence; not candidate receipt |
| M3 seen, filed, commission, and dark artifacts | M3 evidence/state | separate M3 track; not imported-book authority |
| SDK Engine `ReceiptJournal` | derived-index freshness receipts | cannot implement ingestion pending/receipt state |
| provider response cache and `KeyPenaltyStore` | protected shared provider state | remains under `shared_api_cache`; never copied locally |

## Rebuildable Derived-State Writers

The following may update derived databases or exports but do not own durable
candidate card truth:

- `bin/index_impl.py`, `bin/index.sh`, and search index repair;
- `bin/engine.py` and `bin/eidetic_engine_worker.py` logical index sync;
- embedding, reranking, and code/vector index builders;
- export-vault tooling; and
- read-only search, recall, lint, health, and reporting tools.

Derived-state success or failure cannot decide whether an imported card was
durably accepted. Index writers remain downstream of file truth.

## Installed Runtime And Source Copies

The installed Core runtime contains deployed copies of writer scripts and may
drift from the source checkout. Before a scope is enabled, review must compare:

- source and installed worker/schema/build identities;
- source and installed versions of every writer that can reach the selected
  target;
- runtime configuration mapping the logical scope to the private target; and
- current hooks/MCP configurations that could invoke a writer.

Source-code inventory alone is not sufficient runtime evidence.

## Dirty-Worktree Caveat

At this snapshot the Core worktree contains unrelated uncommitted M3 judge and
hook work. It is outside this documentation phase and was not classified as a
new production writer. Before Phase 4, rerun the inventory against:

1. the reviewed canonical Core commit;
2. the exact installed runtime bundle; and
3. any M3/preview work merged since this snapshot.

Any newly discovered path capable of touching the pilot target blocks scope
enablement until it either adopts the coordinator or is physically excluded.

## Isolated Pilot Target Requirements

The Phase 4 synthetic target and later Phase 6 real pilot target must satisfy
all of these conditions:

1. Core resolves a logical `imported-book` scope to a new Core-private target
   class; no physical path crosses the protocol.
2. The target is physically outside project memory, agent memory, topic-base
   roots, and all current writer directory-resolution/glob rules.
3. `remember.py --project`, `compound.resolve_memory_dir`, M1/M2/M3, curation,
   topic-base add, and the legacy importer cannot resolve or traverse it.
4. Only `DurableWriteCoordinator` may create or mutate candidate card files.
5. Direct/manual divergence is detected by expected target digests and the
   operation-bound provenance marker; it never becomes silent overwrite.
6. Core may read/index the target after file commit, but derived index code has
   no durable write authority.
7. Scope configuration defaults to off and is inspectable through sanitized
   capabilities.
8. Rollback uses coordinator-owned compensating lifecycle writes.

## Long-Term Migration Boundary

If imported cards later need to live in existing project memory or compound
automatically with existing cards, the isolated-scope exception no longer
applies. Before enabling that target, every writer in this inventory, every
installed copy, direct-edit policy, backup/restore path, and any new writer must
be migrated to or serialized through the common coordinator. That is a future
reviewed phase, not part of the first book vertical slice.
