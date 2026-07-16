# Phase 1 Test And Evidence Plan

**Status:** candidate for pre-implementation review
**Phase:** 1 — Core ingestion contract with writes disabled
**Implementation authority:** none
**Companion:** `phase-1-spec.md` and `phase-1-requirements.md`

## 1. Test Rule

Phase 1 is accepted only when schemas, runtime behavior, golden fixtures,
negative fixtures, process tests, static boundaries, and filesystem/state
snapshots agree. A schema-only pass or a worker-only pass is insufficient.

All tests are synthetic, offline, deterministic, and provider-free. They must
not read real memory cards, real source documents, credentials, provider cache,
or central penalty state.

## 2. Planned Test Files

```text
tests/test_ingestion_contract.py
tests/test_ingestion_worker.py
tests/test_ingestion_no_write.py

tests/fixtures/ingestion/v1/
  valid/
  invalid/
  canonical/
  compatibility/
  preview/
  privacy/
```

Fixture files are JSON only. They contain synthetic names, synthetic text,
logical identifiers, and canary strings. No personal identity, real path,
provider, credential, or copyrighted book excerpt is allowed.

## 3. Exact Source Commands

The implementation packet must make these commands reproducible from the clean
Phase 1 worktree:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest \
  tests.test_ingestion_contract \
  tests.test_ingestion_worker \
  tests.test_ingestion_no_write

PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'

git diff --check <reviewed-phase-1-spec-commit>..HEAD

git diff --name-only <reviewed-phase-1-spec-commit>..HEAD
```

If the repository test layout cannot import `tests.*` as modules, the reviewed
implementation may use these equivalent exact commands instead:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/test_ingestion_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/test_ingestion_worker.py
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/test_ingestion_no_write.py
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
```

The packet must record which command shape actually ran; it may not silently
replace a failing targeted command with only the broad discovery run.

No installed-runtime command, `install.sh`, LLM/provider call, source import,
or live target smoke is part of source implementation verification.

## 4. Contract And Schema Tests

| Test ID | Required proof |
| --- | --- |
| CT-001 | Every canonical JSON file parses and every local `$ref` resolves inside `contracts/ingestion/v1/`. |
| CT-002 | Contract manifest names exactly the 15 reviewed contract files under `contracts/ingestion/v1/`, one worker, six executable operations, blocked `submit_ingest` plus `retrieve_candidate_grant`, immutable artifact identity, and `submit_available=false`. |
| CT-003 | Contract-manifest/artifact digest is stable over path-sorted file names and exact bytes; a one-byte contract mutation changes it. |
| CT-004 | Request/response envelopes reject missing, unknown, duplicate, mistyped, or contradictory fields. |
| CT-005 | Strict UTF-8 JSONL rejects BOM, blank line, invalid UTF-8, duplicate keys, trailing bytes, `NaN`, `Infinity`, and a line over 64 MiB; every physical line including blank/malformed/final-without-LF produces exactly one sanitized response, while empty EOF produces none. |
| CT-006 | `ok=true` requires non-null result and null error; `ok=false` requires null result and a sanitized error. |
| CT-007 | Runtime operation and result shapes match the canonical schemas for every executable operation and both exact blocked-operation denial responses. |
| CT-008 | Runtime contains no private schema fork or second operation list that can drift without a parity-test failure. |
| CT-009 | Error schema contains the reviewed Phase 1 taxonomy and rejects traceback/path/raw-value details. |
| CT-010 | Reserved submit/grant/idempotency fields are accepted only in their exact blocked operation shapes; they are rejected in every executable payload and cannot reach semantic dispatch. |
| CT-011 | Core source contract bytes deterministically produce the reviewed immutable artifact version/digest; a checkout path, submodule, copied SDK schema tree, or changed bytes under the same identity fails. |

## 5. Compatibility Tests

