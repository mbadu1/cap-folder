"""Offline tests for shard scope, pacing, checkpoints, exports and handoffs."""
import csv
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/medium"))
import parallel_medium_collection as p
from collect_medium_history import Collector
from merge_team_exports import merge


def feed(handle="p0", candidate="abc", ident="0123456789ab", date="Wed, 01 Jan 2020 00:00:00 GMT"):
    return (f'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Stories by Fixture on Medium</title>'
            f'<link>https://medium.com/@{handle}?source=rss-{candidate}-x</link><description>Test</description>'
            f'<item><title>Story</title><guid>https://medium.com/p/{ident}</guid>'
            f'<link>https://medium.com/@{handle}/story-{ident}</link><pubDate>{date}</pubDate>'
            '<content:encoded><![CDATA[<p>Public prose fixture.</p>]]></content:encoded></item></channel></rss>').encode()


class MediumParallelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / "batch"
        self.batch.mkdir()
        self.rows = []
        for i in range(4):
            profile = f"https://medium.com/@p{i}"
            self.rows.append(dict(rank=i + 1, profile_url=profile, feed_url=f"https://medium.com/feed/@p{i}",
                                 priority=p.digest("medium-discovery-v1|feed|" + profile),
                                 stage="reserve_candidate" if i >= 2 else "primary_candidate", owner="Ziyang", shard="shard-2"))
        for name, rows in (("shard-2.csv", self.rows[:2]), ("shard-2-reserve.csv", self.rows[2:])):
            with (self.batch / name).open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(self.rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        manifest = dict(version="medium-candidate-worklists-v1", target_collected_authors=5,
                        start_date=p.START, end_date=p.END, owners={"shard-2": "Ziyang"},
                        shard_counts={"shard-2": 2}, reserve_counts={"shard-2": 2},
                        files={f.name: p.sha_file(f) for f in self.batch.glob("*.csv")})
        (self.batch / "manifest.json").write_text(json.dumps(manifest))
        self.base = Collector(self.root / "baseline", self.root / "report", min_free_gb=0, max_cache_gb=0)
        self.addCleanup(self.base.db.close)
        self.base.configure_author_pool(5)
        self.base.admit_pool_profile("https://medium.com/@known", "known")
        self.base.db.commit()
        self.ledger = self.root / "ledger.json.gz"
        p.reconcile(self.base.cache / "crawl.sqlite3", self.ledger, self.batch)
        self.c = self.new_collector()

    def new_collector(self, name="team"):
        c = p.TeamCollector(self.root / name, self.root / (name + "-report"), min_free_gb=0, max_cache_gb=0)
        self.addCleanup(c.db.close)
        c.prepare(self.batch, 2, self.ledger)
        return c

    def result(self, task, raw=None, status=200, error=None):
        return dict(task=task, stamp="2026-10-03T21:00:00+00:00", status=status, size=len(raw or b""),
                    raw=raw, sha=p.digest(raw) if raw else None, cf=None, retry=None, error=error)

    def gate(self, **kw):
        return p.Gate(self.c.cache, kw.get("delay", 0.015), threading.Event(), 0,
                      per_worker_gap=kw.get("per_worker_gap", 0), enforce_minimum=False)

    def test_production_defaults_and_both_pacing_values_are_pinned(self):
        self.assertEqual(1.5, self.c.delay)
        self.assertEqual(6.0, self.c.per_worker_gap)
        self.assertEqual(1.5, self.c.get("team_binding")["delay_seconds"])
        self.assertEqual(6.0, self.c.get("team_binding")["per_worker_gap_seconds"])
        self.c.per_worker_gap = 7.0
        with self.assertRaisesRegex(ValueError, "binding differs"):
            self.c.prepare(self.batch, 2, self.ledger)

    def test_shared_gate_also_enforces_each_workers_gap(self):
        gate = self.gate(delay=0.005, per_worker_gap=0.025)
        task = dict(kind="feed", item_key="fixture")
        def worker():
            for _ in range(3):
                gate.start(task)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            for future in futures:
                future.result()
        log = [json.loads(line) for line in (self.c.cache / "request_starts.jsonl").read_text().splitlines()]
        self.assertEqual(6, len(log))
        self.assertGreaterEqual(min(b["epoch"] - a["epoch"] for a, b in zip(log, log[1:])), 0.004)
        self.assertEqual(2, len({r["worker_id"] for r in log}))
        for ident in {r["worker_id"] for r in log}:
            stamps = [r["epoch"] for r in log if r["worker_id"] == ident]
            self.assertGreaterEqual(min(b - a for a, b in zip(stamps, stamps[1:])), 0.024)

    def test_invalid_production_gaps_are_rejected_before_cache_creation(self):
        for global_gap, worker_gap in ((1.4, 6), (float("nan"), 6), (1.5, 5.9), (1.5, float("inf"))):
            with self.subTest(global_gap=global_gap, worker_gap=worker_gap), self.assertRaises(ValueError):
                p.TeamCollector(self.root / "invalid", self.root / "report-invalid",
                                delay=global_gap, per_worker_gap=worker_gap)
        self.assertFalse((self.root / "invalid").exists())

    def test_prepare_offline_no_discovery_and_binding_refusal(self):
        self.assertEqual(4, self.c.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        self.assertEqual(1, self.c.get("author_pool_count"))
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        self.c.add_profile("unassigned")
        self.assertEqual(4, self.c.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        with self.assertRaisesRegex(ValueError, "binding differs"):
            self.c.prepare(self.batch, 2, self.ledger, delay=4)
        (self.batch / "shard-2.csv").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            p.validate_batch(self.batch, 2)

    def test_primary_inflight_holds_reserves_and_inflight_tasks_are_unique(self):
        tasks = [self.c.pick(), self.c.pick()]
        self.assertEqual(2, len({t["item_key"] for t in tasks}))
        self.assertIsNone(self.c.pick())
        self.c.db.execute("UPDATE tasks SET state='error' WHERE state='inflight'")
        self.c.db.commit()
        self.assertIn(self.c.pick()["item_key"], {r["profile_url"] for r in self.rows[2:]})

    def test_target_reservations_do_not_overshoot(self):
        self.c.set("author_pool_count", 4)
        self.c.db.commit()
        self.assertIsNotNone(self.c.pick())
        self.assertIsNone(self.c.pick())

    def test_known_and_new_candidate_ids_are_deduplicated(self):
        self.assertFalse(self.c.admit_pool_profile("https://medium.com/@p0", "known"))
        self.assertTrue(self.c.admit_pool_profile("https://medium.com/@p0", "new"))
        self.assertTrue(self.c.admit_pool_profile("https://medium.com/@p1", "new"))
        self.assertEqual(2, self.c.get("author_pool_count"))

    def test_results_parse_on_coordinator_scope_mirrors_and_replay_matches(self):
        task = dict(self.c.db.execute("SELECT * FROM tasks WHERE item_key='https://medium.com/@p0'").fetchone())
        raw = feed()
        self.c.commit_result(self.result(task, raw), self.gate())
        self.base.add_profile("p0")
        self.base.parse(task, raw, "2026-10-03T21:00:00+00:00")
        for table in ("observations", "bodies"):
            self.assertEqual([tuple(r) for r in self.base.db.execute(f"SELECT * FROM {table}")],
                             [tuple(r) for r in self.c.db.execute(f"SELECT * FROM {table}")])
        self.assertEqual(1, self.c.db.execute("SELECT COUNT(*) FROM author_pool_mirrors").fetchone()[0])
        self.c.add_story("https://medium.com/@outsider/story-abcdef123456")
        self.assertIsNone(self.c.db.execute("SELECT 1 FROM creators WHERE handle='outsider'").fetchone())
        self.assertIsNone(self.c.db.execute("SELECT 1 FROM author_pool_mirrors WHERE post_id='abcdef123456'").fetchone())

    def test_reconciliation_preserves_failure_and_completed_mirrors(self):
        self.base.add_profile("p0")
        self.base.db.execute("UPDATE tasks SET state='error',error='original failure' WHERE item_key='https://medium.com/@p0'")
        self.base.add_story("https://medium.com/@known/story-0123456789ab")
        self.base.db.execute("UPDATE tasks SET state='done' WHERE kind='mirror'")
        self.base.db.commit()
        second = self.root / "ledger2.json.gz"
        p.reconcile(self.base.cache / "crawl.sqlite3", second, self.batch)
        c = p.TeamCollector(self.root / "reconciled", self.root / "report2", min_free_gb=0, max_cache_gb=0)
        self.addCleanup(c.db.close)
        c.prepare(self.batch, 2, second)
        row = c.db.execute("SELECT state,error FROM tasks WHERE item_key='https://medium.com/@p0'").fetchone()
        self.assertEqual(("error", "original failure"), tuple(row))
        c.add_story("https://medium.com/@p1/story-0123456789ab")
        c.enqueue_pool_mirror("0123456789ab")
        self.assertEqual("done", c.db.execute("SELECT state FROM author_pool_mirrors").fetchone()[0])

    def test_parser_rollback_does_not_count_author(self):
        task = self.c.pick()
        def fail(*_):
            self.c.admit_pool_profile(task["item_key"], "bad")
            raise ValueError("schema fixture")
        with patch.object(self.c, "parse", side_effect=fail):
            self.c.commit_result(self.result(task, b"bad"), self.gate())
        self.assertEqual(1, self.c.get("author_pool_count"))
        self.assertEqual("error", self.c.db.execute("SELECT state FROM tasks WHERE item_key=?", (task["item_key"],)).fetchone()[0])

    def test_shared_gate_spacing_under_contention_and_stop(self):
        gate = self.gate(delay=0.015)
        task = dict(kind="feed", item_key="fixture")
        with ThreadPoolExecutor(max_workers=20) as executor:
            starts = list(executor.map(lambda _: gate.start(task), range(20)))
        log = [json.loads(line) for line in (self.c.cache / "request_starts.jsonl").read_text().splitlines()]
        self.assertEqual(20, len(starts))
        self.assertGreaterEqual(min(b["epoch"] - a["epoch"] for a, b in zip(log, log[1:])), 0.014)
        (self.c.cache / "STOP").touch()
        self.assertIsNone(gate.start(task))

    def test_access_pause_prevents_further_starts_and_preserves_first_reason(self):
        gate = self.gate()
        gate.outcome(403, "challenge", None, None)
        original = (self.c.cache / "PAUSED.json").read_bytes()
        gate.pause("later event")
        self.assertEqual(original, (self.c.cache / "PAUSED.json").read_bytes())
        self.assertIsNone(gate.start(dict(kind="feed", item_key="fixture")))

    def test_429_shared_cooldown_persists_and_retries_without_three_event_pause(self):
        gate = self.gate()
        gate.outcome(429, None, "90", None)
        restored = self.gate()
        self.assertGreater(restored.state["cooldown_until"], time.time() + 89)
        task = self.c.pick()
        self.c.commit_result(self.result(task, status=429, error="HTTP 429"), gate)
        self.assertEqual("pending", self.c.db.execute("SELECT state FROM tasks WHERE item_key=?", (task["item_key"],)).fetchone()[0])
        self.assertEqual((429, "HTTP 429"), tuple(self.c.db.execute("SELECT status,error FROM requests").fetchone()))
        self.assertEqual(task["item_key"], self.c.pick()["item_key"])
        gate.outcome(429, None, "bad", None)
        gate.outcome(429, None, None, None)
        self.assertFalse((self.c.cache / "PAUSED.json").exists())
        self.assertFalse(self.c.blocked("feed"))

    def test_ordinary_http_errors_record_failure_and_continue_other_assignments(self):
        gate = self.gate()
        for status in (401, 403, 404, 410, 500, 503):
            gate.outcome(status, None, None, None)
        self.assertFalse(gate.stopped())
        statuses = iter((401, 403, 404, 500))
        def fake(task, gate):
            code = next(statuses)
            return self.result(task, status=code, error=f"HTTP {code}")
        self.assertEqual("assigned_candidates_exhausted_with_gaps", p.collect(self.c, 1, 10, continuous=True, transport=fake))
        self.assertEqual(4, self.c.db.execute("SELECT COUNT(*) FROM requests WHERE error IS NOT NULL").fetchone()[0])
        self.assertEqual(4, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='error'").fetchone()[0])
        self.assertFalse(self.c.blocked("feed"))

    def test_429_waits_all_workers_then_same_item_succeeds_without_losing_attempt(self):
        clock = [1000.0]
        gate = self.gate()
        with patch("parallel_medium_collection.time.time", side_effect=lambda: clock[0]):
            gate.outcome(429, None, "1", None)
            self.assertEqual(1060, gate.state["cooldown_until"])
            task = self.c.pick()
            self.c.commit_result(self.result(task, status=429, error="HTTP 429"), gate)
            waits = []
            def advance(seconds):
                waits.append(seconds); clock[0] += seconds
            with patch.object(gate.stop, "wait", side_effect=advance):
                self.assertIsNotNone(gate.start(task))
            self.assertGreaterEqual(sum(waits), 59.999)
            task2 = self.c.pick()
            self.assertEqual(task["item_key"], task2["item_key"])
            self.c.commit_result(self.result(task2, feed(handle=task2["item_key"].split("@")[-1])), gate)
            self.assertEqual([429, 200], [r[0] for r in self.c.db.execute("SELECT status FROM requests ORDER BY id")])
            self.assertEqual("done", self.c.db.execute("SELECT state FROM tasks WHERE item_key=?", (task["item_key"],)).fetchone()[0])

    def test_mirror_429_stays_pending_in_both_queues_and_keeps_attempt(self):
        task = self.c.pick()
        self.c.commit_result(self.result(task, feed(handle=task["item_key"].split("@")[-1])), self.gate())
        mirror = dict(self.c.db.execute("SELECT * FROM tasks WHERE kind='mirror'").fetchone())
        gate = self.gate()
        gate.outcome(429, None, None, None)
        self.c.commit_result(self.result(mirror, status=429, error='HTTP 429'), gate)
        self.assertEqual('pending', self.c.db.execute("SELECT state FROM tasks WHERE kind='mirror'").fetchone()[0])
        self.assertEqual('pending', self.c.db.execute("SELECT state FROM author_pool_mirrors").fetchone()[0])
        self.assertEqual(1, self.c.db.execute('SELECT COUNT(*) FROM requests WHERE status=429').fetchone()[0])

    def test_transport_distinguishes_ordinary_forbidden_from_explicit_challenge(self):
        for prefix, paused in ((b'<html>Access denied for this item</html>', False),
                               (b'<html>/cdn-cgi/challenge-platform/</html>', True)):
            with self.subTest(paused=paused):
                gate = self.gate()
                response = Mock(status_code=403, headers={})
                response.iter_content.return_value = iter([prefix])
                with patch('requests.Session') as session, patch.object(gate, 'start', return_value='fixture-time'):
                    session.return_value.__enter__.return_value.get.return_value.__enter__.return_value = response
                    result = p.fetch(dict(url='https://medium.com/feed/@fixture'), gate)
                self.assertEqual(paused, gate.stopped())
                self.assertEqual('HTML access challenge response' if paused else 'HTTP 403', result['error'])
                if paused: self.assertTrue((self.c.cache/'PAUSED.json').exists())

    def test_interrupted_tasks_not_retried_and_cancelled_unstarted_remain_pending(self):
        task = self.c.pick()
        self.c.prepare(self.batch, 2, self.ledger)
        self.assertEqual("error", self.c.db.execute("SELECT state FROM tasks WHERE item_key=?", (task["item_key"],)).fetchone()[0])
        task = self.c.pick()
        result = self.result(task)
        result["stamp"] = None
        self.c.commit_result(result, self.gate())
        self.assertEqual("pending", self.c.db.execute("SELECT state FROM tasks WHERE item_key=?", (task["item_key"],)).fetchone()[0])

    def test_budget_20_threads_is_exact_without_network(self):
        def fake(task, gate):
            return self.result(task, status=404, error="HTTP 404")
        with patch("requests.Session.get", side_effect=AssertionError("Unexpected live request")):
            self.assertEqual("pilot_complete", p.collect(self.c, 20, 2, transport=fake))
        self.assertEqual(2, self.c.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        self.assertEqual(2, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE state='pending'").fetchone()[0])

    def test_export_and_merge_preserve_versions_failures_baseline_and_deduplicate_import(self):
        task = dict(self.c.db.execute("SELECT * FROM tasks WHERE item_key='https://medium.com/@p0'").fetchone())
        self.c.commit_result(self.result(task, feed()), self.gate())
        export = self.root / "export"
        result = p.export_cache(self.c.cache, export)
        self.assertEqual(1, result["new_qualifying_candidate_ids"])
        self.assertTrue((export / "request_gate.json").exists())
        before = self.base.db.execute("SELECT COUNT(*) FROM author_pool_creators").fetchone()[0]
        merged = self.root / "merged"
        receipt = merge(self.base.cache, [export, export], merged)
        self.assertEqual(2, receipt["known_candidate_ids"])
        self.assertEqual(before, self.base.db.execute("SELECT COUNT(*) FROM author_pool_creators").fetchone()[0])
        db = sqlite3.connect(merged / "crawl.sqlite3")
        self.addCleanup(db.close)
        self.assertEqual(1, db.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
        self.assertEqual(1, db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0])
        with self.assertRaises(FileExistsError):
            p.export_cache(self.c.cache, export)
        (export / "new_creator_keys.json").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            merge(self.base.cache, [export], self.root / "tampered")

    def test_resource_and_lock_guards(self):
        gate = p.Gate(self.c.cache, 0.001, threading.Event(), 20 * 1024**3, enforce_minimum=False)
        with patch("parallel_medium_collection.shutil.disk_usage", return_value=Mock(free=19 * 1024**3)):
            self.assertIsNone(gate.start(dict(kind="feed", item_key="fixture")))
        with self.assertRaises(ValueError):
            p.Gate(self.c.cache, 0.6, threading.Event(), 0)
        import fcntl
        with (self.c.cache / "collector.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                p.export_cache(self.c.cache, self.root / "locked")


if __name__ == "__main__":
    unittest.main()
