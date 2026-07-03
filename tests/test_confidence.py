"""STEP 1B — confidence lifecycle foundation (spec §3–§5).

Covers the pure fold algebra (§4–§5), the cold-start table (§3.4), the schema +
migration incl. the OBS-1 stamp bump (§3.2, §3.4), and the `## Evidence`
markdown-is-truth projection (§3.2). unittest so it runs under pytest +
`unittest discover`.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import confidence as C  # noqa: E402
import index_impl  # noqa: E402

FRESH = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")


def _card(name, type_="project", source="user-explicit", card_kind="", evidence_lines=None,
          history_lines=None, extra_meta=""):
    kind = f"\n  card_kind: {card_kind}" if card_kind else ""
    body = "The flarnpuzzle rotation policy requires daily rotation of keys.\n"
    if history_lines:
        body += "\n## History\n\n" + "".join(f"- {l}\n" for l in history_lines)
    if evidence_lines:
        body += "\n## Evidence\n\n" + "".join(f"- {l}\n" for l in evidence_lines)
    return f"""---
name: {name}
description: test card {name}
metadata:
  type: {type_}
  source: {source}
  last_verified: {FRESH}{kind}{extra_meta}
---

{body}"""


class PureFoldTest(unittest.TestCase):
    """§4–§5 pure algebra — property style."""

    def test_cold_start_values(self):  # §3.4
        self.assertEqual(C.cold_start_confidence("feedback", "user-explicit", ""), 0.70)
        self.assertEqual(C.cold_start_confidence("feedback", "agent-extracted", ""), 0.70)
        self.assertEqual(C.cold_start_confidence("project", "user-explicit", ""), 0.80)
        self.assertEqual(C.cold_start_confidence("project", "agent-extracted", ""), 0.40)

    def test_is_managed_scope(self):  # §2.3
        self.assertTrue(C.is_managed("feedback", "user-explicit", ""))
        self.assertTrue(C.is_managed("project", "agent-extracted", "finding"))
        self.assertFalse(C.is_managed("user", "user-explicit", ""))
        self.assertFalse(C.is_managed("reference", "user-explicit", "concept"))
        self.assertFalse(C.is_managed("project", "imported", ""))
        self.assertFalse(C.is_managed("project", "user-explicit", ""))  # not in the managed table

    def test_conf_weight_bounds_and_exempt(self):  # §5.2
        self.assertAlmostEqual(C.conf_weight(0.05, True), 0.3825, places=4)
        self.assertAlmostEqual(C.conf_weight(0.95, True), 0.9675, places=4)
        self.assertEqual(C.conf_weight(0.95, False), 1.0)  # exempt
        for c in (0.05, 0.4, 0.7, 0.95):
            self.assertLess(C.conf_weight(c, True), 1.0)  # managed always < 1 (never up-ranks)

    def test_fold_empty_is_cold_start(self):
        self.assertAlmostEqual(C.fold_confidence(0.70, [])[0], 0.70)
        self.assertAlmostEqual(C.fold_confidence(0.40, [])[0], 0.40)

    def test_fold_is_deterministic_replay(self):
        evs = [{"event_type": "observed"}, {"event_type": "confirmed", "actor_tier": 3},
               {"event_type": "observed"}]
        a = C.fold_confidence(0.40, evs)[0]
        b = C.fold_confidence(0.40, list(evs))[0]
        self.assertEqual(a, b)

    def test_fold_bounds_clamped(self):
        hi = C.fold_confidence(0.90, [{"event_type": "confirmed", "actor_tier": 3}] * 10)[0]
        self.assertLessEqual(hi, C.CONF_MAX)
        lo = C.fold_confidence(0.40, [{"event_type": "corrected", "actor_tier": 3}] * 10)[0]
        self.assertGreaterEqual(lo, C.CONF_MIN)

    def test_agent_cannot_self_promote_past_gate(self):  # §4.2
        conf = C.fold_confidence(0.40, [{"event_type": "observed"}] * 10)[0]
        self.assertLessEqual(conf, 0.50)
        self.assertLess(conf, C.INJECT_GATE)
        self.assertFalse(C.injected(conf, managed=True))

    def test_ten_agent_observes_lose_to_one_user_correction(self):  # §4.4
        evs = [{"event_type": "observed"}] * 10 + [{"event_type": "corrected", "actor_tier": 3}]
        conf = C.fold_confidence(0.40, evs)[0]
        self.assertLess(conf, C.INJECT_GATE)
        self.assertFalse(C.injected(conf, managed=True))

    def test_low_authority_tier2_gated_by_user_highwater(self):  # §4.4
        # user-authored card at 0.80; a tier-2 `contradicted` cannot lower it
        # below the tier-3 high-water mark, and surfaces a relation_claim flag.
        conf, flags = C.fold_confidence(
            0.80, [{"event_type": "contradicted", "actor_tier": 2}], user_authored=True)
        self.assertAlmostEqual(conf, 0.80)
        self.assertIn("relation_claim", flags)
        # a tier-3 (user) contradiction MAY push below the mark.
        conf3, _ = C.fold_confidence(
            0.80, [{"event_type": "contradicted", "actor_tier": 3}], user_authored=True)
        self.assertLess(conf3, 0.80)

    def test_tier1_never_lowers(self):
        base = C.fold_confidence(0.60, [])[0]
        after = C.fold_confidence(0.60, [{"event_type": "observed"}])[0]
        self.assertGreaterEqual(after, base)

    def test_decay_only_above_floor_and_never_below(self):  # §4.3
        self.assertAlmostEqual(C.fold_confidence(0.80, [{"event_type": "decayed"}])[0], 0.70)
        self.assertAlmostEqual(C.fold_confidence(0.55, [{"event_type": "decayed"}])[0], 0.55)
        self.assertAlmostEqual(C.fold_confidence(0.50, [{"event_type": "decayed"}])[0], 0.50)


class MigrationTest(unittest.TestCase):
    """Schema, cold-start materialization, OBS-1 stamp bump, evidence projection."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-1b-")
        self.mem = os.path.join(self.tmp, "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, filename, text):
        path = os.path.join(self.mem, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _conf(self, conn, name):
        return conn.execute(
            "SELECT confidence, lifecycle FROM memory_chunks WHERE name = ?", (name,)
        ).fetchone()

    def test_schema_has_card_events_and_lifecycle(self):
        conn = index_impl.init_db(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_chunks)")}
        self.assertIn("lifecycle", cols)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("card_events", tables)
        conn.close()

    def test_cold_start_materialized_for_managed_cards(self):  # §3.4
        files = [
            self._write("rule.md", _card("rule", type_="feedback", source="user-explicit")),
            self._write("agent-note.md", _card("agent-note", type_="project", source="agent-extracted")),
            self._write("profile.md", _card("profile", type_="user", source="user-explicit")),
        ]
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, files)
        self.assertAlmostEqual(self._conf(conn, "rule")[0], 0.70)
        self.assertEqual(self._conf(conn, "rule")[1], "managed")
        self.assertAlmostEqual(self._conf(conn, "agent-note")[0], 0.40)
        self.assertEqual(self._conf(conn, "agent-note")[1], "managed")
        self.assertEqual(self._conf(conn, "profile")[1], "exempt")  # user profile = exempt
        conn.close()

    def test_evidence_markdown_is_truth(self):  # §3.2 / §9.3
        # A managed agent card with a user `confirmed` event → confidence folds
        # above cold-start; the card_events projection matches the markdown.
        ev = ["2026-07-01 · confirmed · user-explicit · sess=ab12 · Δ+0.20 · \"user re-affirmed\""]
        f = self._write("earned.md", _card("earned", type_="project",
                                            source="agent-extracted", evidence_lines=ev))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [f])
        self.assertAlmostEqual(self._conf(conn, "earned")[0], 0.60)  # 0.40 + 0.20 confirmed
        rows = conn.execute(
            "SELECT event_type, actor_tier FROM card_events WHERE card_slug='earned'").fetchall()
        self.assertEqual(rows, [("confirmed", 3)])
        # Delete the projection row; a reindex recovers it from the markdown (truth).
        conn.execute("DELETE FROM card_events")
        conn.commit()
        bumped = os.stat(f).st_mtime_ns + 10 ** 9
        os.utime(f, ns=(bumped, bumped))
        index_impl.run_incremental(conn, [f])
        rows2 = conn.execute("SELECT event_type FROM card_events WHERE card_slug='earned'").fetchall()
        self.assertEqual(rows2, [("confirmed",)])
        conn.close()

    def test_migration_no_double_count_ignores_history(self):  # risk #5
        # Legacy `## History` date-lines must NOT be parsed as events.
        hist = ["2026-07-01: confirmed something in history prose"]
        f = self._write("h.md", _card("h", type_="project", source="agent-extracted",
                                       history_lines=hist))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [f])
        self.assertAlmostEqual(self._conf(conn, "h")[0], 0.40)  # cold-start, no events counted
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM card_events").fetchone()[0], 0)
        conn.close()

    def test_incremental_delete_clears_card_events_no_orphan(self):  # audit NEW-2
        # A deleted event-bearing card must not orphan card_events rows on an
        # INCREMENTAL reindex — else the doctor reports a PERSISTENT divergence
        # until a --full. After the delete: no orphan, doctor quiet, and the
        # projection equals a full rebuild of the surviving corpus.
        gone = self._write("gone.md", _card("gone", type_="project", source="agent-extracted",
                           evidence_lines=["2026-07-01 · observed · agent-extracted · Δ+0.05 · \"g\""]))
        keep = self._write("keep.md", _card("keep", type_="project", source="agent-extracted",
                           evidence_lines=["2026-07-02 · observed · agent-extracted · Δ+0.05 · \"k\""]))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [gone, keep])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM card_events WHERE path=?", (gone,)).fetchone()[0], 1)

        os.remove(gone)  # delete the card file
        _i, _s, removed = index_impl.run_incremental(conn, [keep])
        self.assertEqual(removed, 1)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM card_events WHERE path=?", (gone,)).fetchone()[0], 0,
            "orphan card_events rows after an incremental delete (NEW-2)")
        # doctor QUIET, and the projection == a full rebuild of the survivors.
        self.assertEqual(index_impl.check_evidence_divergence(conn), [])
        rows = conn.execute("SELECT path, event_type FROM card_events").fetchall()
        conn.close()
        self.assertEqual(rows, [(keep, "observed")])

    def test_evidence_divergence_doctor_check(self):  # audit F2 / §3.2
        # A consistent store reports no divergence; a tampered projection is
        # surfaced LOUDLY (non-zero problem count) — markdown wins.
        f = self._write("d.md", _card("d", type_="project", source="agent-extracted",
                        evidence_lines=["2026-07-01 · observed · agent-extracted · Δ+0.05 · \"x\""]))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [f])
        self.assertEqual(index_impl.check_evidence_divergence(conn), [])
        conn.execute(
            "INSERT INTO card_events (path, card_slug, project_hash, ts, event_type, actor_tier, delta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (f, "d", "", "2099-01-01", "confirmed", 3, 0.2))
        conn.commit()
        problems = index_impl.check_evidence_divergence(conn)
        conn.close()
        self.assertEqual(len(problems), 1)
        self.assertIn("card_events diverges", problems[0])
        self.assertIn("projection-only=1", problems[0])

    def test_same_slug_cards_do_not_collide_in_projection(self):  # audit F3
        # Two distinct files whose name: normalizes to the same slug in one
        # project must NOT collide in card_events — the projection is keyed by
        # the card's file path (the fold's per-card identity), so the second
        # card's index cannot DELETE the first's events.
        a = self._write("a.md", _card("Shared Rule", type_="project", source="agent-extracted",
                                      evidence_lines=["2026-07-01 · observed · agent-extracted · Δ+0.05 · \"a\""]))
        b = self._write("b.md", _card("Shared-Rule", type_="project", source="agent-extracted",
                                      evidence_lines=["2026-07-02 · confirmed · user-explicit · Δ+0.20 · \"b\""]))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [a, b])
        rows = dict(conn.execute("SELECT path, event_type FROM card_events ORDER BY path").fetchall())
        conn.close()
        self.assertEqual(set(rows), {a, b}, "same-slug cards collided in card_events (F3)")
        self.assertEqual(rows[a], "observed")
        self.assertEqual(rows[b], "confirmed")

    def test_f3_migrates_old_projection_schema_and_repopulates_once(self):
        # An existing 1B store (card_events keyed by project_hash+slug, stamped
        # backfill_v6b) migrates to the per-path schema and repopulates exactly
        # once on the next run.
        a = self._write("m.md", _card("m", type_="project", source="agent-extracted",
                        evidence_lines=["2026-07-01 · observed · agent-extracted · Δ+0.05 · \"x\""]))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [a])
        conn.execute("DROP TABLE card_events")
        conn.execute(
            "CREATE TABLE card_events (card_slug TEXT NOT NULL, project_hash TEXT NOT NULL, "
            "ts TEXT NOT NULL, event_type TEXT NOT NULL, actor_tier INTEGER NOT NULL, "
            "session_id TEXT, delta REAL NOT NULL, note TEXT, "
            "PRIMARY KEY (project_hash, card_slug, ts, event_type))")
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (index_impl.BACKFILL_STAMP_KEY,))
        conn.execute("INSERT INTO schema_meta(key, value) VALUES('backfill_v6b', 'done')")
        conn.commit()
        conn.close()

        conn = index_impl.init_db(self.db)  # _migrate_card_events drops old schema
        cols = {r[1] for r in conn.execute("PRAGMA table_info(card_events)")}
        self.assertIn("path", cols)
        index_impl.run_incremental(conn, [a])  # forced re-read repopulates
        rows = conn.execute("SELECT path, event_type FROM card_events").fetchall()
        conn.close()
        self.assertEqual(rows, [(a, "observed")])

    def test_obs1_step1_stamped_store_backfills_once_stable_rowids(self):
        # A store stamped by STEP 1 (backfill_v6) but lacking the 1B columns must
        # back-fill EXACTLY once when the 1B code runs, then never re-index again.
        files = [
            self._write("rule.md", _card("rule", type_="feedback", source="user-explicit")),
            self._write("agent-note.md", _card("agent-note", type_="project", source="agent-extracted")),
        ]
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, files)
        # Downgrade to a STEP-1 state: old stamp key, 1B columns blank.
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (index_impl.BACKFILL_STAMP_KEY,))
        conn.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('backfill_v6', 'done')")
        conn.execute("UPDATE memory_chunks SET lifecycle = '', confidence = 0.7")
        conn.commit()
        conn.close()

        # 1B run #1 (fresh init, like a new process) must back-fill once.
        conn = index_impl.init_db(self.db)
        i1, _s1, _r1 = index_impl.run_incremental(conn, files)
        ids1 = dict(conn.execute("SELECT path, id FROM memory_chunks").fetchall())
        self.assertGreater(i1, 0, "1B back-fill did not re-read a STEP-1-stamped store")
        self.assertAlmostEqual(self._conf(conn, "rule")[0], 0.70)  # re-materialized
        self.assertEqual(self._conf(conn, "rule")[1], "managed")
        self.assertEqual(
            conn.execute("SELECT value FROM schema_meta WHERE key = ?",
                         (index_impl.BACKFILL_STAMP_KEY,)).fetchone()[0], "done")
        conn.close()

        # 1B run #2 must skip everything (back-fill fired once) with STABLE rowids.
        conn = index_impl.init_db(self.db)
        i2, s2, _r2 = index_impl.run_incremental(conn, files)
        ids2 = dict(conn.execute("SELECT path, id FROM memory_chunks").fetchall())
        conn.close()
        self.assertEqual(i2, 0, "1B back-fill fired more than once (OBS-1)")
        self.assertEqual(s2, len(files))
        self.assertEqual(ids1, ids2, "chunk rowids churned after the one-time 1B back-fill")


if __name__ == "__main__":
    unittest.main()
