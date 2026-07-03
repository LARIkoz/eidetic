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

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import compound  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
