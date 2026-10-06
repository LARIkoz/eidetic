"""Cooperative, per-user CPU/GPU average budget for Eidetic workers.

This is not an instantaneous hardware quota. Call checkpoints in parsing loops,
use small compute batches, and synchronize GPU work before leaving compute_slot.
All topic bases share the budget; EIDETIC_MEMORY_SYSTEM is intentionally ignored.
Only EIDETIC_RESOURCE_ROOT redirects the host budget (primarily for tests).
Import changes no files, environment variables, or scheduling policies.
"""

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import warnings

try:
    import fcntl
except ImportError:
    fcntl = None


class ResourceBudgetError(RuntimeError):
    """The shared budget cannot safely admit work."""


_CHECKPOINT_CPU_SECONDS = 0.025
_MAX_FUTURE_SECONDS = 7 * 24 * 3600
_pid = os.getpid()
_mutex = threading.RLock()
_depth = 0
_gpu_active = False
_last_cpu = time.process_time()
_last_wall = time.monotonic()
_policy_pid = None
_boot_id = None
_warned = set()
_settings_cache = None
_cores = None


def _root():
    return Path(os.environ.get("EIDETIC_RESOURCE_ROOT", "~/.claude/memory-system")).expanduser()


def _cpu_count():
    global _cores
    if _cores is None:
        _cores = os.cpu_count() or 1
    return _cores


def settings():
    """Validate policy; a missing configuration uses conservative defaults."""
    global _settings_cache
    _ensure_process()
    root = _root()
    now = time.monotonic()
    if _settings_cache is not None:
        cached_root, checked, cached = _settings_cache
        if root == cached_root and now - checked < 1:
            return dict(cached)
    cores = _cpu_count()
    values = dict(enabled=True, cpu_percent=20, gpu_percent=20, batch_size=1,
                  threads=min(2, max(1, math.floor(cores * 0.2))), mlx_cache_mb=128)
    path = root / ".resource-budget.json"
    try:
        with path.open(encoding="utf-8") as handle:
            supplied = json.load(handle)
    except FileNotFoundError:
        supplied = {}
    except (OSError, ValueError) as exc:
        raise ResourceBudgetError(f"Cannot read resource budget configuration: {path}: {exc}") from exc
    if not isinstance(supplied, dict) or set(supplied) - set(values):
        raise ResourceBudgetError("Resource budget configuration must be an object with known policy keys")
    values.update(supplied)
    if type(values["enabled"]) is not bool:
        raise ResourceBudgetError("Resource budget enabled must be a boolean")
    for key in ("cpu_percent", "gpu_percent"):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 100:
            raise ResourceBudgetError(f"Resource budget {key} must be finite and in (0, 100]")
    for key in ("batch_size", "threads", "mlx_cache_mb"):
        if type(values[key]) is not int or values[key] < 1:
            raise ResourceBudgetError(f"Resource budget {key} must be a positive integer")
    values["threads"] = min(values["threads"], 2, max(1, math.floor(cores * values["cpu_percent"] / 100)))
    _settings_cache = (root, now, values)
    return dict(values)


def _ensure_process():
    global _pid, _mutex, _depth, _gpu_active, _last_cpu, _last_wall, _policy_pid, _settings_cache, _cores
    if _pid != os.getpid():
        # A fork must not inherit a mutex owned by a vanished parent thread.
        _pid = os.getpid()
        _mutex = threading.RLock()
        _depth = 0
        _gpu_active = False
        _last_cpu, _last_wall = time.process_time(), time.monotonic()
        _policy_pid = None
        _settings_cache = None
        _cores = None


def _warn_once(key, message):
    if key not in _warned:
        _warned.add(key)
        warnings.warn(message, RuntimeWarning, stacklevel=3)


def apply_background_policy():
    """Cap numerical threads and lower CPU priority, preserving disk I/O policy."""
    global _policy_pid
    _ensure_process()
    policy = settings()
    if not policy["enabled"]:
        return
    with _mutex:
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS"):
            try:
                current = int(os.environ.get(name, policy["threads"]))
            except ValueError:
                current = policy["threads"]
            os.environ[name] = str(min(current, policy["threads"]) if current > 0 else policy["threads"])
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        if _policy_pid == os.getpid():
            return
        _policy_pid = os.getpid()
        try:
            current = os.nice(0)
            if current < 10:
                os.nice(10 - current)
        except (AttributeError, OSError) as exc:
            _warn_once("nice", f"Eidetic background priority unavailable: {exc}")


def _boot_identity():
    global _boot_id
    if _boot_id is not None:
        return _boot_id
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                                    capture_output=True, text=True, check=True, timeout=2)
            identity = result.stdout.strip()
        else:
            identity = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not identity:
            raise ValueError("empty boot identity")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ResourceBudgetError(f"Cannot identify boot for shared resource budget: {exc}") from exc
    _boot_id = identity
    return identity


