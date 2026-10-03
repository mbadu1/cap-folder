#!/usr/bin/env python3
"""Materialize and validate one Substack month from the shared history cache."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_substack_platform_month import Checkpoint  # noqa: E402
from collect_substack_history import HISTORY_STAGE  # noqa: E402


MONTH_STAGE = "publication_posts_api_v2"


def partition_status(
    selected_creators: int,
    target_creators: int,
    topic_floor_shortfall: int,
    text_bounds_valid: bool,
    frame_complete: bool,
) -> str:
    if (
        selected_creators == target_creators
        and topic_floor_shortfall == 0
        and text_bounds_valid
    ):
        return "target_achieved"
    if frame_complete and text_bounds_valid:
        return "frame_exhausted_census"
    return "pilot_incomplete"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True)
    parser.add_argument("--frame-size", type=int, required=True)
    parser.add_argument("--max-frame-size", type=int, default=20000)
    parser.add_argument("--target-creators", type=int, default=2500)
    parser.add_argument("--topic-floor", type=int, default=150)
    parser.add_argument(
        "--frame-file",
        type=Path,
        default=Path(
            "data/monthly_full/frame/"
            "substack_publications_full_2026-09-21.csv"
        ),
    )
    parser.add_argument(
        "--history-cache-root",
        type=Path,
        default=Path(".cache/substack_history/history_v1"),
    )
    parser.add_argument(
        "--month-cache-root",
        type=Path,
        default=Path(".cache/substack_platform_month"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/monthly_full/substack"),
    )
    return parser.parse_args()


def load_frame(path: Path, frame_size: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: (row["frame_priority"], row["publication_url"]))
    if frame_size > len(rows):
        raise ValueError(
            f"requested frame {frame_size} exceeds frozen frame {len(rows)}"
        )
    return rows[:frame_size]


def materialize_month_cache(args: argparse.Namespace) -> dict[str, int]:
    frame_rows = load_frame(args.frame_file, args.frame_size)
    frame_urls = {row["publication_url"] for row in frame_rows}
    history = Checkpoint(args.history_cache_root / "crawl.sqlite3")
    history_rows = {
        row["item_key"]: row
        for row in history.rows(HISTORY_STAGE)
        if row["item_key"] in frame_urls
    }
    month_checkpoint = Checkpoint(
        args.month_cache_root
        / args.month
        / "api_first_v2"
        / "crawl.sqlite3"
    )
    success_count = 0
    failure_count = 0
    post_count = 0
    for frame_row in frame_rows:
        publication_url = frame_row["publication_url"]
        history_row = history_rows.get(publication_url)
        if not history_row:
            month_checkpoint.put(
                MONTH_STAGE,
                publication_url,
                False,
                None,
                RuntimeError("publication is missing from shared history cache"),
            )
            failure_count += 1
            continue
        if not history_row["ok"] or not history_row["payload"]:
            month_checkpoint.put(
                MONTH_STAGE,
                publication_url,
                False,
                None,
                RuntimeError(
                    f"history collection failed: {history_row['error_type']}: "
                    f"{history_row['error_message']}"
                ),
            )
            failure_count += 1
            continue
        posts = [
            post
            for post in history_row["payload"].get("posts") or []
            if str(post.get("published_at") or "").startswith(args.month)
        ]
        month_checkpoint.put(
            MONTH_STAGE,
            publication_url,
            True,
            {
                "publication_url": publication_url,
                "method": "frozen_shared_history_cache_v1",
                "api_error": "",
                "posts": posts,
            },
            None,
        )
        success_count += 1
        post_count += len(posts)
    history.close()
    month_checkpoint.close()
    return {
        "frame_publications": len(frame_rows),
        "successful_history_rows": success_count,
        "failed_or_missing_history_rows": failure_count,
        "month_posts_in_cache": post_count,
    }


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_partition(args: argparse.Namespace) -> dict[str, Any]:
    year, month_number = args.month.split("-")
    root = args.output_root / year / month_number
    required = [
        "posts_meta.csv",
        "posts_text.csv",
        "creator_month.csv",
        "coverage.csv",
        "scrape_errors.csv",
        "manifest.json",
        "README.md",
    ]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise RuntimeError(f"missing monthly outputs: {missing}")
    with (root / "posts_meta.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        meta = list(csv.DictReader(handle))
    with (root / "posts_text.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        texts = list(csv.DictReader(handle))
    with (root / "creator_month.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        creators = list(csv.DictReader(handle))
    with (root / "coverage.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        coverage = list(csv.DictReader(handle))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    creator_ids = [row["creator_id"] for row in creators]
    meta_ids = [row["post_id"] for row in meta]
    text_ids = [row["post_id"] for row in texts]
    selected_text_assignments = sum(
        int(row["selected_text_count"]) for row in creators
    )
    creator_text_bounds_valid = all(
        1 <= int(row["selected_text_count"]) <= 3 for row in creators
    )
    checks = {
        "creator_ids_unique": len(creator_ids) == len(set(creator_ids)),
        "creator_ids_numeric": all(value.isdigit() for value in creator_ids),
        "post_ids_unique": len(meta_ids) == len(set(meta_ids)),
        "text_ids_unique": len(text_ids) == len(set(text_ids)),
        "text_ids_join_metadata": set(text_ids) <= set(meta_ids),
        "month_dates_valid": all(
            row["published_at"].startswith(args.month) for row in meta
        ),
        "creator_text_bounds_valid": creator_text_bounds_valid,
        "selected_text_assignment_bounds_valid": (
            len(creators) <= selected_text_assignments <= 3 * len(creators)
        ),
        "coverage_has_eight_topics": len(coverage) == 8,
        "manifest_creator_count_matches": (
            int(manifest["counts"]["selected_creators"]) == len(creators)
        ),
        "manifest_metadata_count_matches": (
            int(manifest["counts"]["metadata_posts"]) == len(meta)
        ),
        "manifest_text_count_matches": (
            int(manifest["counts"]["selected_texts"]) == len(texts)
        ),
        "manifest_text_assignment_count_matches": (
            int(manifest["counts"]["selected_text_assignments"])
            == selected_text_assignments
        ),
    }
    for name, metadata in manifest.get("files", {}).items():
        if name in {"manifest.json", "README.md"}:
            continue
        path = root / name
        checks[f"hash_matches_{name}"] = (
            path.is_file() and hash_file(path) == metadata.get("sha256")
        )
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise RuntimeError(f"monthly validation failed: {failed}")

    selected = len(creators)
    total_floor_shortfall = sum(
        int(row["topic_floor_shortfall"]) for row in coverage
    )
    status = partition_status(
        selected,
        args.target_creators,
        total_floor_shortfall,
        creator_text_bounds_valid,
        args.frame_size >= args.max_frame_size,
    )
    ready_to_publish = status in {
        "target_achieved",
        "frame_exhausted_census",
    } and all(checks.values())
    report = {
        "month": args.month,
        "frame_size": args.frame_size,
        "max_frame_size": args.max_frame_size,
        "target_creators": args.target_creators,
        "selected_creators": selected,
        "metadata_posts": len(meta),
        "selected_texts": len(texts),
        "selected_text_assignments": selected_text_assignments,
        "selected_text_assignment_minimum": selected,
        "selected_text_assignment_maximum": selected * 3,
        "topic_floor_shortfall": total_floor_shortfall,
        "frame_status": status,
        "ready_to_publish": ready_to_publish,
        "checks": checks,
    }
    (root / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    if args.frame_size > args.max_frame_size:
        raise ValueError("frame size cannot exceed maximum frame size")
    cache_counts = materialize_month_cache(args)
    command = [
        sys.executable,
        str(SCRIPT_DIR / "build_substack_platform_month.py"),
        "--month",
        args.month,
        "--frame-size",
        str(args.frame_size),
        "--frame-file",
        str(args.frame_file),
        "--target-creators",
        str(args.target_creators),
        "--topic-floor",
        str(args.topic_floor),
        "--output-root",
        str(args.output_root),
        "--cache-root",
        str(args.month_cache_root),
    ]
    subprocess.run(command, check=True)
    report = validate_partition(args)
    print(
        json.dumps(
            {"cache": cache_counts, "validation": report},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if report["ready_to_publish"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
