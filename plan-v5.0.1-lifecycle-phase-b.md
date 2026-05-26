# v5.0.1 Lifecycle Phase B Plan

Status: Approved by final qreview (`SHIP`, audit `OK`, mechanical `PASS`) and implemented.
Date: 2026-05-26.

## Goal

Extend v4.3 Lifecycle Signals from file-edit facts to bounded command/failure
facts so Eidetic can distinguish "the agent ran commands" and "a tool failed"
without storing command text, stdout, stderr, tool output, transcript text, file
content, diffs, raw paths, raw cwd, or raw filenames.

Phase B is still raw event capture only. It must not change search ranking,
compounding, memory-context injection, or task-planner behavior.

## Current Hook Contract

Checked against the current Claude Code hooks reference on 2026-05-26:

- `PostToolUse` fires after successful tool execution and receives `tool_input`
  plus `tool_response`; schemas depend on the tool:
  https://code.claude.com/docs/en/hooks#posttooluse-input
- `PostToolUseFailure` fires when a tool execution fails, matches on tool name,
  and receives `tool_name`, `tool_input`, `tool_use_id`, plus top-level error
  metadata such as `error` and `is_interrupt`:
  https://code.claude.com/docs/en/hooks#posttoolusefailure-input
- Tool-event matchers include `PostToolUseFailure` and filter by `tool_name`:
  https://code.claude.com/docs/en/hooks#matcher-patterns
- Bash tool output shape includes `stdout`, `stderr`, `interrupted`, and
  `isImage`; Phase B must not read or persist these output fields:
  https://code.claude.com/docs/en/hooks#posttooluse-decision-control
- Hook `timeout` is in seconds:
  https://code.claude.com/docs/en/hooks#run-hooks-in-the-background

## Scope

Add two new metadata-only signal classes:

1. Successful Bash metadata from `PostToolUse` with matcher `Bash`.
2. Tool failure metadata from `PostToolUseFailure` with matcher `Bash|Write|Edit|MultiEdit`.

The failure matcher starts narrow. Add more tools only after evidence shows the
noise is useful and privacy-safe.

## Non-Goals

- No raw command capture.
- No command token capture.
- No stdout/stderr/tool_response capture.
- No error string capture.
- No `tool_input.description` reads, capture, hashes, lengths, or derived
  values.
- No transcript reads.
- No file content/diff reads.
- No ranking, search, MCP, or compounding behavior changes.
- No task creation or planner integration.
- No automatic remediation advice to Claude.

## Event Schema

Continue writing append-only JSONL under:

`events/lifecycle/YYYY-MM-DD.jsonl`

Use the existing HMAC key and `O_APPEND` bounded-write path. Keep the 512-byte
cap unless qreview finds that the schema needs a different bound.

Existing Phase A file-edit events keep `schema_version: 1`. New Phase B Bash
success and tool-failure events use `schema_version: 2`.

The HMAC key must remain cryptographically generated with secure entropy and
stored with mode `0o600`.

Shared fields:

- `schema_version`: `2` for Phase B event types.
- `recorded_at`: UTC ISO timestamp.
- `hook_event_name`: `PostToolUse` or `PostToolUseFailure`.
- `session_id`: bounded opaque ID, dropped if the event would exceed the cap.
- `tool_name`: tool enum.
- `tool_use_id`: bounded opaque ID, dropped if the event would exceed the cap.
- `duration_ms`: numeric only when provided, dropped if the event would exceed the cap.
- `project_slug`: `project_<hmac8>` from resolved cwd when cwd is available and safe.
- `cwd_hash`: HMAC only, omitted when cwd is unavailable and dropped if the event would exceed the cap.
- `operation`: enum.

Before writing any Bash success or tool-failure event, resolve the payload cwd
when provided. If that resolved cwd is a configured vault root/projection,
`shared_api_cache`, `.ssh`, `.aws`, `.git`, memory-system `db/`, or otherwise
matches the existing sensitive path logic, drop the event. If
`PostToolUseFailure` does not provide cwd, omit `project_slug` and `cwd_hash`;
do not infer cwd from `tool_input`, command text, paths, descriptions, or error
text.

Bash success fields:

- `operation`: `bash`.
- `command_class`: enum derived from command string without persisting tokens.
- `background`: boolean from `tool_input.run_in_background`.
- `timeout_ms_bucket`: enum from `tool_input.timeout`, not exact value.

Failure fields:

- `operation`: `tool_failure`.
- `failed_operation`: one of `bash`, `write`, `edit`, `multi_edit`, `unknown`.
- `failure_class`: enum derived from top-level fields only.
- `interrupted`: boolean from `is_interrupt` only.
- `command_class`: same enum as Bash success when `failed_operation == bash`
  and a valid non-empty command string is present; otherwise omitted.

Example `PostToolUseFailure` event when cwd is unavailable:

```json
{"schema_version":2,"hook_event_name":"PostToolUseFailure","tool_name":"Edit","operation":"tool_failure","failed_operation":"edit","failure_class":"permission_denied","interrupted":false}
```

Allowed `command_class` values:

- `test`: common test runners and test-like command invocations.
- `lint`: lint/format/typecheck/static-analysis commands.
- `git`: git command family.
- `package`: package-manager install/update/build scripts.
- `build`: build/compile/bundle commands.
- `network`: curl/wget/HTTP-like fetches.
- `shell`: valid non-empty command string whose safe leading form is a shell
  interpreter, shell builtin, or shell-control invocation and does not match a
  more specific class.
- `unknown`: valid non-empty command string whose safe leading form cannot be
  classified without parsing or extracting potentially sensitive tokens.

