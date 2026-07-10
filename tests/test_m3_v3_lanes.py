"""M3 v3 — two lanes (spec-m3-miner-v3: FR-2 kind-aware accept, FR-3 dark
acquisition, FR-6 door fallback, FR-7 slugify translit, FR-8 seen-cache).

Hermetic, leg-agnostic (no model, no SDK): the miner's SDK is a fake module
injected into sys.modules; the judge is monkeypatched per test. The live
~/.claude store is NEVER touched — HOME and EIDETIC_MEMORY_SYSTEM point at
temp dirs wherever a path could escape.

Safety ACs baked in:
  * FR-2/B1: a valid acquisition candidate SURVIVES mine_transcript — the
    silent-drop class that made M3 inert on 07-08 stays RED on regression.
  * FR-3: quote-absent → reject with ZERO judge calls; judge_unavailable is
    NEVER would_file; a candidate crash never kills the run.
  * FR-8: the second identical hook run burns 0 judge calls (skipped_seen),
    transients are retried.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import m3_acquisition as acq  # noqa: E402
import m3_autofile as m3  # noqa: E402
import m3_hook  # noqa: E402
import m3_judge  # noqa: E402
import m3_recall_miner as miner  # noqa: E402
import m3_seen_cache as cache  # noqa: E402
import remember  # noqa: E402


def _write_transcript(path, turns):
    """turns = [(role, text)] → Claude-Code-shaped JSONL."""
    with open(path, "w", encoding="utf-8") as f:
        for role, text in turns:
            rec = {"type": role,
                   "message": {"content": [{"type": "text", "text": text}]}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


ASSISTANT_TEXT = ("We decided to route all pipeline jobs through the Redis queue "
                  "because cron polling was too slow for the ingest workers.")
USER_TEXT = ("Here is a pasted log line: the scheduler decided to use the "
             "Postgres queue for pipeline jobs since forever and ever.")

VALID_ACQ = {"kind": "decision",
             "claim": ("Pipeline jobs are routed through the Redis queue; cron "
                       "polling was too slow for the ingest workers."),
             "transcript_quote": ("decided to route all pipeline jobs through the "
                                  "Redis queue because cron polling was too slow")}
VALID_RECALL = {"kind": "recall",
                "recall_query": "how are pipeline jobs scheduled",
                "recalled_answer": ("Pipeline jobs run through the Redis queue; "
                                    "workers consume directly from it and cron "
                                    "polling was retired last quarter for speed.")}


class _FakeSDK:
    def __init__(self, content):
        self.content = content

    def chat(self, **kw):
        return {"response_shape": {"ok": True}, "content": self.content}


class MinerAcceptTest(unittest.TestCase):
    """FR-2 accept contract — kind-aware, per-kind counters, in-run dedup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3v3-")
        self.transcript = os.path.join(self.tmp, "sess-1.jsonl")
        _write_transcript(self.transcript,
                          [("user", USER_TEXT), ("assistant", ASSISTANT_TEXT)])
        self._saved_sdk = sys.modules.get("shared_api_cache")

    def tearDown(self):
        if self._saved_sdk is None:
            sys.modules.pop("shared_api_cache", None)
        else:
            sys.modules["shared_api_cache"] = self._saved_sdk
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mine_with(self, candidates):
        mod = types.ModuleType("shared_api_cache")
        content = json.dumps({"candidates": candidates}, ensure_ascii=False)
        mod.get_sdk = lambda: _FakeSDK(content)
        sys.modules["shared_api_cache"] = mod
        return miner.mine_transcript(self.transcript)

    def test_valid_acquisition_candidate_survives(self):
        """B1: the accept path must NOT silently drop acquisition kinds."""
        out, meta = self._mine_with([VALID_ACQ])
        self.assertEqual(len(out), 1, meta)
        self.assertEqual(out[0]["kind"], "decision")
        self.assertEqual(out[0]["claim"], VALID_ACQ["claim"])
        self.assertEqual(meta["raw_by_kind"], {"decision": 1})
        self.assertEqual(meta["kept_by_kind"], {"decision": 1})

    def test_recall_and_kindless_v2_shape_accepted(self):
        kindless = {k: v for k, v in VALID_RECALL.items() if k != "kind"}
        out, meta = self._mine_with([kindless])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "recall")
        self.assertEqual(meta["kept_by_kind"], {"recall": 1})

    def test_unknown_kind_dropped_and_counted(self):
        out, meta = self._mine_with(
            [dict(VALID_ACQ, kind="prophecy"), VALID_RECALL])
        self.assertEqual([c["kind"] for c in out], ["recall"])
        self.assertEqual(meta["dropped_unknown_kind"], 1)
        self.assertEqual(meta["raw_by_kind"].get("unknown"), 1)

    def test_short_quote_or_claim_dropped_at_accept(self):
        short_quote = dict(VALID_ACQ, transcript_quote="Redis queue wins forever")
        short_claim = dict(VALID_ACQ, claim="Redis queue.")
        out, meta = self._mine_with([short_quote, short_claim])
        self.assertEqual(out, [])
        # raw counted, kept zero — an accept-drop is visible, not a fake zero.
        self.assertEqual(meta["raw_by_kind"], {"decision": 2})
        self.assertEqual(meta["kept_by_kind"], {})

    def test_in_run_dedup_is_kind_aware(self):
        out, _ = self._mine_with([VALID_ACQ, dict(VALID_ACQ), VALID_RECALL])
        self.assertEqual(len(out), 2)  # dup acquisition collapsed, recall kept


