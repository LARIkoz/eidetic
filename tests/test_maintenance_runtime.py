"""Install/update parity, cancellation evidence and cross-interpreter clocks."""

import importlib
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
import bounded_worker
import maintenance_hooks
import resource_budget as budget
import vector_maintenance


class MaintenanceRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="eidetic-maintenance-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            "EIDETIC_RESOURCE_ROOT": str(self.root / "budget"),
            "PYTHONPATH": str(REPO / "bin"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        importlib.reload(budget)
        self.addCleanup(importlib.reload, budget)

    def test_hook_migration_deduplicates_only_owned_commands(self):
        keep = {"command": "my-index.sh --incremental", "timeout": 42}
        settings = {"env": {"EIDETIC_CONFIDENCE_EVENTS": "off"}, "hooks": {"Stop": [
            {"hooks": [keep, {"command": 'taskpolicy -b python3 ~/.claude/memory-system/bin/lock_runner.py ~/.claude/memory-system/.m2-background.lock /bin/bash ~/.claude/memory-system/bin/index.sh'}]},
            {"hooks": [{"command": 'nohup ~/.claude/hooks/semantic-maintenance.sh &'}]},
        ]}}
        maintenance_hooks.ensure_maintenance_hook(settings, str(self.root / "custom store"))
        before = json.dumps(settings)
        maintenance_hooks.ensure_maintenance_hook(settings, str(self.root / "custom store"))
        self.assertEqual(json.dumps(settings), before)
        hooks = [h for e in settings["hooks"]["Stop"] for h in e["hooks"]]
        self.assertIn(keep, hooks)
        owned = [h for h in hooks if "semantic-maintenance" in h["command"]]
        self.assertEqual(len(owned), 1)
        self.assertTrue(owned[0]["async"])
        self.assertIn("custom store'", owned[0]["command"])
        self.assertEqual(settings["env"]["EIDETIC_CONFIDENCE_EVENTS"], "off")

    def test_actual_installer_and_updater_registration_blocks(self):
        home = self.root / "home"
        settings_path = home / ".claude/settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "user-hook"}]}]}}))
        for filename in ("install.sh", "bin/update.sh", "install.sh"):
            source = (REPO / filename).read_text()
            marker = 'EIDETIC_INSTALL_MEMORY_SYSTEM="$MEMORY_SYSTEM" python3 << \'PYEOF\'\n'
            block = source.split(marker, 1)[1].split("\nPYEOF", 1)[0]
            result = subprocess.run([sys.executable, "-c", block], env=dict(
                os.environ, HOME=str(home), EIDETIC_INSTALL_MEMORY_SYSTEM=str(REPO)),
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(settings_path.read_text())
        commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(sum("semantic-maintenance.sh" in c for c in commands), 1)
        self.assertIn("user-hook", commands)

    def test_hook_migration_preserves_local_overrides_and_similar_user_commands(self):
        for local, global_value in (("off", "on"), ("on", "off")):
            unrelated = ["echo semantic-maintenance.sh", "/other/project/semantic-maintenance.sh"]
            settings = {"env": {"EIDETIC_M2_SYNTHESIS": global_value}, "hooks": {"Stop": [
                {"hooks": [{"command": c} for c in unrelated] + [{"command":
                    f'EIDETIC_M2_SYNTHESIS={local} /bin/bash "$HOME/.claude/hooks/semantic-maintenance.sh"'}]}
            ]}}
            maintenance_hooks.ensure_maintenance_hook(settings)
            before = json.dumps(settings)
            maintenance_hooks.ensure_maintenance_hook(settings)
            self.assertEqual(json.dumps(settings), before)
            commands = [h["command"] for e in settings["hooks"]["Stop"] for h in e["hooks"]]
            self.assertTrue(all(c in commands for c in unrelated))
            self.assertIn(f"EIDETIC_M2_SYNTHESIS={local}", commands[-1])

    def test_reinstall_preserves_unspecified_model_choices(self):
        home = self.root / "reinstall-home"
        runtime = home / ".claude/memory-system"
        runtime.mkdir(parents=True)
        choices = {".embed_profile": "english", ".embed_engine": "fastembed",
                   ".signal_model": "haiku", ".translate_backend": "auto"}
        for filename, value in choices.items():
            (runtime / filename).write_text(value + "\n")
        env = dict(os.environ, HOME=str(home), EIDETIC_MEMORY_SYSTEM=str(runtime),
                   EIDETIC_QUERY_TRANSLATE="off", EIDETIC_NONINTERACTIVE="1", PYTHONNOUSERSITE="1")
        for name in ("EIDETIC_EMBED_PROFILE", "EIDETIC_EMBED_ENGINE", "EIDETIC_SIGNAL_MODEL"):
            env.pop(name, None)
        # Exercise the stdlib-only install independently of CI's optional packages.
        scripts = self.root / "stdlib-bin"
        scripts.mkdir()
        python = scripts / "python3"
        python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' -S "$@"\n')
        python.chmod(0o755)
        env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
        p = subprocess.run(["/bin/bash", str(REPO / "install.sh")], cwd=REPO, env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        choices[".translate_backend"] = "off"
        for filename, value in choices.items():
            self.assertEqual((runtime / filename).read_text().strip(), value)

    def test_cpu_backpressure_precedes_new_lexical_work_but_not_completed_results(self):
        root = budget._root()
        root.mkdir()
        state = budget._load_state(root)
        state["cpu_deadline"] = budget.monotonic() + 20
        budget._save_state(root, state)
        with mock.patch.dict(os.environ, {"EIDETIC_RESOURCE_WAIT_SECONDS": "0.05"}):
            with self.assertRaises(budget.ResourceBudgetBusy):
                budget.cpu_admission()
            start = time.monotonic()
            budget.cpu_checkpoint(force=True, wait=False)
            self.assertLess(time.monotonic() - start, 0.5)
        self.assertGreater(budget._load_state(root)["cpu_deadline"], budget.monotonic())

    def test_registration_creates_settings_on_first_install(self):
        home = self.root / "fresh-home"
        (home / ".claude").mkdir(parents=True)
        source = (REPO / "install.sh").read_text()
        marker = 'EIDETIC_INSTALL_MEMORY_SYSTEM="$MEMORY_SYSTEM" python3 << \'PYEOF\'\n'
        block = source.split(marker, 1)[1].split("\nPYEOF", 1)[0]
        p = subprocess.run([sys.executable, "-c", block], env=dict(
            os.environ, HOME=str(home), EIDETIC_INSTALL_MEMORY_SYSTEM=str(REPO)),
            capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        settings = json.loads((home / ".claude/settings.json").read_text())
        self.assertIn("SessionStart", settings["hooks"])
        self.assertIn("Stop", settings["hooks"])

    def test_updater_installs_maintenance_and_preserves_source_card(self):
        home = self.root / "update-home"
        runtime = home / ".claude/memory-system"
        (runtime / "bin").mkdir(parents=True)
        (home / ".claude/settings.json").write_text('{"hooks":{"Stop":[]}}')
        (runtime / ".installed.json").write_text('{"git_sha":"unknown"}')
        card = home / ".claude/projects/fixture/memory/rule.md"
        card.parent.mkdir(parents=True)
        original = "---\nname: preserve-source\ntype: feedback\n---\nAlways preserve the source card.\n"
        card.write_text(original)
        fakebin = self.root / "fakebin"
        fakebin.mkdir()
        # Only Git transport is replaced; the actual updater, refreshed index
        # and hook registration execute in a fully isolated HOME.
        git = fakebin / "git"
        git.write_text('#!' + sys.executable + '''
import os, pathlib, shutil, sys
source = pathlib.Path(os.environ['FIXTURE_SOURCE'])
if sys.argv[1] == 'clone':
    destination = pathlib.Path(sys.argv[3]); destination.mkdir()
    for name in ('bin', 'hooks', 'skill', 'schemas'):
        shutil.copytree(source/name, destination/name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('README.md', 'mcp_server.py'):
        shutil.copyfile(source/name, destination/name)
    if os.environ.get('FAIL_FIXTURE_REFRESH') == '1':
        (destination/'bin/assemble_context.py').write_text('raise SystemExit(75)\\n')
elif 'rev-parse' in sys.argv:
    print('1'*40)
else:
    sys.exit(1)
''')
        git.chmod(0o755)
        env = dict(os.environ, HOME=str(home), EIDETIC_MEMORY_SYSTEM=str(runtime),
                   FIXTURE_SOURCE=str(REPO), PYTHONNOUSERSITE="1",
                   PATH=str(fakebin) + os.pathsep + str(Path(sys.executable).parent) + ":/usr/bin:/bin")
        env.pop("EIDETIC_USAGE_LOG", None)
        env["FAIL_FIXTURE_REFRESH"] = "1"
        p = subprocess.run(["/bin/bash", str(REPO / "bin/update.sh")], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("exit 75", p.stdout)
        self.assertEqual((runtime / ".refresh-pending").read_text().strip(), "1")
        env.pop("FAIL_FIXTURE_REFRESH")
        p = subprocess.run(["/bin/bash", str(REPO / "bin/update.sh")], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((runtime / ".refresh-pending").read_text().strip(), "0")
        self.assertEqual(card.read_text(), original)
        self.assertTrue((runtime / "bin/vector_maintenance.py").exists())
        settings = json.loads((home / ".claude/settings.json").read_text())
        commands = [h["command"] for e in settings["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(sum("semantic-maintenance.sh" in c for c in commands), 1)

    def test_timeout_preserves_error_then_success_clears_it(self):
        log = self.root / "embed-last.log"
        log.write_text("previous model load failed\n")
        with mock.patch.object(vector_maintenance, "run", return_value=bounded_worker.Result(-9, "", "", True)):
            failed, reason = vector_maintenance.refresh("embed", "index", "vectors", log)
        self.assertTrue(failed)
        self.assertIn("timed out", reason)
        self.assertIn("previous model load failed", log.read_text())
        with mock.patch.object(vector_maintenance, "run", return_value=bounded_worker.Result(0, "done", "")):
            self.assertEqual(vector_maintenance.refresh("embed", "index", "vectors", log), (False, ""))
        self.assertEqual(log.read_text(), "")

    def test_competing_vector_request_preserves_active_runs_log(self):
        import fcntl
        log = self.root / "embed-last.log"
        log.write_text("existing diagnosis")
        with open(str(log) + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(vector_maintenance, "run") as run:
                failed, reason = vector_maintenance.refresh("embed", "index", "vectors", log)
            self.assertTrue(failed)
            self.assertIn("already running", reason)
            run.assert_not_called()
        self.assertEqual(log.read_text(), "existing diagnosis")

    def test_vector_refresh_really_bounds_hung_worker(self):
        script = self.root / "embed.py"
        script.write_text("import time\ntime.sleep(30)\n")
        with mock.patch.dict(os.environ, {"EIDETIC_EMBED_TIMEOUT": "0.1"}):
            start = time.monotonic()
            failed, reason = vector_maintenance.refresh(script, "index", "vectors", self.root / "log")
        self.assertTrue(failed)
        self.assertIn("timed out", reason)
        self.assertLess(time.monotonic() - start, 3)

    def test_previous_boot_receipt_does_not_create_new_cooldown(self):
        root = budget._root()
        root.mkdir()
        ledger = root / ".resource-budget-deferred.jsonl"
        ledger.write_text(json.dumps(dict(boot_id="previous-boot", cpu=100,
                                         start=budget.monotonic() + 100000,
                                         gpu_elapsed=100)) + "\n")
        state = budget._load_state(root)
        budget._settle(root, state, budget.settings(), 0, budget.monotonic(), wait=False)
        self.assertLessEqual(state["cpu_deadline"], budget.monotonic())
        self.assertEqual(state["gpu_deadline"], 0)
        self.assertEqual(state["deferred_offset"], ledger.stat().st_size)

    def test_invalid_journal_offset_and_recovery_shape_fail_closed(self):
        root = budget._root()
        root.mkdir()
        state = budget._load_state(root)
        path = root / ".resource-budget.state.json"
        for patch in ({"deferred_offset": -1}, {"deferred_offset": "1"},
                      {"deferred_offset": True}, {"deferred_offset": 1000},
                      {"recovered_charges": {"id": {"cpu": "bad", "gpu_elapsed": 0}}}):
            path.write_text(json.dumps(dict(state, **patch)))
            with self.assertRaises(budget.ResourceBudgetError):
                budget._load_state(root)

    def test_incomplete_journal_tail_is_not_consumed_or_penalized_twice(self):
        root = budget._root()
        root.mkdir()
        ledger = root / ".resource-budget-deferred.jsonl"
        ledger.write_bytes(b'{"boot_id":')
        state = budget._load_state(root)
        with self.assertRaises(budget.ResourceBudgetBusy):
            budget._settle(root, state, budget.settings(), 0, budget.monotonic(), wait=False)
        self.assertEqual(state.get("deferred_offset", 0), 0)
        self.assertFalse((root / ".resource-budget.state.json").exists())
        self.assertEqual(ledger.read_bytes(), b'{"boot_id":')

    def test_supervisor_subtracts_already_accounted_worker_cpu(self):
        from types import SimpleNamespace
        script = '''import os,json,pathlib,sys
pathlib.Path(os.environ['EIDETIC_WORK_RECEIPTS'],'paid.json').write_text(
    json.dumps(dict(active=False, accounted_cpu=8.0)))
sys.exit(75)
'''
        with mock.patch.object(bounded_worker.resource, "getrusage", side_effect=[
            SimpleNamespace(ru_utime=0, ru_stime=0),
            SimpleNamespace(ru_utime=10, ru_stime=0),
        ]):
            result = bounded_worker.run([sys.executable, "-c", script], timeout=3)
        self.assertEqual(result.returncode, 75)
        rows = [json.loads(line) for line in
                (budget._root() / ".resource-budget-deferred.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(sum(row["cpu"] for row in rows), 2)

    def test_checkpoint_receipt_records_cpu_once_without_active_model(self):
        receipts = self.root / "receipts"
        receipts.mkdir()
        with mock.patch.dict(os.environ, {"EIDETIC_WORK_RECEIPTS": str(receipts)}), \
                mock.patch.object(budget, "_admission_wait"):
            budget.cpu_checkpoint(force=True)
        row = json.loads((receipts / f"{os.getpid()}.json").read_text())
        self.assertFalse(row["active"])
        self.assertGreater(row["accounted_cpu"], 0)

    def test_compute_only_caller_frames_torn_tail_and_retains_gpu_charge(self):
        root = budget._root()
        with self.assertRaises(budget.ResourceBudgetBusy):
            with budget.compute_slot("gpu"):
                row = dict(boot_id=budget._boot_identity(), cpu=0,
                           start=budget.monotonic(), gpu_elapsed=0)
                (root / ".resource-budget-deferred.jsonl").write_text(json.dumps(row))
                time.sleep(0.01)
        rows = [json.loads(line) for line in
                (root / ".resource-budget-deferred.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(len(rows), 2)
        self.assertGreater(rows[1]["gpu_elapsed"], 0)
        state = budget._load_state(root)
        budget._settle(root, state, budget.settings(), 0, budget.monotonic(), wait=False)
        self.assertGreater(state["gpu_deadline"], rows[1]["start"])
        self.assertEqual(state["deferred_offset"], (root / ".resource-budget-deferred.jsonl").stat().st_size)

    def test_background_launcher_does_not_enable_disabled_features(self):
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin/lock_runner.py").write_bytes((REPO / "bin/lock_runner.py").read_bytes())
        output = self.root / "flags"
        (runtime / "bin/index.sh").write_text('#!/bin/bash\nprintf "%s|%s" "$EIDETIC_CONFIDENCE_EVENTS" "$EIDETIC_M2_SYNTHESIS" > "$FLAGS_OUT"\n')
        env = dict(os.environ, EIDETIC_MEMORY_SYSTEM=str(runtime), FLAGS_OUT=str(output),
                   EIDETIC_CONFIDENCE_EVENTS="off", EIDETIC_M2_SYNTHESIS="off")
        p = subprocess.run(["/bin/bash", str(REPO / "hooks/semantic-maintenance.sh")],
                           env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(output.read_text(), "off|off")

    @unittest.skipUnless(sys.platform == "darwin", "macOS system Python clock regression")
    def test_system_python_shares_existing_modern_clock_and_debt(self):
        if not Path("/usr/bin/python3").exists():
            self.skipTest("system Python missing")
        root = budget._root()
        root.mkdir()
        state = budget._load_state(root)
        state["gpu_deadline"] = budget.monotonic() + 20
        budget._save_state(root, state)
        source = '''import json, resource_budget as b
state = b._load_state(b._root())
print(json.dumps(dict(now=b.monotonic(), debt=state['gpu_deadline'])))
'''
        start = budget.monotonic()
        p = subprocess.run(["/usr/bin/python3", "-c", source], capture_output=True,
                           text=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        row = json.loads(p.stdout)
        self.assertGreaterEqual(row["now"], start)
        self.assertLessEqual(row["now"], budget.monotonic())
        self.assertEqual(row["debt"], state["gpu_deadline"])
