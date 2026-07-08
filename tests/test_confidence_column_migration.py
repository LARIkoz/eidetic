"""Regression: a pre-STEP-1B index (memory_chunks with NO `confidence` column)
must survive the v6 upgrade. `confidence` lived ONLY in the base CREATE TABLE,
never in a migration, so `CREATE TABLE IF NOT EXISTS` never added it to an old
table — and every reader SELECTs `c.confidence`, so the first search/inject after
upgrade crashed with `no such column: c.confidence`. `confidence` is now a
reader-safe migration (DEFAULT 0.7); ensure_agent_columns adds it. unittest so it
runs under the project runner on both CI legs.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context  # noqa: E402
import search_impl  # noqa: E402

# The pre-confidence schema: everything a v5.12 index had, WITHOUT `confidence`.
_OLD_SCHEMA = """
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY, path TEXT, project TEXT, name TEXT, type TEXT,
    evidence TEXT DEFAULT 'observed', source TEXT DEFAULT 'user-explicit',
    last_verified TEXT, content TEXT, section_heading TEXT, description TEXT,
    card_kind TEXT DEFAULT '', status TEXT DEFAULT 'current', area TEXT DEFAULT '',
    supersedes TEXT DEFAULT '', superseded_by TEXT DEFAULT ''
)
"""


class ConfidenceColumnMigration(unittest.TestCase):
    def _old_index(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO memory_chunks (id, path, name, type, content, section_heading) "
            "VALUES (1, '/tmp/a.md', 'A', 'project', 'body', 'H')")
        conn.commit()
        return conn

    def _has_confidence(self, conn):
        return "confidence" in {r[1] for r in conn.execute("PRAGMA table_info(memory_chunks)")}

    def test_search_ensure_adds_confidence(self):
        conn = self._old_index()
        self.assertFalse(self._has_confidence(conn))
        search_impl.ensure_agent_columns(conn)
        self.assertTrue(self._has_confidence(conn), "search reader must add confidence to an old index")
        # and the default is the neutral 0.7 (base-schema default)
        self.assertEqual(conn.execute("SELECT confidence FROM memory_chunks WHERE id=1").fetchone()[0], 0.7)

    def test_assemble_ensure_adds_confidence(self):
        conn = self._old_index()
        assemble_context.ensure_agent_columns(conn)
        self.assertTrue(self._has_confidence(conn), "assemble_context must add confidence to an old index")

    def test_reader_select_confidence_survives_after_ensure(self):
        # The exact failure mode: SELECT c.confidence on an old index must not raise
        # once the reader has ensured its columns.
        conn = self._old_index()
        search_impl.ensure_agent_columns(conn)
        rows = conn.execute("SELECT c.confidence FROM memory_chunks c").fetchall()
        self.assertEqual(rows, [(0.7,)])


if __name__ == "__main__":
    unittest.main()
