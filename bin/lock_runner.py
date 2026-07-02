#!/usr/bin/env python3
"""Run a command while holding the shared Eidetic runtime lock."""

import fcntl
import os
import subprocess
import sys


def main():
    argv = sys.argv[1:]
    # Opt-in: exit N (instead of 0) when the lock is busy, so callers that must
    # NOT lose work on contention (Stop-hook signal compounding) can detect it
    # and spool. Default stays 0 — the SessionStart re-exec path relies on it.
    busy_exit = 0
    if argv and argv[0] == "--busy-exit":
        if len(argv) < 2 or not argv[1].isdigit():
            print("Usage: lock_runner.py [--busy-exit N] <lockfile> <command> [args...]", file=sys.stderr)
            return 2
        busy_exit = int(argv[1])
        argv = argv[2:]

    if len(argv) < 2:
        print("Usage: lock_runner.py [--busy-exit N] <lockfile> <command> [args...]", file=sys.stderr)
        return 2

    lockfile = argv[0]
    command = argv[1:]
    os.makedirs(os.path.dirname(lockfile), exist_ok=True)

    with open(lockfile, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Memory system busy, skipping")
            return busy_exit

        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()

        return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
