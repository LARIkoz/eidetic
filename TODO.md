# Eidetic TODO

## Current Track — v2.7 Agent Memory Review Loop

Context: pause Obsidian/human-facing vault work until the agent-facing memory layer is excellent. A clean vault is secondary; the core product is an agent that recalls the right rules, decisions, bugs, and project state, and refuses low-confidence retrieval instead of surfacing random near-matches.

Canonical product governance lives in `~/Documents/cursore/claude-native-kurdyuk-lite/.kurdyuk-lite/runs/ai-memory-system/`. Use `PROJECT_MAP.md` as the local routing table before editing roadmap, charter-sensitive behavior, runtime docs, or installed state.

### Closed In v4.2.1

- [x] Keep `install.sh` non-interactive by default. Daily vault export is opt-in through `EIDETIC_SETUP_CRON=1`.
- [x] Add `.DS_Store` to `.gitignore` and remove generated Finder files from the working tree.
- [x] Synchronize public docs/versioning from v4.0/v2.5 language to v4.2.x.
- [x] Update MCP server version and expose v4 export controls instead of forcing the slow default path.
- [x] Keep MCP export safe by default: no LLM polish/synthesis unless explicitly requested.
- [x] Add CI smoke coverage for no-LLM Obsidian vault export.

### Closed In v4.2.2

- [x] Disable topic synthesis by default for CLI exports.
- [x] Keep `--synthesize` as an explicit experimental flag.
- [x] Keep `--no-synthesize` accepted as a compatibility no-op.
- [x] Mark topic synthesis as experimental in docs until v4.3 Vault IA lands.

### Closed In v4.2.3

- [x] Add confidence-aware search UX: all-low-confidence result sets now report `No confident results` instead of surfacing random-looking matches.
- [x] Add confidence metadata to JSON search results: `confidence`, `confidence_reason`, `retrieval_score`, and `rrf_score`.
- [x] Add stale-context detection to `health.sh`: `memory-context.md` counts are compared against current `index.db`.
- [x] Add operator recall regression set in `bin/recall_smoke.py` for large-prompt bug, Gap Pipeline concept, Obsidian best practices, and a weak negative query.
- [x] Add `PROJECT_MAP.md` and link repo entrypoints to canonical brief/charter, installed runtime, source corpus, and human projection.
- [x] Sync canonical Kurdyuk-run `state.md`, `todo.md`, `gate-log.md`, `brief.md`, and `charter.md` with v4.2.3/v2.6 status.

### Closed In v4.2.4

- [x] Add explicit JSON/MCP no-confident contract via `--json-object` and MCP `memory_search`.
- [x] Add durable retrieval fields: `card_kind`, `status`, `area`, `supersedes`, `superseded_by`.
- [x] Infer card kind/status conservatively from frontmatter, memory type, filename, and path.
- [x] Add status-aware ranking so current cards outrank resolved/superseded/deprecated/archived cards.
- [x] Make drift visible in search results, CLI rows, context assembly, and health output.
- [x] Expand operator recall smoke from 4 to 21 cases on the live corpus.
- [x] Keep Obsidian/Vault IA untouched; v4.x projection remains maintenance/deferred.

### Closed In v4.2.5

- [x] Run v2.7 Stage 2 consreview. Pipeline was DEGRADED (`audit=ISSUES`, `mechanical=FAIL`, `redteam=WEAK`), so `SYNTHESIS.md` is not final; corrected findings are based on raw voices plus validator artifacts.
- [x] Make MCP `memory_search` return parsed `structuredContent` plus JSON text fallback, with `isError` on subprocess/search failures.
- [x] Remove storage paths from lifecycle/card-kind inference and use word-level matching to avoid archive/debug substring false positives.
- [x] Make `recall_smoke.py` fail hard on broken `--json-object` contract and positive cases that return `no_confident_results=true`.
- [x] Stabilize `age_stale` drift identity and migrate old `age=N threshold=M` rows to threshold-based detail.
- [x] Keep feedback rules visible name-by-name even when the context budget is exceeded; no `...and N more feedback rules` hiding.
- [x] Add CI checks for MCP structured search, recall-smoke contract assertions, lifecycle path false positives, age drift migration, and feedback visibility.

### Closed In v4.2.6

- [x] Add old-DB lifecycle backfill: if migrated rows still have empty `card_kind`, incremental indexing reindexes existing memory files instead of skipping by unchanged mtime.
- [x] Add CI reproducer for the old `index_meta` skip bug.
- [x] Reduce wikilink lint/drift false positives by ignoring fenced Markdown examples and placeholder links such as `[[filename]]`.
- [x] Clean maintainer corpus broken wikilinks from 24 to 0 by fixing memory-to-memory targets and converting non-memory file references to Markdown links.

### Closed In v4.2.7

