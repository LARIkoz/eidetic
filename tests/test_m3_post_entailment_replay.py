"""Hermetic tests for the offline M3 post-entailment Phase A replay."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from unittest import mock


ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import m3_legacy_candidate_replay as candidate_replay  # noqa: E402
import m3_legacy_replay as legacy  # noqa: E402
import m3_post_entailment_replay as post  # noqa: E402
import m3_recall_miner as miner  # noqa: E402


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_transcript(path, assistant_turns):
    rows = [{
        "type": "user",
        "message": {"content": [{"type": "text", "text": "Inspect."}]},
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


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class PostEntailmentBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3-post-entailment-")
        self.ms = os.path.join(self.tmp, "memory-system")
        self.projects = os.path.join(self.tmp, "projects")
        self.project = os.path.join(self.projects, "project")
        self.events = os.path.join(self.ms, "events")
        self.transcript = os.path.join(self.project, "session.jsonl")
        self.legacy_manifest_path = os.path.join(self.tmp, "legacy-manifest.json")
        self.legacy_output = os.path.join(self.tmp, "legacy-output")
        self.candidate_manifest_path = os.path.join(
            self.tmp, "candidate-manifest.json")
        self.candidate_output = os.path.join(self.tmp, "candidate-output")
        self.candidate_results_path = os.path.join(
            self.candidate_output, "candidate-results.jsonl")
        self.catalog_path = os.path.join(self.tmp, "source-catalog.json")
        self.manifest_path = os.path.join(self.tmp, "triage-manifest.json")
        self.output = os.path.join(self.tmp, "triage-output")
        self.source = os.path.join(self.tmp, "sources", "source.md")
        os.makedirs(os.path.join(self.ms, "bin"))
        for name in ("m3_recall_miner.py", "m3_acquisition.py", "m3_judge.py"):
            shutil.copy2(os.path.join(ROOT, "bin", name),
                         os.path.join(self.ms, "bin", name))
        os.makedirs(self.project)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def candidate(claim, kind="finding"):
        return {
            "kind": kind,
            "claim": claim,
            "transcript_quote": claim,
            "miner_policy": miner.MINER_POLICY_VERSION,
        }

    def seed_upstream(self, candidates, assistant_turns=None):
        turns = assistant_turns or [item["transcript_quote"] for item in candidates]
        _write_transcript(self.transcript, turns)
        _write_jsonl(os.path.join(self.events, "m3_acquisition_dark.jsonl"), [{
            "session_id": "session",
            "project_slug": "project",
            "kind": "finding",
            "claim": "Historical derived row is not reused.",
            "transcript_quote": "Historical quote.",
            "quote_ok": True,
            "judge": "entailed",
            "would_file": True,
        }])
        legacy_manifest = legacy.build_inventory(self.ms, self.projects)
        legacy._atomic_json(self.legacy_manifest_path, legacy_manifest)
        os.makedirs(self.legacy_output)
        legacy._bind_output_manifest(
            self.legacy_output, legacy._manifest_hash(legacy_manifest))
        cache_dir = os.path.join(self.legacy_output, "miner-cache")
        os.makedirs(cache_dir)
        transcript_meta = legacy_manifest["rows"][0]["transcript"]
        legacy._atomic_json(os.path.join(cache_dir, "cache.json"), {
            "schema": legacy.MINER_CACHE_SCHEMA,
            "transcript_sha256": transcript_meta["sha256"],
            "miner_policy": miner.MINER_POLICY_VERSION,
            "candidates": candidates,
            "meta": {"error": None, "miner_policy": miner.MINER_POLICY_VERSION},
        })
        candidate_manifest = candidate_replay.build_inventory(
            self.legacy_manifest_path, self.legacy_output)
        legacy._atomic_json(self.candidate_manifest_path, candidate_manifest)
        os.makedirs(self.candidate_output)
        candidate_manifest_hash = legacy._manifest_hash(candidate_manifest)
        candidate_replay._bind_output(
            self.candidate_output, candidate_manifest_hash)
        claim_to_id = {}
        for meta in candidate_manifest["rows"]:
            item = candidate_replay._load_candidate(meta)
            full_turns = candidate_replay._full_turns(meta["transcript"]["path"])
            indices = [index for index, (role, text) in enumerate(full_turns)
                       if role == "assistant" and item["transcript_quote"] in text]
            record = candidate_replay._base_record(meta, candidate_manifest_hash)
            record.update({
                "claim": item["claim"],
                "supporting_quote": item["transcript_quote"],
                "deterministic_rail": {"outcome": "survived", "reason": None},
                "quote_verification": {
                    "exact": True, "assistant_turn_indices": indices},
                "judge": {
                    "invoked": True,
                    "reused": False,
                    "route": "fixture",
                    "model": "fixture",
                    "outcome": "entailed",
                    "quote_verified": True,
                },
                "final_outcome": "review_candidate",
                "final_reason": "entailed_quote_verified",
                "error": None,
            })
            legacy._append_record(self.candidate_results_path, record)
            claim_to_id[item["claim"]] = meta["candidate_id"]
        return candidate_manifest, claim_to_id

    def write_source(self, lines):
        os.makedirs(os.path.dirname(self.source), exist_ok=True)
        with open(self.source, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def write_catalog(self, rows):
        legacy._atomic_json(self.catalog_path, {
            "schema": post.CATALOG_SCHEMA,
            "rows": rows,
        })

    def build(self):
        manifest = post.build_manifest(
            self.candidate_manifest_path,
            self.candidate_results_path,
            self.catalog_path)
        legacy._atomic_json(self.manifest_path, manifest)
        return manifest

    def result_rows(self):
        rows, malformed = legacy._read_jsonl(
            os.path.join(self.output, "triage-results.jsonl"))
        return [row for row in rows if row.get("record_type") == "result"], malformed

    def one(self, claim, catalog_row, assistant_turns=None):
        _manifest, ids = self.seed_upstream(
            [self.candidate(claim)], assistant_turns=assistant_turns)
        row = dict(catalog_row)
        row["candidate_id"] = ids[claim]
        self.write_catalog([row])
        manifest = self.build()
        summary, _path = post.run_replay(manifest, self.output)
        return summary, self.result_rows()[0][0], manifest


class PostEntailmentContractTest(PostEntailmentBase):
    def test_non_entailed_upstream_is_rejected(self):
        claim = "A durable owner rule has exact transcript support."
        self.seed_upstream([self.candidate(claim)])
        rows, _malformed = legacy._read_jsonl(self.candidate_results_path)
        rows[0]["judge"]["outcome"] = "not_entailed"
        _write_jsonl(self.candidate_results_path, rows)
        self.write_catalog([{
            "candidate_id": rows[0]["candidate_id"], "owner_scope": "manual"}])
        with self.assertRaises(post.PostEntailmentError):
            self.build()

    def test_source_transcript_and_catalog_hash_drift_fail_closed(self):
        claim = "The current source contains a project-owned defect."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source(["Current defect line."])
        self.write_catalog([{
            "candidate_id": ids[claim],
            "owner_scope": "project",
            "source_assertions": [{
                "source_id": "defect", "path": self.source,
                "line_start": 1, "role": "current_project_defect",
                "authority": "current_source",
            }],
        }])
        self.build()
        with open(self.source, "a", encoding="utf-8") as fh:
            fh.write("drift\n")
        with self.assertRaises(post.PostEntailmentError):
            post._load_manifest(self.manifest_path)

        # Rebuild a clean fixture, then prove transcript drift is also fatal.
        self.tearDown()
        self.setUp()
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_catalog([{"candidate_id": ids[claim], "owner_scope": "manual"}])
        self.build()
        with open(self.transcript, "a", encoding="utf-8") as fh:
            fh.write("\n")
        with self.assertRaises(post.PostEntailmentError):
            post._load_manifest(self.manifest_path)

    def test_symlink_source_is_rejected(self):
        claim = "The current source contains a project-owned defect."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source(["Current defect line."])
        link = os.path.join(self.tmp, "linked-source.md")
        os.symlink(self.source, link)
        self.write_catalog([{
            "candidate_id": ids[claim],
            "source_assertions": [{
                "source_id": "defect", "path": link, "line_start": 1,
                "role": "current_project_defect", "authority": "current_source",
            }],
        }])
        with self.assertRaises(post.PostEntailmentError):
            self.build()

    def test_manifest_is_body_free_and_source_lines_are_hashed(self):
        claim = "A canonical handoff already owns this durable rule."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        source_text = "Canonical source contains the owner rule."
        self.write_source([source_text])
        self.write_catalog([{
            "candidate_id": ids[claim],
            "source_assertions": [{
                "source_id": "canonical", "path": self.source, "line_start": 1,
                "role": "stronger_duplicate", "authority": "canonical_handoff",
            }],
        }])
        manifest = self.build()
        rendered = legacy._canonical(manifest)
        self.assertNotIn(claim, rendered)
        self.assertNotIn(source_text, rendered)
        self.assertEqual(len(manifest["rows"][0]["source_assertions"]), 1)
        self.assertTrue(
            manifest["rows"][0]["source_assertions"][0]["selected_text_sha256"])

    def test_secret_bearing_source_selection_is_rejected(self):
        claim = "A source assertion must be safe to render as a pointer."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source(["integration api_key=abcdefgh12345678"])
        self.write_catalog([{
            "candidate_id": ids[claim],
            "source_assertions": [{
                "source_id": "unsafe", "path": self.source, "line_start": 1,
                "role": "stronger_duplicate", "authority": "canonical_handoff",
            }],
        }])
        with self.assertRaises(post.PostEntailmentError):
            self.build()

    def test_source_catalog_hash_drift_is_rejected(self):
        claim = "A stable candidate waits for semantic review."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_catalog([{"candidate_id": ids[claim]}])
        self.build()
        with open(self.catalog_path, "a", encoding="utf-8") as fh:
            fh.write("\n")
        with self.assertRaises(post.PostEntailmentError):
            post._load_manifest(self.manifest_path)


class PostEntailmentRoutingTest(PostEntailmentBase):
    def test_duplicate_points_to_stronger_bound_source(self):
        claim = "A canonical handoff already owns this durable rule."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source(["The canonical handoff preserves the durable rule."])
        self.write_catalog([{
            "candidate_id": ids[claim], "owner_scope": "memory",
            "source_assertions": [{
                "source_id": "canonical-rule", "path": self.source,
                "line_start": 1, "role": "stronger_duplicate",
                "authority": "canonical_handoff",
            }],
        }])
        manifest = self.build()
        summary, _path = post.run_replay(manifest, self.output)
        row = self.result_rows()[0][0]
        self.assertEqual(summary["terminal_outcomes"], {"duplicate_existing": 1})
        self.assertEqual(row["novelty"]["status"], "duplicate")
        self.assertEqual(row["novelty"]["related_source_ids"], ["canonical-rule"])

    def test_current_source_defect_routes_to_project_fix(self):
        claim = "The demo view still renders an unlocalized relative-date label."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source(['let scannedLabel = mock ? "unlocalized demo" : ""'])
        self.write_catalog([{
            "candidate_id": ids[claim], "owner_scope": "demo-app",
            "source_assertions": [{
                "source_id": "swift-defect", "path": self.source,
                "line_start": 1, "role": "current_project_defect",
                "authority": "current_source",
            }],
        }])
        manifest = self.build()
        _summary, _path = post.run_replay(manifest, self.output)
        row = self.result_rows()[0][0]
        self.assertEqual(row["final_outcome"], "project_fix_candidate")
        self.assertEqual(row["routing"]["destination"], "project_fix")
        self.assertEqual(row["routing"]["action"], "create_candidate")

    def test_incident_requires_observation_and_resolution_context(self):
        claim = "A point-in-time incident observation was recorded for the asset."
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source([
            "The incident handoff records the time-scoped observation.",
            "The same handoff records compensating controls and resolution.",
        ])
        base = {
            "candidate_id": ids[claim], "owner_scope": "incident-handoff",
            "incident_scope": {
                "observed_at": "2026-07-13",
                "asset_scope": "controlled-test-asset",
                "resolution_source_ids": ["resolution"],
            },
            "source_assertions": [{
                "source_id": "observation", "path": self.source,
                "line_start": 1, "role": "incident_observation",
                "authority": "incident_handoff",
            }, {
                "source_id": "resolution", "path": self.source,
                "line_start": 2, "role": "incident_resolution",
                "authority": "incident_handoff",
            }],
        }
        self.write_catalog([base])
        manifest = self.build()
        post.run_replay(manifest, self.output)
        row = self.result_rows()[0][0]
        self.assertEqual(row["final_outcome"], "incident_evidence_candidate")
        self.assertEqual(row["routing"]["action"], "preserve_existing")

        self.tearDown()
        self.setUp()
        _manifest, ids = self.seed_upstream([self.candidate(claim)])
        self.write_source(["Only the observation is present."])
        self.write_catalog([{
            "candidate_id": ids[claim],
            "incident_scope": {
                "observed_at": "2026-07-13",
                "asset_scope": "controlled-test-asset",
                "resolution_source_ids": [],
            },
            "source_assertions": [{
                "source_id": "observation", "path": self.source,
                "line_start": 1, "role": "incident_observation",
                "authority": "incident_handoff",
            }],
        }])
        manifest = self.build()
        post.run_replay(manifest, self.output)
        self.assertEqual(self.result_rows()[0][0]["final_outcome"],
                         "needs_manual_review")

    def test_bound_later_turn_supersedes_and_ambiguous_stays_manual(self):
        claim = "The runtime snapshot shows the core process at 1286 MB."
        later = "After reclaim the process uses 77 MB and available RAM rose."
        _manifest, ids = self.seed_upstream(
            [self.candidate(claim)], [claim, later])
        turns = candidate_replay._full_turns(self.transcript)
        later_index = next(index for index, (_role, text) in enumerate(turns)
                           if text == later)
        later_hash = legacy._sha_bytes(later.encode("utf-8"))
        self.write_catalog([{
            "candidate_id": ids[claim],
            "later_turn_assertions": [{
                "assistant_turn_index": later_index,
                "text_sha256": later_hash,
                "relation": "supersedes",
            }],
        }])
        manifest = self.build()
        post.run_replay(manifest, self.output)
        row = self.result_rows()[0][0]
        self.assertEqual(row["final_outcome"], "superseded_later_context")
        self.assertEqual(row["currentness"]["status"], "superseded")

        self.tearDown()
        self.setUp()
        _manifest, ids = self.seed_upstream(
            [self.candidate(claim)], [claim, later])
        turns = candidate_replay._full_turns(self.transcript)
        later_index = next(index for index, (_role, text) in enumerate(turns)
                           if text == later)
        self.write_catalog([{
            "candidate_id": ids[claim],
            "later_turn_assertions": [{
                "assistant_turn_index": later_index,
                "text_sha256": legacy._sha_bytes(later.encode("utf-8")),
                "relation": "ambiguous",
            }],
        }])
        manifest = self.build()
        post.run_replay(manifest, self.output)
        self.assertEqual(self.result_rows()[0][0]["final_outcome"],
                         "needs_manual_review")

    def test_overgeneralized_shapes_are_rejected(self):
        claims = [
            "Agent knowledge proved more accurate and valuable, and the live "
            "pipeline nearly eliminates obsolescence.",
            "At filing time obsolescence approximately 0 because mining is fast.",
        ]
        _manifest, ids = self.seed_upstream([self.candidate(item) for item in claims])
        self.write_catalog([{"candidate_id": ids[item]} for item in claims])
        manifest = self.build()
        summary, _path = post.run_replay(manifest, self.output)
        self.assertEqual(summary["terminal_outcomes"],
                         {"rejected_overgeneralized": 2})

    def test_weak_retrieval_and_hybrid_distinct_never_create_memory(self):
        claims = [
            "A durable stable rule may be useful across future sessions.",
            "A second durable stable rule may be useful across future sessions.",
        ]
        _manifest, ids = self.seed_upstream([self.candidate(item) for item in claims])
        self.write_catalog([{
            "candidate_id": ids[claims[0]],
            "retrieval": {"mode": "fts_only", "status": "distinct",
                          "no_confident_results": True},
        }, {
            "candidate_id": ids[claims[1]],
            "retrieval": {"mode": "hybrid", "status": "distinct",
                          "no_confident_results": False},
        }])
        manifest = self.build()
        summary, _path = post.run_replay(manifest, self.output)
        self.assertEqual(summary["terminal_outcomes"], {"needs_manual_review": 2})
        self.assertEqual(summary["memory_candidate_count"], 0)

    def test_unowned_runtime_or_route_state_is_rejected_not_memorized(self):
        claims = [
            "The core process is holding 1286 MB of RAM in the current build.",
            "The provider route currently uses model alpha with timeout 2700.",
        ]
        _manifest, ids = self.seed_upstream([self.candidate(item) for item in claims])
        self.write_catalog([{"candidate_id": ids[item]} for item in claims])
        manifest = self.build()
        summary, _path = post.run_replay(manifest, self.output)
        self.assertEqual(summary["terminal_outcomes"],
                         {"rejected_volatile_state": 2})
        self.assertEqual(summary["memory_candidate_count"], 0)


class PostEntailmentReplaySafetyTest(PostEntailmentBase):
    def _duplicate_fixture(self, count=1):
        claims = ["Durable duplicate rule %d already has an owner." % index
                  for index in range(count)]
        _manifest, ids = self.seed_upstream([self.candidate(item) for item in claims])
        self.write_source(["Canonical owner line %d." % index
                           for index in range(count)])
        self.write_catalog([{
            "candidate_id": ids[claim],
            "source_assertions": [{
                "source_id": "source-%d" % index, "path": self.source,
                "line_start": index + 1, "role": "stronger_duplicate",
                "authority": "canonical_handoff",
            }],
        } for index, claim in enumerate(claims)])
        return self.build()

    def test_resume_is_byte_idempotent_and_sources_are_read_only(self):
        manifest = self._duplicate_fixture(2)
        protected = [self.transcript, self.source, self.candidate_manifest_path,
                     self.candidate_results_path]
        before_sources = {path: legacy._sha_file(path) for path in protected}
        post.run_replay(manifest, self.output)
        artifact_names = (
            "triage-manifest-binding.json", "triage-results.jsonl",
            "triage-summary.json", "triage-checkpoint.json")
        before = {name: _read_bytes(os.path.join(self.output, name))
                  for name in artifact_names}
        post.run_replay(manifest, self.output)
        after = {name: _read_bytes(os.path.join(self.output, name))
                 for name in artifact_names}
        self.assertEqual(before, after)
        self.assertEqual(before_sources,
                         {path: legacy._sha_file(path) for path in protected})
        self.assertEqual(len(self.result_rows()[0]), 2)

    def test_completed_unit_is_fsynced_before_process_stop(self):
        manifest = self._duplicate_fixture(2)
        original = post._evaluate
        calls = {"count": 0}

        def stop_after_one(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise KeyboardInterrupt()
            return original(*args, **kwargs)

        with mock.patch.object(post, "_evaluate", side_effect=stop_after_one):
            with self.assertRaises(KeyboardInterrupt):
                post.run_replay(manifest, self.output)
        self.assertEqual(len(self.result_rows()[0]), 1)
        summary, _path = post.run_replay(manifest, self.output)
        self.assertEqual(summary["terminal_outcomes"], {"duplicate_existing": 2})
        self.assertEqual(len(self.result_rows()[0]), 2)

    def test_truncated_final_append_is_preserved(self):
        manifest = self._duplicate_fixture(1)
        os.makedirs(self.output)
        manifest_hash = legacy._manifest_hash(manifest)
        post._bind_output(self.output, manifest_hash, manifest["created_at"])
        results = os.path.join(self.output, "triage-results.jsonl")
        with open(results, "wb") as fh:
            fh.write(b'{"partial":')
        summary, _path = post.run_replay(manifest, self.output)
        rows, malformed = legacy._read_jsonl(results)
        self.assertEqual(malformed, [1])
        self.assertTrue(any(row.get("record_type") == "recovery_marker"
                            for row in rows))
        self.assertEqual(summary["terminal_outcomes"], {"duplicate_existing": 1})

    def test_review_packet_has_no_mutation_action(self):
        manifest = self._duplicate_fixture(1)
        _summary, results = post.run_replay(manifest, self.output)
        packet = post.render_review_packet(manifest, results)
        self.assertIn("No memory import", packet)
        self.assertIn("[ ] accept", packet)
        self.assertIn("no action or command", packet)
        self.assertNotIn("--import", packet)

    def test_output_inside_live_memory_root_is_rejected(self):
        manifest = self._duplicate_fixture(1)
        live_output = os.path.join(self.ms, "events", "triage-output")
        with self.assertRaises(legacy.ReplayError):
            post.run_replay(manifest, live_output)
        self.assertFalse(os.path.exists(live_output))


class PostEntailmentNineteenFixtureTest(PostEntailmentBase):
    def test_manual_19_row_distribution_is_reproduced_exactly(self):
        fixture_path = os.path.join(
            ROOT, "tests", "fixtures", "m3_post_entailment_19.json")
        with open(fixture_path, encoding="utf-8") as fh:
            fixture = json.load(fh)
        self.assertEqual(len(fixture["rows"]), 19)
        claims = {}
        for item in fixture["rows"]:
            label = item["label"]
            shape = item["shape"]
            if shape == "overgeneralized_evaluative":
                claim = (label + " agent knowledge proved more accurate and valuable; "
                         "the live pipeline nearly eliminates this enemy.")
            elif shape == "overgeneralized_zero":
                claim = label + " filing-time obsolescence approximately 0."
            elif shape == "project_fix":
                claim = label + " current source renders an unlocalized demo label."
            elif shape == "superseded":
                claim = label + " records an interim runtime or audit state."
            elif shape == "incident":
                claim = label + " records a time-scoped incident observation."
            else:
                claim = label + " durable knowledge already has a canonical owner."
            claims[label] = claim
        later_c09 = "C09 later state records successful memory reclaim."
        later_c16 = "C16 later state records the applied and verified correction."
        assistant_turns = [claims[item["label"]] for item in fixture["rows"]]
        assistant_turns += [later_c09, later_c16]
        _manifest, ids = self.seed_upstream(
            [self.candidate(claims[item["label"]]) for item in fixture["rows"]],
            assistant_turns)
        source_lines = []
        line_by_label = {}
        for item in fixture["rows"]:
            if item["shape"] in ("duplicate", "project_fix"):
                source_lines.append(
                    item["label"] + " current stronger source evidence.")
                line_by_label[item["label"]] = len(source_lines)
        source_lines += [
            "C19 incident handoff observation with asset and time scope.",
            "C19 incident handoff resolution and compensating controls.",
        ]
        incident_lines = (len(source_lines) - 1, len(source_lines))
        self.write_source(source_lines)
        turns = candidate_replay._full_turns(self.transcript)
        later_lookup = {text: (index, legacy._sha_bytes(text.encode("utf-8")))
                        for index, (_role, text) in enumerate(turns)
                        if text in (later_c09, later_c16)}
        catalog = []
        for item in fixture["rows"]:
            label = item["label"]
            row = {"candidate_id": ids[claims[label]], "owner_scope": label}
            if item["shape"] == "duplicate":
                row["source_assertions"] = [{
                    "source_id": label + "-owner", "path": self.source,
                    "line_start": line_by_label[label],
                    "role": "stronger_duplicate",
                    "authority": "canonical_handoff",
                }]
            elif item["shape"] == "project_fix":
                row["source_assertions"] = [{
                    "source_id": label + "-source", "path": self.source,
                    "line_start": line_by_label[label],
                    "role": "current_project_defect",
                    "authority": "current_source",
                }]
            elif item["shape"] == "incident":
                row["incident_scope"] = {
                    "observed_at": "2026-07-13",
                    "asset_scope": "controlled-fixture-asset",
                    "resolution_source_ids": [label + "-resolution"],
                }
                row["source_assertions"] = [{
                    "source_id": label + "-observation", "path": self.source,
                    "line_start": incident_lines[0],
                    "role": "incident_observation",
                    "authority": "incident_handoff",
                }, {
                    "source_id": label + "-resolution", "path": self.source,
                    "line_start": incident_lines[1],
                    "role": "incident_resolution",
                    "authority": "incident_handoff",
                }]
            elif item["shape"] == "superseded":
                later = later_c09 if label == "C09" else later_c16
                index, digest = later_lookup[later]
                row["later_turn_assertions"] = [{
                    "assistant_turn_index": index,
                    "text_sha256": digest,
                    "relation": "supersedes",
                }]
            catalog.append(row)
        self.write_catalog(catalog)
        manifest = self.build()
        summary, _path = post.run_replay(manifest, self.output)
        expected_counts = Counter(
            item["expected_outcome"] for item in fixture["rows"])
        self.assertEqual(summary["terminal_outcomes"],
                         dict(sorted(expected_counts.items())))
        self.assertEqual(summary["terminal_count"], 19)
        self.assertEqual(summary["memory_candidate_count"], 0)
        actual_by_label = {
            row["claim"].split()[0]: row["final_outcome"]
            for row in self.result_rows()[0]
        }
        self.assertEqual(actual_by_label, {
            item["label"]: item["expected_outcome"]
            for item in fixture["rows"]})


if __name__ == "__main__":
    unittest.main()
