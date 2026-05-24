# Eidetic TODO

## Next Session — v4.2 Vault Export Stabilization

Context: v4.1/v4.2 moved Eidetic from "searchable AI memory" into a human-readable Obsidian export path: LLM polish, plug-and-play vault open, LLM clustering, and synthesized topic pages. The next work should protect that product surface, not reopen v2.5 drift detection unless a regression appears there.

### Closed In v4.2.1

- [x] Keep `install.sh` non-interactive by default. Daily vault export is opt-in through `EIDETIC_SETUP_CRON=1`.
- [x] Add `.DS_Store` to `.gitignore` and remove generated Finder files from the working tree.
- [x] Synchronize public docs/versioning from v4.0/v2.5 language to v4.2.x.
- [x] Update MCP server version and expose v4 export controls instead of forcing the slow default path.
- [x] Keep MCP export safe by default: no LLM polish/synthesis unless explicitly requested.
- [x] Add CI smoke coverage for no-LLM Obsidian vault export.

### Current Residual Risks

- [ ] LLM polish/synthesis full-path smoke was not run in CI because it requires `claude-batch` and API budget.
- [ ] Filename quality still needs a focused pass for punctuation, truncation, and long title readability.
- [ ] Topic-source wikilinks should be checked after a real synthesis run, not only after the fast no-LLM export.
- [ ] Memory lint still reports corpus debt: broken links, orphans, and large files. Treat this as input-quality debt, not a core runtime failure.

### Suggested Next Checks

- [ ] Run one bounded LLM export smoke: `export-vault --polish-count 3` plus synthesis on a small project.
- [ ] Validate the generated Obsidian vault visually: `HOME.md`, folder MOCs, topic pages, graph grouping, and backlinks.
- [ ] Decide whether CLI export should keep LLM enrichment on by default or switch to fast/no-API default with explicit `--polish`.
- [ ] Update GitHub repository description/topics if positioning should include Obsidian, AI wiki, and vault export.