Allowed `timeout_ms_bucket` values:

- `none`
- `lt_10s`
- `10s_60s`
- `1m_5m`
- `gt_5m`

Allowed `failure_class` values:

- `interrupted`
- `nonzero_exit`
- `timeout`
- `permission_denied`
- `tool_error`
- `unknown`

## Classification Rules

Command classification may inspect `tool_input.command` only in memory and only
to emit an enum. It must never read, write, hash, measure, tokenize, or derive
from `tool_input.description`. It must never write the command, command hash,
command length, arguments, basename, flags, cwd-relative path fragments, or
extracted tokens.

Suggested implementation:

- Lowercase the command string in memory.
- Classify using conservative regex/word-boundary checks.
- Prefer `test` / `lint` / `git` / `build` / `package` over generic `shell`.
- For Bash success, no-op on missing, non-string, or empty command.
- Return `shell` for valid non-empty shell interpreter/builtin/control
  invocations that do not match a more specific class.
- Return `unknown` only for valid non-empty command strings whose safe leading
  form cannot be classified without deeper parsing or token extraction.

Failure classification may inspect top-level `error` only in memory and only to
emit an enum. It must ignore `tool_input.description`. It must never write the
error string, hash, length, or extracted tokens.

Suggested implementation:

- `is_interrupt == true` => `interrupted`.
- error contains timeout/timed out => `timeout`.
- error contains permission/denied/not allowed => `permission_denied`.
- error contains non-zero/status/exit code => `nonzero_exit`.
- otherwise `tool_error` when an error string exists, else `unknown`.

## Settings Registration

Keep the existing dedicated `PostToolUse` entry for file edits. Do not merge
Bash into the same matcher.

Add/update dedicated entries:

- `PostToolUse` matcher `Bash`, command `~/.claude/hooks/lifecycle-signals.sh`,
  timeout `2`.
- `PostToolUseFailure` matcher `Bash|Write|Edit|MultiEdit`, same command,
  timeout `2`.

Preserve unrelated hook entries. Registration must remain idempotent and must
remove stale older `lifecycle-signals` hook commands from both `PostToolUse`
and `PostToolUseFailure` hook arrays before appending exactly one entry per
intended event/matcher.

## Tests

Add unit coverage for:

- Bash success stores only class/buckets and not command/stdout/stderr/error.
- Bash success no-ops for missing/non-string command.
- Bash success classification examples for test/lint/git/build/package/network/shell/unknown.
- Bash success classifier negative cases prove regexes do not match keywords
  inside arguments, quoted strings, URLs, filenames, or descriptions.
- Bash success `timeout_ms_bucket` boundary cases.
- Bash success `background` extraction from `tool_input.run_in_background`.
- Bash success and failure ignore `tool_input.description` sentinels.
- Bash success and tool failures are dropped under sensitive resolved cwd roots.
- `PostToolUseFailure` without cwd omits `project_slug`/`cwd_hash` rather than
  reading raw fields to infer them.
- Failure event stores failure enum and not raw `error`, `tool_response`, stdout, stderr, command, path, filename, or cwd.
- Bash failure includes `command_class` only when a valid non-empty command
  string is present.
- Failure event for file tools does not read `file_path` and does not store path hash.
- `is_interrupt=true` maps to `interrupted`.
- `PostToolUseFailure` missing `tool_name` no-ops without writing an event.
- `PostToolUseFailure` missing or non-dict `tool_input` is handled safely.
- Event byte cap still drops optional fields in order and drops oversize events.
- Settings registration creates exactly:
  - `PostToolUse` `Write|Edit|MultiEdit`
  - `PostToolUse` `Bash`
  - `PostToolUseFailure` `Bash|Write|Edit|MultiEdit`
- Existing formatter/test hooks are preserved.
- Unrelated `PostToolUseFailure` hooks are preserved while stale lifecycle
  failure hooks are removed.
- Wrapper stays silent/no-op on malformed payloads.

Add real redacted fixture-shaped JSON for:

- `PostToolUse` Bash success with redacted sentinel command/output/description.
- `PostToolUseFailure` Bash failure with redacted sentinel error/output/description.
- `PostToolUseFailure` Edit failure with redacted sentinel path/content/description.

Before merge, validate or fixture-lock the real `PostToolUseFailure` payload
shape for cwd/session fields. If real hook capture is not available in the
local harness, document the fallback behavior in the implementation notes and
tests.

CI should run the existing lifecycle unittest discovery and hook smoke.

## Docs And Versioning

If implemented:

- Bump README badge and MCP server version to `5.0.1`.
- Update README Key capabilities and changelog.
- Update TODO and llms.txt.
- Update `CLAUDE.md` only if command examples or invariants change.
- Update canonical run `todo.md`, `gate-log.md`, and `state.md` after publish/install.

## Review Gate

Run `/qreview` on this plan before implementation. Treat any privacy/security
finding about raw command/output/error persistence as blocking.

Implementation should get its own `/qreview` before publish. Full consreview is
required if qreview finds unresolved privacy risk, schema ambiguity, or a hook
contract mismatch.

Final plan qreview artifacts:

- `output/qreview-v5.0.1-lifecycle-phase-b-plan-final-20260526-203138/SYNTHESIS.md`
- `output/qreview-v5.0.1-lifecycle-phase-b-plan-final-20260526-203138/AUDIT_VERDICT.md`
- `output/qreview-v5.0.1-lifecycle-phase-b-plan-final-20260526-203138/MECHANICAL_AUDIT.md`
