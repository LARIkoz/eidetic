#!/usr/bin/env python3
"""Run a command while holding the shared Eidetic runtime lock."""

import fcntl
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 3:
        print("Usage: lock_runner.py <lockfile> <command> [args...]", file=sys.stderr)
        return 2

    lockfile = sys.argv[1]
    command = sys.argv[2:]
    os.makedirs(os.path.dirname(lockfile), exist_ok=True)

    with open(lockfile, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Memory system busy, skipping")
            return 0

        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()

        return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
