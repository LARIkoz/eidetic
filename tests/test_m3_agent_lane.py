"""M3 agent lane — spec-m3-agent-lane-plumbing (FR-1 enumeration/eligibility/
ledger, FR-2 frozen-instrument mining + metadata, FR-3 dark-file purity +
acknowledgements, FR-4 flag/envelope, FR-5 --lane agent report).

Hermetic, leg-agnostic (no model, no SDK): the miner is monkeypatched per
test (v3.1 is FROZEN — its internals are not under test here; the v3 lanes
suite covers them); the judge likewise. The live ~/.claude store is NEVER
touched — HOME and EIDETIC_MEMORY_SYSTEM point at temp dirs wherever a path
could escape.

Safety ACs baked in:
  * FR-1/D6: a pre-floor file is NEVER mined; absent/corrupt/duplicate init
    ⇒ state_lost with ZERO scanning and no replacement floor.
  * FR-3/D3: agent rows never land in the main dark file and vice versa;
    failed dark/cache/ledger appends leave NO `done` row (nothing can hide a
    missing dark row).
  * FR-5: report-time key-dedup is the wall for concurrent-drain duplicates;
    a later definitive verdict outranks transients; conflicting definitive
    outcomes are loud and gate-excluded.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import m3_acquisition as acq  # noqa: E402
import m3_agent_lane as lane  # noqa: E402
import m3_dark_report as report  # noqa: E402
import m3_hook  # noqa: E402
import m3_judge  # noqa: E402
import m3_recall_miner as miner  # noqa: E402
import m3_seen_cache as cache  # noqa: E402

ASSISTANT_TEXT = ("We decided to route all pipeline jobs through the Redis "
                  "queue because cron polling was too slow for the ingest "
                  "workers.")

VALID_ACQ = {"kind": "decision",
             "claim": ("Pipeline jobs are routed through the Redis queue; "
                       "cron polling was too slow for the ingest workers."),
             "transcript_quote": ("decided to route all pipeline jobs through "
                                  "the Redis queue because cron polling was "
                                  "too slow")}
VALID_RECALL = {"kind": "recall",
                "recall_query": "how are pipeline jobs scheduled",
                "recalled_answer": ("Pipeline jobs run through the Redis "
                                    "queue; workers consume directly from it "
                                    "and cron polling was retired last "
                                    "quarter for speed.")}

QUIET_NS = lane.QUIESCENCE_NS + 60 * 10 ** 9  # comfortably quiescent


def _write_agent_transcript(path, turns=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    turns = turns or [("user", "please do the thing"),
                      ("assistant", ASSISTANT_TEXT)]
    with open(path, "w", encoding="utf-8") as f:
        for role, text in turns:
            rec = {"type": role,
                   "message": {"content": [{"type": "text", "text": text}]}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _age(path, ns_ago):
    """Set st_mtime_ns to now − ns_ago (os.utime ns granularity)."""
    m = time.time_ns() - ns_ago
    os.utime(path, ns=(m, m))
    return m


class _Base(unittest.TestCase):
    """Temp HOME + memory-system + one parent project tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3agent-")
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        self.ms = os.path.join(self.tmp, "memory-system")
        os.makedirs(os.path.join(self.ms, "events"), exist_ok=True)
        os.environ["EIDETIC_MEMORY_SYSTEM"] = self.ms
        # Hermetic vs the DEPLOYED box: once EIDETIC_M3_AGENT_LANE=on lands in
        # settings.json the ambient env carries it into the test process, and
        # mock.patch.dict ADDS keys without removing ambient ones — a
        # flag-off test would silently see the flag on (bitten live 07-12).
        self.ambient_flag = os.environ.pop("EIDETIC_M3_AGENT_LANE", None)
        self.slug = "-proj-alpha"
        self.project_dir = os.path.join(self.tmp, ".claude", "projects",
                                        self.slug)
        self.sid = "aaaa-bbbb"
        self.parent_transcript = os.path.join(self.project_dir,
                                              f"{self.sid}.jsonl")
        _write_agent_transcript(self.parent_transcript)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if self.home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.home
        os.environ.pop("EIDETIC_MEMORY_SYSTEM", None)
        if self.ambient_flag is None:
            os.environ.pop("EIDETIC_M3_AGENT_LANE", None)
        else:
            os.environ["EIDETIC_M3_AGENT_LANE"] = self.ambient_flag
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixtures ------------------------------------------------------------

    def _plain_agent(self, name="agent-p1", sid=None, ns_ago=QUIET_NS):
        p = os.path.join(self.project_dir, sid or self.sid, "subagents",
                         f"{name}.jsonl")
        _write_agent_transcript(p)
        m = _age(p, ns_ago)
        return p, m

    def _wf_agent(self, name="agent-w1", wf="wf_x1", sid=None,
                  ns_ago=QUIET_NS):
        p = os.path.join(self.project_dir, sid or self.sid, "subagents",
                         "workflows", wf, f"{name}.jsonl")
        _write_agent_transcript(p)
        m = _age(p, ns_ago)
        return p, m

    def _init(self, floor_ns=None):
        res = lane.init_floor(self.ms, now_ns=floor_ns or (
            time.time_ns() - 2 * QUIET_NS))
        self.assertEqual(res["status"], "initialized", res)
        return res

    def _mine_returns(self, cands, error=None):
        """Patch the FROZEN miner: every file yields these candidates."""
        def fake(path, *, session_id=None, project_slug=""):
            out = []
            for c in cands:
                c = dict(c)
                c["session_id"] = session_id
                c["project_slug"] = project_slug
                out.append(c)
            return out, {"turns": 2, "error": error}
        return mock.patch.object(lane.miner, "mine_transcript",
                                 side_effect=fake)

    def _judge_entailed(self):
        return mock.patch.object(m3_judge, "verdict",
                                 return_value="entailed")

    def _drain(self):
        return lane.drain(self.parent_transcript, self.slug, self.ms)

    def _dark_rows(self, fname=lane.DARK_FILE):
        path = os.path.join(self.ms, "events", fname)
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def _ledger_rows(self):
        return self._dark_rows(lane.LEDGER_FILE)


