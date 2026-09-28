"""Offline regression and delivered-table validation for the Medium toy."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "medium"))
from build_medium_toy import ROOT, normalize_body, period_for, select_toy_posts, sha, verified_source
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "substack"))
from build_substack_platform_month import TOPICS, allocate_sample


def csv_rows(path):
    return list(csv.DictReader(io.StringIO(path.read_text())))


class MediumToyTests(unittest.TestCase):
    def test_prose_boundaries_code_and_boilerplate(self):
        value = normalize_body('<h1>Title</h1><p>First <em>paragraph</em>.</p><p>Second paragraph.</p><pre><code>code token token</code></pre><button>Copy Copy</button><figcaption>Photo credit</figcaption><p>Title was originally published in Medium, where people are continuing the conversation by responding.</p>', "Title")
        self.assertIn("First paragraph.\n\nSecond paragraph.", value["text"])
        self.assertEqual(value["prose_word_count"], 4)
        self.assertEqual(value["code_block_word_count"], 3)
        self.assertNotIn("Copy", value["text"])
        self.assertTrue(value["generated_footer_removed"])

    def test_source_local_month_and_study_boundary(self):
        self.assertEqual(period_for("2020-02-01T00:30:00+01:00"), ("2020-02", True))
        self.assertTrue(period_for("2026-09-17T23:59:59-04:00")[1])
        self.assertFalse(period_for("2026-09-18T00:00:00Z")[1])
        self.assertFalse(period_for("2019-12-31T23:59:59Z")[1])
        with self.assertRaises(ValueError):
            period_for("2020-02-01T00:30:00")

    def test_selection_is_deterministic_capped_and_length_gated(self):
        rows = [dict(post_id=str(i), creator_id="c", year_month="2020-02", prose_word_count=200,
                     in_study_window=True, post_priority=sha("post-priority-v1|medium|" + str(i))) for i in range(6)]
        selected = select_toy_posts(rows)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected, select_toy_posts(list(reversed(rows))))
        rows[0]["prose_word_count"] = 199
        rows[1]["in_study_window"] = False
        self.assertNotIn("0", select_toy_posts(rows))
        self.assertNotIn("1", select_toy_posts(rows))

    def test_cache_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.xml"
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verified_source(path, sha(b"original"))

    def test_shared_quota_rule_capacity_and_floor(self):
        for sizes in [[1000] * 8, [12, 20, 1000, 1500, 250, 250, 300, 400], [5] * 8, [0] * 8]:
            with self.subTest(sizes=sizes):
                creators = [dict(creator_id=f"{h}:{i}", creator_priority=sha(f"{h}:{i}"), topic_family=TOPICS[h])
                            for h, size in enumerate(sizes) for i in range(size)]
                selected, audit = allocate_sample(creators, target=2500, topic_floor=150)
                self.assertEqual(len(selected), min(2500, sum(sizes)))
                for topic, capacity in zip(TOPICS, sizes):
                    self.assertLessEqual(audit[topic]["allocated"], capacity)
                    self.assertGreaterEqual(audit[topic]["allocated"], min(150, capacity))
                self.assertEqual(selected, allocate_sample(list(reversed(creators)), target=2500, topic_floor=150)[0])

    def test_delivered_tables_hashes_joins_missingness_and_primary_gate(self):
        base = ROOT / "data/toy/medium"
        if not (base / "manifest.json").is_file():
            self.skipTest("Optional private Medium toy artifact is not included in the code checkout")
        root_manifest = json.loads((base / "manifest.json").read_text())
        creators = csv_rows(base / "lookup/creators.csv")
        ids = {r["creator_id"] for r in creators}
        publications = {r["publication_id"] for r in csv_rows(base / "lookup/publications.csv")}
        for link in csv_rows(base / "lookup/creator_publications.csv"):
            self.assertIn(link["creator_id"], ids)
            self.assertIn(link["publication_id"], publications)
        records = json.loads((base / "records.json").read_text())
        self.assertEqual(len({r["text_sha256"] for r in records}), len(records))
        self.assertTrue(all(r["native_creator_id"] == "" for r in creators))
        seen, ntext, ncreator_month = set(), 0, 0
        for month in root_manifest["months"]:
            path = base / month.replace("-", "/")
            manifest = json.loads((path / "manifest.json").read_text())
            for name, digest in manifest["files"].items():
                self.assertEqual(sha((path / name).read_bytes()), digest)
            metadata = csv_rows(path / "posts_meta.csv")
            texts = csv_rows(path / "posts_text.csv")
            cms = csv_rows(path / "creator_month.csv")
            ncreator_month += len(cms)
            local = {r["post_id"] for r in metadata}
            self.assertFalse(seen & local)
            seen |= local
            for row in metadata:
                self.assertEqual(row["year_month"], month)
                self.assertIn(row["creator_id"], ids)
                if row["publication_id"]:
                    self.assertIn(row["publication_id"], publications)
                self.assertEqual(row["selected_primary_post"], "False")
                self.assertEqual(row["historical_text_snapshot_verified"], "False")
            for row in texts:
                self.assertIn(row["post_id"], local)
                self.assertGreaterEqual(int(row["prose_word_count"]), 200)
                self.assertEqual(sha(row["text"]), row["text_sha256"])
                self.assertEqual(row["selected_primary_post"], "False")
            for cm in cms:
                selected = [r for r in texts if r["creator_id"] == cm["creator_id"]]
                self.assertEqual(len(selected), int(cm["toy_text_count"]))
                self.assertLessEqual(len(selected), 3)
                self.assertEqual(cm["history_complete"], "")
                self.assertEqual(cm["inclusion_weight"], "")
            ntext += len(texts)
        self.assertEqual(len(seen), root_manifest["metadata_posts"])
        self.assertEqual(ntext, root_manifest["selected_toy_texts"])
        self.assertEqual(ncreator_month, root_manifest["creator_months"])
        self.assertEqual(root_manifest["primary_texts"], 0)


if __name__ == "__main__":
    unittest.main()