class AcquisitionLaneTest(unittest.TestCase):
    """FR-3 — quote gate → judge → dark log; zero store writes."""

    TURNS = [("user", USER_TEXT), ("assistant", ASSISTANT_TEXT)]

    def setUp(self):
        self.ms = tempfile.mkdtemp(prefix="m3v3-ms-")

    def tearDown(self):
        shutil.rmtree(self.ms, ignore_errors=True)

    def _dark_rows(self):
        p = os.path.join(self.ms, "events", acq.DARK_FILE)
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _cand(self, **over):
        c = dict(VALID_ACQ, session_id="sess-1", project_slug="proj")
        c.update(over)
        return c

    def test_entailed_would_file(self):
        with mock.patch.object(m3_judge, "verdict", return_value="entailed"):
            tally, outcomes = acq.process(None, [self._cand()],
                                          memory_system=self.ms, turns=self.TURNS)
        self.assertEqual(outcomes, ["would_file"])
        rows = self._dark_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["would_file"])
        self.assertTrue(rows[0]["quote_ok"])
        self.assertEqual(rows[0]["judge"], "entailed")

    def test_quote_absent_rejects_with_zero_judge_calls(self):
        called = []
        with mock.patch.object(m3_judge, "verdict",
                               side_effect=lambda *a: called.append(1) or "entailed"):
            tally, outcomes = acq.process(
                None, [self._cand(transcript_quote="this text never appears in "
                                                   "any assistant turn at all")],
                memory_system=self.ms, turns=self.TURNS)
        self.assertEqual(outcomes, ["would_reject"])
        self.assertEqual(called, [])  # ZERO judge calls burned
        row = self._dark_rows()[0]
        self.assertFalse(row["quote_ok"])
        self.assertIsNone(row["judge"])
        self.assertFalse(row["would_file"])

    def test_user_turn_quote_is_not_quotable(self):
        """Owner gate Q1: user turns (pasted logs) are never a quote source."""
        with mock.patch.object(m3_judge, "verdict", return_value="entailed"):
            _, outcomes = acq.process(
                None, [self._cand(transcript_quote="the scheduler decided to use "
                                                   "the Postgres queue for pipeline jobs")],
                memory_system=self.ms, turns=self.TURNS)
        self.assertEqual(outcomes, ["would_reject"])

    def test_entailment_fail_rejects(self):
        with mock.patch.object(m3_judge, "verdict", return_value="not_entailed"):
            _, outcomes = acq.process(None, [self._cand()],
                                      memory_system=self.ms, turns=self.TURNS)
        self.assertEqual(outcomes, ["would_reject"])
        self.assertFalse(self._dark_rows()[0]["would_file"])

    def test_judge_unavailable_never_would_file(self):
        with mock.patch.object(m3_judge, "verdict",
                               return_value="judge_unavailable"):
            _, outcomes = acq.process(None, [self._cand()],
                                      memory_system=self.ms, turns=self.TURNS)
        self.assertEqual(outcomes, ["judge_unavailable"])
        row = self._dark_rows()[0]
        self.assertEqual(row["judge"], "judge_unavailable")
        self.assertFalse(row["would_file"])

    def test_one_candidate_crash_never_kills_the_run(self):
        boom = mock.Mock(side_effect=[RuntimeError("boom"), "entailed"])
        with mock.patch.object(m3_judge, "verdict", boom):
            tally, outcomes = acq.process(
                None, [self._cand(), self._cand(claim=VALID_ACQ["claim"] + " v2")],
                memory_system=self.ms, turns=self.TURNS)
        self.assertEqual(outcomes, ["error", "would_file"])
        self.assertEqual(tally.get("would_file"), 1)

    def test_writes_only_under_events(self):
        with mock.patch.object(m3_judge, "verdict", return_value="entailed"):
            acq.process(None, [self._cand()], memory_system=self.ms,
                        turns=self.TURNS)
        entries = sorted(os.listdir(self.ms))
        self.assertEqual(entries, ["events"])  # NFR-2: nothing else appears