| Test ID | Required proof |
| --- | --- |
| COMP-001 | Frozen `1.0` capabilities, health, validation, and preview fixtures pass. |
| COMP-002 | Unlisted `1.1` fails `incompatible_version`; it is not accepted merely because the major is `1`. |
| COMP-003 | `0.9`, `2.0`, malformed versions, and numeric versions fail before candidate processing. |
| COMP-004 | A future-minor simulation proves the rule that `1.1` cannot be listed without retaining the frozen `1.0` fixtures. |
| COMP-005 | Core product version, Engine API version, ingestion protocol version, contract/artifact digest, and build identity remain separate fields. |
| COMP-006 | Incompatible version handling performs no target inspection and no write attempt. |

## 6. Canonicalization And Digest Goldens

| Test ID | Required proof |
| --- | --- |
| CAN-001 | A fixed valid candidate produces exact canonical UTF-8 bytes and exact candidate/content digest goldens. |
| CAN-002 | A fixed manifest produces exact candidate, declared chunk-ledger, manifest, target-state, visible-effect, preview-binding, and token-digest goldens. |
| CAN-003 | Equivalent NFC/NFD and CRLF/LF text normalize as specified; invalid UTF-8/lone-surrogate inputs fail. |
| CAN-004 | Object insertion order does not change canonical bytes; array order does. |
| CAN-005 | Locale, timezone, process/session ID, and Python hash seed do not change candidate/manifest digests. |
| CAN-006 | Integers are canonical; floats, negative offsets/counts, booleans substituted for integers, and non-standard numbers fail. |
| CAN-007 | Candidate, content, chunk-ledger, manifest, span, source-object-claim, target-state, visible-effect, preview-binding, preview-token, grant-token, and grant-binding domains cannot collide on identical bytes. |
| CAN-008 | Caller-declared content, candidate, chunk-ledger, and span digest mismatches fail with stable findings. |
| CAN-009 | The candidate digest does not include `manifest_digest`; manifest recomputation binds the verified candidate digest without recursion. |
| CAN-010 | Reordering manifest candidate entries or chunk outcomes changes the manifest digest and preview binding. |
| CAN-011 | Source timestamps accept only null or exact second-precision UTC `Z`; absent/valid/malformed/fractional/offset/inverted fixtures are deterministic, and either timestamp mutation changes candidate/manifest digests. |
| CAN-012 | Logical target ID is exactly `ing1-` plus the Core-derived source-object-claim digest and changes with authenticated principal, scope, source system, or source object. |

Golden fixtures must include the canonical bytes, not only expected digests, so
a serializer change is reviewable rather than hidden behind a hash mismatch.

## 7. Candidate Validation Matrix

| Test ID | Required proof |
| --- | --- |
| CAND-001 | Minimal valid `create` candidate returns `status=valid`, authoritative digests, and exact flags `source_snapshot_verified=false`, `chunk_text_digests_verified=false`, `declared_coverage_only=true`, and `semantic_support_verified=false`. |
| CAND-002 | Valid candidates cover every allowed card kind and both evidence values. |
| CAND-003 | Invalid/case-changed card kind or evidence is rejected rather than defaulted or normalized. |
| CAND-004 | Empty/whitespace title, empty body, no claims, duplicate claim IDs, no support, and duplicate support IDs are invalid. |
| CAND-005 | Maximum accepted bounds pass; each title/body/claim/span/count/canonical-byte overflow fails. |
| CAND-006 | Source/document revisions must match every support reference. |
| CAND-007 | Zero-length, reversed, negative, boolean, or offsets whose difference is not the normalized span's Unicode-scalar length fail. |
| CAND-008 | Span digest is recomputed from normalized span text; mismatch fails. |
| CAND-009 | Core path, database/table ID, lock handle, internal row ID, provider/model route, credential, raw grant, idempotency key, or policy-verdict injection fails. |
| CAND-010 | `create`, one-predecessor actions, `split`, and `merge` enforce exact predecessor cardinality and uniqueness. |
| CAND-011 | An absent candidate never implies archive/supersede/delete. |
| CAND-012 | Same source claim/revision plus same candidate digest is recognized as a replay candidate; a different digest is surfaced as conflict, never silently normalized. |
| CAND-013 | Expected predecessor is structural input only; Phase 1 does not claim that source ordering is independently proven. |
| CAND-014 | Findings are stable, ordered, pointer-addressable, bounded, and do not echo rejected canary values. |
| CAND-015 | `source_created_at` and `source_updated_at` are always present, independently nullable, canonical when non-null, ordered when both present, and never synthesized from current time. |

