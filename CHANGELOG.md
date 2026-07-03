# Changelog

All notable changes to Eidetic are documented here.

## v5.13.1 (2026-07-03)

Ranking-correctness fix: makes the flagship "drift down-ranks stale memories" claim actually true — an adversarial claims-vs-reality audit of v5.13.0 found it overstated. No new dependencies; the fix is guarded by tests that fail on the pre-fix code.

**Drift-penalty ranking (`search_impl.py`, `assemble_context.py`):**

- **Drift penalties now multiply into the freshness factor instead of replacing it.** v5.13.0 let a penalty _overwrite_ freshness, so on any card older than 30 days (freshness 0.5) a broken-wikilink finding (0.8×) actually _raised_ the card's score by +60%, and an age-stale finding (0.5×) was a no-op against the identical freshness decay. Multiplying is monotonic: a drift finding can never raise a card, a stale-and-drifted card always ranks below a merely-old one, and confidence escalation (0.3×) stays the strongest down-rank.
- **The `first_seen > 1` grace gate is now documented** (in code and the README drift section): a finding starts penalizing only on its second detection (drift runs are ≥24 h apart), so a transient mis-detection never down-ranks a card — until then it appears in diagnostics only.

## v5.13.0 (2026-07-02)

Reliability + honest-diagnostics release: everything found by a full self-audit of a clean install. No behavior changes to search/ranking.

**Runtime defects fixed:**

