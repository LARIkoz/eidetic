"""Wave 2 — the promotion primitive + op-log.

Covers Karpathy's "file good answers back as pages": remember.promote writes a
typed card, a re-promote compounds into a `## Update` section (never duplicates),
an identical body is a no-op, and every op lands on the greppable log.md.
Monkeypatches module globals so it is independent of run order and the real
memory store. unittest so it runs under `python3 -m unittest discover` + pytest.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import compound  # noqa: E402
import oplog  # noqa: E402
import remember  # noqa: E402

NOMATCH_CWD = "/zzz-eidetic-test-nomatch-dir"


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class RememberOplogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eidetic-rem-")
        # Redirect all write targets into the temp store.
        self._saved = (compound.MEMORY_SYSTEM, compound.DB_PATH, oplog.LOG_PATH)
        compound.MEMORY_SYSTEM = self.tmp
        compound.DB_PATH = os.path.join(self.tmp, "db", "index.db")  # absent → no FTS dedup
        oplog.LOG_PATH = os.path.join(self.tmp, "log.md")

    def tearDown(self):
        compound.MEMORY_SYSTEM, compound.DB_PATH, oplog.LOG_PATH = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- oplog ---
    def test_oplog_appends_greppable_entry(self):
        p = oplog.append_op("promote", "a title", project="/x/y/proj", detail="d", count=2,
                            log_path=os.path.join(self.tmp, "l.md"))
        text = _read(p)
        self.assertIn("## [", text)
        self.assertIn("promote — a title", text)
        self.assertIn("project: proj", text)   # path → basename slug
        self.assertIn("count: 2", text)

    # --- promote: new ---
    def test_promote_new_card_has_typed_frontmatter(self):
        path, action = remember.promote("My Synthesized Answer", "Body line.",
                                        kind="synthesis", cwd=NOMATCH_CWD)
        self.assertEqual(action, "new")
        body = _read(path)
        self.assertIn("card_kind: synthesis", body)
        self.assertIn("type: project", body)
        self.assertIn("source: agent-extracted", body)
        self.assertTrue(os.path.basename(path).startswith("synthesis-"))

    def test_concept_kind_maps_to_reference_type(self):
        path, _ = remember.promote("AJTBD job graph", "Methodology.", kind="concept", cwd=NOMATCH_CWD)
        body = _read(path)
        self.assertIn("card_kind: concept", body)
        self.assertIn("type: reference", body)  # KIND_TO_TYPE override

    # --- promote: compounding ---
    def test_repromote_appends_update_not_duplicate(self):
        p1, _ = remember.promote("Same Topic", "First body.", cwd=NOMATCH_CWD)
        p2, action = remember.promote("Same Topic", "Second, newer body.", cwd=NOMATCH_CWD)
        self.assertEqual(p1, p2)            # same file
        self.assertEqual(action, "updated")
        body = _read(p1)
        self.assertIn("First body.", body)
        self.assertIn("Second, newer body.", body)
        self.assertIn("## Update", body)

    def test_repromote_identical_body_is_noop(self):
        remember.promote("Topic X", "Identical body.", cwd=NOMATCH_CWD)
        _p, action = remember.promote("Topic X", "Identical body.", cwd=NOMATCH_CWD)
        self.assertEqual(action, "noop")

    # --- slug + dir helpers ---
    def test_target_slug_prefixes_kind_once(self):
        self.assertEqual(remember.target_slug("Hello World", "synthesis"), "synthesis-hello-world")
        # already-prefixed title must not double-prefix
        self.assertEqual(remember.target_slug("synthesis already", "synthesis"), "synthesis-already")

    def test_resolve_memory_dir_falls_back_to_global(self):
        self.assertEqual(compound.resolve_memory_dir(NOMATCH_CWD), self.tmp)

    # --- consreview fixes ---
    def test_protected_feedback_card_is_never_appended_or_clobbered(self):
        # A same-slug user-validated feedback rule must be left fully intact:
        # not appended into (provenance), not overwritten (data loss).
        slug = remember.target_slug("Stay Tight", "rule")  # -> rule-stay-tight, type feedback
        prot = os.path.join(self.tmp, f"{slug}.md")
        with open(prot, "w", encoding="utf-8") as f:
            f.write("---\nname: rule-stay-tight\ntype: feedback\nsource: user-explicit\n---\n# rule\nUSER RULE BODY\n")
        before = _read(prot)
        path, action = remember.promote("Stay Tight", "agent-authored synthesis text",
                                        kind="rule", cwd=NOMATCH_CWD)
        self.assertEqual(action, "new")
        self.assertNotEqual(path, prot)            # relocated, not the protected path
        self.assertEqual(_read(prot), before)      # protected card byte-for-byte intact

    def test_cyrillic_titles_get_distinct_slugs_not_note(self):
        s1 = remember.target_slug("Привычка возврата", "synthesis")
        s2 = remember.target_slug("Четыре силы переключения", "synthesis")
        self.assertNotEqual(s1, s2)               # not all collapsing to one slug
        self.assertNotIn(s1, ("note", "synthesis-note"))
        # same cyrillic title -> same slug -> re-promote compounds (tier-1), no dup
        p1, _ = remember.promote("Привычка возврата", "первое тело ответа", cwd=NOMATCH_CWD)
        p2, action = remember.promote("Привычка возврата", "второе, новое тело", cwd=NOMATCH_CWD)
        self.assertEqual(p1, p2)
        self.assertEqual(action, "updated")

    def test_update_rejects_non_memory_path(self):
        bogus = os.path.join(self.tmp, "not_a_memory.txt")  # no /memory/ in path
        with open(bogus, "w", encoding="utf-8") as f:
            f.write("zsh stuff")
        with self.assertRaises(ValueError):
            remember.promote("T", "body", cwd=NOMATCH_CWD, update_path=bogus)
        self.assertEqual(_read(bogus), "zsh stuff")  # untouched


if __name__ == "__main__":
    unittest.main()
