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

# A faithful v5.13.1 (base a78af37) memory_chunks schema: has the relation
# columns but NOT the branch-new back-fill columns (*_explicit/status_explicit).
LEGACY_SCHEMA = """
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL, project TEXT, name TEXT, type TEXT,
    evidence TEXT DEFAULT 'observed', source TEXT DEFAULT 'user-explicit',
    confidence REAL DEFAULT 0.7, last_verified TEXT, card_kind TEXT DEFAULT '',
    status TEXT DEFAULT 'current', area TEXT DEFAULT '', supersedes TEXT DEFAULT '',
    superseded_by TEXT DEFAULT '', contradicts TEXT DEFAULT '',
    contradicted_by TEXT DEFAULT '', section_heading TEXT, content TEXT NOT NULL,
    description TEXT, mtime INTEGER, UNIQUE(path, section_heading)
);
CREATE VIRTUAL TABLE memory_fts USING fts5(
    name, description, section_heading, content,
    content=memory_chunks, content_rowid=id, tokenize='porter unicode61');
CREATE TRIGGER memory_chunks_ai AFTER INSERT ON memory_chunks BEGIN
    INSERT INTO memory_fts(rowid, name, description, section_heading, content)
    VALUES (new.id, new.name, new.description, new.section_heading, new.content);
END;
CREATE TABLE index_meta (path TEXT PRIMARY KEY, mtime INTEGER);
"""


