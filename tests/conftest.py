"""pytest configuration for the eidetic test suite.

Registers the `both_modes` marker (spec §9.1) so ranking/injection tests can be
tagged as mode-sensitive without a PytestUnknownMarkWarning. The actual
both-modes guarantee is the CI matrix (two legs: fts-only + vectored) asserted by
tests/test_ci_both_modes.py::test_ci_asserts_both_modes_ran — pytest cannot
parametrize fastembed present/absent in a single process, so the two modes are
two CI legs, not two parametrize cases.
"""

import atexit
import os
import shutil
import tempfile

# Hermetic compute governor: a test that indexes or embeds without its own
# EIDETIC_RESOURCE_ROOT used the LIVE ~/.claude/memory-system budget, so it
# charged the live ledger and failed with "Shared compute busy" whenever the
# installed system was cooling down (2026-11-19, test_m3_autofile). Tests that
# need a specific root still override this per test.
if not os.environ.get("EIDETIC_RESOURCE_ROOT"):
    _governor_root = tempfile.mkdtemp(prefix="eidetic-test-governor-")
    os.environ["EIDETIC_RESOURCE_ROOT"] = _governor_root
    atexit.register(shutil.rmtree, _governor_root, True)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "both_modes: ranking/injection test exercised in BOTH the FTS-only and "
        "vectored CI legs (spec §9.1).",
    )
