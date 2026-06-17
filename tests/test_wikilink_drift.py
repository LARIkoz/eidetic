"""W4: broken-wikilink detection must not fire on prose-in-brackets.

Memory names are whitespace-free kebab slugs, so a `[[spaced target]]` is prose
accidentally bracketed (or an Obsidian display title) — not a broken memory
link. Pinning this keeps drift noise low without suppressing real renamed-slug
links (which are whitespace-free and still flagged). unittest for the runner.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import drift_check  # noqa: E402


class WikilinkExtractTest(unittest.TestCase):
    def test_prose_in_brackets_is_not_a_link(self):
        links = drift_check.extract_wikilinks_from_content(
            "see [[was briefly added to our picker]] and [[real-slug-name]]"
        )
        self.assertIn("real-slug-name", links)
        self.assertNotIn("was briefly added to our picker", links)

    def test_pipe_display_title_keeps_slug_target_only(self):
        # `[[slug|Display Title]]` -> target is the pre-pipe slug (no space), kept.
        links = drift_check.extract_wikilinks_from_content("[[topics/foo-bar|Foo Bar Display]]")
        self.assertEqual(links, ["topics/foo-bar"])

    def test_normal_kebab_slug_still_extracted(self):
        # A real (possibly renamed/broken) slug link must still be detected.
        self.assertEqual(
            drift_check.extract_wikilinks_from_content("[[feedback-decide-from-context]]"),
            ["feedback-decide-from-context"],
        )


if __name__ == "__main__":
    unittest.main()
