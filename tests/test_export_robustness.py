"""W2 export robustness — the concurrency-lock primitive.

_acquire_export_lock is the non-blocking guard that makes a second concurrent
exporter (Stop hook + 3am cron) no-op instead of interleaving writes. unittest so
it runs under `python3 -m unittest discover` and pytest (no pytest tmp_path
fixture — uses tempfile).
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import export_vault as ev  # noqa: E402


class ExportLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_lock_is_exclusive_and_releasable(self):
        fd1 = ev._acquire_export_lock(self.tmp)
        self.assertIsNotNone(fd1, "first acquirer should get the lock")
        # A second concurrent acquirer must no-op (None), not block or steal it.
        self.assertIsNone(ev._acquire_export_lock(self.tmp))
        fd1.close()  # release
        fd3 = ev._acquire_export_lock(self.tmp)
        self.assertIsNotNone(fd3, "lock should be re-acquirable after release")
        fd3.close()

    def test_export_lock_file_lives_in_target(self):
        fd = ev._acquire_export_lock(self.tmp)
        try:
            self.assertTrue(os.path.exists(os.path.join(self.tmp, ".export.lock")))
        finally:
            fd.close()


if __name__ == "__main__":
    unittest.main()
