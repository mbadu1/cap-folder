"""Operator scope changes preserve live work, historical results and bindings."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "medium/scripts"))
from defer_reserves import defer
import test_medium_parallel as fixtures


class ReserveDeferralTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MediumParallelTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c, self.batch = self.fixture.c, self.fixture.batch

    def test_deferred_reserves_never_start_after_primary_exhaustion(self):
        binding = (self.c.cache / "binding.json").read_bytes()
        primary = self.c.pick()
        receipt = defer(self.c.cache, self.batch)
        self.assertEqual(2, receipt["changed_pending_reserves"])
        self.assertEqual("inflight", self.c.db.execute("SELECT state FROM tasks WHERE item_key=?", (primary["item_key"],)).fetchone()[0])
        self.assertEqual(binding, (self.c.cache / "binding.json").read_bytes())
        self.c.db.execute("UPDATE tasks SET state='error' WHERE item_key IN (SELECT profile_url FROM assignments WHERE stage='primary_candidate')")
        self.c.db.commit()
        self.assertIsNone(self.c.pick())
        self.assertEqual(2, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='deferred'").fetchone()[0])
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        # Exact original runner/binding resume retains the held reserve states.
        self.c.prepare(self.batch, 2, self.fixture.ledger)
        self.assertIsNone(self.c.pick())

    def test_idempotent_and_completed_failed_reserves_unchanged(self):
        row = self.fixture.rows[2]["profile_url"]
        self.c.db.execute("UPDATE tasks SET state='error',error='historical fixture' WHERE item_key=?", (row,))
        self.c.db.commit()
        self.assertEqual(1, defer(self.c.cache, self.batch)["changed_pending_reserves"])
        self.assertEqual(0, defer(self.c.cache, self.batch)["changed_pending_reserves"])
        self.assertEqual(("error", "historical fixture"), tuple(self.c.db.execute("SELECT state,error FROM tasks WHERE item_key=?", (row,)).fetchone()))
        self.assertEqual(2, self.c.db.execute("SELECT COUNT(*) FROM reserve_scope_audit").fetchone()[0])
        self.assertEqual("primary_only_until_sampling_review", json.loads((self.c.cache / "reserve_policy.json").read_text())["policy"])

    def test_primary_story_queue_still_drains(self):
        primary = self.c.pick()
        defer(self.c.cache, self.batch)
        raw = fixtures.feed(handle=primary["item_key"].split("@")[-1])
        self.c.commit_result(self.fixture.result(primary, raw=raw), self.fixture.gate())
        self.c.db.execute("UPDATE tasks SET state='error' WHERE kind='feed' AND state='pending'")
        self.c.db.commit()
        task = self.c.pick()
        self.assertEqual("mirror", task["kind"])
        self.assertEqual("0123456789ab", task["item_key"])
        self.assertEqual(2, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='deferred'").fetchone()[0])

    def test_inflight_reserve_refuses_and_rolls_back(self):
        self.c.db.execute("UPDATE tasks SET state='inflight' WHERE item_key=?", (self.fixture.rows[2]["profile_url"],))
        self.c.db.commit()
        with self.assertRaisesRegex(ValueError, "already in flight"):
            defer(self.c.cache, self.batch)
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='deferred'").fetchone()[0])
        self.assertFalse((self.c.cache / "reserve_policy.json").exists())

    def test_assignment_mismatch_refuses(self):
        self.c.db.execute("DELETE FROM assignments WHERE profile_url=?", (self.fixture.rows[2]["profile_url"],))
        self.c.db.commit()
        with self.assertRaisesRegex(ValueError, "assignments differ"):
            defer(self.c.cache, self.batch)
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='deferred'").fetchone()[0])

    def test_live_writer_without_pending_primary_refuses(self):
        import fcntl
        self.c.db.execute("UPDATE tasks SET state='done' WHERE item_key IN (SELECT profile_url FROM assignments WHERE stage='primary_candidate')")
        self.c.db.commit()
        checked = {k: True for k in ("recorded_pid_alive_on_this_node", "process_command_matches", "code_matches_binding", "file_matches_sqlite_binding")}
        with (self.c.cache / "collector.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("defer_reserves.check", return_value=checked), self.assertRaisesRegex(ValueError, "pending primary"):
                defer(self.c.cache, self.batch)
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='deferred'").fetchone()[0])
