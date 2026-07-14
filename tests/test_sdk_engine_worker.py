import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKER_PATH = ROOT / "bin" / "eidetic_engine_worker.py"
SPEC = importlib.util.spec_from_file_location("eidetic_engine_worker_test", WORKER_PATH)
worker_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker_module)


class FakeEngineUnavailable(RuntimeError):
    pass


class FakeIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            "chunk_id INTEGER PRIMARY KEY, path TEXT, name TEXT, section_heading TEXT, "
            "content_hash TEXT, embedding BLOB, mtime REAL DEFAULT 0)"
        )
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def existing_hashes(self):
        return dict(self.conn.execute("SELECT chunk_id, content_hash FROM vectors"))

    def upsert(self, rows):
        for row in rows:
            self.conn.execute(
                "INSERT OR REPLACE INTO vectors "
                "(chunk_id,path,name,section_heading,content_hash,embedding,mtime) "
                "VALUES(?,?,?,?,?,?,0)",
                (
                    row["chunk_id"], row["path"], row["name"],
                    row["section_heading"], row["content_hash"], row["embedding"],
                ),
            )
        self.conn.commit()
        return len(rows)

    def delete(self, ids):
        before = self.conn.total_changes
        self.conn.executemany("DELETE FROM vectors WHERE chunk_id=?", [(value,) for value in ids])
        self.conn.commit()
        return self.conn.total_changes - before

    def stamp(self):
        for key, value in (
            ("model", "fake-model"), ("dim", "1"),
            ("hash_scheme", "fake-hash"), ("fastembed_version", "test"),
        ):
            self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()

    def search(self, query, limit=5):
        print("Fetching model files: 100%", file=sys.stderr)
        rows = self.conn.execute(
            "SELECT chunk_id,path,name,section_heading,content_hash FROM vectors ORDER BY chunk_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "score": 1.0,
                "chunk_id": row[0],
                "path": row[1],
                "name": row[2],
                "section_heading": row[3],
                "content_hash": row[4],
            }
            for row in rows
        ]


class FakeLock:
    def close(self):
        return None


class FakeEngine:
    ENGINE_API = "1.1"
    EngineUnavailable = FakeEngineUnavailable

    @staticmethod
    def model_info():
        return {
            "model": "fake-model", "dim": 1, "hash_scheme": "fake-hash",
            "fastembed": "test", "engine_api": "1.1", "profile": "test",
        }

    @staticmethod
    def content_hash(name, desc, content, heading):
        import hashlib
        return hashlib.sha256("\0".join((name, desc, content, heading)).encode()).hexdigest()

    @staticmethod
    def embedding_text(name, desc, content, heading):
        return "\n".join((name, desc, content, heading))

    @staticmethod
    def embed_passages(texts):
        return [text.encode("utf-8") for text in texts]

    @staticmethod
    def acquire_build_lock(path):
        return FakeLock()

    @staticmethod
    def open_index(path):
        return FakeIndex(path)

    @staticmethod
    def rerank(query, docs):
        return [float(len(text)) for text in docs]


def request(operation, payload=None, version="1.0"):
    return {
        "protocol": "eidetic.engine",
        "version": version,
        "request_id": "test-request",
        "operation": operation,
        "payload": payload or {},
    }


def record(record_id="task:1#0", object_id="task-1", text="hello"):
    return {
        "record_id": record_id,
        "object_id": object_id,
        "title": "Example",
        "kind": "task",
        "text": text,
    }


class EngineWorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worker = worker_module.EngineWorker(
            engine_module=FakeEngine,
            runtime_root=self.root,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_capabilities_are_path_free(self):
        response = self.worker.handle(request("capabilities"))
        self.assertTrue(response["ok"])
        encoded = json.dumps(response)
        self.assertNotIn(str(self.root), encoded)
        self.assertEqual(response["result"]["storage_identity"], "logical-index-only")
        self.assertEqual(response["result"]["engine_api"], "1.1")

    def test_reconcile_absent_index_is_read_only(self):
        response = self.worker.handle(request("reconcile", {
            "index_id": "fixture", "records": [record()], "snapshot": "complete",
        }))
        self.assertTrue(response["ok"])
        receipt = response["result"]["receipt"]
        self.assertEqual(receipt["status"], "stale")
        self.assertEqual(receipt["missing"], 1)
        self.assertFalse((self.root / "sdk-state").exists())

    def test_sync_is_idempotent_and_reconcile_becomes_fresh(self):
        payload = {"index_id": "fixture", "records": [record()], "snapshot": "complete"}
        first = self.worker.handle(request("sync", payload))
        second = self.worker.handle(request("sync", payload))
        reconciled = self.worker.handle(request("reconcile", payload))
        self.assertTrue(first["ok"])
        self.assertEqual(first["result"]["receipt"]["upserted"], 1)
        self.assertEqual(second["result"]["receipt"]["upserted"], 0)
        self.assertEqual(
            first["result"]["receipt"]["source_digest"],
            second["result"]["receipt"]["source_digest"],
        )
        self.assertEqual(reconciled["result"]["receipt"]["status"], "fresh")

    def test_partial_snapshot_never_deletes(self):
        full = {
            "index_id": "fixture",
            "records": [record(), record("task:2#0", "task-2", "second")],
            "snapshot": "complete",
        }
        self.worker.handle(request("sync", full))
        partial = {"index_id": "fixture", "records": [record()], "snapshot": "partial"}
        response = self.worker.handle(request("sync", partial))
        self.assertEqual(response["result"]["receipt"]["deleted"], 0)
        health = self.worker.handle(request("health", {"index_id": "fixture"}))
        self.assertEqual(health["result"]["index"]["vectors"], 2)

    def test_complete_snapshot_deletes_orphans(self):
        full = {
            "index_id": "fixture",
            "records": [record(), record("task:2#0", "task-2", "second")],
            "snapshot": "complete",
        }
        self.worker.handle(request("sync", full))
        reduced = {"index_id": "fixture", "records": [record()], "snapshot": "complete"}
        response = self.worker.handle(request("sync", reduced))
        self.assertEqual(response["result"]["receipt"]["deleted"], 1)

    def test_search_returns_source_owned_fields_only(self):
        payload = {"index_id": "fixture", "records": [record()], "snapshot": "complete"}
        self.worker.handle(request("sync", payload))
        response = self.worker.handle(request("search", {
            "index_id": "fixture", "query": "hello", "limit": 3,
        }))
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["status"], "ok")
        hit = response["result"]["hits"][0]
        self.assertEqual(set(hit), {"score", "object_id", "title", "kind"})

    def test_version_and_index_id_fail_closed(self):
        incompatible = self.worker.handle(request("capabilities", version="2.0"))
        invalid_id = self.worker.handle(request("health", {"index_id": "../private"}))
        false_idempotency = self.worker.handle(request("sync", {
            "index_id": "fixture",
            "records": [record()],
            "snapshot": "complete",
            "idempotency_key": "not-an-engine-v1-field",
        }))
        self.assertEqual(incompatible["error"]["code"], "incompatible_version")
        self.assertEqual(invalid_id["error"]["code"], "invalid_request")
        self.assertEqual(false_idempotency["error"]["code"], "invalid_request")
        self.assertNotIn(str(self.root), json.dumps(invalid_id))

    def test_core_python_never_imports_sdk(self):
        offenders = []
        for path in list((ROOT / "bin").glob("*.py")) + [ROOT / "mcp_server.py"]:
            text = path.read_text(encoding="utf-8")
            if "import eidetic_sdk" in text or "from eidetic_sdk" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_contract_json_is_valid_and_docs_match_engine_api(self):
        for path in (ROOT / "schemas" / "sdk" / "engine" / "v1").glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))
        engine_text = (ROOT / "bin" / "engine.py").read_text(encoding="utf-8")
        docs_text = (ROOT / "docs" / "engine.md").read_text(encoding="utf-8")
        self.assertIn('ENGINE_API = "1.1"', engine_text)
        self.assertIn('ENGINE_API = "1.1"', docs_text)


if __name__ == "__main__":
    unittest.main()
