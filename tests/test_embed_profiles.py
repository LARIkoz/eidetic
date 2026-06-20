#!/usr/bin/env python3
"""Embedding-profile selection (model-by-language).

The embedder is config-driven so an English-only corpus can opt into a smaller,
faster model. Selection order: env EIDETIC_EMBED_PROFILE > .embed_profile file >
"multilingual" default. A wrong/unknown value must fall back, never crash.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import embed  # noqa: E402


class EmbedProfiles(unittest.TestCase):
    def test_profiles_carry_model_dim_and_both_prefixes(self):
        # A profile missing a prefix would silently halve recall — assert all four.
        for name, p in embed.PROFILES.items():
            for key in ("model", "dim", "query_prefix", "passage_prefix"):
                self.assertIn(key, p, f"profile {name!r} missing {key}")
        self.assertEqual(embed.PROFILES["multilingual"]["dim"], 1024)
        self.assertEqual(embed.PROFILES["english"]["dim"], 384)
        # e5 needs both prefixes; bge-en uses an asymmetric query instruction + no
        # passage prefix. These are the values the A/B validated.
        self.assertEqual(embed.PROFILES["multilingual"]["query_prefix"], "query: ")
        self.assertEqual(embed.PROFILES["multilingual"]["passage_prefix"], "passage: ")
        self.assertEqual(embed.PROFILES["english"]["passage_prefix"], "")

    def test_default_is_multilingual(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EIDETIC_EMBED_PROFILE", None)
            # point the file lookup at a path that does not exist -> default
            self.assertEqual(embed._active_profile("/nonexistent/.embed_profile"),
                             "multilingual")

    def test_env_overrides_to_english(self):
        with mock.patch.dict(os.environ, {"EIDETIC_EMBED_PROFILE": "english"}):
            self.assertEqual(embed._active_profile(), "english")

    def test_unknown_value_falls_back_not_crashes(self):
        with mock.patch.dict(os.environ, {"EIDETIC_EMBED_PROFILE": "klingon"}):
            self.assertEqual(embed._active_profile(), "multilingual")

    def test_config_file_selects_when_no_env(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, ".embed_profile")
            with open(cfg, "w", encoding="utf-8") as f:
                f.write("english\n")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("EIDETIC_EMBED_PROFILE", None)
                self.assertEqual(embed._active_profile(cfg), "english")

    def test_module_level_default_matches_e5(self):
        # The values the live system loads with no opt-in must equal the old
        # hardcoded e5 setup (byte-identical behaviour for existing installs).
        self.assertEqual(embed.MODEL_NAME, "intfloat/multilingual-e5-large")
        self.assertEqual(embed.VECTOR_DIM, 1024)
        self.assertEqual(embed.QUERY_PREFIX, "query: ")
        self.assertEqual(embed.PASSAGE_PREFIX, "passage: ")


if __name__ == "__main__":
    unittest.main()
