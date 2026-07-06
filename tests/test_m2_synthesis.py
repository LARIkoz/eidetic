"""M2 multi-page synthesis (spec-m2-synthesis AC-1..AC-9 + M1×M2 interaction).

Hermetic, both legs, temp DBs only. Neighbors / confirmer / supersedes are INJECTED
so the pipeline is deterministic without a model. The safety-critical locator ACs
(AC-2a/2c/2d/2e) bake in their revert-verify: they assert the real fence-hardened,
id-verified locator handles the fixture AND that a STRAWMAN locator (heading-title /
naive-toggle / EOF-replace / string-only) MISHANDLES it — so the test provably
discriminates the safe implementation from the reopen-path ones.
"""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import confidence as C  # noqa: E402
import evidence as EV  # noqa: E402
import index_impl  # noqa: E402
import m1_contradiction as m1  # noqa: E402
import m2_synthesis as m2  # noqa: E402

NO = lambda a, b: "no_contradiction"      # noqa: E731
YES = lambda a, b: "contradiction"        # noqa: E731
NOSUP = lambda t, p: False                # noqa: E731
SUP = lambda t, p: True                   # noqa: E731


def _fastembed_available():
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None

NEW = "2026-06-01"
OLD = "2024-01-01"


def _card(name, type_="project", source="agent-extracted", body="body text", extra_fm=""):
    fm = [f"name: {name}", f"type: {type_}", f"source: {source}",
          f"last_verified: {NEW}"]
    if extra_fm:
        fm.append(extra_fm)
    return "---\n" + "\n".join(fm) + "\n---\n\n" + body + "\n"


