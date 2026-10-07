"""Bound optional model work and reap its process group before returning."""

import math
import json
import hashlib
import tempfile
from pathlib import Path
import os
import resource
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
import time
from dataclasses import dataclass
from resource_budget import monotonic


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def timeout_seconds(name, default):
    try:
        value = float(os.environ.get(name, default))
        return value if math.isfinite(value) and 0 < value <= 300 else float(default)
    except ValueError:
        return float(default)


def cooperative_termination():
    # A Python frame can unwind compute_slot and persist cancellation debt.
    # Native code may not handle signals promptly; the supervisor also kills.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))


@contextmanager
def _supervisor_signals():
    # Library callers may not have installed the CLI cancellation handler.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.getsignal(signal.SIGTERM)
        cooperative_termination()
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def run(cmd, *, timeout, env=None, input_text=None):
    with _supervisor_signals(), tempfile.TemporaryDirectory(prefix="eidetic-worker-") as receipts:
        environment = dict(os.environ if env is None else env, EIDETIC_WORK_RECEIPTS=receipts)
        return _run(cmd, timeout=timeout, env=environment, input_text=input_text)


def _run(cmd, *, timeout, env=None, input_text=None):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = monotonic()
    # The independent guard preserves the deadline if this supervisor is killed.
    guarded = [sys.executable, os.path.abspath(__file__), "--guard", str(timeout), *cmd]
    # Publish completed results before cooldown; the next admission still pays
    # all persisted debt. A worker deadline must not kill an already-ready result.
    worker_env = dict(os.environ if env is None else env, EIDETIC_RETURN_COMPLETED_COMPUTE="1")
    proc = subprocess.Popen(guarded, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=worker_env,
                            start_new_session=True)
    residual_id = "residual:" + hashlib.sha256(
        f"{worker_env.get('EIDETIC_WORK_RECEIPTS')}:{proc.pid}:{started}".encode()).hexdigest()
    try:
        out, err = proc.communicate(input_text, timeout=timeout)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        returncode = proc.returncode
        completion = Path(worker_env["EIDETIC_WORK_RECEIPTS"]) / ".guard-result"
        if completion.exists():
            returncode = json.loads(completion.read_text())["returncode"]
        from resource_budget import defer_charge, settings
        if settings()["enabled"]:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
            covered_cpu = _recover_receipts(worker_env)
            defer_charge(max(0.0, cpu - covered_cpu), started, charge_id=residual_id)
        return Result(returncode, out, err)

    except BaseException as exc:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            out, err = proc.communicate(timeout=1.0)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        finally:
            # Also reap inherited-pipe children after the group leader exits.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            # An escaped process must not keep the supervisor's pipes open.
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            proc.kill()
            proc.wait(timeout=2)
            err += "\nWorker pipe drain timed out; escaped descendant may remain"
        # Recover unfinished slots; charge only CPU not already covered by
        # worker checkpoints or that conservative recovery.
        from resource_budget import defer_charge, settings
        if settings()["enabled"]:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
            covered_cpu = _recover_receipts(worker_env)
            defer_charge(max(0.0, cpu - covered_cpu), started, charge_id=residual_id)
        if isinstance(exc, subprocess.TimeoutExpired):
            return Result(proc.returncode, out, err, timed_out=True)
        raise


def _recover_receipts(environment):
    from resource_budget import defer_charge
    directory = environment.get("EIDETIC_WORK_RECEIPTS")
    if not directory:
        return 0.0
    covered_cpu = 0.0
    for path in Path(directory).glob("*.json"):
        row = json.loads(path.read_text())
        covered_cpu += row.get("accounted_cpu", 0.0)
        if not row.get("active"):
            continue
        started = row["started"]
        elapsed = max(0.0, monotonic() - started)
        identity = hashlib.sha256(f"{directory}:{row['pid']}:{started}".encode()).hexdigest()
        defer_charge(elapsed * (os.cpu_count() or 1), started,
                     gpu_elapsed=elapsed if row["gpu"] else 0.0, charge_id=identity)
        covered_cpu += elapsed * (os.cpu_count() or 1)
    return covered_cpu


class _GuardCancelled(BaseException):
    pass


def _guard():
    """Contain descendants even if their parent exits after supervisor SIGKILL."""
    timeout = float(sys.argv[2])
    child = subprocess.Popen(sys.argv[3:])

    def cancel(*_):
        raise _GuardCancelled()

    signal.signal(signal.SIGTERM, cancel)
    completed = False
    code = 124
    try:
        code = child.wait(timeout=timeout)
        code = code if code >= 0 else 128 - code
        completed = True
    except (subprocess.TimeoutExpired, _GuardCancelled):
        pass
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            os.killpg(os.getpgrp(), signal.SIGTERM)
            try:
                child.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                # Reap the direct worker before the guard dies, so its CPU is
                # included in the supervisor's waited-descendant resource usage.
                child.kill()
                child.wait(timeout=0.25)
            time.sleep(0.05)
            try:
                from resource_budget import settings
                if settings()["enabled"]:
                    _recover_receipts(os.environ)
            except Exception as exc:
                print(f"WARNING: watchdog accounting failed ({type(exc).__name__})", file=sys.stderr)
                code = 75
            if completed:
                # Group cleanup also kills the guard. Publish the worker status
                # first; a surviving supervisor can distinguish success from a
                # deadline kill without letting descendants outlive the guard.
                directory = Path(os.environ["EIDETIC_WORK_RECEIPTS"])
                with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as f:
                    json.dump({"returncode": code}, f)
                    temporary = f.name
                os.replace(temporary, directory / ".guard-result")
        finally:
            os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--guard":
        sys.exit(_guard())
    sys.exit(2)
