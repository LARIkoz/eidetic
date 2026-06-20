#!/usr/bin/env python3
"""Usage telemetry — read-side logging (which cards get surfaced) + reporting.

Contract: log_surfaced is APPEND-ONLY, FAIL-OPEN, PRIVACY-SAFE (never writes the
raw query), and only records cards in a medium+ result set. usage_stats aggregates
(rollup + live log), detects DEAD cards (indexed but never surfaced), and --rollup
compacts the log without losing counts.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import usage  # noqa: E402
import usage_stats  # noqa: E402


def _res(path, section="", conf="medium"):
    return {"path": path, "section": section, "confidence": conf}


def _make_index(db_path, cards):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE memory_chunks "
                 "(path TEXT, section_heading TEXT, name TEXT, card_kind TEXT)")
    conn.executemany("INSERT INTO memory_chunks VALUES (?,?,?,?)", cards)
    conn.commit()
    conn.close()


class LogSurfaced(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = os.path.join(self.d, "index.db")
        self.log = os.path.join(self.d, "usage.log")

    def _lines(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def test_logs_medium_plus_with_ranks(self):
        usage.log_surfaced([_res("a.md", "S1"), _res("b.md")], "q", self.db, "medium")
        lines = self._lines()
        self.assertEqual([(l["path"], l["rank"]) for l in lines], [("a.md", 1), ("b.md", 2)])

    def test_raw_query_is_never_written(self):
        usage.log_surfaced([_res("a.md")], "привет очень секретный текст", self.db, "high")
        with open(self.log, encoding="utf-8") as f:
            blob = f.read()
        self.assertNotIn("секрет", blob)        # raw query text must not leak
        self.assertIn("qh", self._lines()[0])   # only a short hash is stored

    def test_low_confidence_not_logged(self):
        usage.log_surfaced([_res("a.md", "", "low")], "q", self.db, "low")
        self.assertEqual(self._lines(), [])

    def test_empty_results_not_logged(self):
        usage.log_surfaced([], "q", self.db, "high")
        self.assertEqual(self._lines(), [])

    def test_opt_out(self):
        with mock.patch.dict(os.environ, {"EIDETIC_USAGE_LOG": "off"}):
            usage.log_surfaced([_res("a.md")], "q", self.db, "high")
        self.assertEqual(self._lines(), [])

    def test_caps_at_top_k(self):
        usage.log_surfaced([_res(f"{i}.md") for i in range(20)], "q", self.db, "high")
        self.assertEqual(len(self._lines()), usage.TOP_K_LOG)

    def test_fail_open_on_bad_results(self):
        try:
            usage.log_surfaced([None, 123, "x"], "q", self.db, "high")
        except Exception as e:  # noqa: BLE001
            self.fail(f"log_surfaced must be fail-open, raised: {e}")
        self.assertEqual(self._lines(), [])     # nothing partially written


class AggregateAndDead(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = os.path.join(self.d, "index.db")
        _make_index(self.db, [
            ("a.md", "S1", "Card A", "finding"),
            ("a.md", "S2", "Card A", "finding"),
            ("b.md", "", "Card B", "rule"),
            ("c.md", "", "Card C", "code"),
        ])
        usage.log_surfaced([_res("a.md", "S1"), _res("b.md")], "q1", self.db, "high")
        usage.log_surfaced([_res("a.md", "S1")], "q2", self.db, "high")

    def test_per_card_counts(self):
        log, rollup = usage_stats._paths(self.db)
        agg = usage_stats.aggregate(rollup, log)
        self.assertEqual(agg["a.md\x00S1"]["count"], 2)
        self.assertEqual(agg["b.md\x00"]["count"], 1)
        self.assertEqual(agg["a.md\x00S1"]["best_rank"], 1)

    def test_dead_and_coverage(self):
        c = usage_stats.compute(self.db)
        self.assertEqual(c["total_indexed"], 4)
        self.assertEqual(c["total_surfacings"], 3)     # a§S1 ×2 + b ×1
        self.assertEqual(c["distinct_surfaced"], 2)    # a§S1, b
        self.assertEqual(c["dead_count"], 2)           # a§S2, c never surfaced
        self.assertEqual(c["coverage_pct"], 50.0)


class Rollup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = os.path.join(self.d, "index.db")
        _make_index(self.db, [("a.md", "", "A", "finding"), ("b.md", "", "B", "rule")])
        usage.log_surfaced([_res("a.md"), _res("b.md")], "q1", self.db, "high")
        usage.log_surfaced([_res("a.md")], "q2", self.db, "high")

    def test_rollup_preserves_counts_and_resets_log(self):
        before = usage_stats.compute(self.db)["total_surfacings"]
        usage_stats.rollup(self.db)
        log, rollup = usage_stats._paths(self.db)
        self.assertTrue(os.path.exists(rollup))
        self.assertFalse(os.path.exists(log) and os.path.getsize(log) > 0)
        after = usage_stats.compute(self.db)["total_surfacings"]
        self.assertEqual(before, after)            # counts survive the compaction
        # a second rollup is idempotent on counts
        usage_stats.rollup(self.db)
        self.assertEqual(usage_stats.compute(self.db)["total_surfacings"], before)


if __name__ == "__main__":
    unittest.main()
