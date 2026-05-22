# Eidetic TODO

## Next Session — v2.5 Drift Detection Stabilization

Context: v2.5 landed on `main` at `b992f81` after `26dffb8`. Treat the feature as shipped-but-not-clean until the findings below are fixed and covered by tests.

### Blockers

- [ ] Preserve the "index.db is derived" invariant. Move `drift_findings`, `first_seen`, `resolved_at`, and the `__drift_check__` throttle marker out of `index.db` into a dedicated runtime state DB such as `~/.claude/memory-system/db/drift_state.db`, or explicitly redesign/document the storage contract.
- [ ] Fix baseline semantics. The first drift run must never apply penalty. Deduplicate findings inside one run before incrementing `first_seen`; penalty should start only after a finding appears in a later run.
- [ ] Make drift findings granular. Replace the current `(path, drift_type)` identity with `(path, drift_type, target_hash)` or equivalent so multiple broken links in one file can be tracked and auto-resolved independently.
- [ ] Fix wikilink false positives. Reuse the lint wikilink parser or shared parser logic so regex/code/placeholders like `[[^\\]]`, `[[{target}]]`, `[[...]]`, and shell snippets are not treated as broken Obsidian links.
- [ ] Exclude `source='code-index'` from wikilink drift unless a future code-specific checker intentionally validates code references.
- [ ] Enforce the hook write-lock invariant for drift writes. SessionStart DB writes must use the shared `~/.claude/memory-system/.memory.lock` contract or a documented equivalent.
- [ ] Fix repeated code indexing. `code_index.py` currently deletes by project slug, but the slug can change (`eidetic` vs `Users-mikhailkozlov-Documents-cursore-eidetic`), causing `UNIQUE constraint failed: memory_chunks.path, memory_chunks.section_heading` and hiding new code such as `bin/drift_check.py` from code search.

### Concept Checks

- [ ] Keep drift as a quality/review layer, not a replacement for relevance. Old memory is not automatically false; age drift should mean "needs review" and only weakly affect ranking where appropriate.
- [ ] Keep differential penalty non-stacking with freshness, but make the rule explicit in README/CLAUDE docs after storage is corrected.
- [ ] Revisit confidence escalation. Counting chunks from `source='agent-extracted'` is not the same as counting independent agent updates/history.
- [ ] Add visible but low-noise drift diagnostics. Crash-guarding with `|| true` is correct for SessionStart, but failures should be observable outside chat/stdout.

### Required Tests / Smokes

- [ ] Baseline run creates findings with no penalized `first_seen > 1`.
- [ ] Second run of the same unresolved findings enables penalty.
- [ ] Duplicate links/chunks inside one run do not increment `first_seen`.
- [ ] Fixing one broken link in a file resolves only that target, not unrelated targets.
- [ ] Code snippets/placeholders are ignored by wikilink drift.
- [ ] Repeated `code_index.py` on the same project succeeds and indexes `bin/drift_check.py`.
- [ ] Search smoke for `--type code "drift_check"` returns Eidetic `bin/drift_check.py`, not unrelated projects.
- [ ] `health.sh`, `lint.sh`, `py_compile`, and GitHub Actions remain green.

### Evidence From Review

- Live v2.5 state observed: `173 active` drift findings: `162 age_stale`, `11 broken_wikilink`, `0 confidence_escalation`.
- Baseline-copy test showed `7 broken_wikilink` rows with `first_seen > 1` on the first run.
- Code search smoke for `drift_check` did not return `bin/drift_check.py`; direct `code_index.py` rerun failed with `UNIQUE constraint failed`.
- `health.sh` passed and installed runtime matched repo files, so this is a correctness/design follow-up, not a broken install.
