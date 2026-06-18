---
type: reference
title: Cross-Encoder Reranking
aliases: ["cross-encoder-reranking"]
tags: ["reference"]
---

# Cross-Encoder Reranking

A cross-encoder reads the query and a candidate together and scores their relevance directly, which is far more accurate than comparing embeddings — but too slow to run over a whole corpus. Use it to rerank the top results from a cheap first pass.

## Related

- [[vector-embeddings-basics]]
- [[reciprocal-rank-fusion]]
- [[cosine-similarity]]
- [[references-moc]]
