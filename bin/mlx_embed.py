#!/usr/bin/env python3
"""Eidetic v6 — self-contained MLX embedding backend (Apple-Silicon native).

WHY: the fastembed/onnxruntime path leaks CoreML compile temp (~0.96 GB + ~772
$TMPDIR files per embed run) and can OOM the CoreML compiler. This module is a
native MLX forward pass for the SAME model (intfloat/multilingual-e5-large, an
XLM-RoBERTa-large encoder, 1024-d) using the full-precision MLX weights at
`mlx-community/multilingual-e5-large-mlx` — so vectors are byte-faithful to the
fastembed geometry (measured cosine ≈ 1.0000 on RU+EN) with ZERO onnxruntime.

Deps: `mlx` + `tokenizers` ONLY — deliberately NOT the fragile `mlx-embeddings`
(0.1.0, transformers>=5.0 pin-hell) nor `transformers`/`mlx_lm`.

Import-safe: `import mlx.core` is DEFERRED into the functions, so this module is
importable (and embed.py's engine selection is testable) on a host WITHOUT mlx.
`available()` reports the runtime; the heavy paths raise a clear error if absent.
"""

import os
import sys

# XLM-RoBERTa-large / multilingual-e5-large architecture (fixed — this module only
# serves this one model, the fastembed default's twin).
MLX_MODEL_REPO = "mlx-community/multilingual-e5-large-mlx"
HIDDEN = 1024
LAYERS = 24
HEADS = 16
HEAD_DIM = HIDDEN // HEADS          # 64
INTERMEDIATE = 4096
MAX_POSITION = 514
LAYER_NORM_EPS = 1e-5
PAD_TOKEN_ID = 1                    # RoBERTa pad id — also the position offset
MAX_TOKENS = 512                    # e5 context window (fastembed truncates here)

# Geometry marker for the vectors.db stamp: bump if this encoder's math changes so
# the search-time guard rebuilds instead of mixing geometries.
MLX_ENGINE_VERSION = "xlmr-mlx-1"

# Persistent, env-overridable model cache (never $TMPDIR — the whole point).
MLX_CACHE = (os.environ.get("EIDETIC_MLX_CACHE_PATH")
             or os.path.expanduser("~/.cache/eidetic-mlx"))


def available():
    """True iff the MLX runtime is importable on this host."""
    import importlib.util
    try:
        return importlib.util.find_spec("mlx.core") is not None
    except ModuleNotFoundError:
        return False


def _require_mlx():
    if not available():
        raise RuntimeError(
            "EIDETIC_EMBED_ENGINE=mlx but the `mlx` package is not installed. "
            "Install `mlx` + `tokenizers`, or unset EIDETIC_EMBED_ENGINE to use "
            "the fastembed default.")


# --- weight-key resolution (robust to conversion prefixes) -------------------
def _strip_prefix(weights):
    """HF/MLX conversions sometimes prefix every key with `roberta.`/`model.`/
    `bert.`. Strip a single common leading component so the canonical HF names
    below resolve regardless of the exporter."""
    for pre in ("roberta.", "model.", "bert.", "xlm_roberta."):
        if all(k.startswith(pre) or k.startswith("pooler.") for k in weights):
            return {k[len(pre):] if k.startswith(pre) else k: v
                    for k, v in weights.items()}
    return weights


def _pick(weights, *names):
    for n in names:
        if n in weights:
            return weights[n]
    raise KeyError(f"none of {names} in weights (have e.g. {list(weights)[:4]}…)")


_STATE = None


def _load():
    """Download (once) + load the MLX weights and the repo tokenizer. Cached."""
    global _STATE
    if _STATE is not None:
        return _STATE
    _require_mlx()
    import glob
    import mlx.core as mx
    from huggingface_hub import snapshot_download
    from tokenizers import Tokenizer

    os.makedirs(MLX_CACHE, exist_ok=True)
    local = snapshot_download(
        MLX_MODEL_REPO, cache_dir=MLX_CACHE,
        allow_patterns=["*.safetensors", "tokenizer.json", "config.json"])

    weights = {}
    for st in sorted(glob.glob(os.path.join(local, "*.safetensors"))):
        weights.update(mx.load(st))
    weights = _strip_prefix(weights)
    # The mlx-community conversion stores float16; fastembed's geometry is float32.
    # Upcast to float32 so (a) the encoder is byte-faithful to fastembed and (b) the
    # additive attention mask (-1e9) stays FINITE — in float16 it overflows to -inf
    # and 0·(-inf) NaNs every real token. e5-large is small; fp32 cost is trivial.
    weights = {k: v.astype(mx.float32) for k, v in weights.items()}

    tok = Tokenizer.from_file(os.path.join(local, "tokenizer.json"))
    tok.enable_truncation(max_length=MAX_TOKENS)
    _STATE = (weights, tok)
    return _STATE


# --- the XLM-RoBERTa forward pass in MLX --------------------------------------
def _layer_norm(mx, x, w, b):
    mu = mx.mean(x, axis=-1, keepdims=True)
    var = mx.var(x, axis=-1, keepdims=True)
    return (x - mu) * mx.rsqrt(var + LAYER_NORM_EPS) * w + b


def _linear(mx, x, w, b):
    # HF Linear stores weight as [out, in]; y = x @ Wᵀ + b.
    return x @ w.T + b