class M2Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m2-")
        self.mem = os.path.join(self.tmp, ".claude", "projects", "proj-a", "memory")
        os.makedirs(self.mem)
        self.db = os.path.join(self.tmp, "db", "index.db")
        os.environ["EIDETIC_CONFIDENCE_EVENTS"] = "on"
        os.environ["EIDETIC_M2_SYNTHESIS"] = "on"  # M2 dormant-by-default; opt in
        # M2.1: the relevance gate requires a reranker; the reranker is unprovisioned
        # on this host, so MOCK it admit-all by default (the real quality dogfood runs
        # on the owner's box). F1 tests override with explicit low/None scorers.
        m2.register_relevance(lambda a, b: 1.0)

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        os.environ.pop("EIDETIC_M2_SYNTHESIS", None)
        m2.register_relevance(None)
        if hasattr(self, "_saved_nvd"):
            m1.neighbors_via_door = self._saved_nvd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, fn, text):
        p = os.path.join(self.mem, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _read(self, p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def _index(self, paths):
        conn = index_impl.init_db(self.db)
        index_impl.run_incremental(conn, paths)
        return conn

    def _conf(self, conn, name):
        return conn.execute("SELECT confidence FROM memory_chunks WHERE name=? LIMIT 1",
                            (name,)).fetchone()[0]

    def _events(self, path):
        return index_impl.parse_evidence_events(
            index_impl.parse_frontmatter(self._read(path))[1])


# --- FR-1 selection ----------------------------------------------------------
class SelectionTest(M2Base):
    def test_ac1_k_bounded_and_deterministic(self):
        neigh = [{"score": 0.80 + i * 0.001, "path": f"/m/c{i}.md"} for i in range(30)]
        a = m2.select_related("/m/self.md", neigh)
        b = m2.select_related("/m/self.md", list(reversed(neigh)))
        self.assertEqual(len(a), m2.M2_FANOUT)          # K-bounded
        self.assertEqual(a, b)                          # deterministic (order-independent)
        # highest scores first
        self.assertEqual([p for p, _ in a], sorted([p for p, _ in a],
                         key=lambda p: -dict((x["path"], x["score"]) for x in neigh)[p]))

    def test_ac1_below_floor_excluded(self):
        neigh = [{"score": 0.70, "path": "/m/lo.md"}, {"score": 0.95, "path": "/m/hi.md"}]
        got = m2.select_related("/m/self.md", neigh)
        self.assertEqual([p for p, _ in got], ["/m/hi.md"])  # 0.70 < M2_RELATED_MIN dropped


# --- FR-4 region locator + safety ACs ---------------------------------------
class RegionTest(M2Base):
    def _page_with_region(self, rid="abc123", region="synth body"):
        return (_card("proj-x") + "\n" + m2._begin_sentinel(rid) + "\n" + region
                + "\n" + m2._END_SENTINEL + "\n").replace(
            "type: project", f"type: project\nsynthesis_region_id: {rid}")

    def test_ac2_dual_invariant_converge_and_preserve(self):
        p = self._write("x.md", self._page_with_region(region="OLD synthesis"))
        before = self._read(p)
        outcomes = m2.process_trigger(self.db, self._trig_path(), self._trig_meta(),
                                      "trigger body", neighbors=[{"score": 0.9, "path": p}],
                                      confirmer=NO, supersedes=NOSUP,
                                      synth_body_fn=lambda t, tg, prov: prov + "\nNEW converged synthesis")
        self.assertEqual([o["action"] for o in outcomes], ["edited"])
        after = self._read(p)
        # (i) convergence: region body rewritten toward the trigger, OLD gone
        self.assertIn("NEW converged synthesis", after)
        self.assertNotIn("OLD synthesis", after)
        # (ii) safety (F6): every USER byte BEFORE the region's begin sentinel is
        # byte-identical (frontmatter + all user sections). The only change AFTER the
        # end sentinel is the FR-6 `observed` event appended to ## Evidence.
        self.assertEqual(before.split(m2._begin_sentinel("abc123"))[0],
                         after.split(m2._begin_sentinel("abc123"))[0])
        tail_before = before.split(m2._END_SENTINEL)[1]
        tail_after = after.split(m2._END_SENTINEL)[1]
        # after removing the FR-6 ## Evidence event block, the tail is unchanged
        self.assertEqual(tail_after.split("## Evidence")[0].strip(), tail_before.strip())
        self.assertIn("observed", tail_after)  # the only addition is the observed event

    def test_ac2_convergence_not_append(self):
        # An append-only strawman would keep OLD; assert the region does not accrete.
        content = self._page_with_region(region="line1")
        c2, _, _ = m2.apply_region(content, "line2")
        self.assertIn("line2", m2.current_region_body(c2))
        self.assertNotIn("line1", m2.current_region_body(c2))

    def test_ac2a_colliding_heading_and_string_preserved(self):
        # user H2 with a synthesis-like title + a user-pasted sentinel-LIKE string
        page = _card("proj-y", body="## Synthesis (auto)\n\nUser wrote this section.\n\n"
                     "Here is a pasted-looking marker eidetic:synthesis:begin (no comment).\n")
        p = self._write("y.md", page)
        _m_before, body_before = index_impl.parse_frontmatter(self._read(p))
        m2.process_trigger(self.db, self._trig_path(), self._trig_meta(), "t",
                           neighbors=[{"score": 0.9, "path": p}], confirmer=NO, supersedes=NOSUP)
        after = self._read(p)
        # the user's H2 + pasted sentinel-LIKE string sit BEFORE the real region and
        # are preserved verbatim (the delimiter is the id-verified pair, not a heading).
        user_part = after.split("<!-- eidetic:synthesis:begin")[0]
        self.assertIn("## Synthesis (auto)", user_part)                 # user heading preserved
        self.assertIn("User wrote this section.", user_part)
        self.assertIn("eidetic:synthesis:begin (no comment)", user_part)  # user string preserved
        # the user body is byte-identical (only frontmatter gained the id + region appended)
        self.assertIn(body_before.rstrip(), after)

    def test_ac2a_heading_locator_strawman_would_misfire(self):
        page = _card("proj-y", body="## Synthesis (auto)\n\nUser section body.\n")
        # the strawman finds the user H2 as a "region"; the real locator finds none.
        self.assertIsNotNone(_heading_title_locator(page, "Synthesis (auto)"))
        self.assertIsNone(m2._synthesis_region_bounds(page, "whatever"))

    def test_ac2b_agent_owned_bounded_growth(self):
        page = _card("proj-z", body="User paragraph A.\n\n## Sec\nUser paragraph B.\n")
        p = self._write("z.md", page)
        sizes, outside_prefix = [], None
        for i in range(4):
            m2.process_trigger(self.db, self._trig_path(), self._trig_meta(), f"t{i}",
                               neighbors=[{"score": 0.9, "path": p}], confirmer=NO, supersedes=NOSUP,
                               synth_body_fn=lambda t, tg, prov: prov + f"\nrev {i}")
            after = self._read(p)
            sizes.append(len(after))
            self.assertIn("User paragraph A.", after)
            self.assertIn("User paragraph B.", after)
            # everything OUTSIDE the region (incl. the once-added frontmatter id) is
            # byte-stable from the first synthesis onward (revise replaces only inside).
            prefix = after.split("<!-- eidetic:synthesis:begin")[0]
            if outside_prefix is None:
                outside_prefix = prefix
            else:
                self.assertEqual(prefix, outside_prefix)
        # bounded: page size does not grow monotonically with M (revise replaces)
        self.assertLessEqual(max(sizes) - min(sizes), 2, f"region grew unbounded: {sizes}")
        # first synthesis inserted AFTER all user bytes
        after = self._read(p)
        self.assertGreater(after.index("eidetic:synthesis:begin"), after.index("User paragraph B."))

    def test_ac2c_fence_hardened(self):
        rid = "cafef00d"
        # a ``` INSIDE a ~~~ fence: the char+length discipline stays fenced (marker
        # char ` != opening ~), so the fenced `begin` sentinel is ignored and the real
        # region is found. The naive `in_fence = not in_fence` toggle flips on the inner
        # ``` and DESYNCS → it mis-reads fence state around the real region.
        page = self._page_with_region(rid=rid, region="real region")
        fenced = page.replace(
            "body text",
            "body text\n\n~~~\n```\n" + m2._begin_sentinel("dead") + "\n~~~\n")
        self.assertIsNotNone(m2._synthesis_region_bounds(fenced, rid))  # real region still found
        # revert-verify: the naive toggle desyncs on the mismatched fence → mis-bounds
        self.assertNotEqual(_naive_toggle_bounds(fenced, rid),
                            m2._synthesis_region_bounds(fenced, rid))

    def test_ac2c_mismatched_fence_lengths(self):
        rid = "beadfeed"
        # ```` (4 backticks) open, ``` (3) does NOT close → char+len discipline
        body = ("# t\n\n````\n" + m2._begin_sentinel("x") + "\n```\nstill fenced\n````\n\n"
                + m2._begin_sentinel(rid) + "\nreal\n" + m2._END_SENTINEL + "\n")
        page = ("---\nname: proj-x\ntype: project\nsource: agent-extracted\n"
                f"synthesis_region_id: {rid}\n---\n\n" + body)
        b = m2._synthesis_region_bounds(page, rid)
        self.assertIsNotNone(b)
        self.assertEqual(page[b[0]:b[1]].strip(), "real")

    def test_ac2d_malformed_fail_closed_no_eof_replace(self):
        rid = "0badc0de"
        page = self._page_with_region(rid=rid, region="region body")
        broken = page.replace("\n" + m2._END_SENTINEL, "")  # delete end sentinel
        self.assertIsNone(m2._synthesis_region_bounds(broken, rid))  # no valid region
        # apply_region must NOT replace to EOF — it opens a FRESH region instead
        new, new_rid, op = m2.apply_region(broken, "fresh body")
        self.assertEqual(op, "create")
        self.assertNotEqual(new_rid, rid)
        self.assertIn("region body", new)  # malformed bytes preserved, not swallowed
        # revert-verify: an EOF-replace strawman destroys the trailing user bytes
        self.assertNotIn("region body", _eof_replace(broken, rid, "fresh body"))

    def test_ac2d_duplicate_sentinels_fail_closed(self):
        rid = "d00dfeed"
        page = self._page_with_region(rid=rid, region="r")
        dup = page + "\n" + m2._begin_sentinel(rid) + "\nsecond\n" + m2._END_SENTINEL + "\n"
        self.assertIsNone(m2._synthesis_region_bounds(dup, rid))  # duplicate → fail-closed

    def test_ac2e_forged_pair_on_unsynthesized_page(self):
        # page has NO synthesis_region_id; user pastes a complete pair around own bytes
        page = _card("proj-f", body="Intro.\n\n" + m2._begin_sentinel("forged99")
                     + "\nMY OWN BYTES\n" + m2._END_SENTINEL + "\n\nOutro.\n")
        p = self._write("f.md", page)
        m2.process_trigger(self.db, self._trig_path(), self._trig_meta(), "t",
                           neighbors=[{"score": 0.9, "path": p}], confirmer=NO, supersedes=NOSUP)
        after = self._read(p)
        self.assertIn("MY OWN BYTES", after)   # forged region preserved as user bytes
        self.assertIn("Intro.", after)
        self.assertIn("Outro.", after)
        # a fresh, differently-id'd region was opened at the safe anchor
        fresh_id = m2.read_region_id(after)
        self.assertIsNotNone(fresh_id)
        self.assertNotEqual(fresh_id, "forged99")
        # revert-verify: a string-only (un-nonced) locator would revise the forged pair
        self.assertIsNotNone(_string_only_bounds(page))

    # trigger helpers
    def _trig_path(self):
        if not hasattr(self, "_tp"):
            self._tp = self._write("trigger.md", _card("trigger-card", source="user-explicit"))
        return self._tp

    def _trig_meta(self):
        return {"name": "trigger-card", "type": "project", "source": "user-explicit",
                "last_verified": NEW}


# --- FR-6/FR-2/FR-3/FR-7 pipeline via indexing -------------------------------
class PipelineTest(M2Base):
    def _trig(self):
        return self._write("trigger.md", _card("trigger-card", source="user-explicit")), \
            {"name": "trigger-card", "type": "project", "source": "user-explicit", "last_verified": NEW}

    def test_ac3_no_launder(self):
        # managed agent page cold-start 0.40; one M2 edit → observed → ≤0.50, not injected
        p = self._write("target.md", _card("target-page", source="agent-extracted",
                                            body="A managed page body."))
        conn = self._index([p]); self.assertAlmostEqual(self._conf(conn, "target-page"), 0.40); conn.close()
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "t body",
                                 neighbors=[{"score": 0.9, "path": p}], confirmer=NO, supersedes=NOSUP)
        self.assertEqual(out[0]["action"], "edited")
        evs = self._events(p)
        self.assertEqual([e["event_type"] for e in evs], ["observed"])   # NOT confirmed
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [p])
        conf = self._conf(conn, "target-page"); conn.close()
        self.assertLessEqual(conf, 0.50)
        self.assertLess(conf, 0.55, "M2 must not lift a page across the injection gate")

    def test_ac4_contradiction_defers_to_m1_no_overwrite(self):
        m1.register_confirmer(None)
        p = self._write("store-o.md", _card("store-o", source="agent-extracted",
                                             body="The primary datastore is MySQL."))
        conn = self._index([p]); conn.close()
        before = self._read(p)
        tp = self._write("store-n.md", _card("store-n", source="user-explicit",
                                              body="The primary datastore is PostgreSQL."))
        tm = {"name": "store-n", "type": "project", "source": "user-explicit", "last_verified": NEW}
        os.environ["EIDETIC_M1_CONTRADICTION"] = "on"
        try:
            out = m2.process_trigger(self.db, tp, tm, "The primary datastore is PostgreSQL.",
                                     neighbors=[{"score": 0.9, "path": p}],
                                     confirmer=m1.production_confirmer, supersedes=NOSUP)
        finally:
            os.environ.pop("EIDETIC_M1_CONTRADICTION", None)
        self.assertEqual(out[0]["action"], "deferred_to_m1")
        after = self._read(p)
        # M2 made ZERO body edits — no synthesis region opened, original claim intact.
        self.assertNotIn("eidetic:synthesis:begin", after)
        self.assertIn("The primary datastore is MySQL.", after)
        self.assertEqual(before.split("## Evidence")[0].rstrip(),
                         after.split("## Evidence")[0].rstrip())  # body intact (M1 only added ## Evidence)
        # the pair was routed to M1, which emitted the contradicted event on the loser P.
        self.assertEqual([e["event_type"] for e in self._events(p)], ["contradicted"])

    def test_ac5_user_and_imported_untouchable(self):
        pu = self._write("user-fact.md", _card("user-fact", type_="user", source="user-explicit",
                                                body="User profile fact."))
        pi = self._write("ref-doc.md", _card("ref-doc", type_="reference", source="imported",
                                              body="Imported reference."))
        self._index([pu, pi]).close()
        bu, bi = self._read(pu), self._read(pi)
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "t",
                                 neighbors=[{"score": 0.95, "path": pu}, {"score": 0.92, "path": pi}],
                                 confirmer=NO, supersedes=SUP)
        self.assertEqual(sorted(o["action"] for o in out), ["read_only_context", "read_only_context"])
        self.assertEqual(self._read(pu), bu)  # no edit
        self.assertEqual(self._read(pi), bi)
        self.assertEqual(self._events(pu), [])  # no event
        self.assertEqual(self._events(pi), [])

    def test_ac8_gated_supersession(self):
        # a NON-dominating trigger (managed target, but trigger is older/equal-tier)
        # cannot set superseded_by → suggestion, target untouched.
        p_hi = self._write("hi.md", _card("hi-page", source="agent-extracted",
                                           body="plan to ship X"))  # last_verified NEW (newer)
        low_trig = self._write("lo.md", _card("lo-page", source="agent-extracted"))
        lm = {"name": "lo-page", "type": "project", "source": "agent-extracted", "last_verified": OLD}
        before_hi = self._read(p_hi)
        out = m2.process_trigger(self.db, low_trig, lm, "shipped X",
                                 neighbors=[{"score": 0.9, "path": p_hi}], confirmer=NO, supersedes=SUP)
        self.assertEqual(out[0]["action"], "supersession_suggested")
        self.assertEqual(self._read(p_hi), before_hi)  # non-dominated page untouched
        # dominating temporal supersession sets superseded_by + terminal event
        p_lo = self._write("target2.md", _card("target2", source="agent-extracted",
                                               body="the plan to ship target2"))
        self._index([p_lo]).close()
        hi_trig = self._write("hitrig.md", _card("hitrig", source="user-explicit"))
        hm = {"name": "hitrig", "type": "project", "source": "user-explicit", "last_verified": NEW}
        out = m2.process_trigger(self.db, hi_trig, hm, "shipped target2",
                                 neighbors=[{"score": 0.9, "path": p_lo}], confirmer=NO, supersedes=SUP)
        self.assertEqual(out[0]["action"], "superseded")
        self.assertEqual(m2._read_frontmatter_key(self._read(p_lo), "superseded_by"), "hitrig")
        evs = self._events(p_lo)
        self.assertEqual([e["event_type"] for e in evs], ["contradicted"])
        self.assertIn("superseded", evs[0]["note"])

    def test_m1_m2_interaction_xor(self):
        # One card, one pair: gets M1 contradicted XOR M2 supersession terminal — never both.
        os.environ["EIDETIC_M1_CONTRADICTION"] = "on"
        try:
            # (a) real contradiction → M1 emits, M2 defers (no supersession terminal)
            p = self._write("c.md", _card("c-page", source="agent-extracted",
                                          body="Feature flags are enabled by default."))
            self._index([p]).close()
            tp = self._write("ct.md", _card("ct-page", source="user-explicit"))
            tm = {"name": "ct-page", "type": "project", "source": "user-explicit", "last_verified": NEW}
            m2.process_trigger(self.db, tp, tm, "Feature flags are disabled by default.",
                               neighbors=[{"score": 0.9, "path": p}],
                               confirmer=m1.production_confirmer,
                               supersedes=lambda t, x: True)  # even if supersedes says yes...
            evs = self._events(p)
            types = [e["event_type"] for e in evs]
            self.assertEqual(types, ["contradicted"])   # exactly one; from M1
            self.assertNotIn("superseded", (evs[0]["note"] or ""))  # NOT the M2 terminal
        finally:
            os.environ.pop("EIDETIC_M1_CONTRADICTION", None)


