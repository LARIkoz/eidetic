---
type: reference
title: SQLite Indexing
aliases: ["sqlite-indexing"]
tags: ["reference"]
---

# SQLite Indexing

An index trades write cost and disk for fast lookups on a column or expression. Cover the columns a query filters and sorts on; an unused index is pure overhead. Measure with EXPLAIN QUERY PLAN before adding one.

## Related

- [[sqlite-wal-mode]]
- [[fts5-full-text-search]]
- [[profile-before-optimising]]
- [[references-moc]]
