"""Hermetic shared-budget tests: no numerical runtimes, models, or live DBs."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import resource_budget as budget


class ResourceBudgetTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="eidetic-budget-test-")
        self.root = Path(self.directory.name)
        self.environment = mock.patch.dict(os.environ, {"EIDETIC_RESOURCE_ROOT": str(self.root)})
        self.environment.start()
        importlib.reload(budget)

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()
        importlib.reload(budget)

    def config(self, values):
        (self.root / ".resource-budget.json").write_text(json.dumps(values))
        budget._settings_cache = None

    def run_python(self, script, timeout=10):
        env = dict(os.environ, PYTHONPATH=str(BIN))
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                capture_output=True, text=True, timeout=timeout)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_import_is_side_effect_free_and_topic_base_does_not_split_budget(self):
        result = self.run_python("""
import os, json
before = dict(os.environ)
import resource_budget as b
budget = b
os.environ['EIDETIC_MEMORY_SYSTEM'] = '/irrelevant/topic/base'
assert b._root() == __import__('pathlib').Path(os.environ['EIDETIC_RESOURCE_ROOT'])
os.environ.pop('EIDETIC_MEMORY_SYSTEM')
assert before == dict(os.environ)
print(json.dumps(b.settings()))
""")
        self.assertEqual(json.loads(result.stdout)["gpu_percent"], 20)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_default_cpu_normalization_and_threads(self):
        with mock.patch.object(budget, "_cpu_count", return_value=10):
            policy = budget.settings()
            self.assertEqual(policy["threads"], 2)
            state = dict(cpu_deadline=0.0, gpu_deadline=0.0)
            with mock.patch.object(budget, "_save_state"), mock.patch.object(budget, "_sleep_until") as sleep:
                budget._settle(self.root, state, policy, cpu=2, start=100)
            self.assertEqual(state["cpu_deadline"], 101)
            sleep.assert_called_once_with(101)

    def test_invalid_configuration_fails_closed(self):
        cases = [{"enabled": "false"}, {"cpu_percent": 0}, {"gpu_percent": 101},
                 {"cpu_percent": float("nan")}, {"threads": True}, {"batch_size": -1},
                 {"mlx_cache_mb": 0}, {"typo": 20}, []]
        for values in cases:
            with self.subTest(values=values):
                self.config(values)
                with self.assertRaises(budget.ResourceBudgetError):
                    with budget.compute_slot():
                        self.fail("Malformed policy admitted work")
        (self.root / ".resource-budget.json").write_text("{")
        budget._settings_cache = None
        with self.assertRaises(budget.ResourceBudgetError):
            budget.settings()

    def test_disabled_is_noop_even_without_locking(self):
        self.config({"enabled": False})
        before = dict(os.environ)
        with mock.patch.object(budget, "fcntl", None), mock.patch.object(budget.os, "nice") as nice:
            budget.apply_background_policy()
            with budget.compute_slot("gpu"):
                budget.cpu_checkpoint(force=True)
            budget.cpu_checkpoint(force=True)
        nice.assert_not_called()
        self.assertEqual(before, dict(os.environ))
        self.assertFalse((self.root / ".resource-budget.lock").exists())

    def test_lock_failure_does_not_admit_work(self):
        with mock.patch.object(budget, "fcntl", None):
            with self.assertRaises(budget.ResourceBudgetError):
                with budget.compute_slot():
                    self.fail("Missing locking admitted work")

    def test_background_policy_never_raises_existing_thread_count(self):
        self.config({"threads": 2})
        with mock.patch.dict(os.environ, {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "30"}), \
                mock.patch.object(budget.os, "nice", return_value=0) as nice, \
                mock.patch.object(budget.subprocess, "run") as run:
            budget.apply_background_policy()
            budget.apply_background_policy()
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "1")
            self.assertLessEqual(int(os.environ["OPENBLAS_NUM_THREADS"]), 2)
            self.assertEqual(nice.call_args_list, [mock.call(0), mock.call(10)])
            run.assert_not_called()

    def test_background_policy_preserves_already_lower_priority(self):
        with mock.patch.object(budget.os, "nice", return_value=19) as nice, \
                mock.patch.object(budget.subprocess, "run") as run:
            budget.apply_background_policy()
            budget.apply_background_policy()
        nice.assert_called_once_with(0)
        run.assert_not_called()

    def test_interrupted_lock_wait_closes_descriptor(self):
        with mock.patch.object(budget.fcntl, "flock", side_effect=KeyboardInterrupt), \
                mock.patch.object(budget.os, "close", wraps=os.close) as close:
            with self.assertRaises(KeyboardInterrupt):
                with budget.compute_slot():
                    self.fail("Interrupted lock admitted work")
            close.assert_called_once()
        with budget.compute_slot():
            pass

    def test_settings_cache_is_brief_and_returns_independent_values(self):
        first = budget.settings()
        first["cpu_percent"] = 99
        self.assertEqual(budget.settings()["cpu_percent"], 20)
        with mock.patch.object(Path, "open", side_effect=AssertionError("unexpected file access")):
            self.assertEqual(budget.settings()["gpu_percent"], 20)
        budget._settings_cache = (self.root, budget.monotonic() - 2, {})
        self.config({"cpu_percent": 10})
        self.assertEqual(budget.settings()["cpu_percent"], 10)

    def test_reboot_state_recovers_and_nonsensical_current_state_refuses(self):
        path = self.root / ".resource-budget.state.json"
        path.write_text(json.dumps(dict(boot_id="old-boot", cpu_deadline=1e99)))
        with budget.compute_slot():
            pass
        state = json.loads(path.read_text())
        self.assertEqual(state["boot_id"], budget._boot_identity())
        state["cpu_deadline"] = budget.monotonic() + 1e12
        path.write_text(json.dumps(state))
        with self.assertRaises(budget.ResourceBudgetError):
            with budget.compute_slot():
                self.fail("Nonsensical clock admitted work")

    def test_nested_gpu_promotes_cpu_and_exception_releases_stable_lock(self):
        started = budget.monotonic()
        with self.assertRaisesRegex(ValueError, "body failure"):
            with budget.compute_slot("cpu"):
                with budget.compute_slot("gpu"):
                    budget.cpu_checkpoint(force=True)
                    time.sleep(0.02)
                    raise ValueError("body failure")
        self.assertGreaterEqual(budget.monotonic() - started, 0.1)
        inode = (self.root / ".resource-budget.lock").stat().st_ino
        self.run_python("import resource_budget as b\nwith b.compute_slot(): pass")
        self.assertEqual((self.root / ".resource-budget.lock").stat().st_ino, inode)

    def test_model_cpu_is_not_charged_at_following_checkpoint(self):
        self.run_python("""
