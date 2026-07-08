"""W1b: content_hash must track the embedded text, not the full content.

embedding_text() truncates content to [:EMBED_CONTENT_CHARS] (spec-chunker FR-5
raised this 500→1500 in lockstep), so an edit BEYOND that cut does not change the
embedding. content_hash() must ignore those edits too, or the search-time guard
drops a still-valid vector. Also pins the HASH_SCHEME stamp. unittest so it runs
under the project runner and pytest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import embed  # noqa: E402

W = embed.EMBED_CONTENT_CHARS  # the shared embed-content window (1500)


class ContentHashSchemeTest(unittest.TestCase):
    def test_hash_ignores_edits_past_the_window(self):
        base = "x" * W
        a = base + " original tail well beyond the embedded window"
        b = base + " a COMPLETELY different tail beyond the window"
        self.assertEqual(
            embed.content_hash("n", "d", a, "h"),
            embed.content_hash("n", "d", b, "h"),
        )

    def test_hash_changes_on_edit_within_the_window(self):
        a = "x" * W
        c = "y" + a[1:]  # differs at char 0 (inside the embedded window)
        self.assertNotEqual(
            embed.content_hash("n", "d", a, "h"),
            embed.content_hash("n", "d", c, "h"),
        )

    def test_hash_changes_on_edit_between_old_and_new_window(self):
        # FR-5 lockstep: an edit at char ~1000 (past the OLD 500 cut, inside the
        # NEW 1500 window) now DOES change both the embedding and the hash.
        a = "x" * W
        c = a[:1000] + "y" + a[1001:]
        self.assertNotEqual(embed.embedding_text("n", "d", a, "h"),
                            embed.embedding_text("n", "d", c, "h"))
        self.assertNotEqual(embed.content_hash("n", "d", a, "h"),
                            embed.content_hash("n", "d", c, "h"))

    def test_hash_matches_embedding_text_window(self):
        # The hash must be a function of exactly embedding_text's inputs.
        a = "y" * (W + 100)
        b = a[:W] + "z" * 100
        self.assertEqual(embed.embedding_text("n", "d", a, "h"),
                         embed.embedding_text("n", "d", b, "h"))
        self.assertEqual(embed.content_hash("n", "d", a, "h"),
                         embed.content_hash("n", "d", b, "h"))

    def test_hash_scheme_is_set_and_bumped(self):
        self.assertEqual(embed.HASH_SCHEME, "trunc1500-v3")


if __name__ == "__main__":
    unittest.main()
