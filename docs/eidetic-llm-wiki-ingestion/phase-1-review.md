# Phase 1 Pre-implementation Review

**Program:** Eidetic LLM Wiki ingestion
**Phase:** 1 — Core ingestion contract with writes disabled
**Review type:** fresh-context pre-implementation architecture/contract review
**Date:** 2026-07-16
**Reviewed commit:** `74c8920f896da8f80e389fdf7fbd913502a0002e`
**Initial specification commit:** `7bf32b75b50a630aaa95792578754b32ceb5675b`
**Accepted Phase 0 commit:** `3757e9a`
**Review mode:** independent read-only runtime, network disabled
**Verdict:** CLEAN
**P0/P1/P2:** 0 / 0 / 0
**Implementation authority:** none; separate owner GO pending

## Outcome

The exact reviewed commit is coherent and ready for an owner decision about a
separate Phase 1 implementation GO. It remains documentation-only and creates
no schemas/contracts, worker, tests, SDK/importer change, provider execution,
target, grant, receipt, or durable state.

The frozen repository boundary is one-way:

```text
future eidetic-importer -> eidetic-sdk -> public eidetic.ingestion contract -> Core
future eidetic-importer -> provider layer for LLM/local-MLX atomization
Core -X-> eidetic-sdk / eidetic-importer / provider layer
```

- Core owns canonical contract sources, protocol semantics, validation,
  logical target state, visible-effect preview, future authorization/durable
  write, receipts/recovery, storage/search, and existing Core MLX embeddings.
- The separate SDK owns typed public-protocol transport/session orchestration
  and later consumes a pinned immutable Core contract artifact; it has no Core
  checkout/submodule/path dependency and no canonical schema copy.
- The separate future importer owns document decoding, OCR, chunking,
  LLM/local-MLX atomization, provider routing, repair, manifests, and source
  checkpoints while using only the SDK boundary.

## Reviewed Scope

Commit `74c8920` changes exactly:

- `docs/eidetic-llm-wiki-ingestion/phase-1-requirements.md`;
- `docs/eidetic-llm-wiki-ingestion/phase-1-spec.md`;
- `docs/eidetic-llm-wiki-ingestion/phase-1-test-plan.md`; and
- `docs/eidetic-llm-wiki-ingestion/phase-1-worktree-inventory.md`.

No implementation, Core Engine, SDK, importer, M3, hook, MCP, provider,
installed-runtime, preview-package, or YouGile path appears in the commit.

## Initial Review And Rework

The first fresh-context review of `7bf32b7` returned `REWORK` with eight P1
findings and one P2 finding. Commit `74c8920` resolves them as follows:

| Initial finding | Resolution in reviewed packet | Result |
| --- | --- | --- |
| `EIDETIC-P1R-001` — no exact grant-retrieval wire contract | Exact disabled submit plus reserved unconsumed candidate-grant request/result, binding, repeat retrieval, expiry, consumption, and post-`INTENT` rules | RESOLVED |
| `EIDETIC-P1R-002` — target/effect/binding ambiguity | Core-derived logical target ID, canonical absent/present state, ordered action table, exact structured visible effect, and exact preview-binding bytes | RESOLVED |
| `EIDETIC-P1R-003` — unverifiable chunk-text digest | Required false verification flags distinguish declared coverage from recomputed candidate/content/span/ledger integrity | RESOLVED |
| `EIDETIC-P1R-004` — support could escape chunk | Offset/span-length equality, exact referenced-chunk containment, and split-row rule for cross-chunk evidence | RESOLVED |
| `EIDETIC-P1R-005` — missing source timestamps | Always-present independently nullable source-created/source-updated fields with one canonical UTC grammar and digest inclusion | RESOLVED |
| `EIDETIC-P1R-006` — disabled submit/schema contradiction | Eight exact request discriminators; both blocked operations deny after structural validation and before semantic processing | RESOLVED |
| `EIDETIC-P1R-007` — blank-line response contradiction | Exactly one sanitized response per physical input line, including blank/malformed/final-without-LF; empty EOF emits none | RESOLVED |
| `EIDETIC-P1R-008` — write traps installed too late | Isolated `python3 -I -B -S` child, pre-import traps, disposable snapshotted roots, source/installed parity, and startup/cache/bytecode detection | RESOLVED |
| `EIDETIC-P2R-001` — unnamed evidence fixtures | Stable `SCH-*`, `CFG-*`, and `DEP-*` evidence IDs with requirements traceability | RESOLVED |

