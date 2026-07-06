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


if __name__ == "__main__":
    unittest.main()
