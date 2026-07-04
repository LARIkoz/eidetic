"""Contract tests for the public Engine API v1 (bin/engine.py).

Both modes are NATIVE on this box: Leg A (venv py3.12 + fastembed) exercises the
vectored path; Leg B (/usr/bin/python3, no fastembed) is the degrade mode — no
monkeypatching needed there, absence is real. The forced-degrade test (3) also
blocks fastembed so it runs meaningfully on Leg A too.

unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import engine  # noqa: E402


def _fastembed_available():
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None


VECTORED_ONLY = "vectored-mode contract test: requires fastembed (Leg A)"

# CPU-pin every model load in this file — CoreML OOMs the e5 embedder on this
# box (STEP-0 fact); configure() is itself part of the contract under test.
engine.configure(provider="cpu", threads=8)


class SurfaceAndVersionTest(unittest.TestCase):
    """FR-4.1 — surface, version, metadata (both modes, no model load)."""

    def test_1_exports_and_require_and_model_info(self):
        for name in (
            "ENGINE_API", "EngineUnavailable", "require", "model_info", "configure",
            "embedding_text", "content_hash", "embed_passages", "embed_query",
            "acquire_build_lock", "open_index", "Index", "rerank",
            "embed_query_batch", "profile",  # v1.1 (S1, S3)
        ):
            self.assertTrue(hasattr(engine, name), f"missing public export {name!r}")

        self.assertEqual(engine.ENGINE_API, "1.1")
        engine.require("1")  # v1.1: major still "1" → no-op
        with self.assertRaises(engine.EngineUnavailable):
            engine.require("2")

        info = engine.model_info()  # must never load the model / raise
        # AC-9: "profile" is additively added in v1.1 — the golden set extends.
        self.assertEqual(set(info),
                         {"model", "dim", "hash_scheme", "fastembed", "engine_api", "profile"})
        self.assertIsInstance(info["dim"], int)
        self.assertEqual(info["engine_api"], "1.1")
        self.assertEqual(info["profile"], engine.profile())
        # content_hash / embedding_text are model-free and must work on both legs.
        h = engine.content_hash("n", "d", "body", "sec")
        self.assertEqual(h, engine.content_hash("n", "d", "body", "sec"))
        self.assertIn("n", engine.embedding_text("n", "d", "body", "sec"))


class VectoredContractTest(unittest.TestCase):
    """FR-4.2 — real embed → upsert → stamp → search, plus the drift drill."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="engine-vec-")
        self.db = os.path.join(self.dir, "vectors.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    @unittest.skipUnless(_fastembed_available(), VECTORED_ONLY)
    def test_2_roundtrip_and_drift(self):
        passages = [
            "the indexer performs an incremental rebuild after every vacuum",
            "политика ротации ключей требует ежедневной замены",  # RU passage
            "kubernetes ingress certificate renewal timed out",
        ]
        blobs = engine.embed_passages(passages)
        self.assertEqual(len(blobs), 3)
        self.assertEqual(len(blobs[0]) // 4, engine.model_info()["dim"])

        idx = engine.open_index(self.db)
        rows = [
            {
                "chunk_id": i + 1,
                "path": f"/mem/card-{i}.md",
                "name": f"card-{i}",
                "section_heading": f"card-{i}",
                "content_hash": engine.content_hash(f"card-{i}", "", passages[i], f"card-{i}"),
                "embedding": blobs[i],
            }
            for i in range(3)
        ]
        self.assertEqual(idx.upsert(rows), 3)
        idx.stamp()
        self.assertEqual(idx.stats()["vectors"], 3)
        self.assertEqual(idx.existing_hashes()[2], rows[1]["content_hash"])

        # top-1 for an English paraphrase of card-0.
        hits = idx.search("incremental reindex rebuild of the index", limit=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["chunk_id"], 1)
        self.assertEqual(hits[0]["name"], "card-0")
        # cross-lingual: an English query still retrieves the RU card in the set.
        ru_hits = idx.search("key rotation policy daily", limit=3)
        self.assertIn(2, [h["chunk_id"] for h in ru_hits])
        idx.close()

        # Drift drill: tamper the model stamp → search must go SOFT ([]) + warn.
        con = sqlite3.connect(self.db)
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('model', 'WRONG/model')")
        con.commit()
        con.close()
        idx2 = engine.open_index(self.db)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            drifted = idx2.search("incremental rebuild", limit=3)
        idx2.close()
        self.assertEqual(drifted, [], "drifted stamp must return no hits")
        self.assertTrue(err.getvalue().strip(), "drift must print a stderr reason")

    @unittest.skipUnless(_fastembed_available(), VECTORED_ONLY)
    def test_ac8_embed_query_batch_and_neighbors(self):
        # S1: query-batch → aligned float32 blobs.
        qb = engine.embed_query_batch(["alpha probe", "beta probe"])
        self.assertEqual(len(qb), 2)
        self.assertEqual(len(qb[0]) // 4, engine.model_info()["dim"])

        # Build a tiny store and probe S2 neighbors.
        passages = ["use postgres for the primary store",
                    "use mysql for the primary store",
                    "kubernetes ingress certificate renewal"]
        blobs = engine.embed_passages(passages)
        idx = engine.open_index(self.db)
        idx.upsert([
            {"chunk_id": i + 1, "path": f"/mem/c{i}.md", "name": f"c{i}",
             "section_heading": f"c{i}",
             "content_hash": engine.content_hash(f"c{i}", "", passages[i], f"c{i}"),
             "embedding": blobs[i]}
            for i in range(3)])
        idx.stamp()
        # neighbors of the stored card-0 EXCLUDE self, share search()'s hit shape.
        hits = idx.neighbors(probe_chunk_id=1, limit=5)
        self.assertTrue(hits)
        self.assertNotIn(1, [h["chunk_id"] for h in hits], "neighbors must exclude self")
        self.assertEqual(set(hits[0]),
                         {"score", "chunk_id", "path", "name", "section_heading", "content_hash"})
        # the mysql card is the nearest neighbor of the postgres card (moderate cos).
        self.assertEqual(hits[0]["chunk_id"], 2)
        # probe by text also works and excludes the given path.
        thits = idx.neighbors(probe_text="use postgres", exclude_paths={"/mem/c0.md"}, limit=5)
        self.assertNotIn("/mem/c0.md", [h["path"] for h in thits])
        idx.close()


class ForcedDegradeTest(unittest.TestCase):
    """FR-4.3 — with the embedder runtime blocked: build raises, reads go soft."""

    def test_3_build_raises_reads_soft(self):
        emb = engine._embed()
        rr = engine._rerank()
        saved_fe = sys.modules.get("fastembed")
        saved_model = getattr(emb, "_model", None)
        saved_unavail = getattr(rr, "_unavailable", False)
        emb._model = None
        rr._model = None
        rr._unavailable = False
        sys.modules["fastembed"] = None  # force ImportError on the lazy import
        tmp = tempfile.mkdtemp(prefix="engine-degrade-")
        try:
            with self.assertRaises(engine.EngineUnavailable):
                engine.embed_passages(["hello world"])

            idx = engine.open_index(os.path.join(tmp, "v.db"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                soft = idx.search("hello", limit=3)
            idx.close()
            self.assertEqual(soft, [])
            self.assertTrue(err.getvalue().strip())

            self.assertEqual(engine.rerank("q", ["d1", "d2"]), [])
        finally:
            import shutil
            if saved_fe is not None:
                sys.modules["fastembed"] = saved_fe
            else:
                sys.modules.pop("fastembed", None)
            emb._model = saved_model
            rr._unavailable = saved_unavail
            shutil.rmtree(tmp, ignore_errors=True)


class BuildLockTest(unittest.TestCase):
    """FR-4.4 — the second lock on the same path is refused (both modes)."""

    def test_4_second_lock_is_none(self):
        tmp = tempfile.mkdtemp(prefix="engine-lock-")
        db = os.path.join(tmp, "vectors.db")
        try:
            h1 = engine.acquire_build_lock(db)
            self.assertIsNotNone(h1, "first build lock must be acquired")
            h2 = engine.acquire_build_lock(db)
            self.assertIsNone(h2, "second concurrent build lock must be refused")
            if hasattr(h1, "close"):
                h1.close()
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class BackCompatTest(unittest.TestCase):
    """FR-4.5 / AC-1.3 — a vectors.db built by embed.py run_full opens + searches
    through the door with no reindex and no schema change."""

    @unittest.skipUnless(_fastembed_available(), VECTORED_ONLY)
    def test_5_embed_run_full_fixture_opens_and_searches(self):
        tmp = tempfile.mkdtemp(prefix="engine-bc-")
        try:
            index_db = os.path.join(tmp, "index.db")
            vectors_db = os.path.join(tmp, "vectors.db")
            con = sqlite3.connect(index_db)
            con.execute(
                "CREATE TABLE memory_chunks (id INTEGER PRIMARY KEY, path TEXT, name TEXT, "
                "description TEXT, content TEXT, section_heading TEXT, mtime INTEGER)"
            )
            cards = [
                (1, "/mem/rotation.md", "rotation-policy", "", "the flarnpuzzle rotation policy rotates keys daily", "rotation-policy", 1),
                (2, "/mem/indexer.md", "indexer-rebuild", "", "the indexer performs an incremental rebuild after vacuum", "indexer-rebuild", 1),
                (3, "/mem/ingress.md", "ingress-cert", "", "kubernetes ingress certificate renewal responder", "ingress-cert", 1),
            ]
            con.executemany(
                "INSERT INTO memory_chunks (id, path, name, description, content, section_heading, mtime) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", cards)
            con.commit()
            con.close()

            # Build the vectors.db with the REAL private builder (not the door).
            with contextlib.redirect_stdout(io.StringIO()):
                engine._embed().run_full(index_db, vectors_db)

            # Open the pre-built db through the door and search — no reindex.
            idx = engine.open_index(vectors_db)
            self.assertEqual(idx.stats()["vectors"], 3)
            hits = idx.search("flarnpuzzle key rotation", limit=3)
            self.assertTrue(hits)
            self.assertEqual(hits[0]["name"], "rotation-policy")
            idx.close()
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
