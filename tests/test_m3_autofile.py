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


if __name__ == "__main__":
    unittest.main()
