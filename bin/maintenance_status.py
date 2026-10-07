"""Scoped failure evidence for optional semantic maintenance.

Read APIs keep their soft fallback contract. A queue consumer additionally needs
to distinguish a successful empty result from work that could not be evaluated.
Only bounded codes and exception class names enter the derived status record.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_failures = ContextVar("eidetic_maintenance_failures", default=None)


def note_failure(stage, reason):
    failures = _failures.get()
    if failures is None:
        return
    item = {"stage": stage, "reason": type(reason).__name__
            if isinstance(reason, BaseException) else reason}
    if item not in failures:
        failures.append(item)


@contextmanager
def capture_failures():
    failures = []
    token = _failures.set(failures)
    try:
        yield failures
    finally:
        _failures.reset(token)


def read_status(db_path):
    """Read derived progress without importing models or mutating the index."""
    import json
    from pathlib import Path
    import sqlite3
    from evidence import events_enabled
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = dict(conn.execute("SELECT key, value FROM schema_meta WHERE key IN (?, ?)",
                    ("pending_ingest_paths_v1", "semantic_maintenance_status_v1")))
    finally:
        conn.close()
    pending = json.loads(rows.get("pending_ingest_paths_v1", "[]"))
    status = json.loads(rows.get("semantic_maintenance_status_v1", "{}"))
    failures = {p: issue for p, issue in status.get("failures", {}).items() if p in pending}
    return {"enabled_for_caller": events_enabled(), "pending_count": len(pending), "failed_count": len(failures),
            "failures": failures, "last_attempt": status.get("last_attempt"),
            "last_completed_at": status.get("last_completed_at")}


if __name__ == "__main__":
    import json
    import sys
    import time
    try:
        status = read_status(sys.argv[1])
        if "--summary" in sys.argv:
            last = status["last_completed_at"]
            age = "unknown" if last is None else str(max(0, int(time.time() - last))) + "s ago"
            reasons = sorted({i["stage"] + "=" + i["reason"]
                              for row in status["failures"].values() for i in row["issues"]})
            paused = " (paused for this caller; queue retained)" if not status["enabled_for_caller"] and status["pending_count"] else ""
            print(f"semantic queue: {status['pending_count']} pending, "
                  f"{status['failed_count']} with failure evidence; last completed {age}{paused}"
                  + ("; " + "; ".join(reasons) if reasons else ""))
        else:
            print(json.dumps(status, indent=2))
        sys.exit((1 if status["enabled_for_caller"] else 3) if status["pending_count"] else 0)
    except Exception as exc:
        print(f"semantic status unavailable: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