class SeenCacheTest(unittest.TestCase):
    """FR-8 — definitive outcomes cached; transients retried; one dialect."""

    def setUp(self):
        self.ms = tempfile.mkdtemp(prefix="m3v3-cache-")

    def tearDown(self):
        shutil.rmtree(self.ms, ignore_errors=True)

    def test_definitive_roundtrip_and_transient_ignored(self):
        cand = dict(VALID_RECALL, session_id="s1")
        key = cache.candidate_key(cand)
        self.assertFalse(cache.record(self.ms, "s1", key, "recall", "noop"))
        self.assertFalse(cache.record(self.ms, "s1", key, "recall",
                                      "judge_unavailable"))
        self.assertEqual(cache.load_seen(self.ms, "s1"), set())
        self.assertTrue(cache.record(self.ms, "s1", key, "recall", "filed"))
        self.assertEqual(cache.load_seen(self.ms, "s1"), {key})
        self.assertEqual(cache.load_seen(self.ms, "s2"), set())  # session-scoped

    def test_key_normalization_uses_judge_dialect(self):
        a = cache.candidate_key({"kind": "decision", "claim": "Use Redis, NOW!"})
        b = cache.candidate_key({"kind": "decision", "claim": "use   redis now"})
        self.assertEqual(a, b)
        c = cache.candidate_key({"kind": "finding", "claim": "use redis now"})
        self.assertNotEqual(a, c)  # kind is part of the key


