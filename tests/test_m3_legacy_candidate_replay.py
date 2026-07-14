"""Hermetic tests for candidate-first M3 legacy salvage."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import m3_legacy_candidate_replay as candidate_replay  # noqa: E402
import m3_legacy_replay as legacy  # noqa: E402
import m3_recall_miner as miner  # noqa: E402


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_transcript(path, assistant_turns):
    rows = [{
        "type": "user",
        "message": {"content": [{"type": "text", "text": "Explain the result."}]},
    }]
    for index, text in enumerate(assistant_turns):
        rows.append({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        })
        if index + 1 < len(assistant_turns):
            rows.append({
                "type": "user",
                "message": {"content": [{"type": "text", "text": "Continue."}]},
            })
    _write_jsonl(path, rows)


class CandidateReplayBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3-candidate-replay-")
        self.ms = os.path.join(self.tmp, "memory-system")
        self.projects = os.path.join(self.tmp, "projects")
        self.project = os.path.join(self.projects, "proj")
        self.events = os.path.join(self.ms, "events")
        self.source_output = os.path.join(self.tmp, "legacy-final-v3")
        self.output = os.path.join(self.tmp, "candidate-output")
        self.legacy_manifest_path = os.path.join(self.tmp, "legacy-inventory.json")
        self.candidate_manifest_path = os.path.join(
            self.tmp, "candidate-inventory.json")
        os.makedirs(os.path.join(self.ms, "bin"))
        for name in ("m3_recall_miner.py", "m3_acquisition.py", "m3_judge.py"):
            shutil.copy2(os.path.join(os.path.dirname(__file__), "..", "bin", name),
                         os.path.join(self.ms, "bin", name))
        os.makedirs(self.project)
        self.session_id = "session-1"
        self.transcript = os.path.join(self.project, self.session_id + ".jsonl")
        self.quote1 = (
            "Durable pipeline jobs use the Redis queue because cron polling loses "
            "work during restarts."
        )
        self.claim1 = self.quote1
        self.quote2 = (
            "The audit ledger keeps immutable evidence hashes so later review can "
            "prove every source binding."
        )
        self.claim2 = self.quote2
        self._reset_source([self.quote1 + " " + self.quote2])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _legacy_row(self):
        return {
            "session_id": self.session_id,
            "project_slug": "proj",
            "kind": "decision",
            "claim": "Old derived claim that is deliberately not reused.",
            "transcript_quote": "old derived quote",
            "quote_ok": True,
            "judge": "entailed",
            "would_file": True,
        }

    def _reset_source(self, assistant_turns):
        _write_transcript(self.transcript, assistant_turns)
        _write_jsonl(os.path.join(self.events, "m3_acquisition_dark.jsonl"),
                     [self._legacy_row()])
        self.legacy_manifest = legacy.build_inventory(self.ms, self.projects)
        legacy._atomic_json(self.legacy_manifest_path, self.legacy_manifest)
        shutil.rmtree(self.source_output, ignore_errors=True)
        os.makedirs(self.source_output)
        legacy._bind_output_manifest(
            self.source_output, legacy._manifest_hash(self.legacy_manifest))
        os.makedirs(os.path.join(self.source_output, "miner-cache"))

    def candidate(self, claim=None, quote=None, kind="decision"):
        return {
            "kind": kind,
            "claim": claim or self.claim1,
            "transcript_quote": quote or self.quote1,
            "miner_policy": miner.MINER_POLICY_VERSION,
        }

    def _seed(self, candidates, prior_rows=()):
        transcript = self.legacy_manifest["rows"][0]["transcript"]
        cache_path = os.path.join(self.source_output, "miner-cache", "cache.json")
        legacy._atomic_json(cache_path, {
            "schema": legacy.MINER_CACHE_SCHEMA,
            "transcript_sha256": transcript["sha256"],
            "miner_policy": miner.MINER_POLICY_VERSION,
            "candidates": candidates,
            "meta": {"error": None, "miner_policy": miner.MINER_POLICY_VERSION},
        })
        for row in prior_rows:
            legacy._append_record(
                os.path.join(self.source_output, "replay-results.jsonl"), row)
        manifest = candidate_replay.build_inventory(
            self.legacy_manifest_path, self.source_output)
        legacy._atomic_json(self.candidate_manifest_path, manifest)
        return manifest, cache_path

    def prior_survivor(self, candidate):
        row = self.legacy_manifest["rows"][0]
        return {
            "schema": legacy.RESULT_SCHEMA,
            "record_type": "result",
            "origin": "legacy_replay",
            "replay_policy": legacy.REPLAY_POLICY,
            "manifest_sha256": legacy._manifest_hash(self.legacy_manifest),
            "replay_row_id": row["replay_row_id"],
            "final_outcome": "salvaged_current_policy",
            "kind": candidate["kind"],
            "claim": candidate["claim"],
            "supporting_quote": candidate["transcript_quote"],
            "current_miner_policy": miner.MINER_POLICY_VERSION,
            "transcript_sha256": row["transcript"]["sha256"],
            "deterministic_rail": {"outcome": "survived", "reason": None},
            "judge": {
                "invoked": True,
                "quote_verified": True,
                "outcome": "entailed",
                "route": "writer",
                "model": "palmyra-x5",
            },
        }

    def replay_run(self, manifest, verdict="entailed", **kwargs):
        judge = mock.Mock(return_value=verdict)
        with mock.patch.object(candidate_replay.legacy,
                               "_canonical_production_roots", return_value=()), \
                mock.patch.object(candidate_replay.legacy,
                                  "_side_effect_free_judge_verdict", judge):
            summary, results = candidate_replay.run_replay(
                manifest, self.output, **kwargs)
        return summary, results, judge

    def result_rows(self):
        rows, malformed = legacy._read_jsonl(
            os.path.join(self.output, "candidate-results.jsonl"))
        return [row for row in rows if row.get("record_type") == "result"], malformed


class CandidateInventoryTest(CandidateReplayBase):
    def test_historical_judge_drift_is_allowed_but_miner_drift_is_not(self):
        frozen = json.loads(json.dumps(self.legacy_manifest))
        frozen["policy_identity"]["files"]["m3_judge.py"] = "0" * 64
        legacy._atomic_json(self.legacy_manifest_path, frozen)
        loaded = candidate_replay._load_source_legacy_manifest(
            self.legacy_manifest_path)
        self.assertEqual(
            loaded["policy_identity"]["files"]["m3_judge.py"], "0" * 64)
        frozen["policy_identity"]["files"]["m3_recall_miner.py"] = "1" * 64
        legacy._atomic_json(self.legacy_manifest_path, frozen)
        with self.assertRaises(candidate_replay.CandidateReplayError):
            candidate_replay._load_source_legacy_manifest(
                self.legacy_manifest_path)

    def test_inventory_is_body_free_and_collapses_exact_occurrences(self):
        first = self.candidate()
        manifest, _ = self._seed([first, dict(first), self.candidate(
            claim=self.claim2, quote=self.quote2, kind="finding")])
        rendered = legacy._canonical(manifest)
        self.assertNotIn(self.claim1, rendered)
        self.assertNotIn(self.quote2, rendered)
        self.assertEqual(manifest["totals"]["cached_candidates"], 3)
        self.assertEqual(manifest["totals"]["unique_acquisition_candidates"], 2)
        self.assertEqual(manifest["totals"]["duplicate_candidate_occurrences"], 1)
        self.assertEqual(len(manifest["rows"][0]["source_occurrences"]), 2)

    def test_secret_like_candidate_aborts_inventory(self):
        secret = self.candidate(
            claim="The durable integration credential is api_key=abcdefgh12345678.",
            quote="The durable integration credential is api_key=abcdefgh12345678.")
        with self.assertRaises(candidate_replay.CandidateReplayError):
            self._seed([secret])

    def test_acquisition_candidate_miner_policy_must_match_cache(self):
        item = self.candidate()
        item["miner_policy"] = "historical-miner-policy"
        with self.assertRaises(candidate_replay.CandidateReplayError):
            self._seed([item])

    def test_source_cache_change_is_detected_before_judge(self):
        manifest, cache_path = self._seed([self.candidate()])
        with open(cache_path, "a", encoding="utf-8") as fh:
            fh.write("\n")
        judge = mock.Mock(return_value="entailed")
        with mock.patch.object(candidate_replay.legacy,
                               "_canonical_production_roots", return_value=()), \
                mock.patch.object(candidate_replay.legacy,
                                  "_side_effect_free_judge_verdict", judge):
            with self.assertRaises(candidate_replay.CandidateReplayError):
                candidate_replay.run_replay(
                    manifest, self.output, enable_judge=True)
        judge.assert_not_called()

    def test_transcript_change_is_detected_before_judge(self):
        manifest, _ = self._seed([self.candidate()])
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write("\n")
        judge = mock.Mock(return_value="entailed")
        with mock.patch.object(candidate_replay.legacy,
                               "_canonical_production_roots", return_value=()), \
                mock.patch.object(candidate_replay.legacy,
                                  "_side_effect_free_judge_verdict", judge):
            with self.assertRaises(candidate_replay.CandidateReplayError):
                candidate_replay.run_replay(
                    manifest, self.output, enable_judge=True)
        judge.assert_not_called()


class CandidateOutcomeTest(CandidateReplayBase):
    def test_candidate_first_evaluates_both_old_row_ambiguous_candidates(self):
        manifest, _ = self._seed([
            self.candidate(),
            self.candidate(claim=self.claim2, quote=self.quote2, kind="finding"),
        ])
        summary, _results, judge = self.replay_run(
            manifest, enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"], {"review_candidate": 2})
        self.assertEqual(judge.call_count, 2)

    def test_deterministic_reject_and_missing_quote_never_call_judge(self):
        noise = self.candidate(
            claim="The next step is to migrate the durable queue immediately.",
            quote="The next step is to migrate the durable queue immediately.")
        fabricated = self.candidate(
            claim="Immutable evidence hashes support durable source verification.",
            quote="This fabricated supporting quote never appeared in the transcript.",
            kind="finding")
        manifest, _ = self._seed([noise, fabricated])
        summary, _results, judge = self.replay_run(manifest, enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"], {
            "invalid_evidence": 1, "rejected_noise": 1,
        })
        judge.assert_not_called()

    def test_bound_prior_entailment_is_reused_without_judge(self):
        item = self.candidate()
        manifest, _ = self._seed([item], [self.prior_survivor(item)])
        summary, _results, judge = self.replay_run(manifest, enable_judge=False)
        self.assertEqual(summary["terminal_outcomes"], {"review_candidate": 1})
        judge.assert_not_called()
        row = self.result_rows()[0][0]
        self.assertTrue(row["judge"]["reused"])
        self.assertFalse(row["judge"]["invoked"])

    def test_forged_prior_salvage_contract_fails_closed(self):
        item = self.candidate()
        forged = self.prior_survivor(item)
        forged["judge"]["outcome"] = "not_entailed"
        manifest, _ = self._seed([item], [forged])
        with self.assertRaises(candidate_replay.CandidateReplayError):
            self.replay_run(manifest, enable_judge=False)

    def test_retry_then_terminal_resume_is_idempotent(self):
        manifest, _ = self._seed([self.candidate()])
        first, _results, first_judge = self.replay_run(
            manifest, verdict="judge_unavailable", enable_judge=True)
        self.assertEqual(first["nonterminal_retry_count"], 1)
        self.assertEqual(first_judge.call_count, 1)
        second, _results, second_judge = self.replay_run(
            manifest, verdict="entailed", enable_judge=True)
        self.assertEqual(second["terminal_outcomes"], {"review_candidate": 1})
        self.assertEqual(second_judge.call_count, 1)
        count = len(self.result_rows()[0])
        third, _results, third_judge = self.replay_run(
            manifest, verdict="not_entailed", enable_judge=True)
        self.assertEqual(third["terminal_outcomes"], {"review_candidate": 1})
        third_judge.assert_not_called()
        self.assertEqual(len(self.result_rows()[0]), count)

    def test_intra_replay_duplicate_uses_candidate_id(self):
        manifest, _ = self._seed([
            self.candidate(kind="decision"),
            self.candidate(kind="finding"),
        ])
        summary, _results, judge = self.replay_run(manifest, enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"], {
            "conflict_manual_review": 1, "review_candidate": 1,
        })
        self.assertEqual(judge.call_count, 1)
        duplicate = [row for row in self.result_rows()[0]
                     if row["final_reason"] == "duplicate_replay"][0]
        self.assertTrue(duplicate["relation"]["related_id"])
        self.assertEqual(len(duplicate["relation"]["related_id"]), 64)

    def test_possible_later_correction_is_manual_conflict(self):
        corrected = (
            "Correction: durable pipeline jobs do not use the Redis queue because "
            "the queue lost work during restarts."
        )
        self._reset_source([self.quote1, corrected])
        manifest, _ = self._seed([self.candidate()])
        summary, _results, judge = self.replay_run(manifest, enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"], {
            "conflict_manual_review": 1,
        })
        judge.assert_not_called()
        row = self.result_rows()[0][0]
        self.assertEqual(row["final_reason"], "possible_later_correction")
        self.assertTrue(row["later_context"]["possible_correction_hits"])

    def test_correction_that_repeats_exact_quote_is_not_skipped(self):
        corrected = (
            "Correction: " + self.quote1 +
            " That statement was wrong; durable pipeline jobs do not use the Redis "
            "queue because it lost work during restarts."
        )
        self._reset_source([self.quote1, corrected])
        manifest, _ = self._seed([self.candidate()])
        summary, _results, judge = self.replay_run(
            manifest, enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"], {
            "conflict_manual_review": 1,
        })
        judge.assert_not_called()

    def test_completed_candidate_is_persisted_before_later_process_stop(self):
        manifest, _ = self._seed([
            self.candidate(),
            self.candidate(claim=self.claim2, quote=self.quote2, kind="finding"),
        ])
        judge = mock.Mock(side_effect=["entailed", KeyboardInterrupt()])
        with mock.patch.object(candidate_replay.legacy,
                               "_canonical_production_roots", return_value=()), \
                mock.patch.object(candidate_replay.legacy,
                                  "_side_effect_free_judge_verdict", judge):
            with self.assertRaises(KeyboardInterrupt):
                candidate_replay.run_replay(
                    manifest, self.output, enable_judge=True)
        rows, malformed = self.result_rows()
        self.assertEqual(malformed, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["final_outcome"], "review_candidate")
        summary, _results, resumed_judge = self.replay_run(
            manifest, enable_judge=True)
        self.assertEqual(summary["terminal_outcomes"], {"review_candidate": 2})
        self.assertEqual(resumed_judge.call_count, 1)

    def test_workers_must_be_one(self):
        manifest, _ = self._seed([self.candidate()])
        with self.assertRaises(candidate_replay.CandidateReplayError):
            candidate_replay.run_replay(manifest, self.output, workers=2)
        self.assertFalse(os.path.exists(self.output))


class CandidateReviewTest(CandidateReplayBase):
    def test_review_packet_requires_human_decisions_and_reconciles(self):
        manifest, _ = self._seed([self.candidate()])
        _summary, results, _judge = self.replay_run(manifest, enable_judge=True)
        packet = candidate_replay.render_review_packet(manifest, results)
        self.assertIn("Nothing here is imported or counted toward D5", packet)
        self.assertIn("[ ] later corrections checked", packet)
        self.assertIn("[ ] semantically novel vs production", packet)
        self.assertIn("[ ] accept", packet)
        self.assertIn("Reconciled: **true**", packet)

    def test_truncated_append_is_preserved_with_recovery_marker(self):
        manifest, _ = self._seed([self.candidate()])
        os.makedirs(self.output)
        manifest_hash = legacy._manifest_hash(manifest)
        candidate_replay._bind_output(self.output, manifest_hash)
        results = os.path.join(self.output, "candidate-results.jsonl")
        with open(results, "wb") as fh:
            fh.write(b'{"partial":')
        self.replay_run(manifest, enable_judge=False)
        rows, malformed = legacy._read_jsonl(results)
        self.assertEqual(malformed, [1])
        self.assertTrue(any(row.get("record_type") == "recovery_marker"
                            for row in rows))
        self.assertEqual(self.result_rows()[0][-1]["final_outcome"],
                         "transient_retry")


if __name__ == "__main__":
    unittest.main()
