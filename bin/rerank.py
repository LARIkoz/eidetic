#!/usr/bin/env python3
"""Eidetic v6 — multilingual cross-encoder rerank for cross-lingual recall salvage.

The bi-encoder (e5) cannot separate a true cross-lingual match (~0.83 cosine)
from topical hard-negative garbage (~0.83) — measured. Lexical corroboration is
the cheap second signal, but a RU->EN paraphrase shares zero anchor tokens with
its target, so true matches collapse to "low" and get suppressed.

A multilingual cross-encoder jointly encodes the (query, doc) pair and DOES
separate them. Calibration 2026-05-31 over the live corpus (name+section+snippet
as doc text): 10 true cross-lingual queries scored >= -0.89, 8 plausible-but-
absent garbage probes scored <= -1.66 — a clean +0.775 gap with no overlap.

Used only to rescue otherwise-suppressed vector hits; it never downgrades a
result. Lazy + bounded: the model loads only when a query is about to return
"no confident results" and there is a salvageable candidate. Any failure
(fastembed/model missing) degrades silently to the prior behaviour.
"""

import os
import sys

MODEL_NAME = "jinaai/jina-reranker-v2-base-multilingual"
# Pin fastembed's model cache to a persistent dir (env-overridable). Its default
# TMPDIR cache gets purged by macOS, silently evicting the model — the same
# failure mode that froze the e5 embeddings. Keep in sync with embed.py.
FASTEMBED_CACHE = os.environ.get("FASTEMBED_CACHE_PATH") or os.path.expanduser("~/.cache/fastembed")

_model = None
_unavailable = False


def get_model():
    global _model
    if _model is None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        _model = TextCrossEncoder(model_name=MODEL_NAME, cache_dir=FASTEMBED_CACHE)
    return _model


def scores(query, docs):
    """Relevance logits for each doc vs query (higher = more relevant).

    Returns a list aligned with ``docs``. On any failure (model missing, load
    error) returns [] and disables further attempts this process, so the caller
    degrades to its pre-rerank behaviour instead of crashing.
    """
    global _unavailable
    if _unavailable or not docs:
        return []
    try:
        model = get_model()
        return [float(s) for s in model.rerank(query, docs)]
    except Exception as e:  # pragma: no cover — defensive degrade path
        _unavailable = True
        print(
            f"WARNING: cross-encoder unavailable, skipping rerank salvage: {e}",
            file=sys.stderr,
        )
        return []


def main(argv=None):
    argv = argv or sys.argv
    if len(argv) < 3:
        print('Usage: rerank.py "<query>" "<doc1>" ["<doc2>" ...]')
        return 1
    query, docs = argv[1], argv[2:]
    for doc, score in zip(docs, scores(query, docs)):
        print(f"  {score:8.3f}  {doc[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
