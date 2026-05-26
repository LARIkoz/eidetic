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
- [x] Mark topic synthesis as experimental in docs until deferred Vault IA lands.

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

### Closed In v4.2.11

- [x] Run clean consreview against v4.2.10. Pipeline was DEGRADED (`audit=ISSUES`, `mechanical=FAIL`, `redteam=WEAK`), so final fixes are based on raw voices plus validator artifacts.
- [x] Count confidence-escalation drift at history-event level instead of chunk level.
- [x] Honor `EIDETIC_MEMORY_SYSTEM` across wrapper scripts, MCP reindex/serendipity, hooks, install, update, and update-check routing.
- [x] Include repo-local `output/handoff-*/state.md` files in SessionStart handoff discovery.
- [x] Protect cleanup candidates with large frontmatter and inbound skill wikilinks.
- [x] Keep compound history entries inside the existing `## History` section.
- [x] Centralize vault polish/synthesis model IDs behind environment overrides and preserve literal prompt placeholders in note bodies.
- [x] Add regression tests for the above.

### Closed In v4.2.12

- [x] Run clean consreview against v4.2.11. Pipeline FAILED quorum (`3/8 voices`, `2/3 families`), so fixes are based only on directly verified raw findings.
- [x] Index fallback Stop-hook signals under the active memory-system root.
- [x] Keep cleanup signal scans and archive destinations in the same custom root.
- [x] Protect cleanup candidates linked by frontmatter `name:` aliases.
- [x] Route MCP `memory_lint` through the active index path.
- [x] Refresh hook registrations with `EIDETIC_MEMORY_SYSTEM` during custom-root update.
- [x] Make install/update/check-update metadata path handling robust to shell quotes.
- [x] Ignore fenced/inline code examples when counting confidence-escalation history events.

### Closed In v4.2.13

- [x] Run clean consreview against v4.2.12. Pipeline FAILED quorum (`3/8 voices`, `2/3 families`), so fixes are based only on directly verified raw findings.
- [x] Make markdown section splitting fence-aware so fenced `## History` examples stay inside one code block chunk and do not trigger confidence-escalation drift.
- [x] Make SessionStart hook code/vector refresh pass custom-root paths through Python `argv` instead of interpolating paths into Python source strings.
- [x] Add CI regressions for fenced headings and SessionStart custom roots with apostrophes.

### Closed In v4.2.14

- [x] Keep Stop-hook signal extraction on Haiku/`claude-batch` as primary.
- [x] Add `codex-batch` fallback with `gpt-5.4-mini` when the Claude route is missing, fails, or returns `EMPTY`.
- [x] Add CI regression proving Codex fallback output is passed to `compound.py`.
- [x] Expose signal route overrides through `EIDETIC_SIGNAL_CLAUDE_MODEL`, `EIDETIC_SIGNAL_CODEX_MODEL`, `EIDETIC_SIGNAL_CODEX_REASONING`, and `EIDETIC_SIGNAL_CODEX_TIMEOUT`.

### Closed In v4.2.15

- [x] Run clean consreview against v4.2.14. Pipeline was DEGRADED (`audit=ISSUES`, `mechanical=FAIL`, `redteam=WEAK`), so final fixes are based on corrected findings and direct local repros.
- [x] Filter Stop-hook extractor output to contract-shaped `Decision:`, `Rule:`, `Worked:`, `Failed:`, and `Knowledge:` lines before compounding.
- [x] Fall back to Codex when Claude succeeds but returns `EMPTY` or no valid signal lines.
- [x] Add defense-in-depth prefix filtering in `compound.py` for direct/manual stdin usage.
- [x] Keep SessionStart degraded fallback scoped to the current CWD project `MEMORY.md` instead of injecting the first project on disk.
- [x] Add `EIDETIC_SIGNAL_CLAUDE_TIMEOUT` and align async Stop-hook registration timeout with the Claude + Codex fallback budget.

### Closed In v4.2.16

- [x] Triage the 88 active `age_stale` findings before the next clean consreview: all were `type: project`, but most were historical findings/research/reference/handoffs rather than active stale status.
- [x] Make age drift honor lifecycle status: archived, deprecated, obsolete, resolved, and superseded cards are no longer counted as active stale findings.
- [x] Apply `card_kind` freshness thresholds before broad memory `type`: findings/research/reference/handoffs use 90 days; active bug/todo/status cards use a 60-day backlog window.
- [x] Stop applying calendar-age drift to code-index chunks; code freshness is covered by source mtime reindexing and vector identity, not by "file older than N days".
- [x] Add CI regression proving the old blanket `type: project` 30-day threshold no longer turns historical project memory into review noise.