# --- FR-8/FR-5 idempotence + provenance via the real ingest hook -------------
class IngestHookTest(M2Base):
    def _wire(self, neigh_for):
        self._saved_nvd = m1.neighbors_via_door
        m1.neighbors_via_door = lambda db, probe, exclude_paths=(): neigh_for(exclude_paths)

    def test_ac6_idempotent_full_equals_incremental(self):
        tgt = self._write("t.md", _card("t-page", source="agent-extracted", body="target body"))
        trg = self._write("s.md", _card("s-page", source="user-explicit", body="signal body"))
        self._wire(lambda excl: [] if tgt in excl else [{"score": 0.9, "path": tgt}])
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [tgt, trg]); conn.close()
        after1 = self._read(tgt); evs1 = self._events(tgt)
        # second pass: same trigger, same corpus → no new edit, no new event
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [tgt, trg]); conn.close()
        after2 = self._read(tgt); evs2 = self._events(tgt)
        self.assertEqual(after1, after2, "re-ingest changed page bytes (not idempotent)")
        self.assertEqual(len(evs1), len(evs2), "re-ingest added a duplicate event")
        self.assertTrue(any("M2 synthesis" in ln for ln in after1.splitlines()))  # provenance present

    def test_ac7_provenance_survives_reindex(self):
        tgt = self._write("t.md", _card("t-page", source="agent-extracted", body="target body"))
        trg = self._write("s.md", _card("s-page", source="user-explicit", body="signal body"))
        self._wire(lambda excl: [] if tgt in excl else [{"score": 0.9, "path": tgt}])
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [tgt, trg]); conn.close()
        prov = [ln for ln in self._read(tgt).splitlines() if "M2 synthesis · trigger=" in ln]
        self.assertTrue(prov, "no provenance line written")
        # reindex only (no new trigger content) → provenance still in file bytes
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [tgt, trg]); conn.close()
        prov2 = [ln for ln in self._read(tgt).splitlines() if "M2 synthesis · trigger=" in ln]
        self.assertEqual(prov, prov2)


