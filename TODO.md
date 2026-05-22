# Eidetic TODO

## Next Session — v2.5 Drift Detection Stabilization

Context: v2.5 landed on `main` at `b992f81` after `26dffb8`. The original consreview blockers were addressed through `399b2e8`, then follow-up verification found and fixed a full-index CI regression plus two recall/drift edge cases. Keep this file as the regression checklist for future v2.5 changes.

### Closed Blockers

- [x] Preserve the "index.db is derived" invariant. Drift observations now live in `~/.claude/memory-system/db/drift_state.db`.
- [x] Fix baseline semantics. The first drift run creates findings with `first_seen=1`; penalty starts only after a later run.
- [x] Make drift findings granular. Drift identity uses `(path, drift_type, detail)` so multiple broken links in one file can be tracked and auto-resolved independently.
- [x] Fix wikilink false positives. Regex/code/placeholders like `[[^\\]]`, `[[{target}]]`, `[[...]]`, and shell snippets like `[[-f ~/.cargo/env]]` are ignored.
- [x] Exclude `source='code-index'` from wikilink drift unless a future code-specific checker intentionally validates code references.
- [x] Enforce the hook write-lock invariant for drift writes through the shared PID lock used by SessionStart/Stop hooks.
- [x] Fix repeated code indexing. `code_index.py` deletes by path prefix, so changed project slugs no longer leave conflicting stale chunks.
- [x] Fix full-index CI regression after crash-safe reindex. `run_full()` reopens the swapped DB before counting chunks.
- [x] Keep vector fallback drift-aware and prevent vector results from displacing strong exact/code identifier FTS matches.

### Concept Checks

- [x] Keep drift as a quality/review layer, not a replacement for relevance. Old memory is not automatically false; age drift is a review/quality signal.
- [x] Keep differential penalty non-stacking with freshness.
- [x] Revisit confidence escalation. Current implementation remains conservative because live state has effectively no repeated agent-extracted memories.
- [x] Add visible but low-noise drift diagnostics. Crash-guarding with `|| true` remains correct for SessionStart; failures are now covered by local/CI smokes.

### Required Tests / Smokes

- [x] Baseline run creates findings with no penalized `first_seen > 1`.
- [x] Second run of the same unresolved findings enables penalty.
- [x] Duplicate links/chunks inside one run do not increment `first_seen`.
- [x] Fixing one broken link in a file resolves only that target, not unrelated targets.
- [x] Code snippets/placeholders are ignored by wikilink drift.
- [x] Repeated `code_index.py` on the same project succeeds and indexes `bin/drift_check.py`.
- [x] Search smoke for `--type code "drift_check"` returns Eidetic `bin/drift_check.py` first.
- [x] `health.sh`, `lint.sh`, `py_compile`, and local CI-equivalent smokes pass.

### Evidence From Review

- Live v2.5 state observed: `173 active` drift findings: `162 age_stale`, `11 broken_wikilink`, `0 confidence_escalation`.
- Baseline-copy test showed `7 broken_wikilink` rows with `first_seen > 1` on the first run.
- Code search smoke for `drift_check` did not return `bin/drift_check.py`; direct `code_index.py` rerun failed with `UNIQUE constraint failed`.
- `health.sh` passed and installed runtime matched repo files, so this is a correctness/design follow-up, not a broken install.
