---
name: eidetic-base
description: Build and attach an Eidetic topic base — a PULL knowledge-base ("wiki base") over an external corpus (scraped API docs, a product methodology, a book) that you attach to a project over MCP and query on demand. Use when the user wants to turn a source into a queryable base. Triggers — "build a topic base", "make a knowledge/wiki base from <docs|API|book>", "attach an eidetic base to this project", "собери базу / вики-базу из <источник>", "подключи базу к проекту", "eidetic base". NOT for recalling personal memory (use /memory-recall); never index an external corpus into personal memory.
---

# Eidetic topic base — build & attach

A **topic base** is a **PULL** knowledge-base: an isolated corpus you attach per project and
query on demand — the opposite of personal memory (**PUSH**, auto-injected every session).
Same engine (e5-large + FTS5 + vectors), separate index, its own git repo.

**Full reference:** `docs/topic-bases.md` in the eidetic repo —
<https://github.com/LARIkoz/eidetic/blob/main/docs/topic-bases.md> (storage model, ingest
scenarios, API-doc gotchas, the complete agent contract).

The CLI is `python3 ~/.claude/memory-system/bin/base.py <cmd>` (alias it as `eidetic-base`).

## Contract — build a base end to end

Hand the agent a source, then run these real commands:

1. **Init** — `… base.py init <name>` → scaffolds `~/eidetic-bases/<name>-base/` (set
   `$EIDETIC_BASES_DIR` to override). **Never** inside the consuming project or the eidetic repo.
2. **Ingest** — the agent scrapes the source and converts it to clean Markdown (**format
   conversion, not translation** — keep the source language), writing pages into the base's
   `docs/`:
   - one readable **page per unit** — a book → one page per chapter, an API → one page per
     endpoint; always a `docs/HOME.md` hub with `[[cross-links]]`.
   - **Raw pages, not distilled fact-cards** — no LLM auto-extraction (lossy, hallucinates,
     drops the source). RAG returns the relevant passage; it does not need pre-chewed cards.
3. **Index** — `… base.py index <name>` (full: FTS + e5 vectors).
4. **Verify** — `… base.py doctor <name>` must print `ok`; spot-check 2–3 real queries hit the
   right page.
5. **Attach** — `… base.py attach <name> --scope project` (run **inside** the target project;
   add `--run` to execute the printed `claude mcp add …` line). Exposes `<name>_search` /
   `<name>_search_detail` / `<name>_serendipity` / `<name>_add`. Detach: `claude mcp remove <name>`.

## Invariants — do not break

- **Isolation** — a base indexes ONLY its own `corpus_dirs`, never `~/.claude`; it is PULL,
  never auto-injected like personal memory. **Never** index an external corpus into personal
  memory (it would inject that corpus into every unrelated session).
- **Curate-write is explicit-only** — `add` / `<name>_add` run **only** when the user says
  "save this to the base". Never write to a base autonomously; what the agent learns on its
  own compounds into **core** memory, not a base.
- **Storage** — bases live under one root outside any project tree (`$EIDETIC_BASES_DIR` ▸
  `~/eidetic-bases`); each base is its **own git repo** (source committed, `db/` gitignored +
  rebuilt). A base is a separate repo → publishing eidetic never carries a base's contents.

## Add to a base later (curated)

> "Save this to the `<name>` base: `<fact / file>`."

→ `… base.py add <name> --text "<markdown>"` (or `--file F` `--title T` `--as note|doc`):
writes a `source: user` page and reindexes. Only on an explicit instruction.

## When NOT to use this skill

- Recalling **your own** memory / past decisions → use `/memory-recall`, not a base.
- A one-off question you can answer directly → just answer; don't build a base.

## Storage model (summary — full table in docs/topic-bases.md)

```
<name>-base/
  .eidetic-base.json     # {name, corpus_dirs:["docs","notes"], db:"db/index.db"}
  docs/  HOME.md  api/<endpoint>.md  guides/<topic>.md  library/<book>/01-….md
  notes/<fact>.md        # curated, source: user
  db/                    # gitignored, rebuilt locally
```

| You add…          | Stored as                                      | Split?                |
| ----------------- | ---------------------------------------------- | --------------------- |
| an atomic fact    | one `note` in `notes/`                         | no                    |
| a web article     | one `doc` page in `docs/` (kept whole)         | no (chunked by heads) |
| a whole **book**  | per-chapter `doc` pages + `HOME.md` TOC        | yes — by chapter      |
| full **API docs** | page-per-endpoint tree in `docs/api/` + `HOME` | per page              |
