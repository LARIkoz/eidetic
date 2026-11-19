"""assemble_context: each card appears once in the Project / Recent blocks.

2026-11-19: the live context listed one long card once per section (8 of 8
Recent entries were the same card, 5 Project entries another), crowding out
every other card, and often showed the card's `## History` instead of its text.
unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context as ac  # noqa: E402
import index_impl  # noqa: E402

PROJECT = "tmp-eidetic-one"
CWD = "/tmp/eidetic/one"  # detect_project_slug -> 'tmp-eidetic-one'
MEM = "/tmp/eidetic-one/memory"


class OneEntryPerCardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-one-")
        self.conn = index_impl.init_db(os.path.join(self.tmp, "index.db"))
        now = int(time.time())
        rows = []
        # A long card: intro + 3 sections + History, newest of all.
        for heading, body in [("Long Card", "LEAD TEXT of the long card"),
                              ("Part A", "section a"), ("Part B", "section b"),
                              ("Part C", "section c"),
                              ("History", "- 2026-11-11: Worked: stray line")]:
            rows.append((f"{MEM}/long.md", "long", heading, body, now))
        for i in range(3):
            rows.append((f"{MEM}/small-{i}.md", f"small-{i}", f"small-{i}",
                         f"small body {i}", now - 60 * (i + 1)))
        for path, name, heading, body, mtime in rows:
            self.conn.execute(
                "INSERT INTO memory_chunks (path, project, name, type, evidence, source, last_verified,"
                " card_kind, status, section_heading, content, description, mtime)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (path, PROJECT, name, "project", "observed", "user-explicit", "", "finding",
                 "current", heading, body, name, mtime))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check(self, text):
        self.assertEqual(text.count(f"{MEM}/long.md"), 1, text)
        self.assertIn("LEAD TEXT of the long card", text)
        self.assertNotIn("stray line", text)
        for i in range(3):
            self.assertIn(f"small-{i}.md", text)

    def test_project_block(self):
        text, _used, _inc = ac.fetch_project(self.conn, CWD, 10_000_000, {})
        self._check(text)

    def test_recent_block(self):
        text, _used, _inc = ac.fetch_recent(self.conn, 10_000_000, None, {})
        self._check(text)


if __name__ == "__main__":
    unittest.main()
