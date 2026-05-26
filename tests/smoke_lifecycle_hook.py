#!/usr/bin/env python3
"""Smoke the lifecycle hook through the shell wrapper."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import lifecycle_signals  # noqa: E402


def payload(project: Path, target: Path, idx: int = 0):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": f"smoke-{idx}",
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": f"toolu_{idx}",
        "duration_ms": 1,
        "tool_input": {
            "file_path": str(target),
            "old_string": "SMOKE_OLD_SECRET",
            "new_string": "SMOKE_NEW_SECRET",
        },
        "tool_response": {
            "stdout": "SMOKE_STDOUT_SECRET",
            "stderr": "SMOKE_STDERR_SECRET",
        },
    }


def run_hook(memory: Path, payload_obj: dict, timeout: float):
    env = os.environ.copy()
    env["EIDETIC_MEMORY_SYSTEM"] = str(memory)
    start = time.monotonic()
    result = subprocess.run(
        ["bash", str(ROOT / "hooks" / "lifecycle-signals.sh")],
        input=json.dumps(payload_obj),
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    elapsed = time.monotonic() - start
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    if result.stdout or result.stderr:
        raise AssertionError("hook must stay silent")
    return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--assert-settings-timeout", type=int, default=2)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        memory = base / "memory-system"
        project = base / "project"
        project.mkdir()

        settings = {"hooks": {"PostToolUse": [], "PostToolUseFailure": []}}
        lifecycle_signals.ensure_lifecycle_hook(settings, str(memory))
        lifecycle_matchers = []
        for event_name in ("PostToolUse", "PostToolUseFailure"):
            for entry in settings["hooks"][event_name]:
                if any("lifecycle-signals" in hook.get("command", "") for hook in entry.get("hooks", [])):
                    lifecycle_matchers.append((event_name, entry["matcher"], entry["hooks"][0]))
        assert [(event, matcher) for event, matcher, _ in lifecycle_matchers] == [
            ("PostToolUse", "Write|Edit|MultiEdit"),
            ("PostToolUse", "Bash"),
            ("PostToolUseFailure", "Bash|Write|Edit|MultiEdit"),
        ], lifecycle_matchers
        for _, _, hook in lifecycle_matchers:
            assert hook["timeout"] == args.assert_settings_timeout, hook

        targets = []
        for i in range(args.concurrency):
            target = project / f"file_{i}.py"
            target.write_text("old\n", encoding="utf-8")
            targets.append(target)

        with ThreadPoolExecutor(max_workers=min(args.concurrency, 16)) as pool:
            elapsed = list(pool.map(
                lambda item: run_hook(memory, payload(project, item[1], item[0]), args.timeout + 1.0),
                enumerate(targets),
            ))

        if max(elapsed) > args.timeout:
            raise AssertionError(f"hook exceeded timeout budget: max={max(elapsed):.3f}s")

        event_dir = memory / "events" / "lifecycle"
        event_files = list(event_dir.glob("*.jsonl"))
        if not event_files:
            raise AssertionError("no lifecycle JSONL written")
        lines = []
        for event_file in event_files:
            lines.extend(event_file.read_text(encoding="utf-8").splitlines())
        if len(lines) < args.concurrency:
            raise AssertionError(f"expected {args.concurrency} events, got {len(lines)}")
        records = [json.loads(line) for line in lines]
        if any(record.get("operation") != "edit" for record in records):
            raise AssertionError(f"unexpected lifecycle records: {records}")
        raw = "\n".join(lines)
        for forbidden in ("SMOKE_OLD_SECRET", "SMOKE_NEW_SECRET", "SMOKE_STDOUT_SECRET", "SMOKE_STDERR_SECRET"):
            if forbidden in raw:
                raise AssertionError(f"raw sentinel leaked: {forbidden}")

    print("lifecycle hook smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
