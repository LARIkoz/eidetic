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


def monotonic():
    """One boot-relative clock across Python versions sharing the debt ledger.

    macOS Python <3.10 offsets time.monotonic() by interpreter start. Uptime
    raw is the unadjusted mach_absolute_time clock used by newer interpreters,
    so existing ledger deadlines remain valid without resetting owed work.
    """
    if sys.platform == "darwin":
        if not hasattr(time, "CLOCK_UPTIME_RAW"):
            raise ResourceBudgetError("This Python lacks the shared macOS uptime clock")
        return time.clock_gettime(time.CLOCK_UPTIME_RAW)
    return time.monotonic()


class ResourceBudgetError(RuntimeError):
    """The shared budget cannot safely admit work."""


class ResourceBudgetBusy(ResourceBudgetError):
    """Work was deferred because the shared governor is occupied."""


_deferred_count = 0


def deferred_count():
    return _deferred_count


def wait_seconds():
    try:
        value = float(os.environ.get("EIDETIC_RESOURCE_WAIT_SECONDS", "2"))
        return value if math.isfinite(value) and 0 <= value <= 30 else 2.0
    except ValueError:
        return 2.0


def defer_charge(cpu, start, gpu_elapsed=0.0, charge_id=None):
    """Append unpaid work without waiting for a stalled model's state lock."""
    root = _root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    row = dict(boot_id=_boot_identity(), cpu=max(0.0, cpu), start=start,
               gpu_elapsed=max(0.0, gpu_elapsed))
    if charge_id is not None:
        row["charge_id"] = charge_id
    data = ("\n" + json.dumps(row, allow_nan=False) + "\n").encode()
    fd = os.open(root / ".resource-budget-deferred.jsonl",
                 os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise ResourceBudgetError("Incomplete deferred budget charge")
        os.fsync(fd)
    finally:
        os.close(fd)


def _work_receipt(started=None, gpu=False):
    """Only unfinished model work needs cancellation recovery, never waiting."""
    directory = os.environ.get("EIDETIC_WORK_RECEIPTS")
    if not directory:
        return
    target = Path(directory) / f"{os.getpid()}.json"
    row = dict(active=started is not None, started=started, gpu=bool(gpu), pid=os.getpid(),
               accounted_cpu=_accounted_cpu)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix="receipt-", delete=False) as f:
        json.dump(row, f)
        temporary = f.name
    os.replace(temporary, target)


