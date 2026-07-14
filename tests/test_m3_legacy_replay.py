"""Hermetic M3 legacy replay safety and reconciliation tests."""
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import m3_legacy_replay as replay  # noqa: E402
import m3_recall_miner as miner  # noqa: E402


def _lock_probe(output_dir, marker):
    os.makedirs(output_dir, exist_ok=True)
    with replay._output_lock(output_dir):
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write("start:%s\n" % os.getpid())
            fh.flush()
        time.sleep(0.15)
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write("end:%s\n" % os.getpid())


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_transcript(path, assistant_text):
    _write_jsonl(path, [
        {"type": "user", "message": {"content": [{"type": "text", "text": "why"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": assistant_text}]}}
    ])


class ReplayBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3-legacy-")
        self.ms = os.path.join(self.tmp, "memory-system")
        self.projects = os.path.join(self.tmp, "projects")
        self.project = os.path.join(self.projects, "proj")
        self.output = os.path.join(self.tmp, "replay-output")
        self.events = os.path.join(self.ms, "events")
        os.makedirs(os.path.join(self.ms, "bin"))
        for name in ("m3_recall_miner.py", "m3_acquisition.py", "m3_judge.py"):
            shutil.copy2(os.path.join(os.path.dirname(__file__), "..", "bin", name),
                         os.path.join(self.ms, "bin", name))
        os.makedirs(self.project)
        self.sid = "session-1"
        self.transcript = os.path.join(self.project, self.sid + ".jsonl")
        self.assistant = ("We decided that all durable pipeline jobs use the Redis queue "
                          "because cron polling loses work during restarts.")
        _write_transcript(self.transcript, self.assistant)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row(self, claim=None, **extra):
        row = {
            "session_id": self.sid, "project_slug": "proj", "kind": "decision",
            "claim": claim or ("Durable pipeline jobs use the Redis queue because cron "
                               "polling loses work during restarts."),
            "transcript_quote": "old derived quote", "quote_ok": True,
            "judge": "entailed", "would_file": True,
        }
        row.update(extra)
        return row

    def candidate(self, claim=None, quote=None, kind="decision"):
        return {
            "kind": kind,
            "claim": claim or self.row()["claim"],
            "transcript_quote": quote or (
                "all durable pipeline jobs use the Redis queue because cron polling "
                "loses work during restarts"),
            "miner_policy": miner.MINER_POLICY_VERSION,
        }

    def inventory(self, rows=None):
        _write_jsonl(os.path.join(self.events, "m3_acquisition_dark.jsonl"),
                     rows if rows is not None else [self.row()])
        return replay.build_inventory(self.ms, self.projects)

    def replay_run(self, manifest, candidates, judge="entailed", **kwargs):
        meta = {"error": None, "miner_policy": miner.MINER_POLICY_VERSION}
        with mock.patch.object(replay._miner, "mine_transcript",
                               return_value=(candidates, meta)), \
                mock.patch.object(replay._judge, "verdict", return_value=judge):
            return replay.run_replay(manifest, self.output, **kwargs)

    def results(self):
        rows, malformed = replay._read_jsonl(
            os.path.join(self.output, "replay-results.jsonl"))
        return [r for r in rows if r.get("record_type") == "result"], malformed


class InventoryTest(ReplayBase):
    def test_one_per_transcript_selection_is_stable(self):
        rows = [{"replay_row_id": "a", "transcript": {"sha256": "x"}},
                {"replay_row_id": "b", "transcript": {"sha256": "x"}},
                {"replay_row_id": "c", "transcript": {"sha256": "y"}}]
        self.assertEqual([r["replay_row_id"] for r in replay._select_rows(rows, True)],
                         ["a", "c"])

    def test_inventory_is_body_free_and_reconciled(self):
        manifest = self.inventory([self.row(), self.row(session_id="missing")])
        self.assertEqual(manifest["totals"]["legacy_rows"], 2)
        self.assertEqual(manifest["totals"]["source_resolvability"],
                         {"missing": 1, "resolved": 1})
        rendered = replay._canonical(manifest)
        self.assertNotIn(self.row()["claim"], rendered)
        self.assertNotIn(self.assistant, rendered)
        self.assertEqual(manifest["rows"][0]["original_policy"], None)

    def test_current_policy_rows_are_not_replay_inputs(self):
        manifest = self.inventory([
            self.row(), self.row(miner_policy=miner.MINER_POLICY_VERSION)])
        self.assertEqual(manifest["totals"]["legacy_rows"], 1)
        self.assertEqual(manifest["sources"][0]["current_policy_row_count"], 1)

    def test_output_guard_rejects_live_paths(self):
        with self.assertRaises(replay.ReplayError):
            replay._guard_output(os.path.join(self.ms, "events", "x"),
                                 self.ms, self.projects)

    def test_agent_path_escape_never_resolves(self):
        outside = os.path.join(self.tmp, "outside.jsonl")
        _write_transcript(outside, self.assistant)
        rows = [self.row(session_id="", agent_file=outside, lane="agent"),
                self.row(session_id="", agent_file="../../outside.jsonl", lane="agent")]
        _write_jsonl(os.path.join(self.events, "m3_agent_dark.jsonl"), rows)
        manifest = replay.build_inventory(self.ms, self.projects)
        self.assertEqual(
            [row["transcript"]["status"] for row in manifest["rows"]],
            ["missing", "missing"])

    def test_session_id_path_escape_never_resolves(self):
        outside = os.path.join(self.tmp, "outside-session.jsonl")
        _write_transcript(outside, self.assistant)
        absolute_sid = outside[:-len(".jsonl")]
        manifest = self.inventory([
            self.row(session_id=absolute_sid),
            self.row(session_id="../../outside-session"),
            self.row(session_id="*")])
        self.assertEqual(
            [row["transcript"]["status"] for row in manifest["rows"]],
            ["missing", "missing", "missing"])

    def test_singleton_candidate_requires_legacy_similarity(self):
        candidate, reason, evidence = replay._select_candidate(
            "The durable queue is Redis for restart safety.",
            [self.candidate("The UI navigation uses a blue sidebar on desktop.")])
        self.assertIsNone(candidate)
        self.assertEqual(reason, "singleton_candidate_below_similarity_floor")
        self.assertLess(evidence[0]["score"], replay.SIMILARITY_FLOOR)


class OutcomeTest(ReplayBase):
    def test_durable_survivor_and_idempotent_resume(self):
        manifest = self.inventory()
        summary, _ = self.replay_run(manifest, [self.candidate()],
                              enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"],
                         {"salvaged_current_policy": 1})
        first_count = len(self.results()[0])
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        self.assertEqual(len(self.results()[0]), first_count)

    def test_miner_result_cache_survives_cli_style_resume(self):
        manifest = self.inventory([self.row(), self.row()])
        meta = {"error": None, "miner_policy": miner.MINER_POLICY_VERSION}
        fake = mock.Mock(return_value=([self.candidate()], meta))
        with mock.patch.object(replay._miner, "mine_transcript", fake), \
                mock.patch.object(replay._judge, "verdict", return_value="entailed"):
            replay.run_replay(manifest, self.output, offset=0, limit=1,
                              enable_judge=True)
            replay.run_replay(manifest, self.output, offset=1, limit=1,
                              enable_judge=True)
        self.assertEqual(fake.call_count, 1)

    def test_known_transient_classes_reject_before_judge(self):
        cases = [
            self.candidate("The next step is to migrate the queue immediately.",
                           "We recommend the next step is to migrate the queue immediately"),
            self.candidate("The live model provider config is Spark today.",
                           "The live model provider config is Spark today for this run"),
            self.candidate("Ticket TASK-1 has status completed successfully.",
                           "Ticket TASK-1 has status completed successfully today"),
            self.candidate("We propose a new worker configuration for next week.",
                           "We propose a new worker configuration for next week now"),
        ]
        for index, cand in enumerate(cases):
            with self.subTest(index=index):
                shutil.rmtree(self.output, ignore_errors=True)
                manifest = self.inventory([self.row(claim=cand["claim"])])
                with mock.patch.object(replay._judge, "verdict") as judge:
                    self.replay_run(manifest, [cand], enable_judge=True)
                    judge.assert_not_called()
                row = self.results()[0][0]
                self.assertEqual(row["final_outcome"], "rejected_noise")

    def test_quote_not_present_is_invalid(self):
        manifest = self.inventory()
        self.replay_run(manifest, [self.candidate(quote="a fabricated quote with enough words "
                                                     "that never appears anywhere")],
                 enable_judge=True)
        self.assertEqual(self.results()[0][0]["final_reason"],
                         "quote_not_in_transcript")

    def test_normalized_but_not_exact_quote_is_invalid(self):
        manifest = self.inventory()
        quote = ("all  durable pipeline jobs use the Redis queue because cron polling "
                 "loses work during restarts")
        self.replay_run(manifest, [self.candidate(quote=quote)], enable_judge=True)
        self.assertEqual(self.results()[0][0]["final_reason"],
                         "quote_not_in_transcript")

    def test_source_missing_and_hash_changed_fail_closed(self):
        manifest = self.inventory([self.row(), self.row(session_id="missing")])
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write("{}\n")
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        outcomes = [r["final_outcome"] for r in self.results()[0]]
        self.assertEqual(outcomes, ["invalid_evidence", "source_missing"])

    def test_ambiguous_candidates_require_manual_review(self):
        manifest = self.inventory()
        other = dict(self.candidate())
        other["kind"] = "rule"  # equally plausible mapping to the same old row
        self.replay_run(manifest, [self.candidate(), other], enable_judge=True)
        self.assertEqual(self.results()[0][0]["final_outcome"],
                         "conflict_manual_review")

    def test_duplicate_and_conflicting_survivors(self):
        rows = [self.row(), self.row(session_id="session-2")]
        second = os.path.join(self.project, "session-2.jsonl")
        _write_transcript(second, self.assistant)
        manifest = self.inventory(rows)
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        outcomes = [r["final_outcome"] for r in self.results()[0]]
        self.assertEqual(outcomes,
                         ["salvaged_current_policy", "conflict_manual_review"])

        shutil.rmtree(self.output)
        neg_assistant = ("We decided that durable pipeline jobs do not use the Redis "
                         "queue because cron polling loses work during restarts.")
        _write_transcript(second, neg_assistant)
        rows = [self.row(), self.row(
            session_id="session-2",
            claim="Durable pipeline jobs do not use the Redis queue because cron "
                  "polling loses work during restarts.")]
        manifest = self.inventory(rows)
        negative = self.candidate(
            claim=rows[1]["claim"],
            quote="durable pipeline jobs do not use the Redis queue because cron polling "
                  "loses work during restarts")
        calls = {os.path.basename(self.transcript): [self.candidate()],
                 os.path.basename(second): [negative]}
        with mock.patch.object(replay._miner, "mine_transcript",
                               side_effect=lambda path, **kw: (
                                   calls[os.path.basename(path)], {"error": None})), \
                mock.patch.object(replay._judge, "verdict", return_value="entailed"):
            replay.run_replay(manifest, self.output, enable_judge=True)
        self.assertEqual([r["final_outcome"] for r in self.results()[0]],
                         ["salvaged_current_policy", "conflict_manual_review"])

    def test_judge_unavailable_and_malformed_are_retryable(self):
        for verdict in ("judge_unavailable", "error"):
            with self.subTest(verdict=verdict):
                shutil.rmtree(self.output, ignore_errors=True)
                manifest = self.inventory()
                summary, _ = self.replay_run(manifest, [self.candidate()], judge=verdict,
                                      enable_judge=True)
                self.assertEqual(summary["nonterminal_retry_count"], 1)
                self.assertEqual(self.results()[0][0]["final_outcome"],
                                 "transient_retry")

    def test_judge_disabled_is_retryable(self):
        manifest = self.inventory()
        summary, _ = self.replay_run(manifest, [self.candidate()], enable_judge=False)
        self.assertEqual(summary["nonterminal_retry_count"], 1)
        self.assertTrue(summary["reconciled"])


class ResumeAndSafetyTest(ReplayBase):
    def test_parallel_model_calls_fail_closed_without_worker_contract(self):
        manifest = self.inventory()
        with self.assertRaises(replay.ReplayError):
            self.replay_run(manifest, [self.candidate()], workers=2,
                            enable_judge=True)

    def test_real_judge_logging_is_redirected_from_live_events(self):
        manifest = self.inventory()
        quote = self.candidate()["transcript_quote"]
        sdk = mock.Mock()
        sdk.chat_for_route.return_value = {
            "response_shape": {"ok": True},
            "content": json.dumps({"entailed": True, "quote": quote}),
        }
        with mock.patch.object(replay._miner, "mine_transcript", return_value=(
                [self.candidate()], {"error": None,
                                     "miner_policy": miner.MINER_POLICY_VERSION})), \
                mock.patch.object(replay._judge, "_get_sdk", return_value=sdk):
            replay.run_replay(manifest, self.output, enable_judge=True)
        self.assertFalse(os.path.exists(os.path.join(self.events, "m3_judge.log")))
        self.assertEqual(self.results()[0][0]["final_outcome"],
                         "salvaged_current_policy")

    def test_secret_candidate_is_never_persisted_in_miner_cache(self):
        secret = "Authorization: Bearer example_secret_value_123456789"
        assistant = self.assistant + " " + secret
        _write_transcript(self.transcript, assistant)
        manifest = self.inventory()
        candidate = self.candidate(
            claim=self.row()["claim"] + " " + secret,
            quote="all durable pipeline jobs use the Redis queue because cron polling "
                  "loses work during restarts " + secret)
        self.replay_run(manifest, [candidate], enable_judge=True)
        for root, _dirs, files in os.walk(self.output):
            for name in files:
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                    self.assertNotIn(secret, fh.read())

    def test_changed_transcript_never_polls_or_poisons_miner_cache(self):
        manifest = self.inventory()
        with open(self.transcript, "rb") as fh:
            original = fh.read()
        with open(self.transcript, "ab") as fh:
            fh.write(b"{}\n")
        fake = mock.Mock(return_value=(
            [self.candidate()], {"error": None,
                                 "miner_policy": miner.MINER_POLICY_VERSION}))
        with mock.patch.object(replay._miner, "mine_transcript", fake):
            replay.run_replay(manifest, self.output, enable_judge=True)
        fake.assert_not_called()
        cache_dir = os.path.join(self.output, "miner-cache")
        self.assertFalse(os.path.exists(cache_dir))

        with open(self.transcript, "wb") as fh:
            fh.write(original)
        clean_output = os.path.join(self.tmp, "clean-replay-output")
        with mock.patch.object(replay._miner, "mine_transcript", fake), \
                mock.patch.object(replay._judge, "verdict", return_value="entailed"):
            replay.run_replay(manifest, clean_output, enable_judge=True)
        self.assertEqual(fake.call_count, 1)
        rows, _ = replay._read_jsonl(
            os.path.join(clean_output, "replay-results.jsonl"))
        self.assertEqual(rows[-1]["final_outcome"], "salvaged_current_policy")

    def test_transcript_change_during_mining_never_writes_cache(self):
        manifest = self.inventory()

        def mutate_during_mining(_path, **_kwargs):
            with open(self.transcript, "ab") as fh:
                fh.write(b"{}\n")
            return ([self.candidate()],
                    {"error": None, "miner_policy": miner.MINER_POLICY_VERSION})

        with mock.patch.object(replay._miner, "mine_transcript",
                               side_effect=mutate_during_mining):
            replay.run_replay(manifest, self.output, enable_judge=True)
        self.assertFalse(os.path.exists(os.path.join(self.output, "miner-cache")))
        self.assertEqual(self.results()[0][-1]["final_reason"],
                         "transcript_hash_changed")

    def test_result_symlink_cannot_append_to_live_event(self):
        manifest = self.inventory()
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        result_path = os.path.join(self.output, "replay-results.jsonl")
        os.unlink(result_path)
        live = os.path.join(self.events, "m3_judged.jsonl")
        _write_jsonl(live, [{"protected": True}])
        before = replay._sha_file(live)
        os.symlink(live, result_path)
        with self.assertRaises(replay.ReplayError):
            self.replay_run(manifest, [self.candidate()], enable_judge=True)
        self.assertEqual(replay._sha_file(live), before)

    def test_review_cli_rejects_output_aliasing_manifest_or_results(self):
        manifest = self.inventory()
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        manifest_path = os.path.join(self.output, "inventory-manifest.json")
        replay._atomic_json(manifest_path, manifest)
        results_path = os.path.join(self.output, "replay-results.jsonl")
        for output in (manifest_path, results_path,
                       os.path.join(self.output, "checkpoint.json")):
            with self.subTest(output=output), self.assertRaises(replay.ReplayError):
                replay.main(["review", "--manifest", manifest_path,
                             "--results", results_path, "--output", output])

    def test_output_manifest_binding_rejects_changed_snapshot(self):
        manifest = self.inventory()
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write("{}\n")
        changed = self.inventory()
        with self.assertRaises(replay.ReplayError):
            self.replay_run(changed, [self.candidate()], enable_judge=True)

    def test_resume_revalidates_terminal_survivor_source(self):
        manifest = self.inventory()
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write("{}\n")
        with self.assertRaises(replay.ReplayError):
            self.replay_run(manifest, [self.candidate()], enable_judge=True)

    def test_output_lock_serializes_processes(self):
        marker = os.path.join(self.tmp, "lock-order.txt")
        ctx = multiprocessing.get_context("spawn")
        processes = [ctx.Process(target=_lock_probe, args=(self.output, marker))
                     for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        with open(marker, encoding="utf-8") as fh:
            rows = fh.read().splitlines()
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].split(":")[0], "start")
        self.assertEqual(rows[1], "end:" + rows[0].split(":", 1)[1])
        self.assertEqual(rows[2].split(":")[0], "start")
        self.assertEqual(rows[3], "end:" + rows[2].split(":", 1)[1])

    def test_truncated_final_append_is_preserved_and_recovered(self):
        manifest = self.inventory()
        os.makedirs(self.output)
        replay._bind_output_manifest(self.output, replay._manifest_hash(manifest))
        result_path = os.path.join(self.output, "replay-results.jsonl")
        with open(result_path, "wb") as fh:
            fh.write(b'{"schema":"torn"')
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        rows, malformed = replay._read_jsonl(result_path)
        self.assertEqual(malformed, [1])
        self.assertTrue(any(r.get("record_type") == "recovery_marker" for r in rows))
        self.assertTrue(any(r.get("final_outcome") == "salvaged_current_policy"
                            for r in rows))

    def test_deterministic_fresh_runs_have_equal_outcome_fingerprint(self):
        manifest = self.inventory()
        first, _ = self.replay_run(manifest, [self.candidate()], enable_judge=True)
        fingerprint = (first["terminal_outcomes"], first["reason_distribution"])
        shutil.rmtree(self.output)
        second, _ = self.replay_run(manifest, [self.candidate()], enable_judge=True)
        self.assertEqual(fingerprint,
                         (second["terminal_outcomes"], second["reason_distribution"]))

    def test_zero_live_writes_and_activation_exclusion(self):
        manifest = self.inventory()
        before = {p: replay._sha_file(p) for p in (
            os.path.join(self.events, "m3_acquisition_dark.jsonl"), self.transcript)}
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        after = {p: replay._sha_file(p) for p in before}
        self.assertEqual(before, after)
        self.assertFalse(os.path.exists(os.path.join(self.events, "m3_judged.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.events, "m3_filed.jsonl")))
        # Live report reads only its exact event files, never replay output.
        self.assertTrue(all(r.get("origin") == "legacy_replay"
                            for r in self.results()[0]))

    def test_review_packet_reconciles_and_never_imports(self):
        manifest = self.inventory()
        self.replay_run(manifest, [self.candidate()], enable_judge=True)
        packet = replay.render_review_packet(
            manifest, os.path.join(self.output, "replay-results.jsonl"))
        self.assertIn("Reconciled: **true**", packet)
        self.assertIn("No automatic import action exists", packet)
        self.assertIn("Exact supporting quote", packet)


if __name__ == "__main__":
    unittest.main()
