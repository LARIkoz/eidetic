#!/usr/bin/env python3
"""Fixtures for the P0 aligned-coverage audit (coverage_audit.py).

The metric must be able to FAIL: each category is provoked by a crafted
index.db/vectors.db pair and asserted exactly. Mirrors the real search guard
(join by chunk_id → path/heading → recomputed content_hash).
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import coverage_audit  # noqa: E402
import embed           # noqa: E402
import index_impl      # noqa: E402


def _make_index(path):
    conn = sqlite3.connect(path)
    conn.executescript(index_impl.DB_SCHEMA)
    return conn


def _add_chunk(conn, cid, path, name, content, heading="", desc="d"):
    conn.execute(
        "INSERT INTO memory_chunks (id, path, name, content, description, section_heading) "
        "VALUES (?,?,?,?,?,?)",
        (cid, path, name, content, desc, heading),
    )


def _make_vectors(path):
    conn = embed.init_vector_db(path)
    return conn


def _add_vector(conn, cid, path, name, heading, chash):
    conn.execute(
        "INSERT OR REPLACE INTO vectors (chunk_id, path, name, section_heading, content_hash, embedding, mtime) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, path, name, heading, chash, b"\x00\x00\x00\x00", 0),
    )


def _parse_oneline(s):
    """Parse the bash-facing KEY=VALUE line back into a dict (the same split
    doctor.sh's `eval` and the inject hook's dict() do)."""
    return {k: v for k, v in (t.split("=", 1) for t in s.split() if "=" in t)}


class CoverageAuditFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.index_db = os.path.join(d, "index.db")
        self.vectors_db = os.path.join(d, "vectors.db")

        # on-disk files only matter for ZERO-ROW reason classification
        self.p_empty = os.path.join(d, "empty.md")
        with open(self.p_empty, "w", encoding="utf-8") as f:
            f.write("---\nname: e\n---\n")          # frontmatter, no body -> empty-body
        self.p_absent = os.path.join(d, "absent.md")
        with open(self.p_absent, "w", encoding="utf-8") as f:
            f.write("---\nname: a\n---\nreal body here\n")  # body, but no rows -> absent-from-index

        # chunk-bearing fixtures (paths need not exist on disk: they have rows)
        p_aligned = os.path.join(d, "aligned.md")
        p_novec = os.path.join(d, "novec.md")
        p_stalehash = os.path.join(d, "stalehash.md")
        p_stalevec = os.path.join(d, "stalevec.md")

        ic = _make_index(self.index_db)
        _add_chunk(ic, 1, p_aligned, "Aligned", "alpha content")
        _add_chunk(ic, 2, p_novec, "NoVec", "beta content")
        _add_chunk(ic, 3, p_stalehash, "StaleHash", "gamma content")
        _add_chunk(ic, 4, p_stalevec, "StaleVec", "delta content")
        ic.commit(); ic.close()

        vc = _make_vectors(self.vectors_db)
        # aligned: guard recomputes content_hash(VEC.name, CHUNK.desc, CHUNK.content, CHUNK.heading)
        good = embed.content_hash("Aligned", "d", "alpha content", "")
        _add_vector(vc, 1, p_aligned, "Aligned", "", good)
        # chunk 2 -> NO vector row  => indexed-no-vector
        # stale-hash: path/heading match, hash wrong
        _add_vector(vc, 3, p_stalehash, "StaleHash", "", "deadbeef" * 8)
        # stale-vector: chunk_id live but path mismatch
        _add_vector(vc, 4, "/wrong/path.md", "StaleVec", "", "whatever")
        # orphan: chunk_id 999 not in any memory_chunks row
        _add_vector(vc, 999, "/ghost.md", "Ghost", "", "x")
        vc.commit(); vc.close()

        self.collected = [p_aligned, p_novec, p_stalehash, p_stalevec,
                          self.p_empty, self.p_absent]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        with mock.patch.object(index_impl, "collect_files", return_value=self.collected):
            return coverage_audit.audit(self.index_db, self.vectors_db)

    def test_categories_each_reachable(self):
        r = self._run()
        c = r["categories"]
        self.assertEqual(c["aligned"], 1, "one fully-aligned chunk")
        self.assertEqual(c["indexed-no-vector"], 1, "chunk with no vector row")
        self.assertEqual(c["stale-hash"], 1, "vector hash mismatch (path/heading match)")
        self.assertEqual(c["stale-vector"], 1, "vector chunk_id live but path mismatch")

    def test_orphan_vector_counted(self):
        r = self._run()
        self.assertEqual(r["orphan_vectors"], 1, "vector chunk_id absent from live rows")

    def test_zero_row_reasons_closed_vocab(self):
        r = self._run()
        z = r["zero_row_reasons"]
        self.assertEqual(z["empty-body"], 1)
        self.assertEqual(z["absent-from-index"], 1, "real body but unindexed = BUG sentinel")
        self.assertEqual(z["non-utf8"], 0)
        self.assertEqual(z["parse-error"], 0)

    def test_aligned_pct_can_drop(self):
        """The metric is sensitive: 1 aligned of 4 chunk-bearing files = 25% file alignment."""
        r = self._run()
        allscope = r["scopes"]["all"]
        self.assertEqual(allscope["files_aligned"], 1)
        self.assertEqual(allscope["files_blind"], 3)       # novec/stalehash/stalevec
        self.assertEqual(allscope["files_zero_row"], 2)
        self.assertEqual(allscope["aligned_file_pct"], 25.0)  # 1 / (6 - 2 zero-row)

    def test_oneline_mixed(self):
        """--oneline emits guard-accurate, bash-parseable facts (the mixed fixture)."""
        kv = _parse_oneline(coverage_audit._oneline(self._run()))
        self.assertEqual(kv["align_pct"], "25")    # 1 aligned / 4 chunks, floored int
        self.assertEqual(kv["aligned"], "1")
        self.assertEqual(kv["total"], "4")
        self.assertEqual(kv["orphan"], "1")
        self.assertEqual(kv["no_vector"], "1")
        self.assertEqual(kv["stale"], "2")         # stale-hash + stale-vector
        self.assertEqual(kv["blind_files"], "3")
        for v in kv.values():                      # every value a bare int -> eval-safe
            self.assertRegex(v, r"^-?\d+$")


class OnelineEdgeCases(unittest.TestCase):
    """The two boundary fixtures the doctor gate (align_pct < 80 -> BAD) turns on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.index_db = os.path.join(self.d, "index.db")
        self.vectors_db = os.path.join(self.d, "vectors.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _audit(self, collected):
        with mock.patch.object(index_impl, "collect_files", return_value=collected):
            return coverage_audit.audit(self.index_db, self.vectors_db)

    def test_oneline_all_aligned_100pct(self):
        p1 = os.path.join(self.d, "a.md"); p2 = os.path.join(self.d, "b.md")
        ic = _make_index(self.index_db)
        _add_chunk(ic, 1, p1, "A", "alpha")
        _add_chunk(ic, 2, p2, "B", "beta")
        ic.commit(); ic.close()
        vc = _make_vectors(self.vectors_db)
        _add_vector(vc, 1, p1, "A", "", embed.content_hash("A", "d", "alpha", ""))
        _add_vector(vc, 2, p2, "B", "", embed.content_hash("B", "d", "beta", ""))
        vc.commit(); vc.close()
        kv = _parse_oneline(coverage_audit._oneline(self._audit([p1, p2])))
        self.assertEqual(kv["align_pct"], "100")
        self.assertEqual(kv["orphan"], "0")
        self.assertEqual(kv["blind_files"], "0")
        self.assertEqual(kv["no_vector"], "0")

    def test_oneline_disaster_all_misaligned_0pct(self):
        """The real outage in miniature: vectors EXIST but every chunk_id is
        orphaned -> align_pct 0, orphans dominate, so the doctor BAD gate fires.
        A gross (chunks-vectors) count would have called this healthy."""
        p1 = os.path.join(self.d, "a.md"); p2 = os.path.join(self.d, "b.md")
        ic = _make_index(self.index_db)
        _add_chunk(ic, 1, p1, "A", "alpha")
        _add_chunk(ic, 2, p2, "B", "beta")
        ic.commit(); ic.close()
        vc = _make_vectors(self.vectors_db)
        for vid in (101, 102, 103):                # vectors with no live chunk row
            _add_vector(vc, vid, f"/ghost{vid}.md", "G", "", "x")
        vc.commit(); vc.close()
        kv = _parse_oneline(coverage_audit._oneline(self._audit([p1, p2])))
        self.assertEqual(kv["align_pct"], "0")     # doctor: < 80 -> BAD
        self.assertEqual(kv["aligned"], "0")
        self.assertEqual(kv["orphan"], "3")        # vectors exist but all dead
        self.assertEqual(kv["blind_files"], "2")   # both files fully blind
        self.assertEqual(kv["no_vector"], "2")


if __name__ == "__main__":
    unittest.main()