_CHECKPOINT_CPU_SECONDS = 0.025
_MAX_FUTURE_SECONDS = 7 * 24 * 3600
_pid = os.getpid()
_mutex = threading.RLock()
_depth = 0
_gpu_active = False
_work_started = None
_last_cpu = time.process_time()
_last_wall = monotonic()
_accounted_cpu = 0.0
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
    now = monotonic()
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
    global _pid, _mutex, _depth, _gpu_active, _last_cpu, _last_wall, _policy_pid, _settings_cache, _cores, _accounted_cpu, _deferred_count, _work_started, _warned
    if _pid != os.getpid():
        # A fork must not inherit a mutex owned by a vanished parent thread.
        _pid = os.getpid()
        _mutex = threading.RLock()
        _depth = 0
        _gpu_active = False
        _last_cpu, _last_wall = time.process_time(), monotonic()
        _accounted_cpu = 0.0
        _deferred_count = 0
        _work_started = None
        _warned = set()
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
def _shared_lock(wait_budget=None):
    if fcntl is None:
        raise ResourceBudgetError("Shared resource budgeting requires fcntl locking")
    descriptor = None
    try:
        root = _root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(root / ".resource-budget.lock",
                             os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        global _deferred_count
        deadline = monotonic() + (wait_seconds() if wait_budget is None else wait_budget)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    _deferred_count += 1
                    raise ResourceBudgetBusy("Shared compute busy; work deferred")
                time.sleep(min(0.02, max(0.0, deadline - monotonic())))
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
    now = monotonic()
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
    offset = state.get("deferred_offset", 0)
    if type(offset) is not int or offset < 0:
        raise ResourceBudgetError("Invalid deferred journal offset")
    journal = root / ".resource-budget-deferred.jsonl"
    if offset > (journal.stat().st_size if journal.exists() else 0):
        raise ResourceBudgetError("Deferred journal offset exceeds preserved journal")
    recovered = state.get("recovered_charges", {})
    if not isinstance(recovered, dict):
        raise ResourceBudgetError("Invalid recovered charge map")
    for key, record in recovered.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise ResourceBudgetError("Invalid recovered charge identity")
        for field in ("cpu", "gpu_elapsed"):
            value = record.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ResourceBudgetError("Invalid recovered charge amount")
    if state["updated"] > now + 1:
        raise ResourceBudgetError("Resource budget clock moved backwards; refusing work")
    return state


def _save_state(root, state):
    state["updated"] = monotonic()
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
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        time.sleep(remaining)


def _admission_wait(deadline, expires):
    global _deferred_count
    if deadline > expires:
        _deferred_count += 1
        raise ResourceBudgetBusy("Shared compute cooldown pending; work deferred")
    _sleep_until(deadline)


def _settle(root, state, policy, cpu, start, gpu_elapsed=0.0, *, wait=True):
    global _last_cpu, _last_wall, _accounted_cpu, _deferred_count
    capacity = _cpu_count() * policy["cpu_percent"] / 100.0
    # Offset is committed with the debt, so retries never double-charge a row.
    try:
        with (root / ".resource-budget-deferred.jsonl").open("rb") as charges:
            charges.seek(state.get("deferred_offset", 0))
            while True:
                line = charges.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # Do not consume a writer's unfinished tail or penalize it
                    # twice. A subsequent append frames a genuinely torn line.
                    _deferred_count += 1
                    raise ResourceBudgetBusy("Deferred charge tail incomplete; retry pending")
                if not line.strip():
                    state["deferred_offset"] = charges.tell()
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get("boot_id"), str):
                        raise ValueError("invalid charge record")
                    # A boot change resets the clock domain. An old timestamp
                    # may exceed today's uptime; it is not a damaged new charge.
                    if row["boot_id"] != state["boot_id"]:
                        state["deferred_offset"] = charges.tell()
                        continue
                    if row.get("charge_id") is not None and not isinstance(row["charge_id"], str):
                        raise ValueError("invalid charge identity")
                    for key in ("cpu", "start", "gpu_elapsed"):
                        value = row[key]
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                            raise ValueError("invalid charge value")
                    if row["start"] > monotonic() + 1:
                        raise ValueError("invalid charge clock")
                except (ValueError, KeyError, TypeError):
                    # Preserve damaged evidence and charge a full maximum worker
                    # window once. A leading newline frames the next append even
                    # after a short write; no repair truncates the journal.
                    row = dict(boot_id=state["boot_id"], start=monotonic(),
                               cpu=_cpu_count() * 300.0, gpu_elapsed=300.0)
                    _warn_once("damaged-charge", "Damaged deferred charge retained; conservative 300s work charge applied")
                charge_id = row.get("charge_id")
                recovered = state.setdefault("recovered_charges", {})
                if not isinstance(recovered, dict):
                    recovered = state["recovered_charges"] = {}
                if row["boot_id"] == state["boot_id"]:
                    if charge_id is not None:
                        previous = recovered.get(charge_id, {"cpu": 0.0, "gpu_elapsed": 0.0})
                        current = {k: max(previous[k], row[k]) for k in ("cpu", "gpu_elapsed")}
                        for k in current:
                            row[k] = current[k] - previous[k]
                        recovered[charge_id] = current
                    state["cpu_deadline"] = max(state["cpu_deadline"], row["start"]) + row["cpu"] / capacity
                    if row["gpu_elapsed"]:
                        state["gpu_deadline"] = max(state["gpu_deadline"], row["start"]) + row["gpu_elapsed"] * 100.0 / policy["gpu_percent"]
                state["deferred_offset"] = charges.tell()
    except FileNotFoundError:
        pass
    state["cpu_deadline"] = max(state["cpu_deadline"], start) + max(0.0, cpu) / capacity
    if gpu_elapsed:
        state["gpu_deadline"] = max(state["gpu_deadline"], start) + gpu_elapsed * 100.0 / policy["gpu_percent"]
    # Persist before sleeping, so an interrupted cooldown survives its owner.
    _save_state(root, state)
    # Once debt is durable, retries must not charge this CPU again. A failed
    # save leaves the previous baseline intact so unpaid CPU is still due.
    _last_cpu, _last_wall = time.process_time(), monotonic()
    _accounted_cpu += max(0.0, cpu)
    _work_receipt()
    if wait:
        _sleep_until(max(state["cpu_deadline"], state["gpu_deadline"]))