class HookRoutingTest(unittest.TestCase):
    """FR-3 kind-routing + FR-8 at the hook layer: second run burns 0 judge
    calls and reports skipped_seen; acquisition never enters drive()."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3v3-hook-")
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        self.ms = os.path.join(self.tmp, "ms")
        os.makedirs(os.path.join(self.ms, "db"))
        with open(os.path.join(self.ms, "db", "index.db"), "w"):
            pass
        os.environ["EIDETIC_MEMORY_SYSTEM"] = self.ms
        os.environ["EIDETIC_M3_DRIVER"] = "on"
        slug = "proj-x"
        os.makedirs(os.path.join(self.tmp, ".claude", "projects", slug, "memory"))
        tdir = os.path.join(self.tmp, "transcripts", slug)
        os.makedirs(tdir)
        self.transcript = os.path.join(tdir, "sess-42.jsonl")
        _write_transcript(self.transcript,
                          [("user", USER_TEXT), ("assistant", ASSISTANT_TEXT)])
        # The hook derives memory_dir from the transcript's parent dir name:
        os.makedirs(os.path.join(self.tmp, ".claude", "projects",
                                 os.path.basename(tdir), "memory"), exist_ok=True)

    def tearDown(self):
        if self.home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.home
        os.environ.pop("EIDETIC_MEMORY_SYSTEM", None)
        os.environ.pop("EIDETIC_M3_DRIVER", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_hook(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = m3_hook.main(["m3_hook.py", self.transcript])
        self.assertEqual(rc, 0)
        line = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertNotEqual(line.get("m3_driver"), "error", line)
        return line

    def test_two_runs_second_is_all_cache_hits(self):
        cands = [dict(VALID_RECALL, session_id="sess-42", project_slug=""),
                 dict(VALID_ACQ, session_id="sess-42", project_slug="")]
        meta = {"turns": 2, "raw_by_kind": {"recall": 1, "decision": 1},
                "kept_by_kind": {"recall": 1, "decision": 1}}
        drive_calls = []

        def fake_drive(idb, recall_cands, memory_dir=None):
            drive_calls.append(list(recall_cands))
            return [{"action": "filed"} for _ in recall_cands], True

        def fake_process(transcript, acq_cands, memory_system=None):
            return {"would_file": len(acq_cands)}, ["would_file"] * len(acq_cands)

        with mock.patch.object(m3_hook, "main", m3_hook.main), \
                mock.patch.object(sys.modules["m3_recall_miner"], "mine_transcript",
                                  return_value=(cands, dict(meta))), \
                mock.patch.object(sys.modules["m3_producer_driver"], "drive",
                                  side_effect=fake_drive), \
                mock.patch.object(sys.modules["m3_acquisition"], "process",
                                  side_effect=fake_process):
            first = self._run_hook()
            second = self._run_hook()

        self.assertEqual(first["tally"], {"filed": 1})
        self.assertEqual(first["acq"], {"would_file": 1})
        self.assertEqual(first["meta"]["skipped_seen"], 0)
        self.assertEqual(len(drive_calls), 1)  # acquisition never entered drive
        self.assertEqual([c["kind"] for c in drive_calls[0]], ["recall"])

        self.assertEqual(second["meta"]["skipped_seen"], 2)
        self.assertEqual(len(drive_calls), 1)  # second run: 0 judge/drive calls
        self.assertNotIn("tally", second)

    def test_transient_outcome_is_retried_next_run(self):
        cands = [dict(VALID_ACQ, session_id="sess-42", project_slug="")]
        meta = {"turns": 2}
        process_calls = []

        def transient_then_definitive(transcript, acq_cands, memory_system=None):
            process_calls.append(1)
            out = "judge_unavailable" if len(process_calls) == 1 else "would_reject"
            return {out: 1}, [out]

        with mock.patch.object(sys.modules["m3_recall_miner"], "mine_transcript",
                               return_value=(cands, dict(meta))), \
                mock.patch.object(sys.modules["m3_acquisition"], "process",
                                  side_effect=transient_then_definitive):
            self._run_hook()
            second = self._run_hook()
            third = self._run_hook()

        self.assertEqual(len(process_calls), 2)  # retried once, then cached
        self.assertEqual(third["meta"]["skipped_seen"], 1)


class SlugifyTranslitTest(unittest.TestCase):
    """FR-7 — RU→Latin before the ASCII strip; ASCII byte-identical."""

    def test_live_dup_query_readable(self):
        self.assertEqual(remember.slugify("какие треки сейчас открыты"),
                         "kakie-treki-sejchas-otkryty")

    def test_no_hash_or_bare_digit_for_cyrillic(self):
        for q in ("какие треки остались открытыми", "правило трёх ревью",
                  "3 трека открыты"):
            s = remember.slugify(q)
            self.assertFalse(s.startswith("note-"), (q, s))
            self.assertFalse(s.isdigit(), (q, s))

    def test_ascii_byte_identical_to_old_behavior(self):
        import hashlib
        import re as _re

        def old_slugify(text, maxlen=70):
            s = _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
            s = s[:maxlen].rstrip("-")
            if not s:
                return "note-" + hashlib.sha1(
                    (text or "").encode("utf-8")).hexdigest()[:10]
            return s

        for t in ("Plain ASCII Title!", "a-b_c 42", "  ", "", "UPPER lower 9",
                  "x" * 200):
            self.assertEqual(remember.slugify(t), old_slugify(t), t)

    def test_unslugifiable_still_hash_falls_back_distinctly(self):
        a, b = remember.slugify("世界"), remember.slugify("平和")
        self.assertTrue(a.startswith("note-") and b.startswith("note-"))
        self.assertNotEqual(a, b)

    def test_target_slug_prefixes_translit(self):
        self.assertEqual(remember.target_slug("какие треки сейчас открыты",
                                              "synthesis"),
                         "synthesis-kakie-treki-sejchas-otkryty")


class DoorFallbackTest(unittest.TestCase):
    """FR-6 — [] from the in-process door + vectors.db on disk → subprocess
    re-probe; no vectors.db → no subprocess."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m3v3-door-")
        self.idb = os.path.join(self.tmp, "db", "index.db")
        os.makedirs(os.path.dirname(self.idb))
        with open(self.idb, "w"):
            pass
        m3._door_subprocess_dead = False

    def tearDown(self):
        m3._door_subprocess_dead = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch_vectors(self):
        with open(self.idb.replace("index.db", "vectors.db"), "w"):
            pass

    def test_blind_door_with_vectors_reprobes_via_subprocess(self):
        self._touch_vectors()
        hits = [{"path": "/mem/synthesis-x.md", "score": 0.91}]
        fake = mock.Mock(return_value=mock.Mock(
            returncode=0, stdout=json.dumps(hits), stderr=""))
        with mock.patch.object(m3._M1, "neighbors_via_door", return_value=[]), \
                mock.patch("subprocess.run", fake):
            got = m3._default_neighbors(self.idb, "probe text")
        self.assertEqual(got, hits)
        self.assertTrue(fake.called)

    def test_no_vectors_db_means_no_subprocess(self):
        fake = mock.Mock()
        with mock.patch.object(m3._M1, "neighbors_via_door", return_value=[]), \
                mock.patch("subprocess.run", fake):
            got = m3._default_neighbors(self.idb, "probe text")
        self.assertEqual(got, [])
        self.assertFalse(fake.called)

    def test_in_process_hits_short_circuit(self):
        hits = [{"path": "/mem/a.md", "score": 0.99}]
        fake = mock.Mock()
        with mock.patch.object(m3._M1, "neighbors_via_door", return_value=hits), \
                mock.patch("subprocess.run", fake):
            got = m3._default_neighbors(self.idb, "probe text")
        self.assertEqual(got, hits)
        self.assertFalse(fake.called)

    def test_dead_subprocess_marked_once(self):
        self._touch_vectors()
        fake = mock.Mock(side_effect=OSError("no venv"))
        with mock.patch.object(m3._M1, "neighbors_via_door", return_value=[]), \
                mock.patch("subprocess.run", fake):
            self.assertEqual(m3._default_neighbors(self.idb, "p1"), [])
            self.assertEqual(m3._default_neighbors(self.idb, "p2"), [])
        self.assertEqual(fake.call_count, 1)  # dead-marked after first failure


