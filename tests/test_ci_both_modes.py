"""§9.1 — CI runs the whole suite in BOTH modes; the build fails if a leg is skipped.

`@both_modes` tags a ranking/injection test as mode-sensitive. pytest cannot
parametrize fastembed present/absent in one process, so "both modes" is realised
as the two CI legs (fts-only + vectored) of `.github/workflows/test.yml`;
`test_ci_asserts_both_modes_ran` fails if either leg or the skip-proof gate is
removed. unittest so it runs under pytest + `unittest discover`.
"""

import importlib.util
import os
import unittest

WORKFLOW = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "test.yml"))


def current_mode():
    """The ranking mode of the running interpreter: vectored iff fastembed is
    importable (Leg A), else fts-only (Leg B)."""
    return "vectored" if importlib.util.find_spec("fastembed") else "fts-only"


def both_modes(obj):
    """Mark a ranking/injection test as mode-sensitive (§9.1). Under pytest this
    attaches the registered `both_modes` marker; a no-op tag under plain
    unittest. The both-modes GUARANTEE is the CI matrix, not an in-process
    parametrize (fastembed presence is fixed per interpreter)."""
    try:
        import pytest
        return pytest.mark.both_modes(obj)
    except Exception:  # pragma: no cover
        return obj


class CiBothModesTest(unittest.TestCase):
    def test_current_mode_is_recognized(self):
        self.assertIn(current_mode(), ("vectored", "fts-only"))

    def test_ci_asserts_both_modes_ran(self):
        # The workflow MUST run the suite in both modes and fail the build if a
        # leg is skipped — assert both legs + the skip-proof gate are declared.
        with open(WORKFLOW, encoding="utf-8") as f:
            wf = f.read()
        self.assertIn("suite-both-modes", wf, "the both-modes matrix job is missing")
        self.assertIn("fts-only", wf, "the FTS-only leg is missing")
        self.assertIn("vectored", wf, "the vectored leg is missing")
        self.assertIn("both-modes-gate", wf, "the skip-proof gate job is missing")
        # the gate fails the build when a matrix leg's result != success.
        self.assertIn("needs.suite-both-modes.result", wf,
                      "the gate does not check the matrix result (not skip-proof)")


if __name__ == "__main__":
    unittest.main()