import time, resource_budget as b
budget = b
b._cores = 1
with b.compute_slot():
    end = time.process_time() + 0.04
    while time.process_time() < end: pass
from unittest import mock
with mock.patch.object(b, "_settle", wraps=b._settle) as settle:
    b.cpu_checkpoint(force=True)
# Compare actual charged CPU, not wall-clock scheduling jitter on CI.
assert settle.call_args.args[3] < 0.02, settle.call_args
""")

    def test_cancellation_returns_promptly_and_next_process_pays_debt(self):
        for cancellation in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(cancellation=cancellation.__name__):
                with self.assertRaises(cancellation):
                    with budget.compute_slot("gpu"):
                        time.sleep(0.05)
                        interrupted = budget.monotonic()
                        raise cancellation()
                self.assertLess(budget.monotonic() - interrupted, 0.1)
                state = json.loads((self.root / ".resource-budget.state.json").read_text())
                deadline = state["gpu_deadline"]
                self.assertGreater(deadline - budget.monotonic(), 0.1)
                result = self.run_python("""
import json, time, resource_budget as b
budget = b
started = budget.monotonic()
with b.compute_slot():
    admitted = budget.monotonic()
print(json.dumps(dict(started=started, admitted=admitted)))
""")
                timing = json.loads(result.stdout)
                self.assertGreaterEqual(timing["admitted"], deadline)

    def check_interrupted_cooldown_is_not_charged_twice(self, initial_slot):
        budget._cores = 1
        budget._last_cpu, budget._last_wall = 10.0, 100.0
        clock = dict(cpu=10.03, wall=100.03)
        save = budget._save_state

        def save_then_delay(root, state):
            save(root, state)
            # Paging/fsync/scheduling may move the new baseline past the debt.
            # A fixed bound on the next deadline's growth would be incorrect.
            clock["wall"] = 101.0

        path = self.root / ".resource-budget.state.json"
        with mock.patch.object(budget.time, "process_time", side_effect=lambda: clock["cpu"]), \
                mock.patch.object(budget, "monotonic", side_effect=lambda: clock["wall"]), \
                mock.patch.object(budget, "_boot_identity", return_value="test-boot"):
            with mock.patch.object(budget, "_save_state", side_effect=save_then_delay), \
                    mock.patch.object(budget, "_sleep_until", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    if initial_slot:
                        with budget.compute_slot():
                            self.fail("Interrupted pre-work cooldown admitted compute")
                    else:
                        budget.cpu_checkpoint(force=True)
            self.assertEqual((budget._last_cpu, budget._last_wall), (10.03, 101.0))
            deadline = json.loads(path.read_text())["cpu_deadline"]
            self.assertAlmostEqual(deadline, 100.15)
            clock["cpu"] += 0.001
            with mock.patch.object(budget, "_sleep_until"):
                budget.cpu_checkpoint(force=True)
            following_deadline = json.loads(path.read_text())["cpu_deadline"]
            self.assertAlmostEqual(following_deadline, max(deadline, 101.0) + 0.001 / 0.2)

    def test_checkpoint_interrupted_cooldown_does_not_double_charge(self):
        self.check_interrupted_cooldown_is_not_charged_twice(initial_slot=False)

    def test_prework_interrupted_cooldown_does_not_double_charge(self):
        self.check_interrupted_cooldown_is_not_charged_twice(initial_slot=True)

    def test_failed_charge_persistence_does_not_advance_cpu_baseline(self):
        baseline = budget._last_cpu, budget._last_wall
        with mock.patch.object(budget, "_save_state", side_effect=budget.ResourceBudgetError("failed save")), \
                mock.patch.object(budget, "_sleep_until") as sleep:
            with self.assertRaises(budget.ResourceBudgetError):
                budget.cpu_checkpoint(force=True)
            self.assertEqual((budget._last_cpu, budget._last_wall), baseline)
            with self.assertRaises(budget.ResourceBudgetError):
                with budget.compute_slot():
                    self.fail("Failed pre-work persistence admitted compute")
            self.assertEqual((budget._last_cpu, budget._last_wall), baseline)
            sleep.assert_not_called()

    def test_failed_model_charge_persistence_keeps_unpaid_cpu_baseline(self):
        save = budget._save_state
        calls = 0

        def fail_second_save(root, state):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise budget.ResourceBudgetError("failed model charge save")
            return save(root, state)

        with mock.patch.object(budget, "_save_state", side_effect=fail_second_save):
            with self.assertRaises(budget.ResourceBudgetError):
                with budget.compute_slot():
                    baseline = budget._last_cpu, budget._last_wall
        self.assertEqual((budget._last_cpu, budget._last_wall), baseline)

    def parallel_work(self, mode):
        script = """
