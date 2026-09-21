from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_substack_creator_frame import build_audit  # noqa: E402
from build_substack_month_from_history import partition_status  # noqa: E402
from build_substack_platform_month import (  # noqa: E402
    Checkpoint,
    primary_text_eligible,
)
from collect_substack_history import HISTORY_STAGE  # noqa: E402


def post(
    post_id: str,
    creator_id: str,
    publication_id: str,
    publication_name: str,
    word_count: int,
) -> dict:
    full_text = " ".join(["word"] * word_count)
    return {
        "post_id": post_id,
        "published_at": "2020-01-15T12:00:00Z",
        "title": f"Title {post_id}",
        "subtitle": "technology science",
        "language": "en",
        "full_text": full_text,
        "retained_word_count": word_count,
        "publication_id": publication_id,
        "publication": {
            "publication_id": publication_id,
            "publication_name": publication_name,
            "publication_type": "personal",
            "language": "en",
            "primary_creator_id": creator_id,
        },
        "bylines": [
            {
                "creator_id": creator_id,
                "name": f"Creator {creator_id}",
                "handle": f"creator{creator_id}",
                "publication_role": "admin",
            }
        ],
    }


class SamplingGateTests(unittest.TestCase):
    def test_frame_cap_cannot_override_creator_shortfall(self) -> None:
        self.assertEqual(
            partition_status(1400, 2500, 300, True, True),
            "frame_expansion_required",
        )
        self.assertEqual(
            partition_status(1400, 2500, 300, True, False),
            "pilot_incomplete",
        )
        self.assertEqual(
            partition_status(2500, 2500, 0, True, True),
            "target_achieved",
        )
        self.assertNotEqual(
            partition_status(2500, 2500, 0, False, True),
            "target_achieved",
        )

    def test_text_eligibility_requires_english_public_body_and_200_words(self) -> None:
        eligible = {
            "full_text": "body",
            "retained_word_count": 200,
            "language": "en-US",
        }
        self.assertTrue(primary_text_eligible(eligible))
        self.assertFalse(
            primary_text_eligible({**eligible, "retained_word_count": 199})
        )
        self.assertFalse(primary_text_eligible({**eligible, "language": "fr"}))

    def test_creator_audit_deduplicates_numeric_ids_across_publications(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame_file = root / "frame.csv"
            with frame_file.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "publication_url",
                        "frame_lastmod",
                        "frame_priority",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "publication_url": "https://one.example",
                            "frame_lastmod": "2026-09-01",
                            "frame_priority": "a",
                        },
                        {
                            "publication_url": "https://two.example",
                            "frame_lastmod": "2026-09-01",
                            "frame_priority": "b",
                        },
                    ]
                )

            cache_root = root / "cache"
            checkpoint = Checkpoint(cache_root / "crawl.sqlite3")
            checkpoint.put(
                HISTORY_STAGE,
                "https://one.example",
                True,
                {
                    "posts": [post("p1", "101", "pub1", "One", 250)]
                },
                None,
            )
            checkpoint.put(
                HISTORY_STAGE,
                "https://two.example",
                True,
                {
                    "posts": [
                        post("p2", "101", "pub2", "Two", 250),
                        post("p3", "202", "pub2", "Two", 50),
                    ]
                },
                None,
            )
            checkpoint.close()

            output_dir = root / "audit"
            manifest = build_audit(
                Namespace(
                    start_month="2020-01",
                    end_month="2020-01",
                    frame_size=2,
                    target_creators=2,
                    topic_floor=0,
                    history_cache_root=cache_root,
                    frame_file=frame_file,
                    output_dir=output_dir,
                )
            )
            self.assertEqual(manifest["deduplicated_numeric_creators"], 2)
            self.assertEqual(manifest["creator_publication_relationships"], 3)
            self.assertFalse(manifest["ready_for_final_month_build"])

            with (output_dir / "creators.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                creators = {row["creator_id"]: row for row in csv.DictReader(handle)}
            self.assertEqual(creators["101"]["observed_publication_count"], "2")

            with (output_dir / "monthly_coverage.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                coverage = list(csv.DictReader(handle))
            overall = next(row for row in coverage if row["topic_family"] == "__all__")
            self.assertEqual(overall["primary_text_eligible_creators"], "1")
            self.assertEqual(overall["frame_status"], "frame_expansion_required")

            parsed_manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(parsed_manifest["ready_for_final_month_build"])


if __name__ == "__main__":
    unittest.main()