class JudgeVerdictMappingTest(unittest.TestCase):
    """verdict() granularity + score() folding (byte-identical decisions)."""

    SPANS = ["the retry budget was lowered from five to three after the review"]

    def _with_sdk(self, response=None, raise_exc=None):
        fake_sdk = mock.Mock()
        if raise_exc is not None:
            fake_sdk.chat_for_route.side_effect = raise_exc
        else:
            fake_sdk.chat_for_route.return_value = response
        return mock.patch.object(m3_judge, "_get_sdk", return_value=fake_sdk)

    def test_entailed_with_quote(self):
        resp = {"response_shape": {"ok": True},
                "content": json.dumps({
                    "entailed": True,
                    "quote": "retry budget was lowered from five to three"})}
        with self._with_sdk(resp):
            self.assertEqual(m3_judge.verdict("claim", self.SPANS), "entailed")
            self.assertEqual(m3_judge.score("claim", self.SPANS), 1.0)

    def test_entailed_but_bad_quote_is_not_entailed(self):
        resp = {"response_shape": {"ok": True},
                "content": json.dumps({
                    "entailed": True,
                    "quote": "words that appear in no span whatsoever here"})}
        with self._with_sdk(resp):
            self.assertEqual(m3_judge.verdict("claim", self.SPANS), "not_entailed")
            self.assertEqual(m3_judge.score("claim", self.SPANS), 0.0)

    def test_provider_error_is_unavailable(self):
        resp = {"response_shape": {"ok": False, "provider_error_class": "x"}}
        with self._with_sdk(resp):
            self.assertEqual(m3_judge.verdict("claim", self.SPANS),
                             "judge_unavailable")
            self.assertEqual(m3_judge.score("claim", self.SPANS), 0.0)

    def test_route_exception_is_unavailable(self):
        with self._with_sdk(raise_exc=RuntimeError("dead pool")):
            self.assertEqual(m3_judge.verdict("claim", self.SPANS),
                             "judge_unavailable")
            self.assertEqual(m3_judge.score("claim", self.SPANS), 0.0)

    def test_garbage_content_is_error(self):
        resp = {"response_shape": {"ok": True}, "content": "not json at all"}
        with self._with_sdk(resp):
            self.assertEqual(m3_judge.verdict("claim", self.SPANS), "error")
            self.assertEqual(m3_judge.score("claim", self.SPANS), 0.0)

    def test_empty_spans_definitive_reject(self):
        self.assertEqual(m3_judge.verdict("claim", []), "not_entailed")


if __name__ == "__main__":
    unittest.main()
