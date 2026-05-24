# Eidetic TODO

## Next Session — v2.6 Agent Memory Quality

Context: pause Obsidian/human-facing vault work until the agent-facing memory layer is excellent. A clean vault is secondary; the core product is an agent that recalls the right rules, decisions, bugs, and project state, and refuses low-confidence retrieval instead of surfacing random near-matches.

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

### v2.6 Agent Memory Quality Goals

- [ ] Add durable schema fields for agent retrieval: `card_kind`, `status`, `area`, `supersedes`, `superseded_by`, `last_verified`.
- [ ] Replace overloaded `type: project` semantics with `card_kind`: `decision`, `bug`, `finding`, `handoff`, `todo`, `status`, `reference`, `research`, `profile`, `rule`.
- [ ] Add status-aware ranking: active/current notes outrank resolved/superseded/archived notes.
- [x] Add confidence-aware search UX: when top results are weak/vector-only, report "no confident result" instead of returning random-looking matches.
- [x] Add first agent recall regression set for real queries: large-prompt bug, Gap Pipeline concept, Obsidian best practices, and weak negative recall.
- [x] Add stale-context detection: `health.sh` should report when `memory-context.md` was assembled from an older index.
- [ ] Tighten drift handling for agent recall: broken/stale findings should be visible as diagnostics, not silently buried.

### Suggested Next Checks

- [ ] Rebuild context and verify `memory-context.md` counts match the current index.
- [ ] Expand recall benchmark from 4 smoke cases to 20 queries and classify misses: stale, noisy, low-confidence vector fallback, wrong type, missing memory.
- [ ] Design the minimal schema migration that improves agent recall without requiring human-vault IA.
- [ ] Keep Obsidian export in maintenance mode only: no new human-facing IA until agent recall quality passes.

### Deferred: v4.3 Vault IA

- [ ] Replace flat `projects/` with deterministic `areas/<area>/_MOC.md` pages.
- [ ] Split `references/` into stable library, research archive, tools/provider KB, and data inventory.
- [ ] Rework topics as `topic_candidates`: generated, scored, reviewed, then promoted.
- [ ] Add `_review/topic_quality_report.md` with rejected/mixed/coherent candidate groups.
