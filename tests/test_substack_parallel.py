"""Offline checks for the 20-thread runner and private baseline comparison."""
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
from email.message import Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/substack"))
import parallel_team_collection as parallel
import upgrade_parallel_cache as upgrade
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

    def test_401_pauses_without_dispatching_all_pending_work(self):
        first = self.shards[2][0]["publication_url"]

        def fake_history(_collector, url, _start, _end):
            if url == first:
                raise urllib.error.HTTPError(url, 401, "authentication required", {}, None)
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

    def http_error(self, code=403, body=None, headers=None):
        message = Message()
        for key, value in (headers or {"Content-Type": "text/html; charset=utf-8"}).items():
            message[key] = value
        if body is None:
            body = parallel.INVITE_ONLY_MESSAGE.encode()
        return urllib.error.HTTPError("https://test.substack.invalid/api/v1/posts", code,
                                      "Forbidden", message, io.BytesIO(body))

    def test_confirmed_invite_only_is_recorded_and_other_histories_continue(self):
        first = self.shards[2][0]["publication_url"]

        def response(request, **kwargs):
            if request.full_url.startswith(first + "/"):
                raise self.http_error()
            result = io.BytesIO(b"[]")
            result.status = 200
            return result

        with patch("urllib.request.urlopen", side_effect=response), \
                patch.object(parallel.Pacing, "wait"), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual((code, result["successful"], result["failed"], result["pending"]), (2, 24, 1, 0))
        self.assertEqual(result["invite_only_failures_this_run"], 1)
        self.assertEqual(result["http_403_failures_this_run"], 1)
        self.assertEqual(result["http_status_counts_this_run"], {"403": 1, "200": 24})
        self.assertFalse((self.root / "production/PAUSED.json").exists())
        with team.readonly(self.root / "production/crawl.sqlite3") as db:
            row = db.execute("SELECT ok,error_type,error_message FROM parsed WHERE item_key=?", (first,)).fetchone()
        self.assertEqual(row[:2], (0, "InviteOnlyPublication"))
        self.assertIn("response_sha256=", row[2])
        with patch("urllib.request.urlopen", side_effect=AssertionError("Refetched saved failure")), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual(result["new_publications_this_run"], 0)

    def test_unexplained_403_is_saved_without_retry_and_other_histories_continue(self):
        first = self.shards[2][0]["publication_url"]
        calls = []
        def response(request, **kwargs):
            calls.append(request.full_url)
            if request.full_url.startswith(first + "/"):
                raise self.http_error(body=b"Forbidden")
            result = io.BytesIO(b"[]")
            result.status = 200
            return result
        with patch("urllib.request.urlopen", side_effect=response), \
                patch.object(parallel.Pacing, "wait"), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual((code, result["successful"], result["failed"], result["pending"]), (2, 24, 1, 0))
        self.assertEqual((result["http_403_failures_this_run"], result["invite_only_failures_this_run"]), (1, 0))
        self.assertEqual(sum(url.startswith(first + "/") for url in calls), 1)
        self.assertFalse((self.root / "production/PAUSED.json").exists())
        with team.readonly(self.root / "production/crawl.sqlite3") as db:
            row = db.execute("SELECT ok,error_type,error_message,payload FROM parsed WHERE item_key=?", (first,)).fetchone()
        self.assertEqual((row[0], row[1], row[3]), (0, "HTTPError", None))
        self.assertIn("403", row[2])
        with patch("urllib.request.urlopen", side_effect=AssertionError("Refetched saved failure")), redirect_stdout(io.StringIO()):
            result, code = parallel.production(self.args())
        self.assertEqual(result["new_publications_this_run"], 0)

    def test_only_401_and_429_pause_and_unknown_403_is_not_labeled_invite_only(self):
        message = parallel.INVITE_ONLY_MESSAGE.encode()
        cases = [
            (401, message, None), (429, message, None),
            (403, b"Forbidden", None), (403, message + b" More information", None),
            (403, b"\xff", None), (403, b" " * 16385 + message, None),
            (403, message, {"Content-Type": "text/html", "Retry-After": "600"}),
            (403, message, {"Content-Type": "text/html", "CF-Mitigated": "challenge"}),
            (403, message, {"Content-Type": "application/json"}),
            (403, b"<script>cf-chl-test</script>" + message, None),
        ]
        for code, body, headers in cases:
            with self.subTest(code=code, body=body[:50], headers=headers):
                event = threading.Event()
                collector = parallel.ParallelCollector(0, parallel.Pacing(0, 0, event), event)
                with patch("urllib.request.urlopen", side_effect=self.http_error(code, body, headers)):
                    with self.assertRaises((urllib.error.HTTPError, parallel.CrawlCircuitOpen)) as raised:
                        collector.fetch("https://test.substack.invalid/api/v1/posts")
                self.assertNotIsInstance(raised.exception, parallel.InviteOnlyPublication)
                self.assertEqual(event.is_set(), code in (401, 429))

    def test_runner_upgrade_preserves_every_row_and_markers_without_rebinding_source(self):
        def history(_collector, url, _start, _end):
            return {"publication_url": url, "start_date": team.START, "end_date": team.END, "posts": []}
        with patch.object(parallel, "parse_publication_history", side_effect=history), redirect_stdout(io.StringIO()):
            parallel.production(self.args(limit=2, workers=1))
        source = self.root / "production"
        binding = team.read_json(source / "parallel_runner.json")
        binding["runner_sha256"] = upgrade.INVITE_ONLY_PREDECESSOR_SHA256
        team.write_json(source / "parallel_runner.json", binding)
        checkpoint = team.Checkpoint(source / "crawl.sqlite3")
        checkpoint.put(team.HISTORY_STAGE, self.shards[2][2]["publication_url"], False, None, self.http_error())
        checkpoint.close()
        (source / "PAUSED.json").write_bytes(b'{"reason":"old pause"}\n')
        (source / "STOP").write_bytes(b"intentional stop\n")
        old_digest = upgrade.checkpoint_digest(source / "crawl.sqlite3")
        target = self.root / "successor"
        result = upgrade.upgrade(self.args(source_cache=source, cache_root=target))
        self.assertEqual(result["exact_rows"], old_digest)
        self.assertEqual(team.read_json(source / "parallel_runner.json"), binding)
        self.assertEqual((target / "PAUSED.json").read_bytes(), b'{"reason":"old pause"}\n')
        self.assertEqual((target / "STOP").read_bytes(), b"intentional stop\n")
        self.assertTrue((source / "RETIRED.json").exists())
        self.assertEqual(team.read_json(target / "parallel_runner.json")["runner_sha256"], team.sha_file(parallel.RUNNER))
        with self.assertRaisesRegex(RuntimeError, "pause"):
            parallel.production(self.args(cache_root=target))
        with self.assertRaisesRegex(RuntimeError, "retired"):
            upgrade.upgrade(self.args(source_cache=source, cache_root=self.root / "another"))

    def test_runner_upgrade_rejects_foreign_binding_and_existing_destination(self):
        source = self.root / "source"
        args = self.args(cache_root=source)
        batch, rows, _, expected = team.setup(args)
        with team.cache_lock(source):
            team.check_binding(source, expected, create=True)
            team.Checkpoint(source / "crawl.sqlite3").close()
            binding = parallel.runner_binding(batch["batch_id"], team.sha_file(self.batch / "manifest.json"), 2)
            team.write_json(source / "parallel_runner.json", binding)
        target = self.root / "target"
        with self.assertRaisesRegex(ValueError, "predecessor"):
            upgrade.upgrade(self.args(source_cache=source, cache_root=target))
        binding["runner_sha256"] = upgrade.PREDECESSOR_SHA256
        team.write_json(source / "parallel_runner.json", binding)
        (target / "keep.txt").write_text("existing data")
        with self.assertRaises(FileExistsError):
            upgrade.upgrade(self.args(source_cache=source, cache_root=target))
        self.assertEqual((target / "keep.txt").read_text(), "existing data")
        self.assertFalse((source / "RETIRED.json").exists())

    def test_owner_compares_disposable_rescrape_without_sharing_reference_db(self):
        reference = self.root / "local-reference.sqlite3"
        checkpoint = team.Checkpoint(reference)
        frame = self.root / "baseline-frame.csv"
        rows = []
        for n in range(100):
            url = f"https://baseline-test-{n}.substack.invalid"
            rows.append({"publication_url": url, "frame_lastmod": "",
                         "frame_priority": hashlib.sha256(("publication-frame-v1|substack|" + url).encode()).hexdigest()})
        team.write_csv(frame, team.FIELDS, rows)
        team.write_json(frame.with_suffix(".manifest.json"), {"frame_csv_sha256": team.sha_file(frame)})
        baseline_urls = [row["publication_url"] for row in rows[:60]]
        for url in baseline_urls:
            checkpoint.put(team.HISTORY_STAGE, url, True,
                           {"publication_url": url, "start_date": team.START,
                            "end_date": team.END, "posts": []}, None)
        checkpoint.close()
        batch = self.root / "baseline-batch"
        team.prepare(frame, reference, batch, shard_count=2)
        list_path = self.root / "urls.json"
        parallel.make_validation_list(argparse.Namespace(batch=batch, reference_db=reference,
                                                        output=list_path))
        selected = parallel.validation_list(list_path, batch)
        urls = selected["urls"]
        self.assertEqual((len(urls), selected["reference"]), (40, "baseline"))
        self.assertTrue(set(urls) <= set(baseline_urls))
        with self.assertRaisesRegex(ValueError, "baseline inventory"):
            parallel.make_validation_list(argparse.Namespace(batch=batch,
                reference_db=self.root / "baseline.sqlite3", output=self.root / "bad-urls.json"))
        cache = self.root / "disposable-test"
        args = self.args(command="validation-run", batch=batch, urls=list_path, cache_root=cache,
                         continuous=True)

        def fake_history(_collector, url, _start, _end):
            return {"publication_url": url, "start_date": team.START,
                    "end_date": team.END, "posts": []}

        with patch.object(parallel, "parse_publication_history", side_effect=fake_history), redirect_stdout(io.StringIO()):
            result, code = parallel.validation_run(args)
        self.assertEqual((code, result["successful"]), (0, 40))
        export = self.root / "test-export"
        parallel.validation_export(argparse.Namespace(batch=batch, urls=list_path,
                                                      cache_root=cache, output=export))
        report, code = parallel.validation_compare(argparse.Namespace(batch=batch, urls=list_path,
                                                     reference_db=reference, export_dir=export,
                                                     report=self.root / "comparison.json"))
        self.assertEqual((code, report["status"], report["exact"]), (0, "PASS_EXACT", 40))
        checkpoint = team.Checkpoint(reference)
        checkpoint.put(team.HISTORY_STAGE, urls[0], True,
                       {"publication_url": urls[0], "start_date": team.START,
                        "end_date": team.END, "posts": [{"changed": True}]}, None)
        checkpoint.close()
        report, code = parallel.validation_compare(argparse.Namespace(batch=batch, urls=list_path,
                                                     reference_db=reference, export_dir=export,
                                                     report=self.root / "comparison-changed.json"))
        self.assertEqual((code, report["status"], report["exact"]), (1, "REVIEW_REQUIRED", 39))

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
