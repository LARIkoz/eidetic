#!/usr/bin/env python3
"""Tests for the topic-base feature — index_impl scan-scope isolation (P1) and the
`eidetic base` CLI. The load-bearing guarantee: a base (with .eidetic-base.json) scans
ONLY its corpus_dirs; a personal index (no manifest) is byte-identical to before."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
sys.path.insert(0, BIN)

import index_impl  # noqa: E402
import base  # noqa: E402


def _w(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _r(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _mk_base(corpus_dirs=("docs", "notes")):
    root = tempfile.mkdtemp(suffix="-base")
    for sub in ("docs", os.path.join("docs", "api"), "notes"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    _w(os.path.join(root, ".eidetic-base.json"),
       json.dumps({"name": "tb", "corpus_dirs": list(corpus_dirs), "db": "db/index.db"}))
    _w(os.path.join(root, "docs", "HOME.md"), "# Home")
    _w(os.path.join(root, "docs", "api", "endpoint.md"), "# GET /x")   # nested
    _w(os.path.join(root, "notes", "fact.md"), "# a fact")
    _w(os.path.join(root, "docs", "draft.md.bak"), "skip")            # .bak skipped
    _w(os.path.join(root, "docs", "MEMORY.md"), "skip")               # excluded
    return root


class ScanScopeIsolationTest(unittest.TestCase):
    def setUp(self):
        self.base = _mk_base()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.base, ignore_errors=True))

    def test_manifest_detection(self):
        self.assertIsInstance(index_impl.base_manifest(self.base), dict)
        self.assertIsNone(index_impl.base_manifest(tempfile.mkdtemp()))   # no manifest
        self.assertIsNone(index_impl.base_manifest(None))

    def test_base_scans_only_its_corpus_recursively(self):
        files = index_impl.collect_files(self.base)
        rel = sorted(os.path.relpath(p, self.base) for p in files)
        self.assertEqual(rel, ["docs/HOME.md", "docs/api/endpoint.md", "notes/fact.md"])

    def test_base_never_scans_claude(self):
        # P1: every collected file must resolve INSIDE the base root. Check the REALPATH,
        # not the path string — a symlinked corpus dir yields in-base path strings while
        # its realpath points elsewhere, so a naive `"/.claude/" in p` test is fooled.
        base_real = os.path.realpath(self.base)
        for p in index_impl.collect_files(self.base):
            self.assertTrue(os.path.realpath(p).startswith(base_real + os.sep),
                            f"{p} escapes base root {base_real}")

    def test_absolute_corpus_dir_escape_refused(self):
        # P1 containment: an absolute corpus_dir pointing OUTSIDE the base collects ZERO
        # of those files (a malformed manifest must not leak personal memory) — but the
        # legit relative corpus_dirs still work.
        outside = tempfile.mkdtemp(suffix="-outside")
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        _w(os.path.join(outside, "secret.md"), "# personal-ish")
        b = _mk_base()
        self.addCleanup(lambda: shutil.rmtree(b, ignore_errors=True))
        _w(os.path.join(b, ".eidetic-base.json"),
           json.dumps({"name": "abs", "corpus_dirs": ["docs", outside], "db": "db/index.db"}))
        got = index_impl.collect_files(b)
        self.assertFalse(any("secret.md" in p for p in got), "absolute escaping corpus_dir leaked")
        self.assertTrue(any(p.endswith("HOME.md") for p in got), "legit docs/ dropped")

    def test_symlinked_corpus_dir_escape_refused(self):
        # P1 containment: a corpus dir that is a SYMLINK pointing outside the base must
        # collect none of the target's files (the vector the string-test missed).
        outside = tempfile.mkdtemp(suffix="-outside")
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        _w(os.path.join(outside, "secret.md"), "# personal-ish")
        b = tempfile.mkdtemp(suffix="-base")
        self.addCleanup(lambda: shutil.rmtree(b, ignore_errors=True))
        _w(os.path.join(b, ".eidetic-base.json"),
           json.dumps({"name": "sym", "corpus_dirs": ["docs"], "db": "db/index.db"}))
        os.symlink(outside, os.path.join(b, "docs"))   # docs -> outside
        self.assertFalse(any("secret.md" in p for p in index_impl.collect_files(b)),
                         "symlinked corpus dir leaked files")

    def test_bak_and_excluded_skipped(self):
        names = {os.path.basename(p) for p in index_impl.collect_files(self.base)}
        self.assertNotIn("draft.md.bak", names)
        self.assertNotIn("MEMORY.md", names)

    def test_personal_index_unchanged(self):
        # manifest-absent path = existing behavior: still scans ~/.claude/projects
        personal = os.path.expanduser("~/.claude/memory-system")
        if not os.path.isdir(personal):
            self.skipTest("no personal memory-system on this box")
        files = index_impl.collect_files(personal)
        self.assertTrue(any("/.claude/projects/" in p for p in files))


class BaseCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        os.environ["EIDETIC_BASES_REGISTRY"] = os.path.join(self.tmp, "registry.json")
        base.REGISTRY = os.environ["EIDETIC_BASES_REGISTRY"]
        self.addCleanup(lambda: os.environ.pop("EIDETIC_BASES_REGISTRY", None))
        self._real_index = base._run_index
        base._run_index = lambda *a, **k: 0          # don't shell out to index.sh in unit tests
        self.addCleanup(lambda: setattr(base, "_run_index", self._real_index))

    def test_slug(self):
        self.assertEqual(base._slug("POST /transactions!"), "post-transactions")
        self.assertEqual(base._slug("", "x"), "x")

    def test_init_scaffolds_and_registers(self):
        base.cmd_init(argparse.Namespace(name="acme", dir=self.tmp))
        root = os.path.join(self.tmp, "acme-base")
        self.assertTrue(os.path.exists(os.path.join(root, ".eidetic-base.json")))
        self.assertTrue(os.path.exists(os.path.join(root, "docs", "HOME.md")))
        self.assertTrue(os.path.isdir(os.path.join(root, "notes")))
        self.assertEqual(_r(os.path.join(root, ".gitignore")).strip(), "db/")
        self.assertIn("acme", base._load_registry())

    def test_init_honors_bases_dir_env(self):
        # no --dir → falls back to $EIDETIC_BASES_DIR, never cwd (so a base can't land
        # loose inside whatever project you happen to be in)
        os.environ["EIDETIC_BASES_DIR"] = self.tmp
        self.addCleanup(lambda: os.environ.pop("EIDETIC_BASES_DIR", None))
        base.cmd_init(argparse.Namespace(name="envbase", dir=None))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "envbase-base", ".eidetic-base.json")))
        self.assertIn("envbase", base._load_registry())

    def test_resolve_base_by_path_and_name(self):
        root = _mk_base()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        self.assertEqual(base.resolve_base(root), os.path.abspath(root))
        base._register("byname", root)
        self.assertEqual(base.resolve_base("byname"), os.path.abspath(root))

    def test_resolve_base_missing_exits(self):
        with self.assertRaises(SystemExit):
            base.resolve_base("/no/such/base-xyz")

    def test_add_routes_small_to_note_large_to_doc(self):
        root = _mk_base()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        base.cmd_add(argparse.Namespace(name=root, file=None, text="short fact", title="t1", as_=None))
        self.assertTrue(os.path.exists(os.path.join(root, "notes", "t1.md")))
        big = "x " * 1500  # > ADD_SIZE_THRESHOLD → doc
        base.cmd_add(argparse.Namespace(name=root, file=None, text=big, title="big", as_=None))
        self.assertTrue(os.path.exists(os.path.join(root, "docs", "big.md")))
        body = _r(os.path.join(root, "notes", "t1.md"))
        self.assertIn("type: note", body)
        self.assertIn("source: user", body)

    def test_add_honors_explicit_as(self):
        root = _mk_base()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        base.cmd_add(argparse.Namespace(name=root, file=None, text="short", title="forced", as_="doc"))
        self.assertTrue(os.path.exists(os.path.join(root, "docs", "forced.md")))

    def test_init_rejects_invalid_name(self):
        # I1: a base name becomes an MCP tool prefix + is shell-printed by `attach` — reject
        # anything that could inject or emit a protocol-invalid tool name.
        for bad in ("Bad Name", "UPPER", "foo;rm -rf x", "9starts", "has/slash", "a" * 50):
            with self.assertRaises(SystemExit):
                base.cmd_init(argparse.Namespace(name=bad, dir=self.tmp))
        # a clean name still works
        base.cmd_init(argparse.Namespace(name="good-base_1", dir=self.tmp))
        self.assertIn("good-base_1", base._load_registry())

    def test_doctor_empty_index_is_not_green(self):
        # I3: an init'd-but-empty (0-chunk) index must NOT report green
        root = _mk_base()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        db = os.path.join(root, "db", "index.db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        index_impl.init_db(db)                      # real schema, 0 chunks
        self.assertEqual(base.cmd_doctor(argparse.Namespace(name=root)), 2)

    def test_register_atomic_keeps_all(self):
        # I4: the locked/unique-tmp register path keeps every entry (no lost writes)
        for nm in ("alpha", "beta", "gamma"):
            base._register(nm, os.path.join(self.tmp, f"{nm}-base"))
        reg = base._load_registry()
        for nm in ("alpha", "beta", "gamma"):
            self.assertIn(nm, reg)


if __name__ == "__main__":
    unittest.main()