# --- FR-9 dark-safe ----------------------------------------------------------
class DarkSafeTest(M2Base):
    def test_ac9_dark_safe_no_op(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)  # OFF
        p = self._write("d.md", _card("d-page", source="agent-extracted", body="body"))
        before = self._read(p)
        out = m2.process_trigger(self.db, "/x/trigger.md",
                                 {"name": "t", "type": "project", "source": "user-explicit"},
                                 "t", neighbors=[{"score": 0.99, "path": p}], confirmer=NO, supersedes=SUP)
        self.assertEqual(out, [])                 # complete no-op
        self.assertEqual(self._read(p), before)   # zero page mutation
        self.assertEqual(self._events(p), [])     # zero event

    def test_dormant_when_m2_flag_off(self):
        # events ON (the rail) but the M2 activation flag OFF → still a complete no-op
        # (M2 mutates content, so it stays dormant until explicitly enabled).
        os.environ.pop("EIDETIC_M2_SYNTHESIS", None)
        self.assertFalse(m2.m2_enabled())
        p = self._write("d.md", _card("d-page", source="agent-extracted", body="body"))
        before = self._read(p)
        out = m2.process_trigger(self.db, "/x/trigger.md",
                                 {"name": "t", "type": "project", "source": "user-explicit"},
                                 "t", neighbors=[{"score": 0.99, "path": p}], confirmer=NO, supersedes=SUP)
        self.assertEqual(out, [])
        self.assertEqual(self._read(p), before)
        self.assertEqual(self._events(p), [])

    def test_ac9_require_v1_intact(self):
        import engine
        engine.require("1")
        self.assertEqual(engine.ENGINE_API, "1.1")


# --- strawman locators (encode the revert-verify: these MISHANDLE the fixtures) --
def _heading_title_locator(content, title):
    """F-R2-1 strawman: locate a region by an H2 title (compound._history_section_bounds
    shape). Mis-treats a user H2 as the region boundary."""
    for m in re.finditer(r"(?m)^##\s+(.+?)\s*$", content):
        if m.group(1) == title:
            return m.end()
    return None


def _naive_toggle_bounds(content, rid):
    """F-R2-3 strawman: naive in_fence = not in_fence toggle (compound._markdown_headings)."""
    in_fence = False
    begin = end = None
    off = 0
    for line in content.splitlines(keepends=True):
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            off += len(line); continue
        if not in_fence:
            bm = m2._BEGIN_RE.match(line.strip())
            if bm and bm.group(1) == rid and begin is None:
                begin = off + len(line)
            elif m2._END_RE.match(line.strip()) and begin is not None and end is None:
                end = off
        off += len(line)
    return (begin, end) if begin is not None and end is not None else None


def _eof_replace(content, rid, body):
    """F6-reopen strawman: on a missing end sentinel, replace from begin to EOF."""
    m = re.search(re.escape(m2._begin_sentinel(rid).split(" id=")[0]) + r".*?-->", content)
    if not m:
        return content
    return content[:m.end()] + "\n" + body + "\n"


