"""STEP 1B turn 11 — Phase-A write-safety (audit F1).

F1a: a fresh install (EIDETIC_CONFIDENCE_EVENTS off) mutates NO user files.
F1b: contended appends are LOSSLESS (bounded retry, not busy-exit-drop).
unittest.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, BIN)

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
        # the lock file lives in the shared temp dir, never next to the card.
        self.assertEqual(sorted(os.listdir(self.tmp)), ["rule.md"])

    def test_high_contention_subprocess_lossless(self):
        # 50 REAL concurrent processes appending distinct-ts events to one card
        # (the auditor's NEW-1 repro). True mutual exclusion (persistent lock,
        # one inode) → all survive; the os.unlink-per-open race loses some.
        n = 50
        env = dict(os.environ, EIDETIC_CONFIDENCE_EVENTS="on")
        procs = []
        for i in range(n):
            ts = f"2026-07-01T00:00:{i:02d}"
            code = (
                f"import sys; sys.path.insert(0, {BIN!r}); import evidence; "
                f"sys.exit(0 if evidence.append_event({self.card!r}, 'observed', ts={ts!r}) else 3)"
            )
            procs.append(subprocess.Popen([sys.executable, "-c", code], env=env))
        rcs = [p.wait() for p in procs]
        self.assertTrue(all(rc == 0 for rc in rcs), f"a subprocess append returned non-zero: {rcs}")
        _m, body = index_impl.parse_frontmatter(open(self.card, encoding="utf-8").read())
        events = index_impl.parse_evidence_events(body)
        self.assertEqual(len(events), n, f"lost events under {n}-way contention: {len(events)}/{n}")
        self.assertEqual(len({e["ts"] for e in events}), n, "duplicate events")

    def test_killed_writer_lock_is_recoverable(self):
        # A writer holding the persistent lock, SIGKILLed → the kernel releases
        # the flock → the next append succeeds (no stale-lock deadlock).
        ready = self.card + ".ready"
        code = (
            f"import sys, fcntl, time; sys.path.insert(0, {BIN!r}); import evidence; "
            f"fd = open(evidence._lock_path_for({self.card!r}), 'a'); "
            "fcntl.flock(fd, fcntl.LOCK_EX); "
            f"open({ready!r}, 'w').close(); time.sleep(60)"
        )
        holder = subprocess.Popen([sys.executable, "-c", code])
        try:
            for _ in range(300):
                if os.path.exists(ready):
                    break
                time.sleep(0.01)
            self.assertTrue(os.path.exists(ready), "holder never acquired the lock")
            # the lock is held → an append cannot get it within the retry budget.
            self.assertFalse(evidence.observed(self.card), "append acquired a held lock")
            holder.send_signal(signal.SIGKILL)
            holder.wait()
            # flock auto-released on death → the next append recovers (loudly: a
            # False here would signal a stuck lock; it returns True).
            self.assertTrue(evidence.observed(self.card),
                            "lock not recoverable after a killed writer (stale-lock deadlock)")
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait()
            try:
                os.remove(ready)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