def cpu_admission():
    """Bound subsequent lexical work using the atomically published CPU debt.

    A reader does not need the model lock or GPU admission. Recheck after waits
    because another worker can publish CPU debt while this process sleeps.
    """
    if not settings()["enabled"]:
        return
    expires = monotonic() + wait_seconds()
    while True:
        deadline = _load_state(_root())["cpu_deadline"]
        if deadline <= monotonic():
            return
        _admission_wait(deadline, expires)


def cpu_checkpoint(force=False, *, wait=True):
    """Account cheap CPU separately from model admission and GPU cooldown."""
    global _last_cpu, _last_wall, _accounted_cpu
    _ensure_process()
    with _mutex:
        if _depth:
            return
        current = time.process_time()
        if not force and current - _last_cpu < _CHECKPOINT_CPU_SECONDS:
            return
        policy = settings()
        if not policy["enabled"]:
            _last_cpu, _last_wall = current, monotonic()
            return
        expires = monotonic() + wait_seconds()
        try:
            with _shared_lock(wait_budget=None if wait else 0) as root:
                state = _load_state(root)
                _settle(root, state, policy, time.process_time() - _last_cpu, _last_wall, wait=False)
        except ResourceBudgetBusy:
            unpaid = time.process_time() - _last_cpu
            defer_charge(unpaid, _last_wall)
            _accounted_cpu += max(0.0, unpaid)
            _last_cpu, _last_wall = time.process_time(), monotonic()
            _work_receipt()
            raise
        # GPU cooldown cannot block a lexical read or index-file scan.
        if wait:
            _admission_wait(state["cpu_deadline"], expires)


@contextmanager
def compute_slot(kind="cpu"):
    """Serialize model work; persist debt and release the lock during cooldown.

    Admission rechecks debt under the lock after every lock-free wait. A caller
    can never begin compute based on a stale deadline observed before sleeping.
    """
    global _depth, _gpu_active, _last_cpu, _last_wall, _work_started, _accounted_cpu
    if kind not in ("cpu", "gpu"):
        raise ValueError("compute_slot kind must be 'cpu' or 'gpu'")
    _ensure_process()
    with _mutex:
        if _depth:
            _gpu_active = _gpu_active or kind == "gpu"
            _work_receipt(_work_started, _gpu_active)
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
                _last_cpu, _last_wall = time.process_time(), monotonic()
            return
        expires = monotonic() + wait_seconds()
        completed = False
        cancelled = False
        unpaid_gpu = 0.0
        first_admission = True
        try:
            while True:
                with _shared_lock() as root:
                    state = _load_state(root)
                    cpu = time.process_time() - _last_cpu if first_admission else 0.0
                    _settle(root, state, policy, cpu, _last_wall, wait=False)
                    first_admission = False
                    deadline = max(state["cpu_deadline"], state["gpu_deadline"])
                    if deadline <= monotonic():
                        _sleep_until(deadline)
                        start_cpu, start_wall = time.process_time(), monotonic()
                        _depth = 1
                        _gpu_active = kind == "gpu"
                        _work_started = start_wall
                        try:
                            _work_receipt(start_wall, _gpu_active)
                            yield
                        except BaseException as exc:
                            cancelled = not isinstance(exc, Exception)
                            raise
                        finally:
                            elapsed = monotonic() - start_wall
                            cpu = time.process_time() - start_cpu
                            unpaid_gpu = elapsed if _gpu_active else 0.0
                            _depth = 0
                            try:
                                _settle(root, state, policy, cpu, start_wall,
                                        elapsed if _gpu_active else 0.0, wait=False)
                                deadline = max(state["cpu_deadline"], state["gpu_deadline"])
                                completed = True
                                _work_receipt()
                            finally:
                                _gpu_active = False
                                _work_started = None
                        break
                _admission_wait(deadline, expires)
        except ResourceBudgetBusy:
            if not completed:
                # Even compute-only callers must frame an unfinished ledger
                # tail and retain work whose exit settlement was interrupted.
                unpaid = max(0.0, time.process_time() - _last_cpu)
                defer_charge(unpaid, _last_wall, gpu_elapsed=unpaid_gpu)
                _accounted_cpu += unpaid
                _last_cpu, _last_wall = time.process_time(), monotonic()
                _work_receipt()
            raise
        finally:
            # Completed work may cool longer than admission waits. Release the
            # shared lock first so CPU-only reads can settle their own debt.
            if completed and not cancelled and os.environ.get("EIDETIC_RETURN_COMPLETED_COMPUTE") != "1":
                _sleep_until(deadline)
