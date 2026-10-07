"""CLI writer serialization and resumable, no-op incremental indexing."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import index_impl as idx  # noqa: E402


# Real CLI entrypoint with observation-only discovery/lock probes and fake
# semantic hooks. No test imports an embedding engine or uses the live corpus.
CLI_PROBE = r'''
import json, os, sys, time, types
sys.path.insert(0, sys.argv[1])
import index_impl as idx
db, label = sys.argv[2:4]
def event(name):
    with open(os.environ["INDEX_TEST_EVENTS"], "a") as f:
        f.write(json.dumps([label, name]) + "\n")
original_flock = idx.fcntl.flock
def flock(stream, flags):
    try:
        return original_flock(stream, flags)
    except BlockingIOError:
        if getattr(stream, "name", "").endswith(".index.lock"):
            event("waiting")
        raise
idx.fcntl.flock = flock
original_collect = idx.collect_files
def collect(root):
    event("scan")
    if os.environ.get("INDEX_TEST_FAIL_DISCOVERY"):
        raise RuntimeError("injected discovery failure")
    return original_collect(root)
idx.collect_files = collect
def m1(*args):
    event("m1")
    # Hooks importing the indexer must not acquire the CLI lock recursively.
    import index_impl
    if label == "first":
        deadline = time.monotonic() + 10
        while not os.path.exists(os.environ["INDEX_TEST_RELEASE"]):
            if time.monotonic() > deadline:
                raise RuntimeError("test did not release the first hook")
            time.sleep(0.01)
sys.modules["m1_contradiction"] = types.SimpleNamespace(run_on_ingest=m1)
sys.modules["m2_synthesis"] = types.SimpleNamespace(run_on_ingest=lambda *a: event("m2"))
sys.argv = ["index_impl.py", "--incremental", db]
sys.exit(idx.main(defer_semantics=False))
'''


class IndexSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="eidetic-index-scheduling-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "docs").mkdir()
        (self.root / ".eidetic-base.json").write_text(
            json.dumps({"corpus_dirs": ["docs"]}), encoding="utf-8")
        self.db = str(self.root / "db" / "index.db")
        self.events = self.root / "events.jsonl"
        self.release = self.root / "release"
        self.env = {
            "EIDETIC_RESOURCE_ROOT": str(self.root / "resource-state"),
            "EIDETIC_CONFIDENCE_EVENTS": "1",
            "EIDETIC_PRODUCER": "0",
            "INDEX_TEST_EVENTS": str(self.events),
            "INDEX_TEST_RELEASE": str(self.release),
            "EIDETIC_INDEX_LOCK_TIMEOUT": "5",
        }
        self.environment = mock.patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.m1 = mock.Mock()
        self.m2 = mock.Mock()
        self.modules = mock.patch.dict(sys.modules, {
            "m1_contradiction": types.SimpleNamespace(run_on_ingest=self.m1),
            "m2_synthesis": types.SimpleNamespace(run_on_ingest=self.m2),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def card(self, name="one", body="Original test content."):
        path = self.root / "docs" / f"{name}.md"
        path.write_text(f"---\nname: {name}\ntype: project\n---\n\n{body}\n",
                        encoding="utf-8")
        return str(path)

    def connection(self):
        conn = idx.init_db(self.db)
        self.addCleanup(conn.close)
        return conn

    def start_cli(self, label="normal", db=None, **environment):
        env = {**os.environ, **self.env, **environment}
        proc = subprocess.Popen(
            [sys.executable, "-c", CLI_PROBE, str(BIN), db or self.db, label],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

        def cleanup():
            if proc.poll() is None:
                proc.terminate()
            proc.communicate(timeout=10)
        self.addCleanup(cleanup)
        return proc

    def observed(self):
        if not self.events.exists():
            return []
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def await_event(self, label, event):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if [label, event] in self.observed():
                return
            time.sleep(0.01)
        self.fail(f"Missing {label}/{event}; observed {self.observed()}")

    def finished(self, proc):
        stdout, stderr = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, stderr)
        return stdout

    def test_waiting_cli_scans_latest_files_after_all_first_writer_hooks(self):
        first_path = self.card()
        first = self.start_cli("first")
        self.await_event("first", "m1")
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        second = self.start_cli("second", db=str(alias / "db" / "index.db"))
        self.await_event("second", "waiting")
        self.assertNotIn(["second", "scan"], self.observed())
        self.card(body="Changed while the second writer waited.")
        new_path = self.card("added", "Added while waiting.")
        self.release.touch()
        self.finished(first)
        self.finished(second)
        observed = self.observed()
        self.assertLess(observed.index(["first", "m2"]),
                        observed.index(["second", "scan"]))
        with sqlite3.connect(self.db) as conn:
            rows = dict(conn.execute("SELECT path, content FROM memory_chunks"))
        self.assertIn("Changed while", rows[first_path])
        self.assertIn("Added while", rows[new_path])

    def test_busy_cli_fails_explicitly_before_scan_or_database_creation(self):
        self.card()
        with idx.index_lock(self.db):
            proc = self.start_cli(EIDETIC_INDEX_LOCK_TIMEOUT="0.05")
            stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 75, stderr)
        self.assertIn("request was not indexed", stderr)
        self.assertEqual(stdout, "")
        self.assertFalse(Path(self.db).exists())
        self.assertNotIn(["normal", "scan"], self.observed())

    def test_failure_releases_lock_without_replacing_its_inode(self):
        self.card()
        proc = self.start_cli(INDEX_TEST_FAIL_DISCOVERY="1")
        _stdout, stderr = proc.communicate(timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("injected discovery failure", stderr)
        lock = Path(self.db + ".index.lock")
        inode = lock.stat().st_ino
        self.finished(self.start_cli())
        self.assertEqual(lock.stat().st_ino, inode)

    def test_noop_does_not_propagate_or_run_models_or_write_metadata(self):
        path = self.card()
        conn = self.connection()
        idx.run_incremental(conn, [path])
        self.m1.reset_mock()
        self.m2.reset_mock()
        changes = conn.total_changes
        with mock.patch.object(idx, "propagate_declared_relations") as propagate:
            result = idx.run_incremental(conn, [path])
        self.assertEqual(result, (0, 1, 0))
        self.assertEqual(conn.total_changes, changes)
        propagate.assert_not_called()
        self.m1.assert_not_called()
        self.m2.assert_not_called()

    def test_default_disabled_semantics_do_not_queue_or_call_hooks(self):
        paths = [self.card(str(n)) for n in range(5)]
        conn = self.connection()
        with mock.patch.dict(os.environ):
            os.environ.pop("EIDETIC_CONFIDENCE_EVENTS", None)
            self.assertEqual(idx.run_incremental(conn, paths), (5, 0, 0))
            self.assertEqual(idx.pending_ingest_paths(conn), [])
            changes = conn.total_changes
            self.assertEqual(idx.run_incremental(conn, paths), (0, 5, 0))
            self.assertEqual(conn.total_changes, changes)
        self.m1.assert_not_called()
        self.m2.assert_not_called()

    def test_disabled_caller_preserves_existing_queue_and_does_not_add_new_work(self):
        paths = [self.card(name) for name in ("old", "pending")]
        conn = self.connection()
        with mock.patch.dict(os.environ, {"EIDETIC_INGEST_BATCH_SIZE": "1"}):
            idx.run_incremental(conn, paths)
        self.assertEqual(idx.pending_ingest_paths(conn), paths[1:])
        self.m1.reset_mock()
        self.m2.reset_mock()
        new_path = self.card("created-while-disabled")
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"EIDETIC_CONFIDENCE_EVENTS": "0"}), \
                redirect_stdout(output):
            idx.run_incremental(conn, paths + [new_path])
            changes = conn.total_changes
            idx.run_incremental(conn, paths + [new_path])
            self.assertEqual(conn.total_changes, changes)
        self.assertEqual(idx.pending_ingest_paths(conn), paths[1:])
        self.assertIn("semantic hooks disabled; queue preserved", output.getvalue())
        self.m1.assert_not_called()
        self.m2.assert_not_called()
        idx.run_incremental(conn, paths + [new_path])
        self.m1.assert_called_once_with(conn, self.db, paths[1:])
        self.assertEqual(idx.pending_ingest_paths(conn), [])

    def test_producer_uses_current_changes_and_session_without_deferred_replay(self):
        paths = [self.card(str(n)) for n in range(5)]
        conn = self.connection()
        producer = mock.Mock()
        module = types.SimpleNamespace(produce_test_affirmations=producer)
        with mock.patch.dict(sys.modules, {"m3_autofile": module}), mock.patch.dict(
                os.environ, {"EIDETIC_PRODUCER": "1", "EIDETIC_SESSION_ID": "session-a"}):
            idx.run_incremental(conn, paths)
            producer.assert_called_once_with(self.db, session_id="session-a")
            self.assertEqual(idx.pending_ingest_paths(conn), paths[4:])
            producer.reset_mock()
            os.environ["EIDETIC_SESSION_ID"] = "session-b"
            idx.run_incremental(conn, paths)
            producer.assert_not_called()
            self.assertEqual(idx.pending_ingest_paths(conn), [])
            new_path = self.card("new-session-card")
            idx.run_incremental(conn, paths + [new_path])
            producer.assert_called_once_with(self.db, session_id="session-b")

    def test_producer_failure_does_not_retain_completed_semantic_batch(self):
        path = self.card()
        conn = self.connection()
        producer = mock.Mock(side_effect=RuntimeError("injected producer failure"))
        with mock.patch.dict(sys.modules, {
                "m3_autofile": types.SimpleNamespace(produce_test_affirmations=producer)}), \
                mock.patch.dict(os.environ, {
                    "EIDETIC_PRODUCER": "1", "EIDETIC_SESSION_ID": "session-a"}):
            idx.run_incremental(conn, [path])
        self.assertEqual(idx.pending_ingest_paths(conn), [])
        self.m1.assert_called_once()
        self.m2.assert_called_once()

    def test_producer_interruption_does_not_replay_in_a_later_session(self):
        path = self.card()
        conn = self.connection()
        producer = mock.Mock(side_effect=KeyboardInterrupt)
        with mock.patch.dict(sys.modules, {
                "m3_autofile": types.SimpleNamespace(produce_test_affirmations=producer)}), \
                mock.patch.dict(os.environ, {
                    "EIDETIC_PRODUCER": "1", "EIDETIC_SESSION_ID": "session-a"}):
            with self.assertRaises(KeyboardInterrupt):
                idx.run_incremental(conn, [path])
            self.assertEqual(idx.pending_ingest_paths(conn), [])
            producer.side_effect = None
            producer.reset_mock()
            os.environ["EIDETIC_SESSION_ID"] = "session-b"
            idx.run_incremental(conn, [path])
            producer.assert_not_called()

    def test_interrupted_hooks_retry_even_when_all_file_mtimes_are_current(self):
        path = self.card()
        conn = self.connection()
        self.m1.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            idx.run_incremental(conn, [path])
        self.assertEqual(idx.pending_ingest_paths(conn), [path])
        conn.close()
        conn = self.connection()
        self.m1.side_effect = None
        self.m1.reset_mock()
        with mock.patch.object(idx, "propagate_declared_relations") as propagate:
            result = idx.run_incremental(conn, [path])
        self.assertEqual(result, (0, 1, 0))
        propagate.assert_not_called()
        self.m1.assert_called_once_with(conn, self.db, [path])
        self.m2.assert_called_once_with(conn, self.db, [path])
        self.assertEqual(idx.pending_ingest_paths(conn), [])

    def test_interrupted_relations_retry_before_semantic_hooks_on_unchanged_run(self):
        old_path = self.card("old")
        new_path = self.card("new")
        paths = [old_path, new_path]
        conn = self.connection()
        idx.run_incremental(conn, paths)
        Path(new_path).write_text(
            "---\nname: new\ntype: project\nsupersedes: old\n---\n\nNew guidance.\n")
        self.m1.reset_mock()
        self.m2.reset_mock()

        def stop_before_propagation(_conn):
            # A separate reader proves the retry marker committed with the
            # changed FTS state, rather than only existing in this transaction.
            with sqlite3.connect(self.db) as committed:
                self.assertTrue(idx.relation_propagation_needed(committed))
                self.assertEqual(committed.execute(
                    "SELECT mtime FROM index_meta WHERE path = ?", (new_path,)
                ).fetchone()[0], idx.file_mtime(new_path))
            raise KeyboardInterrupt

        with mock.patch.object(idx, "propagate_declared_relations",
                               side_effect=stop_before_propagation):
            with self.assertRaises(KeyboardInterrupt):
                idx.run_incremental(conn, paths)
        self.m1.assert_not_called()
        self.m2.assert_not_called()
        self.assertEqual(idx.pending_ingest_paths(conn), [new_path])
        conn.close()
        conn = self.connection()

        def require_propagation_before_hooks(connection, _db, _paths):
            self.assertEqual(connection.execute(
                "SELECT status FROM memory_chunks WHERE path = ?", (old_path,)
            ).fetchone()[0], "superseded")
            self.assertFalse(idx.relation_propagation_needed(connection))

        self.m1.side_effect = require_propagation_before_hooks
        self.assertEqual(idx.run_incremental(conn, paths), (0, 2, 0))
        self.m1.assert_called_once()
        self.assertEqual(idx.pending_ingest_paths(conn), [])
        self.assertFalse(idx.relation_propagation_needed(conn))

    def test_failed_relations_retry_without_a_semantic_queue(self):
        path = self.card()
        conn = self.connection()
        with mock.patch.dict(os.environ, {"EIDETIC_CONFIDENCE_EVENTS": "0"}):
            idx.run_incremental(conn, [path])
            self.card(body="Changed body before a propagation failure.")
            with mock.patch.object(idx, "compute_relation_state",
                                   side_effect=sqlite3.OperationalError("injected schema failure")):
                with self.assertRaisesRegex(RuntimeError, "Relation propagation failed"):
                    idx.run_incremental(conn, [path])
            self.assertTrue(idx.relation_propagation_needed(conn))
            self.assertEqual(idx.pending_ingest_paths(conn), [])
            conn.close()
            conn = self.connection()
            with mock.patch.object(idx, "propagate_declared_relations",
                                   wraps=idx.propagate_declared_relations) as propagate:
                self.assertEqual(idx.run_incremental(conn, [path]), (0, 1, 0))
            propagate.assert_called_once_with(conn)
        self.assertFalse(idx.relation_propagation_needed(conn))
        self.m1.assert_not_called()
        self.m2.assert_not_called()

    def test_full_rebuild_does_not_replace_index_after_failed_relations(self):
        path = self.card(body="Original retained content.")
        conn = self.connection()
        idx.run_incremental(conn, [path])
        self.card(body="New content that must not replace the original index yet.")
        with mock.patch.object(idx, "propagate_declared_relations", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "full index was not replaced"):
                idx.run_full(conn, [path])
        with sqlite3.connect(self.db) as stored:
            self.assertIn("Original retained", stored.execute(
                "SELECT content FROM memory_chunks WHERE path = ?", (path,)
            ).fetchone()[0])

    def test_reported_hook_failure_retains_pending_for_next_attempt(self):
        path = self.card()
        conn = self.connection()
        self.m1.side_effect = RuntimeError("injected hook failure")
        idx.run_incremental(conn, [path])
        self.assertEqual(idx.pending_ingest_paths(conn), [path])
        self.m1.side_effect = None
        idx.run_incremental(conn, [path])
        self.assertEqual(idx.pending_ingest_paths(conn), [])
        self.assertEqual(self.m1.call_count, 2)

    def test_failing_semantic_batch_rotates_without_starving_later_cards(self):
        paths = [self.card(name) for name in ("poison", "next", "last")]
        conn = self.connection()

        def fail_poison(_conn, _db, batch):
            if paths[0] in batch:
                raise RuntimeError("injected poison card")

        self.m1.side_effect = fail_poison
        with mock.patch.dict(os.environ, {"EIDETIC_INGEST_BATCH_SIZE": "1"}):
            idx.run_incremental(conn, paths)
            self.assertEqual(idx.pending_ingest_paths(conn), paths[1:] + paths[:1])
            idx.run_incremental(conn, paths)
            self.assertEqual(idx.pending_ingest_paths(conn), [paths[2], paths[0]])
            idx.run_incremental(conn, paths)
        self.assertEqual([call.args[2] for call in self.m1.call_args_list],
                         [[path] for path in paths])
        self.assertEqual(idx.pending_ingest_paths(conn), paths[:1])

    def test_default_batch_leaves_tail_and_next_unchanged_run_drains_it(self):
        paths = [self.card(name) for name in ("z", "y", "x", "w", "v")]
        conn = self.connection()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(idx.run_incremental(conn, paths), (5, 0, 0))
        self.assertEqual([c.args[2][0] for c in self.m1.call_args_list], paths[:4])
        self.assertEqual(idx.pending_ingest_paths(conn), paths[4:])
        self.assertIn("1 cards pending", output.getvalue())
        self.assertIn("FTS is current", output.getvalue())
        self.assertEqual(idx.run_incremental(conn, paths), (0, 5, 0))
        self.assertEqual(self.m1.call_args.args[2], paths[4:])
        self.assertEqual(idx.pending_ingest_paths(conn), [])
        self.assertEqual(self.m1.call_count, 5)

    def test_old_pending_work_precedes_new_edits(self):
        paths = [self.card(name) for name in ("z", "y", "x")]
        conn = self.connection()
        with mock.patch.dict(os.environ, {"EIDETIC_INGEST_BATCH_SIZE": "1"}):
            idx.run_incremental(conn, paths)
            self.assertEqual(idx.pending_ingest_paths(conn), paths[1:])
            new_path = self.card("new")
            idx.run_incremental(conn, paths + [new_path])
        self.assertEqual(self.m1.call_args.args[2], [paths[1]])
        self.assertEqual(idx.pending_ingest_paths(conn), [paths[2], new_path])

    def test_invalid_batch_limit_fails_before_indexing(self):
        path = self.card()
        conn = self.connection()
        for value in ("0", "129", "invalid", "1.5"):
            with self.subTest(value=value), mock.patch.dict(
                    os.environ, {"EIDETIC_INGEST_BATCH_SIZE": value}):
                with self.assertRaisesRegex(ValueError, "EIDETIC_INGEST_BATCH_SIZE"):
                    idx.run_incremental(conn, [path])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM index_meta").fetchone()[0], 0)

    def test_pending_removed_and_emptied_cards_are_not_sent_to_hooks(self):
        empty_path = self.card("empty")
        gone_path = self.card("gone")
        conn = self.connection()
        self.m1.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            idx.run_incremental(conn, [empty_path, gone_path])
        Path(empty_path).write_text("")
        Path(gone_path).unlink()
        self.m1.side_effect = None
        self.m1.reset_mock()
        with mock.patch.dict(os.environ, {"EIDETIC_CONFIDENCE_EVENTS": "0"}):
            self.assertEqual(idx.run_incremental(conn, [empty_path]), (1, 0, 1))
        self.assertEqual(idx.pending_ingest_paths(conn), [])
        self.m1.assert_not_called()
        self.m2.assert_not_called()

    def test_full_rebuild_preserves_interrupted_semantic_work(self):
        path = self.card()
        conn = self.connection()
        self.m1.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            idx.run_incremental(conn, [path])
        idx.run_full(conn, [path])
        conn = self.connection()
        self.assertEqual(idx.pending_ingest_paths(conn), [path])
        self.m1.side_effect = None
        idx.run_incremental(conn, [path])
        self.assertEqual(idx.pending_ingest_paths(conn), [])

    def test_empty_fresh_index_still_stamps_backfill_before_becoming_noop(self):
        conn = self.connection()
        with mock.patch.object(idx, "propagate_declared_relations") as propagate:
            self.assertEqual(idx.run_incremental(conn, []), (0, 0, 0))
        propagate.assert_called_once_with(conn)
        self.assertTrue(idx.backfill_stamp_present(conn))
        changes = conn.total_changes
        idx.run_incremental(conn, [])
        self.assertEqual(conn.total_changes, changes)

    def test_lifecycle_backfill_is_not_mistaken_for_noop(self):
        path = self.card()
        conn = self.connection()
        idx.run_incremental(conn, [path])
        conn.execute("UPDATE memory_chunks SET card_kind = ''")
        conn.commit()
        with mock.patch.object(idx, "propagate_declared_relations") as propagate:
            result = idx.run_incremental(conn, [path])
        self.assertEqual(result, (1, 0, 0))
        propagate.assert_called_once_with(conn)

    def test_malformed_pending_work_is_not_silently_discarded(self):
        conn = self.connection()
        conn.execute("INSERT INTO schema_meta (key, value) VALUES (?, ?)",
                     (idx.PENDING_INGEST_KEY, '{"wrong": "shape"}'))
        conn.commit()
        with self.assertRaisesRegex(ValueError, "Invalid pending ingest"):
            idx.run_incremental(conn, [])


if __name__ == "__main__":
    unittest.main()
