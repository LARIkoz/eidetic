#!/usr/bin/env python3
"""Operator recall regression smoke for agent-facing memory quality.

This uses the real local memory index. It is intentionally separate from CI:
the cases assert important user-corpus memories and should be updated when the
operator corpus changes.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}

CASES = [
    # Minimal GENERIC skeleton — probes Eidetic's OWN indexed code (present in any
    # install) plus a negative control. Operators SHOULD extend this with cases that
    # assert their own important memories, kept OUT of this public repo (never commit
    # private project / infra / provider names or key counts here).
    {
        "name": "drift_check_code",
        "query": "drift_check auto_resolve",
        "type": "code",
        "expect_any": ["drift_check.py", "auto_resolve"],
        "min_confidence": "medium",
    },
    {
        "name": "search_confidence_code",
        "query": "classify retrieval confidence vector semantic match",
        "type": "code",
        "expect_any": ["_classify_confidence", "semantic match"],
        "min_confidence": "high",
    },
    {
        "name": "agent_schema_migration_code",
        "query": "ensure agent columns card_kind status superseded_by",
        "type": "code",
        "expect_any": ["ensure_agent_columns", "card_kind"],
        "min_confidence": "high",
    },
    # NB: an expect_no_confident probe must stay ABSENT from the indexed corpus, else
    # FTS phrase-matches it and the negative self-defeats. Keep it nonsense/unique.
    {
        "name": "code_no_confident_result",
        "query": "nonexistent_function_zzzz",
        "type": "code",
        "expect_no_confident": True,
    },
]


def confidence_at_least(actual, expected):
    return CONFIDENCE_ORDER.get(actual, 0) >= CONFIDENCE_ORDER[expected]


def run_search(script_dir, db_path, case):
    cmd = [
        sys.executable,
        str(script_dir / "search_impl.py"),
        str(db_path),
        case["query"],
        "--limit",
        str(case.get("limit", 5)),
        "--json-object",
    ]
    if case.get("type"):
        cmd.extend(["--type", case["type"]])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"search timed out after {exc.timeout}s") from exc
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("--json-object returned non-object JSON")
    if "results" not in payload or "no_confident_results" not in payload:
        raise RuntimeError("--json-object missing required contract fields")
    return payload


def result_text(result):
    fields = [
        result.get("path", ""),
        result.get("name", ""),
        result.get("section", ""),
        result.get("snippet", ""),
    ]
    return " ".join(fields).lower()


def evaluate(case, payload):
    results = payload.get("results", [])
    if case.get("expect_no_confident"):
        if payload.get("no_confident_results") is not True:
            return False, "expected no_confident_results=true"
        confident = [
            r for r in results
            if CONFIDENCE_ORDER.get(r.get("confidence", "low"), 0) >= CONFIDENCE_ORDER["medium"]
        ]
        if confident:
            top = confident[0]
            return False, f"unexpected confident result: {top.get('path')} ({top.get('confidence')})"
        return True, "no confident result"

    if payload.get("no_confident_results") is not False:
        return False, "positive case returned no_confident_results=true"

    expected = [s.lower() for s in case["expect_any"]]
    min_confidence = case.get("min_confidence", "medium")
    for result in results:
        haystack = result_text(result)
        if any(token in haystack for token in expected):
            confidence = result.get("confidence", "low")
            if confidence_at_least(confidence, min_confidence):
                return True, f"{result.get('path')} ({confidence})"
            return False, (
                f"matched expected item but confidence={confidence}, "
                f"expected>={min_confidence}: {result.get('path')}"
            )

    return False, "expected item not found"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.path.expanduser("~/.claude/memory-system/db/index.db"),
        help="Path to index.db",
    )
    parser.add_argument(
        "--case",
        choices=[case["name"] for case in CASES],
        help="Run one case only",
    )
    args = parser.parse_args()

    # Benchmark searches must NOT write the prod usage.log — they would poison the
    # dead-card / top-used value telemetry. Redirect this process's usage log to temp.
    os.environ.setdefault("EIDETIC_USAGE_LOG_PATH",
                          os.path.join(os.environ.get("TMPDIR", "/tmp"), "eidetic_recall_usage.log"))

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"ERROR: index not found: {db_path}", file=sys.stderr)
        return 1

    script_dir = Path(__file__).resolve().parent
    cases = [case for case in CASES if not args.case or case["name"] == args.case]

    failed = 0
    for case in cases:
        try:
            results = run_search(script_dir, db_path, case)
            ok, detail = evaluate(case, results)
        except Exception as exc:
            ok = False
            detail = str(exc)

        marker = "PASS" if ok else "FAIL"
        print(f"{marker} {case['name']}: {detail}")
        if not ok:
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