### Closed In v4.2.17

- [x] Run clean consreview against v4.2.16. Pipeline was DEGRADED (`audit=ISSUES`, `mechanical=FAIL`, `redteam=WEAK`), so final fixes are based on raw voices, redteam misses, and direct local repros.
- [x] Treat explicit `status: fixed` as inactive for age drift, matching search/context lifecycle weighting.
- [x] Keep feedback/user memories `current` unless lifecycle status is explicit frontmatter or `superseded_by`; this prevents active P3 rules from being demoted by words like "fixed", "closed", or "deprecated" in descriptions.
- [x] Migrate existing derived feedback/user rows that were inferred inactive without explicit frontmatter status.
- [x] Escape SQL `LIKE` wildcards in code-index directory deletion to avoid deleting sibling project code rows when a path contains `_` or `%`.
- [x] Install update files through temp-file plus atomic rename instead of in-place `cp`.
- [x] Add CI regressions for these review findings.

### Closed In v4.2.18

- [x] Run clean consreview against v4.2.17. Pipeline was DEGRADED (`audit=ISSUES`, `mechanical=FAIL`, `redteam=REFUTED`), so final fixes are based on raw voices, redteam misses, and direct local repros.
- [x] Fix Stop-hook transcript parsing for real Claude Code JSONL: read nested `message.role` / `message.content` and fall back to raw tail when parsing yields no turns.
- [x] Replace Stop-hook CI transcript fixtures with real nested schema, including assistant text blocks, so automatic session learning is covered by tests.
- [x] Make fresh install writes atomic for runtime files, hooks, skill, settings, and `.installed.json`.
- [x] Clean up temp files if `update.sh` atomic replacement fails.
- [x] Give MCP full reindex a longer timeout than incremental reindex.
- [x] Add regression proving explicit frontmatter `status: fixed` remains `fixed` through status inference.

### Closed In v4.2.19

- [x] Run clean consreview against v4.2.18. Pipeline FAILED after voice fanout (`5/8 voices OK`; Anthropic voices failed before synthesis), so final fixes are based on raw voice outputs and local source checks.
- [x] Make `update.sh` hook settings, custom-root skill rewrite, and `.installed.json` metadata writes atomic with temp-file plus `os.replace`.
- [x] Exclude `tool_result` and other non-text transcript blocks from Stop-hook signal excerpts so command output is not labeled as user conversation.
- [x] Add CI coverage for Stop-hook tool-result exclusion.
- [x] Add CI coverage proving MCP full reindex uses the 300s timeout path.

### Closed In v4.2.20

- [x] Run clean consreview against v4.2.19. Pipeline DEGRADED (`5/8 voices OK`; Anthropic voices/synth failed), so final fixes are based on raw voices and local repros.
- [x] Remove unsafe raw-tail Stop-hook fallback when the capped JSONL window contains no safe text turns.
- [x] Parse only complete JSONL lines from the capped tail window so partial oversized tool-result lines are not sent to the extractor.
- [x] Add CI coverage for an oversized final `tool_result` line that previously bypassed the v4.2.19 exclusion through raw-tail fallback.

### Closed In v4.2.21

- [x] Run clean consreview against v4.2.20. Pipeline DEGRADED (`5/8 voices OK`; Anthropic voices/synth failed), so final fixes are based on raw voices and local source checks.
- [x] Preserve a complete safe JSONL line when the capped tail window starts exactly on that line boundary.
- [x] Add CI coverage for a complete 8000-byte JSONL user line starting exactly at the tail-window boundary.

### Closed In v4.3.0

- [x] Add metadata-only Claude `PostToolUse` lifecycle capture for `Write`, `Edit`, and `MultiEdit`.
- [x] Store lifecycle events as append-only JSONL facts with HMAC path hashes and no raw paths, filenames, content, diffs, stdout/stderr, tool results, or transcript text.
- [x] Register the dedicated lifecycle hook through install/update and cover retention, sanitization, custom-root routing, and hook registration in tests.

### Closed In v5.0.0

- [x] Add stable per-result `detail_id` selectors to JSON and MCP search payloads without removing existing fields.
- [x] Add `search.sh --detail <detail_id|path>` for exact full-content retrieval after a search candidate looks relevant.
- [x] Add MCP `memory_search_detail` for the same detail step in structured agent clients.
- [x] Add `--brief` / `--full`; broad CLI queries now default to compact rows while `--full` keeps snippet-rich output.
- [x] Preserve `no_confident_results=true` as the hard structured search contract and add progressive-search regression tests.

