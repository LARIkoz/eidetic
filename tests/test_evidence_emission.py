"""STEP 1B turn 10 — event emission (§4.5) + decay-on-silence (§4.3).

Events reach the confidence rails through real writer paths (the compound
distiller for `observed`; drift_check for `decayed`) + the deterministic emitter
API — append-under-lock, then folded on reindex. unittest.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import compound  # noqa: E402
import confidence as C  # noqa: E402
import drift_check  # noqa: E402
import evidence  # noqa: E402
import index_impl  # noqa: E402

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
FRESH = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
OLD = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")


def setUpModule():
    # Event emission is gated behind EIDETIC_CONFIDENCE_EVENTS (default OFF, F1a);
    # these tests exercise the ACTIVATED state. The subprocess inherits it via
    # os.environ. A fresh install writes nothing — see test_evidence_safety.
    os.environ["EIDETIC_CONFIDENCE_EVENTS"] = "on"


def tearDownModule():
    os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)


def _card(name, type_="project", source="agent-extracted", last_verified=FRESH,
          evidence_lines=None):
    ev = ""
    if evidence_lines:
        ev = "\n## Evidence\n\n" + "".join(f"- {l}\n" for l in evidence_lines)
    return f"""---
name: {name}
description: rule about zorptangle indexer rebuild policy {name}
metadata:
  type: {type_}
  source: {source}
  last_verified: {last_verified}
---

