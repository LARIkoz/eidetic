"""compound.py: project scope, History exclusion, repeated signals, worktree mapping.

Regression guards for the 2026-11-19 audit of the live store:
- a card's own `## History` attracted unrelated signals (snowball);
- signals from one project compounded into another project's cards;
- the Stop hook re-extracted the same signal and appended it twice
  (1,462 duplicate History lines across 197 cards);
- a linked git worktree session fell back to the unindexed global root.
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
from test_compound_dedup import SCHEMA  # noqa: E402

BIN_DIR = os.path.join(os.path.dirname(__file__), "..", "bin")

SIGNAL = "Worked: tier review accepted by reviewers confirming quarterly payroll reconciliation fixes"


class HistoryChunkExclusionTest(unittest.TestCase):
    """A card whose BODY is unrelated must not match through its History chunk."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        path = "/tmp/eidetic-test/memory/image-routes.md"
        rows = [
            (path, "Image Routes", "Image Routes",
             "Which image routes draw pop art headless: grok imagine passes, others refuse."),
            (path, "Image Routes", "History",
             "- 2026-11-11: Worked: tier review accepted by reviewers confirming quarterly "
             "payroll reconciliation fixes"),
        ]
        for p, name, heading, content in rows:
            self.conn.execute(
                "INSERT INTO memory_chunks (path, project, name, type, section_heading, content, description) "
                "VALUES (?,?,?,?,?,?,?)", (p, "t", name, "reference", heading, content, name))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_history_chunk_is_not_a_match(self):
        keywords = compound.extract_keywords(SIGNAL)
        self.assertEqual(compound.search_fts5(self.conn, keywords, limit=3), [])
        self.assertEqual(compound.find_overlap_candidate(self.conn, keywords, SIGNAL), (None, None))

    def test_part_n_history_chunk_is_also_excluded(self):
        self.conn.execute("UPDATE memory_chunks SET section_heading='History (part 2)' "
                          "WHERE section_heading='History'")
        keywords = compound.extract_keywords(SIGNAL)
        self.assertEqual(compound.search_fts5(self.conn, keywords, limit=3), [])


class ScopeTest(unittest.TestCase):
    def test_in_scope(self):
        self.assertTrue(compound.in_scope("/a/b/memory/x.md", None))
        self.assertTrue(compound.in_scope("/a/b/memory/x.md", "/a/b/memory"))
        self.assertTrue(compound.in_scope("/a/b/memory/signals/x.md", "/a/b/memory/"))
        self.assertFalse(compound.in_scope("/a/c/memory/x.md", "/a/b/memory"))
        # prefix of a sibling dir name is not inside it
        self.assertFalse(compound.in_scope("/a/b/memory-old/x.md", "/a/b/memory"))

    def test_overlap_candidate_respects_scope(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO memory_chunks (path, project, name, type, section_heading, content, description) "
            "VALUES (?,?,?,?,?,?,?)",
            ("/x/other-project/memory/payroll.md", "o", "Payroll", "project", "Payroll",
             "Quarterly payroll reconciliation fixes were accepted after tier review by reviewers.",
             "Payroll"))
        conn.commit()
        keywords = compound.extract_keywords(SIGNAL)
        action, _ = compound.find_overlap_candidate(conn, keywords, SIGNAL)
        self.assertEqual(action, "compound")
        self.assertEqual(
            compound.find_overlap_candidate(conn, keywords, SIGNAL, scope_root="/x/this-project/memory"),
            (None, None))
        conn.close()


class NormalizeSignalsTest(unittest.TestCase):
    def test_repeated_lines_collapse_in_order(self):
        raw = "Decision: a\nnoise\nDecision: a\nRule: b\n  Rule: b  \nKnowledge: c\n"
        self.assertEqual(compound.normalize_signals(raw), ["Decision: a", "Rule: b", "Knowledge: c"])


def _index_card(tmp, card_path, text):
    conn = index_impl.init_db(os.path.join(tmp, "db", "index.db"))
    meta, body = index_impl.parse_frontmatter(text)
    index_impl.index_file(conn, card_path, meta, body)
    conn.commit()
    conn.close()


class EndToEndTest(unittest.TestCase):
    """compound.py as the Stop hook runs it (fallback global store as the scope)."""

    CARD = """---
name: payroll-reconciliation
description: quarterly payroll reconciliation process
metadata:
  type: project
  source: user-explicit
---

The quarterly payroll reconciliation runs after the tier review; reviewers confirm
the fixes before the payroll export is accepted.
"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-scope-test-")
        self.env = dict(os.environ, EIDETIC_MEMORY_SYSTEM=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, signal):
        return subprocess.run(
            [sys.executable, os.path.join(BIN_DIR, "compound.py"), "/zzz-eidetic-scope-nomatch"],
            input=signal + "\n", text=True, capture_output=True, env=self.env, timeout=60)

    def test_repeated_signal_is_recorded_once(self):
        mem = os.path.join(self.tmp, "memory")
        os.makedirs(mem)
        card = os.path.join(mem, "payroll-reconciliation.md")
        with open(card, "w", encoding="utf-8") as f:
            f.write(self.CARD)
        _index_card(self.tmp, card, self.CARD)

        first = self._run(SIGNAL)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("1 compounded", first.stderr)
        second = self._run(SIGNAL)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("1 already recorded", second.stderr)
        with open(card, encoding="utf-8") as f:
            self.assertEqual(f.read().count(SIGNAL), 1)

    def test_card_outside_scope_is_never_written(self):
        # Same card, but in another project's memory dir: the session's scope is
        # the global store, so the signal becomes a new signal line instead.
        other = os.path.join(self.tmp, "projects", "other", "memory")
        os.makedirs(other)
        card = os.path.join(other, "payroll-reconciliation.md")
        with open(card, "w", encoding="utf-8") as f:
            f.write(self.CARD)
        _index_card(self.tmp, card, self.CARD)

        proc = self._run(SIGNAL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0 compounded, 1 new", proc.stderr)
        with open(card, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.CARD)
        self.assertTrue(glob.glob(os.path.join(self.tmp, "memory", "signals", "*.md")))


class GitWorktreeRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="eidetic-wt-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

    def test_linked_worktree_maps_to_main_checkout(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self._git("init", "-q", cwd=repo)
        self._git("commit", "-q", "--allow-empty", "-m", "init", cwd=repo)
        wt = os.path.join(self.tmp, "wt")
        self._git("worktree", "add", "-q", wt, "-b", "side", cwd=repo)
        self.assertEqual(compound._git_main_root(wt), repo)
        self.assertEqual(compound._git_main_root(repo), repo)

    def test_non_repo_is_none(self):
        self.assertIsNone(compound._git_main_root(self.tmp))


if __name__ == "__main__":
    unittest.main()
