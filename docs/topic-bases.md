# Topic bases — attachable knowledge-bases alongside your memory

Eidetic is your **PUSH** memory: it auto-injects relevant cards into _every_ Claude
Code session from your own work (projects, agent-memory, skills). That's exactly what
you want for _your_ context — and exactly what you DON'T want for an external corpus
(a SaaS help-center, an API reference, a book). If you indexed a product's docs into
your personal memory, every coding session would start getting that product's chunks
injected as "memory".

A **topic base** is the other half: a **PULL** knowledge-base. A separate, isolated
corpus you query on demand and **attach only to the projects that need it** — never
polluting your work-memory recall.

> Personal memory = PUSH (always on, auto-injected). Topic base = PULL (attached per
> project, queried explicitly). Same engine (multilingual-e5-large + FTS5 + vectors),
> separate index, separate lifecycle.

## When to build one

- You want to **ask questions about an external corpus** and get answers with sources,
  inside chosen projects (e.g. "which endpoint creates a transaction, and what params?").
- The corpus is **reusable** across projects — the same API is consumed by several agents.

Do **not** put external docs in your personal memory index — the scan roots are your
work dirs and the content auto-injects into unrelated sessions.

## Where a base lives

A base is its **own git repo** (e.g. `~/eidetic-bases/<name>-base/`), versioned and portable.

- **All bases share one root, OUTSIDE any project tree.** `eidetic base init <name>`
  scaffolds `<root>/<name>-base/`, where `<root>` is `$EIDETIC_BASES_DIR` if set, else
  `~/eidetic-bases/` — **never the current working directory**. That keeps a base from
  landing loose inside whatever project you happen to be in. Point `EIDETIC_BASES_DIR` at
  your own bases folder, or pass `--dir` for a one-off location.
- **One base, many projects.** Build it once; attach it wherever needed. Never store a
  reusable corpus _inside_ a consuming project — that duplicates it per project, bloats
  the repo, and forces a re-scrape. **API docs are ALWAYS a separate base from the
  project that calls the API.**
- **Source-only in git** — `docs/` + `notes/` + the manifest are committed; the index
  (`db/`) is gitignored and rebuilt locally. The index stores absolute paths, so
  rebuilding-on-clone keeps the base **portable**: move or re-clone it anywhere and it
  still works (committing the index would freeze the old machine's paths).

## Build it (host-only: your agent scrapes, eidetic hosts)

Eidetic hosts the folder and serves it; **the scraping is done by your agent** (every
site differs — auth, JS-rendering, rate-limits — so a universal scraper doesn't belong
in the engine). Hand your agent a source and this:

> "Build a **topic base** named `<name>` from `<URL>`. Scrape the public docs, convert
> to clean Markdown (keep the source language — format conversion, not translation),
> organize into the storage model below, then index it as an **isolated** base (its own
> DB, no session auto-injection) and expose it over MCP so I can attach it per-project."

## Storage model

```
<name>-base/
  .eidetic-base.json          # {name, corpus_dirs:["docs","notes"], db:"db/index.db"}
  docs/                       # ingested        (type: reference, source: docs)
    HOME.md                   # hub + [[cross-links]]
    api/<endpoint>.md         # one endpoint = one page (params/request/response/errors)
    schemas/<object>.md
    guides/<topic>.md
    library/<book>/HOME.md 01-….md   # a book = a folder of per-chapter pages
  notes/<fact>.md             # curated         (type: note, source: user)
  db/                         # gitignored, rebuilt locally
```

Two rules decide "how does X get stored":

1. **File = storage unit, section = retrieval unit.** Keep a readable page; the engine
   chunks it by heading. Don't shred a page into per-sentence confetti.
2. **Split only when one file is too big** for coherent retrieval (a book → per-chapter;
   a giant API → per-endpoint). Always leave a `HOME.md` index with cross-links.

### Ingest scenarios

| You add…                 | It is stored as                                                         | Split?                       |
| ------------------------ | ----------------------------------------------------------------------- | ---------------------------- |
| an atomic fact           | one `note` card in `notes/`                                             | no                           |
| a web article (one page) | one `doc` page in `docs/` (kept whole)                                  | no — chunked by its headings |
| a whole **book**         | a folder of per-chapter `doc` pages + `HOME.md` TOC                     | yes — by H1/chapter          |
| full **API docs**        | a page-per-endpoint tree in `docs/api/` + `schemas/` + `guides/` + HOME | per page                     |
| a full doc-site          | a page tree mirroring the site + HOME hub                               | per page                     |

**Raw, not distilled.** A book/article is stored as **pages** (chunked by section) — RAG
returns the relevant passage, never the whole book. An LLM does **not** auto-extract
"fact cards" from it: that is lossy, can hallucinate facts not in the text, and loses the
source. Atomic cards are added only by **deliberate curation** (see below).

### For API docs specifically — what breaks

- **Auth-gated docs** → the scraper needs your account/session.
- **JS-rendered docs** (SPA) → a naive fetch gets an empty shell; the agent must render.
- **Prefer the OpenAPI/Swagger spec** over scraping HTML if one exists — it's structured
  and complete; generate one page per endpoint from it.
- **Pin the version** — v1/v2 mixed in one base makes the agent call a v1 pattern on v2.
- **Staleness** — APIs change; re-`refresh` on a cadence or the agent will call a removed
  endpoint.

## Add to a base later (curated, human-gated)

Beyond the initial scrape, you feed a base **only by explicit instruction** — there is no
autonomous writing (that keeps the base authoritative). In an attached project:

> "Save this to the `<name>` base: <fact / file>."

The agent writes it into `notes/` (tagged `source: user`) and reindexes. Anything the
agent learns _on its own_ compounds into your **core** memory, never silently into a base.

## Attach it where you need it

A base's MCP server keys its index off `EIDETIC_MEMORY_SYSTEM`. Attach to one project:

```bash
claude mcp add <name> -s project \
  -e EIDETIC_MEMORY_SYSTEM=/path/to/<name>-base \
  -- python3 /path/to/eidetic/mcp_server.py
```

- `-s project` → written to that repo's `.mcp.json` — available **only** there (and
  shareable with the team via the repo). `-s user` → global.
- Tools are **named per base** (`<name>_search`, `<name>_ask`, `<name>_add`), so you can
  attach several at once (`stripe_search` + `acme_search`) with no collision.
- Detach: `claude mcp remove <name>`. List: `claude mcp list`.

You keep several bases and plug whichever a project needs, like a USB stick.

## Granularity — one base, or several?

A base is a **coherent knowledge domain you attach as a unit**.

- **One base per product.** `acme` holds `docs/api/` + `docs/methodology/` +
  `docs/guides/` together — a typical consumer wants both the API and the methodology.
- **Split into separate bases** only when different projects need different halves (a
  pure-integration project that needs the API but not the methodology), or when
  lifecycles/sizes diverge sharply.

## Status

Today this is a **recipe** (the steps above, run by your agent), using eidetic's existing
MCP server (which already keys off `EIDETIC_MEMORY_SYSTEM`). A turnkey
`eidetic base init <name>` / `index` / `add` / `attach` CLI — one command per step — is
the next planned feature. The only engine gap it closes is **scan-scope isolation** (so a
base indexes _only_ its corpus, never your `~/.claude` memory); the DB path, search, the
doctor, and this MCP server already key off `EIDETIC_MEMORY_SYSTEM`.