## 8. Manifest And Chunk-accounting Matrix

| Test ID | Required proof |
| --- | --- |
| MAN-001 | Minimal complete manifest with contiguous chunks and one candidate validates and previews. |
| MAN-002 | Multi-chapter manifest with page-null and page-present support validates. |
| MAN-003 | Chunk offsets must start at zero, be ordered, contiguous, non-overlapping, and end at `normalized_text_length`. |
| MAN-004 | Duplicate/missing chunk IDs, gaps, overlaps, reversed ranges, and wrong final length invalidate completeness. |
| MAN-005 | Every `candidates` chunk has nonempty candidate entry IDs; other states have none. |
| MAN-006 | Every candidate entry is referenced exactly once by chunk accounting, and every referenced entry exists. |
| MAN-007 | Every candidate support `chunk_id` resolves to a manifest chunk with the same document revision and `chunk.start <= support.start < support.end <= chunk.end`. |
| MAN-008 | `owner_no_knowledge` requires an owner-review reference and reason; automatic/empty no-knowledge fails. |
| MAN-009 | Empty, malformed, truncated, or schema-invalid atomization is representable only as repair/failure, never successful zero knowledge. |
| MAN-010 | Candidate/content/span and declared chunk-ledger digests are recomputed; source/chunk-text digests remain explicitly unverified, and any recomputable mismatch invalidates the manifest. |
| MAN-011 | A missing candidate, missing support, unresolved logical identity, or false completeness assertion invalidates the manifest. |
| MAN-012 | Candidate/chunk maxima and the 64 MiB line bound have exact pass/fail boundary tests without allocating unbounded memory. |
| MAN-013 | Duplicate manifest entry, claim, support, chunk, or logical-card IDs fail in the correct namespace. |
| MAN-014 | Changed source/document/pipeline identity, candidate body/claim/support, lineage, scope, principal expectation, policy expectation, chunk state, or candidate set changes the manifest digest. |
| MAN-015 | Re-atomization of unchanged source is a new manifest/diff; exact frozen-manifest replay needs no LLM call and returns the same canonical digests. |
| MAN-016 | A support row crossing a chunk boundary or lying outside its referenced chunk fails; equivalent support split into one contained row per chunk passes. |
| MAN-017 | `(document_key, logical_card_id)` maps one-to-one to one source-object pair/logical target; duplicate aliases and either-direction mapping conflicts fail. |

## 9. Preview Matrix

| Test ID | Required proof |
| --- | --- |
| PRE-001 | Absent logical target returns the exact path-free canonical absent target-state object/digest and `create` effect without creating the target parent. |
| PRE-002 | Ready present, unreadable, missing-marker, invalid-marker, changed-digest, and changed-revision synthetic target states produce the exact deterministic action/conflict row. |
| PRE-003 | Preview returns the complete canonical structured visible effect, logical target/state bindings, card/claim/support detail, action, visible-effect policy/digest, and no path or durable-render claim. |
| PRE-004 | Ready candidate preview returns a non-authorizing token and explicit false flags for write, approval, grant, receipt, and checkpoint authority. |
| PRE-005 | Candidate conflict/rejection is visible and returns no token. |
| PRE-006 | Manifest preview returns aggregate counts and every candidate-level effect; summary-only detail is schema-invalid. |
| PRE-007 | Canonical candidate/manifest token bindings use every required always-present field and bind candidate/manifest digest, principal, scope, policy, ordered target states, visible-effect digest, expiry, session, and nonce. |
| PRE-008 | Changing any binding dimension changes the preview-binding digest and invalidates the prior token. |
| PRE-009 | Fixed clock/secret/nonce produces a deterministic token golden; production default remains session-ephemeral. |
| PRE-010 | Token expiry is enforced by the internal verifier fixture even though no Phase 1 public operation can consume it. |
| PRE-011 | Process restart invalidates all prior tokens and preserves no token state on disk. |
| PRE-012 | Manifest preview states that future candidate commits are independent and may produce a partial session. |
| PRE-013 | The ordered action table covers create/retain/update/split/merge/supersede/archive/alternate/no-change, exact replay, same-revision conflict, missing predecessor, and standalone split/merge denial. |
| PRE-014 | Manifest token binds manifest-entry-ordered candidate preview-binding digests; reorder/substitution/removal invalidates it. |
| PRE-015 | `mutation_expected` is true only for the reviewed mutating action rows and false for retain/no-change/conflict. |

