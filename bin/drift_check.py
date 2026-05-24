#!/usr/bin/env python3
"""Eidetic v2.5 — Drift Detection

Detects stale memories via wikilink validation and age checks.
Reads from index.db, writes to drift_state.db (separate from derived index — P1).
Runs at SessionStart (24h throttle). No file mutations.

Charter: P1 (derived separate), P5 (quality tracked), P6 (improves), P11 (contradictions).
"""

import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

AGE_THRESHOLDS = {
    "feedback": None,
    "user": 180,
    "reference": 90,
    "project": 30,
    "code": 30,
}
DEFAULT_AGE_DAYS = 60

try:
    from constants import DRIFT_PENALTIES
except ImportError:
    DRIFT_PENALTIES = {"broken_wikilink": 0.8, "age_stale": 0.5, "confidence_escalation": 0.3}


def get_drift_db_path(index_db_path):
    return os.path.join(os.path.dirname(index_db_path), "drift_state.db")


def init_drift_db(drift_path):
    conn = sqlite3.connect(drift_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drift_findings (
            path TEXT NOT NULL,
            drift_type TEXT NOT NULL,
            detail TEXT,
            memory_type TEXT,
            detected_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by TEXT,
            first_seen INTEGER DEFAULT 1,
            PRIMARY KEY (path, drift_type, detail)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drift_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    migrate_age_stale_details(conn)
    return conn


def normalize_age_stale_detail(detail):
    """Keep age-stale identity stable while threshold stays the same."""
    m = re.search(r"threshold=(\d+)d", detail or "")
    if m:
        return f"threshold={m.group(1)}d"
    return detail or ""


def migrate_age_stale_details(conn):
    """Normalize old age-stale details like `age=88d threshold=30d`.

    v2.5 stored changing age values in the primary key detail, so consecutive
    detections could never accumulate `first_seen`. Merge old rows into the
    stable threshold identity and preserve the highest detection count.
    """
    try:
        rows = conn.execute("""
            SELECT rowid, path, detail, first_seen, detected_at, resolved_at
            FROM drift_findings
            WHERE drift_type = 'age_stale'
        """).fetchall()
    except sqlite3.OperationalError:
        return

    for rowid, path, detail, first_seen, detected_at, resolved_at in rows:
        stable_detail = normalize_age_stale_detail(detail)
        if stable_detail == (detail or ""):
            continue

        existing = conn.execute("""
            SELECT rowid, first_seen, detected_at, resolved_at
            FROM drift_findings
            WHERE path = ? AND drift_type = 'age_stale' AND detail = ?
        """, (path, stable_detail)).fetchone()

        if existing and int(existing[0]) != int(rowid):
            existing_rowid, existing_seen, existing_detected, existing_resolved = existing
            merged_seen = max(int(existing_seen or 0), int(first_seen or 0))
            merged_detected = max(existing_detected or "", detected_at or "")
            merged_resolved = None if (resolved_at is None or existing_resolved is None) else existing_resolved
            conn.execute("""
                UPDATE drift_findings
                SET first_seen = ?, detected_at = ?, resolved_at = ?,
                    resolved_by = CASE WHEN ? IS NULL THEN NULL ELSE resolved_by END
                WHERE rowid = ?
            """, (merged_seen, merged_detected, merged_resolved, merged_resolved, existing_rowid))
            conn.execute("DELETE FROM drift_findings WHERE rowid = ?", (rowid,))
        else:
            conn.execute(
                "UPDATE drift_findings SET detail = ? WHERE rowid = ?",
                (stable_detail, rowid),
            )
    conn.commit()


def should_run(drift_conn):
    try:
        row = drift_conn.execute(
            "SELECT value FROM drift_meta WHERE key = 'last_check'"
        ).fetchone()
        if row and time.time() - float(row[0]) < 86400:
            return False
    except (sqlite3.OperationalError, ValueError):
        pass
    return True


def record_run(drift_conn):
    drift_conn.execute(
        "INSERT OR REPLACE INTO drift_meta (key, value) VALUES ('last_check', ?)",
        (str(time.time()),)
    )
    drift_conn.commit()


def build_known_names(index_conn):
    names = set()
    rows = index_conn.execute("SELECT DISTINCT path, name FROM memory_chunks").fetchall()
    for path, name in rows:
        stem = os.path.basename(path).replace(".md", "")
        names.add(stem)
        if name:
            names.add(name)

    skill_dir = os.path.expanduser("~/.claude/skills")
    if os.path.isdir(skill_dir):
        for d in os.listdir(skill_dir):
            if os.path.isdir(os.path.join(skill_dir, d)):
                names.add(d)
    return names


def extract_wikilinks_from_content(content):
    if not content:
        return []
    raw = re.findall(r'\[\[([^\]]+)\]\]', content)
    links = []
    seen = set()
    for link in raw:
        target = link.split("|")[0].split("#")[0].strip()
        if not target or target in seen:
            continue
        if "$" in target or "{" in target or "\\" in target:
            continue
        if re.match(r"^-[A-Za-z]\s", target) or "~/" in target:
            continue
        if re.search(r'(^|\s)(==|!=|-eq|-ne|-gt|-lt|-ge|-le)(\s|$)', target):
            continue
        if target == "..." or len(target) < 2:
            continue
        seen.add(target)
        links.append(target)
    return links


def check_wikilink_drift(index_conn, known_names):
    rows = index_conn.execute("""
        SELECT DISTINCT path, type, content FROM memory_chunks
        WHERE content LIKE '%[[%' AND source != 'code-index'
    """).fetchall()

    findings = []
    for path, mem_type, content in rows:
        links = extract_wikilinks_from_content(content)
        for link in links:
            if link not in known_names:
                findings.append((path, mem_type, "broken_wikilink", f"[[{link}]]"))
    return findings


def check_age_drift(index_conn):
    now = datetime.now()
    findings = []

    rows = index_conn.execute("""
        SELECT path, type, evidence, last_verified, mtime
        FROM memory_chunks
        WHERE evidence != 'hypothesis'
        GROUP BY path
        HAVING MIN(id)
    """).fetchall()

    for path, mem_type, evidence, last_verified, mtime in rows:
        threshold = AGE_THRESHOLDS.get(mem_type or "", DEFAULT_AGE_DAYS)
        if threshold is None:
            continue

        verified_dt = None
        if last_verified:
            try:
                clean = last_verified.replace("Z", "").replace("+00:00", "")
                verified_dt = datetime.fromisoformat(clean)
            except (ValueError, AttributeError):
                pass

        if verified_dt is None and mtime:
            verified_dt = datetime.fromtimestamp(mtime)

        if verified_dt is None:
            continue

        age_days = (now - verified_dt).days
        if age_days > threshold:
            findings.append((
                path, mem_type, "age_stale",
                f"threshold={threshold}d"
            ))
    return findings


def check_confidence_escalation(index_conn):
    rows = index_conn.execute("""
        SELECT path, type FROM memory_chunks
        WHERE source = 'agent-extracted'
    """).fetchall()

    path_counts = {}
    for path, mem_type in rows:
        path_counts.setdefault(path, {"agent": 0, "type": mem_type})
        path_counts[path]["agent"] += 1

    user_paths = set()
    for row in index_conn.execute(
        "SELECT DISTINCT path FROM memory_chunks WHERE source = 'user-explicit'"
    ):
        user_paths.add(row[0])

    findings = []
    for path, info in path_counts.items():
        if info["agent"] >= 3 and path not in user_paths:
            findings.append((
                path, info["type"], "confidence_escalation",
                f"agent={info['agent']}"
            ))
    return findings


def write_findings(drift_conn, findings):
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_count = 0
    for path, mem_type, drift_type, detail in findings:
        row = drift_conn.execute(
            "SELECT first_seen FROM drift_findings WHERE path = ? AND drift_type = ? AND detail = ?",
            (path, drift_type, detail)
        ).fetchone()

        if row:
            drift_conn.execute("""
                UPDATE drift_findings SET detected_at = ?,
                    first_seen = first_seen + 1, resolved_at = NULL, resolved_by = NULL
                WHERE path = ? AND drift_type = ? AND detail = ?
            """, (now_iso, path, drift_type, detail))
        else:
            drift_conn.execute("""
                INSERT INTO drift_findings (path, drift_type, detail, memory_type, detected_at, first_seen)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (path, drift_type, detail, mem_type, now_iso))
            new_count += 1
    drift_conn.commit()
    return new_count


def auto_resolve(drift_conn, findings):
    finding_keys = {(p, dt, d) for p, _, dt, d in findings}
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    existing = drift_conn.execute(
        "SELECT path, drift_type, detail FROM drift_findings WHERE resolved_at IS NULL"
    ).fetchall()

    resolved = 0
    for path, drift_type, detail in existing:
        if (path, drift_type, detail) not in finding_keys:
            drift_conn.execute("""
                UPDATE drift_findings SET resolved_at = ?, resolved_by = 'auto-resolve'
                WHERE path = ? AND drift_type = ? AND detail = ?
            """, (now_iso, path, drift_type, detail))
            resolved += 1
    drift_conn.commit()
    return resolved


def prune_orphans(drift_conn, index_conn):
    known_paths = set()
    for row in index_conn.execute("SELECT DISTINCT path FROM memory_chunks"):
        known_paths.add(row[0])

    all_drift_paths = drift_conn.execute(
        "SELECT DISTINCT path FROM drift_findings"
    ).fetchall()

    deleted = 0
    for (path,) in all_drift_paths:
        if path not in known_paths:
            drift_conn.execute("DELETE FROM drift_findings WHERE path = ?", (path,))
            deleted += 1
    drift_conn.commit()
    return deleted


def main():
    if len(sys.argv) < 2:
        print("Usage: drift_check.py <index_db_path>", file=sys.stderr)
        sys.exit(1)

    index_db_path = sys.argv[1]
    if not os.path.exists(index_db_path):
        sys.exit(0)

    drift_db_path = get_drift_db_path(index_db_path)

    index_conn = sqlite3.connect(index_db_path)
    index_conn.execute("PRAGMA journal_mode=WAL")
    index_conn.execute("PRAGMA busy_timeout=5000")

    drift_conn = init_drift_db(drift_db_path)

    if not should_run(drift_conn):
        active = drift_conn.execute(
            "SELECT COUNT(*) FROM drift_findings WHERE resolved_at IS NULL"
        ).fetchone()[0]
        print(f"Drift check: skipped (ran <24h ago). {active} active findings.")
        index_conn.close()
        drift_conn.close()
        return

    known_names = build_known_names(index_conn)

    all_findings = []
    all_findings.extend(check_wikilink_drift(index_conn, known_names))
    all_findings.extend(check_age_drift(index_conn))
    all_findings.extend(check_confidence_escalation(index_conn))

    pruned = prune_orphans(drift_conn, index_conn)
    resolved = auto_resolve(drift_conn, all_findings)
    new_count = write_findings(drift_conn, all_findings)

    record_run(drift_conn)

    active = drift_conn.execute(
        "SELECT COUNT(*) FROM drift_findings WHERE resolved_at IS NULL"
    ).fetchone()[0]

    print(f"Drift check: {len(all_findings)} detected, {new_count} new, {resolved} resolved, {pruned} pruned. {active} active.")
    index_conn.close()
    drift_conn.close()


if __name__ == "__main__":
    main()
