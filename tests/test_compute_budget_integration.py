"""Exercise the real embedding entry points without loading a model."""

from contextlib import contextmanager
import os
import sqlite3
import subprocess
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import embed
import mlx_embed
import rerank
import resource_budget as budget


class ComputeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"EIDETIC_RESOURCE_ROOT": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.saved = embed._model, rerank._model, rerank._unavailable
        embed._model = rerank._model = None
        rerank._unavailable = False
        embed._query_cache.clear()
        self.addCleanup(self.restore)

    def restore(self):
        embed._model, rerank._model, rerank._unavailable = self.saved
        embed._query_cache.clear()

    def test_query_vector_reused_but_geometry_change_misses(self):
        with patch.object(embed, "_embed_prefixed", return_value=[b"vector"]) as encode:
            self.assertEqual(embed.embed_query_texts(["same", "same"]), [b"vector"] * 2)
            self.assertEqual(encode.call_count, 1)
            with patch.object(embed, "EMBED_ENGINE", "different-engine"):
                embed.embed_query_texts(["same"])
            self.assertEqual(encode.call_count, 2)

    def test_query_cache_is_bounded_and_large_text_not_retained(self):
        with patch.object(embed, "_embed_prefixed", return_value=[b"v"]):
            embed.embed_query_texts([str(i) for i in range(200)])
            self.assertEqual(len(embed._query_cache), 128)
            embed.embed_query_texts(["x" * 20000])
            self.assertEqual(len(embed._query_cache), 128)

    def test_fastembed_constructor_receives_thread_cap_even_on_fallback(self):
        constructor = Mock(side_effect=[RuntimeError("provider failed"), object()])
        module = types.ModuleType("fastembed")
        module.TextEmbedding = constructor
        with patch.dict(sys.modules, {"fastembed": module}), \
             patch.object(embed, "apply_background_policy"), \
             patch.object(embed, "_embed_providers", return_value=["CoreMLExecutionProvider"]):
            embed.get_model()
        self.assertEqual(constructor.call_count, 2)
        for call in constructor.call_args_list:
            self.assertLessEqual(call.kwargs["threads"], 2)
        self.assertEqual(constructor.call_args.kwargs["providers"], ["CPUExecutionProvider"])

    def test_fastembed_cold_load_is_outside_gpu_windows(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy required for array conversion test")
        events = []

        @contextmanager
        def slot(kind):
            events.append(("enter", kind))
            yield
            events.append(("exit", kind))

        class Model:
            def embed(self, texts, batch_size):
                events.append(("encode", tuple(texts)))
                return [np.array([1.], dtype=np.float32) for _ in texts]

        def constructor(*args, **kwargs):
            events.append("load")
            return Model()

        module = types.ModuleType("fastembed")
        module.TextEmbedding = constructor
        with patch.dict(sys.modules, {"fastembed": module}), \
             patch.object(embed, "EMBED_ENGINE", "fastembed"), \
             patch.object(embed, "apply_background_policy"), \
             patch.object(embed, "compute_slot", slot), \
             patch.object(embed, "_embed_providers", return_value=["CoreMLExecutionProvider"]):
            self.assertEqual(len(embed.embed_texts(["one", "two"])), 2)
        self.assertEqual(events[:3], [("enter", "cpu"), "load", ("exit", "cpu")])
        self.assertEqual(events[3:], [e for t in ["one", "two"] for e in
                                     [("enter", "gpu"), ("encode", (embed.PASSAGE_PREFIX + t,)),
                                      ("exit", "gpu")]])

    def test_path_loaded_engine_shares_governor_without_sys_path_changes(self):
        engine_path = Path(__file__).resolve().parents[1] / "bin" / "engine.py"
        code = '''import importlib.util, sys
before = list(sys.path)
spec = importlib.util.spec_from_file_location("standalone_engine", sys.argv[1])
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
emb = engine._embed()
rerank = engine._rerank()
assert emb.compute_slot is sys.modules["resource_budget"].compute_slot
assert sys.modules["mlx_embed"].compute_slot is emb.compute_slot
assert engine._load_sibling("resource_budget", shared=True) is sys.modules["resource_budget"]
assert sys.path == before
print("ok")
'''
        result = subprocess.run([sys.executable, "-I", "-c", code, str(engine_path)],
                                cwd=self.tmp.name, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")

    def vector_fixture(self):
        index_path = str(Path(self.tmp.name) / "index.db")
        vector_path = str(Path(self.tmp.name) / "vectors.db")
        with sqlite3.connect(index_path) as conn:
            conn.execute("CREATE TABLE memory_chunks (id INTEGER PRIMARY KEY, path TEXT, name TEXT, "
                         "description TEXT, content TEXT, section_heading TEXT, mtime INTEGER)")
            conn.execute("INSERT INTO memory_chunks VALUES(1, 'card', 'name', '', 'body', '', 1)")
        conn = embed.init_vector_db(vector_path)
        digest = embed.content_hash("name", "", "body", "")
        conn.execute("INSERT INTO vectors VALUES(1, 'card', 'name', '', ?, ?, 1)",
                     (digest, b"previous vector"))
        conn.execute("INSERT INTO meta VALUES('test-stamp', 'old')")
        conn.commit()
        conn.close()
        return index_path, vector_path

    def test_failed_full_rebuild_preserves_committed_vectors_even_with_wal_reader(self):
        index_path, vector_path = self.vector_fixture()
        with sqlite3.connect(index_path) as conn:
            conn.executemany("INSERT INTO memory_chunks VALUES(?, 'card', 'name', '', 'body', '', 1)",
                             [(i,) for i in range(2, 66)])
        reader = sqlite3.connect(vector_path)
        self.addCleanup(reader.close)
        before = reader.execute("SELECT * FROM vectors").fetchall()
        with patch.object(embed, "apply_background_policy"), \
             patch.object(embed, "embed_texts", side_effect=[[b"new"] * 64,
                                                             RuntimeError("budget unavailable")]):
            with self.assertRaisesRegex(RuntimeError, "budget unavailable"):
                embed.run_full(index_path, vector_path)
        reader.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertEqual(reader.execute("SELECT * FROM vectors").fetchall(), before)
        self.assertEqual(reader.execute("SELECT value FROM meta WHERE key='test-stamp'").fetchone(), ("old",))

    def test_full_rebuild_publishes_vectors_and_stamp_after_encoding(self):
        index_path, vector_path = self.vector_fixture()
        reader = sqlite3.connect(vector_path)
        self.addCleanup(reader.close)

        def encode(texts):
            self.assertEqual(reader.execute("SELECT embedding FROM vectors").fetchone(),
                             (b"previous vector",))
            return [b"replacement"]

        with patch.object(embed, "apply_background_policy"), \
             patch.object(embed, "embed_texts", side_effect=encode), \
             patch.object(embed, "_fastembed_version", return_value="0.8.0"), \
             patch.object(embed, "_engine_stamp", return_value="test-engine"):
            embed.run_full(index_path, vector_path)
        self.assertEqual(reader.execute("SELECT embedding FROM vectors").fetchone(), (b"replacement",))
        meta = dict(reader.execute("SELECT key,value FROM meta"))
        self.assertEqual(meta["embed_engine"], "test-engine")
        self.assertEqual(meta["model"], embed.MODEL_NAME)

    def test_unchanged_and_deletion_only_vector_runs_settle_cpu_without_model(self):
        index_path, vector_path = self.vector_fixture()
        for delete in [False, True]:
            if delete:
                with sqlite3.connect(index_path) as conn:
                    conn.execute("DELETE FROM memory_chunks")
            with patch.object(embed, "apply_background_policy") as policy, \
                 patch.object(embed, "cpu_checkpoint") as checkpoint, \
                 patch.object(embed, "embed_texts") as model:
                embed.run_incremental(index_path, vector_path)
            policy.assert_called_once()
            model.assert_not_called()
            self.assertGreater(checkpoint.call_count, 1)
            self.assertEqual(checkpoint.call_args.kwargs, {"force": True})

    def test_reranker_microbatches_preserve_order_and_cpu_provider(self):
        calls = []
        class Model:
            def rerank(self, query, docs, batch_size):
                calls.append((query, docs, batch_size))
                return [float(doc) for doc in docs]
        constructor = Mock(return_value=Model())
        module = types.ModuleType("fastembed.rerank.cross_encoder")
        module.TextCrossEncoder = constructor
        with patch.dict(sys.modules, {"fastembed.rerank.cross_encoder": module}), \
             patch.object(budget, "apply_background_policy"):
            self.assertEqual(rerank.scores("q", ["3", "1", "2"]), [3., 1., 2.])
        self.assertEqual([c[1] for c in calls], [["3"], ["1"], ["2"]])
        self.assertTrue(all(c[2] == 1 for c in calls))
        self.assertEqual(constructor.call_args.kwargs["providers"], ["CPUExecutionProvider"])

    def test_mlx_sync_precedes_cooldown_for_every_microbatch(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy required for array conversion test")
        events = []
        mx = types.ModuleType("mlx.core")
        mx.synchronize = lambda: events.append("sync")
        mx.set_cache_limit = lambda limit: events.append(("cache", limit))
        mlx = types.ModuleType("mlx")
        mlx.core = mx

        @contextmanager
        def slot(kind):
            events.append(("enter", kind))
            try:
                yield
            finally:
                events.append("cooldown")

        def encode(texts):
            events.append(("encode", tuple(texts)))
            return np.array([[float(text)] for text in texts], dtype=np.float32)

        with patch.dict(sys.modules, {"mlx": mlx, "mlx.core": mx}), \
             patch.object(mlx_embed, "apply_background_policy"), \
             patch.object(mlx_embed, "compute_slot", slot), \
             patch.object(mlx_embed, "_load"), \
             patch.object(mlx_embed, "_encode", side_effect=encode):
            blobs = mlx_embed.embed_texts(["1", "2", "3"])
        self.assertEqual([float(np.frombuffer(b, dtype=np.float32)[0]) for b in blobs], [1., 2., 3.])
        self.assertEqual(events[:3], [("enter", "cpu"), "cooldown", ("cache", 128 * 1024 * 1024)])
        self.assertEqual(events[3:], [event for text in ["1", "2", "3"]
                                     for event in [("enter", "gpu"), ("encode", (text,)), "sync", "cooldown"]])


if __name__ == "__main__":
    unittest.main()
