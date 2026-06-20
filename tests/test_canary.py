#!/usr/bin/env python3
"""Tests for bin/canary.py — the doctor's functional embed→vector→search + usage canary.

The pure functions take injectable callables so §3.1 (embed canary) and §3.2 (usage
canary) are tested WITHOUT loading the ~2 GB e5 model. One regression test uses the
REAL embed.search against a vectors.db stamped with a mismatched model — the search
guard short-circuits to [] before any model load, so it proves "a corrupted/mismatched
vectors.db fails the canary loudly" (AC1) with zero fastembed dependency.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import canary  # noqa: E402
import embed   # noqa: E402


def _mk_vectors_db(rows, model=None, dim=None, hash_scheme=None):
    """rows: list of (chunk_id, name). Builds a real vectors.db via embed.init_vector_db
    (so the schema + meta match production). Optional meta stamp for the drift test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = embed.init_vector_db(path)
    try:
        for cid, name in rows:
            conn.execute(
                "INSERT OR REPLACE INTO vectors "
                "(chunk_id, path, name, section_heading, content_hash, embedding, mtime) "
                "VALUES (?,?,?,?,?,?,?)",
                (cid, f"/mem/{cid}.md", name, "", "deadbeef", b"\x00" * 16, 0),
            )
        if model is not None:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('model',?)", (model,))
        if dim is not None:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('dim',?)", (str(dim),))
        if hash_scheme is not None:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('hash_scheme',?)", (hash_scheme,))
        conn.commit()
    finally:
        conn.close()
    return path


class EmbedCanaryTest(unittest.TestCase):
    def setUp(self):
        self.vdb = _mk_vectors_db([(1, "short"), (7, "a distinctive memory card name here"), (3, "another card")])
        self.addCleanup(lambda: os.path.exists(self.vdb) and os.remove(self.vdb))

    def test_pick_prefers_long_distinctive_name(self):
        cid, name = canary.pick_canary_card(self.vdb)
        self.assertEqual(cid, 7)
        self.assertEqual(name, "a distinctive memory card name here")

    def test_pass_when_card_in_top3(self):
        # injected search returns (sim, chunk_id, ...) rows like embed.search; target rank 2
        def fake(_vdb, _query, _limit):
            return [(0.9, 99, "", "", "", ""), (0.8, 7, "", "", "", ""), (0.7, 3, "", "", "", "")]
        r = canary.embed_canary("idx", self.vdb, search_fn=fake)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["rank"], 2)

    def test_warn_when_rank_beyond_top(self):
        def fake(_vdb, _query, _limit):
            return [(0.9, i, "", "", "", "") for i in (10, 11, 12, 13, 7)]  # target at rank 5
        r = canary.embed_canary("idx", self.vdb, search_fn=fake)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["rank"], 5)

    def test_fail_when_empty(self):
        r = canary.embed_canary("idx", self.vdb, search_fn=lambda *a: [])
        self.assertEqual(r["status"], "fail")
        self.assertIn("0 results", r["detail"])

    def test_fail_when_card_absent(self):
        def fake(_vdb, _query, _limit):
            return [(0.9, 100, "", "", "", ""), (0.8, 200, "", "", "", "")]
        r = canary.embed_canary("idx", self.vdb, search_fn=fake)
        self.assertEqual(r["status"], "fail")
        self.assertIn("NOT in top", r["detail"])

    def test_skip_when_no_vectors_db(self):
        r = canary.embed_canary("idx", "/nonexistent/vectors.db")
        self.assertEqual(r["status"], "skip")

    def test_real_meta_mismatch_fails_loud(self):
        """AC1 regression: a vectors.db built by a DIFFERENT model must fail the canary.
        embed.search's _vector_meta_ok short-circuits to [] on the stamp mismatch BEFORE
        loading the model — so this runs with no fastembed and proves the loud failure."""
        bad = _mk_vectors_db([(7, "a distinctive memory card name here")],
                             model="some/other-model", dim=384, hash_scheme="trunc500-v2")
        self.addCleanup(lambda: os.path.exists(bad) and os.remove(bad))
        r = canary.embed_canary("idx", bad, search_fn=embed.search)
        self.assertEqual(r["status"], "fail")


class UsageCanaryTest(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.db) and os.remove(self.db))
        self._saved = os.environ.get("EIDETIC_USAGE_LOG")
        os.environ.pop("EIDETIC_USAGE_LOG", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("EIDETIC_USAGE_LOG", None)
        else:
            os.environ["EIDETIC_USAGE_LOG"] = self._saved

    def test_off_when_opted_out(self):
        os.environ["EIDETIC_USAGE_LOG"] = "off"
        r = canary.usage_canary(self.db, "probe")
        self.assertEqual(r["status"], "off")

    def test_live_via_search(self):
        # run_search writes a confident line through the real usage module → temp log grows
        def run_search(db_path, query):
            mod = canary._load_usage()
            mod.log_surfaced([{"path": "p", "section": "s", "confidence": "high"}], query, db_path, "high")
        r = canary.usage_canary(self.db, "probe", run_search=run_search)
        self.assertEqual(r["status"], "live")
        self.assertIn("real confident search", r["detail"])

    def test_live_via_probe_when_search_silent(self):
        # search logs nothing (e.g. not confident) but the direct logger probe writes → live
        r = canary.usage_canary(self.db, "probe",
                                run_search=lambda *a: None)  # default log_probe writes
        self.assertEqual(r["status"], "live")
        self.assertIn("direct probe", r["detail"])

    def test_silent_when_nothing_writes(self):
        r = canary.usage_canary(self.db, "probe",
                                run_search=lambda *a: None,
                                log_probe=lambda *a: None)
        self.assertEqual(r["status"], "silent")

    def test_notdeployed_when_usage_missing(self):
        import unittest.mock as mock
        with mock.patch.object(canary, "_load_usage", return_value=None):
            r = canary.usage_canary(self.db, "probe")
        self.assertEqual(r["status"], "notdeployed")

    def test_temp_log_does_not_pollute_prod(self):
        """The canary must write ONLY to its temp log, never the real usage.log next
        to db (else it poisons the telemetry it verifies)."""
        prod = os.path.join(os.path.dirname(os.path.abspath(self.db)), "usage.log")
        before = os.path.getsize(prod) if os.path.exists(prod) else 0
        canary.usage_canary(self.db, "probe", run_search=lambda *a: None)  # writes via probe → temp only
        after = os.path.getsize(prod) if os.path.exists(prod) else 0
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
