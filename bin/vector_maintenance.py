"""Bound a vector refresh without hiding interrupted or failed progress."""

import os
import fcntl
from pathlib import Path
import sys
import tempfile

from bounded_worker import run, timeout_seconds


def _write_log(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def refresh(script, db_path, vectors_db, log_path):
    """Serialize managed refreshes and their diagnostic publication."""
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(str(log) + ".lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True, "vector maintenance already running; retry pending"
        return _refresh(script, db_path, vectors_db, log_path)


def _refresh(script, db_path, vectors_db, log_path):
    """Return (incomplete, reason). Clear an old error only on actual success."""
    try:
        timeout = timeout_seconds("EIDETIC_EMBED_TIMEOUT", 30)
        environment = dict(os.environ)
        environment.setdefault("EIDETIC_RESOURCE_WAIT_SECONDS", str(min(timeout, 30)))
        result = run([sys.executable, str(script), str(db_path), str(vectors_db)],
                     timeout=timeout, env=environment)
        if result.timed_out:
            reason = "vector refresh timed out; completed batches retained; retry pending"
        elif result.returncode:
            output = (result.stderr or result.stdout or "nonzero exit").strip().splitlines()
            reason = (output[-1] if output else "nonzero exit")[:160]
        else:
            _write_log(log_path, "")
            return False, ""
        details = result.stderr or result.stdout or ""
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"[:160]
        details = ""
    # Retain the prior error on incomplete work: timeout is not proof that the
    # earlier failure is repaired. Keep one bounded diagnostic tail, not a DB.
    try:
        previous = Path(log_path).read_text(encoding="utf-8")[-8192:]
    except FileNotFoundError:
        previous = ""
    _write_log(log_path, previous + "\n" + details[-8192:] + "\n" + reason + "\n")
    return True, reason


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit("Usage: vector_maintenance.py embed.py index.db vectors.db log")
    incomplete, reason = refresh(*sys.argv[1:])
    if incomplete:
        print(reason, file=sys.stderr)
    sys.exit(75 if incomplete else 0)