def _string_only_bounds(content):
    """F-R3-1 strawman: match ANY begin…end pair regardless of id (un-nonced)."""
    b = re.search(r"<!--\s*eidetic:synthesis:begin.*?-->", content)
    e = re.search(r"<!--\s*eidetic:synthesis:end\s*-->", content)
    if b and e and e.start() > b.end():
        return b.end(), e.start()
    return None


# =========================== TURN 2 (D1..D5) ================================
class TriggerMixin:
    def _trig(self):
        return self._write("trigger.md", _card("trigger-card", source="user-explicit")), \
            {"name": "trigger-card", "type": "project", "source": "user-explicit", "last_verified": NEW}


# --- D1: real deterministic consolidation body -------------------------------
class D1BodyTest(M2Base, TriggerMixin):
    def test_default_body_is_deterministic_consolidation(self):
        # trigger + two co-related managed pages → the region on each consolidates
        # the related set as [[link]] — one-liner rows (deterministic slug order).
        a = self._write("page-a.md", _card("page-a", source="agent-extracted",
                                            body="Alpha claim about the auth service."))
        b = self._write("page-b.md", _card("page-b", source="agent-extracted",
                                            body="Beta claim about the auth service."))
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "auth service trigger claim",
                                 neighbors=[{"score": 0.9, "path": a}, {"score": 0.88, "path": b}],
                                 confirmer=NO, supersedes=NOSUP)
        self.assertEqual([o["action"] for o in out], ["edited", "edited"])
        body_a = m2.current_region_body(self._read(a))
        # page-a's region references the trigger AND the co-related page-b (not itself)
        self.assertIn("[[trigger-card]]", body_a)
        self.assertIn("[[page-b]]", body_a)
        self.assertNotIn("[[page-a]]", body_a)
        self.assertIn("Consolidated related context", body_a)
        # deterministic: identical inputs → identical body
        c1 = self._read(a)
        m2.process_trigger(self.db, tp, tm, "auth service trigger claim",
                           neighbors=[{"score": 0.9, "path": a}, {"score": 0.88, "path": b}],
                           confirmer=NO, supersedes=NOSUP)
        self.assertEqual(self._read(a), c1)  # FR-8: re-run no-op with the real body

    def test_real_body_preserves_ac2_dual_invariant(self):
        # the real (non-trivial) body still leaves every user byte before the region
        # byte-identical, and appends only the FR-6 observed event after it.
        page = _card("proj-x", body="User A.\n\n## Notes\nUser B.\n")
        p = self._write("x.md", page)
        n = self._write("page-n.md", _card("page-n", source="agent-extracted",
                                            body="Neighbor salient claim."))
        tp, tm = self._trig()
        before = self._read(p)
        m2.process_trigger(self.db, tp, tm, "t",
                           neighbors=[{"score": 0.9, "path": p}, {"score": 0.85, "path": n}],
                           confirmer=NO, supersedes=NOSUP)
        after = self._read(p)
        # user body (frontmatter aside) is byte-identical; the region is a fresh
        # append after all user bytes; the only tail change is the ## Evidence event.
        _mb, body_before = index_impl.parse_frontmatter(before)
        user_part = after.split("<!-- eidetic:synthesis:begin")[0]
        self.assertIn(body_before.rstrip(), after)
        self.assertIn("User A.", user_part)
        self.assertIn("User B.", user_part)
        self.assertIn("[[trigger-card]]", m2.current_region_body(after))

    def test_bounded_growth_real_body(self):
        p = self._write("g.md", _card("g-page", source="agent-extracted", body="User body."))
        n = self._write("gn.md", _card("gn-page", source="agent-extracted", body="Neighbor."))
        tp, tm = self._trig()
        sizes = []
        for i in range(5):
            m2.process_trigger(self.db, tp, tm, f"trig {i}",
                               neighbors=[{"score": 0.9, "path": p}, {"score": 0.85, "path": n}],
                               confirmer=NO, supersedes=NOSUP)
            sizes.append(len(self._read(p)))
        self.assertLessEqual(max(sizes) - min(sizes), 3, f"unbounded growth {sizes}")


# --- D2: supersession classifier ---------------------------------------------
class D2SupersedesTest(M2Base, TriggerMixin):
    def _run(self, target_body, trig_body, trig_src="user-explicit"):
        p = self._write("target.md", _card("t2-page", source="agent-extracted", body=target_body))
        self._index([p]).close()
        trig = self._write("s2.md", _card("t2-page-src", source=trig_src))
        tm = {"name": "t2-page-src", "type": "project", "source": trig_src, "last_verified": NEW}
        out = m2.process_trigger(self.db, trig, tm, trig_body,
                                 neighbors=[{"score": 0.9, "path": p}],
                                 confirmer=NO)  # default supersedes classifier
        return p, out

    def test_plan_to_shipped_supersedes(self):
        p, out = self._run("the plan to ship t2 page is drafted", "shipped t2 page to production")
        self.assertEqual(out[0]["action"], "superseded")
        self.assertEqual(m2._read_frontmatter_key(self._read(p), "superseded_by"), "t2-page-src")

    def test_version_bump_supersedes(self):
        p, out = self._run("t2 page API is v1", "t2 page API is now v2")
        self.assertEqual(out[0]["action"], "superseded")

    def test_year_evolution_supersedes(self):
        p, out = self._run("t2 page report for 2023", "t2 page report for 2024")
        self.assertEqual(out[0]["action"], "superseded")

    def test_true_opposition_stays_m1_not_supersession(self):
        # enabled↔disabled is a semantic contradiction → M1 owns it, M2 never supersedes
        os.environ["EIDETIC_M1_CONTRADICTION"] = "on"
        try:
            p = self._write("c2.md", _card("c2-page", source="agent-extracted",
                                            body="Feature flags are enabled by default."))
            self._index([p]).close()
            trig = self._write("c2t.md", _card("c2-page-src", source="user-explicit"))
            tm = {"name": "c2-page-src", "type": "project", "source": "user-explicit", "last_verified": NEW}
            out = m2.process_trigger(self.db, trig, tm, "Feature flags are disabled by default.",
                                     neighbors=[{"score": 0.9, "path": p}],
                                     confirmer=m1.production_confirmer)  # real confirmer
            self.assertEqual(out[0]["action"], "deferred_to_m1")
            evs = self._events(p)
            self.assertEqual([e["event_type"] for e in evs], ["contradicted"])
            self.assertNotIn("superseded", evs[0]["note"] or "")  # NOT an M2 terminal
            self.assertIsNone(m2._read_frontmatter_key(self._read(p), "superseded_by"))
        finally:
            os.environ.pop("EIDETIC_M1_CONTRADICTION", None)

    def test_revert_verify_authority_gate(self):
        # a NON-dominating trigger must NOT set superseded_by — with the gate it's a
        # suggestion; disabling the gate (revert) lets it set → RED-on-break.
        p = self._write("hp.md", _card("hp-page", source="agent-extracted", body="the plan for hp"))
        self._index([p]).close()
        trig = self._write("lp.md", _card("hp-page-src", source="agent-extracted"))
        tm = {"name": "hp-page-src", "type": "project", "source": "agent-extracted", "last_verified": OLD}
        out = m2.process_trigger(self.db, trig, tm, "shipped hp",
                                 neighbors=[{"score": 0.9, "path": p}], confirmer=NO)
        self.assertEqual(out[0]["action"], "supersession_suggested")
        self.assertIsNone(m2._read_frontmatter_key(self._read(p), "superseded_by"))
        # revert: bypass the authority gate → non-dominating source sets it (the bug)
        orig = m2._authority_dominates
        m2._authority_dominates = lambda t, x: True
        try:
            p2 = self._write("hp2.md", _card("hp2-page", source="agent-extracted", body="the plan for hp2"))
            self._index([p2]).close()
            tm2 = {"name": "hp2-page-src", "type": "project", "source": "agent-extracted", "last_verified": OLD}
            trig2 = self._write("lp2.md", _card("hp2-page-src", source="agent-extracted"))
            m2.process_trigger(self.db, trig2, tm2, "shipped hp2",
                               neighbors=[{"score": 0.9, "path": p2}], confirmer=NO)
            self.assertEqual(m2._read_frontmatter_key(self._read(p2), "superseded_by"), "hp2-page-src",
                             "revert-verify: without the authority gate a non-dominating source sets it")
        finally:
            m2._authority_dominates = orig


