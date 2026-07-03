"""KEEP #5 — candidate-selection determinism.

Every candidate-selection query with a LIMIT must carry a TOTAL ORDER BY, so the
LIMIT cutoff keeps the SAME rows regardless of insertion/scan order. The context
injectors ordered only by `mtime DESC`; when cards share an mtime (a batch
written together, or equal-mtime cards) the tie-break was arbitrary, so the same
corpus in a different insertion order injected a DIFFERENT subset of memories.

Property test: build the identical corpus twice in OPPOSITE insertion orders and
assert the selected set is identical. unittest so it runs under
`python3 -m unittest discover` + pytest.
"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context as ac  # noqa: E402
import index_impl  # noqa: E402

PROJECT = "tmp-eidetic-det"
CWD = "/tmp/eidetic/det"  # detect_project_slug -> 'tmp-eidetic-det'
MEM = "/tmp/eidetic-det/memory"
N = 60  # more than either injector's LIMIT (fetch_project 50, fetch_recent 30)
TIE_MTIME = 1_700_000_000  # identical for every card => the tie-break decides


def _build(tmpdir, order):
    """Create an index.db with N equal-mtime cards inserted in `order`."""
    db = os.path.join(tmpdir, "index.db")
    conn = index_impl.init_db(db)
    for i in order:
        conn.execute(
            "INSERT INTO memory_chunks (path, project, name, type, evidence, source,"
            " last_verified, card_kind, status, section_heading, content, description, mtime)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{MEM}/card-{i:02d}.md", PROJECT, f"card-{i:02d}", "project",
             "observed", "user-explicit", "", "finding", "current",
             f"card-{i:02d}", f"body of card {i:02d}", f"card {i:02d}", TIE_MTIME),
        )
    conn.commit()
    return conn


class SelectionDeterminismTest(unittest.TestCase):
    def setUp(self):
        self.ascending = _build(self.mkdir("asc"), range(N))
        self.descending = _build(self.mkdir("desc"), reversed(range(N)))

    def mkdir(self, name):
        import tempfile
        d = tempfile.mkdtemp(prefix=f"eidetic-det-{name}-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def tearDown(self):
        self.ascending.close()
        self.descending.close()

    def test_fetch_project_selection_is_insertion_order_independent(self):
        # Huge budget so the whole LIMIT-50 candidate set is reported.
        _t1, _u1, inc_asc = ac.fetch_project(self.ascending, CWD, 10_000_000, {})
        _t2, _u2, inc_desc = ac.fetch_project(self.descending, CWD, 10_000_000, {})
        self.assertEqual(len(inc_asc), 50, "LIMIT 50 should bind with 60 candidates")
        self.assertEqual(
            inc_asc, inc_desc,
            "fetch_project selected a different subset when insertion order changed",
        )

    def test_fetch_recent_selection_is_insertion_order_independent(self):
        # fetch_recent keeps cards from the last 14 days; give them a recent mtime.
        recent = int((datetime.now() - timedelta(days=1)).timestamp())
        for conn in (self.ascending, self.descending):
            conn.execute("UPDATE memory_chunks SET mtime = ?", (recent,))
            conn.commit()
        _t1, _u1, inc_asc = ac.fetch_recent(self.ascending, 10_000_000, drift_map={})
        _t2, _u2, inc_desc = ac.fetch_recent(self.descending, 10_000_000, drift_map={})
        self.assertEqual(len(inc_asc), 30, "LIMIT 30 should bind with 60 candidates")
        self.assertEqual(
            inc_asc, inc_desc,
            "fetch_recent selected a different subset when insertion order changed",
        )


if __name__ == "__main__":
    unittest.main()
