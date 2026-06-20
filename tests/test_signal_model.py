#!/usr/bin/env python3
"""Tests for bin/signal_model.py — the card-extraction model resolver shared by the
Stop hook and the doctor. Resolution: env (explicit id) > .signal_model file
(friendly name | full id) > sonnet default."""

import os
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import signal_model  # noqa: E402


class SignalModelResolveTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def _write(self, value):
        with open(os.path.join(self.root, ".signal_model"), "w", encoding="utf-8") as f:
            f.write(value)

    def test_default_when_no_env_no_file(self):
        self.assertEqual(signal_model.resolve(env={}, root=self.root), "claude-sonnet-4-6")

    def test_env_explicit_wins(self):
        env = {"EIDETIC_SIGNAL_CLAUDE_MODEL": "claude-opus-4-8"}
        self._write("haiku")  # env must override the file
        self.assertEqual(signal_model.resolve(env=env, root=self.root), "claude-opus-4-8")

    def test_file_haiku_maps_to_pinned_id(self):
        self._write("haiku\n")
        self.assertEqual(signal_model.resolve(env={}, root=self.root), "claude-haiku-4-5-20251001")

    def test_file_sonnet_maps_to_pinned_id(self):
        self._write("sonnet")
        self.assertEqual(signal_model.resolve(env={}, root=self.root), "claude-sonnet-4-6")

    def test_file_full_id_passthrough(self):
        self._write("claude-custom-9-9")
        self.assertEqual(signal_model.resolve(env={}, root=self.root), "claude-custom-9-9")

    def test_garbage_file_falls_back_to_default(self):
        self._write("banana")
        self.assertEqual(signal_model.resolve(env={}, root=self.root), "claude-sonnet-4-6")

    def test_friendly_name_is_case_insensitive(self):
        self._write("Haiku")  # a hand-edit with different case must still map
        self.assertEqual(signal_model.resolve(env={}, root=self.root), "claude-haiku-4-5-20251001")

    def test_never_returns_bare_alias(self):
        # the whole point of pinning: a friendly name must never resolve to 'sonnet'
        self._write("sonnet")
        self.assertNotEqual(signal_model.resolve(env={}, root=self.root), "sonnet")

    def test_describe_marks_source(self):
        self.assertIn("default", signal_model.describe(env={}, root=self.root))
        self._write("haiku")
        d = signal_model.describe(env={}, root=self.root)
        self.assertIn("haiku", d)
        self.assertIn(".signal_model", d)
        env = {"EIDETIC_SIGNAL_CLAUDE_MODEL": "claude-opus-4-8"}
        self.assertIn("env", signal_model.describe(env=env, root=self.root))


if __name__ == "__main__":
    unittest.main()
