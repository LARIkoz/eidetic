"""M3 commission — machine marking for the D5 gates (brief-m3-commission-gate).

Hermetic: NO cli-council import, NO subprocess — tests patch the ONE seam
`m3_commission._invoke_voice` and pass judge dicts whose values are never
touched. The live ~/.claude store is never used (EIDETIC_MEMORY_SYSTEM →
temp dir).

Safety ACs baked in:
  * evidenced-dangerous rule: ONE dangerous verdict WITH a checkable ref
    marks the item dangerous_wrong even against 2 keeps; WITHOUT a ref it
    downgrades to a noise vote (one flaky judge cannot kill a round).
  * quorum: <2 definitive votes = unresolved — loud and gate-EXCLUDED,
    never silently kept or dropped.
  * resume: an (item, judge) pair with an ok row is never re-asked.
  * gate math = D4 unchanged (≥70% keep AND dangerous ≤1 → activate;
    <50% → kill; between → iterate).
  * zero writes outside events/.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import m3_agent_lane as lane  # noqa: E402
import m3_commission as comm  # noqa: E402
import m3_dark_report as report  # noqa: E402

JUDGES = {"gemini31": object(), "grok45": object(), "codex53": object()}


def _item(key="k1", claim=None):
    return {"key": key, "lane": "main", "kind": "finding",
            "claim": claim or ("The ingest retry queue drops records when "
                               "the backlog exceeds five thousand entries"),
            "quote": "retry queue drops records when the backlog exceeds",
            "rewordings": [], "project_slug": "-proj-alpha",
            "session_id": "sid-1", "agent_kind": None, "workflow_id": None,
            "transcript_mtime": "2026-07-12T09:00:00.000Z",
            "seen_main": False}


def _verdict(verdict, ref="none", note="", reason="r"):
    return json.dumps({"verdict": verdict, "confidence": 0.9,
                       "evidence_ref": ref, "evidence_note": note,
                       "reason": reason})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3comm-")
        self.ms = os.path.join(self.tmp, "memory-system")
        os.makedirs(os.path.join(self.ms, "events"), exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, items, answers):
        """answers: judge → (ok, text) or callable(item_key) → (ok, text)."""
        def fake(name, prov, prompt, timeout):
            a = answers[name]
            if callable(a):
                for it in items:
                    if it["claim"] in prompt:
                        return a(it["key"])
                return a(None)
            return a
        with mock.patch.object(comm, "_invoke_voice", side_effect=fake):
            return comm.judge_items(items, self.ms, judges=JUDGES,
                                    lane="main")

    def _outcomes(self, items):
        return comm.resolve_items(items, self.ms, lane="main")


class QuorumTest(_Base):
    def test_two_keeps_one_noise_is_keep(self):
        items = [_item()]
        self._run(items, {"gemini31": (True, _verdict("keep")),
                          "grok45": (True, _verdict("keep")),
                          "codex53": (True, _verdict("noise"))})
        out = self._outcomes(items)
        self.assertEqual(out[0]["outcome"], "keep")

    def test_one_keep_two_noise_is_noise(self):
        items = [_item()]
        self._run(items, {"gemini31": (True, _verdict("keep")),
                          "grok45": (True, _verdict("noise")),
                          "codex53": (True, _verdict("noise"))})
        self.assertEqual(self._outcomes(items)[0]["outcome"], "noise")

    def test_evidenced_dangerous_overrides_two_keeps(self):
        items = [_item()]
        self._run(items, {
            "gemini31": (True, _verdict("keep")),
            "grok45": (True, _verdict("keep")),
            "codex53": (True, _verdict("dangerous_wrong",
                                       ref="bin/m3_hook.py:42",
                                       note="config contradicts claim"))})
        o = self._outcomes(items)[0]
        self.assertEqual(o["outcome"], "dangerous_wrong")
        self.assertEqual(len(o["evidenced_dangerous"]), 1)

    def test_unevidenced_dangerous_downgrades_to_noise_vote(self):
        """dangerous without a checkable ref = a noise vote — with two keeps
        the item stays keep (one flaky judge cannot kill a round)."""
        items = [_item()]
        self._run(items, {"gemini31": (True, _verdict("keep")),
                          "grok45": (True, _verdict("keep")),
                          "codex53": (True, _verdict("dangerous_wrong",
                                                     ref="none"))})
        o = self._outcomes(items)[0]
        self.assertEqual(o["outcome"], "keep")
        self.assertEqual(o["evidenced_dangerous"], [])

    def test_below_quorum_is_unresolved_and_gate_excluded(self):
        items = [_item()]
        self._run(items, {"gemini31": (True, _verdict("keep")),
                          "grok45": (False, "grok: timeout after 600s"),
                          "codex53": (False, "codex53: exit 1: boom")})
        out = self._outcomes(items)
        self.assertEqual(out[0]["outcome"], "unresolved")
        gate = comm.gate_math(out)
        self.assertEqual(gate["resolved"], 0)
        self.assertEqual(gate["unresolved"], 1)
        self.assertEqual(gate["verdict"], "no_data")


class ResumeTest(_Base):
    def test_ok_pairs_never_reasked_transients_retried(self):
        items = [_item()]
        c1 = self._run(items, {"gemini31": (True, _verdict("keep")),
                               "grok45": (False, "grok: timeout"),
                               "codex53": (True, _verdict("keep"))})
        self.assertEqual(c1["ok"], 2)
        self.assertEqual(c1["voice_error"], 1)
        calls = []

        def fake(name, prov, prompt, timeout):
            calls.append(name)
            return True, _verdict("noise")
        with mock.patch.object(comm, "_invoke_voice", side_effect=fake):
            c2 = comm.judge_items(items, self.ms, judges=JUDGES, lane="main")
        self.assertEqual(calls, ["grok45"])  # only the transient re-asked
        self.assertEqual(c2["skipped_done"], 2)
        o = self._outcomes(items)[0]
        self.assertEqual(o["outcome"], "keep")  # 2 keep + 1 noise

    def test_parse_fail_is_transient(self):
        items = [_item()]
        c = self._run(items, {"gemini31": (True, "utter prose, no json"),
                              "grok45": (True, _verdict("keep")),
                              "codex53": (True, _verdict("keep"))})
        self.assertEqual(c["parse_fail"], 1)
        done = comm.load_done(self.ms, "main")
        self.assertNotIn(("k1", "gemini31"), done)  # will be re-asked
        self.assertEqual(self._outcomes(items)[0]["outcome"], "keep")


class GateMathTest(_Base):
    def _mk(self, keep, noise, dangerous):
        out = []
        for i in range(keep):
            out.append({"item": _item(f"k{i}"), "outcome": "keep",
                        "votes": [], "evidenced_dangerous": []})
        for i in range(noise):
            out.append({"item": _item(f"n{i}"), "outcome": "noise",
                        "votes": [], "evidenced_dangerous": []})
        for i in range(dangerous):
            out.append({"item": _item(f"d{i}"),
                        "outcome": "dangerous_wrong", "votes": [],
                        "evidenced_dangerous": []})
        return out

    def test_activate_kill_iterate_thresholds(self):
        self.assertEqual(comm.gate_math(self._mk(8, 2, 0))["verdict"],
                         "activate")   # 80% keep, 0 dangerous
        self.assertEqual(comm.gate_math(self._mk(7, 2, 1))["verdict"],
                         "activate")   # 70% keep, 1 dangerous = budget edge
        self.assertEqual(comm.gate_math(self._mk(8, 0, 2))["verdict"],
                         "iterate")    # dangerous over budget blocks activate
        self.assertEqual(comm.gate_math(self._mk(4, 6, 0))["verdict"],
                         "kill")       # 40% keep
        self.assertEqual(comm.gate_math(self._mk(6, 4, 0))["verdict"],
                         "iterate")    # 60% keep — between

    def test_summary_renders_and_carries_owner_question(self):
        out = self._mk(3, 1, 1)
        out[-1]["votes"] = [{"judge": "codex53",
                             "verdict": "dangerous_wrong", "reason": "stale"}]
        out[-1]["evidenced_dangerous"] = [
            {"judge": "codex53", "evidence_ref": "cfg.toml:3",
             "evidence_note": "current config says otherwise"}]
        gate = comm.gate_math(out)
        text = comm.render_summary(out, gate, "main")
        self.assertIn("GATE VERDICT", text)
        self.assertIn("cfg.toml:3", text)
        self.assertIn("OWNER:", text)
        self.assertIn("dangerous-wrong: 1", text)


class ZeroWriteTest(_Base):
    def test_only_events_written(self):
        memdir = os.path.join(self.tmp, "memory")
        os.makedirs(memdir)
        page = os.path.join(memdir, "page.md")
        with open(page, "w") as f:
            f.write("orig")
        items = [_item()]
        self._run(items, {"gemini31": (True, _verdict("keep")),
                          "grok45": (True, _verdict("keep")),
                          "codex53": (True, _verdict("keep"))})
        with open(page) as f:
            self.assertEqual(f.read(), "orig")
        ev = os.listdir(os.path.join(self.ms, "events"))
        self.assertIn(comm.VERDICTS_FILE, ev)
        self.assertIn(comm.RAW_FILE, ev)


class CollectTest(_Base):
    """collect_lane_items — the one collection path the commission reads."""

    def _write(self, fname, rows):
        with open(os.path.join(self.ms, "events", fname), "a",
                  encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_main_lane_clusters_to_items(self):
        row = {"ts": "2026-07-12T10:00:00.000Z", "session_id": "sid-9",
               "project_slug": "-p", "kind": "finding",
               "claim": "The parser cache eviction path is disabled in the "
                        "current build of the ingest service",
               "transcript_quote": "cache eviction disabled in this build",
               "quote_ok": True, "judge": "entailed", "would_file": True}
        self._write("m3_acquisition_dark.jsonl", [row, dict(row)])
        items, meta = report.collect_lane_items(self.ms, "main")
        self.assertEqual(meta["items"], 1)  # key-dedup collapsed the dup
        self.assertEqual(items[0]["kind"], "finding")
        self.assertFalse(items[0]["seen_main"])
        self.assertTrue(items[0]["key"])

    def test_agent_lane_state_lost_raises(self):
        with open(os.path.join(self.ms, "events", lane.LEDGER_FILE),
                  "w") as f:
            f.write("garbage first line\n")
        with self.assertRaises(report.LedgerStateLost):
            report.collect_lane_items(self.ms, "agent")


if __name__ == "__main__":
    unittest.main()
