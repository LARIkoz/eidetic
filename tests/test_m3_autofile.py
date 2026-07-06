"""M3 auto-file (spec-m3-autofile turn-1: FR-1 dedup, FR-2 claim-support gate,
FR-3 file-at-0.40, FR-7 dark-safe; ACs 1/2/8 — the laundering-critical core).

Hermetic, both legs, temp DBs only (the live ~/.claude store is NEVER touched —
every filing target is an explicit temp `memory_dir`). The support scorer and the
dedup neighbors are INJECTED so the pipeline is deterministic without a model:
the DEFAULT support scorer is the LLM-free deterministic span-overlap, so both
legs (vectored + FTS-only) exercise the gate identically.

Each safety AC bakes in its revert-verify:
  * AC-1: the real gate REJECTS an unsupported answer (no page, no event); a
    pass-through support scorer (the gate disabled) WOULD file it — RED on break.
  * AC-2: a supported answer files at EXACTLY 0.40 with an EMPTY `## Evidence`
    log; a version that seeds an `observed` (→0.45) or files at ≥0.55 fails.
  * AC-8: with EIDETIC_CONFIDENCE_EVENTS off M3 is a COMPLETE no-op (no page, no
    event); `engine.require("1")` still passes; runs on BOTH legs.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import confidence as C  # noqa: E402
import index_impl  # noqa: E402
import m3_autofile as m3  # noqa: E402


def _prov(answer, spans, query="the recall query", session="sess-1"):
    """Build a typed M3 provenance record (the turn-1 input contract)."""
    return {
        "answer_text": answer,
        "sources": [{"card_id": f"card-{i}", "span": s} for i, s in enumerate(spans)],
        "recall_query": query,
        "session_id": session,
    }


class M3Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3-")
        self.mem = os.path.join(self.tmp, ".claude", "projects", "proj-a", "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")
        os.makedirs(os.path.dirname(self.db))
        os.environ["EIDETIC_CONFIDENCE_EVENTS"] = "on"
        os.environ["EIDETIC_M3_AUTOFILE"] = "on"  # dormant-by-default; opt in

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        os.environ.pop("EIDETIC_M3_AUTOFILE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self, p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def _index_conf(self, path):
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [path])
        name = index_impl.parse_frontmatter(self._read(path))[0].get("name")
        row = conn.execute("SELECT confidence FROM memory_chunks WHERE name=? LIMIT 1",
                           (name,)).fetchone()
        conn.close()
        return row[0]

    def _no_neighbors(self, db, probe, exclude=()):
        return []


# --- AC-2 supported novel answer files at 0.40, empty ## Evidence ------------
class SupportedFilesTest(M3Base):
    ANSWER = ("The auth service issues JWT access tokens for user sessions. "
              "Refresh tokens rotate the JWT session daily.")
    SPANS = ["The auth service issues JWT access tokens for sessions.",
             "Refresh tokens rotate the JWT session for the auth service daily."]

    def test_ac2_files_at_cold_start_empty_evidence(self):
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "filed")
        path = out["path"]
        self.assertTrue(os.path.exists(path))
        content = self._read(path)
        meta, body = index_impl.parse_frontmatter(content)
        # source = agent-extracted, managed lifecycle
        self.assertEqual(meta.get("source"), "agent-extracted")
        # EMPTY ## Evidence — no events written at filing
        self.assertEqual(index_impl.parse_evidence_events(body), [])
        # confidence EXACTLY 0.40, NOT 0.45 (no synthetic observed seed)
        conf = self._index_conf(path)
        self.assertAlmostEqual(conf, 0.40, places=6)
        self.assertNotAlmostEqual(conf, 0.45, places=6)
        # the built fold matches exactly
        self.assertAlmostEqual(C.fold_confidence(0.40, [])[0], 0.40, places=6)
        # un-injected: 0.40 < 0.55 inject gate; managed page not injected
        self.assertFalse(C.injected(conf, managed=True))
        self.assertLess(conf, C.INJECT_GATE)

    def test_ac2_no_promoting_event_minted_by_filing(self):
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        _m, body = index_impl.parse_frontmatter(self._read(out["path"]))
        evs = index_impl.parse_evidence_events(body)
        self.assertNotIn("confirmed", [e["event_type"] for e in evs])
        self.assertNotIn("observed", [e["event_type"] for e in evs])
        self.assertEqual(evs, [])  # filing alone mints NOTHING


# --- AC-1 unsupported answer REJECTED (no page, no event) --------------------
class UnsupportedRejectedTest(M3Base):
    # a claim NOT span-supported by its cited sources
    ANSWER = "The primary datastore is PostgreSQL running on port 9999."
    SPANS = ["The auth service issues JWT access tokens for user sessions."]

    def _files_before(self):
        return sorted(os.listdir(self.mem))

    def test_ac1_rejected_no_page_no_event(self):
        before = self._files_before()
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(out.get("reason"), "unsupported_claim")
        self.assertEqual(self._files_before(), before)  # NO page created

    def test_ac1_no_sources_rejected_outright(self):
        out = m3.file_recalled_answer(
            self.db, _prov("Any claim at all here.", []), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(out.get("reason"), "no_sources")
        self.assertEqual(os.listdir(self.mem), [])

    def test_ac1_revert_verify_gate_is_load_bearing(self):
        # REVERT-VERIFY: disabling the gate (a pass-through support scorer) FILES the
        # same unsupported answer → the gate is the only laundering barrier.
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors,
            support_fn=lambda claim, spans: 1.0)  # gate neutered
        self.assertEqual(out["action"], "filed", "revert-verify: no gate → it files")
        self.assertTrue(os.path.exists(out["path"]))


# --- FR-1 dedup: near-duplicate routes to M2, NO new page --------------------
class DedupRoutesToM2Test(M3Base):
    ANSWER = "The auth service issues JWT access tokens for user sessions."
    SPANS = ["The auth service issues JWT access tokens for sessions."]

    def test_near_duplicate_routes_to_m2_no_new_page(self):
        routed = {}

        def spy_handoff(db, prov, hits, top, **kw):
            routed["top"] = top
            routed["hits"] = hits
            return [{"action": "edited"}]

        # inject a near-duplicate neighbor above the dedup line
        hi = m3.dedup_min() + 0.02
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=lambda db, probe, excl=(): [{"score": hi, "path": "/x/dup.md"}],
            m2_handoff=spy_handoff)
        self.assertEqual(out["action"], "deduped_to_m2")
        self.assertEqual(routed["top"]["path"], "/x/dup.md")
        self.assertEqual(os.listdir(self.mem), [])  # NO new page filed

    def test_below_dedup_line_files_new_page(self):
        lo = m3.dedup_min() - 0.05
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=lambda db, probe, excl=(): [{"score": lo, "path": "/x/far.md"}])
        self.assertEqual(out["action"], "filed")

    def test_fts_only_no_neighbor_path_files(self):
        # the FTS-only leg: neighbors_via_door returns [] → no semantic dedup → file
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "filed")


# --- FR-2 support scorer (deterministic span-overlap, no LLM) ----------------
def _fastembed_available():
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None


# --- FR-1 dedup through the REAL S2 door (Leg A only) ------------------------
class DedupRealDoorTest(M3Base):
    @unittest.skipUnless(_fastembed_available(), "Leg-A dedup e2e requires fastembed")
    def test_real_door_routes_paraphrase_to_m2(self):
        import engine
        engine.configure(provider="cpu", threads=8)
        # an existing embedded page about auth tokens
        page = ("---\nname: auth-tokens\ntype: project\nsource: agent-extracted\n"
                "last_verified: 2026-06-01\n---\n\n"
                "The auth service issues JWT access tokens for user sessions.\n")
        p = os.path.join(self.mem, "auth-tokens.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(page)
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [p])
        conn.close()
        engine._embed().run_full(self.db, self.db.replace("index.db", "vectors.db"))

        # a near-paraphrase answer → cosine ≥ dedup line → route to M2, NO new page
        near = _prov("The auth service issues JWT access tokens for user sessions.",
                     ["The auth service issues JWT access tokens for user sessions."])
        out = m3.file_recalled_answer(self.db, near, memory_dir=self.mem)
        self.assertEqual(out["action"], "deduped_to_m2")
        self.assertEqual(out["neighbor"], p)
        self.assertNotIn("synthesis-", "".join(os.listdir(self.mem)))  # no new page

    @unittest.skipUnless(_fastembed_available(), "Leg-A dedup e2e requires fastembed")
    def test_real_door_files_far_topic(self):
        import engine
        engine.configure(provider="cpu", threads=8)
        page = ("---\nname: auth-tokens\ntype: project\nsource: agent-extracted\n"
                "last_verified: 2026-06-01\n---\n\n"
                "The auth service issues JWT access tokens for user sessions.\n")
        p = os.path.join(self.mem, "auth-tokens.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(page)
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [p])
        conn.close()
        engine._embed().run_full(self.db, self.db.replace("index.db", "vectors.db"))
        # a far-topic supported answer → no neighbor above the dedup line → files
        far = _prov("The office coffee machine broke again on Friday afternoon.",
                    ["The office coffee machine broke again on Friday afternoon."],
                    query="coffee machine status")
        out = m3.file_recalled_answer(self.db, far, memory_dir=self.mem)
        self.assertEqual(out["action"], "filed")


class SupportScorerTest(M3Base):
    def test_overlap_supported_vs_unsupported(self):
        s = m3.active_support()
        strong = s("The auth service issues JWT access tokens.",
                   ["The auth service issues JWT access tokens for sessions."])
        weak = s("The datastore is PostgreSQL on port 9999.",
                 ["The auth service issues JWT access tokens for sessions."])
        self.assertGreaterEqual(strong, m3.support_min())
        self.assertLess(weak, m3.support_min())

    def test_deterministic(self):
        s = m3.active_support()
        a = s("auth service tokens", ["auth service issues tokens"])
        b = s("auth service tokens", ["auth service issues tokens"])
        self.assertEqual(a, b)

    def test_material_claim_split(self):
        claims = m3._split_claims("Alpha beta gamma delta. Yes. Epsilon zeta eta theta.")
        material = [c for c in claims if m3._is_material(c)]
        self.assertEqual(len(material), 2)  # "Yes." is non-material filler


# --- FR-7 / AC-8 dark-safe: flag OFF → complete no-op ------------------------
class DarkSafeTest(M3Base):
    ANSWER = "The auth service issues JWT access tokens for user sessions."
    SPANS = ["The auth service issues JWT access tokens for sessions."]

    def test_ac8_events_off_is_complete_noop(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)  # OFF
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "noop")
        self.assertEqual(os.listdir(self.mem), [])  # NO page

    def test_ac8_m3_flag_off_is_complete_noop(self):
        os.environ.pop("EIDETIC_M3_AUTOFILE", None)  # M3 activation OFF
        self.assertFalse(m3.m3_enabled())
        out = m3.file_recalled_answer(
            self.db, _prov(self.ANSWER, self.SPANS), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "noop")
        self.assertEqual(os.listdir(self.mem), [])

    def test_ac8_require_v1_intact(self):
        import engine
        engine.require("1")
        self.assertEqual(engine.ENGINE_API, "1.1")


# ======================= TURN 2 (FR-4/5/6/8 — ACs 3/5/6/7) =================
# Supported, novel answer reused across the turn-2 ACs.
_SUP_ANSWER = "The auth service issues JWT access tokens for user sessions."
_SUP_SPANS = ["The auth service issues JWT access tokens for user sessions."]


def _events(path):
    return index_impl.parse_evidence_events(
        index_impl.parse_frontmatter(open(path, encoding="utf-8").read())[1])


# --- FR-4 promoting events (AC-3) --------------------------------------------
class FR4AffirmationTest(M3Base):
    def _file(self, query="auth token policy"):
        return m3.file_recalled_answer(
            self.db, _prov(_SUP_ANSWER, _SUP_SPANS, query=query), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)

    def _conf(self, path):
        return self._index_conf(path)

    def test_ac3_user_affirmation_lifts_to_0_60_injected(self):
        out = self._file()
        self.assertEqual(out["action"], "filed")
        path = out["path"]
        # filing ALONE mints nothing → still 0.40, un-injected (turn-1 invariant)
        self.assertAlmostEqual(self._conf(path), 0.40, places=6)
        self.assertEqual(_events(path), [])
        # a GENUINE in-session user affirmation referencing THIS page
        res = m3.affirm_filed_page(
            self.db, path,
            {"kind": "user_affirmation", "target": path, "session_id": "sess-1"})
        self.assertEqual(res["promoted"], "confirmed")
        evs = _events(path)
        self.assertEqual([e["event_type"] for e in evs], ["confirmed"])
        # fold: 0.40 + 0.20 = 0.60 ≥ 0.55 → INJECTED (the algebra working, not laundering)
        conf = self._conf(path)
        self.assertAlmostEqual(conf, 0.60, places=6)
        self.assertTrue(C.injected(conf, managed=True))

    def test_ac3_verified_by_test_lifts_tier2(self):
        out = self._file(query="cache eviction policy")
        res = m3.affirm_filed_page(
            self.db, out["path"],
            {"kind": "test_pass", "target": out["path"], "session_id": "sess-t"})
        self.assertEqual(res["promoted"], "verified_by_test")
        conf = self._conf(out["path"])
        self.assertAlmostEqual(conf, 0.55, places=6)   # 0.40 + 0.15
        self.assertTrue(C.injected(conf, managed=True))

    def test_ac3_one_call_affirmation_via_recall_query(self):
        # the ergonomic one-call form: affirmation targets the recall query identity
        out = m3.file_recalled_answer(
            self.db, _prov(_SUP_ANSWER, _SUP_SPANS, query="session timeout policy"),
            memory_dir=self.mem, neighbors_fn=self._no_neighbors,
            affirmation={"kind": "user_affirmation", "target": "session timeout policy",
                         "session_id": "sess-1"})
        self.assertEqual(out["action"], "filed")
        self.assertEqual(out["promotion"]["promoted"], "confirmed")
        self.assertAlmostEqual(self._conf(out["path"]), 0.60, places=6)

    def test_ac3_misattributed_affirmation_does_not_lift(self):
        # mis-attribution guard (§8 Breaks-when): an affirmation referencing a
        # DIFFERENT page does NOT lift this one — it stays at 0.40, un-injected.
        out = self._file(query="rate limit policy")
        res = m3.affirm_filed_page(
            self.db, out["path"],
            {"kind": "user_affirmation", "target": "some-unrelated-other-topic",
             "session_id": "sess-1"})
        self.assertIsNone(res["promoted"])
        self.assertEqual(res.get("reason"), "mis_attributed")
        self.assertEqual(_events(out["path"]), [])                 # NO event
        self.assertAlmostEqual(self._conf(out["path"]), 0.40, places=6)
        self.assertFalse(C.injected(self._conf(out["path"]), managed=True))

    def test_ac3_revert_verify_filing_act_never_mints_and_gate_is_load_bearing(self):
        # REVERT-VERIFY: filing WITHOUT an affirmation reaches NO ≥0.55 path — the
        # page is 0.40. A version that minted `confirmed` on the filing act would
        # put it at 0.60 across the gate (laundering). Prove the delta is real: the
        # SAME page only crosses 0.55 once a genuine tier-≥2 event is appended.
        out = self._file(query="retry budget policy")
        self.assertLess(self._conf(out["path"]), C.INJECT_GATE)     # 0.40 < 0.55
        # simulate the (forbidden) mint-on-filing → it WOULD cross the gate
        C_evs = [{"event_type": "confirmed"}]
        self.assertGreaterEqual(C.fold_confidence(0.40, C_evs)[0], C.INJECT_GATE)

    def test_ac3_dark_safe_affirmation_noop(self):
        out = self._file(query="dark policy")
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)  # OFF
        res = m3.affirm_filed_page(
            self.db, out["path"],
            {"kind": "user_affirmation", "target": out["path"], "session_id": "s"})
        self.assertIsNone(res["promoted"])
        self.assertEqual(_events(out["path"]), [])   # no event written in the dark


# --- FR-5 provenance persistence + reindex survival (AC-5) -------------------
class FR5ProvenanceTest(M3Base):
    def test_ac5_provenance_recorded_survives_full_reindex_queryable(self):
        prov = _prov(_SUP_ANSWER, _SUP_SPANS, query="token lifetime", session="sess-9")
        out = m3.file_recalled_answer(self.db, prov, memory_dir=self.mem,
                                      neighbors_fn=self._no_neighbors)
        path = out["path"]
        content = open(path, encoding="utf-8").read()
        # every provenance field present: source card ids, cited SPANS, query, session, scores
        self.assertIn("## Provenance", content)
        self.assertIn("card-0", content)
        self.assertIn(_SUP_SPANS[0], content)          # the cited span text
        self.assertIn("token lifetime", content)       # recall query
        self.assertIn("sess-9", content)               # session id
        self.assertRegex(content, r"\d\.\d{3}")        # a per-claim support score
        # queryable from the DERIVED index, and survives a --full reindex
        conn = index_impl.init_db(self.db)
        index_impl.run_full(conn, [path])   # run_full closes the passed conn
        conn = index_impl.init_db(self.db)  # re-open to query the derived index
        rows = conn.execute(
            "SELECT content FROM memory_chunks WHERE path=? AND content LIKE '%card-0%'",
            (path,)).fetchall()
        conn.close()
        self.assertTrue(rows, "provenance not queryable from the derived index after --full")
        # provenance still in the file bytes post-reindex
        self.assertIn("## Provenance", open(path, encoding="utf-8").read())

    def test_ac5_oplog_row_survives_full_reindex(self):
        prov = _prov(_SUP_ANSWER, _SUP_SPANS, query="oplog survives")
        m3.file_recalled_answer(self.db, prov, memory_dir=self.mem,
                                neighbors_fn=self._no_neighbors)
        log = os.path.join(os.path.dirname(os.path.dirname(self.db)), "log.md")
        self.assertTrue(os.path.exists(log))
        self.assertIn("op=autofile_filed", open(log, encoding="utf-8").read())
        conn = index_impl.init_db(self.db)
        index_impl.run_full(conn, [])   # reindex does not touch the op-log
        conn.close()
        self.assertIn("op=autofile_filed", open(log, encoding="utf-8").read())


# --- FR-6 identity/collision (AC-6) ------------------------------------------
class FR6CollisionTest(M3Base):
    def test_ac6_same_slug_same_project_routes_to_m2_no_clobber(self):
        query = "auth service token policy"
        slug = m3._REM.target_slug(query, "synthesis")
        # an existing same-slug card in a SUBDIR (proves recursive find_same_slug_card,
        # not just an exact-path check) with distinctive user bytes.
        sub = os.path.join(self.mem, "sub")
        os.makedirs(sub)
        existing = os.path.join(sub, slug + ".md")
        existing_page = ("---\nname: " + slug + "\ntype: project\nsource: agent-extracted\n"
                         "last_verified: 2026-06-01\n---\n\nDISTINCTIVE USER BYTES.\n")
        with open(existing, "w", encoding="utf-8") as f:
            f.write(existing_page)
        before = open(existing, encoding="utf-8").read()
        routed = {}

        def spy(db, prov, hits, top, **kw):
            routed["top"] = top
            return [{"action": "edited"}]

        out = m3.file_recalled_answer(
            self.db, _prov(_SUP_ANSWER, _SUP_SPANS, query=query), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors, m2_handoff=spy)
        self.assertEqual(out["action"], "deduped_to_m2")
        self.assertEqual(routed["top"]["path"], existing)   # routed at the existing card
        self.assertFalse(os.path.exists(os.path.join(self.mem, slug + ".md")))  # NO new page
        self.assertEqual(open(existing, encoding="utf-8").read(), before)  # never clobbered

    def test_ac6_cross_project_same_slug_untouched(self):
        query = "shared slug topic"
        slug = m3._REM.target_slug(query, "synthesis")
        # project B carries a same-slug card; filing into project A must not touch it.
        memB = os.path.join(self.tmp, ".claude", "projects", "proj-b", "memory")
        os.makedirs(memB)
        pb = os.path.join(memB, slug + ".md")
        pageB = ("---\nname: " + slug + "\ntype: project\nsource: agent-extracted\n"
                 "last_verified: 2026-06-01\n---\n\nPROJECT B CONTENT.\n\n## Evidence\n\n"
                 "- 2026-06-01T00:00:00 · confirmed · user-explicit · Δ+0.20\n")
        with open(pb, "w", encoding="utf-8") as f:
            f.write(pageB)
        before_b = open(pb, encoding="utf-8").read()
        before_evs_b = _events(pb)
        out = m3.file_recalled_answer(
            self.db, _prov(_SUP_ANSWER, _SUP_SPANS, query=query), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "filed")                       # new page in A
        self.assertTrue(os.path.exists(os.path.join(self.mem, slug + ".md")))
        self.assertEqual(open(pb, encoding="utf-8").read(), before_b)  # B byte-identical
        self.assertEqual(_events(pb), before_evs_b)                    # B event-identical


# --- FR-8 idempotence (AC-7) -------------------------------------------------
class FR8IdempotenceTest(M3Base):
    def _card_events(self, path):
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [path])
        rows = conn.execute(
            "SELECT event_type, session_id, delta FROM card_events WHERE path=? "
            "ORDER BY ts, event_type", (path,)).fetchall()
        conn.close()
        return rows

    def test_ac7_double_file_yields_one_page(self):
        prov = _prov(_SUP_ANSWER, _SUP_SPANS, query="idempotent topic")
        out1 = m3.file_recalled_answer(self.db, prov, memory_dir=self.mem,
                                       neighbors_fn=self._no_neighbors)
        self.assertEqual(out1["action"], "filed")
        # 2nd identical file → same slug on disk → dedups to M2, NO 2nd page
        out2 = m3.file_recalled_answer(self.db, prov, memory_dir=self.mem,
                                       neighbors_fn=self._no_neighbors)
        self.assertEqual(out2["action"], "deduped_to_m2")
        slug = m3._REM.target_slug("idempotent topic", "synthesis")
        pages = [f for f in os.listdir(self.mem) if f == slug + ".md"]
        self.assertEqual(len(pages), 1)   # exactly one page

    def test_ac7_affirmation_idempotent_explicit_same_source_guard(self):
        out = m3.file_recalled_answer(
            self.db, _prov(_SUP_ANSWER, _SUP_SPANS, query="idem affirm"),
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        aff = {"kind": "user_affirmation", "target": out["path"], "session_id": "sess-x"}
        r1 = m3.affirm_filed_page(self.db, out["path"], aff)
        self.assertEqual(r1["promoted"], "confirmed")
        # re-apply the SAME affirmation (same session) → explicit content guard skips
        # (append_event stamps a fresh ts, so the PK cannot dedup — the guard must).
        r2 = m3.affirm_filed_page(self.db, out["path"], aff)
        self.assertIsNone(r2["promoted"])
        self.assertEqual(r2.get("reason"), "idempotent_skip")
        evs = [e["event_type"] for e in _events(out["path"])]
        self.assertEqual(evs, ["confirmed"])   # exactly one, not two

    def test_ac7_card_events_full_equals_incremental(self):
        out = m3.file_recalled_answer(
            self.db, _prov(_SUP_ANSWER, _SUP_SPANS, query="full eq incr"),
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        m3.affirm_filed_page(
            self.db, out["path"],
            {"kind": "user_affirmation", "target": out["path"], "session_id": "s"})
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, [out["path"]])
        inc = conn.execute("SELECT event_type, session_id, delta FROM card_events "
                           "WHERE path=? ORDER BY ts, event_type", (out["path"],)).fetchall()
        conn.close()
        conn = index_impl.init_db(self.db)
        index_impl.run_full(conn, [out["path"]])   # run_full closes the passed conn
        conn = index_impl.init_db(self.db)          # re-open to query card_events
        full = conn.execute("SELECT event_type, session_id, delta FROM card_events "
                            "WHERE path=? ORDER BY ts, event_type", (out["path"],)).fetchall()
        conn.close()
        self.assertEqual(inc, full)   # --full == --incremental


# ===================== FIX R1 (adversarial audit A1a/A1b/A3) ================
def _pure_overlap(claim, spans):
    """The OLD bag-of-words overlap (no polarity / no salient gate) — used only in
    revert-verify to prove the new hard gates are load-bearing."""
    ct = m3._content_tokens(claim)
    if not ct:
        return 1.0
    best = 0.0
    for s in spans or []:
        ss = set(m3._content_tokens(s))
        if ss:
            best = max(best, sum(1 for t in ct if t in ss) / len(ct))
    return best


# --- A1a: fact-support gate (negation + salient-entity coverage) -------------
class A1aFactGateTest(M3Base):
    def _file(self, answer, spans, query="q"):
        return m3.file_recalled_answer(
            self.db, _prov(answer, spans, query=query), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)

    def test_negation_rejected_end_to_end(self):
        out = self._file(
            "The auth service does not issue JWT access tokens for user sessions.",
            ["The auth service issues JWT access tokens for user sessions."])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(out.get("reason"), "unsupported_claim")
        self.assertEqual(os.listdir(self.mem), [])           # NO page filed

    def test_mongodb_dilution_rejected(self):
        out = self._file("The configuration database is MongoDB.",
                         ["The configuration database settings."])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_port_number_dilution_rejected(self):
        out = self._file("The datastore is PostgreSQL running on port 9999.",
                         ["The datastore is running for the auth service user sessions."])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_genuine_paraphrase_still_files(self):
        # all salient entities present, polarity matches → must STILL FILE
        out = self._file(
            "The auth service issues JWT access tokens for user sessions.",
            ["Auth service issues JWT access tokens for user sessions daily."])
        self.assertEqual(out["action"], "filed")
        self.assertTrue(os.path.exists(out["path"]))

    def test_matching_negation_still_files(self):
        # polarity MATCHES (both negated) + salient covered → supported, files
        out = self._file("The JWT session tokens do not expire after logout.",
                         ["The JWT session tokens do not expire after logout ever."])
        self.assertEqual(out["action"], "filed")

    def test_revert_verify_pure_overlap_files_the_negation(self):
        # REVERT-VERIFY: restore pure bag-of-words overlap → the negation answer FILES
        m3.register_support(_pure_overlap)
        try:
            out = self._file(
                "The auth service does not issue JWT access tokens for user sessions.",
                ["The auth service issues JWT access tokens for user sessions."])
        finally:
            m3.register_support(None)
        self.assertEqual(out["action"], "filed", "revert-verify: pure overlap launders negation")


# --- A1b: materiality widened to short factual / directive assertions --------
class A1bMaterialityTest(M3Base):
    def test_short_entity_rider_now_gated_and_rejected(self):
        # "Use MongoDB." is now MATERIAL (proper noun) + unsupported → whole answer REJECT
        out = m3.file_recalled_answer(
            self.db, _prov("The auth service issues JWT access tokens for user sessions. "
                           "Use MongoDB.",
                           ["The auth service issues JWT access tokens for user sessions."]),
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_short_directive_rider_gated_and_rejected(self):
        out = m3.file_recalled_answer(
            self.db, _prov("The auth service issues JWT access tokens for user sessions. "
                           "Delete all data.",
                           ["The auth service issues JWT access tokens for user sessions."]),
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "rejected")

    def test_material_predicate(self):
        self.assertTrue(m3._is_material("Use MongoDB."))      # proper noun
        self.assertTrue(m3._is_material("Port 9999."))        # number
        self.assertTrue(m3._is_material("Delete all data."))  # directive imperative
        self.assertFalse(m3._is_material("Yes."))             # still non-material filler
        self.assertFalse(m3._is_material("Okay then."))

    def test_revert_verify_old_materiality_files_the_rider(self):
        # REVERT-VERIFY: restore ≥3-content-words-only → the short rider is skipped,
        # never scored, and the answer FILES with the un-gated "Use MongoDB." rider.
        old = m3._is_material
        m3._is_material = lambda c: len(m3._content_tokens(c)) >= 3
        try:
            out = m3.file_recalled_answer(
                self.db, _prov("The auth service issues JWT access tokens for user sessions. "
                               "Use MongoDB.",
                               ["The auth service issues JWT access tokens for user sessions."]),
                memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        finally:
            m3._is_material = old
        self.assertEqual(out["action"], "filed")
        self.assertIn("Use MongoDB.", open(out["path"], encoding="utf-8").read())


# --- A3: empty-answer reject is op-logged like every other reject path -------
class A3EmptyAnswerOplogTest(M3Base):
    def _oplog_rejected(self):
        log = os.path.join(os.path.dirname(os.path.dirname(self.db)), "log.md")
        if not os.path.exists(log):
            return 0
        return open(log, encoding="utf-8").read().count("op=autofile_rejected")

    def test_empty_answer_rejected_and_logged(self):
        out = m3.file_recalled_answer(
            self.db, _prov("   ", ["some span text here"], query="empty answer case"),
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(out.get("reason"), "empty_answer")
        self.assertEqual(self._oplog_rejected(), 1)   # logged, consistent with other rejects
        self.assertEqual(os.listdir(self.mem), [])


# ===================== FIX R2 (re-audit N4 / N1 / N2) =======================
class R2Base(M3Base):
    def _file(self, answer, spans, query="q"):
        return m3.file_recalled_answer(
            self.db, _prov(answer, spans, query=query), memory_dir=self.mem,
            neighbors_fn=self._no_neighbors)


# --- N4 (MED): antonym-opposite claims still file → now REJECT ---------------
class N4AntonymTest(R2Base):
    def test_enabled_disabled_rejected(self):
        out = self._file("The request cache is disabled by default for all clients.",
                         ["The request cache is enabled by default for all clients."])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_refresh_access_rejected(self):
        out = self._file("The auth service issues refresh tokens for user sessions.",
                         ["The auth service issues access tokens for user sessions."])
        self.assertEqual(out["action"], "rejected")

    def test_allow_deny_rejected(self):
        out = self._file("The firewall rule allows inbound traffic on the subnet.",
                         ["The firewall rule denies inbound traffic on the subnet."])
        self.assertEqual(out["action"], "rejected")

    def test_same_side_still_files(self):
        # NOT an antonym cross (both "enabled") → supported paraphrase files
        out = self._file("The request cache is enabled by default for all clients.",
                         ["The request cache is enabled by default for every client."])
        self.assertEqual(out["action"], "filed")

    def test_revert_verify_drop_lexicon_files_the_opposite(self):
        # REVERT-VERIFY: empty the antonym lexicon → the disabled/enabled opposite FILES
        saved = m3._ANTONYM_PAIRS
        m3._ANTONYM_PAIRS = []
        try:
            out = self._file("The request cache is disabled by default for all clients.",
                             ["The request cache is enabled by default for all clients."])
        finally:
            m3._ANTONYM_PAIRS = saved
        self.assertEqual(out["action"], "filed", "revert-verify: no lexicon launders the antonym")


# --- N1b (MED): legitimately multi-span-supported answer now FILES -----------
class N1bMultiSpanTest(R2Base):
    TWO_SPANS = ["The configuration database is PostgreSQL for the app.",
                 "It listens on port 5432 for all clients."]

    def test_two_span_synthesis_files(self):
        # entity in span 1, number in span 2, BOTH cited → union coverage → files
        out = self._file("PostgreSQL listens on port 5432.", self.TWO_SPANS)
        self.assertEqual(out["action"], "filed")

    def test_salient_in_no_span_still_rejects(self):
        # safety preserved: a salient token in NONE of the cited spans → reject
        out = self._file("MongoDB listens on port 5432.", self.TWO_SPANS)
        self.assertEqual(out["action"], "rejected")

    def test_revert_verify_per_span_coverage_rejects_two_span(self):
        # REVERT-VERIFY: a per-span (pre-R2) coverage scorer rejects the two-span answer;
        # the union default files it → union coverage is the load-bearing change.
        def _per_span(claim, spans):
            ct = m3._content_tokens(claim)
            if not ct:
                return 1.0
            csal = m3._salient_set(claim)
            best = 0.0
            for s in spans or []:
                sw = {w.lower() for w in m3._WORD_RE.findall(s)}
                if not csal.issubset(sw):
                    continue
                ss = set(m3._content_tokens(s))
                best = max(best, sum(1 for t in ct if t in ss) / len(ct))
            return best
        m3.register_support(_per_span)
        try:
            out = self._file("PostgreSQL listens on port 5432.", self.TWO_SPANS)
        finally:
            m3.register_support(None)
        self.assertEqual(out["action"], "rejected", "revert-verify: per-span coverage rejects RAG synthesis")


# --- N1a/N1c (LOW): formatting-mismatch false-rejects fixed ------------------
class N1FormattingTest(R2Base):
    def test_thousands_separator_files(self):
        out = self._file("The datastore listens on port 9999 for clients.",
                         ["The datastore listens on port 9,999 for clients."])
        self.assertEqual(out["action"], "filed")

    def test_codeish_subtoken_files(self):
        out = self._file("The datastore listens on port 8080 for clients.",
                         ["The datastore listens on port 8080/tcp for clients."])
        self.assertEqual(out["action"], "filed")


# --- N2 (LOW): subordinate-clause negation — mitigated by the same-statement gate -
class N2SubclauseTest(R2Base):
    def test_subclause_negation_not_false_vetoed(self):
        # The whole-sentence polarity read COULD over-reject a negation in a
        # subordinate clause; the _CONTRADICTION_MIN "same-statement" coverage gate
        # mitigates it — the span here is NOT the same statement (coverage < 0.75), so
        # the polarity veto does not fire and this legitimately-supported answer FILES.
        # DECLARED residual (M3-PROGRESS Fix R2): if a span WERE nearly identical to a
        # subclause-negated claim it would still over-reject — bias-safe (a human can
        # still promote), and we do NOT add a clause parser.
        out = self._file("The token, if not expired, is accepted by the gateway.",
                         ["The token is accepted by the gateway when valid."])
        self.assertEqual(out["action"], "filed")


# ===================== FIX R3 (re-audit NS4 HIGH regression) ================
class NS4PaddingDisarmTest(R2Base):
    """The contradiction/antonym veto must NEVER be disarmable by attacker-controlled
    claim padding (NS4 reopened R1 A1a). Padding the claim with non-shared words must
    not flip reject→file."""

    PAD_NEG = ("The internal backend auth service does not issue access tokens "
               "for authenticated user sessions.")
    BARE_NEG = "The auth service does not issue access tokens for authenticated user sessions."
    NEG_SPAN = "The auth service issues access tokens for authenticated user sessions."

    PAD_ANTO = "The request cache is disabled by default in this deployment configuration."
    ANTO_SPAN = "The request cache is enabled by default."

    def test_padded_negation_rejected(self):
        out = self._file(self.PAD_NEG, [self.NEG_SPAN])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_bare_negation_still_rejected(self):
        out = self._file(self.BARE_NEG, [self.NEG_SPAN])
        self.assertEqual(out["action"], "rejected")

    def test_padded_antonym_rejected(self):
        out = self._file(self.PAD_ANTO, [self.ANTO_SPAN])
        self.assertEqual(out["action"], "rejected")

    def test_padding_does_not_flip_reject_to_file(self):
        # both padded and un-padded reject → padding is inert against the veto
        self.assertEqual(self._file(self.BARE_NEG, [self.NEG_SPAN])["action"], "rejected")
        self.assertEqual(self._file(self.PAD_NEG, [self.NEG_SPAN])["action"], "rejected")

    def test_revert_verify_claim_coverage_gate_launders_padded(self):
        # REVERT-VERIFY: a scorer that gates the veto on CLAIM coverage (the R2 bug)
        # FILES the padded negation (padding drops claim coverage below 0.75, disarming
        # the veto); the unconditional/precise default REJECTS it.
        def _claim_coverage_gated(claim, spans):
            ct = [t for t in m3._content_tokens(claim) if t not in m3._ANTONYM_FORMS]
            cw, cneg = m3._word_set(claim), m3._has_negation(claim)
            for s in spans or []:
                sc = set(m3._content_tokens(s))
                if not ct:
                    continue
                cov = sum(1 for t in ct if t in sc) / len(ct)  # CLAIM coverage (paddable)
                if cov < 0.75:
                    continue
                if m3._has_negation(s) != cneg or m3._antonym_cross(cw, m3._word_set(s)):
                    return 0.0
            return 1.0  # support assumed for the revert-verify
        m3.register_support(_claim_coverage_gated)
        try:
            out = self._file(self.PAD_NEG, [self.NEG_SPAN])
        finally:
            m3.register_support(None)
        self.assertEqual(out["action"], "filed", "revert-verify: claim-coverage gate launders padding")
        # the real default rejects the same padded claim
        self.assertEqual(self._file(self.PAD_NEG, [self.NEG_SPAN])["action"], "rejected")

    def test_multi_span_answer_still_files(self):
        # the legitimate multi-span answer (access sentence + refresh sentence, each
        # with its own cited span) must STILL file — the veto is precise, not blanket.
        out = m3.file_recalled_answer(
            self.db, {"answer_text": "The auth service issues JWT access tokens for user sessions. "
                                     "Refresh tokens rotate the JWT session daily.",
                      "sources": [{"card_id": "c0", "span": "The auth service issues JWT access tokens for sessions."},
                                  {"card_id": "c1", "span": "Refresh tokens rotate the JWT session for the auth service daily."}],
                      "recall_query": "token policy", "session_id": "s"},
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "filed")


# ===================== FIX R4 (re-audit LS4 — verbose-span antonym) =========
class LS4VerboseAntonymTest(R2Base):
    """The ANTONYM veto must fire regardless of how VERBOSE the cited span is — a
    normal long source chunk must not disarm it (LS4, the antonym analog of NS4)."""

    def test_verbose_enabled_disabled_rejected(self):
        out = self._file(
            "The request cache is disabled by default.",
            ["The request cache is enabled by default for all authenticated users "
             "across every region in production today."])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_verbose_allow_deny_rejected(self):
        out = self._file(
            "The API endpoint allows anonymous access.",
            ["The API endpoint denies anonymous access for all unauthenticated "
             "external clients by strict default policy."])
        self.assertEqual(out["action"], "rejected")

    def test_verbose_primary_secondary_rejected(self):
        out = self._file(
            "The replica database handles all primary writes.",
            ["The replica database handles all secondary read queries for reporting "
             "dashboards across the analytics cluster."])
        self.assertEqual(out["action"], "rejected")

    def test_negation_contrast_verbose_span_rejects(self):
        # contrast: the coverage-free negation veto ALSO rejects the same verbose span
        out = self._file(
            "The request cache is not enabled by default.",
            ["The request cache is enabled by default for all authenticated users "
             "across every region in production today."])
        self.assertEqual(out["action"], "rejected")

    def test_normal_length_antonym_still_rejects(self):  # LS2 keep
        out = self._file("The request cache is disabled by default for all clients.",
                         ["The request cache is enabled by default for all clients."])
        self.assertEqual(out["action"], "rejected")

    def test_dual_antonym_legitimate_still_files(self):  # OR1 keep
        # claim carries BOTH sides (old disabled, new enabled) → no cross → files
        out = self._file(
            "The old cache was disabled but the new cache is enabled by default.",
            ["The new cache is enabled by default for all clients."])
        self.assertEqual(out["action"], "filed")

    def test_multi_span_access_refresh_still_files(self):
        # a span AGREES with each claim's side (access span for the access sentence,
        # refresh span for the refresh sentence) → corroborated → no false veto
        out = m3.file_recalled_answer(
            self.db, {"answer_text": "The auth service issues JWT access tokens for user sessions. "
                                     "Refresh tokens rotate the JWT session daily.",
                      "sources": [{"card_id": "c0", "span": "The auth service issues JWT access tokens for sessions."},
                                  {"card_id": "c1", "span": "Refresh tokens rotate the JWT session for the auth service daily."}],
                      "recall_query": "token policy", "session_id": "s"},
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "filed")

    def test_revert_verify_span_cov_gate_launders_verbose_antonym(self):
        # REVERT-VERIFY: a scorer that gates the antonym veto on span_cov>=0.75 (the R3
        # antonym path) FILES the verbose-span antonym; the coverage-free default REJECTS.
        def _span_cov_gated(claim, spans):
            cw = m3._word_set(claim)
            ccs = set(m3._content_tokens(claim))
            for s in spans or []:
                sc = set(m3._content_tokens(s))
                if not sc:
                    continue
                shared = (ccs & sc) - m3._ANTONYM_FORMS
                span_nonanto = {t for t in sc if t not in m3._ANTONYM_FORMS}
                span_cov = (len(shared) / len(span_nonanto)) if span_nonanto else 0.0
                if span_cov >= 0.75 and m3._antonym_cross(cw, m3._word_set(s)):
                    return 0.0
            return 1.0  # support assumed for the revert-verify
        m3.register_support(_span_cov_gated)
        try:
            out = self._file("The request cache is disabled by default.",
                             ["The request cache is enabled by default for all authenticated "
                              "users across every region in production today."])
        finally:
            m3.register_support(None)
        self.assertEqual(out["action"], "filed", "revert-verify: span_cov gate launders verbose antonym")
        self.assertEqual(
            self._file("The request cache is disabled by default.",
                       ["The request cache is enabled by default for all authenticated "
                        "users across every region in production today."])["action"],
            "rejected")


# ===================== FIX R5 (re-audit NS-B — relatedness flat-strip bug) ==
class NSBLexiconAnchorTest(R2Base):
    """The antonym relatedness test must subtract only the PAIR UNDER TEST, not the
    FLAT lexicon union — else a shared anchor that is itself a lexicon term (from any
    pair) is stripped, the span is judged 'unrelated', the veto skips, and the
    antonym-opposite files (NS-B)."""

    def test_read_access_enabled_vs_disabled_rejected(self):
        # anchor "read access" — both lexicon forms; must still count as related
        out = self._file("Read access is enabled.", ["Read access is disabled."])
        self.assertEqual(out["action"], "rejected")
        self.assertEqual(os.listdir(self.mem), [])

    def test_primary_access_enabled_vs_disabled_rejected(self):
        out = self._file("Primary access is enabled.", ["Primary access is disabled."])
        self.assertEqual(out["action"], "rejected")

    def test_revert_verify_flat_union_strip_launders(self):
        # REVERT-VERIFY: a relatedness test that strips the FLAT _ANTONYM_FORMS union
        # (the R4 bug) FILES "Read access is enabled." vs "…disabled."; the pair-scoped
        # default REJECTS it.
        def _flat_strip_scorer(claim, spans):
            cw = m3._word_set(claim)
            ccs = set(m3._content_tokens(claim))
            spans_tok = [(m3._word_set(s), set(m3._content_tokens(s))) for s in spans or []]
            for side_a, side_b in m3._ANTONYM_PAIRS:
                ca, cb = bool(cw & side_a), bool(cw & side_b)
                if ca == cb:
                    continue
                claim_side, opp = (side_a, side_b) if ca else (side_b, side_a)
                agree = oppose = False
                for sw, sc in spans_tok:
                    if not ((ccs & sc) - m3._ANTONYM_FORMS):   # FLAT strip (the bug)
                        continue
                    if sw & claim_side:
                        agree = True
                    if sw & opp:
                        oppose = True
                if oppose and not agree:
                    return 0.0
            return 1.0
        m3.register_support(_flat_strip_scorer)
        try:
            out = self._file("Read access is enabled.", ["Read access is disabled."])
        finally:
            m3.register_support(None)
        self.assertEqual(out["action"], "filed", "revert-verify: flat-union strip launders the opposite")
        self.assertEqual(self._file("Read access is enabled.", ["Read access is disabled."])["action"],
                         "rejected")

    # --- NS-A over-rejection kept: all legitimate cases still FILE ---
    def test_dual_antonym_legit_files(self):
        out = self._file("The old cache was disabled but the new cache is enabled by default.",
                         ["The new cache is enabled by default for all clients."])
        self.assertEqual(out["action"], "filed")

    def test_agreement_mention_files(self):
        out = self._file("Read access is enabled by default.",
                         ["Read access is enabled by default for all clients."])
        self.assertEqual(out["action"], "filed")

    def test_corroborated_plus_unrelated_opposite_files(self):
        # same-side corroborating (related) span + an UNRELATED opposite-side span → files
        out = m3.file_recalled_answer(
            self.db, {"answer_text": "The request cache is enabled by default.",
                      "sources": [{"card_id": "c0", "span": "The request cache is enabled by default in test."},
                                  {"card_id": "c1", "span": "The door lock is disabled for maintenance."}],
                      "recall_query": "cache default", "session_id": "s"},
            memory_dir=self.mem, neighbors_fn=self._no_neighbors)
        self.assertEqual(out["action"], "filed")


if __name__ == "__main__":
    unittest.main()