## 10. Disabled-submit And Unsupported-operation Matrix

| Test ID | Required proof |
| --- | --- |
| DENY-001 | Exact schema-valid `submit_ingest` and `retrieve_candidate_grant` each return `permission_denied`, `result=null`, and no success-shape field. |
| DENY-002 | Denial occurs after strict envelope/version/operation-shape validation but before payload semantics, candidate canonicalization, target inspection, token lookup/generation, or state allocation. |
| DENY-003 | `submit`, `ingest`, `write`, `commit`, `approve`, `grant`, `retrieve_grant`, `get_receipt`, `query`, `retrieve`, case variants, whitespace variants, and Unicode lookalikes are unsupported/invalid and never dispatched. |
| DENY-004 | A forged scope/principal/policy-enable field is invalid and cannot alter later capabilities. |
| DENY-005 | Repeating or concurrently sending either blocked operation creates no shared state and remains deterministic. |
| DENY-006 | Exact submit payload schema contains candidate/digest/preview/authorization/grant/idempotency fields; exact retrieval schema/result contains only reviewed references/token metadata, with additional/omitted fields rejected. |
| DENY-007 | Future-retrieval invariants are frozen in fixtures: same principal obtains the same unconsumed token/digest/expiry; cross-principal/scope, mismatch, expiry, consumed, or post-`INTENT` cases fail closed. Phase 1 executes only the denial branch. |

## 11. Exact No-write Proof

### 11.1 Canary roots

Each no-write test creates isolated synthetic roots for:

```text
runtime-root/
core-source-tree/
contract-source-tree/
installed-worker-contract-root/
imported-target-root/
project-memory-root/
agent-memory-root/
topic-base-root/
provider-state-canary/
disposable-home/
disposable-tmp/
disposable-cache/
```

Each existing root contains mode-sensitive sentinel files and nested content.
A separate expected target parent is deliberately absent.

The test snapshots, before and after:

- relative path set and file type;
- SHA-256 content digest;
- size and permission mode;
- symlink target where applicable; and
- directory existence.

Mtime is diagnostic only; acceptance requires identical content/path/mode and
no new entry. The Core source snapshot excludes `.git` but includes every
worker/contract source path. Tests must not normalize or rewrite a fixture
merely to compare it.

The parent harness creates all fixtures before the baseline snapshot and writes
evidence only after post-child equality is proven. The disposable home/temp/
cache roots are intentionally empty at baseline and are not writable
exceptions.

### 11.2 Primitive traps

The parent launches an absolute bootstrap path with `python3 -I -B -S`,
`PYTHONDONTWRITEBYTECODE=1`, and controlled `HOME`, `TMPDIR`, and
`XDG_CACHE_HOME`. A child mode inside `tests/test_ingestion_no_write.py`
installs traps before loading the worker through `runpy.run_path`; importing the
worker before traps is a test failure. The source and separately authorized
installed-runtime smokes use this same child path.

The pre-import worker test layer traps and fails on:

- `open`/`os.open` with write, append, create, truncate, or read-write flags;
- `Path.write_*`, `mkdir`, `makedirs`, `mkstemp`, and `NamedTemporaryFile`;
- `os.replace`, `rename`, `unlink`, `remove`, and `rmdir`;
- writable SQLite connections or any database creation;
- `fsync`/`fdatasync` in the preview worker;
- subprocess/network/provider calls; and
- imports of known SDK/importer/provider/MLX modules.

