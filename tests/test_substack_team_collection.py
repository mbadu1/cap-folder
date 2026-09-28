"""Offline coverage, resume, isolation, and lossless handoff tests."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/substack"))
import team_collection as team


class TeamCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.frame = self.root / "frame.csv"
        self.rows = [dict(publication_url=f"https://p{i}.substack.com", frame_lastmod="",
                          frame_priority=hashlib.sha256(f"publication-frame-v1|substack|https://p{i}.substack.com".encode()).hexdigest()) for i in range(11)]
        team.write_csv(self.frame, team.FIELDS, self.rows)
        team.write_json(self.frame.with_suffix(".manifest.json"), {"frame_csv_sha256": team.sha_file(self.frame)})
        self.baseline = self.root / "baseline.sqlite3"
        cp = team.Checkpoint(self.baseline)
        cp.put(team.HISTORY_STAGE, self.rows[0]["publication_url"], True, self.payload(self.rows[0]["publication_url"]), None)
        cp.put(team.HISTORY_STAGE, self.rows[1]["publication_url"], False, None, ValueError("old failure"))
        cp.put(team.HISTORY_STAGE, "https://outside.invalid", False, None, ValueError("outside frame"))
        cp.close()
        self.batch = self.root / "batch"
        team.prepare(self.frame, self.baseline, self.batch)

    def payload(self, url):
        return dict(publication_url=url, start_date=team.START, end_date=team.END,
                    posts=[dict(post_id="42", full_text='Unicode café\n"quoted", text ' * 30)])

    def args(self, shard=1, **overrides):
        values = dict(batch=self.batch, shard=shard, cache_root=self.root / f"cache-{shard}",
                      limit=500, continuous=False, delay_seconds=6.0, check=False)
        values.update(overrides)
        return argparse.Namespace(**values)

    def collect(self, args=None):
        with patch.object(team, "parse_publication_history", side_effect=lambda c, url, start, end: self.payload(url)) as fetch, redirect_stdout(io.StringIO()):
            result = team.run(args or self.args())
        return result, fetch.call_count

    def test_partition_excludes_successes_and_failures_and_covers_frame(self):
        m, shards = team.validate_batch(self.batch)
        self.assertEqual(m["baseline_count"], 2)
        self.assertEqual(m["baseline_errors"], 1)
        self.assertEqual(m["baseline_out_of_frame"], 1)
        self.assertEqual([len(shards[n]) for n in (1, 2, 3)], [3, 3, 3])
        self.assertEqual(len({r["publication_url"] for rows in shards.values() for r in rows}), 9)

    def test_missing_manifest_and_changed_csv_fail_before_network(self):
        path = self.batch / "shard-1.manifest.json"
        original = path.read_bytes()
        path.unlink()
        with patch.object(team, "parse_publication_history") as fetch:
            with self.assertRaises(FileNotFoundError):
                team.run(self.args())
            fetch.assert_not_called()
        path.write_bytes(original)
        path = self.batch / "shard-1.csv"
        path.write_text(path.read_text() + "changed\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            team.run(self.args())

    def test_check_is_offline_and_does_not_create_cache(self):
        args = self.args(check=True)
        with patch.object(team, "parse_publication_history") as fetch:
            result, code = team.run(args)
        self.assertEqual(code, 0)
        self.assertFalse(args.cache_root.exists())
        fetch.assert_not_called()

    def test_partial_chunk_resumes_and_completed_resume_does_not_fetch(self):
        (result, code), n = self.collect(self.args(limit=1))
        self.assertEqual((code, n, result["pending"]), (4, 1, 2))
        (result, code), n = self.collect()
        self.assertEqual((code, n, result["pending"]), (0, 2, 0))
        self.assertEqual(self.collect()[1], 0)

    def test_errors_are_recorded_without_automatic_retries(self):
        with patch.object(team, "parse_publication_history", side_effect=ValueError("malformed")), redirect_stdout(io.StringIO()):
            result, code = team.run(self.args())
        self.assertEqual((code, result["failed"], result["pending"]), (2, 3, 0))
        self.assertEqual(self.collect()[1], 0)

    def test_access_stop_persists_and_prevents_restart(self):
        error = HTTPError("https://p.example", 403, "Forbidden", {}, None)
        with patch.object(team, "parse_publication_history", side_effect=error), redirect_stdout(io.StringIO()):
            result, code = team.run(self.args())
        self.assertEqual((code, result["failed"], result["pending"]), (3, 1, 2))
        with self.assertRaisesRegex(RuntimeError, "pause"):
            team.run(self.args())

    def test_rate_circuit_leaves_inflight_publication_pending(self):
        with patch.object(team, "parse_publication_history", side_effect=team.CrawlCircuitOpen("429")), redirect_stdout(io.StringIO()):
            result, code = team.run(self.args())
        self.assertEqual((code, result["failed"], result["pending"]), (3, 0, 3))

    def test_binding_and_lock_prevent_cross_assignment_and_duplicate_workers(self):
        self.collect(self.args(limit=1))
        with self.assertRaisesRegex(ValueError, "different assignment"):
            team.run(self.args(shard=2, cache_root=self.args().cache_root))
        with team.cache_lock(self.args().cache_root):
            with self.assertRaisesRegex(RuntimeError, "lock"):
                team.run(self.args())

    def test_stop_file_and_rate_validation(self):
        args = self.args()
        args.cache_root.mkdir()
        (args.cache_root / "STOP").touch()
        (result, code), n = self.collect(args)
        self.assertEqual((code, n, result["pending"]), (5, 0, 3))
        for delay in (2, float("nan")):
            with self.assertRaises(ValueError):
                team.run(self.args(delay_seconds=delay))

    def test_export_split_and_merge_preserve_payload_and_baseline_errors(self):
        exports = []
        for n in (1, 2, 3):
            self.collect(self.args(shard=n))
            output = self.root / f"export-{n}"
            with patch.object(team, "PART_BYTES", 91):
                manifest = team.export(self.args(shard=n, output=output))
            self.assertGreater(len(manifest["parts"]), 1)
            exports.append(output)
        output = self.root / "merged.sqlite3"
        team.merge(argparse.Namespace(batch=self.batch, baseline_db=self.baseline, exports=exports, output=output))
        with sqlite3.connect(output) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM parsed").fetchone()[0], 12)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM parsed WHERE ok=0").fetchone()[0], 2)
            row = db.execute("SELECT item_key,payload FROM parsed WHERE ok=1 LIMIT 1").fetchone()
            self.assertEqual(json.loads(row[1]), self.payload(row[0]))
        with self.assertRaises(FileExistsError):
            team.merge(argparse.Namespace(batch=self.batch, baseline_db=self.baseline, exports=exports, output=output))

    def test_corrupt_export_rejected(self):
        self.collect()
        output = self.root / "export"
        m = team.export(self.args(output=output))
        (output / m["parts"][0]["file"]).write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "checksum"):
            list(team.exported_rows(output, m))


if __name__ == "__main__":
    unittest.main()
