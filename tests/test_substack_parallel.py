"""Offline checks for the 20-thread runner and private overlap comparison."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/substack"))
import parallel_team_collection as parallel
import team_collection as team
from build_substack_platform_month import Collector
import check_substack_compatibility as checks


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        frame = self.root / "frame.csv"
        rows = []
        for n in range(50):
            url = f"https://parallel-test-{n}.substack.invalid"
            rows.append({"publication_url": url, "frame_lastmod": "",
                         "frame_priority": hashlib.sha256(("publication-frame-v1|substack|" + url).encode()).hexdigest()})
        team.write_csv(frame, team.FIELDS, rows)
        team.write_json(frame.with_suffix(".manifest.json"), {"frame_csv_sha256": team.sha_file(frame)})
        baseline = self.root / "baseline.sqlite3"
        team.Checkpoint(baseline).close()
        self.batch = self.root / "batch"
        team.prepare(frame, baseline, self.batch, shard_count=2)
        _, self.shards = team.validate_batch(self.batch)

    def args(self, command="run", **changes):
        values = {"batch": self.batch, "shard": 2, "cache_root": self.root / "production",
                  "workers": 20, "delay_seconds": 6.0, "global_gap_seconds": 0.6,
                  "continuous": False, "limit": 25, "check": False}
        values.update(changes)
        return argparse.Namespace(**values)

    def test_start_gate_enforces_both_intervals(self):
        gate = parallel.Pacing(0.025, 0.008, threading.Event())
        calls, lock = [], threading.Lock()

        def worker():
            for _ in range(3):
                gate.wait()
                with lock:
                    calls.append((threading.get_ident(), time.monotonic()))

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        times = [stamp for _, stamp in sorted(calls, key=lambda x: x[1])]
        self.assertEqual(len(times), 9)
        self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), 0.006)
        for ident in {item[0] for item in calls}:
            values = [stamp for worker_id, stamp in calls if worker_id == ident]
            self.assertGreaterEqual(min(b - a for a, b in zip(values, values[1:])), 0.022)

    def test_twenty_workers_checkpoint_once_and_resume_without_refetch(self):
        active, peak = 0, 0
        lock = threading.Lock()

        def fake_history(_collector, url, _start, _end):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"publication_url": url, "start_date": team.START, "end_date": team.END, "posts": []}

        with patch.object(parallel, "parse_publication_history", side_effect=fake_history), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual((code, result["successful"], result["pending"]), (0, 25, 0))
        self.assertGreaterEqual(peak, 10)
        with patch.object(parallel, "parse_publication_history", side_effect=AssertionError("refetched")), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual((code, result["new_publications_this_run"]), (0, 0))

    def test_403_pauses_without_dispatching_all_pending_work(self):
        first = self.shards[2][0]["publication_url"]

        def fake_history(_collector, url, _start, _end):
            if url == first:
                raise urllib.error.HTTPError(url, 403, "invite only", {}, None)
            time.sleep(0.01)
            return {"publication_url": url, "start_date": team.START, "end_date": team.END, "posts": []}

        with patch.object(parallel, "parse_publication_history", side_effect=fake_history), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual(code, 3)
        self.assertTrue((self.root / "production/PAUSED.json").exists())
        self.assertGreater(result["pending"], 0)
        with self.assertRaisesRegex(RuntimeError, "pause"):
            parallel.production(self.args())

    def test_429_opens_shared_pause_before_another_request(self):
        event = threading.Event()
        gate = parallel.Pacing(0.01, 0.01, event)
        collector = parallel.ParallelCollector(0.01, gate, event)
        with self.assertRaisesRegex(parallel.CrawlCircuitOpen, "HTTP 429"):
            collector._record_rate_limit(0.01)
        self.assertTrue(event.is_set())
        with self.assertRaises(parallel.CrawlCircuitOpen):
            gate.wait()

    def test_owner_compares_disposable_rescrape_without_sharing_reference_db(self):
        reference = self.root / "local-reference.sqlite3"
        checkpoint = team.Checkpoint(reference)
        urls = [row["publication_url"] for row in self.shards[1][:21]]
        for url in urls:
            checkpoint.put(team.HISTORY_STAGE, url, True,
                           {"publication_url": url, "start_date": team.START,
                            "end_date": team.END, "posts": []}, None)
        checkpoint.close()
        list_path = self.root / "urls.json"
        parallel.make_validation_list(argparse.Namespace(batch=self.batch, reference_db=reference,
                                                        output=list_path))
        cache = self.root / "disposable-test"
        args = self.args(command="validation-run", urls=list_path, cache_root=cache,
                         continuous=True)

        def fake_history(_collector, url, _start, _end):
            return {"publication_url": url, "start_date": team.START,
                    "end_date": team.END, "posts": []}

        with patch.object(parallel, "parse_publication_history", side_effect=fake_history), redirect_stdout(io.StringIO()):
            result, code = parallel.validation_run(args)
        self.assertEqual((code, result["successful"]), (0, 21))
        export = self.root / "test-export"
        parallel.validation_export(argparse.Namespace(batch=self.batch, urls=list_path,
                                                      cache_root=cache, output=export))
        report, code = parallel.validation_compare(argparse.Namespace(batch=self.batch, urls=list_path,
                                                     reference_db=reference, export_dir=export,
                                                     report=self.root / "comparison.json"))
        self.assertEqual((code, report["status"], report["exact"]), (0, "PASS_EXACT", 21))
        checkpoint = team.Checkpoint(reference)
        checkpoint.put(team.HISTORY_STAGE, urls[0], True,
                       {"publication_url": urls[0], "start_date": team.START,
                        "end_date": team.END, "posts": [{"changed": True}]}, None)
        checkpoint.close()
        report, code = parallel.validation_compare(argparse.Namespace(batch=self.batch, urls=list_path,
                                                     reference_db=reference, export_dir=export,
                                                     report=self.root / "comparison-changed.json"))
        self.assertEqual((code, report["status"], report["exact"]), (1, "REVIEW_REQUIRED", 20))

    def test_parallel_and_existing_runner_parse_identical_frozen_responses(self):
        _, fixture, _ = checks.load_reference()
        responses = json.dumps(fixture["posts"], ensure_ascii=False).encode("utf-8")
        serial_cache = self.root / "serial"
        serial_args = argparse.Namespace(batch=self.batch, shard=2, cache_root=serial_cache,
                                         limit=25, continuous=False, delay_seconds=6.0, check=False)
        with patch.object(Collector, "fetch", return_value=responses), redirect_stdout(io.StringIO()):
            serial, serial_code = team.run(serial_args)
            concurrent, parallel_code = parallel.production(self.args())
        self.assertEqual((serial_code, parallel_code), (0, 0))
        self.assertEqual((serial["successful"], concurrent["successful"]), (25, 25))
        with team.readonly(serial_cache / "crawl.sqlite3") as left, team.readonly(self.root / "production/crawl.sqlite3") as right:
            original = {key: json.loads(payload) for key, payload in left.execute(
                "SELECT item_key,payload FROM parsed WHERE stage=?", (team.HISTORY_STAGE,))}
            updated = {key: json.loads(payload) for key, payload in right.execute(
                "SELECT item_key,payload FROM parsed WHERE stage=?", (team.HISTORY_STAGE,))}
        self.assertEqual(original, updated)
        export = self.root / "parallel-export"
        team.export(self.args(output=export))
        batch, shards = team.validate_batch(self.batch)
        result = checks.validate_export(export, self.batch, batch, shards[2], 2)
        self.assertEqual((result["status"], result["pending"]), ("PASS", 0))


if __name__ == "__main__":
    unittest.main()
