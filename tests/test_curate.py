#!/usr/bin/env python3
"""curate.py — READ-ONLY lifecycle candidate finders.

Contract: promote-candidates surfaces pulled-but-not-yet-feedback cards (and
NEVER a protected feedback/user card); archive-candidates surfaces age_stale +
never-pulled + not-protected cards. Neither mutates anything.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import curate  # noqa: E402
import drift_check  # noqa: E402
import usage  # noqa: E402


def _res(path, section="", conf="high"):
    return {"path": path, "section": section, "confidence": conf}


def _make_index(db_path, cards):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE memory_chunks "
                 "(path TEXT, section_heading TEXT, name TEXT, card_kind TEXT, type TEXT)")
    conn.executemany("INSERT INTO memory_chunks VALUES (?,?,?,?,?)", cards)
    conn.commit()
    conn.close()


class PromoteCandidates(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = os.path.join(self.d, "index.db")
        _make_index(self.db, [
            ("hot.md", "", "Hot Rule", "finding", "project"),    # pulled by 2 -> candidate
            ("fb.md", "", "Already FB", "rule", "feedback"),     # pulled by 3 -> PROTECTED
            ("once.md", "", "Once", "finding", "project"),       # pulled by 1 -> below min
        ])
        usage.log_surfaced([_res("hot.md")], "q1", self.db, "high")
        usage.log_surfaced([_res("hot.md")], "q2", self.db, "high")
        usage.log_surfaced([_res("fb.md")], "q1", self.db, "high")
        usage.log_surfaced([_res("fb.md")], "q2", self.db, "high")
        usage.log_surfaced([_res("fb.md")], "q3", self.db, "high")
        usage.log_surfaced([_res("once.md")], "q1", self.db, "high")

    def test_only_pulled_nonfeedback_above_min(self):
        rows = curate.promote_candidates(self.db, min_hits=2)
        paths = [r["path"] for r in rows]
        self.assertEqual(paths, ["hot.md"])           # fb protected, once below min
        self.assertEqual(rows[0]["distinct_queries"], 2)

    def test_feedback_never_promoted_however_hot(self):
        rows = curate.promote_candidates(self.db, min_hits=1)
        self.assertNotIn("fb.md", [r["path"] for r in rows])  # protected at any heat


class ArchiveCandidates(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.db = os.path.join(self.d, "index.db")
        _make_index(self.db, [
            ("stale.md", "", "Stale", "finding", "project"),     # stale, never pulled -> candidate
            ("stalefb.md", "", "StaleFB", "rule", "feedback"),   # stale but PROTECTED
            ("stalehot.md", "", "StaleHot", "finding", "project"),  # stale but still pulled
        ])
        drift_path = os.path.join(self.d, "drift_state.db")
        conn = drift_check.init_drift_db(drift_path)
        drift_check.write_findings(conn, [
            ("stale.md", "project", "age_stale", "threshold=30d"),
            ("stalefb.md", "feedback", "age_stale", "threshold=30d"),
            ("stalehot.md", "project", "age_stale", "threshold=30d"),
        ])
        conn.close()
        usage.log_surfaced([_res("stalehot.md")], "q1", self.db, "high")  # keeps it alive

    def test_stale_unpulled_unprotected_only(self):
        rows = curate.archive_candidates(self.db)
        paths = [r["path"] for r in rows]
        self.assertEqual(paths, ["stale.md"])         # fb protected, hot still pulled

    def test_no_drift_db_is_empty_not_crash(self):
        empty = tempfile.mkdtemp()
        db2 = os.path.join(empty, "index.db")
        _make_index(db2, [("a.md", "", "A", "finding", "project")])
        self.assertEqual(curate.archive_candidates(db2), [])  # no drift_state.db -> []


class ArchiveWriter(unittest.TestCase):
    """_set_status_archived — reversible frontmatter flag; never corrupts body."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _w(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_replace_existing_status(self):
        p = self._w("a.md", "---\nname: A\ntype: project\nstatus: current\n---\n\nBody here.\n")
        self.assertEqual(curate._set_status_archived(p), "archived")
        txt = open(p, encoding="utf-8").read()
        self.assertIn("status: archived", txt)
        self.assertNotIn("status: current", txt)
        self.assertIn("Body here.", txt)               # body untouched

    def test_insert_when_no_status(self):
        p = self._w("b.md", "---\nname: B\ntype: project\n---\n\nBody.\n")
        self.assertEqual(curate._set_status_archived(p), "archived")
        self.assertIn("status: archived", open(p, encoding="utf-8").read())

    def test_idempotent_already_archived(self):
        p = self._w("c.md", "---\nname: C\nstatus: archived\n---\n\nB.\n")
        self.assertEqual(curate._set_status_archived(p), "already")

    def test_state_key_variant(self):
        p = self._w("d.md", "---\nname: D\nstate: current\n---\n\nB.\n")
        self.assertEqual(curate._set_status_archived(p), "archived")
        self.assertIn("state: archived", open(p, encoding="utf-8").read())

    def test_no_frontmatter_skipped(self):
        p = self._w("e.md", "# Just a heading\n\nNo frontmatter.\n")
        self.assertEqual(curate._set_status_archived(p), "skip")
        self.assertNotIn("archived", open(p, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