# --- D3: spool-under-lock ----------------------------------------------------
class D3ConcurrencyTest(M2Base, TriggerMixin):
    def _counter_race(self, use_lock):
        """Classic lost-update probe on the SAME lock primitive M2's _edit_page uses
        (evidence.card_lock). With the lock two increments serialize → 2; without it,
        a forced-interleave barrier makes both read 0 then write 1 → 1 (lost update)."""
        import threading, contextlib
        cf = self._write("cnt.txt", "0")
        barrier = None if use_lock else threading.Barrier(2)

        def inc():
            if use_lock:
                with EV.card_lock(cf) as held:
                    self.assertTrue(held)
                    v = int(open(cf).read().strip()) + 1
                    with open(cf, "w") as f:
                        f.write(str(v))
            else:
                v = int(open(cf).read().strip())
                barrier.wait(timeout=3)  # both read 0 before either writes
                with open(cf, "w") as f:
                    f.write(str(v + 1))
        ts = [threading.Thread(target=inc) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=5)
        return int(open(cf).read().strip())

    def test_card_lock_serializes_no_lost_update(self):
        self.assertEqual(self._counter_race(use_lock=True), 2)

    def test_revert_verify_no_lock_loses_update(self):
        # REVERT-VERIFY: remove the lock (no-op) → the forced race loses an update.
        self.assertEqual(self._counter_race(use_lock=False), 1)

    def test_concurrent_m2_edits_leave_file_consistent(self):
        # two concurrent M2 edits (distinct triggers) to the same card under the REAL
        # spool-under-lock → the file stays CONSISTENT: exactly one well-formed region
        # whose id matches the frontmatter, user bytes intact, no torn/interleaved
        # write. (The region is replaced → last-writer-wins content; the invariant is
        # no corruption / no id↔region desync, which the lock guarantees.)
        import threading
        p = self._write("shared.md", _card("shared", source="agent-extracted",
                                            body="User content stays.\n\n## Sec\nMore user."))

        def edit(slug):
            trig = self._write(f"{slug}.md", _card(slug, source="user-explicit"))
            tm = {"name": slug, "type": "project", "source": "user-explicit", "last_verified": NEW}
            m2.process_trigger(self.db, trig, tm, f"claim from {slug}",
                               neighbors=[{"score": 0.9, "path": p}], confirmer=NO, supersedes=NOSUP)
        ts = [threading.Thread(target=edit, args=(f"trig-{i}",)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=5)
        after = self._read(p)
        self.assertEqual(after.count("eidetic:synthesis:begin"), 1)  # exactly one region
        self.assertEqual(after.count("eidetic:synthesis:end"), 1)
        rid = m2.read_region_id(after)
        self.assertIsNotNone(m2._synthesis_region_bounds(after, rid),
                             "frontmatter id ↔ region desynced (corruption under contention)")
        self.assertIn("User content stays.", after)   # user bytes intact
        self.assertIn("More user.", after)


# --- D4: op-log schema + reindex survival ------------------------------------
class D4OplogTest(M2Base, TriggerMixin):
    def _wire(self, tgt):
        self._saved_nvd = m1.neighbors_via_door
        m1.neighbors_via_door = lambda db, probe, exclude_paths=(): (
            [] if tgt in exclude_paths else [{"score": 0.9, "path": tgt}])

    def _oplog_rows(self):
        log = os.path.join(os.path.dirname(os.path.dirname(self.db)), "log.md")
        if not os.path.exists(log):
            return []
        return [ln for ln in open(log, encoding="utf-8").read().splitlines()
                if "op=synthesis_edit" in ln or "op=supersession" in ln]

    def test_oplog_schema_and_reindex_survival(self):
        tgt = self._write("t.md", _card("t-page", source="agent-extracted", body="target body"))
        trg = self._write("s.md", _card("s-page", source="user-explicit", body="signal body"))
        self._wire(tgt)
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [tgt, trg]); conn.close()
        rows = self._oplog_rows()
        self.assertTrue(rows, "no op-log row written")
        row = rows[0]
        for field in ("op=synthesis_edit", "target=", "date=", "trigger=s-page", "source=user-explicit", "score="):
            self.assertIn(field, row)
        # survives a --full rebuild of the index (op-log is at the memory-system root)
        conn = index_impl.init_db(self.db); index_impl.run_full(conn, [tgt, trg]); conn.close()
        self.assertTrue(self._oplog_rows(), "op-log did not survive --full reindex")
        # idempotent: re-ingest with no change adds NO duplicate op row
        n_before = len(self._oplog_rows())
        conn = index_impl.init_db(self.db); index_impl.run_incremental(conn, [tgt, trg]); conn.close()
        self.assertEqual(len(self._oplog_rows()), n_before, "duplicate op rows on idempotent re-run")

    def test_oplog_never_touches_global_log(self):
        # with no index_db_path the op-log is skipped entirely (never the live global).
        self.assertIsNone(m2._log_path_for(None))


