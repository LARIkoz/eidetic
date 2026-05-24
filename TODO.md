# Eidetic TODO

## Next Session — v4.3 Vault IA

Context: v4.1/v4.2 moved Eidetic from "searchable AI memory" into a human-readable Obsidian export path. The current flat vault folders (`projects`, `references`, `rules`, `topics`) are not yet a reliable information architecture. Topic synthesis is now opt-in until v4.3 replaces it with reviewed topic candidates.

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

### v4.3 Vault IA Goals

- [ ] Add `card_kind`: `decision`, `bug`, `finding`, `handoff`, `todo`, `status`, `reference`, `research`, `profile`, `rule`.
- [ ] Add project/area identity from source path and metadata.
- [ ] Replace flat `projects/` with deterministic `areas/<area>/_MOC.md` pages.
- [ ] Split `references/` into stable library, research archive, tools/provider KB, and data inventory.
- [ ] Rework topics as `topic_candidates`: generated, scored, reviewed, then promoted.
- [ ] Add `_review/topic_quality_report.md` with rejected/mixed/coherent candidate groups.

### Suggested Next Checks

- [ ] Export normal vault and verify it does not create `topics/` unless `--synthesize` is passed.
- [ ] Validate the generated Obsidian vault visually: `HOME.md`, folder MOCs, graph grouping, and backlinks.
- [ ] Audit top duplicate-looking `projects/` cards and map them into candidate `card_kind` values.
- [ ] Audit `references/` and define the first deterministic folder split.
