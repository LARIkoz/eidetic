---
type: reference
title: Reciprocal Rank Fusion
aliases: ["reciprocal-rank-fusion"]
tags: ["reference"]
---

# Reciprocal Rank Fusion

RRF merges several ranked lists by summing 1/(k+rank) for each item, so an item ranked highly by any retriever floats up. It needs no score calibration, which makes it a robust way to combine keyword and vector search.

## Related

- [[fts5-full-text-search]]
- [[vector-embeddings-basics]]
- [[cross-encoder-reranking]]
- [[references-moc]]
