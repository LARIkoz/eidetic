"""assemble_context reads <memory-system>/local.env itself (not only via the hook).

2026-11-19: update.sh rebuilt the context by calling the script directly, so the
per-machine EIDETIC_FEEDBACK_OVERFLOW=pointer switch was lost and the context
doubled. unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context as ac  # noqa: E402


class LocalEnvTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="eidetic-localenv-")
        with open(os.path.join(self.root, "local.env"), "w", encoding="utf-8") as f:
            f.write('# comment\nEIDETIC_FEEDBACK_OVERFLOW=pointer\nEIDETIC_X_QUOTED="a b"\n'
                    'NOT_EIDETIC_KEY=1\nEIDETIC_BAD KEY=1\n')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_switches_applied_only_eidetic_keys(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("EIDETIC_FEEDBACK_OVERFLOW", None)
            os.environ.pop("EIDETIC_X_QUOTED", None)
            os.environ.pop("NOT_EIDETIC_KEY", None)
            ac.load_local_env(self.root)
            self.assertEqual(os.environ["EIDETIC_FEEDBACK_OVERFLOW"], "pointer")
            self.assertEqual(os.environ["EIDETIC_X_QUOTED"], "a b")
            self.assertNotIn("NOT_EIDETIC_KEY", os.environ)

    def test_environment_wins(self):
        with mock.patch.dict(os.environ, {"EIDETIC_FEEDBACK_OVERFLOW": "names"}):
            ac.load_local_env(self.root)
            self.assertEqual(os.environ["EIDETIC_FEEDBACK_OVERFLOW"], "names")

    def test_missing_file_is_noop(self):
        ac.load_local_env(os.path.join(self.root, "nope"))


if __name__ == "__main__":
    unittest.main()
