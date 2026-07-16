# Phase 1 Source And Worktree Inventory

**Snapshot date:** 2026-07-16
**Repository:** Eidetic Core
**Current branch:** `main`
**Accepted Phase 0 commit:** `3757e9a`
**Purpose:** prevent pre-existing M3/preview work from entering the Phase 1
specification or later implementation commits

## Phase 1 Packet Paths

The only packet changes created by this specification pass are:

```text
docs/eidetic-llm-wiki-ingestion/phase-1-requirements.md
docs/eidetic-llm-wiki-ingestion/phase-1-spec.md
docs/eidetic-llm-wiki-ingestion/phase-1-test-plan.md
docs/eidetic-llm-wiki-ingestion/phase-1-worktree-inventory.md
```

`phase-1-review.md` does not exist until a fresh reviewer has reviewed the
committed packet.

## Pre-existing Tracked Owner Changes

These tracked modifications existed before the Phase 1 packet and are excluded:

```text
bin/m3_judge.py
hooks/session-signals.sh
```

## Pre-existing Untracked Owner Changes

These untracked paths or path groups existed before the Phase 1 packet and are
excluded:

```text
bin/m3_judge_ac0_benchmark.py
bin/m3_judge_ac0_compare.py
bin/m3_judge_ac0_merge.py
bin/m3_judge_benchmark.py
bin/m3_judge_codex_worker.py
bin/m3_judge_core.py
bin/m3_judge_external_reference.py
bin/m3_judge_grok_worker.py
bin/m3_judge_import_reference.py
bin/m3_judge_mlx_worker.py
bin/m3_judge_reference_privacy.py
bin/m3_judge_runtime.py
bin/m3_judge_shadow.py
docs/adr/0002-core-judge-system.md
docs/m3-*.md
eval/
schemas/m3_judge/
tests/test_m3_judge_ac0_compare.py
tests/test_m3_judge_ac0_merge.py
tests/test_m3_judge_core.py
tests/test_m3_judge_grok_worker.py
```

The wildcard is inventory shorthand only; it is never a staging pathspec.
Every Phase 1 commit stages explicit file paths.

## Specification Commit Gate

Before committing the packet:

```text
git diff --cached --name-only
```

must equal the four Phase 1 packet paths above. Any other staged path aborts the
commit. The pre-existing tracked and untracked owner changes remain untouched.

## Future Implementation Worktree

Phase 1 implementation must not use this dirty worktree. After a CLEAN review
and a separate owner GO, create a dedicated clean branch/worktree from the exact
reviewed specification commit. The worktree path and branch name are chosen at
that time and recorded in the GO; this packet does not create them.

The implementation worktree must begin with:

```text
git status --short
-> no output
```

Its staged set must remain a subset of the implementation allowlist in
`phase-1-spec.md`. No stash, reset, clean, delete, copy, or index manipulation
is allowed against the primary owner's dirty worktree.

## Drift Rule

This inventory is a snapshot, not permission to ignore later changes. Before
implementation and before every commit, rerun status in both the primary and
dedicated worktrees. Any new overlapping path or changed reviewed base blocks
the phase until the packet is updated and reviewed again.