The zorptangle indexer performs an incremental rebuild after every vacuum.
{ev}"""


class EvidenceApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ev-api-")
        self.card = os.path.join(self.tmp, "rule.md")
        with open(self.card, "w", encoding="utf-8") as f:
            f.write(_card("rule"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_emit_creates_evidence_section_and_parses_back(self):
        self.assertTrue(evidence.observed(self.card, note="agent re-derived"))
        body = open(self.card, encoding="utf-8").read()
        self.assertIn("## Evidence", body)
        _meta, parsed_body = index_impl.parse_frontmatter(body)
        events = index_impl.parse_evidence_events(parsed_body)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "observed")
        self.assertEqual(events[0]["actor_tier"], 1)

    def test_typed_emitters_have_correct_tiers(self):
        evidence.confirmed(self.card)
        evidence.verified_by_test(self.card)
        _m, b = index_impl.parse_frontmatter(open(self.card, encoding="utf-8").read())
        by_type = {e["event_type"]: e["actor_tier"] for e in index_impl.parse_evidence_events(b)}
        self.assertEqual(by_type["confirmed"], 3)
        self.assertEqual(by_type["verified_by_test"], 2)

    def test_append_is_deduped(self):
        ts = "2026-07-01T10:00:00"
        self.assertTrue(evidence.append_event(self.card, "confirmed", ts=ts))
        self.assertFalse(evidence.append_event(self.card, "confirmed", ts=ts))  # same ts+type
        _m, b = index_impl.parse_frontmatter(open(self.card, encoding="utf-8").read())
        self.assertEqual(len(index_impl.parse_evidence_events(b)), 1)

    def test_append_refused_under_lock(self):
        import fcntl
        lock = open(self.card + ".evlock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.assertFalse(evidence.observed(self.card), "must not write while another holds the lock")
        finally:
            lock.close()
            os.unlink(self.card + ".evlock")


class ObservedWiringTest(unittest.TestCase):
    """§4.5 bullet 1 — the compound distiller emits `observed` for managed cards."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ev-compound-")
        self.mem = os.path.join(self.tmp, "memory")
        os.makedirs(self.mem)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, fn, text):
        p = os.path.join(self.mem, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_helper_emits_only_for_managed(self):
        managed = self._write("agent.md", _card("agent", type_="project", source="agent-extracted"))
        exempt = self._write("profile.md", _card("profile", type_="user", source="user-explicit"))
        compound._emit_observed_if_managed(managed, "Rule: rebuild incrementally")
        compound._emit_observed_if_managed(exempt, "Rule: rebuild incrementally")
        self.assertIn("## Evidence", open(managed, encoding="utf-8").read())
        self.assertNotIn("## Evidence", open(exempt, encoding="utf-8").read())

    def test_compound_subprocess_emits_observed_and_folds(self):
        card = self._write("indexer-rule.md",
                           _card("indexer-rule", type_="project", source="agent-extracted"))
        db = os.path.join(self.tmp, "db", "index.db")
        conn = index_impl.init_db(db)
        index_impl.run_incremental(conn, [card])
        conn.close()
        env = dict(os.environ, EIDETIC_MEMORY_SYSTEM=self.tmp)
        proc = subprocess.run(
            [sys.executable, os.path.join(BIN, "compound.py"), self.tmp],
            input="Knowledge: the zorptangle indexer performs an incremental rebuild after vacuum\n",
            text=True, capture_output=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("## Evidence", open(card, encoding="utf-8").read())
        # reindex → the observed event folds confidence above the 0.40 cold start.
        conn = index_impl.init_db(db)
        bumped = os.stat(card).st_mtime_ns + 10 ** 9
        os.utime(card, ns=(bumped, bumped))
        index_impl.run_incremental(conn, [card])
        conf = conn.execute("SELECT confidence FROM memory_chunks WHERE name='indexer-rule'").fetchone()[0]
        conn.close()
        self.assertGreater(conf, 0.40)
        self.assertAlmostEqual(conf, 0.45)  # 0.40 + observed(+0.05)


class DecayEmissionTest(unittest.TestCase):
    """§4.3 — decay rides the age_stale clock, managed & non-feedback & >0.55."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ev-decay-")
        self.mem = os.path.join(self.tmp, "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")
        self.drift = os.path.join(self.tmp, "db", "drift_state.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, fn, text):
        p = os.path.join(self.mem, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _seed_stale(self, drift_conn, path, first_seen=2):
        drift_conn.execute(
            "INSERT OR IGNORE INTO drift_findings "
            "(path, drift_type, detail, memory_type, detected_at, first_seen)"
            " VALUES (?,?,?,?,?,?)",
            (path, "age_stale", "stale", "project", "2026-07-01T00:00:00Z", first_seen))
        drift_conn.commit()

    def _emit(self, paths):
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, paths)
        drift_conn = drift_check.init_drift_db(self.drift)
        for p in paths:
            self._seed_stale(drift_conn, p)
        n = drift_check.emit_decay_events(conn, drift_conn)
        conn.close()
        drift_conn.close()
        return n

    def test_managed_above_floor_decays_once_and_folds_down(self):
        # a managed project card earned confidence 0.60 (confirmed) → decays to 0.50.
        card = self._write("earned.md", _card(
            "earned", type_="project", source="agent-extracted", last_verified=OLD,
            evidence_lines=["2026-06-01 · confirmed · user-explicit · Δ+0.20 · \"ok\""]))
        self.assertEqual(self._emit([card]), 1)
        _m, b = index_impl.parse_frontmatter(open(card, encoding="utf-8").read())
        self.assertIn("decayed", [e["event_type"] for e in index_impl.parse_evidence_events(b)])
        # idempotent — a second run does not stack another decay.
        self.assertEqual(self._emit([card]), 0)
        # reindex: fold(0.40, [confirmed, decayed]) = max(0.55, 0.60-0.10) = 0.55
        conn = index_impl.init_db(self.db)
        bumped = os.stat(card).st_mtime_ns + 10 ** 9
        os.utime(card, ns=(bumped, bumped))
        index_impl.run_incremental(conn, [card])
        conf = conn.execute("SELECT confidence FROM memory_chunks WHERE name='earned'").fetchone()[0]
        conn.close()
        self.assertAlmostEqual(conf, 0.55)

    def test_feedback_and_low_confidence_never_decay(self):
        fb = self._write("rule.md", _card("rule", type_="feedback", source="user-explicit",
                                          last_verified=OLD))  # feedback = timeless
        lo = self._write("lo.md", _card("lo", type_="project", source="agent-extracted",
                                        last_verified=OLD))    # cold 0.40 <= 0.55
        self.assertEqual(self._emit([fb, lo]), 0)
        self.assertNotIn("## Evidence", open(fb, encoding="utf-8").read())
        self.assertNotIn("## Evidence", open(lo, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
