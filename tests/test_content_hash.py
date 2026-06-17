"""W1b: content_hash must track the embedded text, not the full content.

embedding_text() truncates content to [:500], so an edit BEYOND char 500 does
not change the embedding. content_hash() must therefore ignore those edits too,
or the search-time guard drops a still-valid vector. Also pins the HASH_SCHEME
stamp. unittest so it runs under the project runner and pytest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import embed  # noqa: E402


class ContentHashSchemeTest(unittest.TestCase):
    def test_hash_ignores_edits_past_char_500(self):
        base = "x" * 500
        a = base + " original tail well beyond five hundred characters"
        b = base + " a COMPLETELY different tail beyond five hundred"
        self.assertEqual(
            embed.content_hash("n", "d", a, "h"),
            embed.content_hash("n", "d", b, "h"),
        )

    def test_hash_changes_on_edit_within_first_500(self):
        a = "x" * 500
        c = "y" + a[1:]  # differs at char 0 (inside the embedded window)
        self.assertNotEqual(
            embed.content_hash("n", "d", a, "h"),
            embed.content_hash("n", "d", c, "h"),
        )

    def test_hash_matches_embedding_text_window(self):
        # The hash must be a function of exactly embedding_text's inputs.
        a = "y" * 600
        b = a[:500] + "z" * 100
        self.assertEqual(embed.embedding_text("n", "d", a, "h"),
                         embed.embedding_text("n", "d", b, "h"))
        self.assertEqual(embed.content_hash("n", "d", a, "h"),
                         embed.content_hash("n", "d", b, "h"))

    def test_hash_scheme_is_set(self):
        self.assertTrue(embed.HASH_SCHEME)


if __name__ == "__main__":
    unittest.main()
