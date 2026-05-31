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
        "name": "gemini_acc2_home_override",
        "query": "gemini cli account 2 HOME override",
        "expect_any": ["bug_gemini_cli_acc2_switch", "home override"],
        "min_confidence": "high",
    },
    {
        "name": "gemini_batch_double_cap",
        "query": "gemini batch two env overrides parallel Pro",
        "expect_any": ["bug_gemini_batch_double_cap", "two env overrides"],
        "min_confidence": "high",
    },
    {
        "name": "key_penalty_store",
        "query": "key penalty store shared key_penalty.db route-scoped failures",
        "expect_any": ["key-penalty-store", "key_penalty.db"],
        "min_confidence": "high",
    },
    {
        "name": "model_split_opus46_47",
        "query": "Model split Opus 4.6 4.7 sub-agent",
        "expect_any": ["feedback-model-split", "opus 4.6"],
        "min_confidence": "high",
    },
    {
        "name": "consilium_partial_failure",
        "query": "bug consilium voices partial failure 2026-04-28",
        "expect_any": ["bug_consilium_voices_fail", "partial failure"],
        "min_confidence": "high",
    },
    {
        "name": "sync_skills_private_bug",
        "query": "sync skills PRIVATE_SKILLS bug",
        "expect_any": ["private_skills", "sync-skills-private"],
        "min_confidence": "medium",
    },
    {
        "name": "dashscope_400_all_keys",
        "query": "DashScope 400 all 110 keys",
        "expect_any": ["bug_dashscope_400_all_keys", "dashscope 400"],
        "min_confidence": "high",
    },
    {
        "name": "deepseek_json_truncation",
        "query": "DeepSeek R1 truncates JSON taxonomy prompts",
        "expect_any": ["bug_deepseek_r1_truncation", "truncates json"],
        "min_confidence": "high",
    },
    {
        "name": "llm_consilium_parse_failure",
        "query": "LLMConsiliumClient classify parse failure Cohere MiniMax Reka",
        "expect_any": ["bug_llm_consilium_classify_parse", "parse failure"],
        "min_confidence": "high",
    },
    {
        "name": "gap_malformed_platform_predicate",
        "query": "gap pipeline live DB malformed platform predicate",
        "expect_any": ["bug_gap_pipeline_live_db_malformed", "malformed platform"],
        "min_confidence": "high",
    },
    {
        "name": "zamesin_methodology_skill",
        "query": "Zamesin AJTBD methodology router",
        "expect_any": ["zamesin-methodology", "ajtbd"],
        "min_confidence": "high",
    },
    {
        "name": "provider_admission_contract",
        "query": "provider route admission exact model degraded blocked",
        "expect_any": ["provider-admission", "route admission"],
        "min_confidence": "high",
    },
    {
        "name": "memory_recall_skill",
        "query": "Memory Recall Search the FTS5 memory index",
        "expect_any": ["memory-recall", "fts5 memory index"],
        "min_confidence": "high",
    },
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
    {
        "name": "code_no_confident_result",
        "query": "nonexistent_function_zzzz",
        "type": "code",
        "expect_no_confident": True,
    },
    {
        "name": "x_web_public_indexed_research",
        "query": "x-web KeyPenaltyStore x_web_murphy_check public indexed X",
        "expect_any": ["x-web", "keypenaltystore"],
        "min_confidence": "medium",
    },
    {
        "name": "suppress_missing_database_mock_rule",
        "query": "do not mock database tests",
        "expect_no_confident": True,
    },
    {
        # Cross-lingual recall guard: a Russian paraphrase with no shared verb
        # ("what replaces claude batch after June 15") must still surface the
        # English memory via vector + lexical corroboration (anchors: claude,
        # batch, 15). Locks in the e5 win against the two-signal precision gate.
        "name": "ru_paraphrase_claude_voice",
        "query": "то что заменяет claude batch после 15 июня",
        "expect_any": ["feedback-claude-voice-after-june15", "claude-voice"],
        "min_confidence": "medium",
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