@contextmanager
def _shared_lock():
    if fcntl is None:
        raise ResourceBudgetError("Shared resource budgeting requires fcntl locking")
    descriptor = None
    try:
        root = _root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(root / ".resource-budget.lock",
                             os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except BaseException as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, OSError):
            raise ResourceBudgetError(f"Cannot lock shared resource budget: {exc}") from exc
        raise
    try:
        yield root
    finally:
        # Closing releases flock, but the inode must survive for other waiters.
        os.close(descriptor)


def _load_state(root):
    now = time.monotonic()
    boot = _boot_identity()
    empty = dict(boot_id=boot, cpu_deadline=0.0, gpu_deadline=0.0, updated=now)
    try:
        with (root / ".resource-budget.state.json").open(encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return empty
    except (OSError, ValueError) as exc:
        raise ResourceBudgetError(f"Cannot read shared resource budget state: {exc}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("boot_id"), str):
        raise ResourceBudgetError("Invalid shared resource budget state")
    if state["boot_id"] != boot:
        return empty
    for name in ("cpu_deadline", "gpu_deadline", "updated"):
        value = state.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > now + _MAX_FUTURE_SECONDS:
            raise ResourceBudgetError(f"Invalid resource budget state field: {name}")
    if state["updated"] > now + 1:
        raise ResourceBudgetError("Resource budget clock moved backwards; refusing work")
    return state


def _save_state(root, state):
    state["updated"] = time.monotonic()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".resource-budget-state-", delete=False) as handle:
            temporary = handle.name
            json.dump(state, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, root / ".resource-budget.state.json")
    except (OSError, ValueError) as exc:
        raise ResourceBudgetError(f"Cannot persist shared resource budget: {exc}") from exc
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _sleep_until(deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(remaining)


def _settle(root, state, policy, cpu, start, gpu_elapsed=0.0, *, wait=True):
    capacity = _cpu_count() * policy["cpu_percent"] / 100.0
    state["cpu_deadline"] = max(state["cpu_deadline"], start) + max(0.0, cpu) / capacity
    if gpu_elapsed:
        state["gpu_deadline"] = max(state["gpu_deadline"], start) + gpu_elapsed * 100.0 / policy["gpu_percent"]
    # Persist before sleeping, so an interrupted cooldown survives its owner.
    _save_state(root, state)
    if wait:
        _sleep_until(max(state["cpu_deadline"], state["gpu_deadline"]))


def cpu_checkpoint(force=False):
    """Charge CPU since the previous checkpoint/slot; call once per file or row.

    Small unaccounted tails (<25ms CPU per process) can exist until force=True.
    Call force=True at the end of a worker. The shared deadline prevents each
    concurrent worker from independently consuming the full machine budget.
    """
    global _last_cpu, _last_wall
    _ensure_process()
    with _mutex:
        if _depth:
            return
        current = time.process_time()
        if not force and current - _last_cpu < _CHECKPOINT_CPU_SECONDS:
            return
        policy = settings()
        if not policy["enabled"]:
            _last_cpu, _last_wall = current, time.monotonic()
            return
        with _shared_lock() as root:
            state = _load_state(root)
            _settle(root, state, policy, time.process_time() - _last_cpu, _last_wall)
            _last_cpu, _last_wall = time.process_time(), time.monotonic()


@contextmanager
def compute_slot(kind="cpu"):
    """Serialize compute and cooldown across workers; nested slots are reentrant.

    A nested GPU slot conservatively marks the entire outer slot as GPU work.
    The caller must synchronize device work even when its own operation raises.
    Cancellation persists debt without sleeping; the next worker pays it before
    entering its compute slot.
    """
    global _depth, _gpu_active, _last_cpu, _last_wall
    if kind not in ("cpu", "gpu"):
        raise ValueError("compute_slot kind must be 'cpu' or 'gpu'")
    _ensure_process()
    with _mutex:
        if _depth:
            _gpu_active = _gpu_active or kind == "gpu"
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        policy = settings()
        if not policy["enabled"]:
            try:
                yield
            finally:
                _last_cpu, _last_wall = time.process_time(), time.monotonic()
            return
        with _shared_lock() as root:
            state = _load_state(root)
            _settle(root, state, policy, time.process_time() - _last_cpu, _last_wall)
            start_cpu, start_wall = time.process_time(), time.monotonic()
            _depth = 1
            _gpu_active = kind == "gpu"
            cancelled = False
            try:
                yield
            except BaseException as exc:
                cancelled = not isinstance(exc, Exception)
                raise
            finally:
                elapsed = time.monotonic() - start_wall
                cpu = time.process_time() - start_cpu
                _depth = 0
                try:
                    _settle(root, state, policy, cpu, start_wall,
                            elapsed if _gpu_active else 0.0, wait=not cancelled)
                finally:
                    _gpu_active = False
                    _last_cpu, _last_wall = time.process_time(), time.monotonic()