class InitFloorTest(_Base):
    """FR-1 — explicit write-once init; fail-closed state."""

    def test_init_writes_one_row_and_second_refuses(self):
        res = lane.init_floor(self.ms)
        self.assertEqual(res["status"], "initialized")
        rows1 = self._ledger_rows()
        self.assertEqual(len(rows1), 1)
        self.assertEqual(rows1[0]["type"], "init")
        self.assertIsInstance(rows1[0]["mtime_floor_ns"], int)
        res2 = lane.init_floor(self.ms)
        self.assertEqual(res2["status"], "refused")
        self.assertEqual(self._ledger_rows(), rows1)  # no mutation

    def test_init_short_write_is_recoverable(self):
        """Review P2-6: a short os.write must NOT report 'initialized' and
        must NOT leave a partial init occupying the O_EXCL path (that state
        was permanently refused + state_lost). It fails controlled and a
        retry succeeds."""
        real_write = os.write
        with mock.patch.object(lane.os, "write",
                               side_effect=lambda fd, data:
                               real_write(fd, data[:len(data) - 1])):
            res = lane.init_floor(self.ms)
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["error"], "short_write")
        self.assertFalse(os.path.isfile(
            os.path.join(self.ms, "events", lane.LEDGER_FILE)))
        res2 = lane.init_floor(self.ms)  # retry works
        self.assertEqual(res2["status"], "initialized")
        _floor, _done, status = lane.read_ledger(self.ms)
        self.assertEqual(status, "ok")

    def test_flag_on_absent_init_is_state_lost_zero_scan(self):
        self._plain_agent()
        with mock.patch.object(lane, "enumerate_eligible") as en:
            block = self._drain()
        self.assertEqual(block["status"], "state_lost")
        en.assert_not_called()  # zero scanning
        self.assertEqual(block["scanned"], 0)
        self.assertFalse(os.path.isfile(
            os.path.join(self.ms, "events", lane.LEDGER_FILE)))  # no new floor

    def test_corrupt_init_is_state_lost(self):
        with open(os.path.join(self.ms, "events", lane.LEDGER_FILE), "w") as f:
            f.write("not json at all\n")
        self.assertEqual(self._drain()["status"], "state_lost")

    def test_duplicate_init_is_state_lost(self):
        self._init()
        with open(os.path.join(self.ms, "events", lane.LEDGER_FILE), "a") as f:
            f.write(json.dumps({"type": "init", "mtime_floor_ns": 1}) + "\n")
        self.assertEqual(self._drain()["status"], "state_lost")

    def test_non_first_init_is_state_lost(self):
        path = os.path.join(self.ms, "events", lane.LEDGER_FILE)
        with open(path, "w") as f:
            f.write(json.dumps({"type": "done", "agent_file": "x",
                                "mtime_ns": 1}) + "\n")
            f.write(json.dumps({"type": "init", "mtime_floor_ns": 1}) + "\n")
        self.assertEqual(self._drain()["status"], "state_lost")

    def test_malformed_done_line_skipped_fails_toward_remine(self):
        self._init(floor_ns=1)
        path = os.path.join(self.ms, "events", lane.LEDGER_FILE)
        with open(path, "a") as f:
            f.write("garbage line\n")
        floor, done, status = lane.read_ledger(self.ms)
        self.assertEqual(status, "ok")
        self.assertEqual(done, {})