def _card(name, relations="", source="user-explicit"):
    rel_block = f"\n  {relations}" if relations else ""
    return f"""---
name: {name}
description: test card {name}
metadata:
  type: project
  evidence: observed
  source: {source}
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

    # --- v5.13.1 audit regressions ------------------------------------------

    def _write(self, filename, text, directory=None):
        directory = directory or self.mem
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _contradicted_by(self, conn, name):
        row = conn.execute(
            "SELECT contradicted_by FROM memory_chunks WHERE name = ?", (name,)
        ).fetchone()
        return row[0]

    def test_removed_declaration_clears_on_incremental_reindex(self):
        # Sticky propagation (fill-empty-only) kept the 0.4x penalty forever
        # after the contradicts: line was deleted, until a --full rebuild.
        old = self._write("old-rule.md", _card("old-rule"))
        new = self._write("new-rule.md", _card("new-rule", "contradicts: old-rule"))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [old, new])
        self.assertEqual(self._contradicted_by(conn, "old-rule"), "new-rule")

        self._write("new-rule.md", _card("new-rule"))
        bumped = os.stat(new).st_mtime_ns + 10**9  # deterministic mtime change
        os.utime(new, ns=(bumped, bumped))
        index_impl.run_incremental(conn, [old, new])
        self.assertEqual(self._contradicted_by(conn, "old-rule"), "")
        self.assertEqual(drift_check.check_declared_contradictions(conn), [])
        conn.close()

    def test_cross_project_same_slug_does_not_contaminate(self):
        # Bare targets resolve only inside the declarer's project: a
        # `contradicts: old-rule` in proj-a must not mark proj-b's old-rule.
        proj_a = os.path.join(self.tmp, ".claude", "projects", "proj-a", "memory")
        proj_b = os.path.join(self.tmp, ".claude", "projects", "proj-b", "memory")
        files = [
            self._write("old-rule.md", _card("old-rule"), proj_a),
            self._write("old-rule.md", _card("old-rule"), proj_b),
            self._write("new-rule.md", _card("new-rule", "contradicts: old-rule"), proj_a),
        ]
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, files)
        rows = dict(conn.execute(
            "SELECT DISTINCT path, contradicted_by FROM memory_chunks WHERE name = 'old-rule'"
        ).fetchall())
        conn.close()
        self.assertEqual(rows[files[0]], "new-rule")
        self.assertEqual(rows[files[1]], "", "cross-project same-slug card was contaminated")

    def test_path_qualified_target_does_not_nuke_other_project(self):
        # KEEP #1: a PATH-QUALIFIED `contradicts: notes/methodology.md` declared
        # in proj-a must resolve ONLY inside proj-a. The pre-fix path-suffix
        # match (`endswith("/notes/methodology")`) matched the same-suffix card
        # in ANY project, rank-nuking proj-b's identically-named card. Identity
        # is (project, normalized name) — never a bare suffix match across
        # projects. proj-b's card must be byte- and rank-identical (untouched).
        proj_a = os.path.join(self.tmp, ".claude", "projects", "proj-a", "memory")
        proj_b = os.path.join(self.tmp, ".claude", "projects", "proj-b", "memory")
        files = [
            self._write("methodology.md", _card("methodology"),
                        os.path.join(proj_a, "notes")),
            self._write("methodology.md", _card("methodology"),
                        os.path.join(proj_b, "notes")),
            self._write("rules.md", _card("rules", "contradicts: notes/methodology.md"),
                        proj_a),
        ]
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, files)
        rows = dict(conn.execute(
            "SELECT DISTINCT path, contradicted_by FROM memory_chunks WHERE name = 'methodology'"
        ).fetchall())
        conn.close()
        # proj-a's methodology IS the real target of the same-project declaration.
        self.assertEqual(rows[files[0]], "rules")
        # proj-b's same-suffix card in ANOTHER project must be untouched.
        self.assertEqual(rows[files[1]], "",
                         "path-qualified target nuked a same-suffix card in another project")

    def _status(self, conn, name):
        return conn.execute(
            "SELECT status FROM memory_chunks WHERE name = ?", (name,)
        ).fetchone()[0]

    def test_propagated_supersession_recomputes_derived_status_each_incremental(self):
        # KEEP #4: DERIVED state (not just the relation columns) must recompute
        # from CURRENT declarations on EVERY incremental reindex. A card
        # superseded by ANOTHER card's `supersedes:` must have its derived
        # `status` become 'superseded' incrementally, and revert to 'current'
        # when the declaration is removed — clear-when-removed for derived state.
        b = self._write("b-card.md", _card("b-card"))
        a = self._write("a-card.md", _card("a-card", "supersedes: b-card"))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [a, b])
        self.assertEqual(self._status(conn, "b-card"), "superseded",
                         "propagated supersession did not update the derived status")

        self._write("a-card.md", _card("a-card"))
        bump = os.stat(a).st_mtime_ns + 10**9  # deterministic mtime change
        os.utime(a, ns=(bump, bump))
        index_impl.run_incremental(conn, [a, b])
        self.assertEqual(self._status(conn, "b-card"), "current",
                         "derived status stayed 'superseded' after the declaration was removed")
        conn.close()

    def test_explicit_status_current_does_not_defeat_propagated_supersession(self):
        # audit F4: `status: current` is the non-authoritative DEFAULT. A card
        # carrying it that is superseded by ANOTHER card's `supersedes:` must
        # still derive status='superseded' — literal 'current' must not block it.
        c = self._write("c-current.md", _card("c-current", "status: current"))
        d = self._write("d-super.md", _card("d-super", "supersedes: c-current"))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [c, d])
        row = conn.execute(
            "SELECT superseded_by, status FROM memory_chunks WHERE name='c-current'").fetchone()
        conn.close()
        self.assertEqual(row[0], "d-super")
        self.assertEqual(row[1], "superseded",
                         "explicit 'status: current' wrongly defeated the propagated supersession")

    def test_propagation_never_overrides_explicit_status(self):
        # An explicit `status:` must survive propagated supersession — derived
        # recompute must respect the (project-authored) explicit value.
        b = self._write("b-card.md", _card("b-card", "status: archived"))
        a = self._write("a-card.md", _card("a-card", "supersedes: b-card"))
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [a, b])
        self.assertEqual(self._status(conn, "b-card"), "archived")
        conn.close()

    def _legacy_row(self, path, name, status, superseded_by):
        return (
            "INSERT INTO memory_chunks (path, project, name, type, evidence, source,"
            " last_verified, card_kind, status, area, supersedes, superseded_by,"
            " contradicts, contradicted_by, section_heading, content, description, mtime)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (path, "", name, "project", "observed", "user-explicit", FRESH, "finding",
             status, "", "", superseded_by, "", "", name, BODY, "", index_impl.file_mtime(path)),
        )

    def test_reader_first_migration_preserves_demotion(self):
        # audit F1: a search (reader) that touches a freshly-upgraded legacy DB
        # BEFORE the first writer-incremental must NOT defeat the *_explicit
        # back-fill and let propagation erase a deliberate demotion. Two demoted
        # cards: an own-frontmatter supersession (0.35) and an explicit archive
        # (0.25). Both must SURVIVE the reader→writer-incremental sequence.
        sup = self._write("self-super.md", _card("self-super", "superseded_by: newer-card"))
        arch = self._write("shelved.md", _card("shelved", "status: archived"))

        # 1) Build the legacy v5.13.1 DB with both cards indexed and index_meta
        #    mtimes matching the files (so a plain incremental would SKIP them).
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        legacy = sqlite3.connect(self.db)
        legacy.executescript(LEGACY_SCHEMA)
        legacy.execute(*self._legacy_row(sup, "self-super", "superseded", "newer-card"))
        legacy.execute(*self._legacy_row(arch, "shelved", "archived", ""))
        for p in (sup, arch):
            legacy.execute("INSERT INTO index_meta (path, mtime) VALUES (?, ?)",
                           (p, index_impl.file_mtime(p)))
        legacy.commit()
        legacy.close()

        # 2) READER FIRST: a search opens the upgraded DB and runs its migration.
        rconn = sqlite3.connect(self.db)
        si.ensure_agent_columns(rconn)
        rconn.commit()
        rconn.close()

        # 3) WRITER incremental, exactly as the next index run does.
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [sup, arch])
        sup_row = conn.execute(
            "SELECT superseded_by, status FROM memory_chunks WHERE name='self-super'").fetchone()
        arch_status = self._status(conn, "shelved")
        conn.close()

        self.assertEqual(sup_row[0], "newer-card",
                         "reader-first migration erased the superseded_by demotion")
        self.assertEqual(sup_row[1], "superseded")
        self.assertEqual(arch_status, "archived",
                         "reader-first migration un-archived a deliberately archived card")

    def test_low_trust_declarer_cannot_downrank_user_explicit_target(self):
        # Authority gate: an agent-extracted card contradicting a NEWER-tier
        # user-explicit card must not apply the 0.4x penalty — the claim is
        # surfaced as a non-penalizing relation_claim finding instead.
        conn = self._index([
            ("old-rule.md", _card("old-rule")),
            ("sneaky.md", _card("sneaky", "contradicts: old-rule", source="agent-extracted")),
        ])
        self.assertEqual(self._contradicted_by(conn, "old-rule"), "")
        self.assertEqual(drift_check.check_declared_contradictions(conn), [])
        diags = drift_check.check_relation_diagnostics(conn)
        conn.close()
        claims = [d for d in diags if d[2] == "relation_claim"]
        self.assertEqual(len(claims), 1)
        self.assertTrue(claims[0][0].endswith("old-rule.md"))
        self.assertIn("by=sneaky", claims[0][3])
        # relation_claim must be diagnostics-only: penalty 1.0, not declared.
        from constants import DRIFT_PENALTIES, DECLARED_DRIFT_TYPES
        self.assertEqual(DRIFT_PENALTIES["relation_claim"], 1.0)
        self.assertNotIn("relation_claim", DECLARED_DRIFT_TYPES)

    def test_unresolved_target_surfaces_diagnostic_finding(self):
        # A typo'd/deleted target used to silently no-op; it must surface as
        # an unresolved_relation finding on the DECLARER.
        conn = self._index([
            ("new-rule.md", _card("new-rule", "contradicts: no-such-card")),
        ])
        diags = drift_check.check_relation_diagnostics(conn)
        conn.close()
        unresolved = [d for d in diags if d[2] == "unresolved_relation"]
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(unresolved[0][0].endswith("new-rule.md"))
        self.assertIn("no-such-card", unresolved[0][3])

    def test_target_resolution_normalizes_md_suffix_and_case(self):
        conn = self._index([
            ("old-rule.md", _card("old-rule")),
            ("new-rule.md", _card("new-rule", "contradicts: Old-Rule.md")),
        ])
        contradicted = self._contradicted_by(conn, "old-rule")
        conn.close()
        self.assertEqual(contradicted, "new-rule")

    def test_multiple_contradictors_recorded_deterministically(self):
        # Fill-first-only dropped every contradictor after the first; all
        # current declarers must be recorded, sorted, so the surfaced finding
        # detail is stable across runs.
        conn = self._index([
            ("old-rule.md", _card("old-rule")),
            ("challenger-b.md", _card("challenger-b", "contradicts: old-rule")),
            ("challenger-a.md", _card("challenger-a", "contradicts: old-rule")),
        ])
        contradicted = self._contradicted_by(conn, "old-rule")
        findings = drift_check.check_declared_contradictions(conn)
        conn.close()
        self.assertEqual(contradicted, "challenger-a, challenger-b")
        self.assertEqual(findings[0][3], "by=challenger-a, challenger-b")

    def test_missing_explicit_relation_column_surfaces_not_silent(self):
        # KEEP #2: the *_explicit columns are load-bearing for truth-maintenance.
        # When they are absent/malformed, compute_relation_state raises
        # OperationalError and the pre-fix propagate_declared_relations SWALLOWED
        # it and skipped ALL propagation with zero signal — a silent no-op. A
        # missing/malformed explicit-relation column must SURFACE (warning),
        # never silently disable truth-maintenance, and must not crash.
        import contextlib
        import io

        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        conn = sqlite3.connect(self.db)
        # A memory_chunks table WITHOUT the *_explicit columns (a pre-migration
        # or corrupted schema).
        conn.execute("""
            CREATE TABLE memory_chunks (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL, project TEXT, name TEXT,
                type TEXT, source TEXT DEFAULT 'user-explicit',
                evidence TEXT DEFAULT 'observed', mtime INTEGER,
                supersedes TEXT DEFAULT '', contradicts TEXT DEFAULT '',
                superseded_by TEXT DEFAULT '', contradicted_by TEXT DEFAULT '',
                section_heading TEXT, content TEXT NOT NULL,
                UNIQUE(path, section_heading)
            )""")
        conn.commit()

        # (a) the schema check surfaces BOTH missing columns.
        problems = index_impl.check_relation_schema(conn)
        self.assertTrue(any("superseded_by_explicit" in p for p in problems), problems)
        self.assertTrue(any("contradicted_by_explicit" in p for p in problems), problems)

        # (b) propagation surfaces a warning to stderr instead of silently
        #     no-oping, and does not raise.
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            index_impl.propagate_declared_relations(conn)  # must not raise
        conn.close()
        err = buf.getvalue().lower()
        self.assertIn("relation", err, f"no surfaced warning: {err!r}")
        self.assertIn("skip", err, f"warning did not report the skip: {err!r}")

    def test_healthy_relation_schema_reports_no_problems(self):
        # The surfacing check must NOT false-positive on a correctly migrated DB.
        conn = index_impl.init_db(self.db)
        problems = index_impl.check_relation_schema(conn)
        conn.close()
        self.assertEqual(problems, [])

    def test_bulk_declared_resolve_trips_the_safety_valve(self):
        # Declared findings penalize from first_seen=1, so a bulk
        # disappearance of them (possible detector regression) must be
        # blocked by auto_resolve's safety valve like any penalized finding.
        os.makedirs(os.path.join(self.tmp, "db"), exist_ok=True)
        drift_conn = drift_check.init_drift_db(os.path.join(self.tmp, "db", "drift_state.db"))
        for i in range(8):
            drift_conn.execute(
                "INSERT INTO drift_findings (path, drift_type, detail, memory_type,"
                " detected_at, first_seen) VALUES (?,?,?,?,?,1)",
                (f"{self.mem}/card-{i}.md", "contradicted", f"by=other-{i}",
                 "project", "2026-07-01T00:00:00Z"),
            )
        drift_conn.commit()
        resolved = drift_check.auto_resolve(drift_conn, [])
        active = drift_conn.execute(
            "SELECT COUNT(*) FROM drift_findings WHERE resolved_at IS NULL"
        ).fetchone()[0]
        drift_conn.close()
        self.assertEqual(resolved, 0, "bulk declared resolve bypassed the safety valve")
        self.assertEqual(active, 8)


if __name__ == "__main__":
    unittest.main()
