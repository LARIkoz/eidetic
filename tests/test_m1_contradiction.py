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

def _fastembed_available():
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None


VECTORED_ONLY = "vectored-mode e2e: requires fastembed (Leg A)"

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

    # --- FR-3 production confirmer (deterministic opposition; both legs) --
    def test_production_confirmer_recall_and_zero_fp(self):
        # AC-1 precision + AC-1b confirmer FP, on the labeled set. Model-free.
        def v(a, b):
            return m1.production_confirmer({"text": a}, {"text": b})
        contra = [
            ("The primary datastore is PostgreSQL.", "The primary datastore is MySQL."),
            ("Feature flags are enabled by default.", "Feature flags are disabled by default."),
            ("Retries are capped at 3 attempts.", "Retries are capped at 10 attempts."),
            ("Auth tokens expire after 24 hours.", "Auth tokens never expire."),
            ("The API is synchronous.", "The API is asynchronous."),
            ("Access is allowed for guests.", "Access is denied for guests."),
        ]
        noncontra = [
            ("The primary datastore is PostgreSQL.", "PostgreSQL supports JSON columns."),
            ("Feature flags are enabled by default.", "Feature flags are read from config."),
            ("Retries are capped at 3 attempts.", "Retries use exponential backoff."),
            ("The API is synchronous.", "The API returns JSON."),
            ("The primary datastore is PostgreSQL.", "Kubernetes ingress certificate renewal timed out."),
            ("Retries are capped at 3 attempts.", "The office coffee machine is broken again."),
            ("Access is allowed for guests.", "The deployment pipeline runs on Fridays."),
        ]
        self.assertTrue(all(v(a, b) == "contradiction" for a, b in contra), "recall < 6/6")
        self.assertEqual([v(a, b) for a, b in noncontra].count("contradiction"), 0, "FP > 0")

    def test_production_confirmer_fail_closed_on_error(self):
        # A record without "text" (or any raising access) → no_contradiction.
        class Boom(dict):
            def get(self, *a):
                raise RuntimeError("boom")
        self.assertEqual(m1.production_confirmer(Boom(), {"text": "x"}), "no_contradiction")

    # --- FR-1/FR-7 ingest hook end-to-end (production confirmer, both legs)
    def test_ingest_hook_active_but_precise(self):
        # Hook is ACTIVE by default (production confirmer), but two NON-conflicting
        # cards produce NO event — the active hook does not false-positive.
        self._saved_nvd = m1.neighbors_via_door
        o = self._write("db-a.md", _card("db-a", source="agent-extracted", last_verified=OLD,
                                          body="PostgreSQL supports JSON columns."))
        nfile = self._write("db-b.md", _card("db-b", source="user-explicit",
                                              body="The primary datastore is PostgreSQL."))
        m1.neighbors_via_door = lambda db, probe, exclude_paths=(): (
            [] if o in exclude_paths else [{"score": 0.9, "path": o}])
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [o, nfile])
        conn.close()
        self.assertNotIn("## Evidence", open(o, encoding="utf-8").read())

    def test_ingest_hook_end_to_end_production_confirmer(self):
        # Real conflict via the DEFAULT production confirmer (no injection): the
        # exclusive-set opposition Postgres↔MySQL fires → event on the loser O.
        self._saved_nvd = m1.neighbors_via_door
        o = self._write("store-o.md", _card("store-o", source="agent-extracted", last_verified=OLD,
                                             body="The primary datastore is MySQL."))
        nfile = self._write("store-n.md", _card("store-n", source="user-explicit",
                                                body="The primary datastore is PostgreSQL."))
        m1.neighbors_via_door = lambda db, probe, exclude_paths=(): (
            [] if o in exclude_paths else [{"score": 0.9, "path": o}])
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [o, nfile])  # hook fires for store-n → event on O
        conn.close()
        evs = self._events(o)
        self.assertEqual([e["event_type"] for e in evs], ["contradicted"])
        self.assertEqual(evs[0]["actor_tier"], 2)
        self.assertIn("store-n", evs[0]["note"])
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [o, nfile])
        self.assertAlmostEqual(self._conf(conn, "store-o"), 0.10)
        self.assertEqual(len(self._events(o)), 1)  # idempotent on re-ingest
        conn.close()

    # --- FR-4/§4.4 relation_claim DURABLE persistence --------------------
    def test_ac5_relation_claim_persisted_to_drift(self):
        import drift_check
        u1 = self._write("user-a.md", _card("user-a", source="user-explicit", last_verified=OLD,
                                             body="The primary datastore is MySQL."))
        u2 = self._write("user-b.md", _card("user-b", source="user-explicit",
                                             body="The primary datastore is PostgreSQL."))
        self._index([u1, u2]).close()
        m2_, b2 = self._meta_body(u2)
        out = m1.process_card(u2, m2_, b2, neighbors=[{"score": 0.9, "path": u1}],
                              confirmer=YES, index_db_path=self.db)
        self.assertEqual([o["action"] for o in out], ["relation_claim"])
        self.assertTrue(out[0]["persisted"])
        # a durable relation_claim finding sits on the LOSER, penalty-1.0 diagnostic.
        drift_db = drift_check.get_drift_db_path(self.db)
        dconn = drift_check.init_drift_db(drift_db)
        rows = dconn.execute(
            "SELECT path, drift_type, detail FROM drift_findings WHERE drift_type='relation_claim'"
        ).fetchall()
        dconn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], u1)  # the capped loser (older user card)
        self.assertIn("user-b", rows[0][2])
        # and NO confidence event was written on either user card.
        self.assertNotIn("## Evidence", open(u1, encoding="utf-8").read())

    def test_ac5_relation_claim_dark_safe(self):
        # flag OFF → no drift finding persisted (dark-safe).
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        import drift_check
        u1 = self._write("user-a.md", _card("user-a", source="user-explicit", last_verified=OLD,
                                             body="The primary datastore is MySQL."))
        u2 = self._write("user-b.md", _card("user-b", source="user-explicit",
                                             body="The primary datastore is PostgreSQL."))
        self._index([u1, u2]).close()
        m2_, b2 = self._meta_body(u2)
        out = m1.process_card(u2, m2_, b2, neighbors=[{"score": 0.9, "path": u1}],
                              confirmer=YES, index_db_path=self.db)
        self.assertFalse(out[0]["persisted"])
        drift_db = drift_check.get_drift_db_path(self.db)
        self.assertFalse(os.path.exists(drift_db), "no drift db written when flag OFF")

    # --- Leg-A full vectored e2e: real vectors.db + production confirmer -
    @unittest.skipUnless(_fastembed_available(), VECTORED_ONLY)
    def test_leg_a_full_e2e_real_vectors_and_confirmer(self):
        import engine
        engine.configure(provider="cpu", threads=8)
        o = self._write("store-o.md", _card("store-o", source="agent-extracted", last_verified=OLD,
                                             body="The primary datastore is MySQL."))
        nfile = self._write("store-n.md", _card("store-n", source="user-explicit",
                                                body="The primary datastore is PostgreSQL."))
        self._index([o, nfile]).close()
        # Build a REAL vectors.db from the index (the actual private builder).
        vectors_db = self.db.replace("index.db", "vectors.db")
        engine._embed().run_full(self.db, vectors_db)
        # Whole vectored path: door neighbor retrieval → candidate gate →
        # production confirmer (exclusive-set Postgres↔MySQL) → event on the loser.
        conn = index_impl.init_db(self.db)
        m1.run_on_ingest(conn, self.db, [o, nfile])
        conn.close()
        evs = self._events(o)
        self.assertEqual([e["event_type"] for e in evs], ["contradicted"])
        self.assertEqual(evs[0]["actor_tier"], 2)
        self.assertIn("store-n", evs[0]["note"])
        # the winner (higher-authority user card) is untouched.
        self.assertNotIn("## Evidence", open(nfile, encoding="utf-8").read())

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
