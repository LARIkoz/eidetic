"""M1 — semantic contradiction detection (spec-m1-contradiction AC-1..AC-7).

Hermetic: the confirmer and the neighbor list are INJECTED (deterministic), so
these run on BOTH legs without a model. AC-8/AC-9 (door contract + parity) live
in test_engine_contract.py. unittest.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import confidence as C  # noqa: E402
import index_impl  # noqa: E402
import m1_contradiction as m1  # noqa: E402

NEW = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
OLD = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

YES = lambda a, b: "contradiction"          # noqa: E731
NO = lambda a, b: "no_contradiction"        # noqa: E731
MAYBE = lambda a, b: "uncertain"            # noqa: E731
BOOM = lambda a, b: (_ for _ in ()).throw(RuntimeError("confirmer blew up"))  # noqa: E731


def _card(name, type_="project", source="agent-extracted", last_verified=NEW, body="claim body"):
    return f"""---
name: {name}
description: card {name}
metadata:
  type: {type_}
  source: {source}
  last_verified: {last_verified}
---

{body}
"""


class M1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m1-")
        self.mem = os.path.join(self.tmp, ".claude", "projects", "proj-a", "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")
        os.environ["EIDETIC_CONFIDENCE_EVENTS"] = "on"

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        m1.register_confirmer(None)
        if hasattr(self, "_saved_nvd"):
            m1.neighbors_via_door = self._saved_nvd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, fn, text, sub=None):
        d = os.path.join(self.mem, sub) if sub else self.mem
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _index(self, paths):
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, paths)
        return conn

    def _meta_body(self, path):
        with open(path, encoding="utf-8") as f:
            return index_impl.parse_frontmatter(f.read())

    def _events(self, path):
        with open(path, encoding="utf-8") as f:
            _m, body = index_impl.parse_frontmatter(f.read())
        return index_impl.parse_evidence_events(body)

    def _conf(self, conn, name):
        return conn.execute("SELECT confidence FROM memory_chunks WHERE name=? LIMIT 1",
                            (name,)).fetchone()[0]

    # --- AC-1 precision -------------------------------------------------
    def test_ac1_no_event_on_merely_related(self):
        n = self._write("neighbor.md", _card("neighbor"))
        c = self._write("card.md", _card("card"))
        self._index([n, c]).close()
        m_, b = self._meta_body(c)
        out = m1.process_card(c, m_, b, neighbors=[{"score": 0.72, "path": n}], confirmer=NO)
        self.assertEqual([o["action"] for o in out], ["no_contradiction"])
        self.assertNotIn("## Evidence", open(n, encoding="utf-8").read())

    # --- AC-2 fail-closed ------------------------------------------------
    def test_ac2_uncertain_and_error_map_to_no_contradiction(self):
        n = self._write("neighbor.md", _card("neighbor"))
        c = self._write("card.md", _card("card"))
        self._index([n, c]).close()
        m_, b = self._meta_body(c)
        for conf in (MAYBE, BOOM):
            out = m1.process_card(c, m_, b, neighbors=[{"score": 0.72, "path": n}], confirmer=conf)
            self.assertEqual([o["action"] for o in out], ["no_contradiction"])
        self.assertNotIn("## Evidence", open(n, encoding="utf-8").read())

    def test_ac2_default_confirmer_is_fail_closed(self):
        n = self._write("neighbor.md", _card("neighbor"))
        c = self._write("card.md", _card("card"))
        self._index([n, c]).close()
        m_, b = self._meta_body(c)
        out = m1.process_card(c, m_, b, neighbors=[{"score": 0.9, "path": n}])  # no confirmer
        self.assertEqual([o["action"] for o in out], ["no_contradiction"])

    # --- AC-3 true contradiction lands on the LOSER ---------------------
    def test_ac3_event_on_loser_delta_and_confidence_drop(self):
        # O = older, lower authority (agent); N = newer, user → loser is O.
        o = self._write("rule-o.md", _card("rule-o", source="agent-extracted", last_verified=OLD))
        nfile = self._write("rule-n.md", _card("rule-n", source="user-explicit", last_verified=NEW))
        conn = self._index([o, nfile])
        self.assertAlmostEqual(self._conf(conn, "rule-o"), 0.40)  # agent cold-start
        conn.close()

        mN, bN = self._meta_body(nfile)
        out = m1.process_card(nfile, mN, bN, neighbors=[{"score": 0.66, "path": o}], confirmer=YES)
        self.assertEqual(out, [{"loser": o, "winner": "rule-n", "action": "event"}])

        evs = self._events(o)
        self.assertEqual([e["event_type"] for e in evs], ["contradicted"])
        self.assertEqual(evs[0]["actor_tier"], 2)  # AUTOMATED tier-2, never tier-3
        self.assertIn("rule-n", evs[0]["note"])
        # re-index → O's materialized confidence dropped 0.30 (0.40 → 0.10).
        conn = index_impl.init_db(self.db)
        b = os.stat(o).st_mtime_ns + 10 ** 9
        os.utime(o, ns=(b, b))
        index_impl.run_incremental(conn, [o, nfile])
        self.assertAlmostEqual(self._conf(conn, "rule-o"), 0.10)
        conn.close()

    # --- AC-4 cross-project non-contamination ---------------------------
    def test_ac4_cross_project_neighbor_is_excluded(self):
        proj_b = os.path.join(self.tmp, ".claude", "projects", "proj-b", "memory")
        os.makedirs(proj_b)
        b_card = os.path.join(proj_b, "rule-o.md")
        with open(b_card, "w", encoding="utf-8") as f:
            f.write(_card("rule-o", source="agent-extracted", last_verified=OLD))
        nfile = self._write("rule-n.md", _card("rule-n", source="user-explicit"))
        self._index([nfile]).close()
        mN, bN = self._meta_body(nfile)
        out = m1.process_card(nfile, mN, bN, neighbors=[{"score": 0.9, "path": b_card}], confirmer=YES)
        self.assertEqual([o["action"] for o in out], ["skip_cross_project"])
        self.assertNotIn("## Evidence", open(b_card, encoding="utf-8").read())

    # --- AC-5 authority cap ---------------------------------------------
    def test_ac5_user_card_not_nuked_relation_claim(self):
        # two user-explicit peers (tier-3) conflict → loser is a user card whose
        # tier-3 hwm gates the tier-2 event → relation_claim, NO event.
        u1 = self._write("user-a.md", _card("user-a", source="user-explicit", last_verified=OLD))
        u2 = self._write("user-b.md", _card("user-b", source="user-explicit", last_verified=NEW))
        self._index([u1, u2]).close()
        m2_, b2 = self._meta_body(u2)
        out = m1.process_card(u2, m2_, b2, neighbors=[{"score": 0.7, "path": u1}], confirmer=YES)
        self.assertEqual([o["action"] for o in out], ["relation_claim"])
        self.assertNotIn("## Evidence", open(u1, encoding="utf-8").read())
        self.assertNotIn("## Evidence", open(u2, encoding="utf-8").read())

    def test_ac5_automated_confirmer_never_emits_tier3(self):
        self.assertEqual(m1.AUTOMATED_TIER, 2)
        self.assertEqual(C.ACTOR_TIERS[m1.AUTOMATED_ACTOR], 2)

    # --- AC-6 self + idempotence + full==incremental --------------------
    def test_ac6_no_self_and_idempotent(self):
        o = self._write("rule-o.md", _card("rule-o", source="agent-extracted", last_verified=OLD))
        nfile = self._write("rule-n.md", _card("rule-n", source="user-explicit"))
        self._index([o, nfile]).close()
        mN, bN = self._meta_body(nfile)
        # self excluded: a card is never its own loser (probe path == self).
        self_out = m1.process_card(nfile, mN, bN, neighbors=[{"score": 0.99, "path": nfile}], confirmer=YES)
        self.assertEqual(self_out, [])
        # first run writes the event; the SECOND run is idempotent (same pair).
        m1.process_card(nfile, mN, bN, neighbors=[{"score": 0.66, "path": o}], confirmer=YES)
        again = m1.process_card(nfile, mN, bN, neighbors=[{"score": 0.66, "path": o}], confirmer=YES)
        self.assertEqual([x["action"] for x in again], ["skip_idempotent"])
        self.assertEqual(len(self._events(o)), 1)  # exactly one contradicted, not two

    # --- FR-1/FR-7 ingest hook (dormant until confirmer) end-to-end ------
    def test_ingest_hook_dormant_by_default(self):
        # No confirmer registered → run_on_ingest is a pure no-op even with the
        # flag on and a neighbor available (would-be conflict never evaluated).
        self._saved_nvd = m1.neighbors_via_door
        o = self._write("rule-o.md", _card("rule-o", source="agent-extracted", last_verified=OLD))
        nfile = self._write("rule-n.md", _card("rule-n", source="user-explicit"))
        m1.neighbors_via_door = lambda db, probe, exclude_paths=(): (
            [] if o in exclude_paths else [{"score": 0.9, "path": o}])
        index_impl.init_db(self.db)  # ensure dir
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [o, nfile])  # confirmer is None → dormant
        conn.close()
        self.assertNotIn("## Evidence", open(o, encoding="utf-8").read())

    def test_ingest_hook_end_to_end_with_confirmer(self):
        self._saved_nvd = m1.neighbors_via_door
        o = self._write("rule-o.md", _card("rule-o", source="agent-extracted", last_verified=OLD))
        nfile = self._write("rule-n.md", _card("rule-n", source="user-explicit"))
        m1.register_confirmer(YES)
        m1.neighbors_via_door = lambda db, probe, exclude_paths=(): (
            [] if o in exclude_paths else [{"score": 0.9, "path": o}])
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [o, nfile])  # hook fires for rule-n → event on O
        conn.close()
        evs = self._events(o)
        self.assertEqual([e["event_type"] for e in evs], ["contradicted"])
        self.assertIn("rule-n", evs[0]["note"])
        # second pass materializes O's folded confidence and stays idempotent.
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [o, nfile])
        self.assertAlmostEqual(self._conf(conn, "rule-o"), 0.10)
        self.assertEqual(len(self._events(o)), 1)  # no duplicate on re-ingest
        conn.close()

    # --- AC-7 dark-safe --------------------------------------------------
    def test_ac7_events_off_writes_nothing(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)  # default OFF
        o = self._write("rule-o.md", _card("rule-o", source="agent-extracted", last_verified=OLD))
        nfile = self._write("rule-n.md", _card("rule-n", source="user-explicit"))
        self._index([o, nfile]).close()
        mN, bN = self._meta_body(nfile)
        before = open(o, encoding="utf-8").read()
        out = m1.process_card(nfile, mN, bN, neighbors=[{"score": 0.66, "path": o}], confirmer=YES)
        self.assertEqual([x["action"] for x in out], ["gated_off"])  # decided, but not written
        self.assertEqual(open(o, encoding="utf-8").read(), before)
        self.assertNotIn("## Evidence", open(o, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