- **Hook timeouts were registered in milliseconds but Claude Code reads seconds.** `install.sh`/`update.sh` wrote `5000`/`180000` → effective ~83 min (SessionStart, session-blocking) / ~50 h (Stop) caps. Now `60`/`300` seconds; existing installs migrate automatically on their next update (both scripts update the hook entries in place).
- **The plain `codex exec` extraction route had no timeout** — on a codex-only install (`EIDETIC_SIGNAL_SKIP_CLAUDE=1`) one network hang made the Stop hook immortal. Both invocations are now bounded by `EIDETIC_SIGNAL_CODEX_TIMEOUT` (default 120 s): coreutils `timeout` when present, else a pure-bash process-group watchdog (macOS ships no `timeout`). Live-verified: a hanging codex is killed at exactly the bound, no orphaned processes.
- **Extracted signals were silently discarded when the runtime lock was busy** (two sessions ending together → one session's memories vanished after the LLM spend). `lock_runner.py` gains an opt-in `--busy-exit N`; the Stop hook now spools signals to `signals-spool/` on contention and drains the spool (oldest-first, cap 20, removed only after a successful compound) under the lock on the next session end.
- **Compounding was effectively dead:** the dedup query was a strict FTS5 _phrase_ of up to 6 extracted keywords — non-contiguous in any real document, so it matched ~nothing and every signal became a new card. Now staged: phrase first (precise), then one implicit-AND retry of the top 4 keywords; deliberately no loose OR stage (false-compounding is worse than a new card). Covered by new `tests/test_compound_dedup.py`.
- **`update.sh` installed unpinned GitHub HEAD into auto-executed hooks with no integrity check.** Now a full-history clone with a fast-forward-only guard (`merge-base --is-ancestor`): rewritten upstream history aborts loudly (override: `EIDETIC_UPDATE_FORCE=1`); updates are recorded on the op-log.

**Honest diagnostics on stock installs (a fresh healthy install no longer looks broken):**

- doctor: a never-generated Obsidian vault (an optional projection) no longer flips the verdict to "❌ broken"; the fastembed hint is Python-version-aware (the 0.8.0 pin has no wheel for `python3 <3.10` — the old hint prescribed an impossible `pip install`); the Models section now surfaces the codex-only extraction override (`EIDETIC_SIGNAL_SKIP_CLAUDE`, read from env or settings.json) instead of claiming Claude runs extraction.
- health: "No hook backups" is expected on a fresh install — no longer an error.
- recall_smoke: the generic `type: code` cases SKIP with the reason when Eidetic's own code is absent from the (optional tree-sitter) code index, instead of failing 3/4 and reading as broken recall.
- MCP server: `serverInfo.version` now reports the installed version (was hardcoded `5.0.1`).

## v5.12.8 (2026-07-02)

- **Privacy: context assembly no longer ships hardcoded personal rule clusters.** `assemble_context.py`'s `RULE_CLUSTERS` was a two-entry literal whose `summary` strings were injected verbatim into `~/.claude/rules/memory-context.md` as "ALWAYS APPLY" behavioral rules the moment ≥3 of a user's feedback cards matched the patterns — and those summaries carried the maintainer's private operating rules and account names, so every public install could surface another person's rules. `RULE_CLUSTERS` now **ships empty** and is loaded at runtime from an optional, git-ignored local config (`$EIDETIC_RULE_CLUSTERS`, else `<memory-system>/rule_clusters.json`). Absent or malformed → `[]`, so every feedback card is listed individually via the existing tiered path (P3 never-invisible is unchanged). Added `rule_clusters.json` to `.gitignore`.

## v5.12.7 (2026-06-30)

- **CoreML (GPU/ANE) embedding on Apple Silicon — ~5–10× faster than the CPU default.** `embed.py` now passes `["CoreMLExecutionProvider", "CPUExecutionProvider"]` to fastembed on macOS `arm64` (override via `EIDETIC_EMBED_PROVIDERS`; fail-safe to pure CPU if a provider can't init or the fastembed lacks the `providers` arg). On a slow M1 a full re-embed drops from tens of minutes to ~1 minute. The CPU fallback stays in the provider list, and mixed CPU/GPU vectors remain cosine-equivalent (same model + pooling; float-precision only).
- **SessionStart embed no longer stalls or cries wolf on slow machines.** The embed timeout is configurable (`EIDETIC_EMBED_TIMEOUT`, default 30s) so a large unembedded backlog on a slow CPU can converge instead of timing out short every session. A timeout now also **clears a stale `embed-last.log` failure marker** — reaching a timeout means the embedder started (its deps, incl. numpy, imported OK), so a since-resolved earlier crash is no longer current and the doctor stops reporting a fixed problem forever. (Root-caused on a live M1 install: a 2026-06-24 numpy-absent crash kept the doctor red for days while 30s timeouts left 153 chunks unembedded.)
- **README: install slimmed + honest per-CLI capture-model matrix.** `## Install` collapses the 3-model prompt walkthrough (the installer asks inline) and points agent installs at [AGENTS.md](AGENTS.md); **Session-End Auto-Capture** replaces "defaults to Sonnet" with an explicit Claude (`sonnet`/`haiku`) vs Codex (`gpt-5.5`/`gpt-5.3-codex-spark`, subscription-safe) table and states that Gemini/Grok recall via MCP but don't run extraction yet. Folds the prior docs polish (MCP / Performance / competitor table / Roadmap collapse + session auto-capture elevated into "What is it?").
- **`doctor` stops reporting a since-fixed embed crash.** The `embed-last.log` "last session embed FAILED" warning is now suppressed when `vectors.db` is newer than the log — i.e. a later embed succeeded, so the logged crash is stale. Belt-and-suspenders with the timeout-clear above: a real, current failure still warns loudly; a resolved one no longer erodes trust. (`run_incremental` already re-embeds every chunk that lacks a vector and commits per-batch, so a backlog converges across sessions once the timeout no longer cuts it short.)
- **`.gitignore` hardened — the runtime index/vector store is never committable.** Broadened to `db/ *.db *.npy vectors* *.log .cache/`. A built index embeds memory CONTENT verbatim (which can include secrets jotted in notes), so a stray index created in the repo dir can no longer be swept into a commit.

## v5.12.6 (2026-06-30)

- **The Claude extraction route is now harnessed with a strict extractor system prompt — fixes silent signal loss on conversational sessions.** `run_claude_extraction` (both `claude-batch` and the plain `claude --print` fallback) inherited the full agentic Claude-Code system prompt, so on a session whose transcript _tail_ was conversational (e.g. ended on a question to the user) the model **continued the dialogue** instead of extracting — emitting a chat reply with zero `Decision:/Rule:/Worked:/Failed:/Knowledge:` lines, which `filter_signal_lines` then dropped to `EMPTY`. (The codex route was already immune — `codex exec` is task-framed.) The Claude route now passes `--system-prompt` (replace, not append — so its behaviour is hermetic and independent of the evolving default prompt) framing the model as a line-oriented extractor. Verified end-to-end on a real conversational-tail transcript through the actual hook: bare = 0 signal lines, harnessed = 7 clean signals. Override / translate via `EIDETIC_SIGNAL_CLAUDE_SYSTEM`.

## v5.12.5 (2026-06-29)

- **`session-signals.sh` codex fallback passes `--skip-git-repo-check` — fixes silent signal loss in the codex-only route.** When the Claude route is forced off (`EIDETIC_SIGNAL_SKIP_CLAUDE=1`) or unavailable, extraction falls to `codex exec`. codex ≥0.142 **refuses to run in a non-trusted / non-`git` working directory** (exit 1, empty `out.md`), so a Stop-hook fired from such a directory dropped its signals **silently** — there is no Claude fallback on that path, so nothing was extracted and nothing warned. Both `codex exec` invocations in `run_codex_cli_extraction` (with and without `EIDETIC_SIGNAL_CODEX_CLI_MODEL`) now pass `--skip-git-repo-check`, consistent with the `-s read-only` sandbox they already run under.

## v5.12.4 (2026-06-25)

- **`infer_status` no longer mis-demotes a current card for merely _mentioning_ a lifecycle word.** Status was inferred from the card's **name + description** keywords (`_slug_text` folds in the description), so a finding _about_ a fix (`"…Fixed 2026-06-25"`) was ranked `resolved` (0.75×) and any card with the word "archive" in its title/description was ranked `archived` (0.25×) — a silent search-recall penalty on perfectly current memory. Real archival is set **explicitly** via frontmatter `status:` (`curate archive --apply`) and `superseded_by`, so the keyword fallback was legacy and net-harmful and is **removed**: a card is `current` unless it declares otherwise. (`tests/test_infer_status.py`, 10 cases; the explicit `status:` → `0.25×` chain is now pinned in-tree by `tests/test_curate_demote_e2e.py`.) _Apply to an existing index with a full reindex (`index.sh --full`)._
- **Signal extraction: stronger model + a far larger transcript window stop confabulation.** The codex extractor moved off a tiny model on low reasoning + an 8 KB excerpt — in a tool-heavy session a single tool-result JSONL line ate the whole window and starved the extractor into grafting plausible-but-false details onto real events. It now uses a stronger model at medium reasoning over a 2 MB tail (last 20 turns, `[:1500]` each), so signals stay grounded.
- **`session-signals.sh` can emit this run's signals to a caller via `EIDETIC_SIGNAL_OUT`.** A handoff/report can capture exactly the signals just extracted instead of tailing the shared `signals/<day>.md` (which mixes parallel sessions) — kills cross-session lesson mis-attribution in the handoff report.
- **`embed.py` stamps & gates the fastembed version** (`fastembed_version` in `vectors.db` meta). A fastembed upgrade can silently change the e5 pooling (CLS↔MEAN) → different geometry under the same model/dim/hash_scheme, which the old guard could not see. The doctor + `_vector_meta_ok` now warn on a stamp/live mismatch and point at `index.sh --full`. `install.sh`/`doctor.sh` pin `fastembed==0.8.0`.

## v5.12.3 (2026-06-22)

- **Cross-lingual topic-base search — a query now reaches a base in ANY language, not just English.** Query translation was hardwired to translate _into_ English (right for personal memory, whose artifacts are English) — so an English query against a Russian book base never reached the Russian pages; it only matched whatever English helper pages happened to exist. A base now records its dominant language and search translates a foreign-language query **into the corpus language**: an English "variable reward — tribe / hunt / self" now surfaces the exact Russian subsection, while a Russian query still matches natively.
  - `translate.should_translate(query, target)` generalised to any target script (Cyrillic / CJK / Hangul / Arabic), not only `→ en`.
  - `search_impl` resolves the corpus language **explicitly** (env `EIDETIC_TRANSLATE_LANG` > a `.translate_lang` file at the base root) and targets it — **no per-query corpus auto-detect**, so the mixed-but-mostly-English personal corpus can never be mis-targeted. A personal index has no `.translate_lang` → resolves to `en` → search is byte-identical to before this change (regression-tested: personal doctor unchanged, full suite green).
  - `eidetic base init --lang <code>` sets it explicitly; otherwise `eidetic base index` **auto-detects** the dominant language (dominance-thresholded — a little Cyrillic in an English base stays English) and stamps `.translate_lang`.
- **`doctor` is topic-base aware — no more false `❌ broken` on a base.** Pointed at a base (`EIDETIC_MEMORY_SYSTEM=<base>`) the doctor ran PUSH-only checks a PULL base has none of (Obsidian wiki export, compound/op-log deploy, session hooks, the `~/.claude/projects` file count, session-end signal extraction), flipping the verdict to broken (1 FAIL + 3 WARN) on a perfectly healthy base. Those sections are now marked **N/A** in base mode while the index / vectors / canary / translation / search / usage checks that DO apply still run; a healthy base reads `✅ healthy`.
- _Also included since v5.12.2 (prior untagged commits):_ topic-base **isolation hardening** — `_collect_base_files` enforces realpath containment so a base's `corpus_dirs` can't escape its root via `..`/symlink; **malformed-manifest hardening** — base-name validation (no shell-inject / protocol-invalid MCP tool names), a doctor empty-index check, and atomic registry writes.

## v5.12.2 (2026-06-22)

- **`/eidetic-base` skill + discoverable topic bases.** The topic-base workflow now ships as a bundled Claude Code skill (`skill/eidetic-base/SKILL.md`, installed to `~/.claude/skills/eidetic-base/` by `install.sh`): it triggers on "build a topic base / собери базу из &lt;source&gt;" and runs the contract end to end (init → ingest → index → verify → attach), with the isolation / explicit-write / bases-root invariants inline so an agent gets the procedure without being hand-pointed at the docs. README gains a **Topic bases** section linking [`docs/topic-bases.md`](docs/topic-bases.md) — the feature is now findable instead of buried.

## v5.12.1 (2026-06-22)

- **Topic bases — bases-root default + agent contract.** `eidetic base init` now scaffolds under a single bases-root (`$EIDETIC_BASES_DIR`, default `~/eidetic-bases/`) — **never the current working directory** — so a base can't land loose inside whatever project you happen to be in. [`docs/topic-bases.md`](docs/topic-bases.md) is now a complete operating contract rather than a "planned recipe": the real `bin/base.py` CLI (`init`/`index`/`add`/`attach`/`list`/`doctor`/`refresh`), the storage convention, an executable **Agent contract** for building a base end to end, and a _no-divergence_ map of engine (code) vs base data (`~/eidetic-bases/`) vs registry (`~/.claude/eidetic-bases.json`). Corrected the per-base tool list to the real surface (`<name>_search` / `_search_detail` / `_serendipity` / `_add`).
- **Value-measurement telemetry (Phase 0–1).** Passive memory-injection cost log + de-polluted recall benchmarks (Phase 0); a lexical `referenced_k` benefit proxy (Phase 1).

## v5.12.0 (2026-06-22)

- **Topic bases — attachable PULL knowledge-bases, separate from your PUSH memory.** Build a base from any corpus (scraped API docs, a methodology, a book), keep it as its **own git repo**, and **attach it per-project over MCP** — so an agent queries it on demand instead of re-deriving from scratch every time, without polluting your personal memory recall. Personal memory stays PUSH (auto-injected every session); a base is PULL (attached only where needed). Same engine (e5-large + FTS5 + vectors), separate isolated index. Full guide: [`docs/topic-bases.md`](docs/topic-bases.md).
  - **Isolation (the core change)** — `index_impl` is now manifest-aware: when `EIDETIC_MEMORY_SYSTEM` points at a base (a dir with `.eidetic-base.json`), the indexer scans ONLY the base's `corpus_dirs` (recursively), **never** `~/.claude`. A personal index (no manifest) is byte-identical to before (regression-tested) — so a base can never leak into your session auto-injection, and your work-memory never leaks into a base.
  - **`bin/base.py` CLI** — `init` (scaffold a base repo: `docs/` + `notes/` + manifest + gitignored `db/`) · `index` · `add` (curate-write one md, auto-routed note-vs-doc by size) · `attach` (prints the `claude mcp add … -e EIDETIC_MEMORY_SYSTEM=<base> …` line) · `list` · `doctor` (the functional canary against the base) · `refresh`.
  - **Per-base MCP tools** — `mcp_server.py` exposes a base as `<name>_search` / `<name>_search_detail` / `<name>_serendipity` / `<name>_add` (prefixed from the manifest `name`), so several bases attach to one project with no collision. The personal server (generic `memory_*`) is unchanged.
  - **Curate-write is human-gated** — `<name>_add` (and the CLI) write into `notes/` (frontmatter `source: user`) only on an explicit user instruction; nothing auto-compounds into a base (that stays in core memory). Ingested docs stay raw pages, chunked by section — no lossy auto-distillation (that is the extraction-pipeline's job, kept out).
  - **Reuse, not reinvent** — the base reuses the same engine, frontmatter schema, search, and MCP server (all already keyed off `EIDETIC_MEMORY_SYSTEM`); the only new isolation gap closed is the indexer scan-scope. +11 tests (157 total).

## v5.11.1 (2026-06-22)

Three correctness fixes from an adversarial audit of v5.6.0–v5.11.0 (the releases that shipped without a review pass). Each is a case where a green check or a documented usage didn't match the code.

- **`EIDETIC_SIGNAL_CLAUDE_MODEL=haiku` now maps to the pinned id instead of leaking the bare alias.** The README documents that override with a friendly name (`=haiku`), but the env path returned the value verbatim — so the live Stop hook exported `ANTHROPIC_MODEL=haiku`, the exact bare-alias leak this module exists to prevent (a `sonnet→Opus` remap could then re-route the background card-extraction onto a flagship and drain the shared quota pool). The env override now normalizes through the same `NAMES` map as the `.signal_model` file (`haiku → claude-haiku-4-5-20251001`); a full `claude-…` id still passes through verbatim; an unrecognized value falls through to the file/default instead of being exported raw. +5 tests.
- **The translator canary (§3.6) now actually asserts the output is English, as the doctor claims.** The doctor labeled an OK as "changed, **non-Cyrillic English**" and the v5.9.0 note said "Cyrillic-free", but the code only checked non-empty + changed — so a backend that returned a same-script paraphrase (Apple/opusmt echoing Russian) passed as "functional". The canary now **fails when a non-Latin probe's output is still in the source script**; a Latin source (de/fr/…) can't be decided by script, so it isn't gated. The green label now matches what the code verifies. +3 tests.
- **Doctor §3.5 freshness now covers all indexed source roots, not just `projects/*/memory`.** v5.8.1 killed a false "behind" by scoping the disk count to `projects/*/memory[/signals]` — but that left §3.5 **blind to lag in `agent-memory/`, the memory-system `signals/`, and `skills/*/SKILL.md`** (177 indexed paths it never compared, so a stalled hook in those roots would read Δ0 forever). §3.5 now calls the indexer's own `collect_files()` (zero scope-drift — the same code the indexer walks) and set-compares against the FTS paths: the line now reads `983 / 983 (Δ0)` spanning projects + agent-memory + skills + signals.

## v5.11.0 (2026-06-21)

- **The Apple pack check + label are language-adaptive too — finishing the "your language, not Russian" generalization.** v5.10.0 made the _functional_ translator probe (§3.6) adapt to the corpus language; now the _availability_ side does too. `backend_status(source=…)` probes the resolved corpus/configured language's pack (`source→en`) instead of a hardcoded `ru→en`, and the doctor labels the line with that language (e.g. `Apple translation pack de→en: installed ✓` for a German user, with `…→ add the 'de' language` guidance). The `apple_translate.swift` helper already accepted `--from <lang>` — only the Python default and the doctor label were hardcoded. +2 tests (138 total).

## v5.10.0 (2026-06-21)

- **Translator canary is language-adaptive — checks YOUR language, not hardcoded Russian.** Translation is off by default (not everyone needs it), but those who enable it write memories in their own language. The §3.6 functional check now **auto-detects the corpus's dominant non-Latin script** (Cyrillic→ru, Han→zh, Kana→ja, Hangul→ko) and probes in _that_ language; Latin-script corpora (de/fr/es/it/pt) opt in via `EIDETIC_TRANSLATE_LANG` (or a `.translate_lang` file). When the language can't be determined it **skips the functional probe** rather than wrongly assuming Russian (which would false-fail a non-RU user who lacks the RU pack). +2 tests (136 total).

## v5.9.0 (2026-06-21)

- **Doctor now FUNCTIONALLY tests the translator (§3.6), not just its availability.** The doctor showed the translation backend resolved and the Apple `ru→en` pack was installed — but "installed" is not "works". When translation is enabled, the doctor now **translates a fixed Russian sentence** through the resolved backend and asserts the result is non-empty, **changed**, and Cyrillic-free — so a backend that's present but silently returns nothing fails loud (parallel to the v5.7.0 embed canary). Skips cleanly when translation is off (the default). +6 tests (134 total).
  - The probe is a full sentence (`"Память дрейфует со временем" → "Memory drifts with time"`): the Apple backend auto-detects the source language, and a short phrase like `"привет мир"` mis-detects as Kazakh — a full sentence detects as Russian reliably.
- **README**: explicit "Background / idle = **none** (event-driven, no daemon, 0 CPU between sessions)" line in the Performance table.

## v5.8.1 (2026-06-21)

- **Doctor §3.5 index-freshness fix — was a permanent false "behind".** The v5.7.0 freshness NOTE compared a naive recursive `find */memory/*.md` (which counts `MEMORY.md`, `BACKLOG.md`, and `memory/handoff-*/` sub-dir files the indexer NEVER indexes) against the indexed paths — so it always reported a large bogus delta (e.g. "Δ281 behind") even on a perfectly fresh index. Now the disk side matches the indexer's actual scope (`index_impl` SCAN_DIRS: `memory/*.md` + `memory/signals/*.md`, non-recursive, excluding `MEMORY.md`/`BACKLOG.md`), so a fresh index reads Δ0 and only a real incremental-hook lag shows.
- **`signal_model.py` friendly-name match is now case-insensitive** — a hand-edited `.signal_model` of `Sonnet`/`Haiku` resolves correctly instead of silently falling back to the default.

## v5.8.0 (2026-06-21)

- **Install UX — choose the three models that define the system, one command or an agent.** Install was already one command but applied silent env-only defaults; the embedder and translation were invisible, and the card-extraction model was not an install choice at all.
  - **Interactive choose-at-install** — on a TTY, `install.sh` now prompts (enter = default) for the **embedder** (`multilingual` / `english`), **cross-lingual translation** (`off` / `auto` / `apple` / `opusmt` / `cli`), and the **card-extraction model** (`sonnet` / `haiku`). Piped, CI, and agent installs stay **non-interactive** — each option falls back to its env var (`EIDETIC_EMBED_PROFILE`, `EIDETIC_QUERY_TRANSLATE`, `EIDETIC_SIGNAL_MODEL`) or the default, and nothing ever blocks (`EIDETIC_NONINTERACTIVE=1` forces this).
  - **`.signal_model` config (new)** — the card-extraction model is now persisted and resolved by `bin/signal_model.py` (one source of truth for the Stop hook **and** the doctor): `EIDETIC_SIGNAL_CLAUDE_MODEL` (explicit id) > `.signal_model` (`sonnet` | `haiku`) > the pinned sonnet default. An exact id is always pinned, never the bare alias.
  - **Apple `ru→en` pack guidance** — when `apple`/`auto` is chosen on macOS and the pack is absent, install prints the one GUI-only step (System Settings → Translation Languages) and continues (apple fails open).
  - **`AGENTS.md`** — hand a coding agent the repo link: it asks the three choices, runs the install non-interactively, guides the GUI pack step, and verifies with `doctor.sh` (whose functional canary proves the chosen embedder actually embeds).
- +8 tests (127 total).

## v5.7.0 (2026-06-21)

- **Doctor FUNCTIONAL self-checks — catch a silently-broken embedder, not just a missing file.** The doctor's checks were structural (counts, file-existence, vector ALIGNMENT) — all of which pass even when the embedder produces meaningless vectors (wrong model, a fastembed pooling change between index-time and now, an evicted weight cache). New `bin/canary.py` EXERCISES the chain:
  - **§3.1 embed→vector→search canary** — embeds a real indexed card's own name through the live model, vector-searches, and asserts that card self-retrieves at **rank ≤3**. A model/pooling drift puts the query vector in a different space than the stored passages, so the card stops self-retrieving → **fails loud** (the exact class the structural alignment check passes). Fail-soft: no fastembed / no vectors → skipped, not failed.
  - **§3.2 search-tracking verification** — runs a real confident search and confirms the v5.6.0 usage logger actually **fired**, writing to a **temp log (never the real `usage.log`)** so a health check can't poison the dead-card telemetry it verifies. Reports live / silent-broken / off / not-deployed.
  - **§3.3 explicit Apple `ru→en` pack line** — "installed ✓" / "NOT installed — System Settings → Translation Languages", replacing the implicit `apple=Y/n`.
  - **§3.5 index-vs-disk freshness note** — surfaces how many memory `.md` on disk are in the FTS index (informational; the guard-accurate vector-alignment check owns the loud verdict).
  - `EIDETIC_USAGE_LOG_PATH` overrides the usage-log destination (used by the canary + tests).
- +13 tests (119 total). All checks fail-soft; the doctor never crashes on a missing dependency.

## v5.6.0 (2026-06-21)

- **Usage telemetry — which memory cards actually get surfaced.** Eidetic tracked what it _learns_ (op-log, signals) but not what it _uses_. Now every search that surfaces a card in a medium+ result records one append-only line (`bin/usage.py` → `usage.log`), and `bin/usage_stats.py` aggregates it: **top cards** by surfacings, **dead cards** (indexed but never surfaced → prune candidates), coverage %, and per-card last-seen + best/avg rank.
  - **Append-only + fail-open + privacy-safe.** Atomic `O_APPEND` lines never corrupt under parallel sessions (no shared-SQLite write contention); any logging error is swallowed so telemetry can never break search; the raw query is **never** written — only a short hash — so the log is safe in a public tool.
  - **Doctor "Usage" section** — surfacings / distinct / coverage % / dead count at a glance.
  - `usage_stats.py --rollup` compacts `usage.log` into `usage_rollup.json` (atomic move-aside — no lost appends) so the log never grows unbounded; `--json` for tooling; `--top N` report. Opt out with `EIDETIC_USAGE_LOG=off`.
- +10 tests (106 total).

## v5.5.0 (2026-06-20)

- **Cross-lingual query translation (opt-in).** A non-English query is now translated to English and dual-queried — the native and translated searches run in parallel and fuse by best rank, so translation only _adds_ recall and never regresses (measured **5/8 → 7/8 recall@3** on the operator battery; the shipped runtime path carries confidence at 8/8 medium+). Three pluggable backends behind `bin/translate.py`, all **FAIL-OPEN** (any failure ⇒ the native result, never an error):
  - `apple` — Apple Translation NMT (macOS 26+, on-device, Neural Engine) via a self-contained Swift helper (`bin/apple_translate.swift`) using the headless `TranslationSession(installedSource:target:)` API — no SwiftUI, no app window. Install the language pair once via System Settings → Translation Languages.
  - `opusmt` — Helsinki Opus-MT via CTranslate2 (portable, offline, Linux + macOS). Lazy `pip: ctranslate2 sentencepiece huggingface_hub` + a ~75 MB INT8 model pinned by revision.
  - `cli` — codex CLI zero-install fallback.
  - `auto` prefers apple (macOS + pair installed) → opusmt → cli. **Default `off`** (`EIDETIC_QUERY_TRANSLATE` env / `.translate_backend` file) — with no opt-in, search is byte-identical to v5.4.0.
- **Async dual-query, fail-open by construction.** The native search is the anchor: a slow / killed / unavailable translator (verified at a 1 ms forced timeout) still returns the native result. `EIDETIC_TRANSLATE_TIMEOUT` bounds the translator (default 8 s).
- **Doctor shows the active translator** — the "Models — who does what" section reports the configured backend, which concrete backend resolves, and per-backend availability (`apple` / `opusmt` / `cli`).
- `bin/recall_lab.py --translate <backend>` measures the _real_ runtime translator (adds `xlate_` / `dual_` / `runtime_<backend>` strategies) against the hand-written ceiling — both Apple and Opus-MT hit 7/8. +19 tests (93 total).

## v5.4.0 (2026-06-20)

- **The doctor tells the truth about vectors.** The vector-health check used a gross `(chunks - vectors)/chunks` lag that counted dead orphan-vectors as coverage and could read negative — it reported a 99.94% chunk_id-misalignment outage as "healthy, lag -319%" for weeks. New `bin/coverage_audit.py` classifies every chunk against the real search-time guard (join by chunk_id → path/heading → recomputed content_hash); `doctor.sh` and the session-start hook now read its guard-accurate ALIGNED metric and **fail loudly** when vectors exist but are chunk_id-misaligned. Regression-proven on the pre-rebuild backup (0% aligned → doctor exits 2). +3 fixtures.
- **Model-by-language.** The embedder is now a config-driven profile (`EIDETIC_EMBED_PROFILE` env / `.embed_profile` file): `multilingual` (multilingual-e5-large, 1024d — the default, unchanged) or `english` (BAAI/bge-small-en-v1.5, 384d, ~130MB cache vs 2.2GB, ~5× faster embed). A/B on the live corpus: bge-small-en matched e5-large at English recall@3 7/8. Switching profiles trips the existing model/dim stamp guard → a clean `index.sh --full` rebuild, no silent corruption. +6 tests.
- **Doctor shows model routing** — a "Models — who does what" section: which model embeds (the active profile), which writes session-end cards (Sonnet by default), and that cross-lingual query translation is not wired (native-language search; an automatic translate step via a small model is planned).
- Added `bin/recall_lab.py` — an operator harness measuring cross-lingual recall@k and comparing query strategies (native / translated / dual-query fusion). Finding on the restored index: a translated (English) query recalls 7/8 @3 vs 5/8 for the native query; automatic translation is future work.
- Russian README (`README.ru.md`) + an EN/RU language switcher (the contributor docs and core remain English).
- The W5 failed-embed log is now cleared on a clean embed, so one transient error no longer pins the doctor "degraded" until the next manual reindex.

## v5.3.1 (2026-06-18)

- Added the `imported` source-weight tier (`0.3`, below `agent-extracted`) to `SOURCE_WEIGHTS` + the golden ranking test — third-party content filed by the importer (Karpathy's "Ingest") is low-trust by construction and never outranks session-validated knowledge or user feedback.
- Added `EIDETIC_SIGNAL_SKIP_CLAUDE=1` to the session-signals hook — forces the codex-only extraction route so a session-end trigger fired while an interactive Claude session is live cannot share the Anthropic quota pool and kick the extension.
- Portability: `recall_smoke.py` ships generic, install-agnostic recall fixtures (keep your own corpus cases locally), and `export_vault.py` derives its slug path-boilerplate from the runtime home dir instead of a hard-coded value. Trimmed the bundled example vault (a curated synthetic showcase will return).

## v5.3.0 (2026-06-18)

Karpathy [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) completion — answers compound back into the wiki, and the system became loud instead of silently rotting.

- Added `bin/remember.py` — **promote** a synthesized answer into one typed page, with project-scoped search-before-write dedup (a re-promote appends a dated `## Update` instead of duplicating; new pages get `## Related` links). The same write-path a future importer reuses. Never appends/clobbers a protected feedback/user card.
- Added `bin/oplog.py` — a greppable append-only op-log (`log.md`); `compound.py` (the Stop-hook capture) and `remember.py` both record onto it. Unbuffered `os.write` + fsync under flock so the lock actually covers the append.
- Extended `card_kind` with `synthesis` (inferred) + `concept`/`entity` (explicit-only knowledge pages), each with its own age-drift threshold.
- Added `docs/MEMORY-SCHEMA.md` — the explicit maintenance contract (frontmatter, kinds, weights, lifecycle, the four write paths).
- **Loud self-heal (W5):** the session-start embed no longer swallows errors to `/dev/null` (the cause of a 16-day silent vector outage) — a crash lands in `embed-last.log` and surfaces a one-line warning; high vector lag is flagged.
- Deduplicated the `EVIDENCE_WEIGHTS`/`SOURCE_WEIGHTS` tables onto `constants.py` (search + inject now import, not copy); broken-wikilink drift ignores prose-in-brackets (whitespace is never a memory slug).
- `doctor.sh` now covers the op-log, promote/compound deployment state, card-kind distribution, and the W5 failed-embed log.
- Pre-publish review (consilium) verified against live code; fixes: non-ASCII (e.g. Cyrillic) titles get a hash-slug instead of all collapsing to one page; `_atomic_write` preserves an existing card's permissions; `--update` validates its target is a memory card.
- Session-end signal extraction now defaults to **Sonnet** (was Haiku) for higher auto-capture quality, and degrades across whatever CLI is present — `claude-batch → claude --print → codex-batch → codex exec` — so any install with the `claude` _or_ `codex` CLI extracts even without the private batch wrappers (the kickout-safe wrappers stay preferred where present). Override with `EIDETIC_SIGNAL_CLAUDE_MODEL` (e.g. `=haiku` to economize) / `EIDETIC_SIGNAL_CODEX_CLI_MODEL`. Signals remain `source: agent-extracted` (0.5× weight), so the model upgrade lifts capture quality without changing the low-trust ranking.
- `doctor.sh` now flags a missing cross-encoder reranker model (jina): it can vanish independently of e5 (an emptied onnx cache dir) and silently disable rerank salvage / degrade cross-lingual recall while everything else looks healthy. Found by the recall smoke during testing and fixed.

## v5.2.0 (2026-06-17)

- Pinned the fastembed model cache to a persistent dir (`FASTEMBED_CACHE_PATH`, default `~/.cache/fastembed`) instead of TMPDIR, which the OS purges — silently evicting the ~2 GB e5 weights and degrading all vector search to FTS until a manual reindex.
- Added a non-blocking file lock to both `embed.py` and `export_vault.py` so the session-start hook and a manual/cron reindex (or two concurrent vault exports) no longer race into `database is locked` / interleaved writes.
- Made the deterministic vault-export markdown passes fenced-code aware — `[[links]]` and `Field:` lines inside ` ``` ` code examples are no longer rewritten or stripped.
- Hardened vault export: a `.eidetic` ownership sentinel is written first so an interrupted run no longer bricks the next export, `.DS_Store`/`.obsidian` are ignored in the ownership guard, and notes dropped as too-large are listed instead of silently counted.
- Added `bin/doctor.sh` — an end-to-end self-check (deps, index, memory files, vectors + lag, model-cache location, hooks, wiki/vault) that reports which tiers are active and _why_ the wiki isn't being created.
- Pinned ranking weights with a golden-oracle test guarding the constants/search/inject tables and the distinct `export_vault` curation scale.

## v5.1.0 (2026-05-31)

- Replaced the `paraphrase-multilingual-MiniLM-L12-v2` (384-dim) embedder with `intfloat/multilingual-e5-large` (1024-dim) via fastembed ONNX — RU-paraphrase recall@3 25% → 67%, recall@10 42% → 92% (measured on a fixed pool). Still zero-torch.
- Added the e5 `query:`/`passage:` prefixes (fastembed does not add them) and recalibrated vector confidence thresholds for the e5 score distribution.
- Added a two-signal confidence gate: a vector-only hit in the ambiguous mid-cosine band reaches `medium` only with lexical corroboration (≥2 query content-tokens in the candidate), so true cross-lingual matches stay confident while topical noise is suppressed — even at ~0.83 cosine.
- Added a `vectors.db` model/dim meta-stamp plus a search-time guard that warns and degrades to FTS on embedder drift instead of silently returning empty results.
- Aligned the vector-confidence unit tests with the new calibration and added a cross-lingual recall regression guard to the smoke suite.

## v5.0.1 (2026-05-26)

- Added metadata-only `PostToolUse` Bash lifecycle events with `command_class`, `background`, and bucketed timeout fields only
- Added metadata-only `PostToolUseFailure` events for `Bash`, `Write`, `Edit`, and `MultiEdit` with failure enums and no raw command, error, path, description, stdout/stderr, or tool-response persistence
- Extended lifecycle hook registration to manage dedicated `PostToolUse` file/Bash entries plus a `PostToolUseFailure` entry while preserving unrelated hooks
- Added sensitive-cwd exclusion, missing-cwd fallback behavior, redacted Bash/failure fixtures, classifier anchoring tests, and malformed failure-payload coverage

## v5.0.0 (2026-05-26)

- Added stable per-result `detail_id` selectors to CLI JSON, `--json-object`, and MCP `memory_search` payloads without removing existing fields
- Added `search.sh --detail <detail_id|path>` and MCP `memory_search_detail` for exact full-content retrieval after a compact search result looks relevant
- Added `--brief` / `--full`; broad CLI queries now default to compact rows while `--full` preserves the previous snippet-rich output
- Preserved the hard `no_confident_results=true` contract for structured search responses and added regression coverage for progressive search/detail behavior

## v4.3.0 (2026-05-26)

- Added Eidetic-owned `PostToolUse` lifecycle capture for `Write`, `Edit`, and `MultiEdit` with metadata-only JSONL events
- Lifecycle events store HMAC path/cwd hashes, extension, operation, edit count, and duration only; raw paths, filenames, content, diffs, stdout/stderr, tool results, and transcript text are never persisted
- Added safe hook registration with a dedicated `PostToolUse` matcher and `timeout: 2` seconds
- Added vault/projection-root exclusion, symlink-sensitive path checks, atomic HMAC key creation, bounded append-only JSONL writes, lifecycle retention cleanup, and lifecycle unit/smoke coverage

## v4.2.21 (2026-05-25)

- Stop-hook tail-window parsing now checks whether the capped read starts on a JSONL line boundary before discarding the first line, preserving safe complete user/assistant turns at the exact boundary
- Added regression coverage for a complete 8000-byte JSONL user line that starts exactly at the tail-window boundary

## v4.2.20 (2026-05-25)

- Stop-hook transcript extraction now parses complete JSONL lines from the capped tail window and skips extraction when no safe text turns are available, instead of falling back to raw transcript tail
- Added regression coverage for an oversized final `tool_result` line so command output cannot re-enter the signal extractor through the fallback path

## v4.2.19 (2026-05-25)

- `update.sh` now uses temp-file plus atomic rename for hook settings rewrites, custom-root skill rewrites, and `.installed.json` metadata updates
- Stop-hook transcript parsing now ignores `tool_result` and other non-text content blocks instead of feeding command output into the user/assistant signal excerpt
- Added CI coverage for tool-result exclusion in Stop-hook parsing and for the MCP full-reindex 300s timeout path

## v4.2.18 (2026-05-25)

- Stop-hook signal extraction now reads real Claude Code JSONL transcripts with nested `message.role` and `message.content`, while retaining compatibility with older top-level fixtures
- Stop-hook CI fixtures now use the real nested transcript schema, including assistant text blocks, so automatic session learning is covered by tests
- Fresh install now uses temp-file plus atomic rename for runtime scripts, hooks, skill, settings, and install metadata
- `update.sh` removes temp files if atomic replacement fails, and MCP full reindex gets a longer timeout than incremental reindex
- Added regression coverage proving explicit frontmatter `status: fixed` reaches drift handling as `fixed`, not only as a mocked database row

## v4.2.17 (2026-05-25)

- `status: fixed` cards are now treated as inactive for age drift, matching existing search/context status weighting
- Feedback and user memories no longer infer inactive lifecycle status from words like "fixed", "closed", or "deprecated" in titles/descriptions unless frontmatter explicitly sets a status
- Code-index refresh now escapes SQL `LIKE` wildcards when deleting rows for a project path, preventing underscore/percent path prefixes from matching sibling projects
- `update.sh` installs runtime files via temp-file plus atomic rename to avoid readers seeing partially overwritten scripts
- Added CI regressions for fixed-status age drift, feedback/user status migration, and code-index wildcard path deletion

## v4.2.16 (2026-05-25)

- `age_stale` drift detection now honors lifecycle status: archived, deprecated, obsolete, resolved, and superseded cards do not count as active stale findings
- Freshness thresholds now use `card_kind` before broad memory `type`, so historical findings/research/reference/handoffs use a 90-day window while active bug/todo/status cards use a 60-day backlog window
- Code-index chunks no longer generate `age_stale`; unchanged source code is handled by reindex/vector identity checks, not calendar age
- Added CI coverage for lifecycle/card-kind age drift behavior, preventing the old `type: project` 30-day threshold from turning historical project memories into review noise

## v4.2.15 (2026-05-25)

- Stop-hook signal extraction now accepts only `Decision:`, `Rule:`, `Worked:`, `Failed:`, and `Knowledge:` lines before compounding, and falls back to Codex when Claude returns no valid signal lines
- `compound.py` applies the same prefix filter as defense-in-depth for direct/manual stdin usage
- SessionStart degraded fallback now injects only the CWD-matching project `MEMORY.md`, instead of the first project memory file on disk
- Added `EIDETIC_SIGNAL_CLAUDE_TIMEOUT` and install/update registrations now give the async Stop hook a 180s budget, aligned with the 30s Claude extraction timeout plus 120s Codex fallback timeout

## v4.2.14 (2026-05-25)

- Stop-hook signal extraction now tries `claude-batch`/Haiku first, then falls back to `codex-batch` with `gpt-5.4-mini` when Claude is unavailable, fails, or returns `EMPTY`
- Added environment overrides for signal extraction routes: `EIDETIC_SIGNAL_CLAUDE_MODEL`, `EIDETIC_SIGNAL_CODEX_MODEL`, `EIDETIC_SIGNAL_CODEX_REASONING`, and `EIDETIC_SIGNAL_CODEX_TIMEOUT`
- Added CI coverage for the fallback path so a fake failing `claude-batch` still compounds the Codex-extracted signal

## v4.2.13 (2026-05-25)

- Markdown section splitting is now fence-aware, so `##` headings inside fenced examples cannot become standalone chunks that trigger false `confidence_escalation` drift
- SessionStart code/vector refresh now passes paths through Python `argv`, keeping custom memory roots and CWDs with apostrophes safe
- Added CI regressions for fenced `## History` examples and SessionStart hook refresh under a quoted custom root

## v4.2.12 (2026-05-25)

- Index and lint now include fallback Stop-hook signals under the active `EIDETIC_MEMORY_SYSTEM` root
- Cleanup no longer mixes default-root signal files into custom-root archive operations
- Cleanup skill-link protection resolves frontmatter `name:` aliases, matching lint behavior
- MCP `memory_lint` passes the active index path instead of defaulting to `~/.claude/memory-system`
- `update.sh` refreshes hook registrations with the custom-root `EIDETIC_MEMORY_SYSTEM` prefix
- Install/update/check-update metadata reads now pass paths through argv instead of interpolating shell paths into Python snippets
- `confidence_escalation` ignores dated bullets inside fenced/inline code examples

## v4.2.11 (2026-05-25)

- `confidence_escalation` drift now counts agent-extracted history events instead of markdown chunks, so multi-section files no longer look like multiple independent confirmations
- `EIDETIC_MEMORY_SYSTEM` is honored by wrappers, MCP reindex/serendipity, hooks, install, update, and update-check paths
- SessionStart handoff discovery now scans `output/handoff-*/state.md` in the current repo
- Cleanup protects large-frontmatter `feedback`/`user` files and counts inbound wikilinks from skill `SKILL.md` files
- Compounding appends new entries inside the existing `## History` section instead of after later sections
- Vault polish/synthesis model IDs are centralized behind environment overrides, and polish prompt templating preserves literal `{...}` note text
- Added CI regressions for all fixes above

## v4.2.10 (2026-05-25)

- Fixed SessionStart recent-memory filtering after v4.2.9 nanosecond mtimes; old memories no longer pass a seconds cutoff as "recent"
- Search and context freshness scoring now handle timezone-aware and `Z` `last_verified` values consistently with drift checks
- `export-vault.sh --no-open` is wrapper-only again and no longer reaches `export_vault.py` argparse
- Stop-hook compounding no longer depends on impossible FTS5 rank magnitudes; exact FTS matches can update existing memory history
- Code indexing transaction replacement now uses the sqlite connection context manager instead of manual `BEGIN`
- Added CI regressions for recent mtime normalization, timezone freshness, no-open export, compound matching, and successful code-index replacement

## v4.2.9 (2026-05-25)

- Incremental indexing now uses nanosecond mtimes and deletes stale chunks when a memory file is emptied to frontmatter-only
- Code indexing now builds rows before replacing old code-index chunks, preserving previous code recall on parse failures
- Vector fallback validates path/section/content identity before per-path deduplication
- Hooks now share an `fcntl` lock file through `bin/lock_runner.py`, replacing stale-lock cleanup races
- Drift age checks now handle timezone-aware `last_verified` values
- Cleanup archive destinations are collision-safe for duplicate basenames processed in the same second
- `embed.py --search` now handles vector identity tuples, and `export-vault.sh` preserves option values before target inference
- `bin/update.sh` now reports derived refresh failures as degraded instead of printing a false-green refresh message

## v4.2.8 (2026-05-24)

- Fixed code index file discovery so every supported file in a directory is indexed, not only the last filename visited by `os.walk`
- `bin/update.sh` now refreshes code-aware recall for the whole installed runtime, including `mcp_server.py`, before refreshing vectors and `memory-context.md`
- CI now covers multi-file code discovery to prevent silent code-aware recall regressions

## v4.2.7 (2026-05-24)

- Vector rows now include stable path/section/content-hash identity, and semantic search skips stale vector rows whose chunk IDs no longer match current index content
- Incremental lifecycle backfill now still removes deleted files from old migrated indexes
- SessionStart and Stop hooks now use an atomic lock directory instead of check-then-write PID files
- Stop hook resolves `claude-batch` through `CLAUDE_BATCH`, `PATH`, or the maintainer fallback path instead of hardcoding one local install path only
- MCP tool failures now return `isError: true` consistently; `export_vault` forwards `--synthesize` when requested
- Code indexing no longer parses TypeScript with the JavaScript grammar; `.ts/.tsx` are enabled only when `tree_sitter_typescript` is installed
- Cleanup reports now handle duplicate memory basenames across projects without dropping files
- `bin/update.sh` now refreshes derived FTS/code/vector indexes after runtime updates so code-aware recall and new vector identity metadata are populated immediately
- CI now covers vector identity, old-DB deleted-row cleanup, MCP export/error contracts, TypeScript grammar routing, cleanup basename collisions, and FTS5 special-character command success

## v4.2.6 (2026-05-24)

- Incremental indexing now detects old rows with empty lifecycle metadata and reindexes existing memory files to backfill `card_kind`, `status`, and related derived fields
- CI now includes an old-DB reproducer where unchanged `index_meta` rows previously skipped semantic backfill
- Wikilink lint/drift extraction now ignores fenced Markdown examples, inline code snippets, and obvious placeholders such as `[[filename]]`; drift validates full source files instead of split chunks
- Maintainer corpus broken wikilinks were cleaned from 24 to 0 by converting non-memory references to Markdown links and fixing memory-to-memory targets

## v4.2.5 (2026-05-24)

- MCP `memory_search` now returns parsed `structuredContent` plus JSON text fallback, and marks subprocess failures as `isError`
- Lifecycle and card-kind inference no longer uses storage paths, preventing archive/debug path fragments from downranking active memories
- `recall_smoke.py` now fails hard if `--json-object` returns a list, if positive cases return `no_confident_results=true`, or if negative cases omit the flag
- `age_stale` drift findings now use stable threshold-based identity so repeated detections can reach penalized state
- Feedback rules now remain visible name-by-name even when the context budget is exceeded
- CI now checks lifecycle path false positives, recall-smoke contract assertions, and MCP structured search round-trip
- Duplicate-column races during v2.6 schema safety migrations are now ignored when another process added the column first

## v4.2.4 (2026-05-24)

- Added structured `--json-object` search output with `no_confident_results`, `best_confidence`, and result count metadata
- MCP `memory_search` now uses the structured contract so agents cannot ignore all-low-confidence retrieval by accident
- Added durable retrieval fields: `card_kind`, `status`, `area`, `supersedes`, and `superseded_by`
- Added status-aware ranking so current/active cards outrank resolved, superseded, deprecated, obsolete, or archived cards
- Search results expose drift findings and penalties; CLI prints drift diagnostics on affected rows
- Context assembly includes a bounded `Memory Drift Diagnostics` block for active drift findings
- `health.sh` reports active and penalized drift counts by drift type
- Expanded operator recall smoke from 4 to 21 cases, including code-aware recall and negative no-confident recall
- CI now asserts schema migration and structured no-confident JSON output

## v4.2.3 (2026-05-24)

- Search results now include `confidence`, `confidence_reason`, `retrieval_score`, and `rrf_score` fields
- CLI search suppresses all-low-confidence result sets and reports `No confident results`
- `health.sh` reports stale `memory-context.md` when assembled counts no longer match `index.db`
- Added `bin/recall_smoke.py` for operator-corpus recall regression checks
- CI asserts confidence metadata in JSON search output

## v4.2.2 (2026-05-24)

- Topic synthesis is now opt-in via `--synthesize`; normal CLI exports no longer create `topics/`
- Existing `--no-synthesize` remains accepted as a compatibility no-op
- Documentation marks current topic synthesis as experimental pending deferred Vault IA
- MCP `synthesize=true` remains available for explicit experiments only

## v4.2.1 (2026-05-24)

- Installer stays non-interactive by default; daily vault export is opt-in via `EIDETIC_SETUP_CRON=1`
- MCP `export_vault` now exposes `polish`, `synthesize`, `polish_count`, `polish_model`, `force`, `all`, and `timeout`
- MCP export defaults to no LLM calls; CLI export keeps the enriched v4.2 path
- Version/docs synchronized to v4.2.x and CI now smokes no-LLM vault export

## v4.2.0 (2026-05-23)

- **Topic synthesis** -- clusters exported notes into wiki-style topic pages
- LLM-based clustering replaced hardcoded topic keywords
- Opus synthesis for better topic pages; large clusters use top notes by weight with context caps
- `HOME.md` links synthesized topics when available

## v4.1.0 (2026-05-23)

- **LLM polish** -- rewrites exported note bodies for human-readable Obsidian cards
- Smart model routing: Sonnet for complex notes, Haiku for simple notes
- MCP `export_vault` tool added
- Plug-and-play Obsidian registration/open on macOS
- Human-readable filenames with title preservation and collision handling
- Polish circuit breaker and idempotent re-run guard

## v4.0.0 (2026-05-23)

- **Obsidian vault export** -- `eidetic export-vault ~/my-vault/`
- Quality gate: filters 500+ files down to ~120 validated knowledge notes
- Template formatting: Rule Cards, Status Cards, Quick References, Profile Cards + passthrough fallback
- Wikilink resolution: links verified against export set, dangling stripped, auto-aliases
- Auto-MOC per folder + HOME.md root index
- `.obsidian/` pre-config with graph colors (first export only, never overwrites)
- Delta mode: `.manifest.json` tracks SHA256, `--delta` skips unchanged
- `--project` with fuzzy match, `--all --force` for raw dump
- Reviewed: Murphy (M1-M21), Adversarial, Consilium (6 voices), Consreview (5 voices)

## v2.5.0 (2026-05-22)

- **Drift detection** -- wikilink validation, type-based age thresholds, confidence escalation detection
- Separate `drift_state.db` (P1: index.db stays derived/rebuildable)
- Differential penalty: broken_wikilink=0.8x, age_stale=0.5x, confidence_escalation=0.3x
- Baseline mode: first detection = no penalty, penalty on second consecutive detection
- 24h throttle, auto-resolve when drift disappears, orphan pruning
- Drift-aware ranking in both search and context assembly
- Crash-safe full reindex via temp DB + `os.replace()`
- 13 bugfixes from consilium (5 voices) + consreview (6 voices)
- Constants deduplication (`constants.py`), compound.py project matching fix
- Search recall improved to 18/20 (vector boost + per-path dedup + tiered FTS)

## v2.2.2 (2026-05-22)

- Auto-update system: version tracking, background update check every 6h, one-command update
- `bin/check-update.sh` -- fast version check via `git ls-remote` (~200ms, runs at SessionStart)
- `bin/update.sh` -- fetch latest, replace code files, preserve db/rules/hooks
- Install metadata at `~/.claude/memory-system/.installed.json`

## v2.2.1 (2026-05-22)

- Search recall hardening: phrase -> AND-prefix -> OR-prefix fallback instead of exact long-phrase only
- Vector fallback visibility: import via file path, warning on unavailable/failed vector search, safer merge behavior
- MCP hygiene: clamp invalid/negative limits, support `type_filter=code`, longer search timeout
- Lint fixes: basename collision handling, Bash `[[...]]` false-positive filtering, installed skill link aliases
- Backup/error-path hardening for `embed.py` and `index_impl.py`
- Docs clarify zero-dependency core vs optional v2 packages

## v2.2.0 (2026-05-21)

- Tree-sitter code parsing (.py/.js/.ts/.sh)
- 16 bug fixes (1 BLOCKER, 6 HIGH)
- 3 consreviews (18 voice reviews total)

## v2.0.0 (2026-05-21)

- Hybrid FTS5 + vector search (fastembed ONNX)
- Recall: 30% -> 100% on semantic queries
- Backup/restore for all reindex operations

## v1.3.0 (2026-05-21)

- Smart token compression: 57 -> 124 rules in same budget
- Keyword clustering, tiered display, adaptive budget

## v1.2.0 (2026-05-21)

- MCP server (5 tools, works with Cursor/Windsurf/Cline)
- GitHub repo, SEO/GEO, awesome-list PRs

## v1.0.0 (2026-05-20)

- FTS5 search, context assembly, signal extraction
- Compounding, evidence tiers, serendipity, phase-adaptive