def _forward(mx, W, input_ids, mask):
    import mlx.nn as nn
    B, T = input_ids.shape
    m = mask.astype(mx.int32)
    # RoBERTa absolute positions: (cumsum(mask)·mask) + pad_id → real tokens start
    # at pad_id+1, pads stay at pad_id. THIS scheme is load-bearing for faithfulness.
    positions = (mx.cumsum(m, axis=1) * m) + PAD_TOKEN_ID

    we = _pick(W, "embeddings.word_embeddings.weight")
    pe = _pick(W, "embeddings.position_embeddings.weight")
    tte = _pick(W, "embeddings.token_type_embeddings.weight")
    h = we[input_ids] + pe[positions] + tte[mx.zeros((B, T), dtype=mx.int32)]
    h = _layer_norm(mx, h,
                    _pick(W, "embeddings.LayerNorm.weight", "embeddings.LayerNorm.gamma"),
                    _pick(W, "embeddings.LayerNorm.bias", "embeddings.LayerNorm.beta"))

    # additive attention mask: 0 for real tokens, -inf for pads (broadcast over heads).
    neg = mx.array(-1e9, dtype=h.dtype)
    add_mask = (1 - mask.astype(h.dtype))[:, None, None, :] * neg  # [B,1,1,T]
    scale = 1.0 / (HEAD_DIM ** 0.5)

    for i in range(LAYERS):
        p = f"encoder.layer.{i}."
        q = _linear(mx, h, _pick(W, p + "attention.self.query.weight"),
                    _pick(W, p + "attention.self.query.bias"))
        k = _linear(mx, h, _pick(W, p + "attention.self.key.weight"),
                    _pick(W, p + "attention.self.key.bias"))
        v = _linear(mx, h, _pick(W, p + "attention.self.value.weight"),
                    _pick(W, p + "attention.self.value.bias"))

        def heads(x):
            return x.reshape(B, T, HEADS, HEAD_DIM).transpose(0, 2, 1, 3)
        qh, kh, vh = heads(q), heads(k), heads(v)
        scores = (qh @ kh.transpose(0, 1, 3, 2)) * scale + add_mask
        attn = mx.softmax(scores, axis=-1)
        ctx = (attn @ vh).transpose(0, 2, 1, 3).reshape(B, T, HIDDEN)

        attn_out = _linear(mx, ctx, _pick(W, p + "attention.output.dense.weight"),
                           _pick(W, p + "attention.output.dense.bias"))
        h = _layer_norm(mx, attn_out + h,
                        _pick(W, p + "attention.output.LayerNorm.weight",
                              p + "attention.output.LayerNorm.gamma"),
                        _pick(W, p + "attention.output.LayerNorm.bias",
                              p + "attention.output.LayerNorm.beta"))

        inter = nn.gelu(_linear(mx, h, _pick(W, p + "intermediate.dense.weight"),
                                _pick(W, p + "intermediate.dense.bias")))
        out = _linear(mx, inter, _pick(W, p + "output.dense.weight"),
                      _pick(W, p + "output.dense.bias"))
        h = _layer_norm(mx, out + h,
                        _pick(W, p + "output.LayerNorm.weight", p + "output.LayerNorm.gamma"),
                        _pick(W, p + "output.LayerNorm.bias", p + "output.LayerNorm.beta"))

    # e5 uses MEAN pooling over the attention mask (NOT CLS), then L2-normalize.
    mf = mask.astype(h.dtype)[:, :, None]
    summed = mx.sum(h * mf, axis=1)
    counts = mx.maximum(mx.sum(mf, axis=1), 1e-9)
    pooled = summed / counts
    norm = mx.sqrt(mx.sum(pooled * pooled, axis=-1, keepdims=True)) + 1e-12
    return pooled / norm


def _encode(texts):
    """Tokenize + forward + pool → an [N, 1024] mlx array (L2-normalized)."""
    import mlx.core as mx
    W, tok = _load()
    encs = tok.encode_batch(list(texts))
    maxlen = max((len(e.ids) for e in encs), default=1)
    ids = [e.ids + [PAD_TOKEN_ID] * (maxlen - len(e.ids)) for e in encs]
    att = [[1] * len(e.ids) + [0] * (maxlen - len(e.ids)) for e in encs]
    input_ids = mx.array(ids, dtype=mx.int32)
    mask = mx.array(att, dtype=mx.int32)
    out = _forward(mx, W, input_ids, mask)
    mx.eval(out)
    return out


def embed_texts(texts):
    """Encode already-prefixed passages → list of float32 blobs (1024-d, L2-norm),
    byte-format identical to embed.embed_texts (np.float32.tobytes)."""
    import numpy as np
    if not texts:
        return []
    out = _encode(texts)
    arr = np.array(out, dtype=np.float32)
    return [arr[i].tobytes() for i in range(arr.shape[0])]


def embed_query_texts(texts):
    """Same as embed_texts — callers prepend the query prefix, mirroring embed.py."""
    return embed_texts(texts)


def main(argv=None):
    argv = argv or sys.argv
    if not available():
        print("mlx not installed", file=sys.stderr)
        return 1
    texts = argv[1:] or ["passage: hello world"]
    for t, blob in zip(texts, embed_texts(texts)):
        import numpy as np
        v = np.frombuffer(blob, dtype=np.float32)
        print(f"{len(v)}d |v|={float(np.linalg.norm(v)):.4f}  {t[:40]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
