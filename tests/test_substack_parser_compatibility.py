"""Fixed expected parser output and real-parser collection/export/merge contracts."""
import argparse
from contextlib import redirect_stdout
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/substack"))
import check_substack_compatibility as checks
import collect_substack_history as history
import team_collection as team


class ParserCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.reference, self.fixture, self.expected = checks.load_reference()
        self.url = self.fixture["publication_url"]

    def parse(self, pages):
        transport = checks.FixtureTransport({checks.endpoint(self.url, offset): rows for offset, rows in pages.items()})
        return history.parse_publication_history(transport, self.url, team.START, team.END), transport.calls

    def test_exact_fixed_output_including_schema_ids_dates_text_and_hashes(self):
        actual, calls = self.parse({0: self.fixture["posts"]})
        self.assertEqual(actual, self.expected)
        self.assertEqual(checks.normalized_hash(actual), self.reference["expected_normalized_sha256"])
        self.assertEqual(calls, [checks.endpoint(self.url)])
        self.assertEqual(checks.validate_payload(self.url, actual), 7)

    def test_paid_unlock_and_unknown_bodies_are_never_retained(self):
        actual, _ = self.parse({0: self.fixture["posts"]})
        posts = {r["post_id"]: r for r in actual["posts"]}
        for ident in ("1002", "1003", "1004"):
            self.assertEqual((posts[ident]["full_text"], posts[ident]["retained_word_count"], posts[ident]["text_sha256"]), ("", 0, ""))
        self.assertNotIn("SYNTHETIC PAID BODY", json.dumps(actual))

    def test_date_boundaries_coauthors_and_zero_engagement_are_preserved(self):
        actual, _ = self.parse({0: self.fixture["posts"]})
        posts = {r["post_id"]: r for r in actual["posts"]}
        self.assertEqual(actual["posts"][0]["post_id"], "1007")
        self.assertEqual(actual["posts"][-1]["post_id"], "1001")
        self.assertNotIn("1008", posts)
        self.assertNotIn("1009", posts)
        self.assertEqual([r["creator_id"] for r in posts["1001"]["bylines"]], ["201", "202"])
        self.assertEqual(posts["1001"]["bylines"][0]["publication_role"], "admin")
        self.assertEqual((posts["1001"]["reaction_count"], posts["1001"]["comment_count"]), (0, 0))

    def test_pagination_overlap_deduplicates_and_stops_at_lower_date_bound(self):
        base = self.fixture["posts"][6]
        first = [dict(base, id=2000 + n, post_date="2024-01-01T00:00:00Z") for n in range(50)]
        later = dict(first[0], body_html="<p>Later observation.</p>")
        actual, calls = self.parse({0: first, 50: [later, base, self.fixture["posts"][7]]})
        self.assertEqual(calls, [checks.endpoint(self.url), checks.endpoint(self.url, 50)])
        self.assertEqual(len(actual["posts"]), 51)
        duplicate = next(r for r in actual["posts"] if r["post_id"] == "2000")
        self.assertEqual(duplicate["full_text"], "Later observation.")
        self.assertEqual(duplicate["source_url"], checks.endpoint(self.url, 50))
        self.assertNotIn("1008", {r["post_id"] for r in actual["posts"]})

    def test_repeated_page_and_empty_history_terminate(self):
        page = [dict(self.fixture["posts"][6], id=3000 + n) for n in range(50)]
        actual, calls = self.parse({0: page, 50: page})
        self.assertEqual((len(actual["posts"]), len(calls)), (50, 2))
        actual, calls = self.parse({0: []})
        self.assertEqual((actual["posts"], len(calls)), ([], 1))

    def test_bad_response_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "list"):
            self.parse({0: {"unexpected": "schema"}})

    def test_modified_collector_or_batch_cannot_pass_alignment(self):
        with patch.object(team, "code_hashes", return_value={"changed": "version"}):
            with self.assertRaisesRegex(ValueError, "Collector differs"):
                checks.check_reference(checks.DEFAULT_BATCH)
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)
            team.write_json(batch / "manifest.json", {"different": "batch"})
            with self.assertRaisesRegex(ValueError, "Assignment batch differs"):
                checks.check_reference(batch)

    def test_semantic_corruption_is_detected_even_with_valid_json(self):
        cases = {
            "text checksum": lambda p: p["posts"][-1].update(full_text="altered text"),
            "word count": lambda p: p["posts"][-1].update(retained_word_count=900),
            "study window": lambda p: p["posts"][-1].update(published_at="2026-09-18T00:00:00Z"),
            "month mismatch": lambda p: p["posts"][-1].update(year_month="2026-08"),
            "duplicate post": lambda p: p["posts"].append(copy.deepcopy(p["posts"][-1])),
            "provenance": lambda p: p["posts"][-1].update(source_url="https://wrong.invalid/api/v1/posts?offset=0"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.expected)
                mutate(payload)
                with self.assertRaises(ValueError):
                    checks.validate_payload(self.url, payload)
        payload = copy.deepcopy(self.expected)
        paid = next(p for p in payload["posts"] if p["post_id"] == "1002")
        paid.update(full_text="leak", retained_word_count=1, text_sha256=hashlib.sha256(b"leak").hexdigest())
        with self.assertRaisesRegex(ValueError, "Paid/unknown"):
            checks.validate_payload(self.url, payload)


class RealParserHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _, self.fixture, self.expected = checks.load_reference()
        frame = self.root / "frame.csv"
        rows = []
        for n in range(4):
            url = f"https://team-fixture-{n}.substack.invalid"
            rows.append(dict(publication_url=url, frame_lastmod="", frame_priority=hashlib.sha256(("publication-frame-v1|substack|" + url).encode()).hexdigest()))
        team.write_csv(frame, team.FIELDS, rows)
        team.write_json(frame.with_suffix(".manifest.json"), {"frame_csv_sha256": team.sha_file(frame)})
        self.baseline = self.root / "baseline.sqlite3"
        cp = team.Checkpoint(self.baseline)
        cp.put(team.HISTORY_STAGE, rows[0]["publication_url"], False, None, ValueError("preserved baseline error"))
        cp.close()
        self.batch = self.root / "batch"
        team.prepare(frame, self.baseline, self.batch)
        self.manifest, self.shards = team.validate_batch(self.batch)

    def args(self, n, **overrides):
        values = dict(batch=self.batch, shard=n, cache_root=self.root / f"cache-{n}",
                      limit=5, continuous=False, delay_seconds=6.0, check=False)
        values.update(overrides)
        return argparse.Namespace(**values)

    def collect_export(self, n, failure=False):
        response = checks.FixtureTransport({checks.endpoint(row["publication_url"]): self.fixture["posts"] for row in self.shards[n]})
        # Only the HTTP boundary is replaced; the actual history and post parsers run.
        effect = ValueError("synthetic source error") if failure else response.fetch
        with patch.object(team.Collector, "fetch", side_effect=effect), redirect_stdout(io.StringIO()):
            result, code = team.run(self.args(n))
        self.assertEqual(code, 2 if failure else 0)
        with patch.object(team.Collector, "fetch", side_effect=AssertionError("Resume re-fetched a committed URL")), redirect_stdout(io.StringIO()):
            team.run(self.args(n))
        output = self.root / f"export-{n}"
        with patch.object(team, "PART_BYTES", 257):
            team.export(self.args(n, output=output))
        return output

    def assert_parser_merge(self):
        exports = []
        for n in self.shards:
            output = self.collect_export(n, failure=n == max(self.shards))
            result = checks.validate_export(output, self.batch, self.manifest, self.shards[n], n)
            self.assertEqual((result["status"], result["pending"]), ("PASS", 0))
            exports.append(output)
        merged = self.root / "merged.sqlite3"
        team.merge(argparse.Namespace(batch=self.batch, baseline_db=self.baseline, exports=exports, output=merged))
        with sqlite3.connect(merged) as db:
            records = db.execute("SELECT item_key,ok,payload FROM parsed ORDER BY item_key").fetchall()
        self.assertEqual(len(records), 4)
        self.assertEqual(sum(not r[1] for r in records), 2)
        for key, ok, payload in records:
            if not ok:
                continue
            expected = copy.deepcopy(self.expected)
            expected["publication_url"] = key
            for post in expected["posts"]:
                post["source_url"] = checks.endpoint(key)
                if post["post_id"] != "1001":  # Canonical URL was supplied only for this post.
                    post["candidate_url"] = post["canonical_url"] = post["canonical_url"].replace(self.fixture["publication_url"], key)
            self.assertEqual(json.loads(payload), expected)

    def test_actual_parsing_three_shards_export_merge_equals_expected_records(self):
        self.assert_parser_merge()

    def test_actual_parsing_two_shards_export_merge_equals_expected_records(self):
        self.batch = self.root / "batch-two"
        team.prepare(self.root / "frame.csv", self.baseline, self.batch, shard_count=2)
        self.manifest, self.shards = team.validate_batch(self.batch)
        self.assert_parser_merge()

    def test_wrong_shard_and_missing_or_reordered_parts_fail(self):
        output = self.collect_export(1)
        with self.assertRaisesRegex(ValueError, "different shard"):
            checks.validate_export(output, self.batch, self.manifest, self.shards[2], 2)
        m = team.read_json(output / "manifest.json")
        m["parts"] = list(reversed(m["parts"]))
        team.write_json(output / "manifest.json", m)
        with self.assertRaisesRegex(ValueError, "reordered"):
            checks.validate_export(output, self.batch, self.manifest, self.shards[1], 1)

    def test_payload_tampering_fails_even_if_part_checksum_is_recomputed(self):
        output = self.collect_export(1)
        m = team.read_json(output / "manifest.json")
        records = list(team.exported_rows(output, m))
        payload = json.loads(records[0]["payload"])
        payload["posts"][-1]["full_text"] = "Corrupt text with stale hash"
        records[0]["payload"] = json.dumps(payload)
        part = output / "records-00000.jsonl.gz"
        part.write_bytes(gzip.compress((json.dumps(records[0]) + "\n").encode(), mtime=0))
        m["parts"] = [{"file": part.name, "sha256": team.sha_file(part)}]
        team.write_json(output / "manifest.json", m)
        with self.assertRaisesRegex(ValueError, "Text checksum"):
            checks.validate_export(output, self.batch, self.manifest, self.shards[1], 1)

    def test_partial_export_cannot_be_reported_as_complete(self):
        args = self.args(1)
        args.cache_root.mkdir()
        (args.cache_root / "STOP").touch()
        with redirect_stdout(io.StringIO()):
            team.run(args)
        output = self.root / "interim"
        team.export(self.args(1, output=output))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            checks.validate_export(output, self.batch, self.manifest, self.shards[1], 1)
        result = checks.validate_export(output, self.batch, self.manifest, self.shards[1], 1, allow_partial=True)
        self.assertEqual((result["status"], result["pending"]), ("PASS_PARTIAL", 1))


if __name__ == "__main__":
    unittest.main()
