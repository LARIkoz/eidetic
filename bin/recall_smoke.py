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
    {
        "name": "large_prompt_bug",
        "query": "claude batch large prompts",
        "expect_any": ["bug_claude_batch_large_prompt", "large prompts"],
        "min_confidence": "high",
    },
    {
        "name": "gap_pipeline_niche_definition",
        "query": "gap pipeline niche definition",
        "expect_any": ["gap_pipeline_principles", "niche-definition"],
        "min_confidence": "high",
    },
    {
        "name": "obsidian_best_practices",
        "query": "obsidian vault structure best practices",
        "expect_any": ["obsidian_vault_structure_best_practices"],
        "min_confidence": "high",
    },
    {
        "name": "suppress_missing_database_mock_rule",
        "query": "do not mock database tests",
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
        "--json",
    ]
    if case.get("type"):
        cmd.extend(["--type", case["type"]])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return json.loads(result.stdout)


def result_text(result):
    fields = [
        result.get("path", ""),
        result.get("name", ""),
        result.get("section", ""),
        result.get("snippet", ""),
    ]
    return " ".join(fields).lower()


def evaluate(case, results):
    if case.get("expect_no_confident"):
        confident = [
            r for r in results
            if CONFIDENCE_ORDER.get(r.get("confidence", "low"), 0) >= CONFIDENCE_ORDER["medium"]
        ]
        if confident:
            top = confident[0]
            return False, f"unexpected confident result: {top.get('path')} ({top.get('confidence')})"
        return True, "no confident result"

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