Read-only opening of the configured synthetic target and canonical contract
files is allowed. Bytecode, temp, cache, history, telemetry, or startup log
creation is not. The traps do not govern the parent runner's controlled setup/
teardown, but all parent setup precedes the snapshot and all evidence writing
follows the equality comparison.

### 11.3 No-write cases

| Test ID | Required proof |
| --- | --- |
| NW-001 | `capabilities` and `health` preserve all snapshots, including when a configured target parent is absent. |
| NW-002 | Valid/invalid candidate and manifest validation preserve all snapshots. |
| NW-003 | Every candidate/manifest preview action and conflict preserves all snapshots. |
| NW-004 | Every framing/version/error/timeout path preserves all snapshots. |
| NW-005 | Exact and alias submit-denial cases preserve all snapshots. |
| NW-006 | EOF, SIGTERM at a safe process boundary, close, restart, and malformed final line preserve all snapshots. |
| NW-007 | Parallel read-only preview requests cannot create a lock, journal, cache, token file, or race-dependent output. |
| NW-008 | Core operation/lifecycle logs and Engine/FTS/vector databases remain byte-identical. |
| NW-009 | SDK/importer/provider canary roots remain byte-identical and no module is imported from them. |
| NW-010 | A content canary present only in a rejected request appears in no response error, stderr, log, file, or residual process artifact. |
| NW-011 | Worker import/startup under the pre-import guard leaves Core source, contract, runtime, disposable home/temp/cache, and every canary snapshot identical and creates no `__pycache__`/`.pyc`. |
| NW-012 | The same guarded child and snapshot implementation targets a separately authorized installed worker/contract path without importing the source worker first. |

## 12. Privacy And Static-boundary Tests

| Test ID | Required proof |
| --- | --- |
| PRI-001 | Source and tests contain no key/token/private-key pattern or real credential variable value. |
| PRI-002 | Responses and stderr contain no absolute path, home/user component, physical target, database/table/lock identifier, provider route, or personal identity canary. |
| PRI-003 | Capabilities expose logical scope/principal references and policy/build/contract-artifact identities only. |
| PRI-004 | Validation findings redact rejected path/secret/provider canaries rather than echoing them. |
| PRI-005 | Preview may return only the submitted candidate body and bounded support spans; it cannot return a complete source snapshot or unrelated target bytes. |
| PRI-006 | Worker imports no `eidetic_sdk`, importer, `shared_api_cache`, provider skill/client, `mlx`, `fastembed`, Engine embedding, or memory writer module; book/LLM/local-MLX atomization remains importer-owned. |
| PRI-007 | Core contains no SDK dependency; SDK has no Core checkout/submodule/path dependency and contains no copied canonical ingestion contract tree after the Phase 1 commit. |
| PRI-008 | No test reads `keys.env`, central provider cache, balances, usage ledgers, or `key_penalty.db`. |
| PRI-009 | Core contract artifact metadata contains protocol files/digests only, while generated SDK types identify their pinned artifact as derived code rather than canonical schema ownership. |

Static scans must use bounded allowlists and report file/line evidence without
printing any matched secret-like canary value.

### 12.1 Stable schema-omission evidence

| Test ID | Required proof |
| --- | --- |
| SCH-001 | Capabilities omission/extra-field fixtures cover every required protocol/build/artifact/policy/operation/authority field. |
| SCH-002 | Candidate preview omission fixtures independently remove target binding, action, mutation flag, visible card, claim, support locator, visible-effect digest, token digest, or authority flag and must fail schema validation. |
| SCH-003 | Manifest preview omission fixtures independently remove aggregate counts, one candidate effect, ordered candidate-binding digest, or partial-session statement and must fail. |
| SCH-004 | Error/finding omission and additional-detail fixtures enforce bounded code/message/retryability/pointer fields without raw-value/path leakage. |
| SCH-005 | Request discriminator fixtures prove six executable plus two blocked exact operation payload shapes, with no alias or cross-operation field acceptance. |

### 12.2 Stable Core configuration evidence