### v2.6 Agent Memory Quality Goals

- [x] Add durable schema fields for agent retrieval: `card_kind`, `status`, `area`, `supersedes`, `superseded_by`, `last_verified`.
- [x] Replace overloaded `type: project` semantics with `card_kind`: `decision`, `bug`, `finding`, `handoff`, `todo`, `status`, `reference`, `research`, `profile`, `rule`.
- [x] Add status-aware ranking: active/current notes outrank resolved/superseded/archived notes.
- [x] Add confidence-aware search UX: when top results are weak/vector-only, report "no confident result" instead of returning random-looking matches.
- [x] Add first agent recall regression set for real queries: large-prompt bug, Gap Pipeline concept, Obsidian best practices, and weak negative recall.
- [x] Add stale-context detection: `health.sh` should report when `memory-context.md` was assembled from an older index.
- [x] Tighten drift handling for agent recall: broken/stale findings should be visible as diagnostics, not silently buried.

### Suggested Next Checks

- [x] Run `/qreview` against v4.2.21 because the patch was minor, test-backed, and already CI-green. Verdict: SHIP; no blocker/important/check findings.
- [x] Use `/qreview` for narrow, test-backed follow-up diffs without schema/provider/security/update-boundary changes; keep full consreview for broad/high-risk changes or accumulated minor batches.
- [x] Split old mixed v5 roadmap into agent-facing lifecycle/search/distribution work and deferred human-facing Vault/Soul/dashboard work.
- [x] Draft canonical v4.3 Lifecycle Signals design plan: `plan-v4.3-lifecycle-signals.md`.
- [x] Run `/qreview` R1-R3 on the v4.3 Lifecycle Signals design plan and revise for findings: no raw paths/cwd, vault-root skip, lockless append, separate hook entry, real redacted fixtures, retention, timeout units, HMAC key safety, and custom-root edge cases.
- [x] Run `/qreview` R4 on the revised v4.3 plan. Verdict: `SHIP-WITH-EDITS`; audit: `OK`; mechanical: `PASS`. No further design review required after listed edits.
- [x] Apply R4 listed edits in the canonical plan and repo docs: symlink sanitization, O_APPEND fallback, full sensitive-path fixtures, lock-invariant docs, bounded ancestor scan, operation enum, and schema cleanup.
- [x] Implement v4.3 Lifecycle Signals Phase A from the approved canonical plan: hook, parser, install/update registration, cleanup retention, tests, smoke, and CI.
- [x] Resolve implementation `/qreview` R2 `SHIP-WITH-EDITS` closeout and run final local checks. Applied docs/fd/copy/env fixes; kept `timeout: 2` because current Claude Code docs define hook timeout in seconds.
- [x] Implement v5.0 Progressive Search: compact broad search, stable `detail_id`, CLI/MCP detail retrieval, and structured-contract regressions.
- [ ] Revisit v3.0 Task Planner Bridge after lifecycle signals are stable, or earlier if explicitly routed.
- [x] Decide whether to refresh or explicitly accept the current `age_stale=88` drift set before clean review.
- [x] Triage residual lint debt: broken links are 0; remaining orphans/large files are accepted corpus curation debt.
- [ ] Add recall miss taxonomy output to `bin/recall_smoke.py` if future misses appear.
- [x] Verify schema migration/backfill with an old-DB reproducer before changing update behavior.
- [ ] Keep Obsidian export in maintenance mode only: no new human-facing IA until agent recall quality stays stable after review.

### Deferred: v6+ Vault IA / Human-Facing Layers

- [ ] Replace flat `projects/` with deterministic `areas/<area>/_MOC.md` pages.
- [ ] Split `references/` into stable library, research archive, tools/provider KB, and data inventory.
- [ ] Rework topics as `topic_candidates`: generated, scored, reviewed, then promoted.
- [ ] Add `_review/topic_quality_report.md` with rejected/mixed/coherent candidate groups.
- [ ] Keep Soul/personality adaptation deferred until lifecycle signal quality has evidence-weighted guardrails and reset controls.

### Post-v6 Research: SkillOpt-Style Skill Optimization

- [ ] Evaluate SkillOpt-style optimizer as a later prototype after current lifecycle, distribution, planner, and v6 deferred tracks.
- [ ] Treat optimized skills as proposed patches only: require scored rollouts, train/validation/test split, regression suite, and qreview/consreview before accepting changes to SKILL.md, CLAUDE.md, or command docs.
- [ ] Prefer local Eidetic signals, recall-smoke misses, qreview findings, and real task failures as training/evaluation material; do not rewrite skills from unvalidated chat impressions.
