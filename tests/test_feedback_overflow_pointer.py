"""EIDETIC_FEEDBACK_OVERFLOW — opt-in pointer line past the feedback budget.

  * default ("names", P3 never-invisible): every feedback card past the budget is listed by name, and the
    telemetry slugs cover every card;
  * "pointer": past the budget ONE line names the count and points to recall; telemetry slugs cover only the
    cards actually injected.

Direct-INSERT fixture store, fully deterministic.
"""

import os
import sqlite3
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context as ac  # noqa: E402
import index_impl  # noqa: E402

N_CARDS = 40


class FeedbackOverflowPointerTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="overflow-")
        self.db = os.path.join(self.tmp, "db", "index.db")
        os.makedirs(os.path.dirname(self.db))
        os.environ.pop("EIDETIC_FEEDBACK_OVERFLOW", None)
        conn = index_impl.init_db(self.db)
        for i in range(N_CARDS):
            name = f"feedback-rule-{i:02d}"
            conn.execute(
                "INSERT INTO memory_chunks "
                "(path, project, name, type, description, evidence, source, last_verified, status, "
                " section_heading, content, mtime) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (os.path.join(self.tmp, f"{name}.md"), "", name, "feedback", "x" * 120, "verified", "user",
                 time.strftime("%Y-%m-%d"), "current", name, f"body {i}", int(time.time())))
        conn.commit()
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        os.environ.pop("EIDETIC_FEEDBACK_OVERFLOW", None)

    def test_default_lists_every_name(self):
        text, _, total, slugs = ac.fetch_feedback(self.conn, 1500)
        self.assertEqual(total, N_CARDS)
        self.assertIn("kept name-only by invariant", text)
        for i in range(N_CARDS):
            self.assertIn(f"feedback-rule-{i:02d}", text)
        self.assertEqual(len(slugs), N_CARDS)
        self.assertNotIn("more feedback cards", text)

    def test_pointer_replaces_the_name_list(self):
        os.environ["EIDETIC_FEEDBACK_OVERFLOW"] = "pointer"
        text, used, total, slugs = ac.fetch_feedback(self.conn, 1500)
        self.assertEqual(total, N_CARDS)
        self.assertNotIn("kept name-only by invariant", text)
        listed = [i for i in range(N_CARDS) if f"feedback-rule-{i:02d}" in text]
        self.assertLess(len(listed), N_CARDS)
        self.assertIn(f"- {N_CARDS - len(listed)} more feedback cards are not listed here", text)
        self.assertIn("eidetic recall", text)
        self.assertEqual(len(slugs), len(listed))
        self.assertEqual(used, len(text))

    def test_unknown_value_falls_back_to_names(self):
        os.environ["EIDETIC_FEEDBACK_OVERFLOW"] = "bogus"
        self.assertEqual(ac._feedback_overflow_mode(), "names")


if __name__ == "__main__":
    unittest.main()
