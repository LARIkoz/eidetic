"""Regression tests for wedged compute, contained children and durable queues."""
import contextlib
import fcntl
import importlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import bounded_worker
import index_impl as idx
import resource_budget as budget
import search_impl as search


class BoundedRecallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = mock.patch.dict(os.environ, {
            "EIDETIC_RESOURCE_ROOT": str(self.root / "governor"),
            "EIDETIC_RESOURCE_WAIT_SECONDS": "0.05",
            "EIDETIC_CONFIDENCE_EVENTS": "1",
            "EIDETIC_M2_SYNTHESIS": "1",
            "EIDETIC_PRODUCER": "0",
            "EIDETIC_USAGE_LOG": "off",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        importlib.reload(budget)
        self.addCleanup(importlib.reload, budget)
        (self.root / "docs").mkdir()
        (self.root / ".eidetic-base.json").write_text(json.dumps({"corpus_dirs": ["docs"]}))
        self.db = str(self.root / "db/index.db")
        self.card = str(self.root / "docs/handoff.md")
        Path(self.card).write_text("---\nname: handoff releasewatch\ntype: project\nsource: user-explicit\n---\n\nReleasewatch handoff ready.\n")
        self.conn = idx.init_db(self.db)
        self.addCleanup(self.conn.close)
        idx.run_incremental(self.conn, [self.card], defer_semantics=True)

    def lock(self):
        root = budget._root()
        root.mkdir(exist_ok=True)
        handle = (root / ".resource-budget.lock").open("a")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def test_compute_contention_is_bounded_and_admits_no_work(self):
        self.lock()
        started = budget.monotonic()
        with self.assertRaises(budget.ResourceBudgetBusy):
            with budget.compute_slot():
                self.fail("contended model work admitted")
        self.assertLess(budget.monotonic() - started, 0.5)

    def test_checkpoint_preserves_charge_without_waiting_and_consumes_once(self):
        handle = self.lock()
        with self.assertRaises(budget.ResourceBudgetBusy):
            budget.cpu_checkpoint(force=True)
        ledger = budget._root() / ".resource-budget-deferred.jsonl"
        self.assertTrue(ledger.read_text().strip())
        handle.close()
        with mock.patch.object(budget, "_sleep_until"):
            budget.cpu_checkpoint(force=True)
        state = json.loads((budget._root() / ".resource-budget.state.json").read_text())
        self.assertEqual(state["deferred_offset"], ledger.stat().st_size)
        with mock.patch.object(budget, "_sleep_until"):
            budget.cpu_checkpoint(force=True)
        following = json.loads((budget._root() / ".resource-budget.state.json").read_text())
        self.assertEqual(following["deferred_offset"], state["deferred_offset"])

    def test_long_cooldown_is_retained_and_does_not_admit_compute(self):
        root = budget._root()
        root.mkdir(exist_ok=True)
        state = budget._load_state(root)
        state["gpu_deadline"] = budget.monotonic() + 20
        budget._save_state(root, state)
        with self.assertRaises(budget.ResourceBudgetBusy):
            with budget.compute_slot():
                self.fail("cooldown bypassed")
        self.assertGreater(json.loads((root / ".resource-budget.state.json").read_text())["gpu_deadline"], budget.monotonic())

    def test_cpu_checkpoint_does_not_wait_for_gpu_cooldown(self):
        root = budget._root()
        root.mkdir(exist_ok=True)
        state = budget._load_state(root)
        state["gpu_deadline"] = budget.monotonic() + 20
        budget._save_state(root, state)
        started = budget.monotonic()
        budget.cpu_checkpoint(force=True)
        self.assertLess(budget.monotonic() - started, 0.5)

    def test_completed_model_cools_outside_shared_lock_without_losing_result(self):
        def inspect_cooldown(deadline):
            if deadline <= budget.monotonic():
                return
            with (budget._root() / ".resource-budget.lock").open("a") as probe:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mock.patch.object(budget, "_sleep_until", side_effect=inspect_cooldown):
            with budget.compute_slot("gpu"):
                time.sleep(0.02)
        state = json.loads((budget._root() / ".resource-budget.state.json").read_text())
        self.assertGreater(state["gpu_deadline"], budget.monotonic())

    def test_swallowed_budget_deferral_never_acknowledges_semantic_queue(self):
        handle = self.lock()
        def swallowed(*_):
            try:
                with budget.compute_slot():
                    pass
            except budget.ResourceBudgetBusy:
                pass
        with mock.patch.dict(sys.modules, {
                "m1_contradiction": types.SimpleNamespace(run_on_ingest=swallowed),
                "m2_synthesis": types.SimpleNamespace(run_on_ingest=lambda *_: None)}):
            idx.maintain_pending(self.conn, idx.pending_ingest_paths(self.conn), True)
        self.assertEqual(idx.pending_ingest_paths(self.conn), [self.card])
        handle.close()

    def test_wait_timeout_reaps_child_without_inventing_gpu_work(self):
        result = bounded_worker.run([sys.executable, "-c", "import time;time.sleep(30)"], timeout=0.1)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        rows = (budget._root() / ".resource-budget-deferred.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(rows[-1])["gpu_elapsed"], 0)

    def test_timeout_kills_inherited_pipe_descendant(self):
        pidfile = self.root / "child.pid"
        code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);open(sys.argv[1],'w').write(str(p.pid));time.sleep(30)"
        result = bounded_worker.run([sys.executable, "-c", code, str(pidfile)], timeout=2)
        self.assertTrue(result.timed_out)
        child = int(pidfile.read_text())
        status = subprocess.run(["/bin/ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True)
        self.assertTrue(not status.stdout.strip() or status.stdout.strip().startswith("Z"), status.stdout)

    def test_completed_gpu_result_returns_before_debt_but_next_admission_is_blocked(self):
        script = """import resource_budget as b, time, json
with b.compute_slot('gpu'):
    time.sleep(.1)
print('READY', flush=True)
try:
    with b.compute_slot('gpu'):
        raise AssertionError('debt was bypassed')
except b.ResourceBudgetBusy:
    print('DEBT_PRESERVED')
"""
        result = bounded_worker.run([sys.executable, "-c", script], timeout=1,
                                    env=dict(os.environ, PYTHONPATH=str(BIN)))
        self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY", result.stdout)
        self.assertIn("DEBT_PRESERVED", result.stdout)

    def test_worker_deadline_survives_supervisor_sigkill(self):
        pidfile = self.root / "orphan.pid"
        worker = "import os,sys,time,signal,resource_budget as b\nwith b.compute_slot('gpu'):\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n open(sys.argv[1],'w').write(str(os.getpid()))\n time.sleep(30)"
        supervisor = "import bounded_worker,sys;bounded_worker.run([sys.executable,'-c',sys.argv[1],sys.argv[2]],timeout=1)"
        env = dict(os.environ, PYTHONPATH=str(BIN))
        proc = subprocess.Popen([sys.executable, "-c", supervisor, worker, str(pidfile)], env=env)
        try:
            until = budget.monotonic() + 3
            while not pidfile.exists() and budget.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            proc.kill()
            proc.wait(timeout=2)
            until = budget.monotonic() + 4
            while budget.monotonic() < until:
                status = subprocess.run(["/bin/ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True)
                if not status.stdout.strip() or status.stdout.strip().startswith("Z"):
                    break
                time.sleep(0.05)
            else:
                os.kill(child, signal.SIGKILL)
                self.fail("Worker survived independent deadline")
            ledger = budget._root() / ".resource-budget-deferred.jsonl"
            until = budget.monotonic() + 2
            while not ledger.exists() and budget.monotonic() < until:
                time.sleep(.01)
            self.assertTrue(ledger.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)

    def test_guard_cleans_descendant_after_worker_exit_and_supervisor_sigkill(self):
        pidfile = self.root / "descendant.pid"
        gate = self.root / "exit-now"
        worker = """import subprocess,sys,time,pathlib
child = subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)
"""
        supervisor = "import bounded_worker,sys;bounded_worker.run([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]],timeout=10)"
        proc = subprocess.Popen([sys.executable, "-c", supervisor, worker, str(pidfile), str(gate)],
                                env=dict(os.environ, PYTHONPATH=str(BIN)))
        child = None
        try:
            until = budget.monotonic() + 3
            while not pidfile.exists() and budget.monotonic() < until:
                time.sleep(.01)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            proc.kill()
            proc.wait(timeout=2)
            gate.touch()
            until = budget.monotonic() + 2
            while budget.monotonic() < until:
                status = subprocess.run(["/bin/ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True)
                if not status.stdout.strip() or status.stdout.strip().startswith("Z"):
                    return
                time.sleep(.05)
            self.fail("Descendant outlived normal worker exit after supervisor death")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_short_journal_write_preserves_evidence_and_cannot_poison_later_charges(self):
        root = budget._root()
        root.mkdir(exist_ok=True)
        journal = root / ".resource-budget-deferred.jsonl"
        journal.write_bytes(b'{"boot_id":')
        budget.defer_charge(0.01, budget.monotonic())
        raw = journal.read_bytes()
        with mock.patch.object(budget, "_admission_wait"):
            budget.cpu_checkpoint(force=True)
        state = json.loads((root / ".resource-budget.state.json").read_text())
        self.assertEqual(state["deferred_offset"], len(raw))
        self.assertEqual(journal.read_bytes(), raw)
        self.assertGreater(state["gpu_deadline"], budget.monotonic())
        deadline = state["gpu_deadline"]
        with mock.patch.object(budget, "_admission_wait"):
            budget.cpu_checkpoint(force=True)
        following = json.loads((root / ".resource-budget.state.json").read_text())
        self.assertEqual(following["gpu_deadline"], deadline)

    def test_duplicate_recovery_receipts_charge_only_the_larger_observation(self):
        started = budget.monotonic()
        budget.defer_charge(.1, started, gpu_elapsed=.1, charge_id="same-worker")
        budget.defer_charge(.15, started, gpu_elapsed=.15, charge_id="same-worker")
        with mock.patch.object(budget, "_admission_wait"):
            budget.cpu_checkpoint(force=True)
        state = json.loads((budget._root() / ".resource-budget.state.json").read_text())
        self.assertAlmostEqual(state["gpu_deadline"], started + .15 * 5, places=5)

    def test_lexical_fallback_preserves_confidence_and_reports_degradation(self):
        with mock.patch.object(bounded_worker, "run", return_value=bounded_worker.Result(-9, "", "", True)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                search.search(self.db, "releasewatch", json_object=True)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["no_confident_results"])
        self.assertEqual(payload["retrieval_mode"], "lexical")
        self.assertIn("timed out", payload["degraded_reason"])

    def test_missing_lexical_match_does_not_invent_confidence(self):
        with mock.patch.object(bounded_worker, "run", return_value=bounded_worker.Result(-9, "", "", True)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                search.search(self.db, "nonexistentzebra", json_object=True)
        self.assertTrue(json.loads(output.getvalue())["no_confident_results"])

    def test_real_search_cli_exits_with_json_while_governor_is_held(self):
        self.lock()
        env = dict(os.environ, EIDETIC_SEARCH_TIMEOUT="0.2")
        result = subprocess.run([sys.executable, str(BIN / "search_impl.py"), self.db,
                                 "releasewatch", "--json-object"], env=env,
                                capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["no_confident_results"])

    def test_fts_cli_commits_before_separate_semantic_process_and_preserves_queue(self):
        observed = []
        def worker(cmd, **kwargs):
            # A separate process is dispatched only after the index lock is free.
            with idx.index_lock(self.db, timeout=0):
                c = sqlite3.connect(self.db)
                observed.append(c.execute("select count(*) from memory_chunks").fetchone()[0])
                c.close()
            self.assertIn("--semantic-only", cmd)
            return bounded_worker.Result(-9, "", "", True)
        with mock.patch.object(sys, "argv", ["index_impl.py", "--incremental", self.db]), \
                mock.patch.object(bounded_worker, "run", side_effect=worker):
            self.assertEqual(idx.main(), 0)
        self.assertEqual(observed, [1])
        self.assertEqual(idx.pending_ingest_paths(self.conn), [self.card])

    def test_lexical_only_commits_and_queues_without_launching_models(self):
        self.conn.close()
        with mock.patch.object(sys, "argv", ["index_impl.py", "--lexical-only", self.db]), \
                mock.patch.object(bounded_worker, "run") as worker, \
                mock.patch.object(idx, "cpu_checkpoint"), \
                mock.patch.object(idx, "apply_background_policy"):
            self.assertEqual(idx.main(), 0)
        worker.assert_not_called()
        conn = sqlite3.connect(self.db)
        try:
            self.assertTrue(idx.pending_ingest_paths(conn))
            self.assertGreater(conn.execute("SELECT count(*) FROM memory_chunks").fetchone()[0], 0)
        finally:
            conn.close()

    def test_disabled_hooks_leave_queue_and_do_not_launch_worker(self):
        with mock.patch.dict(os.environ, {"EIDETIC_CONFIDENCE_EVENTS": "0"}), \
                mock.patch.object(sys, "argv", ["index_impl.py", "--incremental", self.db]), \
                mock.patch.object(bounded_worker, "run") as run:
            self.assertEqual(idx.main(), 0)
            run.assert_not_called()
        self.assertEqual(idx.pending_ingest_paths(self.conn), [self.card])


if __name__ == "__main__":
    unittest.main()
