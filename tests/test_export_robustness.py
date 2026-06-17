"""W2 export robustness — concurrency lock primitive.

The Stop hook and the 3am cron can both fire export-vault; without a lock they
interleave writes into shared <path>.tmp targets and clobber each other's
manifest. _acquire_export_lock is the non-blocking guard that makes a second
writer no-op. (Sentinel-first ownership + .DS_Store ignore are exercised by the
export() path; this pins the lock primitive directly.)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import export_vault as ev  # noqa: E402


def test_export_lock_is_exclusive_and_releasable(tmp_path):
    fd1 = ev._acquire_export_lock(str(tmp_path))
    assert fd1 is not None, "first acquirer should get the lock"

    # A second concurrent acquirer must no-op (None), not block or steal it.
    assert ev._acquire_export_lock(str(tmp_path)) is None

    fd1.close()  # release

    fd3 = ev._acquire_export_lock(str(tmp_path))
    assert fd3 is not None, "lock should be re-acquirable after release"
    fd3.close()


def test_export_lock_file_lives_in_target(tmp_path):
    fd = ev._acquire_export_lock(str(tmp_path))
    try:
        assert os.path.exists(os.path.join(str(tmp_path), ".export.lock"))
    finally:
        fd.close()
