"""MLX embedding engine (bin/mlx_embed.py + bin/embed.py engine selection).

Hermetic engine-selection / stamp / guard tests run on BOTH legs (no mlx needed).
The FAITHFULNESS gate (FR-3) and the no-onnx-temp assertion (FR-5) require the mlx
runtime + the mlx-community weights, so they `importorskip("mlx.core")` and skip
cleanly where mlx is absent (both legs on this host). unittest + pytest.
"""

import contextlib
import glob
import io
import os
import sqlite3
import sys
import tempfile
import unittest

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import embed  # noqa: E402
import mlx_embed  # noqa: E402


def _fastembed_available():
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None


RU = "политика ротации ключей требует ежедневной замены сертификата"
EN = "the indexer performs an incremental rebuild after every vacuum"


# --- FR-1 engine selector (both legs) ----------------------------------------
class EngineSelectorTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k)
                       for k in ("EIDETIC_EMBED_ENGINE", "EIDETIC_EMBED_PROFILE")}
        for k in self._saved:
            os.environ.pop(k, None)
        self._saved_profile = embed.EMBED_PROFILE

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        embed.EMBED_PROFILE = self._saved_profile

    def test_default_is_fastembed(self):
        self.assertEqual(embed._active_engine(), "fastembed")

    def test_env_selects_mlx(self):
        os.environ["EIDETIC_EMBED_ENGINE"] = "mlx"
        self.assertEqual(embed._active_engine(), "mlx")

    def test_file_selects_mlx(self):
        d = tempfile.mkdtemp(prefix="mlx-cfg-")
        cfg = os.path.join(d, ".embed_engine")
        with open(cfg, "w") as f:
            f.write("mlx\n")
        self.assertEqual(embed._active_engine(_config_path=cfg), "mlx")

    def test_env_beats_file(self):
        d = tempfile.mkdtemp(prefix="mlx-cfg-")
        cfg = os.path.join(d, ".embed_engine")
        with open(cfg, "w") as f:
            f.write("fastembed\n")
        os.environ["EIDETIC_EMBED_ENGINE"] = "mlx"
        self.assertEqual(embed._active_engine(_config_path=cfg), "mlx")

    def test_unknown_value_falls_back(self):
        os.environ["EIDETIC_EMBED_ENGINE"] = "torch-cuda-nonsense"
        self.assertEqual(embed._active_engine(), "fastembed")

    def test_mlx_only_serves_multilingual(self):
        # english profile + mlx → fastembed (mlx carries only the e5 weights).
        os.environ["EIDETIC_EMBED_ENGINE"] = "mlx"
        embed.EMBED_PROFILE = "english"
        self.assertEqual(embed._active_engine(), "fastembed")
        embed.EMBED_PROFILE = "multilingual"
        self.assertEqual(embed._active_engine(), "mlx")


# --- FR-4 stamp + drift guard (hermetic; both legs) --------------------------
class EngineStampGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlx-vec-")
        self.db = os.path.join(self.tmp, "vectors.db")
        self._saved_engine = embed.EMBED_ENGINE

    def tearDown(self):
        embed.EMBED_ENGINE = self._saved_engine
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stamp(self, **meta):
        c = embed.init_vector_db(self.db)
        for k, v in meta.items():
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, str(v)))
        c.commit()
        return c

    def _meta_ok(self, conn):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = embed._vector_meta_ok(conn)
        return ok, err.getvalue()

    def test_engine_stamp_value(self):
        embed.EMBED_ENGINE = "fastembed"
        self.assertEqual(embed._engine_stamp(), "fastembed")
        embed.EMBED_ENGINE = "mlx"
        self.assertEqual(embed._engine_stamp(), f"mlx:{mlx_embed.MLX_ENGINE_VERSION}")

    def test_mlx_db_read_under_fastembed_degrades_loudly(self):
        embed.EMBED_ENGINE = "fastembed"
        c = self._stamp(model=embed.MODEL_NAME, dim=embed.VECTOR_DIM,
                        hash_scheme=embed.HASH_SCHEME, fastembed_version="0.8.0",
                        embed_engine=f"mlx:{mlx_embed.MLX_ENGINE_VERSION}")
        ok, warn = self._meta_ok(c)
        c.close()
        self.assertFalse(ok)
        self.assertIn("embed_engine", warn)
        self.assertIn("index.sh --full", warn)

    def test_fastembed_db_read_under_mlx_degrades_loudly(self):
        embed.EMBED_ENGINE = "mlx"
        c = self._stamp(model=embed.MODEL_NAME, dim=embed.VECTOR_DIM,
                        hash_scheme=embed.HASH_SCHEME, fastembed_version="0.8.0",
                        embed_engine="fastembed")
        ok, warn = self._meta_ok(c)
        c.close()
        self.assertFalse(ok)
        self.assertIn("embed_engine", warn)

    def test_matching_engine_passes(self):
        embed.EMBED_ENGINE = "fastembed"
        c = self._stamp(model=embed.MODEL_NAME, dim=embed.VECTOR_DIM,
                        hash_scheme=embed.HASH_SCHEME,
                        fastembed_version=embed._fastembed_version() or "x",
                        embed_engine="fastembed")
        ok, _ = self._meta_ok(c)
        c.close()
        self.assertTrue(ok)

    def test_unstamped_engine_is_fastembed_backcompat(self):
        # A pre-engine-stamp db (has model, no embed_engine) reads fine under
        # fastembed (backward-compatible) but LOUDLY under mlx.
        embed.EMBED_ENGINE = "fastembed"
        c = self._stamp(model=embed.MODEL_NAME, dim=embed.VECTOR_DIM,
                        hash_scheme=embed.HASH_SCHEME,
                        fastembed_version=embed._fastembed_version() or "x")
        ok, _ = self._meta_ok(c)
        self.assertTrue(ok, "unstamped fastembed db must stay readable under fastembed")
        c.close()
        embed.EMBED_ENGINE = "mlx"
        c = self._stamp(model=embed.MODEL_NAME, dim=embed.VECTOR_DIM,
                        hash_scheme=embed.HASH_SCHEME)
        ok, warn = self._meta_ok(c)
        c.close()
        self.assertFalse(ok)
        self.assertIn("embed_engine", warn)


