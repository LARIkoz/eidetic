# Engine API v1

## What this is — and what it is not

The **Engine API** (`bin/engine.py`, `ENGINE_API = "1.0"`) is the versioned
seam over Eidetic's embedding / vector-store / search core. Memory, topic bases,
and your own consumers build on the *same* engine through one small public
surface, instead of reaching into private internals.

It **is** a module inside the installed tree with a stable, versioned contract.
It is **not** a pip package, **not** a network service, and **not** a separate
release cycle — it ships and versions with Eidetic. Everything else under `bin/`
is private and may change without notice; only the names in `engine.py` are the
contract.

## The three floors

```
┌─────────────────────────────────────────────────────────────┐
│  Personal memory (PUSH)   Topic bases (PULL)   Your consumer  │   floor 3: products
│      index.sh / search        base.py            (your skill) │
└───────────────┬───────────────────┬──────────────────┬───────┘
                │                   │                  │
                ▼                   ▼                  ▼
        ┌───────────────────────────────────────────────────┐
        │                  bin/engine.py                     │   floor 2: the door
        │  model_info · configure · embed_passages/query     │   (Engine API v1)
        │  open_index → Index(upsert/search/stamp/…) · rerank │
        └───────────────────────────┬───────────────────────┘
                                     ▼
        ┌───────────────────────────────────────────────────┐
        │        embed.py  ·  rerank.py   (private core)     │   floor 1: internals
        │   e5-large embedder · vectors.db · cross-encoder   │
        └───────────────────────────────────────────────────┘
```

Floor 3 never imports floor 1 directly; it goes through the door. That is how a
new consumer (the task-tracker skill below) gets the same drift-guarded search,
CPU-pin policy, and content-hash authority the memory product uses — for free.

## Quickstart

After `bash install.sh`, from `~/.claude/memory-system/bin`:

```python
import engine

engine.require("1")                       # guard the major version
engine.configure(provider="cpu", threads=8)   # long-lived-process-safe policy
print(engine.model_info())                # {'model', 'dim', 'hash_scheme', 'fastembed', 'engine_api'}

texts = ["incremental rebuild of the index",
         "политика ротации ключей"]        # any language the model covers
blobs = engine.embed_passages(texts)      # raises EngineUnavailable if the model is absent

idx = engine.open_index("/tmp/demo/vectors.db")
idx.upsert([{"chunk_id": 1, "path": "doc-1", "name": "rebuild-note",
             "section_heading": "note", "embedding": blobs[0],
             "content_hash": engine.content_hash("rebuild-note", "", texts[0], "note")}])
idx.stamp()                               # record model/dim stamps for the drift guard
for hit in idx.search("reindex rebuild", limit=3):
    print(round(hit["score"], 3), hit["path"], hit["name"])
idx.close()
```

Field names are the storage names, unmapped: **`path` = your stable key**,
**`section_heading` = your kind label**, `name` = a display label.

## Contract table

| Function | Promise | On an environment problem |
|---|---|---|
| `require(major)` | no-op if the ENGINE_API major matches | raises `EngineUnavailable` on mismatch |
| `model_info()` | pure metadata; never loads the model | never raises |
| `configure(provider, threads)` | sets the process-wide provider/threads policy for the next model loads | never raises |
| `embedding_text` / `content_hash` | canonical text composition / the sole hash authority (model-free) | never raises |
| `embed_passages(texts)` | index-time float32 blobs (dim = `model_info()["dim"]`) | **raises `EngineUnavailable`** — a builder must never write nothing |
| `embed_query(text)` | query-time vector (advanced) | **raises `EngineUnavailable`** |
| `acquire_build_lock(path)` | exclusive build lock handle, or `None` if held | never raises |
| `open_index(path)` | opens/creates the index | never raises on a fresh file |
| `Index.upsert/delete/existing_hashes/stamp/stats` | build + doctor primitives | sqlite errors propagate (a build failure is loud) |
| `Index.search(query, limit)` | drift-guarded cosine hits | **SOFT**: `[]` + one stderr reason on a missing model or stamp drift |
| `rerank(query, docs)` | cross-encoder scores aligned with `docs` | **SOFT**: `[]` + one stderr reason (once per process) |

The rule of thumb: **reads degrade, builds shout.** A search on a box with no
model still answers from the lexical path; a build that cannot embed refuses
loudly rather than writing an empty index.

## Versioning and drift stamps

`ENGINE_API` is `MAJOR.MINOR`.

- **MAJOR bumps** on any break: a renamed/removed export, a changed signature or
  semantics, or a storage-schema change to the vector store.
- **MINOR bumps** are additive only (new functions/fields that do not disturb
  existing callers).

`Index.stamp()` records the embedder identity (model / dim / hash-scheme /
fastembed version) into the index. `Index.search` compares those stamps against
the live embedder and, on a mismatch (someone changed the model or upgraded
fastembed under a built index), returns no hits with a one-line reason instead of
silently scoring meaningless cosines. Rebuild (`index.sh --full`) re-stamps.

## Reference consumer — task-tracker semantic search

The first external consumer is a **task-tracker search skill** (YouGile): live
tasks and their chat, embedded into the skill's *own* index, queried through this
same engine — roughly 30 lines of integration (`configure` → `open_index` →
`existing_hashes` for the incremental diff → `upsert`/`stamp` → `search`, with
`rerank` as an opt-in salvage pass). It runs on a **10k+ task** corpus (~27k
vectors) with **sub-second warm queries**, and inherits the drift guard and
CPU-pin policy unchanged.

Because the model is multilingual, a query in one language finds a task written
in another. Two real examples:

- `перед техосмотром` → the emission-test / inspection-prep task (a cross-lingual
  hit: Russian query, differently-worded task).
- `ключи ротация` → the key-rotation task.

Same engine, a different index, zero forks — which is the point of the door.
