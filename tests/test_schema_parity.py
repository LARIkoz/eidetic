"""KEEP #3 — the writer (index_impl) and reader (search_impl) schema-migration
paths must share ONE source of truth: a column added on one path must exist on
the other. They used to keep two hand-maintained lists that DRIFTED — the reader
lacked the `*_explicit` columns and the writer lacked `project` — so an index
migrated by one path had a different effective schema than the other expected.

unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import constants  # noqa: E402
import index_impl  # noqa: E402
import search_impl  # noqa: E402

# A minimal LEGACY memory_chunks table: the pre-derived-columns shape an old
# index.db would have. Both migration paths must bring it to the SAME schema.
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


def _columns_after(migrate):
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEGACY_SCHEMA)
    migrate(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    conn.close()
    return cols


class SchemaParityTest(unittest.TestCase):
    def test_writer_and_reader_migrations_produce_identical_schema(self):
        index_cols = _columns_after(index_impl.migrate_schema)
        search_cols = _columns_after(search_impl.ensure_agent_columns)
        self.assertEqual(
            index_cols, search_cols,
            "index_impl.migrate_schema and search_impl.ensure_agent_columns "
            "produced diverging effective schemas:\n"
            f"  only in writer: {sorted(index_cols - search_cols)}\n"
            f"  only in reader: {sorted(search_cols - index_cols)}",
        )

    def test_both_paths_add_the_relation_and_project_columns(self):
        # The exact columns the two lists used to disagree on.
        for migrate in (index_impl.migrate_schema, search_impl.ensure_agent_columns):
            cols = _columns_after(migrate)
            for required in ("project", "superseded_by_explicit", "contradicted_by_explicit"):
                self.assertIn(required, cols,
                              f"{migrate.__module__}.{migrate.__name__} did not add {required!r}")

    def test_both_paths_reference_the_shared_migration_source(self):
        # Structural guarantee: neither path may hardcode its own column list.
        self.assertIs(index_impl.DERIVED_COLUMNS, constants.MEMORY_CHUNK_MIGRATIONS)
        self.assertIs(search_impl.MEMORY_CHUNK_MIGRATIONS, constants.MEMORY_CHUNK_MIGRATIONS)


if __name__ == "__main__":
    unittest.main()