# --- FR-6 additive / dark (both legs) ----------------------------------------
class AdditiveTest(unittest.TestCase):
    def test_default_engine_is_fastembed_zero_change(self):
        # With a clean environment the resolved engine is fastembed → the historic
        # onnxruntime path, unchanged for existing installs.
        saved = os.environ.pop("EIDETIC_EMBED_ENGINE", None)
        try:
            self.assertEqual(embed._active_engine(), "fastembed")
        finally:
            if saved is not None:
                os.environ["EIDETIC_EMBED_ENGINE"] = saved

    def test_mlx_embed_import_safe_without_mlx(self):
        # mlx_embed imports on a host without mlx; available() is a clean bool.
        self.assertIsInstance(mlx_embed.available(), bool)
        self.assertEqual(mlx_embed.MLX_ENGINE_VERSION, "xlmr-mlx-1")

    def test_require_v1_door_intact(self):
        import engine
        engine.require("1")
        self.assertEqual(engine.ENGINE_API, "1.1")
        # model_info golden key-set must NOT have grown (embed_engine lives in the
        # vectors.db stamp, not the door surface).
        info = engine.model_info()
        self.assertEqual(set(info),
                         {"model", "dim", "hash_scheme", "fastembed", "engine_api", "profile"})


# --- FR-3 FAITHFULNESS GATE (mlx + fastembed required → skips otherwise) ------
class FaithfulnessTest(unittest.TestCase):
    @unittest.skipUnless(_fastembed_available(), "faithfulness gate needs fastembed")
    def test_cosine_ge_0999_per_item_ru_en(self):
        pytest.importorskip("mlx.core")
        import numpy as np
        texts = [RU, EN, "kubernetes ingress certificate renewal", "hello world"]
        # Force the fastembed side explicitly (independent of the ambient engine),
        # then compare against mlx on the SAME e5-prefixed strings.
        saved = embed.EMBED_ENGINE
        try:
            embed.EMBED_ENGINE = "fastembed"
            fe_blobs = embed.embed_texts(texts)
        finally:
            embed.EMBED_ENGINE = saved
        mlx_blobs = mlx_embed.embed_texts([embed.PASSAGE_PREFIX + t for t in texts])
        mins = []
        for fb, mb in zip(fe_blobs, mlx_blobs):
            a = np.frombuffer(fb, dtype=np.float32)
            b = np.frombuffer(mb, dtype=np.float32)
            cos = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
            mins.append(cos)
        worst = min(mins)
        self.assertGreaterEqual(
            worst, 0.999,
            f"MLX engine drifts from fastembed (min per-item cosine {worst:.5f} < 0.999) "
            "— refusing to ship a drifting engine")


# --- FR-5 ZERO onnxruntime/CoreML temp under mlx (mlx required → skips) -------
class NoOnnxTempTest(unittest.TestCase):
    def test_mlx_embed_creates_no_onnx_coreml_temp(self):
        pytest.importorskip("mlx.core")
        tmp = tempfile.gettempdir()

        def onnx_temps():
            return set(glob.glob(os.path.join(tmp, "onnxruntime-*"))) | \
                set(glob.glob(os.path.join(tmp, "*.mlmodelc"))) | \
                set(glob.glob(os.path.join(tmp, "**", "*.mlmodelc"), recursive=True))

        before = onnx_temps()
        mlx_embed.embed_texts(["passage: " + EN, "passage: " + RU])
        after = onnx_temps()
        self.assertEqual(after - before, set(),
                         "MLX embed leaked onnxruntime/CoreML temp into $TMPDIR")


if __name__ == "__main__":
    unittest.main()
