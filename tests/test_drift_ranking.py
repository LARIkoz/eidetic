"""Causal test: drift findings must re-order REAL search output downward only.

The 2026-07-02 audit proved the v5.13.0 penalty (which REPLACED freshness)
up-ranked a stale broken-wikilink card by +60% and made age_stale a no-op.
These tests run the actual `_run_query` pipeline against a seeded index.db +
drift_state.db pair and assert, per drift type:
  - broken_wikilink NEVER raises a card's score vs the no-finding twin;
  - age_stale strictly lowers a stale card vs an equally stale clean twin;
  - confidence_escalation down-ranks below the clean twin;
  - the `first_seen > 1` grace gate: a first-detection finding changes nothing.
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

STALE = "2025-01-01"
FRESH = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
QUERY = "zanzibar quorum election"
BODY = "The zanzibar quorum election policy prefers the oldest replica."


class DriftRankingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-drift-test-")
        self.db = os.path.join(self.tmp, "index.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, cards):
        conn = sqlite3.connect(self.db)
        conn.executescript(index_impl.DB_SCHEMA)
        rows = list(cards)
        # Fillers WITHOUT the query terms keep BM25 IDF non-degenerate
        # (E0 in the audit: terms in most chunks → rank 0 → weights inert).
        for i in range(10):
            rows.append((f"/tmp/eidetic-test/memory/filler-{i}.md",
                         f"filler-{i}", f"unrelated corpus padding entry number {i}", FRESH))
        for path, name, content, last_verified in rows:
            conn.execute(
                "INSERT INTO memory_chunks (path, project, name, type, evidence, source,"
                " last_verified, section_heading, content, description)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (path, "eidetic-test", name, "project", "observed", "user-explicit",
                 last_verified, name, content, ""),
            )
        conn.commit()
        conn.close()

    def _flag(self, path, drift_type, detail, first_seen=2):
        drift = drift_check.init_drift_db(os.path.join(self.tmp, "drift_state.db"))
        drift.execute(
            "INSERT INTO drift_findings (path, drift_type, detail, memory_type,"
            " detected_at, first_seen) VALUES (?,?,?,?,?,?)",
            (path, drift_type, detail, "project", "2026-07-01T00:00:00Z", first_seen),
        )
        drift.commit()
        drift.close()

    def _scores(self):
        results = si._run_query(self.db, QUERY, 10, None)
        return {r["path"]: r["score"] for r in results}

    def test_broken_wikilink_never_upranks_a_stale_card(self):
        flagged = "/tmp/eidetic-test/memory/stale-flagged.md"
        clean = "/tmp/eidetic-test/memory/stale-clean.md"
        self._seed([(flagged, "stale-flagged", BODY, STALE),
                    (clean, "stale-clean", BODY, STALE)])
        self._flag(flagged, "broken_wikilink", "[[dead-target]]")
        scores = self._scores()
        # Identical bodies + identical staleness → fts_rank equal; the ONLY
        # difference is the drift finding, which must strictly down-rank.
        self.assertLess(scores[flagged], scores[clean])

    def test_age_stale_strictly_lowers_a_stale_card(self):
        flagged = "/tmp/eidetic-test/memory/age-flagged.md"
        clean = "/tmp/eidetic-test/memory/age-clean.md"
        self._seed([(flagged, "age-flagged", BODY, STALE),
                    (clean, "age-clean", BODY, STALE)])
        self._flag(flagged, "age_stale", "threshold=30d")
        scores = self._scores()
        # v5.13.0: penalty 0.5 replaced freshness 0.5 → byte-identical scores.
        self.assertLess(scores[flagged], scores[clean])

    def test_confidence_escalation_downranks_a_fresh_card(self):
        flagged = "/tmp/eidetic-test/memory/esc-flagged.md"
        clean = "/tmp/eidetic-test/memory/esc-clean.md"
        self._seed([(flagged, "esc-flagged", BODY, FRESH),
                    (clean, "esc-clean", BODY, FRESH)])
        self._flag(flagged, "confidence_escalation", "threshold=3")
        scores = self._scores()
        self.assertLess(scores[flagged], scores[clean])
        self.assertAlmostEqual(scores[flagged], scores[clean] * 0.3, places=3)

    def test_first_detection_grace_gate_changes_nothing(self):
        flagged = "/tmp/eidetic-test/memory/grace-flagged.md"
        clean = "/tmp/eidetic-test/memory/grace-clean.md"
        self._seed([(flagged, "grace-flagged", BODY, FRESH),
                    (clean, "grace-clean", BODY, FRESH)])
        self._flag(flagged, "broken_wikilink", "[[dead-target]]", first_seen=1)
        scores = self._scores()
        self.assertAlmostEqual(scores[flagged], scores[clean], places=6)


if __name__ == "__main__":
    unittest.main()
