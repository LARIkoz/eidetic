# Changelog

All notable changes to Eidetic are documented here.

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
