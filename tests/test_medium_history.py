"""Offline integrity and operational checks for the Medium collector."""
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import shutil
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "medium"))
from collect_medium_history import Collector, retry_after_seconds, digest, main
import collect_medium_history as medium_module
from test_medium_mirror_adapter import fixture, STORY, MIRROR


class MediumHistoryTests(unittest.TestCase):
    def test_fresh_checkout_initializes_from_portable_seed_without_network(self):
        source = Path(__file__).resolve().parents[1] / "data/seeds/medium/2026-09-24"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(source, root / "data/seeds/medium/2026-09-24")
            with patch.object(medium_module, "ROOT", root), patch.object(self.c.session, "get") as request:
                self.c.initialize()
            request.assert_not_called()
            self.assertEqual(self.c.get("frame")["sitemap_partitions"], {"users": 5165, "posts": 2452})
            self.assertTrue(self.c.blocked("graphql"))
            self.assertTrue(self.c.blocked("profile_html"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache"
        self.output = Path(self.tmp.name) / "output"
        self.c = Collector(self.cache, self.output, min_free_gb=0)
        self.c.wait = Mock()

    def tearDown(self):
        self.c.db.close()
        self.tmp.cleanup()

    def response(self, status, body=b"", headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.iter_content.return_value = [body]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        self.c.session.get = Mock(return_value=response)

    def task(self):
        self.c.add_story(STORY)
        return dict(self.c.db.execute("SELECT * FROM tasks WHERE kind='mirror'").fetchone())

    def test_default_cache_budget_stops_before_network(self):
        task = self.task()
        self.c.db.execute("INSERT INTO sources(sha256,raw_path,bytes) VALUES(?,?,?)",
                          ("oversized", "fixture.gz", 21 * 1024**3))
        self.c.session.get = Mock()
        with self.assertRaisesRegex(RuntimeError, "Configured cache budget reached"):
            self.c.fetch_one(task)
        self.c.session.get.assert_not_called()

    def test_unlimited_cache_skips_accounting_and_commits_request(self):
        self.c.db.close()
        self.c = Collector(self.cache, self.output, max_cache_gb=0)
        self.c.wait = Mock()
        task = self.task()
        self.c.db.execute("INSERT INTO sources(sha256,raw_path,bytes) VALUES(?,?,?)",
                          ("oversized", "fixture.gz", 1000 * 1024**3))
        self.response(200, json.dumps(fixture()).encode())
        queries = []
        self.c.db.set_trace_callback(queries.append)
        with patch("collect_medium_history.shutil.disk_usage", return_value=Mock(free=21 * 1024**3)):
            self.assertTrue(self.c.fetch_one(task))
        self.c.db.set_trace_callback(None)
        self.assertFalse(any("SUM(" in query.upper() for query in queries))
        self.c.db.close()
        self.c = Collector(self.cache, self.output, max_cache_gb=0)
        self.assertEqual("done", self.c.db.execute(
            "SELECT state FROM tasks WHERE kind=? AND item_key=?",
            (task["kind"], task["item_key"])).fetchone()[0])
        self.assertEqual((200, None), tuple(self.c.db.execute("SELECT status,error FROM requests").fetchone()))

    def test_unlimited_cache_preserves_free_disk_reserve(self):
        self.c.db.close()
        self.c = Collector(self.cache, self.output, max_cache_gb=0)
        task = self.task()
        self.c.session.get = Mock()
        with patch("collect_medium_history.shutil.disk_usage", return_value=Mock(free=20 * 1024**3 - 1)):
            with self.assertRaisesRegex(RuntimeError, "Minimum free-disk reserve reached"):
                self.c.fetch_one(task)
        self.c.session.get.assert_not_called()

    def test_invalid_cache_budgets_are_rejected(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max-cache-gb"):
                Collector(self.cache, self.output, max_cache_gb=value)

    def test_portable_seed_replay_is_idempotent_without_private_pilots(self):
        source = Path(__file__).resolve().parents[1] / "data/seeds/medium/2026-09-24"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(source, root / "data/seeds/medium/2026-09-24")
            with patch.object(medium_module, "ROOT", root):
                self.c.initialize()
                before = self.c.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                self.c.initialize()
        self.assertEqual(before, self.c.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        self.assertEqual(0, self.c.network_count)
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM creators WHERE native_id IS NOT NULL").fetchone()[0])
        self.assertEqual({"posts": 2452, "users": 5165}, self.c.get("frame")["sitemap_partitions"])
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM story_frame").fetchone()[0])
        self.assertTrue(self.c.blocked("graphql"))
        self.assertFalse(self.c.blocked("feed"))

    def test_native_identity_requires_matching_browser_evidence(self):
        profile, native = "https://medium.com/@fixture", "0123456789ab"
        self.c.add_profile("fixture")
        raw = ('<rss version="2.0"><channel><title>Stories by Fixture on Medium</title>'
               '<link>' + profile + '?source=rss-' + native + '-x</link><description>Fixture</description>'
               '</channel></rss>').encode()
        task = dict(kind="feed", item_key=profile, url="https://medium.com/feed/@fixture", bucket="")
        self.c.parse(task, raw, "2026-09-24")
        self.assertIsNone(self.c.db.execute("SELECT native_id FROM creators").fetchone()[0])
        self.c.db.execute("INSERT INTO browser_proofs VALUES(?,?,?,?,?)", (profile, native, "Fixture", "[]", "{}"))
        self.c.parse(task, raw, "2026-09-24")
        self.assertEqual((native, "browser_verified", "visible_profile_exhausted"), tuple(
            self.c.db.execute("SELECT native_id,identity_status,history_status FROM creators").fetchone()))

    def test_paid_response_keeps_only_metadata_without_raw_or_body(self):
        task = self.task()
        raw = json.dumps(fixture(False)).encode()
        self.response(200, raw)
        self.c.fetch_one(task)
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0])
        self.assertIsNone(self.c.db.execute("SELECT raw_path FROM sources").fetchone()[0])
        self.assertEqual(1, self.c.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        self.assertEqual([], list(self.cache.glob("raw/**/*.gz")))

    def test_failed_parse_rolls_back_and_quarantines_only_bad_item(self):
        task = self.task()
        self.response(200, b"fixture")
        def broken(*args):
            self.c.add_profile("should_not_persist")
            raise ValueError("Unexpected schema fixture")
        self.c.parse = broken
        self.c.fetch_one(task)
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM creators").fetchone()[0])
        self.assertFalse(self.c.blocked("mirror"))
        self.assertFalse(self.c.blocked("feed"))
        self.assertEqual("error", self.c.db.execute("SELECT state FROM tasks").fetchone()[0])

    def test_three_schema_failures_persist_and_stop_route(self):
        self.c.schema_outcome("mirror","bad shape")
        self.c.schema_outcome("mirror","bad shape")
        self.c.db.commit()
        self.c.db.close()
        self.c=Collector(self.cache,self.output,min_free_gb=0)
        self.assertFalse(self.c.blocked("mirror"))
        self.c.schema_outcome("mirror","bad shape")
        self.assertTrue(self.c.blocked("mirror"))
        self.assertFalse(self.c.blocked("feed"))

    def test_valid_response_resets_schema_failure_streak(self):
        task=self.task()
        self.c.schema_outcome("mirror","bad shape")
        self.c.schema_outcome("mirror","bad shape")
        self.response(200,json.dumps(fixture()).encode())
        self.c.fetch_one(task)
        self.assertEqual(0,self.c.get("schema_failure_streak:mirror"))
        self.c.schema_outcome("mirror","another isolated error")
        self.assertFalse(self.c.blocked("mirror"))

    def test_html_200_challenge_stops_without_parsing(self):
        task=self.task()
        self.response(200,b'<!doctype html><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>')
        self.c.parse=Mock()
        self.c.fetch_one(task)
        self.c.parse.assert_not_called()
        self.assertTrue(self.c.blocked("mirror"))

    def test_repair_reopens_only_known_isolated_parser_stops(self):
        self.c.block("mirror","Parser schema failure: ValueError: No explicit Markdown body; heuristic string fallback is disabled.")
        self.c.block("feed","HTTP 403")
        self.c.block("graphql","HTTP 403")
        self.c.repair_isolated_parser_stops()
        self.assertFalse(self.c.blocked("mirror"))
        self.assertTrue(self.c.blocked("feed"))
        self.assertTrue(self.c.blocked("graphql"))
        self.c.block("mirror","Three consecutive schema failures: bad shape")
        self.c.repair_isolated_parser_stops()
        self.assertTrue(self.c.blocked("mirror"))

    def test_report_distinguishes_discovery_only(self):
        self.c.block("feed","fixture")
        self.c.block("mirror","fixture")
        result=self.c.report()
        self.assertEqual("discovery_only",result["collection_activity"])
        self.assertEqual([],result["text_routes_available"])

    def test_heartbeat_uses_only_activity_queries_and_preserves_full_reports(self):
        self.c.report("stopped_by_signal_or_file")
        reports = {name: (self.output / name).read_bytes() for name in
                   ("status.json", "monthly_coverage.json", "monthly_topic_coverage.json")}
        task = self.task()
        self.response(200, json.dumps(fixture()).encode())
        self.c.fetch_one(task)
        self.c.block("feed", "fixture")
        self.c.block("mirror", "fixture")
        queries = []
        self.c.db.set_trace_callback(queries.append)
        result = self.c.heartbeat()
        self.c.db.set_trace_callback(None)
        self.assertEqual(1, result["requests_this_run"])
        self.assertEqual(1, result["last_request"]["id"])
        self.assertEqual(200, result["last_request"]["status"])
        self.assertEqual("discovery_only", result["collection_activity"])
        self.assertEqual(result, json.loads((self.output / "heartbeat.json").read_text()))
        for query in queries:
            self.assertNotIn("COUNT(", query.upper())
            self.assertNotIn("GROUP BY", query.upper())
            self.assertTrue("FROM routes" in query or "FROM requests ORDER BY id DESC LIMIT 1" in query, query)
        for name, content in reports.items():
            self.assertEqual(content, (self.output / name).read_bytes())

    def test_challenge_is_not_parsed_and_not_retried(self):
        task = self.task()
        self.response(403, headers={"cf-mitigated": "challenge"})
        self.c.parse = Mock()
        self.c.fetch_one(task)
        self.c.parse.assert_not_called()
        self.assertTrue(self.c.blocked("mirror"))
        self.assertIsNone(self.c.pick())
        self.c.session.get.assert_called_once()

    def test_429_cooldown_and_rate_history_survive_reopen(self):
        task = self.task()
        self.response(429, headers={"retry-after": "120"})
        stamp = time.time()
        self.c.fetch_one(task)
        self.assertGreaterEqual(self.c.get("cooldown_until"), stamp + 120)
        self.c.db.close()
        self.c = Collector(self.cache, self.output, min_free_gb=0)
        self.assertEqual(1, len(self.c.get("rate_events")))
        self.assertGreaterEqual(self.c.get("last_request_start"), stamp)
        self.assertEqual(60, retry_after_seconds("garbage"))
        self.assertEqual(120, retry_after_seconds("Thu, 24 Sep 2026 12:02:00 GMT", at=1790251200))

    def test_free_response_is_deduplicated_and_stays_out_of_primary_sample(self):
        task = self.task()
        self.response(200, json.dumps(fixture()).encode())
        self.c.fetch_one(task)
        self.c.add_story(STORY + "?source=alias")
        self.assertEqual(1, self.c.db.execute("SELECT COUNT(*) FROM tasks WHERE kind='mirror'").fetchone()[0])
        self.assertEqual(1, self.c.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0])
        metadata = json.loads(self.c.db.execute("SELECT metadata_json FROM observations").fetchone()[0])
        self.assertFalse(metadata["primary_eligible"])
        self.assertFalse(metadata["historical_snapshot_verified"])
        report = self.c.report()
        self.assertEqual(0, report["counts"]["primary_sample_records"])
        self.assertFalse(report["complete_historical_archive"])
        self.assertEqual(81, len(json.loads((self.output / "monthly_coverage.json").read_text())))
        self.assertEqual(648, len(json.loads((self.output / "monthly_topic_coverage.json").read_text())))

    def test_out_of_window_response_has_metadata_but_no_retained_body(self):
        task = self.task()
        raw = json.dumps(fixture()).replace("2020-02-01T00:30:00Z", "2026-09-18T00:30:00Z").encode()
        self.response(200, raw)
        self.c.fetch_one(task)
        self.assertEqual(0, self.c.db.execute("SELECT in_window FROM observations").fetchone()[0])
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0])
        self.assertIsNone(self.c.db.execute("SELECT raw_path FROM sources").fetchone()[0])

    def test_stop_file_prevents_request(self):
        task = self.task()
        (self.cache / "STOP").touch()
        self.c.session.get = Mock()
        self.assertFalse(self.c.fetch_one(task))
        self.c.session.get.assert_not_called()

    def test_empty_syndicated_body_is_metadata_only(self):
        self.c.store_observation("0123456789ab","official_profile_rss","fixture",None,
            "2020-01-01T00:00:00Z","Title",{},"<p></p>","officially_syndicated")
        self.assertEqual(1,self.c.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        self.assertEqual(0,self.c.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0])


class MediumCollectionLoopTests(unittest.TestCase):
    def test_full_report_runs_once_after_collection_not_at_request_boundaries(self):
        cases = [
            (["--continuous"], 205, "discovery_exhausted_with_gaps", None),
            ([], 100, "chunk_complete", None),
            (["--init-only"], 0, "initialized", None),
            (["--continuous"], 13, "stopped_by_signal_or_file", "stop"),
            (["--continuous"], 13, "stopped_error", "error"),
        ]
        for flags, total, state, ending in cases:
            with self.subTest(flags=flags, ending=ending), tempfile.TemporaryDirectory() as tmp:
                collector = Mock(cache=Path(tmp), stop=False, network_count=0)
                collector.db.execute.return_value.fetchone.return_value = None
                collector.pick.side_effect = lambda: {"kind": "feed"} if collector.network_count < total else None
                collector.report.return_value = {"state": state}

                def fetch(task):
                    collector.report.assert_not_called()
                    collector.network_count += 1
                    if collector.network_count == total:
                        if ending == "error":
                            raise ValueError("fixture failure")
                        if ending == "stop":
                            collector.stop = True
                    return True

                collector.fetch_one.side_effect = fetch
                argv = ["collect_medium_history.py", "--cache-root", tmp, "--max-requests", "100", *flags]
                with patch("collect_medium_history.Collector", return_value=collector), \
                     patch("collect_medium_history.signal.signal"), patch.object(sys, "argv", argv), \
                     redirect_stdout(io.StringIO()):
                    code = main()
                self.assertEqual(total, collector.network_count)
                self.assertEqual(2 if ending == "error" else 0, code)
                reason = "ValueError: fixture failure" if ending == "error" else None
                collector.report.assert_called_once_with(state, reason)
                collector.heartbeat.assert_called_with(state, reason)
                collector.db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