# --- D5: Leg-A e2e with a real vectors.db ------------------------------------
class D5LegATest(M2Base, TriggerMixin):
    @unittest.skipUnless(_fastembed_available(), "Leg-A e2e requires fastembed")
    def test_real_vectors_end_to_end(self):
        import engine
        engine.configure(provider="cpu", threads=8)
        # three managed pages on ONE topic + a trigger on the same topic
        a = self._write("auth-tokens.md", _card("auth-tokens", source="agent-extracted",
                        body="The auth service issues JWT access tokens for sessions."))
        b = self._write("auth-refresh.md", _card("auth-refresh", source="agent-extracted",
                        body="Refresh tokens rotate the JWT session for the auth service daily."))
        far = self._write("coffee.md", _card("coffee", source="agent-extracted",
                          body="The office coffee machine broke again on Friday."))
        trg = self._write("auth-trigger.md", _card("auth-trigger", source="user-explicit",
                          body="The auth service JWT token lifetime and session policy."))
        conn = self._index([a, b, far, trg]); conn.close()
        vectors_db = self.db.replace("index.db", "vectors.db")
        engine._embed().run_full(self.db, vectors_db)
        # run the REAL hook: door → Index.neighbors → M2_FANOUT → M2_RELATED_MIN → edit
        conn = index_impl.init_db(self.db)
        m2.run_on_ingest(conn, self.db, [trg], confirmer=NO, supersedes=NOSUP)
        conn.close()
        # the on-topic auth pages get a synthesis region; the far coffee page does not
        self.assertIn("eidetic:synthesis:begin", self._read(a))
        self.assertIn("eidetic:synthesis:begin", self._read(b))
        self.assertNotIn("eidetic:synthesis:begin", self._read(far))
        # provenance + consolidation present, user bytes intact
        self.assertIn("[[auth-trigger]]", m2.current_region_body(self._read(a)))
        self.assertIn("The auth service issues JWT access tokens", self._read(a))


# ===================== M2.1 — relevance-gated synthesis =====================
class M21RelevanceGateTest(M2Base, TriggerMixin):
    def _run(self, relevance_fn):
        p = self._write("target.md", _card("target-page", source="agent-extracted",
                                            body="A managed durable knowledge page."))
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "trigger claim text",
                                 neighbors=[{"score": 0.9, "path": p}],
                                 confirmer=NO, supersedes=NOSUP, relevance_fn=relevance_fn)
        return p, out

    def _oplog_count(self, verb):
        log = os.path.join(os.path.dirname(os.path.dirname(self.db)), "log.md")
        return open(log, encoding="utf-8").read().count(f"op={verb}") if os.path.exists(log) else 0

    def test_gate_admits_when_relevant(self):
        p, out = self._run(lambda a, b: 0.5)   # ≥ 0.0 floor
        self.assertEqual(out[0]["action"], "edited")
        self.assertIn("eidetic:synthesis:begin", self._read(p))

    def test_gate_rejects_when_irrelevant(self):
        p, out = self._run(lambda a, b: -1.0)  # < 0.0 floor → spurious cross-link
        self.assertEqual(out[0]["action"], "relevance_skipped")
        self.assertNotIn("eidetic:synthesis:begin", self._read(p))  # NO edit
        self.assertEqual(self._events(p), [])                       # NO event
        self.assertEqual(self._oplog_count("relevance_skipped"), 1)  # surfaced once

    def test_gate_dedups_relevance_skips(self):
        p = self._write("t.md", _card("t-page", source="agent-extracted", body="body"))
        tp, tm = self._trig()
        for _ in range(4):
            m2.process_trigger(self.db, tp, tm, "trig", neighbors=[{"score": 0.9, "path": p}],
                               confirmer=NO, supersedes=NOSUP, relevance_fn=lambda a, b: -2.0)
        self.assertEqual(self._oplog_count("relevance_skipped"), 1)  # deduped, no growth

    def test_fail_closed_on_none(self):
        # relevance_fn returns None (reranker unavailable) → NO edit (never cosine-only)
        p, out = self._run(lambda a, b: None)
        self.assertEqual(out[0]["action"], "relevance_skipped")
        self.assertNotIn("eidetic:synthesis:begin", self._read(p))
        self.assertEqual(self._events(p), [])

    def test_default_reranker_absent_is_fail_closed(self):
        # with the REAL default scorer (engine.rerank — unprovisioned here) M2 edits
        # NOTHING: safe-by-default, no reranker ⇒ no synthesis.
        m2.register_relevance(None)  # undo the M2Base admit-all mock
        p = self._write("d.md", _card("d-page", source="agent-extracted", body="body"))
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.9, "path": p}],
                                 confirmer=NO, supersedes=NOSUP)
        self.assertEqual(out[0]["action"], "relevance_skipped")
        self.assertNotIn("eidetic:synthesis:begin", self._read(p))

    def test_revert_verify_gate_dropped_edits_spurious(self):
        # REVERT-VERIFY: neuter the gate → a below-floor pair gets edited → the guard
        # is load-bearing. (Simulated by a scorer the real code would reject.)
        p = self._write("s.md", _card("s-page", source="agent-extracted", body="body"))
        tp, tm = self._trig()
        # with the gate: -1.0 rejects
        out = m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.9, "path": p}],
                                 confirmer=NO, supersedes=NOSUP, relevance_fn=lambda a, b: -1.0)
        self.assertEqual(out[0]["action"], "relevance_skipped")
        # a cosine-only strawman (ignore the score) WOULD edit — proven live in the
        # code revert-verify (see M2-PROGRESS §M2.1); here assert the gate discriminates.
        out2 = m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.9, "path": p}],
                                  confirmer=NO, supersedes=NOSUP, relevance_fn=lambda a, b: 0.01)
        self.assertEqual(out2[0]["action"], "edited")


