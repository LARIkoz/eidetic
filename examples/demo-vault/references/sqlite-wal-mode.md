---
type: reference
title: SQLite WAL Mode
aliases: ["sqlite-wal-mode"]
tags: ["reference"]
---

# SQLite WAL Mode

Write-Ahead Logging lets readers and one writer work concurrently: changes append to a -wal file instead of the main database, and a checkpoint folds them back in. It is persistent once set on a database. A good default for apps with mixed read/write load.

## Related

- [[sqlite-indexing]]
- [[fts5-full-text-search]]
- [[one-source-of-truth]]
- [[back-up-before-destructive-ops]]
- [[references-moc]]