| Test ID | Required proof |
| --- | --- |
| CFG-001 | A sanitized Core-owned `imported-book-pilot` fixture resolves to a logical preview-only target root physically disjoint from every current writer/memory/topic-base root. |
| CFG-002 | Missing, relative, aliased, symlink-overlapping, parent/child-overlapping, or writer-resolvable target configuration disables the scope without creating a directory. |
| CFG-003 | Request principal/scope/policy/root fields cannot create, replace, enable, or mutate Core startup configuration or alter later capabilities. |

### 12.3 Stable packaging/deployment evidence

These IDs remain planned/not-run unless packaging or installed deployment is
separately authorized.

| Test ID | Required proof |
| --- | --- |
| DEP-001 | Fresh installed-root identity/backup inventory names exact pre-deployment worker/contracts and rollback source. |
| DEP-002 | Reviewed Core source, immutable contract artifact, and candidate installed contract bytes have exact path/byte/SHA-256 parity and one contract-manifest digest. |
| DEP-003 | Additive atomic installation touches only the new worker/contracts and preserves every unrelated installed path. |
| DEP-004 | Installed capabilities report the reviewed Core build/artifact identities, six executable/two blocked operations, and every write/approval/grant flag false. |
| DEP-005 | Installed public-protocol synthetic smokes and the NW-011/NW-012 guarded snapshots pass without a real source, provider, scope enablement, target creation, or source checkout import. |
| DEP-006 | Reviewed rollback restores the exact pre-deployment identities without deleting or rewriting protected memory/provider state. |

## 13. Regression Tests

| Test ID | Required proof |
| --- | --- |
| REG-001 | Full existing Core unittest discovery passes in the clean implementation worktree. |
| REG-002 | Existing Engine contract schemas and worker are unchanged by the scoped diff. |
| REG-003 | Existing Core, Engine, memory, hook, MCP, installer, and M3 tests have no regression attributable to Phase 1. |
| REG-004 | `git diff --name-only` from the reviewed specification commit is a subset of the implementation allowlist. |
| REG-005 | `git diff --cached --name-only` before each implementation commit contains no M3, hook, YouGile, SDK, importer, or preview-package owner change. |
| REG-006 | The primary dirty worktree remains unchanged while implementation occurs in the clean dedicated worktree. |

If an unrelated pre-existing M3 test fails on the canonical base, the evidence
must show the same failure on the reviewed base and keep it outside the Phase 1
commit. A broad regression failure cannot be silently waived merely because
targeted ingestion tests pass.

## 14. Requirements Traceability

| Requirement | Planned tests/evidence |
| --- | --- |
| P1-001 | CT-001 through CT-003, CT-011 |
| P1-002 | CT-004 through CT-006 |
| P1-003 | CT-002, CT-007, DENY-003 |
| P1-004 | DENY-001 through DENY-007, NW-005 |
| P1-005 | CT-002, COMP-005, PRE-004, SCH-001, DEP-004 |
| P1-006 | DENY-004, PRI-003 |
| P1-007 | CAN-001 through CAN-012 |
| P1-008 | CAND-009, PRI-002, PRI-004 |
| P1-009 | CAND-001, CAND-006 through CAND-008, CAND-015, CAN-001, CAN-011 |
| P1-010 | CAND-012, PRE-002 |
| P1-011 | MAN-001, MAN-010, MAN-014, MAN-017, CAN-002 |
| P1-012 | MAN-003 through MAN-013, MAN-016, MAN-017 |
| P1-013 | MAN-014, CAN-010, PRE-008 |
| P1-014 | NW-002, NW-011, pre-import primitive traps |
| P1-015 | PRE-001 through PRE-005, PRE-013, PRE-015, SCH-002 |
| P1-016 | PRE-006, PRE-012 |
| P1-017 | PRE-003, PRE-006, SCH-002, SCH-003 |
| P1-018 | PRE-012 |
| P1-019 | PRE-007 through PRE-011, PRE-014 |
| P1-020 | PRE-004, DENY-001 through DENY-007, NW-001 through NW-012 |
| P1-021 | CT-009, CAND-014, PRE-005, SCH-004 |
| P1-022 | PRI-001 through PRI-005, NW-010 |
| P1-023 | PRI-005, NW-008 through NW-010 |
| P1-024 | PRI-006, PRI-007, PRI-009 |
| P1-025 | CT-011, PRI-007, PRI-009, DEP-002 |
| P1-026 | COMP-001 through COMP-006 |
| P1-027 | PRE-001, PRI-003, CFG-001 through CFG-003 |
| P1-028 | PRI-002 through PRI-004 |
| P1-029 | NW-004, NW-006, NW-011, PRE-011 |
| P1-030 | DEP-001 through DEP-006 and Section 16 |
| P1-031 | REG-001 through REG-003 |
| P1-032 | REG-004 through REG-006 |

