"""Golden test: vault export must NOT corrupt fenced code blocks.

Pins the W2 fenced-code mask: rewrite_wikilinks / strip_field / extract_field
must leave [[links]] and "Field:" lines inside ``` code examples verbatim.
unittest.TestCase so it runs under both `python3 -m unittest discover` (the
project runner) and pytest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import export_vault as ev  # noqa: E402


class FenceMaskTest(unittest.TestCase):
    def test_wikilink_inside_fence_is_preserved(self):
        body = '```python\nx = "[[not-a-link]]"  # example, not a wikilink\n```\n'
        out = ev.rewrite_wikilinks(body, {})
        self.assertIn('"[[not-a-link]]"', out, "fenced wikilink corrupted: %r" % out)

    def test_field_line_inside_fence_is_preserved(self):
        body = "```\nWhy: this line is example content inside a code block\n```\n"
        out = ev.strip_field(body, "Why")
        self.assertIn("Why: this line is example content inside a code block", out)

    def test_field_inside_fence_is_not_extracted(self):
        # A "Why:" that exists ONLY inside a code fence must not be read as the
        # card's field (the extract_field fence-mask fix).
        body = "Prose with no real field.\n\n```\nWhy: example inside the fence\n```\n"
        self.assertIsNone(ev.extract_field(body, "Why"))

    def test_real_field_outside_fence_is_extracted(self):
        body = "Why: the real reason\n\n```\nWhy: a fenced example\n```\n"
        self.assertEqual(ev.extract_field(body, "Why"), "the real reason")

    def test_wikilink_outside_fence_still_rewritten(self):
        body = "See [[real-target]] for details.\n"
        out = ev.rewrite_wikilinks(body, {"real-target": "Real Target.md"})
        self.assertIn("[[", out)
        self.assertIn("Real Target", out)


if __name__ == "__main__":
    unittest.main()
