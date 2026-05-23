---
type: rule
title: "DB operations — backup, locking, bulk writes"
aliases: ["db-operations-backup-locking"]
tags: ["rule"]
---

# DB operations — backup, locking, bulk writes

> SQLite DB rules for long-running pipelines — single source of truth, backup before major steps, WAL locking, never kill holders, bulk write logging.

**Why:** Killing a process mid-write can corrupt WAL or leave stale locks. Transaction abort = corruption risk.

## Details

Rules for working with a multi-GB SQLite DB. Data is irreplaceable (API credits spent) — treat it as an asset.

## Source of truth

**Rule:** Work on ONE database. Never copy DB to another location for "safety" and then work on both.

**Real incident:** Copied 20GB SQLite to SSD to avoid iCloud eviction. Did Sprint 0-3 on SSD. Meanwhile other scripts ran on iCloud original. Two DBs with different data, neither complete. Required manual ATTACH + merge.

**How to apply:**

- Pick ONE canonical location. Always.
- SSD = backup ONLY (`cp` for snapshots, never `cd` to work there).
- If iCloud eviction risk: `defaults write com.apple.bird optimize-storage -bool false`.
- Or symlink: `ln -sf <canonical> <secondary>` (one file, two paths).
- All scripts use `DB_PATH` env or `core/config.py DB_PATH` — one place to change.
- Before any pipeline run: verify `DB_PATH` points to correct DB.
- **NEVER** hardcode SSD path in scripts.

## Backup before major steps

**Rule:** No backup = no run. Kill/crash during pipeline step = corrupted state. Backup = safe rollback.

**Triggers (mandatory backup before):**

- Classify run: `cp db/pipeline.db db/pipeline.pre_classify_run.db`
- Post-chain: `cp db/pipeline.db db/pipeline.pre_post_chain.db`
- Bulk import: `cp db/pipeline.db db/pipeline.pre_import.db`
- Audit apply: `cp db/pipeline.db db/pipeline.pre_audit_N.db`

**Naming:** `pre_<step>_<date>.db`. Include in watchdog cron when auto-launching next step.

## Concurrency & WAL locks

**Rule:** SQLite WAL = concurrent reads allowed, only ONE writer. A sleeping Python process (e.g. waiting on API rate limit) holds an open transaction = permanent lock. `PRAGMA busy_timeout` only helps with short locks (active writes), NOT sleeping processes.

**How to apply:**

- Before launching DB-write scripts → `fuser db/pipeline.db` to check locks.
- If locked by another session → wait or ask user to kill.
- Commit every 100 rows (not 1000 or 10000) — shorter transactions = less lock time.
- Use `PRAGMA synchronous=OFF` for single-transaction bulk writes (faster, crash-safe via backup).
- ThreadPoolExecutor for file I/O → collect in RAM → single bulk DB write (avoids holding lock during slow I/O).
- Never run two DB-writing scripts simultaneously on same SQLite file.

## Never kill DB holders

**Rule:** Never `kill` processes that hold the SQLite DB lock. Wait for them to finish.

**How to apply:** When a DB is locked by another process, wait and poll (cron/sleep) until the blocker finishes. Only kill YOUR OWN test processes that you started and know are safe to kill (no pending writes).

## Bulk writes logging

**Rule:** For bulk DB writes (`executemany`, INSERT loops) on >100K rows — always batch with progress log. Unbatched = black box.

**Evidence:** 5.75M upsert hung for 10+ min without a single log line. Unclear whether it was alive or how much was left.

**How to apply:** replace `conn.executemany(sql, all_rows)` with:

```python
for i in range(0, len(rows), BATCH):
    conn.executemany(sql, rows[i:i+BATCH])
    conn.commit()
    logger.info(f"  Written {min(i+BATCH, len(rows))}/{len(rows)} rows")
```

`BATCH = 10K-50K` for balance between speed and logging.

Related: [[bulk-sqlite-ram-disk]] for the I/O bottleneck side.

_Confidence: high · Source: my-project_
