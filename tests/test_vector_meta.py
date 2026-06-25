"""Vector meta guard — fastembed pooling/geometry drift detection.

model+dim+hash_scheme cannot detect a fastembed bump that changes a model's
pooling (e5 switched CLS->mean in 0.6+): same model, same dim, DIFFERENT
geometry. So run_full stamps the fastembed release and _vector_meta_ok degrades
LOUDLY to FTS when the live fastembed differs from the stamped one. Pre-stamp
dbs (no fastembed_version key) stay backward-compatible. unittest so it runs
under the project runner and pytest.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import embed  # noqa: E402


class VectorMetaFastembedGuard(unittest.TestCase):
    def _db_with_meta(self, **meta):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        conn = embed.init_vector_db(os.path.join(d, "vectors.db"))
        self.addCleanup(conn.close)
        for k, v in meta.items():
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, v))
        conn.commit()
        return conn

    def _patch_live_version(self, value):
        orig = embed._fastembed_version
        embed._fastembed_version = lambda: value
        self.addCleanup(setattr, embed, "_fastembed_version", orig)

    def test_unstamped_db_is_ok(self):
        # No model/dim/version at all → cannot verify, stay backward-compatible.
        self.assertTrue(embed._vector_meta_ok(self._db_with_meta()))

    def test_matching_fastembed_is_ok(self):
        self._patch_live_version("9.9.9-test")
        conn = self._db_with_meta(
            model=embed.MODEL_NAME, dim=str(embed.VECTOR_DIM),
            hash_scheme=embed.HASH_SCHEME, fastembed_version="9.9.9-test",
        )
        self.assertTrue(embed._vector_meta_ok(conn))

    def test_mismatched_fastembed_degrades(self):
        # Same model/dim/hash, DIFFERENT fastembed → degrade to FTS (return False).
        self._patch_live_version("0.8.0-new")
        conn = self._db_with_meta(
            model=embed.MODEL_NAME, dim=str(embed.VECTOR_DIM),
            hash_scheme=embed.HASH_SCHEME, fastembed_version="0.5.1-old",
        )
        self.assertFalse(embed._vector_meta_ok(conn))

    def test_absent_fastembed_stamp_does_not_block(self):
        # Pre-stamp db: model/dim/hash present, no fastembed_version → do not block.
        self._patch_live_version("0.8.0")
        conn = self._db_with_meta(
            model=embed.MODEL_NAME, dim=str(embed.VECTOR_DIM),
            hash_scheme=embed.HASH_SCHEME,
        )
        self.assertTrue(embed._vector_meta_ok(conn))

    def test_no_live_fastembed_does_not_block(self):
        # fastembed uninstalled (live None) → cannot compare, do not block.
        self._patch_live_version(None)
        conn = self._db_with_meta(
            model=embed.MODEL_NAME, dim=str(embed.VECTOR_DIM),
            hash_scheme=embed.HASH_SCHEME, fastembed_version="0.5.1-old",
        )
        self.assertTrue(embed._vector_meta_ok(conn))

    def test_pin_constant_set(self):
        self.assertTrue(embed.FASTEMBED_PIN)


if __name__ == "__main__":
    unittest.main()