import json, time, resource_budget as b
budget = b
b._cores = 1
mode = MODE
cpu = active = 0.0
started = budget.monotonic()
for _ in range(3):
    if mode == 'checkpoint':
        initial = time.process_time()
        while time.process_time() - initial < .03: pass
        cpu += time.process_time() - initial
        b.cpu_checkpoint()
    else:
        with b.compute_slot(mode):
            initial_cpu, initial_wall = time.process_time(), budget.monotonic()
            if mode == 'gpu': time.sleep(.02)
            else:
                while time.process_time() - initial_cpu < .02: pass
            cpu += time.process_time() - initial_cpu
            active += budget.monotonic() - initial_wall
b.cpu_checkpoint(force=True)
print(json.dumps(dict(start=started, end=budget.monotonic(), cpu=cpu, active=active)))
""".replace("MODE", repr(mode))
        processes = [subprocess.Popen([sys.executable, "-c", script],
                                      env=dict(os.environ, PYTHONPATH=str(BIN)),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(3)]
        rows = []
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                rows.append(json.loads(stdout))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        elapsed = max(row["end"] for row in rows) - min(row["start"] for row in rows)
        measured = sum(row["active" if mode == "gpu" else "cpu"] for row in rows)
        self.assertLessEqual(measured / elapsed, .202, (mode, measured, elapsed))

    def test_three_processes_share_gpu_duty_budget(self):
        self.parallel_work("gpu")

    def test_three_processes_share_cpu_compute_budget(self):
        self.parallel_work("cpu")

    def test_three_processes_share_cpu_checkpoint_budget(self):
        self.parallel_work("checkpoint")


if __name__ == "__main__":
    unittest.main()
