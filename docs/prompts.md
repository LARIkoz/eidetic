# Copy-paste prompts

Three ready-to-paste prompts for driving Eidetic with an agent (Claude Code in the target
folder, or any MCP agent). Replace the `<…>` placeholders; copy the fenced block as-is. The
canonical, deep reference for each is linked under its block.

---

## 1 · Install Eidetic (if it isn't installed yet)

Hand this to an agent on a machine where Eidetic isn't set up. It is self-guarding — it
checks first and won't reinstall over an existing setup.

```
Install Eidetic — long-term memory for an AI coding agent — if it isn't already installed.

1. Check first: run  ls ~/.claude/memory-system/bin/doctor.sh
   If it exists, Eidetic is already installed — just run
   bash ~/.claude/memory-system/bin/doctor.sh, show the status, and do NOT reinstall.
2. If not installed: clone https://github.com/LARIkoz/eidetic.git and follow AGENTS.md in
   the repo (the step-by-step agent install guide).
3. Three model choices (ask me, or take the defaults):
     - embedder: multilingual (e5-large, ~100 languages) [default] OR english
       (bge-small-en, ~5x faster, English-only)
     - query translation: off [default] / auto / apple (macOS 26 on-device) / opusmt
     - card-extraction model: sonnet (quality) [default] OR haiku (cheaper)
4. IMPORTANT: install.sh installs the CORE only (FTS keyword search). For semantic /
   cross-lingual search, run  pip install fastembed  (~2.2 GB e5 model downloads on the
   first index). Without it you get keyword search only.
5. Finish by running  bash ~/.claude/memory-system/bin/doctor.sh  — show what is green and
   which tiers are active. On Claude Code, memory then auto-injects every session
   (install.sh wires the SessionStart/Stop hooks).
```

→ Full install reference: **[AGENTS.md](../AGENTS.md)**.

---

## 2 · Build a topic base (a "wiki") from a site / book / API

A **topic base** is a PULL knowledge-base — a separate corpus you attach per project, not your
personal memory. This prompt triggers the **`/eidetic-base`** skill. Set `<name>` and `<SOURCE>`.

```
Build an Eidetic topic base "<name>" from <SOURCE: site URL / path to an EPUB|PDF / API docs URL>.
This is a PULL base (a separate corpus, NOT my personal memory). Follow the contract:

1. init:   python3 ~/.claude/memory-system/bin/base.py init <name>
           (creates ~/eidetic-bases/<name>-base/)
2. ingest: scrape the source yourself and convert it to clean Markdown — format conversion,
           NOT translation (keep the source language). Write pages into <base>/docs/,
           one readable page per unit:
             - site / docs: one page per section; for an API, one page per endpoint (prefer
               the OpenAPI/Swagger spec over scraping HTML); add a HOME.md hub with [[links]].
             - a book: one page per chapter (EPUB -> md; PDF via pdftotext) + a HOME.md TOC.
             - RAW pages, not distilled "fact-cards" — no LLM auto-extraction (it drops the
               source and hallucinates facts not in the text).
3. index:  python3 ~/.claude/memory-system/bin/base.py index <name>
4. verify: python3 ~/.claude/memory-system/bin/base.py doctor <name>   (must print "ok";
           spot-check 2-3 queries return the right page)
5. Show me the result and say it's ready to attach. Do NOT attach — that's a separate step,
   run inside the project that needs the base.
```

→ Full guide, storage model, ingest scenarios, API-doc gotchas: **[docs/topic-bases.md](topic-bases.md)**.

---

## 3 · Attach a base to a project

Run this **inside the root of the project** that needs the base (it writes that project's
`.mcp.json`).

```
Attach the Eidetic base "<name>" to this project (I'm in the project root):

  python3 ~/.claude/memory-system/bin/base.py attach <name> --scope project --run

This writes the base's MCP server into the project's .mcp.json. You'll then have the tools
<name>_search / <name>_search_detail / <name>_serendipity / <name>_add. Verify with
claude mcp list. Detach later with  claude mcp remove <name>.
```

→ Attach mechanics + per-base tools: **[docs/topic-bases.md](topic-bases.md#attach-it-where-you-need-it)**.

---

> **One base, many projects.** Build a base once (prompt 2), attach it wherever it's needed
> (prompt 3). A base is its own git repo outside the eidetic tree — your corpora stay private
> even though Eidetic itself is public.
