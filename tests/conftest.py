"""pytest configuration for the eidetic test suite.

Registers the `both_modes` marker (spec §9.1) so ranking/injection tests can be
tagged as mode-sensitive without a PytestUnknownMarkWarning. The actual
both-modes guarantee is the CI matrix (two legs: fts-only + vectored) asserted by
tests/test_ci_both_modes.py::test_ci_asserts_both_modes_ran — pytest cannot
parametrize fastembed present/absent in a single process, so the two modes are
two CI legs, not two parametrize cases.
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "both_modes: ranking/injection test exercised in BOTH the FTS-only and "
        "vectored CI legs (spec §9.1).",
    )
