"""STEP 1B item (d) — Phase-A DARK ranking integration (spec §5, §10).

conf_w exists in the compound formula but is ACTIVE ONLY behind
EIDETIC_CONFIDENCE_RANKING. Default OFF ⇒ conf_w ≡ 1.0 ⇒ ranking + injected
context byte-identical to pre-1B (zero-diff guard). ON ⇒ confidence reorders and
never up-ranks (§9.4 test_confidence_reorders_fts). unittest.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context as ac  # noqa: E402
import confidence as C  # noqa: E402
import index_impl  # noqa: E402
import search_impl as si  # noqa: E402

FRESH = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
BODY = "The flarnpuzzle rotation policy requires daily rotation of keys."
QUERY = "flarnpuzzle rotation policy"


def _feedback(name, evidence_lines, confidence_meta=None):
    conf = f"\n  confidence: {confidence_meta}" if confidence_meta is not None else ""
    ev = "\n## Evidence\n\n" + "".join(f"- {l}\n" for l in evidence_lines)
    return f"""---
name: {name}
description: rule {name}
metadata:
  type: feedback
  source: user-explicit
  last_verified: {FRESH}{conf}
---

{BODY}
{ev}"""


class ConfidenceRankingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-1bd-")
        self.mem = os.path.join(self.tmp, "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")
        os.environ.pop("EIDETIC_CONFIDENCE_RANKING", None)
        # hi: feedback + user `confirmed` → 0.70 + 0.20 = 0.90
        # lo: feedback + user `corrected`  → 0.70 − 0.40 = 0.30  (hand-edited conf ignored)
        self._write("hi.md", _feedback(
            "hi", ["2026-07-01 · confirmed · user-explicit · Δ+0.20 · \"reaffirmed\""]))
        self._write("lo.md", _feedback(
            "lo", ["2026-07-01 · corrected · user-explicit · Δ-0.40 · \"corrected\""],
            confidence_meta=0.99))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [os.path.join(self.mem, "hi.md"),
                                          os.path.join(self.mem, "lo.md")])
        for i in range(10):  # BM25 IDF filler (docs WITHOUT the query terms)
            conn.execute("INSERT INTO memory_chunks (path, name, type, section_heading, content) "
                         "VALUES (?,?,?,?,?)",
                         (os.path.join(self.mem, f"f{i}.md"), f"f{i}", "project", f"f{i}",
                          f"unrelated corpus padding entry {i}"))
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_RANKING", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, fn, text):
        with open(os.path.join(self.mem, fn), "w", encoding="utf-8") as f:
            f.write(text)

    def _scores(self):
        return {r["name"]: r["score"] for r in si._run_query(self.db, QUERY, 10, None)}

    # --- foundation facts -------------------------------------------------
    def test_confidence_materialized_from_fold_not_authored(self):  # §3.1 derived
        conn = sqlite3.connect(self.db)
        conf = dict(conn.execute("SELECT name, confidence FROM memory_chunks "
                                 "WHERE name IN ('hi','lo')").fetchall())
        conn.close()
        self.assertAlmostEqual(conf["hi"], 0.90)
        self.assertAlmostEqual(conf["lo"], 0.30, msg="hand-edited confidence: 0.99 was not ignored")

    # --- ZERO-DIFF GUARD (flag OFF == pre-1B) -----------------------------
    def test_flag_off_ranking_is_confidence_blind(self):
        # hi (conf 0.90) and lo (conf 0.30) share every non-confidence factor, so
        # with the flag OFF their scores are IDENTICAL — confidence is not read.
        s = self._scores()
        self.assertIn("hi", s)
        self.assertIn("lo", s)
        self.assertEqual(s["hi"], s["lo"],
                         "flag OFF must ignore confidence (byte-identical to pre-1B)")

    def test_flag_off_injection_is_confidence_blind(self):
        # compound_weight (the injection ranker) must be confidence-blind off.
        w_hi = ac.compound_weight("observed", "user-explicit", FRESH, type_="feedback",
                                  confidence=0.90)
        w_lo = ac.compound_weight("observed", "user-explicit", FRESH, type_="feedback",
                                  confidence=0.30)
        self.assertEqual(w_hi, w_lo)
        # both rules still render in the assembled feedback block (P3, unchanged).
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        text, _used, _n, _slugs = ac.fetch_feedback(conn, 100000)
        conn.close()
        self.assertIn("hi", text)
        self.assertIn("lo", text)

    # --- FLAG ON (confidence reorders, never up-ranks) --------------------
    def test_confidence_reorders_fts(self):  # §9.4
        off = self._scores()
        os.environ["EIDETIC_CONFIDENCE_RANKING"] = "on"
        on = self._scores()
        # high-confidence rule now ranks strictly above the corrected one.
        self.assertGreater(on["hi"], on["lo"])
        # conf_w ≤ 1 ⇒ neither card ever up-ranks vs the flag-off score.
        self.assertLessEqual(on["hi"], off["hi"] + 1e-9)
        self.assertLessEqual(on["lo"], off["lo"] + 1e-9)
        self.assertLess(on["lo"], off["lo"], "a corrected rule must drop when the flag is on")

    def test_flag_on_injection_weight_orders_by_confidence(self):
        os.environ["EIDETIC_CONFIDENCE_RANKING"] = "on"
        w_hi = ac.compound_weight("observed", "user-explicit", FRESH, type_="feedback",
                                  confidence=0.90)
        w_lo = ac.compound_weight("observed", "user-explicit", FRESH, type_="feedback",
                                  confidence=0.30)
        self.assertGreater(w_hi, w_lo)

    def test_conf_w_never_exceeds_one(self):  # invariant §5.6.1
        for conf in (0.05, 0.4, 0.7, 0.95):
            self.assertLessEqual(C.conf_weight(conf, managed=True), 1.0)


if __name__ == "__main__":
    unittest.main()
