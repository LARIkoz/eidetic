---
type: rule
title: Bulk SQLite import — use RAM disk
aliases: ["bulk-sqlite-ram-disk"]
tags: ["rule"]
---

# Bulk SQLite import — use RAM disk

> For large imports (10M+ rows), move DB to RAM disk first. Always check hardware-optimization patterns before bulk I/O.

**Why:** A keywords import benchmark: 60M rows took ~40 min on SSD. CPU was 43%, RAM 2.2GB — bottleneck was disk I/O. RAM disk would cut this to ~10 min.

## Details

For bulk SQLite imports (10M+ rows), use RAM disk to eliminate I/O bottleneck.

**How to apply:**

1. Before any bulk import >10M rows → check hardware-optimization patterns first
2. Create RAM disk: `diskutil erasevolume HFS+ "RAMDisk" $(hdiutil attach -nomount ram://8388608)` (4GB)
3. Copy DB → RAM disk → run import → copy back
4. Risk mitigation: keep SSD copy as backup, verify checksums after copy back
5. Also applies to: embedding regeneration, full table scans, VACUUM

Related: [[db-operations-backup-locking]] (locking and corruption side of bulk writes).

_Confidence: high · Source: my-project_
