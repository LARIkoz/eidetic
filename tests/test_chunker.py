"""Size-aware recursive chunking + honest embed window (spec-chunker AC-1..AC-7).

unittest so it runs under pytest + `unittest discover`. FTS-only leg exercises
chunking fully; the vectored leg additionally exercises the embed window (AC-6).
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import constants  # noqa: E402
import index_impl  # noqa: E402

CEIL = constants.MAX_CHUNK_CHARS  # 6000


def _chunk(body, path="/mem/x.md"):
    return index_impl.split_sections(body, path)


def _index_top(body, filename, query, limit=1):
    """Index a single file's chunks into a temp DB, return (top_headings, all_headings)."""
    tmp = tempfile.mkdtemp(prefix="chunk-fts-")
    try:
        mem = os.path.join(tmp, "memory")
        os.makedirs(mem)
        path = os.path.join(mem, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        db = os.path.join(tmp, "db", "index.db")
        conn = index_impl.init_db(db)
        meta, parsed = index_impl.parse_frontmatter(body)
        index_impl.index_file(conn, path, meta, parsed)
        conn.commit()
        top = [r[0] for r in conn.execute(
            "SELECT c.section_heading FROM memory_fts JOIN memory_chunks c "
            "ON memory_fts.rowid = c.id WHERE memory_fts MATCH ? "
            "ORDER BY memory_fts.rank LIMIT ?", (query, limit)).fetchall()]
        allh = [r[0] for r in conn.execute(
            "SELECT section_heading FROM memory_chunks").fetchall()]
        conn.close()
        return top, allh
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- AC-3 golden
ZERO_CHURN_BODY = """Intro prose before any heading.

## Alpha

Content of alpha section with some words.

### sub of alpha

nested prose under alpha.

## Beta

Beta content here.

```python
## not a heading (in fence)
def f(): pass
```

more beta.

## Alpha

Duplicate alpha heading content.
"""

GOLDEN = [
    ("note", "Intro prose before any heading."),
    ("Alpha", "Content of alpha section with some words.\n\n### sub of alpha\n\nnested prose under alpha."),
    ("Beta", "Beta content here.\n\n```python\n## not a heading (in fence)\ndef f(): pass\n```\n\nmore beta."),
    ("Alpha (2)", "Duplicate alpha heading content."),
]


class ZeroChurnTest(unittest.TestCase):
    def test_ac3_normal_card_byte_identical(self):
        # FR-4: every H2 <= ceiling → byte-identical to the pre-change chunker.
        self.assertTrue(all(len(c) <= CEIL for _h, c in GOLDEN))  # fixture is small
        self.assertEqual(_chunk(ZERO_CHURN_BODY, "/mem/note.md"), GOLDEN)


class CatalogMonsterTest(unittest.TestCase):
    def _catalog(self, n=200):
        fields = "".join(
            f"#### field-{i}\n\nvalue for field {i} " + ("lorem ipsum " * 20) + "\n\n"
            for i in range(n))
        fields += "#### addeddate\n\nthe addeddate metadata value is 2026-07-04 unique-token\n\n"
        return "## Catalog\n\n" + fields

    def test_ac1_every_chunk_under_ceiling_and_field_ranks_one(self):
        body = self._catalog()
        self.assertGreater(len(body), 45000)  # ~50 KB monster
        chunks = _chunk(body, "/mem/cat.md")
        self.assertTrue(all(len(c) <= CEIL for _h, c in chunks),
                        f"a chunk exceeded {CEIL}: max={max(len(c) for _h,c in chunks)}")
        self.assertGreaterEqual(len(chunks), 190, "catalog did not split into per-field chunks")
        # each field is its own breadcrumb chunk
        self.assertIn("Catalog › addeddate", [h for h, _ in chunks])
        top, _all = _index_top(body, "cat.md", "addeddate")
        self.assertEqual(top[0], "Catalog › addeddate", "field query did not rank its own chunk #1")


class HierarchyTest(unittest.TestCase):
    def _wal(self):
        # H2 'WAL mode' is oversized (two H3 children push it past the ceiling),
        # but each H3 child is ≤ ceiling so it stays one breadcrumb chunk. The
        # child's own content never says 'WAL' — only the breadcrumb carries it.
        child = "checkpoint flushes dirty pages back to the main database file. " + ("detail " * 700)
        other = "unrelated section body. " + ("filler " * 350)
        return (f"## WAL mode\n\nintro line.\n\n### checkpoint operation\n\n{child}\n\n"
                f"### another section\n\n{other}\n")

    def test_ac2_breadcrumb_preserves_parent_context(self):
        body = self._wal()
        chunks = _chunk(body, "/mem/wal.md")
        headings = [h for h, _ in chunks]
        self.assertIn("WAL mode › checkpoint operation", headings)
        # the child content itself never mentions WAL — only the breadcrumb carries it.
        child_content = dict(chunks)["WAL mode › checkpoint operation"]
        self.assertNotIn("WAL", child_content)
        top, _all = _index_top(body, "wal.md", "WAL checkpoint")
        self.assertEqual(top[0], "WAL mode › checkpoint operation",
                         "breadcrumb did not carry parent context into FTS (the PROMOTE_H4_TO_H2 failure mode)")


class WallOfTextTest(unittest.TestCase):
    def test_ac4_paragraph_windows_bounded_and_deterministic(self):
        paras = "\n\n".join(f"paragraph {i} " + ("word " * 60) for i in range(80))
        body = "no heading here.\n\n" + paras  # ~20 KB, heading-less
        self.assertGreater(len(body), 20000)
        chunks = _chunk(body, "/mem/wall.md")
        self.assertGreaterEqual(len(chunks), 2, "wall of text was not windowed")
        for h, c in chunks:
            self.assertIn("(part ", h)
            self.assertLessEqual(len(c), CEIL)
        # deterministic
        self.assertEqual(chunks, _chunk(body, "/mem/wall.md"))

    def test_ac4_single_oversized_block_is_its_own_window(self):
        giant = "x" * (CEIL + 3000)  # one unbreakable paragraph > ceiling
        body = "lead paragraph one.\n\n" + giant + "\n\ntrailing paragraph.\n"
        chunks = _chunk(body, "/mem/big.md")
        sizes = [len(c) for _h, c in chunks]
        self.assertTrue(any(s > CEIL for s in sizes), "the oversized block was wrongly split")
        self.assertEqual(max(sizes), len(giant), "the oversized window is bounded by the block")


class FenceSafetyTest(unittest.TestCase):
    def test_ac5_headings_inside_fences_are_never_boundaries(self):
        fence = "```\n## fake h2\n### fake h3\n#### fake h4\n" + ("payload line\n" * 400) + "```"
        body = f"## Real\n\nlead.\n\n### sub\n\n{fence}\n\n" + ("tail para " * 500)
        chunks = _chunk(body, "/mem/fence.md")
        # no chunk content starts/ends by cutting the fence, and no chunk heading
        # was derived from a fenced heading-like line.
        for h, _c in chunks:
            self.assertNotIn("fake h", h)
        # every fence in the output is balanced (never split across chunks).
        for _h, c in chunks:
            self.assertEqual(c.count("```") % 2, 0, "a code fence was split across a chunk boundary")


class EmbedWindowTest(unittest.TestCase):
    def test_ac6_embedding_text_and_hash_share_1500_window(self):
        import embed
        self.assertEqual(embed.HASH_SCHEME, "trunc1500-v3")
        long = "z" * 3000
        et = embed.embedding_text("n", "d", long, "h")
        # embedding_text carries content truncated to 1500 (not 500)
        self.assertIn("z" * 1500, et)
        self.assertNotIn("z" * 1501, et)
        # content_hash reads the SAME 1500 cut in lockstep: chars 500..1500 matter.
        h_at_1200 = embed.content_hash("n", "d", "z" * 1200, "h")
        h_at_1400 = embed.content_hash("n", "d", "z" * 1400, "h")
        self.assertNotEqual(h_at_1200, h_at_1400, "content_hash ignores chars past 500 (not lockstep)")
        # beyond 1500 the hash is stable (both cut at 1500)
        self.assertEqual(embed.content_hash("n", "d", "z" * 1600, "h"),
                         embed.content_hash("n", "d", "z" * 2000, "h"))

    def test_ac6_stale_hash_scheme_triggers_loud_degrade(self):
        import embed
        tmp = tempfile.mkdtemp(prefix="chunk-stamp-")
        try:
            db = os.path.join(tmp, "vectors.db")
            conn = embed.init_vector_db(db)
            for k, v in (("model", embed.MODEL_NAME), ("dim", str(embed.VECTOR_DIM)),
                         ("hash_scheme", "trunc500-v2")):
                conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, v))
            conn.commit()
            import io, contextlib
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ok = embed._vector_meta_ok(conn)
            conn.close()
            self.assertFalse(ok, "a trunc500-v2 store must be refused after the v3 bump")
            self.assertIn("index.sh --full", err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