- [x] Run clean v2.x/v2.6 consreview against v4.2.6. Pipeline was DEGRADED (`audit=ISSUES`, `mechanical=FAIL`, `redteam=WEAK`), but raw voices exposed a concrete v2.8 hardening pack.
- [x] Guard vector fallback against stale `vectors.db` rows after full index rebuild by storing and checking path, section, and content hash.
- [x] Keep deleted-file cleanup active during old-DB lifecycle backfill.
- [x] Replace hook check-then-write PID locks with atomic lock directories.
- [x] Normalize MCP tool errors to `isError: true` and forward MCP `export_vault` synthesis requests.
- [x] Make Stop-hook signal extraction portable by resolving `claude-batch` instead of hardcoding one maintainer path.
- [x] Stop parsing TypeScript with the JavaScript grammar; use optional `tree_sitter_typescript` when available.
- [x] Fix cleanup basename collisions across projects.
- [x] Refresh derived FTS/code/vector indexes during update so code-aware recall and vector identity migration do not temporarily suppress recall.
- [x] Add regression tests for the above plus FTS5 special-character command success.

### Closed In v4.2.8

- [x] Fix `code_index.find_code_files()` so it discovers every supported file in a directory instead of only the last filename seen by `os.walk`.
- [x] Refresh code-aware recall for the whole installed runtime during update, including top-level `mcp_server.py`, before vector and `memory-context.md` refresh.
- [x] Add CI regression coverage for multi-file code discovery.

### Closed In v4.2.9

- [x] Run clean consreview against v4.2.8. Pipeline was DEGRADED (`audit=UNKNOWN`, `mechanical=FAIL`, `redteam=WEAK`), so `SYNTHESIS.md` is not final, but raw voices/redteam exposed concrete hardening findings.
- [x] Fix incremental indexing stale chunks when a memory file becomes frontmatter-only; switch index mtimes to nanosecond precision so rapid edits are not skipped.
- [x] Make code-index replacement atomic at the project level: parse/build rows first, then replace old rows in one transaction.
- [x] Validate vector identity before per-path deduplication.
- [x] Replace hook stale-lock cleanup with shared `fcntl` lock runner.
- [x] Fix timezone-aware drift age checks, archive destination collisions, `embed.py --search`, `export-vault.sh --project`, and false-green update refresh reporting.
- [x] Add regression tests for the above.

### Closed In v4.2.10

- [x] Run clean consreview against v4.2.9. Pipeline was DEGRADED (`audit=UNKNOWN`, `mechanical=FAIL`, `redteam=WEAK`), so `SYNTHESIS.md` is not final, but raw voices/redteam exposed concrete follow-up findings.
- [x] Fix nanosecond `mtime` unit mismatch in SessionStart recent-memory filtering.
- [x] Align timezone-aware freshness scoring in search and context assembly with drift checks.
- [x] Keep `export-vault.sh --no-open` wrapper-only so Python argparse does not reject it.
- [x] Fix compounding exact-match gate so FTS5 rank magnitude does not prevent updates forever.
- [x] Harden code-index transaction replacement and add a success-path regression.
- [x] Add regression tests for the above.

### v2.6 Agent Memory Quality Goals

- [x] Add durable schema fields for agent retrieval: `card_kind`, `status`, `area`, `supersedes`, `superseded_by`, `last_verified`.
- [x] Replace overloaded `type: project` semantics with `card_kind`: `decision`, `bug`, `finding`, `handoff`, `todo`, `status`, `reference`, `research`, `profile`, `rule`.
- [x] Add status-aware ranking: active/current notes outrank resolved/superseded/archived notes.
- [x] Add confidence-aware search UX: when top results are weak/vector-only, report "no confident result" instead of returning random-looking matches.
- [x] Add first agent recall regression set for real queries: large-prompt bug, Gap Pipeline concept, Obsidian best practices, and weak negative recall.
- [x] Add stale-context detection: `health.sh` should report when `memory-context.md` was assembled from an older index.
- [x] Tighten drift handling for agent recall: broken/stale findings should be visible as diagnostics, not silently buried.

### Suggested Next Checks

- [ ] Re-run clean v2.x/v2.6 consreview against v4.2.10 agent recall behavior.
- [ ] Decide whether to refresh or explicitly accept the current `age_stale=88` drift set before clean review.
- [x] Triage residual lint debt: broken links are 0; remaining orphans/large files are accepted corpus curation debt.
- [ ] Add recall miss taxonomy output to `bin/recall_smoke.py` if future misses appear.
- [x] Verify schema migration/backfill with an old-DB reproducer before changing update behavior.
- [ ] Keep Obsidian export in maintenance mode only: no new human-facing IA until agent recall quality stays stable after review.

### Deferred: v4.3 Vault IA

- [ ] Replace flat `projects/` with deterministic `areas/<area>/_MOC.md` pages.
- [ ] Split `references/` into stable library, research archive, tools/provider KB, and data inventory.
- [ ] Rework topics as `topic_candidates`: generated, scored, reviewed, then promoted.
- [ ] Add `_review/topic_quality_report.md` with rejected/mixed/coherent candidate groups.