class EnumerationTest(_Base):
    """FR-1 — discovery, eligibility, cap, quiescence, re-queue."""

    def test_pre_floor_file_never_mined(self):
        p, m = self._plain_agent()
        self._init(floor_ns=m + 1)  # floor ABOVE the file's mtime
        with self._mine_returns([]) as fake:
            block = self._drain()
        fake.assert_not_called()
        self.assertEqual(block["eligible"], 0)
        self.assertEqual(block["scanned"], 1)

    def test_discovers_plain_and_workflow_not_adjacent_files(self):
        self._init(floor_ns=1)
        p1, _ = self._plain_agent()
        p2, _ = self._wf_agent()
        # adjacent NON-matching files (FR-0 observed: .meta.json + journal)
        for junk in ("compact-x.jsonl", "agent-w1.meta.json"):
            jp = os.path.join(os.path.dirname(p2), junk)
            with open(jp, "w") as f:
                f.write("{}\n")
            _age(jp, QUIET_NS)
        jp2 = os.path.join(self.project_dir, self.sid, "subagents",
                           "compact-y.jsonl")
        with open(jp2, "w") as f:
            f.write("{}\n")
        _age(jp2, QUIET_NS)
        with self._mine_returns([]):
            block = self._drain()
        self.assertEqual(block["scanned"], 2)
        self.assertEqual(block["mined_files"], 2)
        rows = self._ledger_rows()
        kinds = {r["agent_file"] for r in rows if r.get("type") == "done"}
        self.assertEqual(len(kinds), 2)

    def test_cap_three_per_fire_then_rest_without_remine(self):
        self._init(floor_ns=1)
        paths = []
        for i in range(5):
            # distinct mtimes → deterministic newest-first order
            paths.append(self._plain_agent(
                name=f"agent-c{i}", ns_ago=QUIET_NS + i * 10 ** 9))
        with self._mine_returns([]) as fake:
            b1 = self._drain()
        self.assertEqual(b1["mined_files"], 3)
        self.assertEqual(b1["backlog"], 2)
        # the miner receives SNAPSHOT paths (P1-2) — assert by session stem
        mined1 = {c.kwargs["session_id"] for c in fake.call_args_list}
        # newest three first
        self.assertEqual(mined1, {"agent-c0", "agent-c1", "agent-c2"})
        with self._mine_returns([]) as fake2:
            b2 = self._drain()
        self.assertEqual(b2["mined_files"], 2)  # remaining two, no re-mine
        mined2 = {c.kwargs["session_id"] for c in fake2.call_args_list}
        self.assertEqual(mined2, {"agent-c3", "agent-c4"})

    def test_warm_file_skipped_then_picked_up_quiescent(self):
        self._init(floor_ns=1)
        p, _ = self._plain_agent(ns_ago=10 ** 9)  # 1 s old — warm
        with self._mine_returns([]) as fake:
            b1 = self._drain()
        fake.assert_not_called()
        self.assertEqual(b1["eligible"], 0)
        self.assertEqual(b1["scanned"], 1)
        _age(p, QUIET_NS)  # now quiescent
        with self._mine_returns([]) as fake2:
            b2 = self._drain()
        self.assertEqual(b2["mined_files"], 1)

    def test_mtime_advance_requeues_once_cache_makes_idempotent(self):
        self._init(floor_ns=1)
        p, _ = self._plain_agent()
        judge_calls = []
        with self._mine_returns([VALID_ACQ]), \
                mock.patch.object(m3_judge, "verdict",
                                  side_effect=lambda *a, **k:
                                  judge_calls.append(1) or "entailed"):
            b1 = self._drain()
        self.assertEqual(b1["tally"].get("would_file"), 1)
        self.assertEqual(len(judge_calls), 1)
        rows1 = self._dark_rows()
        self.assertEqual(len(rows1), 1)
        # done at recorded mtime → not eligible again
        with self._mine_returns([VALID_ACQ]) as fake:
            b2 = self._drain()
        fake.assert_not_called()
        # mtime advances (agent resumed) → re-queues EXACTLY once
        _age(p, lane.QUIESCENCE_NS + 5 * 10 ** 9)
        with self._mine_returns([VALID_ACQ]), \
                mock.patch.object(m3_judge, "verdict",
                                  side_effect=lambda *a, **k:
                                  judge_calls.append(1) or "entailed"):
            b3 = self._drain()
        # FR-8 cache primed → 0 judge calls, 0 duplicate dark rows
        self.assertEqual(len(judge_calls), 1)
        self.assertEqual(b3["skipped_seen"], 1)
        self.assertEqual(len(self._dark_rows()), 1)
        # advanced-mtime done row appended → eligibility consumes the LATEST
        with self._mine_returns([VALID_ACQ]) as fake:
            b4 = self._drain()
        fake.assert_not_called()

    def test_same_relpath_two_projects_distinct_scopes(self):
        """Two projects, same agent_file relpath and the SAME st_mtime_ns →
        distinct done-ledger keys and cache scopes; neither suppresses the
        other (review P1-1: a bare-relpath done key made the second project's
        equal-or-older file silently 'processed'; equal mtimes make this test
        adversarial instead of accidentally green via a fresher mtime)."""
        self._init(floor_ns=1)
        slug2 = "-proj-beta"
        proj2 = os.path.join(self.tmp, ".claude", "projects", slug2)
        parent2 = os.path.join(proj2, f"{self.sid}.jsonl")
        _write_agent_transcript(parent2)
        p1, m1 = self._plain_agent()  # project 1
        p2 = os.path.join(proj2, self.sid, "subagents", "agent-p1.jsonl")
        _write_agent_transcript(p2)
        os.utime(p2, ns=(m1, m1))  # EXACTLY the same mtime as project 1
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            b1 = lane.drain(self.parent_transcript, self.slug, self.ms)
            b2 = lane.drain(parent2, slug2, self.ms)
        self.assertEqual(b1["mined_files"], 1)
        self.assertEqual(b2["mined_files"], 1)  # NOT suppressed by A's done
        rows = self._dark_rows()
        self.assertEqual(len(rows), 2)  # no cross-project suppression
        with open(os.path.join(self.ms, "events", cache.JUDGED_FILE),
                  encoding="utf-8") as f:
            scopes = {json.loads(ln)["session_id"] for ln in f if ln.strip()}
        self.assertEqual(scopes, {f"{self.slug}/{self.sid}/subagents/agent-p1.jsonl",
                                  f"{slug2}/{self.sid}/subagents/agent-p1.jsonl"})

    def test_snapshot_isolates_quote_gate_from_growing_transcript(self):
        """Review P1-2: the miner and the quote gate must read ONE immutable
        snapshot. The live transcript gains 35 turns mid-drain (after mining,
        before the gate); with two reads of the live path the mined quote
        slides out of the 30-turn window → false-definitive would_reject,
        cached forever. With the snapshot the candidate must pass."""
        self._init(floor_ns=1)
        p, _ = self._plain_agent()

        def fake_mine(path, *, session_id=None, project_slug=""):
            # grow the ORIGINAL file mid-drain (the miner received a snapshot)
            junk = [("assistant", f"filler turn {i} nothing to see here")
                    for i in range(35)]
            with open(p, "a", encoding="utf-8") as f:
                for role, text in junk:
                    f.write(json.dumps({"type": role, "message": {
                        "content": [{"type": "text", "text": text}]}},
                        ensure_ascii=False) + "\n")
            c = dict(VALID_ACQ)
            c["session_id"] = session_id
            c["project_slug"] = project_slug
            return [c], {"turns": 2, "error": None}

        with mock.patch.object(lane.miner, "mine_transcript",
                               side_effect=fake_mine), self._judge_entailed():
            block = self._drain()
        self.assertEqual(block["tally"].get("would_file"), 1,
                         block["tally"])  # gate saw the snapshot, not the tail
        rows = self._dark_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["quote_ok"])

    def test_selected_file_deleted_before_drain_counted_not_fatal(self):
        self._init(floor_ns=1)
        p, m = self._plain_agent()

        real_isfile = os.path.isfile

        def vanish(path):
            if path == p:
                return False  # deleted between select and drain
            return real_isfile(path)

        with self._mine_returns([]) as fake, \
                mock.patch.object(lane.os.path, "isfile", side_effect=vanish):
            block = self._drain()
        fake.assert_not_called()
        self.assertEqual(block["tally"].get("missing"), 1)
        self.assertEqual(block["status"], "ran")

    def test_concurrent_drains_duplicate_rows_report_collapses(self):
        """FR-5 as the wall, asserted not assumed: two interleaved drains of
        one project (ledger/cache writes invisible to each other) → duplicate
        dark rows MAY appear; the --lane agent report collapses them to ONE
        knowledge item with correct counts."""
        self._init(floor_ns=1)
        self._plain_agent()
        # Drain A: its ledger done + cache records are LOST (simulates the
        # other process not seeing them mid-flight).
        with self._mine_returns([VALID_ACQ]), self._judge_entailed(), \
                mock.patch.object(lane, "_append_ledger", return_value=True), \
                mock.patch.object(cache, "record", return_value=True):
            self._drain()
        # Drain B: sees no done row, no cache → re-mines → duplicate dark row.
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            self._drain()
        rows = self._dark_rows()
        self.assertEqual(len(rows), 2)  # duplicates DID appear
        resolved, health = report._resolve_attempts(rows)
        self.assertEqual(len(resolved), 1)  # ONE knowledge item
        self.assertEqual(health["dup_attempts"], 1)


