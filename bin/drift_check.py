#!/usr/bin/env python3
"""Eidetic v2.5 — Drift Detection

Detects stale memories via wikilink validation and age checks.
Writes findings to drift_findings table in index.db.
Runs at SessionStart (24h throttle). No file mutations.

Charter: P5 (quality tracked), P6 (system improves), P11 (contradictions surfaced).
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

DRIFT_PENALTIES = {
    "broken_wikilink": 0.8,
    "age_stale": 0.5,
    "confidence_escalation": 0.3,
}


def init_drift_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drift_findings (
            path TEXT NOT NULL,
            drift_type TEXT NOT NULL,
            memory_type TEXT,
            detail TEXT,
            detected_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by TEXT,
            first_seen INTEGER DEFAULT 1,
            PRIMARY KEY (path, drift_type)
        )
    """)
    conn.commit()


def should_run(conn):
    try:
        row = conn.execute(
            "SELECT mtime FROM index_meta WHERE path = '__drift_check__'"
        ).fetchone()
        if row and time.time() - row[0] < 86400:
            return False
    except sqlite3.OperationalError:
        pass
    return True


def record_run(conn):
    conn.execute(
        "INSERT OR REPLACE INTO index_meta (path, mtime) VALUES ('__drift_check__', ?)",
        (int(time.time()),)
    )
    conn.commit()


def build_known_names(conn):
    names = set()
    rows = conn.execute("SELECT DISTINCT path, name FROM memory_chunks").fetchall()
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
    for link in raw:
        target = link.split("|")[0].split("#")[0].strip()
        if target and "$" not in target and not re.search(
            r'(^|\s)(==|!=|-eq|-ne|-gt|-lt|-ge|-le)(\s|$)', target
        ):
            links.append(target)
    return links


def check_wikilink_drift(conn, known_names):
    rows = conn.execute("""
        SELECT DISTINCT path, type, content FROM memory_chunks
        WHERE content LIKE '%[[%'
    """).fetchall()

    findings = []
    for path, mem_type, content in rows:
        links = extract_wikilinks_from_content(content)
        for link in links:
            if link not in known_names:
                findings.append((path, mem_type, "broken_wikilink", f"[[{link}]] not found"))
    return findings


def check_age_drift(conn):
    now = datetime.utcnow()
    findings = []

    rows = conn.execute("""
        SELECT DISTINCT path, type, evidence, last_verified, mtime
        FROM memory_chunks
        WHERE evidence != 'hypothesis'
    """).fetchall()

    for path, mem_type, evidence, last_verified, mtime in rows:
        threshold = AGE_THRESHOLDS.get(mem_type or "", DEFAULT_AGE_DAYS)
        if threshold is None:
            continue

        if last_verified:
            try:
                verified_dt = datetime.fromisoformat(last_verified.replace("Z", "+00:00").replace("+00:00", ""))
            except (ValueError, AttributeError):
                verified_dt = None
        else:
            verified_dt = None

        if verified_dt is None and mtime:
            verified_dt = datetime.utcfromtimestamp(mtime)

        if verified_dt is None:
            continue

        age_days = (now - verified_dt).days
        if age_days > threshold:
            findings.append((
                path, mem_type, "age_stale",
                f"age={age_days}d threshold={threshold}d type={mem_type}"
            ))
    return findings


def check_confidence_escalation(conn):
    rows = conn.execute("""
        SELECT path, type, content FROM memory_chunks
        WHERE source = 'agent-extracted'
    """).fetchall()

    path_counts = {}
    for path, mem_type, content in rows:
        path_counts.setdefault(path, {"agent": 0, "type": mem_type})
        path_counts[path]["agent"] += 1

    user_paths = set()
    for row in conn.execute("SELECT DISTINCT path FROM memory_chunks WHERE source = 'user-explicit'"):
        user_paths.add(row[0])

    findings = []
    for path, info in path_counts.items():
        if info["agent"] >= 3 and path not in user_paths:
            findings.append((
                path, info["type"], "confidence_escalation",
                f"agent_updates={info['agent']} user_updates=0"
            ))
    return findings


def write_findings(conn, findings):
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_count = 0
    for path, mem_type, drift_type, detail in findings:
        row = conn.execute(
            "SELECT first_seen FROM drift_findings WHERE path = ? AND drift_type = ?",
            (path, drift_type)
        ).fetchone()

        if row:
            conn.execute("""
                UPDATE drift_findings SET detail = ?, detected_at = ?,
                    first_seen = first_seen + 1, resolved_at = NULL, resolved_by = NULL
                WHERE path = ? AND drift_type = ?
            """, (detail, now_iso, path, drift_type))
        else:
            conn.execute("""
                INSERT INTO drift_findings (path, drift_type, memory_type, detail, detected_at, first_seen)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (path, drift_type, mem_type, detail, now_iso))
            new_count += 1
    conn.commit()
    return new_count


def auto_resolve(conn, findings):
    finding_keys = {(path, drift_type) for path, _, drift_type, _ in findings}
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    existing = conn.execute(
        "SELECT path, drift_type FROM drift_findings WHERE resolved_at IS NULL"
    ).fetchall()

    resolved = 0
    for path, drift_type in existing:
        if (path, drift_type) not in finding_keys:
            conn.execute("""
                UPDATE drift_findings SET resolved_at = ?, resolved_by = 'auto-resolve'
                WHERE path = ? AND drift_type = ?
            """, (now_iso, path, drift_type))
            resolved += 1
    conn.commit()
    return resolved


def prune_orphan_findings(conn):
    deleted = conn.execute("""
        DELETE FROM drift_findings
        WHERE path NOT IN (SELECT DISTINCT path FROM memory_chunks)
    """).rowcount
    conn.commit()
    return deleted


def main():
    if len(sys.argv) < 2:
        print("Usage: drift_check.py <db_path>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    if not os.path.exists(db_path):
        sys.exit(0)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    init_drift_table(conn)

    if not should_run(conn):
        active = conn.execute(
            "SELECT COUNT(*) FROM drift_findings WHERE resolved_at IS NULL"
        ).fetchone()[0]
        print(f"Drift check: skipped (ran <24h ago). {active} active findings.")
        conn.close()
        return

    known_names = build_known_names(conn)

    all_findings = []
    all_findings.extend(check_wikilink_drift(conn, known_names))
    all_findings.extend(check_age_drift(conn))
    all_findings.extend(check_confidence_escalation(conn))

    pruned = prune_orphan_findings(conn)
    resolved = auto_resolve(conn, all_findings)
    new_count = write_findings(conn, all_findings)

    record_run(conn)

    active = conn.execute(
        "SELECT COUNT(*) FROM drift_findings WHERE resolved_at IS NULL"
    ).fetchone()[0]

    print(f"Drift check: {len(all_findings)} detected, {new_count} new, {resolved} resolved, {pruned} pruned. {active} active.")
    conn.close()


if __name__ == "__main__":
    main()