The second fresh-context review bound to full commit
`74c8920f896da8f80e389fdf7fbd913502a0002e` returned:

```text
VERDICT: CLEAN
P0: 0
P1: 0
P2: 0
FINDINGS: NONE
```

## Contract And Evidence Review

| Gate | Reviewed result |
| --- | --- |
| Requirements | 32 unique IDs, `P1-001` through `P1-032`, each with planned evidence |
| Canonical contract set | 15 Core-owned files under future `contracts/ingestion/v1/` |
| Stable evidence IDs | 124 unique planned IDs across contract, compatibility, canonicalization, candidate, manifest, preview, denial, no-write, privacy, schema, configuration, deployment, and regression classes |
| JSON examples | All nine normative JSON blocks parse; support example offsets match normalized Unicode-scalar span length |
| Framing | One response per physical line; deterministic blank/malformed behavior |
| Blocked authority | `submit_ingest` and `retrieve_candidate_grant` are schema-visible but non-executable and fail before semantics/state allocation |
| Target/effect binding | Logical identity, canonical state, action, visible effect, preview bytes/token, and mutation expectation are exact and path-free |
| Source evidence | Source/chunk snapshot limits are explicit; support containment is exact; no semantic verification is falsely claimed |
| No-write proof | Pre-import/startup/operation/installed paths, temp/cache/home, source/contract, and protected canaries are covered |
| Repository boundary | Core canonical source -> immutable artifact -> SDK generated client; importer remains separate above SDK |
| Implementation scope | Clean dedicated Core worktree plus exact allowlist; no dirty primary worktree use |

## Exact Verification

The following documentation gates passed:

```text
git diff --check 7bf32b7..74c8920
  -> no output

git show --format= --name-only 74c8920
  -> exactly the four Phase 1 packet files

requirements/traceability check
  -> 32 requirements; 32 matching traceability rows

stable evidence-ID check
  -> 124 definitions; 124 unique

canonical contract inventory
  -> 15 files

normative JSON/span check
  -> nine JSON blocks parse; support offsets match span length

stale boundary terminology
  -> no schemas/sdk path, rendered-content digest, global absent-state digest,
     13-file contract count, or nonblank-only response rule

independent review runtime
  -> complete; one job successful; read-only; network disabled; final CLEAN
```

Code/unit/crash/concurrency/installed-runtime tests are not applicable to this
pre-implementation documentation phase. They remain mandatory planned evidence
for a later authorized implementation/deployment and are not marked passed.

## Privacy And Protected-state Review

- Phase 1 remains synthetic, offline, provider-free, and write-disabled.
- Core neither reads nor writes provider credentials, cache, balance, usage, or
  penalty state.
- No physical target path, raw source snapshot, personal identity, credential,
  raw authorization grant, or provider/model route enters the public protocol.
- A future SDK journal may persist only non-authorizing approval/grant
  references, never raw grant material.
- No cleanup, migration, compaction, or recomputation of protected state is
  authorized.

## Worktree Preservation

Pre-existing owner changes in `bin/m3_judge.py`, `hooks/session-signals.sh`, and
untracked M3/evaluation/schema/test/planning paths remain outside commits
`7bf32b7` and `74c8920`. This review did not stage, reset, stash, delete, copy,
or modify them.

## Acceptance Boundary

The Phase 1 specification review is `CLEAN`, but CLEAN is a decision gate, not
implementation authority. Implementation remains stopped.

Only an explicit owner instruction bound to the reviewed commit, for example:

```text
GO Phase 1 implementation at 74c8920f896da8f80e389fdf7fbd913502a0002e
```

may authorize creation of a clean dedicated Core implementation worktree and
the exact reviewed allowlist. Even that GO would not authorize:

- any SDK or importer modification;
- document/LLM/local-MLX/provider execution;
- real-source ingestion;
- scope enablement or installed deployment;
- executable submit/grant/receipt/retrieval authority;
- release, tag, publish, push, or pull request; or
- capture of existing M3/preview/YouGile owner work.

SDK Phase 2 starts only after Phase 1 implementation and post-implementation
acceptance. Importer Phase 3 remains a later separate-repository phase.