# --- M2.1 F2: exempt behavioral + transient cards ----------------------------
class M21ExemptTest(M2Base, TriggerMixin):
    def _neighbor_out(self, card_text, supersedes=SUP):
        p = self._write("n.md", card_text)
        tp, tm = self._trig()
        return m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.95, "path": p}],
                                  confirmer=NO, supersedes=supersedes, relevance_fn=lambda a, b: 5.0)

    def test_feedback_never_edited(self):
        out = self._neighbor_out(_card("rule-x", type_="feedback", source="agent-extracted",
                                       body="Always do the thing."))
        self.assertEqual(out[0]["action"], "read_only_context")

    def test_todo_never_edited(self):
        out = self._neighbor_out(_card("todo-x", type_="project", source="agent-extracted",
                                       body="Next: finish the thing.", extra_fm="card_kind: todo"))
        self.assertEqual(out[0]["action"], "read_only_context")

    def test_handoff_never_edited(self):
        out = self._neighbor_out(_card("handoff-x", type_="project", source="agent-extracted",
                                       body="Session handoff notes.", extra_fm="card_kind: handoff"))
        self.assertEqual(out[0]["action"], "read_only_context")

    def test_durable_project_still_editable(self):
        out = self._neighbor_out(_card("proj-x", type_="project", source="agent-extracted",
                                       body="Durable project knowledge."), supersedes=NOSUP)
        self.assertEqual(out[0]["action"], "edited")


# --- M2.1 F3: clean salient ---------------------------------------------------
class M21SalientTest(M2Base):
    def test_messy_line_cleaned(self):
        msg = "`reddit_frontier.py` `novelty_pass` is O(N²) over ~1000 threads.** Found"
        out = m2._salient_claim({"text": msg + "\n", "name": "finding-perf", "slug": "finding-perf"})
        self.assertNotIn("**", out)
        self.assertNotIn("`", out)
        self.assertLessEqual(len(out), 90)
        self.assertFalse(out.endswith("**"))
        self.assertNotRegex(out, r"\w…\w")  # no mid-word ellipsis cut

    def test_bold_wrapped_line(self):
        out = m2._clean_oneliner("**Always run the gateway tests** before merge")
        self.assertEqual(out, "Always run the gateway tests before merge")

    def test_word_boundary_cap(self):
        long = "word " * 40
        out = m2._clean_oneliner(long, limit=30)
        self.assertLessEqual(len(out), 31)          # ≤ limit + ellipsis
        self.assertTrue(out.endswith("…"))
        self.assertNotIn("wor…", out)               # never a mid-word cut

    def test_noise_first_line_falls_back_to_name(self):
        out = m2._salient_claim({"text": "|table|header|\n", "name": "the-card-name", "slug": "s"})
        self.assertEqual(out, "the-card-name")


# --- FIX §R1 / F1: user-broken region → bounded skip (A1.7) ------------------
class R1BrokenRegionTest(M2Base, TriggerMixin):
    def _oplog_count(self, verb):
        log = os.path.join(os.path.dirname(os.path.dirname(self.db)), "log.md")
        if not os.path.exists(log):
            return 0
        return open(log, encoding="utf-8").read().count(f"op={verb}")

    def _broken_page(self, rid="RID123"):
        # frontmatter id present, begin sentinel present, END sentinel DELETED
        return ("---\nname: broke\ntype: project\nsource: agent-extracted\n"
                f"synthesis_region_id: {rid}\n---\n\nUser paragraph stays.\n\n"
                f"{m2._begin_sentinel(rid)}\norphan begin, no end sentinel\n")

    def test_f1_broken_region_bounded_and_deduped(self):
        p = self._write("broke.md", self._broken_page())
        tp, tm = self._trig()
        sizes, begins = [], []
        for _ in range(5):
            out = m2.process_trigger(self.db, tp, tm, "trigger body",
                                     neighbors=[{"score": 0.9, "path": p}],
                                     confirmer=NO, supersedes=NOSUP)
            self.assertEqual(out[0]["action"], "broken_region_skipped")  # SKIP, no create
            c = self._read(p)
            sizes.append(len(c))
            begins.append(c.count("eidetic:synthesis:begin"))
        # BOUNDED: page bytes constant across ingests (NOT +264 each)
        self.assertEqual(len(set(sizes)), 1, f"page grew on a broken region: {sizes}")
        # begin-sentinel count does NOT grow (stays the single orphan)
        self.assertEqual(set(begins), {1}, f"begin sentinels grew: {begins}")
        # zero user-byte loss
        self.assertIn("User paragraph stays.", self._read(p))
        # exactly ONE op-log suggestion (deduped, not re-emitted every ingest)
        self.assertEqual(self._oplog_count("region_broken"), 1)

    def test_f1_preserves_first_create_and_forged(self):
        # (i) first-ever synthesis — NO frontmatter id → still CREATES a fresh region
        first = self._write("first.md", _card("first-page", source="agent-extracted", body="Body."))
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.9, "path": first}],
                                 confirmer=NO, supersedes=NOSUP)
        self.assertEqual(out[0]["action"], "edited")
        self.assertIn("eidetic:synthesis:begin", self._read(first))
        # (ii) AC-2e casual-forge — forged pair, NO matching frontmatter id → fresh region
        forged = self._write("forged.md", _card("forged-page", source="agent-extracted",
                             body="Intro.\n\n" + m2._begin_sentinel("forged99")
                             + "\nMY OWN BYTES\n" + m2._END_SENTINEL + "\n\nOutro.\n"))
        out = m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.9, "path": forged}],
                                 confirmer=NO, supersedes=NOSUP)
        self.assertEqual(out[0]["action"], "edited")
        after = self._read(forged)
        self.assertIn("MY OWN BYTES", after)  # forged bytes preserved
        fresh = m2.read_region_id(after)
        self.assertNotEqual(fresh, "forged99")

    def test_f1_dark_safe(self):
        # with M2 off the broken-region path is also a no-op (no mutation, no op-log)
        os.environ.pop("EIDETIC_M2_SYNTHESIS", None)
        p = self._write("broke.md", self._broken_page())
        before = self._read(p)
        tp, tm = self._trig()
        out = m2.process_trigger(self.db, tp, tm, "t", neighbors=[{"score": 0.9, "path": p}],
                                 confirmer=NO, supersedes=NOSUP)
        self.assertEqual(out, [])
        self.assertEqual(self._read(p), before)
        self.assertEqual(self._oplog_count("region_broken"), 0)


if __name__ == "__main__":
    unittest.main()
