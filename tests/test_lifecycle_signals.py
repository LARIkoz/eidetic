import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import lifecycle_signals  # noqa: E402


class LifecycleSignalsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory-system"
        self.project = self.base / "rawcwdsecret"
        self.project.mkdir()
        self.addCleanup(self.tmp.cleanup)
        os.environ.pop("EIDETIC_VAULT_ROOTS", None)
        os.environ.pop("EIDETIC_LIFECYCLE_FORCE_SMALL_CAP", None)
        os.environ.pop("EIDETIC_LIFECYCLE_MAX_EVENT_BYTES", None)

    def payload(self, tool_name="Edit", path=None, **extra):
        target = Path(path) if path else self.project / "secret_name.py"
        if path and not target.is_absolute() and ".." not in target.parts:
            target = self.project / target
        if target.is_absolute():
            target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_absolute() and not target.exists():
            target.write_text("old\n", encoding="utf-8")
        tool_input = {"file_path": str(target)}
        if tool_name == "Write":
            tool_input["content"] = "SENTINEL_SECRET"
        elif tool_name == "Edit":
            tool_input.update({"old_string": "SENTINEL_OLD", "new_string": "SENTINEL_NEW"})
        elif tool_name == "MultiEdit":
            tool_input["edits"] = [
                {"old_string": "SENTINEL_A", "new_string": "SENTINEL_B"},
                {"old_string": "SENTINEL_C", "new_string": "SENTINEL_D"},
            ]
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "cwd": str(self.project),
            "tool_name": tool_name,
            "tool_use_id": "toolu_1",
            "duration_ms": 12,
            "tool_input": tool_input,
            "tool_response": {
                "stdout": "SENTINEL_STDOUT",
                "stderr": "SENTINEL_STDERR",
                "content": "SENTINEL_RESPONSE",
            },
        }
        payload.update(extra)
        return payload

    def bash_payload(self, command="pytest tests SENTINEL_COMMAND_SECRET", **extra):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-bash",
            "cwd": str(self.project),
            "tool_name": "Bash",
            "tool_use_id": "toolu_bash",
            "duration_ms": 34,
            "tool_input": {
                "command": command,
                "description": "SENTINEL_DESCRIPTION",
                "timeout": 9999,
                "run_in_background": True,
            },
            "tool_response": {
                "stdout": "SENTINEL_STDOUT",
                "stderr": "SENTINEL_STDERR",
                "content": "SENTINEL_RESPONSE",
            },
        }
        payload.update(extra)
        return payload

    def failure_payload(self, tool_name="Bash", **extra):
        tool_input = {
            "command": "git status -- SENTINEL_COMMAND_SECRET",
            "description": "SENTINEL_DESCRIPTION",
            "file_path": str(self.project / "sentinel_secret_name.py"),
            "old_string": "SENTINEL_OLD",
            "new_string": "SENTINEL_NEW",
            "timeout": 60000,
        }
        payload = {
            "hook_event_name": "PostToolUseFailure",
            "session_id": "session-failure",
            "cwd": str(self.project),
            "tool_name": tool_name,
            "tool_use_id": "toolu_failure",
            "duration_ms": 56,
            "tool_input": tool_input,
            "error": "command timed out with SENTINEL_ERROR",
            "is_interrupt": False,
            "tool_response": {
                "stdout": "SENTINEL_STDOUT",
                "stderr": "SENTINEL_STDERR",
            },
        }
        if tool_name != "Bash":
            payload["tool_input"] = dict(tool_input)
            payload["tool_input"].pop("command", None)
        payload.update(extra)
        return payload

    def events_file(self):
        return self.memory / "events" / "lifecycle" / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"

    def read_events(self):
        path = self.events_file()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_write_event_stores_only_hashed_metadata(self):
        self.assertTrue(lifecycle_signals.write_event(self.payload("Write"), self.memory))
        events = self.read_events()
        self.assertEqual(len(events), 1)
        record = events[0]
        self.assertEqual(record["operation"], "write")
        self.assertEqual(record["target_ext"], ".py")
        self.assertTrue(record["project_slug"].startswith("project_"))

        raw = self.events_file().read_text(encoding="utf-8")
        for forbidden in [
            "SENTINEL_SECRET",
            "SENTINEL_STDOUT",
            "SENTINEL_STDERR",
            "SENTINEL_RESPONSE",
            "secret_name.py",
            self.project.name,
            str(self.project),
        ]:
            self.assertNotIn(forbidden, raw)

    def test_redacted_real_shaped_fixtures_parse(self):
        fixture_dir = ROOT / "tests" / "fixtures" / "lifecycle"
        for fixture in sorted(fixture_dir.glob("*_real_redacted.json")):
            with self.subTest(fixture=fixture.name):
                text = fixture.read_text(encoding="utf-8").replace("__PROJECT__", str(self.project))
                payload = json.loads(text)
                file_path = payload.get("tool_input", {}).get("file_path")
                if isinstance(file_path, str):
                    target = Path(file_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("old\n", encoding="utf-8")
                self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        events = self.read_events()
        self.assertEqual(
            sorted(e["operation"] for e in events),
            ["bash", "edit", "multi_edit", "tool_failure", "tool_failure", "write"],
        )
        raw = self.events_file().read_text(encoding="utf-8")
        for forbidden in [
            "SENTINEL_COMMAND",
            "SENTINEL_DESCRIPTION",
            "SENTINEL_ERROR",
            "SENTINEL_STDOUT",
            "SENTINEL_STDERR",
            "sentinel_secret_name.py",
        ]:
            self.assertNotIn(forbidden, raw)

    def test_multiedit_count_and_missing_duration(self):
        payload = self.payload("MultiEdit")
        payload.pop("duration_ms")
        self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        record = self.read_events()[0]
        self.assertEqual(record["operation"], "multi_edit")
        self.assertEqual(record["edit_count"], 2)
        self.assertIsNone(record["duration_ms"])

        malformed = self.payload("MultiEdit", path=self.project / "other.py")
        malformed["tool_input"]["edits"] = "bad"
        self.assertTrue(lifecycle_signals.write_event(malformed, self.memory))
        self.assertEqual(self.read_events()[1]["edit_count"], 1)

    def test_bash_success_stores_only_metadata(self):
        self.assertTrue(lifecycle_signals.write_event(self.bash_payload(), self.memory))
        record = self.read_events()[0]
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(record["operation"], "bash")
        self.assertEqual(record["command_class"], "test")
        self.assertTrue(record["background"])
        self.assertEqual(record["timeout_ms_bucket"], "lt_10s")
        self.assertTrue(record["project_slug"].startswith("project_"))

        raw = self.events_file().read_text(encoding="utf-8")
        for forbidden in [
            "SENTINEL_COMMAND",
            "SENTINEL_DESCRIPTION",
            "SENTINEL_STDOUT",
            "SENTINEL_STDERR",
            "SENTINEL_RESPONSE",
            self.project.name,
            str(self.project),
        ]:
            self.assertNotIn(forbidden, raw)

    def test_bash_success_noops_for_missing_or_non_string_command(self):
        for command in (None, "", "   ", 123, ["pytest"]):
            with self.subTest(command=command):
                payload = self.bash_payload(command=command)
                self.assertFalse(lifecycle_signals.write_event(payload, self.memory))
        self.assertFalse(self.events_file().exists())

    def test_command_classification_examples_and_anchoring(self):
        cases = {
            "pytest tests": "test",
            "python -m unittest discover": "test",
            "npm test -- --watch=false": "test",
            "ruff check .": "lint",
            "black --check .": "lint",
            "git status --short": "git",
            "make all": "build",
            "cargo build --release": "build",
            "npm install": "package",
            "python -m pip install pkg": "package",
            "curl https://example.invalid": "network",
            "wget https://example.invalid": "network",
            "bash scripts/run.sh": "shell",
            "echo pytest": "shell",
            "./run-pytest": "unknown",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(lifecycle_signals._command_class(command), expected)

        negative_cases = [
            ('echo "pytest tests"', "shell"),
            ('printf "curl https://example.invalid"', "shell"),
            ("cat git-status.txt", "unknown"),
            ("python -c 'import pytest'", "unknown"),
            ("open https://example.invalid/curl", "unknown"),
        ]
        for command, expected in negative_cases:
            with self.subTest(command=command):
                self.assertEqual(lifecycle_signals._command_class(command), expected)

    def test_timeout_bucket_boundaries_and_background(self):
        cases = [
            (None, "none"),
            (-1, "none"),
            (9999, "lt_10s"),
            (10000, "10s_60s"),
            (60000, "10s_60s"),
            (60001, "1m_5m"),
            (300000, "1m_5m"),
            (300001, "gt_5m"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(lifecycle_signals._timeout_ms_bucket(value), expected)

        payload = self.bash_payload(command="git status")
        payload["tool_input"]["run_in_background"] = "true"
        self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        self.assertFalse(self.read_events()[0]["background"])

    def test_failure_event_stores_only_metadata(self):
        self.assertTrue(lifecycle_signals.write_event(self.failure_payload(), self.memory))
        record = self.read_events()[0]
        self.assertEqual(record["schema_version"], 2)
        self.assertEqual(record["operation"], "tool_failure")
        self.assertEqual(record["failed_operation"], "bash")
        self.assertEqual(record["failure_class"], "timeout")
        self.assertFalse(record["interrupted"])
        self.assertEqual(record["command_class"], "git")

        raw = self.events_file().read_text(encoding="utf-8")
        for forbidden in [
            "SENTINEL_COMMAND",
            "SENTINEL_DESCRIPTION",
            "SENTINEL_ERROR",
            "SENTINEL_STDOUT",
            "SENTINEL_STDERR",
            "sentinel_secret_name.py",
            self.project.name,
            str(self.project),
        ]:
            self.assertNotIn(forbidden, raw)

    def test_file_tool_failure_omits_path_metadata(self):
        payload = self.failure_payload(
            "Edit",
            error="Permission denied while editing SENTINEL_ERROR",
            tool_input={
                "file_path": str(self.project / "sentinel_secret_name.py"),
                "old_string": "SENTINEL_OLD",
                "new_string": "SENTINEL_NEW",
                "description": "SENTINEL_DESCRIPTION",
            },
        )
        self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        record = self.read_events()[0]
        self.assertEqual(record["operation"], "tool_failure")
        self.assertEqual(record["failed_operation"], "edit")
        self.assertEqual(record["failure_class"], "permission_denied")
        self.assertNotIn("path_hash", record)
        self.assertNotIn("target_ext", record)
        self.assertNotIn("command_class", record)

        raw = self.events_file().read_text(encoding="utf-8")
        for forbidden in ["SENTINEL_OLD", "SENTINEL_NEW", "SENTINEL_DESCRIPTION", "sentinel_secret_name.py"]:
            self.assertNotIn(forbidden, raw)

    def test_failure_without_cwd_omits_project_identity(self):
        payload = self.failure_payload("Edit")
        payload.pop("cwd")
        self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        record = self.read_events()[0]
        self.assertEqual(record["failed_operation"], "edit")
        self.assertNotIn("project_slug", record)
        self.assertNotIn("cwd_hash", record)

    def test_failure_interrupt_and_malformed_failure_payloads(self):
        payload = self.failure_payload(is_interrupt=True, error="SENTINEL_ERROR")
        self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        record = self.read_events()[0]
        self.assertEqual(record["failure_class"], "interrupted")
        self.assertTrue(record["interrupted"])

        missing_tool = self.failure_payload()
        missing_tool.pop("tool_name")
        self.assertFalse(lifecycle_signals.write_event(missing_tool, self.memory))

        no_tool_input = self.failure_payload(tool_input="bad")
        self.assertTrue(lifecycle_signals.write_event(no_tool_input, self.memory))
        self.assertNotIn("command_class", self.read_events()[1])

    def test_sensitive_paths_are_dropped(self):
        paths = [
            ".env",
            ".env.local",
            "keys.env",
            ".git/config",
            ".ssh/id_rsa",
            ".ssh/id_ed25519",
            ".aws/credentials",
            ".npmrc",
            ".netrc",
            "cert.pem",
            "secret.key",
            "cert.p12",
            "cert.pfx",
            "state.sqlite",
            "state.sqlite3",
            "state.db",
            "shared_api_cache/foo.py",
        ]
        for rel in paths:
            with self.subTest(rel=rel):
                target = self.project / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x", encoding="utf-8")
                self.assertFalse(lifecycle_signals.write_event(self.payload(path=target), self.memory))
        self.assertFalse(self.events_file().exists())

        memory_db_target = self.memory / "db" / "foo.py"
        memory_db_target.parent.mkdir(parents=True, exist_ok=True)
        memory_db_target.write_text("x", encoding="utf-8")
        payload = self.payload(path=memory_db_target)
        payload["cwd"] = str(self.memory)
        self.assertFalse(lifecycle_signals.write_event(payload, self.memory))

    def test_sensitive_cwd_drops_bash_and_failure_events(self):
        sensitive_dirs = [
            self.project / ".ssh",
            self.project / ".aws",
            self.project / "shared_api_cache",
            self.memory / "db",
        ]
        for cwd in sensitive_dirs:
            with self.subTest(cwd=cwd):
                cwd.mkdir(parents=True, exist_ok=True)
                self.assertFalse(lifecycle_signals.write_event(self.bash_payload(cwd=str(cwd)), self.memory))
                self.assertFalse(lifecycle_signals.write_event(self.failure_payload(cwd=str(cwd)), self.memory))
        self.assertFalse(self.events_file().exists())

    def test_project_db_directory_is_not_globally_sensitive(self):
        target = self.project / "db" / "model.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        self.assertTrue(lifecycle_signals.write_event(self.payload(path=target), self.memory))

    @unittest.skipIf(not hasattr(os, "symlink"), "symlink not available")
    def test_symlink_to_sensitive_path_is_dropped(self):
        sensitive = self.project / ".ssh" / "id_rsa"
        sensitive.parent.mkdir(parents=True, exist_ok=True)
        sensitive.write_text("secret", encoding="utf-8")
        link = self.project / "link.py"
        os.symlink(sensitive, link)
        self.assertFalse(lifecycle_signals.write_event(self.payload(path=link), self.memory))
        self.assertFalse(self.events_file().exists())

    def test_vault_projection_is_dropped_by_ancestor_marker(self):
        vault = self.base / "my-vault"
        target = vault / "projects" / "foo.md"
        target.parent.mkdir(parents=True)
        target.write_text("note", encoding="utf-8")
        (vault / ".obsidian").mkdir()
        payload = self.payload(path=target)
        payload["cwd"] = str(vault)
        self.assertFalse(lifecycle_signals.write_event(payload, self.memory))

    def test_vault_projection_is_dropped_by_env_root(self):
        vault = self.base / "env-vault"
        target = vault / "notes" / "foo.md"
        target.parent.mkdir(parents=True)
        target.write_text("note", encoding="utf-8")
        os.environ["EIDETIC_VAULT_ROOTS"] = str(vault)
        self.addCleanup(os.environ.pop, "EIDETIC_VAULT_ROOTS", None)
        payload = self.payload(path=target)
        payload["cwd"] = str(vault)
        self.assertFalse(lifecycle_signals.write_event(payload, self.memory))

    def test_traversal_and_non_file_tools_are_dropped(self):
        payload = self.payload(path="../secret.txt")
        self.assertFalse(lifecycle_signals.write_event(payload, self.memory))
        self.assertFalse(lifecycle_signals.write_event(self.payload("Bash"), self.memory))

    def test_hmac_key_concurrent_first_use(self):
        targets = []
        for i in range(16):
            target = self.project / f"file_{i}.py"
            target.write_text("x", encoding="utf-8")
            targets.append(target)

        def run(target):
            return lifecycle_signals.write_event(self.payload(path=target), self.memory)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, targets))

        self.assertTrue(all(results))
        key_path = self.memory / "events" / "lifecycle" / ".hmac_key"
        self.assertTrue(key_path.exists())
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        self.assertEqual(len({event["cwd_hash"] for event in self.read_events()}), 1)

    def test_oversized_payload_is_dropped_when_cap_too_small(self):
        os.environ["EIDETIC_LIFECYCLE_MAX_EVENT_BYTES"] = "80"
        self.assertFalse(lifecycle_signals.write_event(self.payload(), self.memory))
        self.assertFalse(self.events_file().exists())

    def test_settings_registration_is_dedicated_and_seconds_based(self):
        settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "(?:Write|Edit)",
                        "hooks": [
                            {"type": "command", "command": "~/.claude/hooks/auto-format.sh"},
                            {"type": "command", "command": "~/.claude/hooks/lifecycle-signals.sh", "timeout": 2000},
                        ],
                    }
                ],
                "PostToolUseFailure": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "~/.claude/hooks/failure-observer.sh"},
                            {"type": "command", "command": "~/.claude/hooks/lifecycle-signals.sh", "timeout": 2000},
                        ],
                    }
                ]
            }
        }
        lifecycle_signals.ensure_lifecycle_hook(settings, str(self.memory))
        lifecycle_signals.ensure_lifecycle_hook(settings, str(self.memory))
        lifecycle_entries = []
        for event_name in ("PostToolUse", "PostToolUseFailure"):
            for entry in settings["hooks"][event_name]:
                if any("lifecycle-signals" in hook.get("command", "") for hook in entry.get("hooks", [])):
                    lifecycle_entries.append((event_name, entry))
        self.assertEqual(len(lifecycle_entries), 3)
        self.assertEqual(
            [(event_name, entry["matcher"]) for event_name, entry in lifecycle_entries],
            [
                ("PostToolUse", "Write|Edit|MultiEdit"),
                ("PostToolUse", "Bash"),
                ("PostToolUseFailure", "Bash|Write|Edit|MultiEdit"),
            ],
        )
        for _, entry in lifecycle_entries:
            hook = entry["hooks"][0]
            self.assertEqual(hook["timeout"], 2)
            self.assertIn("EIDETIC_MEMORY_SYSTEM=", hook["command"])
        self.assertTrue(any("auto-format" in str(entry) for entry in settings["hooks"]["PostToolUse"]))
        self.assertTrue(any("failure-observer" in str(entry) for entry in settings["hooks"]["PostToolUseFailure"]))

    def test_cleanup_lifecycle_events_removes_only_old_jsonl(self):
        lifecycle_dir = self.memory / "events" / "lifecycle"
        lifecycle_dir.mkdir(parents=True)
        old = lifecycle_dir / "2000-01-01.jsonl"
        new = lifecycle_dir / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"
        key = lifecycle_dir / ".hmac_key"
        db = self.memory / "db" / "index.db"
        db.parent.mkdir()
        for path in (old, new, key, db):
            path.write_text("x", encoding="utf-8")
        old_time = time.time() - 31 * 86400
        os.utime(old, (old_time, old_time))
        env = os.environ.copy()
        env["EIDETIC_MEMORY_SYSTEM"] = str(self.memory)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "cleanup.py"), "--lifecycle-events"],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("removed 1", result.stdout)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(key.exists())
        self.assertTrue(db.exists())


if __name__ == "__main__":
    unittest.main()
