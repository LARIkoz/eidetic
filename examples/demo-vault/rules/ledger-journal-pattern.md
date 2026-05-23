---
type: rule
title: LEDGER + JOURNAL pattern for data tracking
aliases: ["ledger-journal-pattern"]
tags: ["rule"]
---

# LEDGER + JOURNAL pattern for data tracking

> Use a single LEDGER.md + append-only IMPORT_JOURNAL.md in the data cache root to make "who is who" discoverable without remembering.

**Why:** Next session can read 3 files (LEDGER + JOURNAL + latest handoff state.md) and have full context. No more scattered memory guessing about "did we already run X" or "where is Y data".

**How to apply:** When starting non-trivial data work, create LEDGER.md at the root of the data directory. Add entries as operations happen. Use the template at the bottom of IMPORT_JOURNAL.md for new entries. Reference these files from handoff state.md.

## Details

When a project accumulates scraped data across multiple sessions and imports, create a two-file tracking pattern in the data cache root:

**`LEDGER.md`** — single source of truth. Living document. Tables per data type with path, file count, coverage in DB, recovery status. Edit when state changes.

**`IMPORT_JOURNAL.md`** — append-only log. Every import operation gets an entry with timestamp, source path, target table, update logic (COALESCE / UPSERT), before/after coverage, gotchas. Never rewrite.

**Proof it works:** before this pattern, had to read ~20 files to reconstruct what data existed, what was imported, what was lost. After the pattern was created, a single LEDGER.md glance answered "is dataset X recovered?" and "what's eligible coverage?" in seconds.

**Complement:** project memory files still have their place, but for pipeline/data state, LEDGER at the data dir root beats scattered memory files.

_Confidence: high · Source: my-project_
