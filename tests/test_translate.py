#!/usr/bin/env python3
"""Cross-lingual query translation — backend selection, language detection,
fail-open, and the dual-query min-rank fusion.

The feature is OFF by default and every backend is FAIL-OPEN, so the contract
these tests pin is: (1) config resolves env > file > "off"; (2) only a
non-English query is ever translated; (3) an unavailable/unknown backend yields
None (caller keeps the native result); (4) fusion ranks a doc by its BEST rank
across native+translated and never drops the native hit.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import translate  # noqa: E402
import search_impl  # noqa: E402


class BackendConfig(unittest.TestCase):
    def test_default_is_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EIDETIC_QUERY_TRANSLATE", None)
            # point at a non-existent config so only env+default decide
            self.assertEqual(translate.active_backend("/nonexistent/.translate_backend"), "off")

    def test_env_overrides_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as f:
            f.write("opusmt")
            cfg = f.name
        try:
            with mock.patch.dict(os.environ, {"EIDETIC_QUERY_TRANSLATE": "apple"}):
                self.assertEqual(translate.active_backend(cfg), "apple")
        finally:
            os.unlink(cfg)

    def test_file_used_when_env_absent(self):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as f:
            f.write("  opusmt \n")  # whitespace tolerated
            cfg = f.name
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("EIDETIC_QUERY_TRANSLATE", None)
                self.assertEqual(translate.active_backend(cfg), "opusmt")
        finally:
            os.unlink(cfg)

    def test_unknown_value_falls_back_to_off(self):
        with mock.patch.dict(os.environ, {"EIDETIC_QUERY_TRANSLATE": "googletranslate"}):
            self.assertEqual(translate.active_backend("/nonexistent"), "off")


class LanguageDetection(unittest.TestCase):
    def test_cyrillic_is_non_english(self):
        self.assertTrue(translate.is_non_english("обнаружение памяти"))

    def test_cjk_and_hangul_and_arabic(self):
        self.assertTrue(translate.is_non_english("メモリ検出"))      # kana
        self.assertTrue(translate.is_non_english("内存检测"))        # CJK
        self.assertTrue(translate.is_non_english("الذاكرة"))        # Arabic

    def test_ascii_is_english(self):
        self.assertFalse(translate.is_non_english("stale memory drift detection"))

    def test_should_translate_into_english_corpus(self):
        # personal-memory default (English corpus): translate a non-English query
        self.assertTrue(translate.should_translate("привет", "en"))
        self.assertFalse(translate.should_translate("hello", "en"))

    def test_should_translate_into_non_english_corpus(self):
        # a non-English topic base (e.g. Russian, target='ru'): an English query is
        # translated INTO the corpus language; a query already in that script is not.
        self.assertTrue(translate.should_translate("variable reward", "ru"))
        self.assertFalse(translate.should_translate("переменное вознаграждение", "ru"))
        # CJK / Hangul targets behave the same
        self.assertTrue(translate.should_translate("memory", "ja"))
        self.assertFalse(translate.should_translate("メモリ", "ja"))
        # unknown/unsupported target fails safe (no translation)
        self.assertFalse(translate.should_translate("hello", "xx"))
        self.assertFalse(translate.should_translate("", "ru"))


class ResolveBackend(unittest.TestCase):
    def test_off_resolves_to_none(self):
        self.assertIsNone(translate.resolve_backend("off"))

    def test_explicit_known_backend(self):
        self.assertEqual(translate.resolve_backend("opusmt"), "opusmt")

    def test_unknown_backend_is_none(self):
        self.assertIsNone(translate.resolve_backend("googletranslate"))

    def test_auto_prefers_apple_then_opusmt_then_cli(self):
        with mock.patch.object(translate, "apple_available", return_value=True), \
             mock.patch.object(translate, "opusmt_available", return_value=True), \
             mock.patch.object(translate, "cli_available", return_value=True):
            self.assertEqual(translate.resolve_backend("auto"), "apple")
        with mock.patch.object(translate, "apple_available", return_value=False), \
             mock.patch.object(translate, "opusmt_available", return_value=True), \
             mock.patch.object(translate, "cli_available", return_value=True):
            self.assertEqual(translate.resolve_backend("auto"), "opusmt")
        with mock.patch.object(translate, "apple_available", return_value=False), \
             mock.patch.object(translate, "opusmt_available", return_value=False), \
             mock.patch.object(translate, "cli_available", return_value=True):
            self.assertEqual(translate.resolve_backend("auto"), "cli")
        with mock.patch.object(translate, "apple_available", return_value=False), \
             mock.patch.object(translate, "opusmt_available", return_value=False), \
             mock.patch.object(translate, "cli_available", return_value=False):
            self.assertIsNone(translate.resolve_backend("auto"))


class TranslateFailOpen(unittest.TestCase):
    def test_empty_query_returns_none(self):
        self.assertIsNone(translate.translate("", "en", "opusmt"))
        self.assertIsNone(translate.translate("   ", "en", "opusmt"))

    def test_off_backend_returns_none(self):
        translate.clear_cache()
        self.assertIsNone(translate.translate("привет", "en", "off"))

    def test_unavailable_backend_returns_none(self):
        # A backend whose impl reports unavailable must yield None, not raise.
        translate.clear_cache()
        with mock.patch.object(translate, "_apple_translate", return_value=None):
            self.assertIsNone(translate.translate("привет", "en", "apple"))


class FuseDual(unittest.TestCase):
    @staticmethod
    def _r(path, tag=""):
        return {"path": path, "section": "", "tag": tag}

    def test_min_rank_orders_by_best_rank_and_keeps_native(self):
        native = [self._r("A", "nat"), self._r("B", "nat"), self._r("C", "nat")]
        translated = [self._r("D", "trn"), self._r("B", "trn")]
        fused = search_impl._fuse_dual(native, translated, limit=10)
        paths = [r["path"] for r in fused]
        # A(0,nat) D(0,trn) tie -> native list iterated first so A precedes D;
        # B best rank 1 (both lists); C rank 2. D (translated-only) is surfaced.
        self.assertEqual(paths, ["A", "D", "B", "C"])
        # B appeared at equal rank in both — the native dict is carried (tie → native).
        b = next(r for r in fused if r["path"] == "B")
        self.assertEqual(b["tag"], "nat")

    def test_translated_only_doc_is_surfaced_high(self):
        native = [self._r("A"), self._r("B"), self._r("C")]
        translated = [self._r("Z")]  # Z only via translation, ranks #1 there
        fused = search_impl._fuse_dual(native, translated, limit=10)
        self.assertEqual(fused[0]["path"], "A")   # native #1 keeps the top (tie)
        self.assertIn("Z", [r["path"] for r in fused])  # but Z is now reachable

    def test_limit_is_respected(self):
        native = [self._r(c) for c in "ABCDE"]
        translated = [self._r(c) for c in "FGHIJ"]
        fused = search_impl._fuse_dual(native, translated, limit=3)
        self.assertEqual(len(fused), 3)


class TranslationOffByDefault(unittest.TestCase):
    def test_resolve_query_translation_returns_none_when_off(self):
        # The shipped default: search_impl must NOT translate unless opted in.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EIDETIC_QUERY_TRANSLATE", None)
            # Even a Cyrillic query stays native when the backend is off.
            self.assertIsNone(
                search_impl._resolve_query_translation("обнаружение памяти", "/nope/db/index.db"))


class CorpusLang(unittest.TestCase):
    """Corpus-language targeting: explicit-only (env > .translate_lang file > None).
    No per-query auto-detect, so the mixed-but-English personal corpus stays 'en'."""

    def setUp(self):
        search_impl._corpus_lang_cache.clear()

    def test_none_when_no_signal(self):
        # personal memory: no .translate_lang at the root → None → target 'en'
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "db"))
            db = os.path.join(d, "db", "index.db")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("EIDETIC_TRANSLATE_LANG", None)
                self.assertIsNone(search_impl._corpus_lang(db))

    def test_translate_lang_file_at_base_root(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "db"))
            db = os.path.join(d, "db", "index.db")
            with open(os.path.join(d, ".translate_lang"), "w") as f:
                f.write("ru\n")  # whitespace tolerated, lowercased
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("EIDETIC_TRANSLATE_LANG", None)
                self.assertEqual(search_impl._corpus_lang(db), "ru")

    def test_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "db"))
            db = os.path.join(d, "db", "index.db")
            with open(os.path.join(d, ".translate_lang"), "w") as f:
                f.write("ru")
            with mock.patch.dict(os.environ, {"EIDETIC_TRANSLATE_LANG": "ja"}):
                self.assertEqual(search_impl._corpus_lang(db), "ja")


class TranslateTimeoutParse(unittest.TestCase):
    """A config typo on EIDETIC_TRANSLATE_TIMEOUT must never crash native search —
    the dual-query timeout falls back to the 8 s default on any bad/zero/negative value."""

    def test_bad_values_fall_back_to_default(self):
        for bad in ("abc", "8s", "  ", "-3", "0", ""):
            with mock.patch.dict(os.environ, {"EIDETIC_TRANSLATE_TIMEOUT": bad}):
                self.assertEqual(search_impl._translate_timeout(), 8.0, f"value={bad!r}")

    def test_valid_value_is_used(self):
        with mock.patch.dict(os.environ, {"EIDETIC_TRANSLATE_TIMEOUT": "3.5"}):
            self.assertEqual(search_impl._translate_timeout(), 3.5)

    def test_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EIDETIC_TRANSLATE_TIMEOUT", None)
            self.assertEqual(search_impl._translate_timeout(), 8.0)


class BackendStatusSource(unittest.TestCase):
    def test_source_language_plumbed_to_apple_pack_check(self):
        # the doctor passes the corpus/configured language → backend_status must probe
        # THAT pair (source->en), not a hardcoded ru->en, and echo it back.
        with mock.patch.object(translate, "apple_available", return_value=True) as m, \
                mock.patch.object(translate, "opusmt_available", return_value=False), \
                mock.patch.object(translate, "cli_available", return_value=False):
            s = translate.backend_status(source="de")
        self.assertEqual(s["apple_source"], "de")
        m.assert_called_once_with(source="de")

    def test_default_source_is_ru(self):
        with mock.patch.object(translate, "apple_available", return_value=False), \
                mock.patch.object(translate, "opusmt_available", return_value=False), \
                mock.patch.object(translate, "cli_available", return_value=False):
            self.assertEqual(translate.backend_status()["apple_source"], "ru")


if __name__ == "__main__":
    unittest.main()
