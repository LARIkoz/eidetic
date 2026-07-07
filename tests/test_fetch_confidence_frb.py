"""FR-B (spec-review Condition 1) — wire real confidence into the INJECTED
context ranking (assemble_context.fetch_project / fetch_recent).

Before FR-B these two paths never SELECTed c.confidence and called compound_weight
without type_/card_kind/confidence, so _inject_conf_w hardcoded 0.7 for every card
even with the Phase-A ranking flag ON. FR-B passes the real column through, behind
the SAME existing Phase-A dark flag (EIDETIC_CONFIDENCE_RANKING):

  * AC-13 — flag OFF (default): ranking is byte-identical to pre-change. Cards
    that differ ONLY in confidence keep the confidence-blind mtime order, and
    compound_weight ignores the confidence argument entirely.
  * AC-14 — flag ON: a managed card at confidence 0.55 outranks an otherwise
    identical card at 0.40.

Direct-INSERT fixture store (fully deterministic, no model, both legs identical).
"""

import os
import sqlite3
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context as ac  # noqa: E402
import index_impl  # noqa: E402

CWD = "/x/proj_fix"
SLUG = "x-proj_fix"  # detect_project_slug(CWD)


class FetchConfidenceFRBTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="frb-")
        self.db = os.path.join(self.tmp, "db", "index.db")
        os.makedirs(os.path.dirname(self.db))
        os.environ.pop("EIDETIC_CONFIDENCE_RANKING", None)
        conn = index_impl.init_db(self.db)
        # Three managed (agent-extracted, type=project) cards, identical in every
        # ranking factor EXCEPT confidence. mtime is set so the confidence-blind
        # order (mtime DESC) is the REVERSE of the confidence order — the strongest
        # possible AC-13/AC-14 contrast.
        now = int(time.time())
        cards = [
            ("hi", 0.55, now - 300),   # highest confidence, OLDEST mtime
            ("mid", 0.40, now - 200),
            ("lo", 0.20, now - 100),   # lowest confidence, NEWEST mtime
        ]
        for name, conf, mtime in cards:
            conn.execute(
                "INSERT INTO memory_chunks "
                "(path, project, name, type, evidence, source, confidence, "
                " last_verified, card_kind, status, section_heading, content, mtime) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (os.path.join(self.tmp, f"{name}.md"), SLUG, name, "project",
                 "hypothesis", "agent-extracted", conf,
                 time.strftime("%Y-%m-%d"), "synthesis", "current", name,
                 f"body for {name} card", mtime))
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        os.environ.pop("EIDETIC_CONFIDENCE_RANKING", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conn(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    # --- AC-13: flag OFF → byte-identical (confidence-blind) ------------------
    def test_ac13_flag_off_fetch_project_confidence_blind(self):
        conn = self._conn()
        order = self._order_project(conn)
        conn.close()
        # confidence-blind ⇒ mtime-DESC order (lo, mid, hi), NOT the confidence
        # order — proving confidence was not consulted with the flag off.
        self.assertEqual(order, ["lo", "mid", "hi"])

    def test_ac13_flag_off_fetch_recent_confidence_blind(self):
        conn = self._conn()
        self.assertEqual(self._order_recent(conn), ["lo", "mid", "hi"])
        conn.close()

    def test_ac13_compound_weight_ignores_confidence_when_off(self):
        # the crisp byte-identical proof: the new confidence arg is INERT off.
        w_with = ac.compound_weight("hypothesis", "agent-extracted", None,
                                    type_="project", card_kind="synthesis", confidence=0.55)
        w_without = ac.compound_weight("hypothesis", "agent-extracted", None)
        self.assertEqual(w_with, w_without)
        w_hi = ac.compound_weight("hypothesis", "agent-extracted", None,
                                  type_="project", card_kind="synthesis", confidence=0.55)
        w_lo = ac.compound_weight("hypothesis", "agent-extracted", None,
                                  type_="project", card_kind="synthesis", confidence=0.40)
        self.assertEqual(w_hi, w_lo)

    # --- AC-14: flag ON → 0.55 outranks 0.40 ---------------------------------
    def test_ac14_flag_on_fetch_project_orders_by_confidence(self):
        os.environ["EIDETIC_CONFIDENCE_RANKING"] = "on"
        conn = self._conn()
        order = self._order_project(conn)
        conn.close()
        self.assertEqual(order, ["hi", "mid", "lo"])
        self.assertLess(order.index("hi"), order.index("mid"))  # 0.55 outranks 0.40

    def test_ac14_flag_on_fetch_recent_orders_by_confidence(self):
        os.environ["EIDETIC_CONFIDENCE_RANKING"] = "on"
        conn = self._conn()
        order = self._order_recent(conn)
        conn.close()
        self.assertEqual(order, ["hi", "mid", "lo"])

    def test_ac14_flag_on_injection_weight_orders_by_confidence(self):
        os.environ["EIDETIC_CONFIDENCE_RANKING"] = "on"
        w_hi = ac.compound_weight("hypothesis", "agent-extracted", None,
                                  type_="project", card_kind="synthesis", confidence=0.55)
        w_lo = ac.compound_weight("hypothesis", "agent-extracted", None,
                                  type_="project", card_kind="synthesis", confidence=0.40)
        self.assertGreater(w_hi, w_lo)

    # helpers: the RANKED slug order the fetchers emit (budget large ⇒ all). The
    # `included` return value is a sorted set, so rank order is read from the text
    # (name lines are appended in ranked order).
    def _order_project(self, conn):
        text, _u, _i = ac.fetch_project(conn, CWD, 100000)
        return self._names_in_order(text)

    def _order_recent(self, conn):
        text, _u, _i = ac.fetch_recent(conn, 100000, None, None)
        return self._names_in_order(text)

    def _names_in_order(self, text):
        order = []
        for line in text.splitlines():
            for name in ("hi", "mid", "lo"):
                if line.startswith(f"**{name}**"):
                    order.append(name)
        return order


if __name__ == "__main__":
    unittest.main()
