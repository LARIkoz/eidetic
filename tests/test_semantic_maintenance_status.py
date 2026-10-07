"""Retry evidence is separate from empty matches and from safe write decisions."""

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import engine
import index_impl as idx
import m1_contradiction as m1
import m2_synthesis as m2
from maintenance_status import capture_failures, note_failure


class SemanticMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="eidetic-semantic-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = mock.patch.dict(os.environ, {
            "EIDETIC_CONFIDENCE_EVENTS": "on", "EIDETIC_M2_SYNTHESIS": "on",
            "EIDETIC_M1_CONTRADICTION": "off", "EIDETIC_PRODUCER": "off",
            "EIDETIC_RESOURCE_ROOT": str(self.root / "budget"),
            "EIDETIC_INGEST_BATCH_SIZE": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.db = str(self.root / "db/index.db")
        self.conn = idx.init_db(self.db)
        self.addCleanup(self.conn.close)
        self.trigger = self.card("trigger")
        self.target = self.card("target")
        self.hits = [{"path": self.target, "score": 0.99}]
        idx.set_pending_ingest_paths(self.conn, [self.trigger, self.target])
        self.conn.commit()

    def card(self, name):
        path = self.root / ".claude/projects/example/memory" / (name + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: " + name + "\ntype: project\nsource: agent-extracted\n---\n\nA durable project policy.\n")
        return str(path)

    def status(self):
        row = self.conn.execute("SELECT value FROM schema_meta WHERE key = ?",
                                ("semantic_maintenance_status_v1",)).fetchone()
        return json.loads(row[0])

    def drain_one(self):
        return idx.maintain_pending(self.conn, idx.pending_ingest_paths(self.conn), True)

    def test_swallowed_ingest_error_retained_and_recovered_without_file_edit(self):
        with mock.patch.object(m1, "_record_from_file", side_effect=OSError("private data")), \
                mock.patch.object(m2, "run_on_ingest") as second:
            self.assertEqual(self.drain_one(), [self.target, self.trigger])
            second.assert_not_called()
        self.assertEqual(self.status()["failures"][self.trigger]["issues"][0],
                         {"stage": "m1_ingest", "reason": "OSError"})
        self.assertNotIn("private data", json.dumps(self.status()))
        with mock.patch.object(m1, "neighbors_via_door", return_value=[]):
            self.assertEqual(self.drain_one(), [self.trigger])
            self.assertIn(self.trigger, self.status()["failures"])
            self.assertEqual(self.drain_one(), [])
        self.assertEqual(self.status()["failures"], {})
        self.assertEqual(self.status()["last_attempt"]["status"], "completed")

    def test_engine_soft_failure_reaches_queue_consumer(self):
        Path(self.db.replace("index.db", "vectors.db")).touch()
        instance = object.__new__(engine.Index)
        instance._conn = None
        with mock.patch.dict(sys.modules, {"numpy": types.SimpleNamespace()}), \
                mock.patch.object(engine, "open_index", return_value=instance), \
                mock.patch.object(engine, "_embed", side_effect=RuntimeError("model absent")):
            self.assertEqual(self.drain_one(), [self.target, self.trigger])
        self.assertIn({"stage": "neighbors", "reason": "RuntimeError"},
                      self.status()["failures"][self.trigger]["issues"])

    def test_m2_reranker_unavailable_is_retry_not_success(self):
        with mock.patch.object(m1, "neighbors_via_door", return_value=self.hits), \
                mock.patch.object(engine, "rerank", return_value=[]):
            self.assertEqual(self.drain_one(), [self.target, self.trigger])
        self.assertIn({"stage": "m2_relevance", "reason": "unavailable"},
                      self.status()["failures"][self.trigger]["issues"])
        self.assertNotIn("synthesis_region_id", Path(self.target).read_text())

    def test_low_relevance_is_completed_not_retry(self):
        with mock.patch.object(m1, "neighbors_via_door", return_value=self.hits), \
                mock.patch.object(engine, "rerank", return_value=[-10.0]):
            self.assertEqual(self.drain_one(), [self.target])
        self.assertEqual(self.status()["failures"], {})

    def test_disabled_m2_does_not_wait_for_reranker(self):
        with mock.patch.dict(os.environ, {"EIDETIC_M2_SYNTHESIS": "off"}), \
                mock.patch.object(m1, "neighbors_via_door", return_value=self.hits), \
                mock.patch.object(engine, "rerank") as rerank:
            self.assertEqual(self.drain_one(), [self.target])
            rerank.assert_not_called()

    def test_m2_confirmer_and_supersedes_errors_cannot_authorize_edits(self):
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        original = Path(self.target).read_text()
        for failing in ("confirmer", "supersedes"):
            kwargs = {"confirmer": lambda a, b: "no_contradiction",
                      "supersedes": lambda a, b: False,
                      "relevance_fn": lambda a, b: 5.0}
            kwargs[failing] = mock.Mock(side_effect=RuntimeError("unavailable"))
            with capture_failures() as failures:
                outcomes = m2.process_trigger(self.db, self.trigger, meta, "policy",
                                              neighbors=self.hits, **kwargs)
            self.assertEqual(outcomes[0]["action"], failing + "_unavailable")
            self.assertTrue(failures)
            self.assertEqual(Path(self.target).read_text(), original)

    def test_region_and_event_commit_atomically_after_failure(self):
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        kwargs = {"neighbors": self.hits, "confirmer": lambda a, b: "no_contradiction",
                  "supersedes": lambda a, b: False, "relevance_fn": lambda a, b: 5.0}
        import evidence
        real_replace = os.replace
        def fail_event(src, dst):
            if str(src).endswith(".evtmp"):
                raise OSError("event write interrupted")
            return real_replace(src, dst)
        with capture_failures() as failures, mock.patch.object(evidence.os, "replace", side_effect=fail_event):
            m2.process_trigger(self.db, self.trigger, meta, "policy", **kwargs)
        self.assertIn({"stage": "evidence_write", "reason": "OSError"}, failures)
        self.assertNotIn("synthesis_region_id", Path(self.target).read_text())
        recovered = m2.process_trigger(self.db, self.trigger, meta, "policy", **kwargs)
        self.assertEqual(recovered[0]["action"], "edited")
        repeated = m2.process_trigger(self.db, self.trigger, meta, "policy", **kwargs)
        self.assertEqual(repeated[0]["action"], "idempotent_skip")
        events = idx.parse_evidence_events(Path(self.target).read_text())
        self.assertEqual(len([e for e in events if e["event_type"] == "observed"]), 1)

    def test_supersession_and_event_commit_atomically_after_failure(self):
        trigger = m1._record(self.trigger, {"name": "trigger", "type": "project",
                             "source": "user-explicit", "last_verified": "2026-10-07"}, "policy")
        target = m1._record_from_file(self.target)
        real_replace = os.replace
        def fail_event(src, dst):
            if str(src).endswith(".evtmp"):
                raise OSError("interrupted event")
            return real_replace(src, dst)
        with capture_failures() as failures, mock.patch.object(m2._EV.os, "replace", side_effect=fail_event):
            m2._apply_supersession(self.db, trigger, target)
        self.assertTrue(failures)
        self.assertNotIn("superseded_by", Path(self.target).read_text())
        self.assertEqual(m2._apply_supersession(self.db, trigger, target), "superseded")
        self.assertEqual(m2._apply_supersession(self.db, trigger, target), "idempotent_skip")
        events = idx.parse_evidence_events(Path(self.target).read_text())
        self.assertEqual(len([e for e in events if e["event_type"] == "contradicted"]), 1)

    def test_diagnostic_uses_live_queue_and_preserves_failure_until_recovered(self):
        from maintenance_status import read_status
        with mock.patch.object(m1, "_record_from_file", side_effect=OSError("failed")):
            self.drain_one()
        status = read_status(self.db)
        self.assertEqual(status["pending_count"], 2)
        self.assertEqual(status["failed_count"], 1)
        self.assertIsNone(status["last_completed_at"])

    def test_missing_vector_store_does_not_ack_enabled_semantics(self):
        self.assertEqual(self.drain_one(), [self.target, self.trigger])
        self.assertIn({"stage": "neighbors", "reason": "vectors_missing"},
                      self.status()["failures"][self.trigger]["issues"])

    def test_requested_m1_corroboration_cannot_fail_open(self):
        with mock.patch.dict(os.environ, {"EIDETIC_M1_CROSS_ENCODER": "on"}), \
                mock.patch.object(m1, "opposition", return_value="opposite"), \
                mock.patch.object(engine, "rerank", return_value=[]), capture_failures() as failures:
            self.assertEqual(m1.production_confirmer({"text": "a"}, {"text": "b"}), "uncertain")
        self.assertEqual(failures, [{"stage": "m1_rerank", "reason": "unavailable"}])

    def test_nonfinite_relevance_never_authorizes_a_card_edit(self):
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        original = Path(self.target).read_text()
        for score in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(score=score), capture_failures() as failures:
                outcomes = m2.process_trigger(self.db, self.trigger, meta, "policy",
                    neighbors=self.hits, confirmer=lambda a, b: "no_contradiction",
                    supersedes=lambda a, b: False, relevance_fn=lambda a, b: score)
            self.assertEqual(outcomes[0]["action"], "relevance_skipped")
            self.assertIn({"stage": "m2_relevance", "reason": "invalid_score"}, failures)
            self.assertEqual(Path(self.target).read_text(), original)

    def test_batch_records_only_the_path_that_failed(self):
        def record(path):
            if path == self.trigger:
                raise OSError("unavailable")
            return original(path)
        original = m1._record_from_file
        with mock.patch.dict(os.environ, {"EIDETIC_INGEST_BATCH_SIZE": "2"}), \
                mock.patch.object(m1, "_record_from_file", side_effect=record), \
                mock.patch.object(m1, "neighbors_via_door", return_value=[]):
            self.assertEqual(self.drain_one(), [self.trigger])
        self.assertEqual(set(self.status()["failures"]), {self.trigger})

    def test_paused_caller_cannot_ack_another_callers_work(self):
        import subprocess
        from maintenance_status import read_status
        with mock.patch.dict(os.environ, {"EIDETIC_CONFIDENCE_EVENTS": "off"}):
            status = read_status(self.db)
            self.assertFalse(status["enabled_for_caller"])
            self.assertEqual(status["pending_count"], 2)
            p = subprocess.run([sys.executable, str(Path(idx.__file__).with_name("maintenance_status.py")),
                                self.db, "--summary"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("paused for this caller", p.stdout)
        self.assertEqual(idx.pending_ingest_paths(self.conn), [self.trigger, self.target])

    def test_distinct_same_second_event_is_retryable_not_successful_dedup(self):
        import evidence
        with capture_failures() as failures:
            self.assertTrue(evidence.append_event(self.target, "observed", note="one", ts="2026-10-07T00:00:00"))
            self.assertFalse(evidence.append_event(self.target, "observed", note="two", ts="2026-10-07T00:00:00"))
        self.assertEqual(failures, [{"stage": "evidence_write", "reason": "timestamp_collision"}])
        self.assertTrue(evidence.append_event(self.target, "observed", note="two", ts="2026-10-07T00:00:01"))
        with capture_failures() as failures:
            self.assertFalse(evidence.append_event(self.target, "observed", note="two", ts="2026-10-07T00:00:01"))
        self.assertEqual(failures, [])

    def test_failed_or_conflicting_neighbor_cannot_support_another_edit(self):
        other = self.card("other")
        hits = self.hits + [{"path": other, "score": 0.99}]
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        for kind in ("error", "contradiction", "supersedes"):
            def confirm(a, b):
                if b["path"] == self.target:
                    if kind == "error":
                        raise RuntimeError("unavailable")
                    if kind == "contradiction":
                        return "contradiction"
                return "no_contradiction"
            captured = []
            with mock.patch.object(m2, "_edit_page", side_effect=lambda *args: captured.append(args[-1]) or {"action": "edited"}), \
                    mock.patch.object(m1, "process_card", return_value=[]):
                m2.process_trigger(self.db, self.trigger, meta, "policy", neighbors=hits,
                    confirmer=confirm, supersedes=lambda a, b: kind == "supersedes" and b["path"] == self.target,
                    relevance_fn=lambda a, b: 5.0)
            self.assertEqual(captured, [[]], kind)

    def test_revised_region_preserves_crlf_user_bytes(self):
        content, rid, _ = m2.apply_region(Path(self.target).read_text(), "old region")
        Path(self.target).write_bytes(content.replace("\n", "\r\n").encode())
        before = Path(self.target).read_bytes()
        trigger = {"slug": "trigger", "source": "agent-extracted"}
        result = m2._edit_page(self.db, self.target, trigger, "target", 0.9,
                              lambda *args: "new region", [])
        self.assertEqual(result["action"], "edited")
        after = Path(self.target).read_bytes()
        sentinel = m2._begin_sentinel(rid).encode()
        self.assertEqual(before.split(sentinel)[0], after.split(sentinel)[0])
        self.assertIn(b"A durable project policy.\r\n", after)

    def test_m1_nonfinite_corroboration_is_retryable(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with mock.patch.dict(os.environ, {"EIDETIC_M1_CROSS_ENCODER": "on"}), \
                    mock.patch.object(m1, "opposition", return_value="opposite"), \
                    mock.patch.object(engine, "rerank", return_value=[value]), capture_failures() as failures:
                self.assertEqual(m1.production_confirmer({"text": "a"}, {"text": "b"}), "uncertain")
            self.assertIn({"stage": "m1_rerank", "reason": "invalid_score"}, failures)

    def test_midnight_retry_does_not_change_completed_synthesis(self):
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        kwargs = {"neighbors": self.hits, "confirmer": lambda a, b: "no_contradiction",
                  "supersedes": lambda a, b: False, "relevance_fn": lambda a, b: 5.0}
        with mock.patch.object(m2, "_iso_date", return_value="2026-10-07"):
            m2.process_trigger(self.db, self.trigger, meta, "policy", **kwargs)
        before = Path(self.target).read_text()
        with mock.patch.object(m2, "_iso_date", return_value="2026-10-08"):
            result = m2.process_trigger(self.db, self.trigger, meta, "policy", **kwargs)
        self.assertEqual(result[0]["action"], "idempotent_skip")
        self.assertEqual(Path(self.target).read_text(), before)

    def test_return_to_prior_body_has_distinct_operation_and_atomic_event(self):
        import evidence
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        kwargs = {"neighbors": self.hits, "confirmer": lambda a, b: "no_contradiction",
                  "supersedes": lambda a, b: False, "relevance_fn": lambda a, b: 5.0,
                  "synth_body_fn": lambda t, p, provenance: t["text"]}
        with mock.patch.object(evidence, "datetime") as clock:
            for second, body in [(0, "A"), (1, "B"), (2, "A")]:
                clock.now.return_value.strftime.return_value = f"2026-10-07T00:00:0{second}"
                if second == 2:
                    before = Path(self.target).read_text()
                    with mock.patch.object(evidence.os, "replace", side_effect=OSError("interrupt")):
                        result = m2.process_trigger(self.db, self.trigger, meta, body, **kwargs)
                    self.assertEqual(result[0]["action"], "event_deferred")
                    self.assertEqual(Path(self.target).read_text(), before)
                    # Successful empty discovery cannot leave a half-written revision.
                    m2.process_trigger(self.db, self.trigger, meta, body, **dict(kwargs, neighbors=[]))
                    self.assertEqual(Path(self.target).read_text(), before)
                m2.process_trigger(self.db, self.trigger, meta, body, **kwargs)
        events = idx.parse_evidence_events(Path(self.target).read_text())
        self.assertEqual(len(events), 3)
        self.assertEqual(len({e["note"] for e in events}), 3)

    def test_equal_slug_loser_is_stable_in_both_directions(self):
        a = {"slug": "same", "path": "/memory/a.md", "authority": 1, "last_verified": ""}
        b = dict(a, path="/memory/b.md")
        self.assertEqual(m1.pick_loser(a, b), m1.pick_loser(b, a))

    def test_synthesis_claim_skips_provenance_and_evidence_block(self):
        rec = {"text": "## Evidence\n- 2026-10-07 observed\n", "name": "fallback"}
        self.assertEqual(m2._salient_claim(rec), "fallback")
        rec["text"] += "## Policy\nReal claim"
        self.assertEqual(m2._salient_claim(rec), "Real claim")
        rec["text"] = "<!-- marker -->\n_M2 synthesis · trigger=old_\nReal claim"
        self.assertEqual(m2._salient_claim(rec), "Real claim")

    def test_changed_operation_cannot_borrow_older_synthesis_evidence(self):
        import evidence
        meta = {"name": "trigger", "type": "project", "source": "agent-extracted"}
        kwargs = {"neighbors": self.hits, "confirmer": lambda a, b: "no_contradiction",
                  "supersedes": lambda a, b: False, "relevance_fn": lambda a, b: 5.0,
                  "synth_body_fn": lambda t, p, provenance: t["text"]}
        with mock.patch.object(evidence, "datetime") as clock:
            clock.now.return_value.strftime.return_value = "2026-10-07T00:00:00"
            m2.process_trigger(self.db, self.trigger, meta, "first operation", **kwargs)
            with capture_failures() as failures:
                deferred = m2.process_trigger(self.db, self.trigger, meta, "second operation", **kwargs)
            self.assertEqual(deferred[0]["action"], "event_deferred")
            self.assertTrue(failures)  # same timestamp, different operation
            clock.now.return_value.strftime.return_value = "2026-10-07T00:00:01"
            recovered = m2.process_trigger(self.db, self.trigger, meta, "second operation", **kwargs)
            self.assertEqual(recovered[0]["action"], "edited")
            repeated = m2.process_trigger(self.db, self.trigger, meta, "second operation", **kwargs)
            self.assertEqual(repeated[0]["action"], "idempotent_skip")
        events = idx.parse_evidence_events(Path(self.target).read_text())
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["note"], events[1]["note"])

    def test_deleted_neighbor_is_not_a_permanent_retry(self):
        with capture_failures() as failures:
            result = m1._record_from_file(str(self.root / "absent.md"))
        self.assertIsNone(result)
        self.assertEqual(failures, [])

    def test_context_does_not_leak_between_calls(self):
        note_failure("ignored", "outside")
        with capture_failures() as first:
            note_failure("model", RuntimeError("private content"))
        with capture_failures() as second:
            pass
        self.assertEqual(first, [{"stage": "model", "reason": "RuntimeError"}])
        self.assertEqual(second, [])

    def test_card_lock_contention_retains_trigger(self):
        from contextlib import contextmanager
        @contextmanager
        def busy(*args):
            yield False
        with mock.patch.object(m1, "neighbors_via_door", return_value=self.hits), \
                mock.patch.object(engine, "rerank", return_value=[5.0]), \
                mock.patch.object(m2._EV, "card_lock", side_effect=busy):
            self.assertEqual(self.drain_one(), [self.target, self.trigger])
        self.assertIn({"stage": "m2_card_lock", "reason": "contended"},
                      self.status()["failures"][self.trigger]["issues"])


if __name__ == "__main__":
    unittest.main()
