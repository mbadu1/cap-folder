"""Offline checks for fixed baseline replay, isolated trials and review gates."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "medium/scripts"))
import validate_dcc as v
from collect_medium_history import Collector, digest
from parallel_medium_collection import Gate
from test_medium_parallel import feed
import threading


class MediumDCCValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = Collector(self.root / "baseline", self.root / "report", min_free_gb=0, max_cache_gb=0)
        self.addCleanup(self.base.db.close)
        self.base.configure_author_pool(100)
        for i in range(40):
            profile = f"https://medium.com/@p{i}"
            self.base.add_profile(f"p{i}")
            task = dict(kind="feed", item_key=profile, url=f"https://medium.com/feed/@p{i}", bucket="")
            raw = feed(f"p{i}", candidate=f"{i + 1:012x}", ident=f"{i + 1:012x}")
            sha = self.base.parse(task, raw, "2026-10-03T00:00:00Z")
            self.base.db.execute("UPDATE tasks SET state='done',response_hash=? WHERE kind='feed' AND item_key=?", (sha, profile))
        self.base.db.commit()
        self.sample_dir = self.root / "sample"
        self.reference = self.root / "private/reference.json.gz"
        self.manifest = v.freeze(self.base.cache / "crawl.sqlite3", self.sample_dir, self.reference)
        self.sample_path = self.sample_dir / "feeds.json"
        self.c = v.ValidationCollector(self.root / "trial", self.root / "trial/report", min_free_gb=0, max_cache_gb=0)
        self.addCleanup(self.c.db.close)
        self.c.prepare_validation(self.sample_path, "smoke", replay=True)

    def complete_smoke(self):
        gate = Gate(self.c.cache, 1.5, threading.Event(), 0)
        starts = []
        for i in range(5):
            task = self.c.pick()
            handle = task["item_key"].split("@")[-1]
            n = int(handle[1:])
            raw = feed(handle, candidate=f"{n + 1:012x}", ident=f"{n + 1:012x}")
            self.c.commit_result(dict(task=task, stamp="2026-10-03T01:00:00Z", status=200, size=len(raw),
                raw=raw, sha=digest(raw), cf=None, retry=None, error=None), gate)
            starts.append(dict(epoch=100 + 6 * i, worker_id=1, kind="feed", key=task["item_key"]))
        (self.c.cache / "request_starts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in starts))

    def test_freeze_replays_exact_bytes_and_preserves_baseline(self):
        self.assertEqual("PASS_EXACT", self.manifest["offline_replay_status"])
        self.assertTrue(self.manifest["no_network_requests"])
        self.assertEqual(40, len(v.load_sample(self.sample_path)["feeds"]))
        before = v.sha_file(self.base.cache / "crawl.sqlite3")
        v.freeze(self.base.cache / "crawl.sqlite3", self.root / "second", self.root / "second-reference.gz")
        self.assertEqual(before, v.sha_file(self.base.cache / "crawl.sqlite3"))
        with self.assertRaises(FileExistsError):
            v.freeze(self.base.cache / "crawl.sqlite3", self.sample_dir, self.reference)

    def test_validation_requests_only_selected_feeds_not_mirrors(self):
        self.complete_smoke()
        self.assertIsNone(self.c.pick())
        self.assertEqual(5, self.c.db.execute("SELECT COUNT(*) FROM requests WHERE kind='feed'").fetchone()[0])
        self.assertEqual(0, self.c.db.execute("SELECT COUNT(*) FROM requests WHERE kind!='feed'").fetchone()[0])
        self.assertIsNone(self.c.get("team_binding"))
        self.c.add_profile("unassigned")
        self.assertIsNone(self.c.db.execute("SELECT 1 FROM creators WHERE handle='unassigned'").fetchone())

    def test_assessment_and_baseline_compare_pass_then_report_payload_drift(self):
        self.complete_smoke()
        self.assertEqual("PASS", v.assess(self.c.cache)["status"])
        self.assertEqual("PASS_EXACT", v.compare(self.c.cache, self.sample_path, self.reference)["status"])
        original = self.reference.read_bytes()
        self.c.db.execute("UPDATE bodies SET text_sha256='changed'")
        self.c.db.commit()
        self.assertEqual("REVIEW_REQUIRED", v.compare(self.c.cache, self.sample_path, self.reference)["status"])
        self.assertEqual(original, self.reference.read_bytes())

    def test_pacing_violations_and_stop_markers_require_review(self):
        self.complete_smoke()
        path = self.c.cache / "request_starts.jsonl"
        starts = [json.loads(r) for r in path.read_text().splitlines()]
        starts[1]["epoch"] = starts[0]["epoch"] + 0.1
        path.write_text("".join(json.dumps(r) + "\n" for r in starts))
        result = v.assess(self.c.cache)
        self.assertEqual("REVIEW_REQUIRED", result["status"])
        self.assertIn("Global request spacing violated", result["issues"])
        self.assertIn("Per-worker request spacing missing or violated", result["issues"])
        (self.c.cache / "PAUSED.json").write_text('{}')
        self.assertIn("Stop/pause evidence requires review", v.assess(self.c.cache)["issues"])

    def test_same_response_replay_detects_parser_output_difference(self):
        self.complete_smoke()
        result = v.replay(self.c.cache, self.sample_path)
        self.assertEqual("PASS_EXACT", result["status"])
        self.assertTrue(result["no_network_requests"])
        self.assertEqual(5, result["profiles_replayed"])
        self.c.db.execute("UPDATE bodies SET text_sha256='changed'")
        self.c.db.commit()
        self.assertEqual("REVIEW_REQUIRED", v.replay(self.c.cache, self.sample_path)["status"])

    def test_reference_tampering_and_stage_rebinding_are_rejected(self):
        self.reference.write_bytes(gzip.compress(b'{}'))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            v.compare(self.c.cache, self.sample_path, self.reference)
        with self.assertRaisesRegex(ValueError, "binding changed"):
            self.c.prepare_validation(self.sample_path, "ten", replay=True)

    def test_live_prepare_requires_ledger_and_honors_feed_access_blocks(self):
        other = v.ValidationCollector(self.root / "live", self.root / "live/report", min_free_gb=0, max_cache_gb=0)
        self.addCleanup(other.db.close)
        with self.assertRaisesRegex(ValueError, "refreshed"):
            other.prepare_validation(self.sample_path, "smoke")
        ledger = self.root / "ledger.gz"
        ledger.write_bytes(gzip.compress(json.dumps(dict(version="medium-team-reconciliation-v1",
            routes=[dict(route="feed", state="blocked")], cooldown_until=0, rate_events=[])).encode()))
        with self.assertRaisesRegex(ValueError, "blocked feed"):
            other.prepare_validation(self.sample_path, "smoke", ledger)


if __name__ == "__main__":
    unittest.main()
