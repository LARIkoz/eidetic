import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEARCH_IMPL = ROOT / "bin" / "search_impl.py"


SCHEMA = """
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    project TEXT,
    name TEXT,
    type TEXT,
    evidence TEXT DEFAULT 'observed',
    source TEXT DEFAULT 'user-explicit',
    confidence REAL DEFAULT 0.7,
    last_verified TEXT,
    card_kind TEXT DEFAULT '',
    status TEXT DEFAULT 'current',
    area TEXT DEFAULT '',
    supersedes TEXT DEFAULT '',
    superseded_by TEXT DEFAULT '',
    section_heading TEXT,
    content TEXT NOT NULL,
    description TEXT,
    mtime INTEGER,
    UNIQUE(path, section_heading)
);
CREATE VIRTUAL TABLE memory_fts USING fts5(
    name, description, section_heading, content,
    content='memory_chunks',
    content_rowid='id'
);
CREATE TRIGGER memory_chunks_ai AFTER INSERT ON memory_chunks BEGIN
    INSERT INTO memory_fts(rowid, name, description, section_heading, content)
    VALUES (new.id, new.name, new.description, new.section_heading, new.content);
END;
"""


DETAIL_CONTENT = (
    "SENTINEL_FULL_CONTENT rotation policy requires two approvers, bounded "
    "blast radius, and a dated rollback note."
)


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    fresh_date = date.today().isoformat()
    rows = [
        (
            "/tmp/eidetic-test/key-rotation.md",
            "eidetic-test",
            "Key Rotation Decision",
            "feedback",
            "validated",
            "user-explicit",
            0.95,
            fresh_date,
            "decision",
            "current",
            "security",
            "",
            "",
            "Policy",
            DETAIL_CONTENT,
            "rotation policy decision",
            1,
        ),
        (
            "/tmp/eidetic-test/rotation-runbook.md",
            "eidetic-test",
            "Rotation Runbook",
            "reference",
            "observed",
            "user-explicit",
            0.8,
            fresh_date,
            "reference",
            "current",
            "security",
            "",
            "",
            "Checklist",
            "Rotation checklist covers owner handoff and audit logging.",
            "rotation checklist",
            1,
        ),
    ]
    conn.executemany(
        """
        INSERT INTO memory_chunks
            (path, project, name, type, evidence, source, confidence,
             last_verified, card_kind, status, area, supersedes, superseded_by,
             section_heading, content, description, mtime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


def run_search(db_path, *args):
    return subprocess.run(
        [sys.executable, str(SEARCH_IMPL), str(db_path), *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    ).stdout


class ProgressiveSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "index.db"
        make_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_object_keeps_contract_and_adds_detail_id(self):
        payload = json.loads(run_search(self.db_path, "rotation policy", "--json-object", "--limit", "5"))
        self.assertIs(payload["no_confident_results"], False)
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        top = payload["results"][0]
        self.assertIn("snippet", top)
        self.assertIn("detail_id", top)
        self.assertTrue(top["detail_id"].startswith("mem_"))

    def test_broad_default_is_compact_and_full_restores_snippet(self):
        compact = run_search(self.db_path, "rotation", "--limit", "5")
        self.assertIn("Compact broad-query results", compact)
        self.assertIn("confidence=high", compact)
        self.assertIn("id=mem_", compact)
        self.assertNotIn("SENTINEL_FULL_CONTENT", compact)

        full = run_search(self.db_path, "rotation", "--limit", "5", "--full")
        self.assertIn("Detail id: mem_", full)
        self.assertIn("SENTINEL_FULL_CONTENT", full)

    def test_detail_by_id_and_path_returns_full_content(self):
        payload = json.loads(run_search(self.db_path, "rotation policy", "--json-object", "--limit", "5"))
        detail_id = payload["results"][0]["detail_id"]

        by_id = json.loads(run_search(self.db_path, "--detail", detail_id, "--json-object"))
        self.assertIs(by_id["found"], True)
        self.assertIs(by_id["no_confident_results"], False)
        self.assertEqual(by_id["results"][0]["content"], DETAIL_CONTENT)

        by_path = json.loads(
            run_search(self.db_path, "--detail", "/tmp/eidetic-test/key-rotation.md", "--json-object")
        )
        self.assertIs(by_path["found"], True)
        self.assertEqual(by_path["results"][0]["detail_id"], detail_id)
        self.assertEqual(by_path["results"][0]["content"], DETAIL_CONTENT)

    def test_missing_and_empty_detail_selectors_keep_structured_contract(self):
        missing = json.loads(run_search(self.db_path, "--detail", "mem_missing000000", "--json-object"))
        self.assertIs(missing["found"], False)
        self.assertIs(missing["no_confident_results"], True)
        self.assertEqual(missing["results"], [])

        for args in [
            ("--detail", ""),
            ("--detail",),
            ("--detail", "--json-object"),
        ]:
            result = subprocess.run(
                [sys.executable, str(SEARCH_IMPL), str(self.db_path), *args],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("detail selector is required", result.stderr)

    def test_mcp_detail_tool_uses_structured_payload(self):
        payload = json.loads(run_search(self.db_path, "rotation policy", "--json-object", "--limit", "5"))
        detail_id = payload["results"][0]["detail_id"]

        spec = importlib.util.spec_from_file_location("eidetic_mcp_server_test", ROOT / "mcp_server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.BIN = str(ROOT / "bin")
        module.INDEX_DB = str(self.db_path)

        response = module.handle_search_detail({"selector": detail_id})
        structured = response["_mcp_result"]["structuredContent"]
        self.assertIs(structured["found"], True)
        self.assertEqual(structured["results"][0]["content"], DETAIL_CONTENT)


class VectorConfidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("eidetic_search_impl_test", SEARCH_IMPL)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_ascii_vector_only_result_requires_strict_similarity(self):
        level, reason = self.module._classify_confidence({
            "match": "vector",
            "match_quality": 0.56,
            "vector_score": 0.56,
            "vector_profile": "strict",
            "source": "user-explicit",
            "freshness": 1.0,
            "status": "current",
        })

        self.assertEqual(level, "low")
        self.assertIn("weak", reason)

    def test_multilingual_vector_only_result_uses_relaxed_similarity(self):
        level, reason = self.module._classify_confidence({
            "match": "vector",
            "match_quality": 0.56,
            "vector_score": 0.56,
            "vector_profile": "multilingual",
            "source": "user-explicit",
            "freshness": 1.0,
            "status": "current",
        })

        self.assertEqual(level, "high")
        self.assertIn("semantic", reason)

    def test_or_match_requires_substantial_term_coverage(self):
        medium, _ = self.module._classify_confidence({
            "match": "or",
            "match_quality": 0.8,
            "source": "user-explicit",
            "freshness": 1.0,
            "status": "current",
        })
        low, _ = self.module._classify_confidence({
            "match": "or",
            "match_quality": 0.667,
            "source": "user-explicit",
            "freshness": 1.0,
            "status": "current",
        })

        self.assertEqual(medium, "medium")
        self.assertEqual(low, "low")


if __name__ == "__main__":
    unittest.main()
