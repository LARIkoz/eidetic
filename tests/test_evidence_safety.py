"""STEP 1B turn 11 — Phase-A write-safety (audit F1).

F1a: a fresh install (EIDETIC_CONFIDENCE_EVENTS off) mutates NO user files.
F1b: contended appends are LOSSLESS (bounded retry, not busy-exit-drop).
unittest.
"""

import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import evidence  # noqa: E402
import index_impl  # noqa: E402

CARD = """---
name: rule
description: a managed rule about zorptangle rebuild
metadata:
  type: project
  source: agent-extracted
---

The zorptangle indexer rebuilds incrementally.
"""


class DefaultOffNoMutationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ev-safe-")
        self.card = os.path.join(self.tmp, "rule.md")
        with open(self.card, "w", encoding="utf-8") as f:
            f.write(CARD)
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)  # default OFF

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_events_default_off_writes_nothing(self):
        before = open(self.card, encoding="utf-8").read()
        self.assertFalse(evidence.events_enabled())
        for emit in (evidence.observed, evidence.confirmed, evidence.corrected,
                     evidence.verified_by_test, evidence.decayed):
            self.assertFalse(emit(self.card))
        after = open(self.card, encoding="utf-8").read()
        self.assertEqual(before, after, "a default install must not mutate user memory files")
        self.assertNotIn("## Evidence", after)
        # and no stray lock/tmp litter next to the card.
        self.assertEqual(os.listdir(self.tmp), ["rule.md"])

    def test_activation_flag_enables_writes(self):
        os.environ["EIDETIC_CONFIDENCE_EVENTS"] = "on"
        self.assertTrue(evidence.events_enabled())
        self.assertTrue(evidence.observed(self.card))
        self.assertIn("## Evidence", open(self.card, encoding="utf-8").read())


class LosslessConcurrentAppendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ev-lossless-")
        self.card = os.path.join(self.tmp, "rule.md")
        with open(self.card, "w", encoding="utf-8") as f:
            f.write(CARD)
        os.environ["EIDETIC_CONFIDENCE_EVENTS"] = "on"

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_appends_are_lossless(self):
        # N writers each append a DISTINCT-ts `observed` line to the one card
        # simultaneously. With bounded retry under the lock, every event survives
        # (no loss, no dup, no corruption); the busy-exit-drop version loses some.
        n = 8
        results = [None] * n

        def worker(i):
            ts = f"2026-07-01T00:00:{i:02d}"
            results[i] = evidence.append_event(self.card, "observed", ts=ts, note=f"w{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(results), f"a writer was dropped under contention: {results}")
        _m, body = index_impl.parse_frontmatter(open(self.card, encoding="utf-8").read())
        events = index_impl.parse_evidence_events(body)
        self.assertEqual(len(events), n, "lost or duplicated events under contention")
        self.assertEqual(len({e["ts"] for e in events}), n, "duplicate events")
        # no leftover lock/tmp files.
        self.assertEqual(sorted(os.listdir(self.tmp)), ["rule.md"])


if __name__ == "__main__":
    unittest.main()
