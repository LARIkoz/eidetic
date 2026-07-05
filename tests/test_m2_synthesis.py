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

    def tearDown(self):
        os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
        os.environ.pop("EIDETIC_M2_SYNTHESIS", None)
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


if __name__ == "__main__":
    unittest.main()
