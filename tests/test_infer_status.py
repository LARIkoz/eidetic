#!/usr/bin/env python3
"""Regression guard for infer_status (2026-06-25 fix).

Bug: infer_status used to infer lifecycle status from name+description keywords
(_slug_text folds in the description), so a CURRENT card whose title or prose
merely MENTIONED a lifecycle word was silently demoted in search:
  - a finding ABOUT a fix ("...Fixed 2026-06-25")  -> "resolved"  (0.75x)
  - any card with "archive" in its name/description -> "archived"  (0.25x)
Real archival is set EXPLICITLY via frontmatter `status:` (curate archive --apply)
and `superseded_by`, so the keyword fallback was removed. A card is now `current`
unless it declares otherwise. These tests pin that contract.
"""

import os
import sys
import unittest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import index_impl  # noqa: E402


class InferStatusExplicitOnly(unittest.TestCase):
    # --- the fix: name/description keyword mentions must NOT demote ---

    def test_fixed_in_description_stays_current(self):
        # the exact shape that mis-fired: a finding documenting a fix
        meta = {"name": "finding-handoff-race", "type": "finding",
                "description": "Cross-session bug. Fixed 2026-06-25."}
        self.assertEqual(index_impl.infer_status(meta, "finding-handoff-race.md"), "current")

    def test_archive_word_in_description_stays_current(self):
        meta = {"name": "handoff-cleanup", "type": "project",
                "description": "How to archive stale handoff dirs."}
        self.assertEqual(index_impl.infer_status(meta, "handoff-cleanup.md"), "current")

    def test_archive_word_in_name_stays_current(self):
        meta = {"name": "finding-memory-index-archive", "type": "reference"}
        self.assertEqual(index_impl.infer_status(meta, "finding-memory-index-archive.md"), "current")

    def test_superseded_word_in_name_stays_current(self):
        # only an explicit superseded_by / status: should mark it superseded
        meta = {"name": "note-superseded-approach", "type": "reference"}
        self.assertEqual(index_impl.infer_status(meta, "note-superseded-approach.md"), "current")

    def test_resolved_word_in_filename_stays_current(self):
        # no meta name -> falls back to basename, which also must not demote
        self.assertEqual(index_impl.infer_status({}, "bug-x-resolved.md"), "current")

    # --- still honoured: the explicit signals ---

    def test_explicit_status_wins(self):
        meta = {"name": "X", "type": "project", "status": "archived"}
        self.assertEqual(index_impl.infer_status(meta, "x.md"), "archived")

    def test_explicit_status_is_lowercased_and_stripped(self):
        meta = {"name": "X", "status": "  Deprecated  "}
        self.assertEqual(index_impl.infer_status(meta, "x.md"), "deprecated")

    def test_superseded_by_meta_marks_superseded(self):
        meta = {"name": "X", "superseded_by": "other-card"}
        self.assertEqual(index_impl.infer_status(meta, "x.md"), "superseded")

    def test_explicit_status_beats_superseded_by(self):
        meta = {"name": "X", "status": "current", "superseded_by": "other"}
        self.assertEqual(index_impl.infer_status(meta, "x.md"), "current")

    def test_plain_card_is_current(self):
        meta = {"name": "finding-y", "type": "finding", "description": "a normal finding"}
        self.assertEqual(index_impl.infer_status(meta, "finding-y.md"), "current")


if __name__ == "__main__":
    unittest.main(verbosity=2)
