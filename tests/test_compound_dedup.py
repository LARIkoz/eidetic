"""FIX — compounding dedup must actually match (phrase-only was dead).

`search_fts5` used to issue ONLY a strict FTS5 phrase of up to 6 extracted
keywords. Keywords are non-contiguous in any real document, so the phrase
almost never matched and every signal became a new card ("0 compounded, N new").
Covers the staged match: phrase first (still wins when a contiguous phrase
exists), then ONE retry as an implicit AND of the top 4 SALIENT keywords
(rarest-in-corpus, not first-in-signal-order); a clearly unrelated signal must
still create a new card (no loose OR stage). The thresholded overlap fallback
(find_overlap_candidate) covers the 2026-07-02 audit's E2C refutation: a
paraphrase of an existing card must compound or be FLAGGED as a possible
duplicate — never silently duplicate.
unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import glob
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import compound  # noqa: E402
import index_impl  # noqa: E402

BIN_DIR = os.path.join(os.path.dirname(__file__), "..", "bin")

SCHEMA = """
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    project TEXT,
    name TEXT,
    type TEXT,
    evidence TEXT DEFAULT 'observed',
    source TEXT DEFAULT 'user-explicit',
    confidence REAL DEFAULT 0.7,
    last_verified TEXT,
    card_kind TEXT DEFAULT '',
    status TEXT DEFAULT 'current',
    area TEXT DEFAULT '',
    supersedes TEXT DEFAULT '',
    superseded_by TEXT DEFAULT '',
    section_heading TEXT,
    content TEXT NOT NULL,
    description TEXT,
    mtime INTEGER,
    UNIQUE(path, section_heading)
);
CREATE VIRTUAL TABLE memory_fts USING fts5(
    name, description, section_heading, content,
    content='memory_chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);
CREATE TRIGGER memory_chunks_ai AFTER INSERT ON memory_chunks BEGIN
    INSERT INTO memory_fts(rowid, name, description, section_heading, content)
    VALUES (new.id, new.name, new.description, new.section_heading, new.content);
END;
"""

# The signal's keywords (switched, indexer, incremental, rebuild, ...) all
# appear here, but interleaved with other words — a strict phrase can never
# match this; only the AND stage can.
SCATTERED_CARD = (
    "/tmp/eidetic-test/memory/indexer-policy.md",
    "Indexer Rebuild Policy",
    "We switched the nightly job so the indexer performs an incremental "
    "rebuild after every full vacuum of the sqlite corpus.",
)

# Contains the signal's top-6 keywords as one CONTIGUOUS phrase.
PHRASE_CARD = (
    "/tmp/eidetic-test/memory/alpha-run.md",
    "Alpha Run Notes",
    "The run log recorded alpha bravo charlie delta echo foxtrot before the "
    "batch completed.",
)

# Same first-4 keywords as PHRASE_CARD's signal, but scattered — the AND stage
# would match it, so it must NOT shadow the phrase winner.
DECOY_CARD = (
    "/tmp/eidetic-test/memory/alpha-decoy.md",
    "Alpha Decoy Notes",
    "alpha tests ran, then bravo checks, later charlie audits and delta sweeps.",
)


class CompoundDedupTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        for path, name, content in (SCATTERED_CARD, PHRASE_CARD, DECOY_CARD):
            self.conn.execute(
                "INSERT INTO memory_chunks (path, project, name, type, section_heading, content, description) "
                "VALUES (?,?,?,?,?,?,?)",
                (path, "eidetic-test", name, "project", name, content, name),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _search(self, signal):
        keywords = compound.extract_keywords(signal)
        self.assertTrue(keywords)
        return compound.search_fts5(self.conn, keywords, limit=3)

    def test_and_stage_matches_non_contiguous_keywords(self):
        rows = self._search(
            "Decision: switched the indexer to incremental rebuild because "
            "full vacuum locked the sqlite database"
        )
        self.assertTrue(rows, "AND fallback stage must fire when the phrase finds nothing")
        self.assertEqual(rows[0][0], SCATTERED_CARD[0])

    def test_unrelated_signal_finds_no_match(self):
        rows = self._search(
            "Failed: kubernetes ingress certificate renewal timed out waiting "
            "for the letsencrypt responder"
        )
        self.assertEqual(rows, [])

    def test_phrase_stage_still_wins_when_contiguous(self):
        rows = self._search(
            "Worked: alpha bravo charlie delta echo foxtrot pipeline finished cleanly"
        )
        paths = [r[0] for r in rows]
        self.assertEqual(paths, [PHRASE_CARD[0]],
                         "a contiguous phrase match must return ONLY the phrase-stage rows")

    def test_salient_and_stage_survives_an_early_word_swap(self):
        # v5.13.0 took the FIRST 4 keywords in signal order; a swapped early
        # content word broke the AND. Salience ordering (rarest-in-corpus)
        # must still converge on the card.
        rows = self._search(
            "Decision: gradual approach kept the indexer on incremental rebuild "
            "after switched vacuum schedule for sqlite"
        )
        self.assertTrue(rows)
        self.assertEqual(rows[0][0], SCATTERED_CARD[0])


class OverlapFallbackTest(unittest.TestCase):
    """Stage 3 — audit E2C: paraphrases must compound or FLAG, never silently dup."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        path, name, content = SCATTERED_CARD
        self.conn.execute(
            "INSERT INTO memory_chunks (path, project, name, type, section_heading, content, description) "
            "VALUES (?,?,?,?,?,?,?)",
            (path, "eidetic-test", name, "project", name, content, name),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _fallback(self, signal):
        keywords = compound.extract_keywords(signal)
        self.assertEqual(compound.search_fts5(self.conn, keywords, limit=3), [],
                         "fallback tests must exercise signals the AND stage misses")
        return compound.find_overlap_candidate(self.conn, keywords, signal)

    def test_audit_e2c_paraphrase_is_flagged_not_silent(self):
        # The exact refutation probe from the 2026-07-02 audit: same topic,
        # different words. Porter absorbs performing/performs; overlap = 2
        # (perform, rebuild) → below the compound threshold, so it must be
        # FLAGGED as a possible duplicate instead of silently duplicated.
        action, payload = self._fallback(
            "Knowledge: the reindex job now runs gradually instead of "
            "performing a complete rebuild, avoiding database locks"
        )
        self.assertEqual(action, "flag")
        self.assertEqual(payload, SCATTERED_CARD[0])

    def test_close_paraphrase_compounds_via_threshold(self):
        # 3 salient keywords (indexer, performs→perform, rebuilds→rebuild)
        # converge on the card → >= OVERLAP_COMPOUND_MIN → compound.
        action, payload = self._fallback(
            "Knowledge: the indexer performs rebuilds gradually now, "
            "avoiding database locks entirely"
        )
        self.assertEqual(action, "compound")
        self.assertEqual(payload[0][0], SCATTERED_CARD[0])

    def test_unrelated_signal_neither_compounds_nor_flags(self):
        action, payload = self._fallback(
            "Failed: kubernetes ingress certificate renewal timed out waiting "
            "for the letsencrypt responder"
        )
        self.assertIsNone(action)
        self.assertIsNone(payload)


class DeterministicCandidateTest(unittest.TestCase):
    """B10: _keyword_paths must return the SAME candidate subset every run —
    an unordered LIMIT made the same signal compound one run, duplicate the next."""

    def test_keyword_paths_are_the_lexicographically_first_paths(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        # Insert in REVERSE lexicographic order: the reverted (no ORDER BY)
        # query emits scan order → the LAST three paths → this fails there.
        for i in reversed(range(6)):
            conn.execute(
                "INSERT INTO memory_chunks (path, project, name, type, section_heading, content, description) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"/tmp/eidetic-test/memory/card-{i}.md", "eidetic-test",
                 f"card-{i}", "project", f"card-{i}", "zanzibar topic entry", ""),
            )
        conn.commit()
        got = compound._keyword_paths(conn, "zanzibar", limit=3)
        conn.close()
        self.assertEqual(got, {f"/tmp/eidetic-test/memory/card-{i}.md" for i in range(3)})


class HyphenKeywordTest(unittest.TestCase):
    """B11: a hyphenated identifier must count as ONE overlap unit —
    _sanitize_words splitting on '-' while extract_keywords kept the word
    whole double-counted both halves and false-compounded."""

    SKLEARN_CARD = (
        "/tmp/eidetic-test/memory/sklearn-choice.md",
        "Sklearn Choice",
        "We standardized the training pipeline on scikit-learn estimators.",
    )

    def test_hyphenated_identifier_counts_once_in_overlap(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        path, name, content = self.SKLEARN_CARD
        conn.execute(
            "INSERT INTO memory_chunks (path, project, name, type, section_heading, content, description) "
            "VALUES (?,?,?,?,?,?,?)",
            (path, "eidetic-test", name, "project", name, content, name),
        )
        conn.commit()
        signal = "Knowledge: prefer scikit-learn estimators for quick baselines"
        keywords = compound.extract_keywords(signal)
        self.assertIn("scikit-learn", keywords.split())
        self.assertEqual(compound.search_fts5(conn, keywords, limit=3), [])
        # Overlap = {scikit-learn, estimators} = 2 → FLAG. The split-on-hyphen
        # bug counted {scikit, learn, estimators} = 3 → false auto-compound.
        action, payload = compound.find_overlap_candidate(conn, keywords, signal)
        conn.close()
        self.assertEqual(action, "flag")
        self.assertEqual(payload, path)


class ProtectedDupFlagTest(unittest.TestCase):
    """B8: a near-dup of a protected (feedback/user) card must emit the
    compound-flag — not silently land as a new card next to the rule."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-flag-test-")
        self.mem = os.path.join(self.tmp, "memory")
        os.makedirs(self.mem)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_protected_near_dup_emits_flag_not_silent_new_card(self):
        card_path = os.path.join(self.mem, "ruby-linter-rule.md")
        card_text = """---
name: ruby-linter-rule
description: run the ruby linter before committing
metadata:
  type: feedback
  source: user-explicit
---

Always run the ruby linter before committing changes to the monorepo.
"""
        with open(card_path, "w", encoding="utf-8") as f:
            f.write(card_text)
        conn = index_impl.init_db(os.path.join(self.tmp, "db", "index.db"))
        meta, body = index_impl.parse_frontmatter(card_text)
        index_impl.index_file(conn, card_path, meta, body)
        conn.commit()
        conn.close()

        env = dict(os.environ, EIDETIC_MEMORY_SYSTEM=self.tmp)
        proc = subprocess.run(
            [sys.executable, os.path.join(BIN_DIR, "compound.py"), self.tmp],
            input="Rule: always run the ruby linter before committing changes\n",
            text=True, capture_output=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1 flagged", proc.stderr)
        self.assertIn(f"possible duplicate of {card_path}", proc.stderr)
        # The signal still lands as a new card (never lost)...
        self.assertTrue(glob.glob(os.path.join(self.tmp, "signals", "*.md")))
        # ...and the protected card was NOT written into.
        with open(card_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), card_text)


if __name__ == "__main__":
    unittest.main()
