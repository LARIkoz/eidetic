"""Truth-maintenance slice (v6 preview): declared contradictions + supersession.

A card declaring `contradicts:`/`supersedes:` must actually down-rank its
TARGET in real search output:
  - index-time propagation fills the target's `contradicted_by`/`superseded_by`
    columns (the target's file usually doesn't know it was contradicted);
  - drift_check surfaces a `contradicted` finding which penalizes 0.4x
    IMMEDIATELY (declared facts bypass the `first_seen > 1` grace gate);
  - a superseded target gets the existing `superseded` status weight (0.35).
Semantic auto-detection of contradictions remains v6 — none of this guesses.
unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import drift_check  # noqa: E402
import index_impl  # noqa: E402
import search_impl as si  # noqa: E402

FRESH = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
BODY = "The flarnpuzzle rotation policy requires daily rotation of keys."
QUERY = "flarnpuzzle rotation policy"


def _card(name, relations=""):
    rel_block = f"\n  {relations}" if relations else ""
    return f"""---
name: {name}
description: test card {name}
metadata:
  type: project
  evidence: observed
  source: user-explicit
  last_verified: {FRESH}{rel_block}
---

{BODY}
"""


class TruthMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-tm-test-")
        self.mem = os.path.join(self.tmp, "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index(self, files):
        conn = index_impl.init_db(self.db)
        for filename, text in files:
            path = os.path.join(self.mem, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            meta, body = index_impl.parse_frontmatter(text)
            index_impl.index_file(conn, path, meta, body)
        # BM25 needs docs WITHOUT the query terms or IDF degenerates (audit E0).
        for i in range(10):
            conn.execute(
                "INSERT INTO memory_chunks (path, name, type, section_heading, content)"
                " VALUES (?,?,?,?,?)",
                (os.path.join(self.mem, f"filler-{i}.md"), f"filler-{i}",
                 "project", f"filler-{i}", f"unrelated corpus padding entry number {i}"),
            )
        conn.commit()
        index_impl.propagate_declared_relations(conn)
        return conn

    def _scores(self):
        return {r["name"]: r["score"] for r in si._run_query(self.db, QUERY, 10, None)}

    def test_contradicts_declaration_propagates_to_target(self):
        conn = self._index([
            ("old-rule.md", _card("old-rule")),
            ("new-rule.md", _card("new-rule", "contradicts: old-rule")),
        ])
        row = conn.execute(
            "SELECT contradicted_by FROM memory_chunks WHERE name = 'old-rule'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "new-rule")

    def test_contradicted_card_ranks_below_its_contradictor(self):
        conn = self._index([
            ("old-rule.md", _card("old-rule")),
            ("new-rule.md", _card("new-rule", "contradicts: old-rule")),
        ])
        findings = drift_check.check_declared_contradictions(conn)
        conn.close()
        self.assertEqual(
            [(f[0].endswith("old-rule.md"), f[2], f[3]) for f in findings],
            [(True, "contradicted", "by=new-rule")],
        )

        drift_conn = drift_check.init_drift_db(os.path.join(self.tmp, "db", "drift_state.db"))
        drift_check.write_findings(drift_conn, findings)  # first_seen = 1
        drift_conn.close()

        scores = self._scores()
        # Declared relations bypass the grace gate: penalized on first_seen=1.
        self.assertLess(scores["old-rule"], scores["new-rule"])
        self.assertAlmostEqual(scores["old-rule"], scores["new-rule"] * 0.4, places=3)

    def test_superseded_card_ranks_below_its_replacement(self):
        conn = self._index([
            ("b-card.md", _card("b-card")),
            ("a-card.md", _card("a-card", "supersedes: b-card")),
        ])
        row = conn.execute(
            "SELECT superseded_by FROM memory_chunks WHERE name = 'b-card'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "a-card")

        scores = self._scores()
        # superseded_by → existing superseded status weight (0.35).
        self.assertLess(scores["b-card"], scores["a-card"])
        self.assertAlmostEqual(scores["b-card"], scores["a-card"] * 0.35, places=3)

    def test_explicit_target_frontmatter_wins_over_propagation(self):
        conn = self._index([
            ("old-rule.md", _card("old-rule", "contradicted_by: hand-set")),
            ("new-rule.md", _card("new-rule", "contradicts: old-rule")),
        ])
        row = conn.execute(
            "SELECT contradicted_by FROM memory_chunks WHERE name = 'old-rule'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "hand-set")

    def test_split_relation_targets(self):
        self.assertEqual(
            index_impl._split_relation_targets('[[a-card]], "b-card" , c'),
            ["a-card", "b-card", "c"],
        )
        self.assertEqual(index_impl._split_relation_targets(""), [])


if __name__ == "__main__":
    unittest.main()
