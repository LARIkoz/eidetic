#!/usr/bin/env python3
"""NO-EMBED end-to-end proof of the curate WRITE -> demote chain on the REAL code.

Chain under test (all production modules, zero mocks):
  curate._set_status_archived  (writer: frontmatter status: archived, atomic, reversible)
    -> index_impl.parse_frontmatter / infer_status / index_file  (real indexer reads status:)
    -> search_impl.search / _run_query  (real ranker applies STATUS_WEIGHTS["archived"]=0.25)

Pure FTS5: the temp DB lives in a dir with NO vectors.db sibling, so
search_impl's `os.path.exists(vector_db)` is False and _vector_search
(fastembed) is never reached. Query is ASCII over an English corpus, and the
translate backend defaults to "off", so no translate/model/network is touched.

Mutates ONLY a fresh tempfile.mkdtemp() dir, removed in tearDown. Never touches
the live runtime DB or real memory cards.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import curate          # noqa: E402  the WRITER under test
import index_impl      # noqa: E402  the real indexer
import search_impl     # noqa: E402  the real ranker

CARD = """---
name: Widget Throttle Tuning Note
type: project
evidence: validated
source: user-explicit
---

# Widget Throttle Tuning Note

The widget throttle subsystem governs flux capacitor alignment for the
zorblax manifold. Tuning the widget throttle keeps the manifold stable.
"""

# An ASCII content word that is unique to the card and survives porter/unicode61
# tokenization (not a stopword) -> the FTS MATCH will hit only the target card.
QUERY = "zorblax manifold widget throttle"

# Off-topic filler cards (disjoint vocabulary). A single-document FTS5 corpus
# gives BM25 rank 0.0 (degenerate IDF) -> compound score 0 regardless of status,
# which would make the 0.25x score-ratio assertion vacuous (0 == 0). Real filler
# docs restore non-zero IDF so fts_rank is non-zero and the score-ratio test has
# teeth: fts_rank is identical before/after, so status_weight is the ONLY changed
# factor in the compound product.
FILLER = ("Document about gardening soil compost and watering schedules for "
          "tomatoes basil and rosemary in a raised bed during spring.")


def _build_fts_index(db_path, card_path, fillers=()):
    """Run the REAL production index path (init_db -> parse -> index_file) for
    the target card plus any filler cards. FTS only — no embed step is ever
    invoked, no vectors.db is ever created."""
    conn = index_impl.init_db(db_path)
    for p in (card_path, *fillers):
        with open(p, encoding="utf-8") as f:
            text = f.read()
        meta, body = index_impl.parse_frontmatter(text)
        index_impl.index_file(conn, p, meta, body)
    conn.commit()
    conn.close()


def _status_weight_for(db_path, card_path):
    """Drive the real lower-level retrieval (_run_query, no translation layer)
    and return (status_weight, score) for our card, plus the full result."""
    results = search_impl._run_query(db_path, QUERY, limit=10, type_filter=None, warn=False)
    mine = [r for r in results if r["path"] == card_path]
    assert mine, f"card not retrieved by FTS for {QUERY!r}; got {[r['path'] for r in results]}"
    r = mine[0]
    return r["status_weight"], r["score"], r


def _no_vectors_db(d):
    """Guarantee the pure-FTS branch: no vectors.db sibling can exist."""
    return not os.path.exists(os.path.join(d, "vectors.db"))


class CurateDemoteEndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="curate-demote-")
        self.db = os.path.join(self.d, "index.db")
        self.card = os.path.join(self.d, "widget-throttle.md")
        with open(self.card, "w", encoding="utf-8") as f:
            f.write(CARD)
        # off-topic filler cards so BM25 IDF is non-degenerate (see FILLER note)
        self.fillers = []
        for i in range(6):
            fp = os.path.join(self.d, f"filler-{i}.md")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"---\nname: Filler {i}\ntype: project\n---\n\n# Filler {i}\n{FILLER} (item {i})\n")
            self.fillers.append(fp)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_write_then_demote_chain(self):
        self.assertTrue(_no_vectors_db(self.d), "vectors.db must not exist -> pure FTS")

        # 1) BASELINE: card is status:current. Real indexer + real ranker.
        _build_fts_index(self.db, self.card, self.fillers)
        w_before, score_before, r_before = _status_weight_for(self.db, self.card)
        self.assertEqual(r_before["status"], "current",
                         "indexer must read the explicit current status")
        self.assertEqual(w_before, 1.0,
                         "current -> STATUS_WEIGHTS['current']=1.0")
        self.assertGreater(score_before, 0.0)

        # 2) WRITE: the curate writer flips frontmatter on the TEMP card only.
        res = curate._set_status_archived(self.card)
        self.assertEqual(res, "archived")
        with open(self.card, encoding="utf-8") as f:
            on_disk = f.read()
        self.assertIn("status: archived", on_disk)
        self.assertIn("zorblax manifold", on_disk)   # body preserved by the writer

        # 3) RE-INDEX the temp (FTS only) so the new status reaches the index.
        os.remove(self.db)                            # rebuild from the mutated card
        _build_fts_index(self.db, self.card, self.fillers)

        # 4) DEMOTE proven on the real ranker: archived -> 0.25 status weight,
        #    and the compound score is exactly 0.25x the prior (only status_w changed).
        w_after, score_after, r_after = _status_weight_for(self.db, self.card)
        self.assertEqual(r_after["status"], "archived",
                         "indexer must read the explicit archived status the writer set")
        self.assertEqual(w_after, 0.25,
                         "archived -> STATUS_WEIGHTS['archived']=0.25 in search_impl")
        self.assertAlmostEqual(score_after, score_before * 0.25, places=4,
                               msg="compound score must drop to exactly 0.25x (status_w is the only changed factor)")

        # echo the numbers for the evidence trail
        print(json.dumps({
            "status_before": r_before["status"], "weight_before": w_before, "score_before": score_before,
            "writer_result": res,
            "status_after": r_after["status"], "weight_after": w_after, "score_after": score_after,
            "ratio": round(score_after / score_before, 6),
            "vectors_db_exists": os.path.exists(os.path.join(self.d, "vectors.db")),
        }, indent=2))

    def test_public_search_entrypoint_also_demotes(self):
        """Prove it through the TOP-LEVEL search() (json_object), not just _run_query."""
        curate._set_status_archived(self.card)
        _build_fts_index(self.db, self.card, self.fillers)

        # capture the json the real CLI entry prints
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            search_impl.search(self.db, QUERY, limit=10, json_object=True)
        payload = json.loads(buf.getvalue())
        mine = [r for r in payload["results"] if r["path"] == self.card]
        self.assertTrue(mine, "public search() must retrieve the archived card")
        self.assertEqual(mine[0]["status"], "archived")
        self.assertEqual(mine[0]["status_weight"], 0.25,
                         "top-level search() applies the 0.25 archived weight end-to-end")

    def test_writer_is_reversible_and_idempotent_on_temp(self):
        """Revert + idempotency on the temp card (mirrors the prod reversibility claim)."""
        self.assertEqual(curate._set_status_archived(self.card), "archived")
        self.assertEqual(curate._set_status_archived(self.card), "already")  # idempotent

        # revert: writer can set status back to current (the documented undo).
        self.assertEqual(curate._set_status_archived(self.card, value="current"), "archived")
        with open(self.card, encoding="utf-8") as f:
            self.assertIn("status: current", f.read())

        # and a reverted card re-indexes back to weight 1.0
        _build_fts_index(self.db, self.card, self.fillers)
        w, _score, r = _status_weight_for(self.db, self.card)
        self.assertEqual(r["status"], "current")
        self.assertEqual(w, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