No requirement may be marked passed from a prose inspection alone when this
table names an executable fixture or snapshot proof.

## 15. Review-time Documentation Checks

Before the specification commit is offered for review:

```bash
git diff --check <phase-0-acceptance-commit>..HEAD
git diff --name-only <phase-0-acceptance-commit>..HEAD
rg -n '^\| P1-[0-9]{3} ' docs/eidetic-llm-wiki-ingestion/phase-1-requirements.md
```

The review must confirm:

- 32 unique requirements remain present;
- every requirement has planned evidence;
- contract and test file names agree between spec, requirements, and test plan;
- no exact provider/model or private route state appears;
- no physical target path is specified; and
- no Phase 1 implementation file exists in the specification commit.

## 16. Installed-runtime Verification Plan

Installed deployment is not part of Phase 1 source implementation GO. If a
later deployment GO is granted, it must require:

1. `DEP-001`: a fresh backup/identity inventory of the exact installed
   worker/contracts;
2. `DEP-002`: exact path/byte/SHA-256 parity among reviewed source, immutable
   contract artifact, and candidate installed contracts;
3. `DEP-003`: additive atomic installation of only the ingestion worker and
   contract files;
4. `DEP-004`: installed `capabilities` reporting the reviewed build and
   contract/artifact digest, six executable operations, both blocked
   operations, and `submit_available=false`;
5. `DEP-005`: installed public-protocol smokes using synthetic payloads only
   plus the pre-import guarded `NW-011`/`NW-012` no-write snapshot over
   installed runtime, memory, target, indexes, SDK/importer, disposable
   home/temp/cache, and provider-state canaries; and
6. `DEP-006`: rollback through the existing reviewed installer/backup route.

The installed smoke must not enable a scope, process a real source, call a
provider, create a target, or import the source worker before testing the
installed worker. Source-checkout tests are not installed-runtime proof,
artifact parity is not durable-ingestion proof, and no SDK release may pin an
artifact before `DEP-002` passes under separate packaging authorization.

## 17. Evidence Bundle

The implementation phase should create a private/local evidence bundle under
the ignored output tree containing:

```text
output/phase-1-ingestion-preview-evidence/
  commands.txt
  targeted-tests.txt
  full-regression.txt
  canonical-goldens.sha256
  contract-manifest.sha256
  no-write-snapshot.json
  privacy-scan.txt
  staged-allowlist.txt
  source-installed-parity.txt       # only if separately deployed
```

The post-implementation review records sanitized counts, hashes, commands, and
verdicts in tracked documentation. Raw candidate bodies, support spans, paths,
environment, and process logs remain private and are not committed.

## 18. Pre-implementation Review Exit

The review returns:

- `CLEAN` only when all required fixture classes, digest semantics, no-write
  proof, privacy boundary, compatibility policy, and allowlists are coherent;
- `REWORK` for any missing/ambiguous test, circular digest, unprovable visible
  effect, stateful preview, path leak, schema/runtime drift risk, or scope
  expansion; or
- `KILL` if exact no-write preview cannot be separated from durable write
  authority.

A CLEAN review authorizes only an owner decision. It does not authorize schema,
worker, test, installer, deployment, or provider execution without a separate
GO bound to the reviewed specification commit.
