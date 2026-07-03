"""KEEP #3 + audit F1/F3 — every schema-migration path shares ONE source.

Three paths touch the memory_chunks schema on an old index:
  * WRITER — index_impl.migrate_schema (adds every column + back-fills).
  * READER (search) — search_impl.ensure_agent_columns.
  * READER (inject) — assemble_context.ensure_agent_columns.

Invariants:
  - the two READERS produce identical schemas (no hand-maintained drift, F3);
  - the WRITER schema is a SUPERSET of the readers, and the extra columns are
    exactly the writer-back-fill set (F1 — readers must NOT add those, because
    adding them with DEFAULT '' and no file re-read erases deliberate demotions);
  - all three reference the shared constants source (no private column lists).

unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import assemble_context  # noqa: E402
import constants  # noqa: E402
import index_impl  # noqa: E402
import search_impl  # noqa: E402

LEGACY_SCHEMA = """
CREATE TABLE memory_chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    name TEXT,
    type TEXT,
    evidence TEXT DEFAULT 'observed',
    source TEXT DEFAULT 'user-explicit',
    confidence REAL DEFAULT 0.7,
    last_verified TEXT,
    section_heading TEXT,
    content TEXT NOT NULL,
    description TEXT,
    mtime INTEGER,
    UNIQUE(path, section_heading)
);
"""

WRITER = index_impl.migrate_schema
READERS = {
    "search_impl.ensure_agent_columns": search_impl.ensure_agent_columns,
    "assemble_context.ensure_agent_columns": assemble_context.ensure_agent_columns,
}


def _columns_after(migrate):
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEGACY_SCHEMA)
    migrate(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    conn.close()
    return cols


class SchemaParityTest(unittest.TestCase):
    def test_both_readers_produce_identical_schema(self):
        cols = {name: _columns_after(fn) for name, fn in READERS.items()}
        (a_name, a_cols), (b_name, b_cols) = cols.items()
        self.assertEqual(
            a_cols, b_cols,
            f"reader schemas diverge:\n  only in {a_name}: {sorted(a_cols - b_cols)}\n"
            f"  only in {b_name}: {sorted(b_cols - a_cols)}",
        )

    def test_writer_is_a_superset_and_the_extra_columns_are_the_backfill_set(self):
        writer_cols = _columns_after(WRITER)
        for name, fn in READERS.items():
            reader_cols = _columns_after(fn)
            self.assertTrue(reader_cols <= writer_cols,
                            f"{name} added a column the writer does not: "
                            f"{sorted(reader_cols - writer_cols)}")
            self.assertEqual(
                writer_cols - reader_cols, set(constants.WRITER_BACKFILL_MIGRATIONS),
                f"writer/{name} difference is not exactly the back-fill set",
            )

    def test_readers_never_add_backfill_columns(self):
        # F1: a read-only path must never create *_explicit/status_explicit.
        for name, fn in READERS.items():
            cols = _columns_after(fn)
            for backfill in constants.WRITER_BACKFILL_MIGRATIONS:
                self.assertNotIn(
                    backfill, cols,
                    f"{name} added back-fill column {backfill!r} (defeats the re-read)")

    def test_all_paths_add_the_columns_they_should(self):
        writer_cols = _columns_after(WRITER)
        for required in ("project", "superseded_by_explicit", "contradicted_by_explicit",
                         "status_explicit"):
            self.assertIn(required, writer_cols)
        for name, fn in READERS.items():
            self.assertIn("project", _columns_after(fn), name)
            self.assertIn("contradicted_by", _columns_after(fn), name)

    def test_every_path_references_the_shared_source(self):
        # No path may hardcode its own column list.
        self.assertIs(index_impl.DERIVED_COLUMNS, constants.MEMORY_CHUNK_MIGRATIONS)
        self.assertIs(search_impl.READER_SAFE_MIGRATIONS, constants.READER_SAFE_MIGRATIONS)
        self.assertIs(assemble_context.READER_SAFE_MIGRATIONS, constants.READER_SAFE_MIGRATIONS)


if __name__ == "__main__":
    unittest.main()