class MiningMetadataTest(_Base):
    """FR-2 — frozen instrument, seven metadata fields, recall drop,
    quote gate, transient miner errors."""

    def test_dark_row_has_all_seven_fields_plain(self):
        self._init(floor_ns=1)
        p, m = self._plain_agent()
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            block = self._drain()
        rows = self._dark_rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["session_id"], "agent-p1")
        self.assertEqual(r["parent_session_id"], self.sid)
        self.assertEqual(r["project_slug"], self.slug)
        self.assertEqual(r["agent_kind"], "plain")
        self.assertIsNone(r["workflow_id"])
        self.assertEqual(r["agent_file"],
                         f"{self.sid}/subagents/agent-p1.jsonl")
        self.assertTrue(r["transcript_mtime"].startswith("20"))
        self.assertTrue(r["would_file"])

    def test_dark_row_workflow_kind_and_id(self):
        self._init(floor_ns=1)
        self._wf_agent(wf="wf_zz9")
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            self._drain()
        r = self._dark_rows()[0]
        self.assertEqual(r["agent_kind"], "workflow")
        self.assertEqual(r["workflow_id"], "wf_zz9")
        self.assertEqual(
            r["agent_file"],
            f"{self.sid}/subagents/workflows/wf_zz9/agent-w1.jsonl")

    def test_recall_dropped_counted_no_writes_no_judge(self):
        self._init(floor_ns=1)
        self._plain_agent()
        with self._mine_returns([VALID_RECALL]), \
                mock.patch.object(m3_judge, "verdict") as judge:
            block = self._drain()
        judge.assert_not_called()
        self.assertEqual(block["recall_dropped"], 1)
        self.assertEqual(self._dark_rows(), [])
        # definitive file: done row written, empty tally
        dones = [r for r in self._ledger_rows() if r.get("type") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["tally"], {})

    def test_quote_absent_would_reject_zero_judge(self):
        self._init(floor_ns=1)
        self._plain_agent()
        bad = dict(VALID_ACQ, transcript_quote="this text is nowhere in the "
                                               "agent transcript at all")
        with self._mine_returns([bad]), \
                mock.patch.object(m3_judge, "verdict") as judge:
            block = self._drain()
        judge.assert_not_called()
        self.assertEqual(block["judge_calls"], 0)
        rows = self._dark_rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["judge"])
        self.assertFalse(rows[0]["would_file"])
        self.assertEqual(block["tally"].get("would_reject"), 1)

    def test_miner_meta_error_is_transient_no_done(self):
        self._init(floor_ns=1)
        self._plain_agent()
        with self._mine_returns([], error="sdk:boom"):
            block = self._drain()
        self.assertEqual(block["tally"].get("miner_error"), 1)
        self.assertEqual(self._dark_rows(), [])
        self.assertEqual([r for r in self._ledger_rows()
                          if r.get("type") == "done"], [])
        # retries on a later fire
        with self._mine_returns([]):
            block2 = self._drain()
        self.assertEqual(block2["mined_files"], 1)
        dones = [r for r in self._ledger_rows() if r.get("type") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertEqual(dones[0]["tally"], {})  # definitive empty


class DarkFilePurityTest(_Base):
    """FR-3 — D3 purity, acknowledged appends, cache scope persistence."""

    def test_mixed_run_zero_cross_contamination(self):
        """Agent rows only in m3_agent_dark.jsonl; parent rows only in
        m3_acquisition_dark.jsonl."""
        self._init(floor_ns=1)
        self._plain_agent()
        # agent lane fires
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            self._drain()
        # main lane fires on the parent transcript (direct acq.process)
        parent_cand = dict(VALID_ACQ, session_id=self.sid,
                           project_slug=self.slug)
        turns = [("assistant", ASSISTANT_TEXT)]
        with self._judge_entailed():
            acq.process(self.parent_transcript, [parent_cand],
                        memory_system=self.ms, turns=turns)
        agent_rows = self._dark_rows(lane.DARK_FILE)
        main_rows = self._dark_rows(acq.DARK_FILE)
        self.assertEqual(len(agent_rows), 1)
        self.assertEqual(len(main_rows), 1)
        self.assertTrue(agent_rows[0]["session_id"].startswith("agent-"))
        self.assertEqual(main_rows[0]["session_id"], self.sid)
        self.assertNotIn("agent_file", main_rows[0])  # schema untouched

    def test_done_loss_with_valid_init_cache_holds(self):
        """Same candidate re-offered after simulated done-row loss while the
        valid init remains → cache hit: 0 judge calls, 0 new dark rows."""
        self._init(floor_ns=1)
        self._plain_agent()
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            self._drain()
        # wipe done rows, keep init + cache
        path = os.path.join(self.ms, "events", lane.LEDGER_FILE)
        rows = self._ledger_rows()
        with open(path, "w") as f:
            f.write(json.dumps(rows[0], ensure_ascii=False) + "\n")
        with self._mine_returns([VALID_ACQ]), \
                mock.patch.object(m3_judge, "verdict") as judge:
            block = self._drain()
        judge.assert_not_called()
        self.assertEqual(block["skipped_seen"], 1)
        self.assertEqual(len(self._dark_rows()), 1)

    def test_dark_append_failed_no_cache_no_done_then_retry_heals(self):
        self._init(floor_ns=1)
        self._plain_agent()
        with self._mine_returns([VALID_ACQ]), self._judge_entailed(), \
                mock.patch.object(acq, "_append_dark", return_value=False):
            block = self._drain()
        self.assertEqual(block["tally"].get("dark_append_failed"), 1)
        self.assertEqual(self._dark_rows(), [])
        self.assertEqual([r for r in self._ledger_rows()
                          if r.get("type") == "done"], [])
        cache_rows = self._dark_rows(cache.JUDGED_FILE)
        self.assertEqual(cache_rows, [])  # NO cache record
        # later retry produces the missing row + done
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            block2 = self._drain()
        self.assertEqual(len(self._dark_rows()), 1)
        self.assertEqual(len([r for r in self._ledger_rows()
                              if r.get("type") == "done"]), 1)

    def test_cache_record_failed_no_done_retry_dup_resolved(self):
        self._init(floor_ns=1)
        self._plain_agent()
        with self._mine_returns([VALID_ACQ]), self._judge_entailed(), \
                mock.patch.object(cache, "record", return_value=False):
            block = self._drain()
        self.assertEqual(block["tally"].get("cache_record_failed"), 1)
        self.assertEqual(len(self._dark_rows()), 1)  # dark row DID land
        self.assertEqual([r for r in self._ledger_rows()
                          if r.get("type") == "done"], [])
        # retry: cache miss → duplicate dark row; report resolves to ONE item
        with self._mine_returns([VALID_ACQ]), self._judge_entailed():
            self._drain()
        rows = self._dark_rows()
        self.assertEqual(len(rows), 2)
        resolved, _health = report._resolve_attempts(rows)
        self.assertEqual(len(resolved), 1)

    def test_ledger_append_failed_no_completion_retry_via_cache(self):
        self._init(floor_ns=1)
        self._plain_agent()
        with self._mine_returns([VALID_ACQ]), self._judge_entailed(), \
                mock.patch.object(lane, "_append_ledger", return_value=False):
            block = self._drain()
        self.assertEqual(block["tally"].get("ledger_append_failed"), 1)
        self.assertEqual([r for r in self._ledger_rows()
                          if r.get("type") == "done"], [])
        # retry reaches done via cache hits — zero judge cost
        with self._mine_returns([VALID_ACQ]), \
                mock.patch.object(m3_judge, "verdict") as judge:
            block2 = self._drain()
        judge.assert_not_called()
        self.assertEqual(block2["skipped_seen"], 1)
        self.assertEqual(len([r for r in self._ledger_rows()
                              if r.get("type") == "done"]), 1)
        self.assertEqual(len(self._dark_rows()), 1)  # no duplicate

    def test_zero_write_scope(self):
        """Agent-lane-scoped v3 FR-3 shape: a drain leaves /memory/ and the
        index DB byte-identical; the lane's only writes are events/ appends."""
        self._init(floor_ns=1)
        self._plain_agent()
        memdir = os.path.join(self.tmp, ".claude", "projects", self.slug,
                              "memory")
        os.makedirs(memdir, exist_ok=True)
        page = os.path.join(memdir, "existing.md")
        with open(page, "w") as f:
            f.write("original\n")
        db_dir = os.path.join(self.ms, "db")
        os.makedirs(db_dir, exist_ok=True)
        idb = os.path.join(db_dir, "index.db")
        with open(idb, "wb") as f:
            f.write(b"DBBYTES")
        with open(page, "rb") as f:
            before_page = f.read()
        with open(idb, "rb") as f:
            before_db = f.read()
        with self._mine_returns([VALID_ACQ, VALID_RECALL]), \
                self._judge_entailed():
            self._drain()
        with open(page, "rb") as f:
            self.assertEqual(f.read(), before_page)
        with open(idb, "rb") as f:
            self.assertEqual(f.read(), before_db)
        self.assertTrue(self._dark_rows())  # events/ DID gain appends


class HookWiringTest(_Base):
    """FR-4 — flag gate, driver line, per-file isolation."""

    def _run_hook(self, env):
        """Run m3_hook.main with the miner + lanes patched hermetic."""
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(m3_hook, "__name__", "m3_hook"), \
                redirect_stdout(buf):
            with mock.patch(
                    "m3_recall_miner.mine_transcript",
                    return_value=([], {"turns": 2, "error": None,
                                       "raw_by_kind": {},
                                       "kept_by_kind": {}})):
                rc = m3_hook.main(["m3_hook.py", self.parent_transcript])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def _hook_env(self, agent_flag):
        memdir = os.path.join(self.tmp, ".claude", "projects", self.slug,
                              "memory")
        os.makedirs(memdir, exist_ok=True)
        db_dir = os.path.join(self.ms, "db")
        os.makedirs(db_dir, exist_ok=True)
        open(os.path.join(db_dir, "index.db"), "a").close()
        env = {"EIDETIC_M3_DRIVER": "on",
               "EIDETIC_MEMORY_SYSTEM": self.ms,
               "EIDETIC_NO_M3_PY_REEXEC": "1"}
        if agent_flag:
            env["EIDETIC_M3_AGENT_LANE"] = agent_flag
        return env

    def test_flag_off_no_scan_line_unchanged(self):
        self._plain_agent()
        with mock.patch.object(lane, "drain") as dr:
            out = self._run_hook(self._hook_env(None))
        dr.assert_not_called()
        self.assertNotIn("agent", out)  # driver line byte-shape unchanged
        self.assertEqual(out["m3_driver"], "ran")

    def test_driver_alone_does_not_enable_lane(self):
        """D7: EIDETIC_M3_DRIVER=on alone must NOT enable the agent lane."""
        self._plain_agent()
        out = self._run_hook(self._hook_env(None))
        self.assertNotIn("agent", out)

    def test_flag_on_line_gains_agent_block(self):
        self._init(floor_ns=1)
        self._plain_agent()
        out = self._run_hook(self._hook_env("on"))
        self.assertIn("agent", out)
        block = out["agent"]
        for key in ("status", "scanned", "eligible", "backlog",
                    "oldest_eligible_s", "mined_files", "tally",
                    "skipped_seen", "recall_dropped", "judge_calls"):
            self.assertIn(key, block)
        self.assertEqual(block["status"], "ran")

    def test_flag_on_state_lost_is_loud_not_fatal(self):
        self._plain_agent()  # no init
        out = self._run_hook(self._hook_env("on"))
        self.assertEqual(out["agent"]["status"], "state_lost")

    def test_one_file_throw_does_not_kill_batch(self):
        self._init(floor_ns=1)
        p1, _ = self._plain_agent(name="agent-ok", ns_ago=QUIET_NS)
        p2, _ = self._plain_agent(name="agent-boom",
                                  ns_ago=QUIET_NS + 10 ** 9)

        def fake(path, *, session_id=None, project_slug=""):
            if "boom" in path:
                raise RuntimeError("mid-mine explosion")
            return [], {"turns": 2, "error": None}

        with mock.patch.object(lane.miner, "mine_transcript",
                               side_effect=fake):
            block = self._drain()
        self.assertEqual(block["tally"].get("miner_error"), 1)
        self.assertEqual(block["mined_files"], 1)  # the other file processed
        dones = [r for r in self._ledger_rows() if r.get("type") == "done"]
        self.assertEqual(len(dones), 1)
        self.assertTrue(dones[0]["agent_file"].endswith("agent-ok.jsonl"))

    def test_drain_failure_never_kills_hook(self):
        self._init(floor_ns=1)
        env = self._hook_env("on")
        with mock.patch.object(lane, "drain",
                               side_effect=RuntimeError("lane dead")):
            out = self._run_hook(env)
        self.assertEqual(out["agent"]["status"], "error")
        self.assertEqual(out["m3_driver"], "ran")  # hook survived


# Dynamic, always inside the report window: the agent window starts at the
# fixture init's REAL now — a hardcoded date passed on its writing day and
# silently expired at midnight (bitten live 07-13: 3 tests went red by clock).
def _fresh_ts(minutes_ahead=5):
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _agent_row(claim=None, key_suffix="", **over):
    row = {"ts": _fresh_ts(),
           "miner_policy": miner.MINER_POLICY_VERSION,
           "session_id": "agent-x1", "parent_session_id": "sid-1",
           "project_slug": "-proj-alpha", "agent_kind": "plain",
           "workflow_id": None,
           "agent_file": "sid-1/subagents/agent-x1.jsonl",
           "transcript_mtime": "2026-07-12T09:00:00.000Z",
           "kind": "finding",
           "claim": claim or ("The ingest retry queue drops records when the "
                              "backlog exceeds five thousand entries" +
                              key_suffix),
           "transcript_quote": "retry queue drops records when the backlog "
                               "exceeds five thousand",
           "quote_ok": True, "judge": "entailed", "would_file": True}
    row.update(over)
    return row


class ReportLaneTest(_Base):
    """FR-5 — --lane agent sections, resolution, D8 accounting, counters."""

    def _write_rows(self, fname, rows):
        path = os.path.join(self.ms, "events", fname)
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _render(self, coverage=0):
        buf = io.StringIO()
        argv = ["m3_dark_report.py", "--lane", "agent",
                "--memory-system", self.ms, "--coverage", str(coverage)]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
            report.main()
        return buf.getvalue()

    def test_four_sections_render_and_main_default_unchanged(self):
        self._init(floor_ns=1)
        self._write_rows(lane.DARK_FILE, [_agent_row()])
        # a real driver-log line carrying an agent block (full read path)
        self._write_rows("m3_driver.log", [{
            "m3_driver": "ran", "mined": 0, "meta": {},
            "agent": {"status": "ran", "ts": _fresh_ts(),
                      "miner_policy": miner.MINER_POLICY_VERSION,
                      "project_slug": "-proj-alpha", "scanned": 7,
                      "eligible": 1, "backlog": 0, "oldest_eligible_s": 0,
                      "mined_files": 1, "tally": {"would_file": 1},
                      "skipped_seen": 0, "recall_dropped": 0,
                      "judge_calls": 1}}])
        out = self._render()
        self.assertIn("## 1. Agent would-file marking sheet", out)
        self.assertIn("## 2. Agent lane counters", out)
        self.assertIn("## 3. Window coverage sample", out)
        self.assertIn("## 4. Dup & health visibility", out)
        self.assertIn("mtime=2026-07-12T09:00:00.000Z", out)
        self.assertIn("agent fires: 1", out)          # block read from the log
        self.assertIn("files scanned: 7", out)
        # default invocation still renders the MAIN report sections
        buf = io.StringIO()
        with mock.patch.object(sys, "argv",
                               ["m3_dark_report.py", "--memory-system",
                                self.ms, "--coverage", "0"]), \
                redirect_stdout(buf):
            report.main()
        main_out = buf.getvalue()
        self.assertIn("## 1. Would-file marking sheet", main_out)
        self.assertIn("## 2. Consolidation counters", main_out)
        self.assertNotIn("Agent lane counters", main_out)

    def test_retry_resolution_transient_then_definitive(self):
        """judge_unavailable → entailed = ONE would-file item; transient-only
        keys produce none; conflicting definitive outcomes are loud and
        gate-excluded."""
        self._init(floor_ns=1)
        rows = [
            # key A: transient then definitive → one item
            _agent_row(judge="judge_unavailable", would_file=False),
            _agent_row(judge="entailed", would_file=True),
            # key B: transient only → health, never an item
            _agent_row(claim="Different transient claim about the parser "
                             "cache eviction path being disabled",
                       judge="judge_unavailable", would_file=False),
            # key C: conflicting definitive → excluded, loud
            _agent_row(claim="Conflicting claim on the retry limit default "
                             "being three attempts per batch",
                       judge="entailed", would_file=True),
            _agent_row(claim="Conflicting claim on the retry limit default "
                             "being three attempts per batch",
                       judge="not_entailed", would_file=False),
        ]
        resolved, health = report._resolve_attempts(rows)
        self.assertEqual(len(resolved), 1)
        self.assertTrue(resolved[0]["would_file"])
        self.assertEqual(health["transient_only"], 1)
        self.assertEqual(len(health["conflicts"]), 1)
        self._write_rows(lane.DARK_FILE, rows)
        out = self._render()
        self.assertIn("1 knowledge items", out)
        self.assertIn("CONFLICTING definitive outcomes — gate-EXCLUDED "
                      "until adjudicated: 1", out)

    def test_two_scopes_resolve_independently_then_cluster(self):
        """Identical candidate text under two canonical scopes resolves
        independently BEFORE clustering may merge them into one item."""
        rows = [
            _agent_row(),
            _agent_row(agent_file="sid-2/subagents/agent-y2.jsonl",
                       session_id="agent-y2", parent_session_id="sid-2"),
        ]
        resolved, health = report._resolve_attempts(rows)
        self.assertEqual(len(resolved), 2)  # two independent resolutions
        self.assertEqual(health["dup_attempts"], 0)
        clusters = report._cluster_paraphrases(
            [r for r in resolved if r.get("would_file")])
        self.assertEqual(len(clusters), 1)  # ONE knowledge item after cluster

    def test_cross_lane_already_seen_main_tagging(self):
        """Same claim in main + agent → one TAGGED agent quality item, zero
        incremental, no row-level double-count."""
        self._init(floor_ns=1)
        agent_rows = [_agent_row()]
        main_row = {"ts": "2026-07-12T08:00:00.000Z", "session_id": "sid-9",
                    "miner_policy": miner.MINER_POLICY_VERSION,
                    "project_slug": "-proj-alpha", "kind": "finding",
                    "claim": agent_rows[0]["claim"] + " indeed",
                    "transcript_quote": "whatever quote",
                    "quote_ok": True, "judge": "entailed", "would_file": True}
        self._write_rows(lane.DARK_FILE, agent_rows)
        self._write_rows(acq.DARK_FILE, [main_row])
        out = self._render()
        self.assertIn("[already_seen_main]", out)
        self.assertIn("knowledge items (clustered): 1 · net-new vs main: 0 "
                      "· already_seen_main: 1", out)

    def test_report_duplicate_init_suppresses_gate(self):
        """Review P1-3: the report must inherit D6 fail-closed — duplicate
        init is state_lost at runtime, so the report must NOT render gate
        sections from it."""
        self._init(floor_ns=1)
        with open(os.path.join(self.ms, "events", lane.LEDGER_FILE), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"type": "init", "mtime_floor_ns": 2}) + "\n")
        self._write_rows(lane.DARK_FILE, [_agent_row()])
        out = self._render()
        self.assertIn("STATE LOST", out)
        self.assertIn("gate rendering SUPPRESSED", out)
        self.assertNotIn("## 1. Agent would-file marking sheet", out)
        self.assertNotIn("Agent-D5 gate", out)

    def test_density_d8_net_new_numerator(self):
        """Review P1-4a: already_seen_main clusters stay in the quality
        denominator but are EXCLUDED from the density numerator (D8)."""
        r1 = _agent_row()
        r2 = _agent_row(claim="Second wholly different finding about the "
                              "shard planner splitting by key range",
                        agent_file="sid-2/subagents/agent-z9.jsonl",
                        parent_session_id="sid-2")
        clusters = report._cluster_paraphrases([r1, r2])
        self.assertEqual(len(clusters), 2)
        text = report.section_agent_counters(
            [], [r1, r2], [], [r1, r2], clusters, [True, False])
        # 1 net-new / 2 mined sessions = 0.50 (was 1.00 with the D8 bug)
        self.assertIn("NET-NEW would-file per mined agent-session (cap 4): "
                      "0.50", text)
        self.assertIn("net-new vs main: 1 · already_seen_main: 1", text)

    def test_density_denominator_scoped_by_project(self):
        """Review P1-4b: same agent_file relpath in TWO projects = two mined
        sessions, not one (canonical identity in the denominator)."""
        r1 = _agent_row()
        r2 = _agent_row(claim="Second wholly different finding about the "
                              "shard planner splitting by key range",
                        project_slug="-proj-beta")  # SAME agent_file relpath
        clusters = report._cluster_paraphrases([r1, r2])
        text = report.section_agent_counters(
            [], [r1, r2], [], [r1, r2], clusters, [False, False])
        # 2 net-new / 2 scoped sessions = 1.00 (bare-relpath bug gave 2.00)
        self.assertIn("(cap 4): 1.00", text)

    def test_latest_snapshot_by_ts_not_row_order(self):
        """Review P2-5: concurrent drains append out of order — the latest
        snapshot per project is the MAX agent-block ts, not the last row."""
        newer = {"status": "ran", "ts": "2026-07-12T05:00:00.000Z",
                 "project_slug": "-proj-alpha", "scanned": 1, "eligible": 1,
                 "backlog": 7, "oldest_eligible_s": 700, "mined_files": 0,
                 "tally": {}, "skipped_seen": 0, "recall_dropped": 0,
                 "judge_calls": 0}
        older_late_row = {"status": "ran", "ts": "2026-07-12T04:00:00.000Z",
                          "project_slug": "-proj-alpha", "scanned": 1,
                          "eligible": 1, "backlog": 2, "oldest_eligible_s": 9,
                          "mined_files": 0, "tally": {}, "skipped_seen": 0,
                          "recall_dropped": 0, "judge_calls": 0}
        text = report.section_agent_counters(
            [newer, older_late_row], [], [], [], [], [])
        self.assertIn("backlog depth (latest per project, summed): 7", text)
        self.assertIn("oldest eligible age: 700s", text)

    def test_infra_health_tallies_rendered(self):
        """Review P2-7: file-level failures must surface in the report, not
        hide in the driver log — infra failure must not read as low yield."""
        b = {"status": "ran", "ts": "2026-07-12T05:00:00.000Z",
             "project_slug": "-proj-alpha", "scanned": 3, "eligible": 3,
             "backlog": 0, "oldest_eligible_s": 0, "mined_files": 1,
             "tally": {"miner_error": 2, "dark_append_failed": 1,
                       "would_file": 1},
             "skipped_seen": 0, "recall_dropped": 0, "judge_calls": 1}
        text = report.section_agent_counters([b], [], [], [], [], [])
        self.assertIn('"dark_append_failed": 1', text)
        self.assertIn('"miner_error": 2', text)
        self.assertNotIn('"would_file"', text.split("infra health")[1]
                         .splitlines()[0])  # yield keys stay out of infra

    def test_counter_aggregation_sum_vs_latest_and_parent_bar(self):
        """Multiple projects/fires: sums for scanned/mined/judge; backlog +
        oldest from the LATEST snapshot per project; accumulation counts
        distinct parent sessions with definitive rows."""
        blocks = [
            {"status": "ran", "ts": "2026-07-12T01:00:00.000Z",
             "project_slug": "-proj-alpha", "scanned": 4, "eligible": 3,
             "backlog": 2, "oldest_eligible_s": 100, "mined_files": 1,
             "tally": {}, "skipped_seen": 0, "recall_dropped": 1,
             "judge_calls": 2},
            {"status": "ran", "ts": "2026-07-12T02:00:00.000Z",
             "project_slug": "-proj-alpha", "scanned": 5, "eligible": 2,
             "backlog": 1, "oldest_eligible_s": 50, "mined_files": 2,
             "tally": {}, "skipped_seen": 1, "recall_dropped": 0,
             "judge_calls": 3},
            {"status": "ran", "ts": "2026-07-12T03:00:00.000Z",
             "project_slug": "-proj-beta", "scanned": 2, "eligible": 1,
             "backlog": 4, "oldest_eligible_s": 500, "mined_files": 1,
             "tally": {}, "skipped_seen": 0, "recall_dropped": 2,
             "judge_calls": 1},
        ]
        resolved = [
            _agent_row(),
            _agent_row(claim="Second finding about beta project workers "
                             "using the shared retry budget pool",
                       parent_session_id="sid-2",
                       project_slug="-proj-beta"),
        ]
        clusters = report._cluster_paraphrases(resolved)
        text = report.section_agent_counters(
            blocks, resolved, [], resolved, clusters, [False, False])
        self.assertIn("fires (agent blocks): 3", text)
        self.assertIn("files scanned: 11 · eligible: 6 · mined: 4", text)
        # latest alpha (backlog 1) + latest beta (backlog 4) = 5; max age 500
        self.assertIn("backlog depth (latest per project, summed): 5 · "
                      "oldest eligible age: 500s", text)
        self.assertIn("recall_dropped: 3 · judge calls: 6", text)
        self.assertIn("distinct parent sessions with ≥1 definitive agent "
                      "row: 2", text)

    def test_coverage_resolves_agent_paths_plain_and_workflow(self):
        """Coverage fixtures resolve nested transcripts from agent_file and
        actually sample them (not merely skip)."""
        self._init(floor_ns=1)
        # real files on disk under the fixture HOME
        p1, _ = self._plain_agent(name="agent-cov1")
        p2, _ = self._wf_agent(name="agent-cov2", wf="wf_c7")
        rows = [
            _agent_row(agent_file=f"{self.sid}/subagents/agent-cov1.jsonl"),
            _agent_row(agent_file=f"{self.sid}/subagents/workflows/wf_c7/"
                                  f"agent-cov2.jsonl",
                       agent_kind="workflow", workflow_id="wf_c7",
                       claim="Workflow coverage claim about the batch "
                             "planner splitting jobs by shard key"),
        ]
        probed = []
        with mock.patch.object(report, "_coverage_probe",
                               side_effect=lambda path, judge:
                               probed.append(path) or (True, 0)):
            text = report.section_agent_coverage(rows, [], 10, False)
        self.assertEqual(len(probed), 2)
        self.assertIn("plain: 1 · workflow: 1 · missing: 0", text)


class WindowStartTest(_Base):
    """FR-5 — the agent window starts at the authoritative init ts."""

    def test_rows_before_init_ts_excluded(self):
        lane.init_floor(self.ms)  # init ts = now
        old = _agent_row(ts="2020-01-01T00:00:00.000Z")
        path = os.path.join(self.ms, "events", lane.DARK_FILE)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(old, ensure_ascii=False) + "\n")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv",
                               ["m3_dark_report.py", "--lane", "agent",
                                "--memory-system", self.ms,
                                "--coverage", "0"]), \
                redirect_stdout(buf):
            report.main()
        self.assertIn("agent dark rows: 0", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
