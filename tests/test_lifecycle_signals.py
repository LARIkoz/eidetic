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
                Path(payload["tool_input"]["file_path"]).write_text("old\n", encoding="utf-8")
                self.assertTrue(lifecycle_signals.write_event(payload, self.memory))
        events = self.read_events()
        self.assertEqual(sorted(e["operation"] for e in events), ["edit", "multi_edit", "write"])

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

        self.assertTrue(any(results))
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
                ]
            }
        }
        lifecycle_signals.ensure_lifecycle_hook(settings, str(self.memory))
        lifecycle_signals.ensure_lifecycle_hook(settings, str(self.memory))
        post_tool = settings["hooks"]["PostToolUse"]
        lifecycle_entries = [
            entry for entry in post_tool
            if any("lifecycle-signals" in hook.get("command", "") for hook in entry.get("hooks", []))
        ]
        self.assertEqual(len(lifecycle_entries), 1)
        self.assertEqual(lifecycle_entries[0]["matcher"], "Write|Edit|MultiEdit")
        hook = lifecycle_entries[0]["hooks"][0]
        self.assertEqual(hook["timeout"], 2)
        self.assertIn("EIDETIC_MEMORY_SYSTEM=", hook["command"])
        self.assertTrue(any("auto-format" in str(entry) for entry in post_tool))

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
