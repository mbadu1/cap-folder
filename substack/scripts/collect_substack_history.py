#!/usr/bin/env python3
"""Collect one frozen Substack publication frame across the study window.

The collector retrieves each sampled publication history once. Parsed records
are checkpointed in SQLite and later filtered into monthly partitions, avoiding
81 repeated crawls of the same publication. Public/free bodies are retained;
paid post bodies are not retained.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_substack_platform_month import (  # noqa: E402
    GLOBAL_SITEMAPS,
    PLATFORM,
    XML_NS,
    Checkpoint,
    Collector,
    canonical_base_url,
    iso_now,
    post_from_api_record,
    run_cached_stage,
    sha256_file,
    sha256_text,
    write_csv,
)


HISTORY_STAGE = "publication_history_v1"


def summarize_checkpoint(
    checkpoint: Checkpoint,
    selected_urls: set[str],
) -> tuple[int, int, int, list[dict[str, str]]]:
    """Summarize cached history without materializing every post in memory."""
    successful = 0
    failed = 0
    post_count = 0
    errors: list[dict[str, str]] = []
    cursor = checkpoint.connection.execute(
        """
        SELECT item_key, ok,
               CASE WHEN ok THEN COALESCE(json_array_length(payload, '$.posts'), 0)
                    ELSE 0 END AS retained_post_count,
               error_type, error_message
        FROM parsed WHERE stage = ?
        """,
        (HISTORY_STAGE,),
    )
    for key, ok, row_post_count, error_type, error_message in cursor:
        if key not in selected_urls:
            continue
        if ok:
            successful += 1
            post_count += int(row_post_count or 0)
        else:
            failed += 1
            errors.append(
                {
                    "publication_url": key,
                    "error_type": error_type or "",
                    "error_message": error_message or "",
                }
            )
    return successful, failed, post_count, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2026-09-17")
    parser.add_argument(
        "--max-frame-size",
        type=int,
        default=0,
        help="Maximum frozen-frame rows; zero freezes the full discovered universe.",
    )
    parser.add_argument(
        "--collection-size",
        type=int,
        default=0,
        help="Rows in scope for collection; zero uses the entire frozen frame.",
    )
    parser.add_argument(
        "--max-new-publications",
        type=int,
        default=0,
        help=(
            "Maximum uncached publications to process in this invocation; "
            "zero processes the entire remaining frame."
        ),
    )
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--max-rate-limit-events", type=int, default=3)
    parser.add_argument("--rate-limit-window-seconds", type=int, default=600)
    parser.add_argument(
        "--frame-file",
        type=Path,
        default=Path(
            "data/monthly_full/frame/"
            "substack_publications_full_2026-09-21.csv"
        ),
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".cache/substack_history/history_v1"),
    )
    parser.add_argument(
        "--permission-basis",
        default="user_reported_written_substack_permission",
    )
    parser.add_argument(
        "--keep-errors",
        action="store_true",
        help="Do not retry publication rows that previously ended in error.",
    )
    return parser.parse_args()


def load_live_frame(
    collector: Collector,
    max_frame_size: int,
) -> tuple[list[dict[str, str]], int, dict[str, str]]:
    indexed: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    for url in GLOBAL_SITEMAPS:
        xml_bytes = collector.fetch(url)
        source_hashes[url] = hashlib.sha256(xml_bytes).hexdigest()
        root = ET.fromstring(xml_bytes)
        for node in root.findall("s:url", XML_NS):
            location = node.findtext("s:loc", default="", namespaces=XML_NS)
            lastmod = node.findtext("s:lastmod", default="", namespaces=XML_NS)
            if location:
                indexed[canonical_base_url(location)] = lastmod
    rows = [
        {
            "publication_url": url,
            "frame_lastmod": lastmod,
            "frame_priority": sha256_text(
                f"publication-frame-v1|{PLATFORM}|{url}"
            ),
        }
        for url, lastmod in indexed.items()
    ]
    rows.sort(key=lambda row: (row["frame_priority"], row["publication_url"]))
    selected_count = (
        len(rows) if max_frame_size == 0 else min(max_frame_size, len(rows))
    )
    return rows[:selected_count], len(rows), source_hashes


def load_or_create_frame(
    collector: Collector,
    frame_path: Path,
    max_frame_size: int,
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    manifest_path = frame_path.with_suffix(".manifest.json")
    if frame_path.is_file() and manifest_path.is_file():
        with frame_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if max_frame_size and len(rows) < max_frame_size:
            raise ValueError(
                f"frozen frame has {len(rows)} rows, fewer than requested "
                f"maximum {max_frame_size}"
            )
        rows.sort(
            key=lambda row: (row["frame_priority"], row["publication_url"])
        )
        selected_count = len(rows) if max_frame_size == 0 else max_frame_size
        return rows[:selected_count], manifest

    rows, candidate_count, hashes = load_live_frame(
        collector, max_frame_size
    )
    write_csv(
        frame_path,
        ["publication_url", "frame_lastmod", "frame_priority"],
        rows,
    )
    manifest = {
        "frame_version": "substack-publication-frame-v1",
        "created_at": iso_now(),
        "platform": PLATFORM,
        "study_start_date": start_date,
        "study_end_date": end_date,
        "candidate_publications": candidate_count,
        "frozen_publications": len(rows),
        "priority_spec": "SHA256(publication-frame-v1|substack|publication_url)",
        "global_sitemaps": GLOBAL_SITEMAPS,
        "global_sitemap_sha256": hashes,
        "frame_csv_sha256": sha256_file(frame_path),
        "limitations": [
            "The frame is a current publication snapshot, not a historical census.",
            "Defunct or no-longer-indexed historical publications may be absent.",
            "Creators with multiple publications can have higher first-stage inclusion probability.",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return rows, manifest


def parse_publication_history(
    collector: Collector,
    publication_url: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    posts: dict[str, dict[str, Any]] = {}
    offset = 0
    batch_size = 50
    previous_signature: tuple[str, ...] = ()
    for _ in range(300):
        source_url = (
            f"{publication_url}/api/v1/posts?limit={batch_size}"
            f"&offset={offset}&sort=new"
        )
        payload = json.loads(collector.fetch(source_url))
        if not isinstance(payload, list):
            raise ValueError("posts endpoint did not return a list")
        if not payload:
            break
        signature = tuple(
            str(row.get("id") or row.get("slug") or "")
            for row in payload
            if isinstance(row, dict)
        )
        if signature == previous_signature:
            break
        previous_signature = signature
        batch_dates = []
        for raw_post in payload:
            if not isinstance(raw_post, dict):
                continue
            published_at = str(raw_post.get("post_date") or "")
            published_date = published_at[:10]
            if published_date:
                batch_dates.append(published_date)
            if start_date <= published_date <= end_date:
                parsed = post_from_api_record(
                    "", source_url, raw_post, {}
                )
                posts[parsed["post_id"]] = parsed
        if batch_dates and min(batch_dates) < start_date:
            break
        if len(payload) < batch_size:
            break
        offset += len(payload)
    else:
        raise RuntimeError("publication exceeded 15,000 post safety cap")

    return {
        "publication_url": publication_url,
        "start_date": start_date,
        "end_date": end_date,
        "posts": sorted(
            posts.values(),
            key=lambda row: (row["published_at"], row["post_id"]),
        ),
    }


def main() -> int:
    args = parse_args()
    if (args.cache_root / "TEAM_HANDOFF.json").exists():
        raise RuntimeError("This baseline was retired for team collection; use team_collection.py and your assigned shard.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.start_date):
        raise ValueError("--start-date must use YYYY-MM-DD")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.end_date):
        raise ValueError("--end-date must use YYYY-MM-DD")
    if args.start_date > args.end_date:
        raise ValueError("start date must not exceed end date")
    if args.max_frame_size < 0:
        raise ValueError("maximum frame size cannot be negative")
    if args.collection_size < 0:
        raise ValueError("collection size cannot be negative")
    if (
        args.max_frame_size
        and args.collection_size
        and args.collection_size > args.max_frame_size
    ):
        raise ValueError("collection size cannot exceed maximum frame size")

    started_at = iso_now()
    collector = Collector(
        args.delay_seconds,
        args.max_rate_limit_events,
        args.rate_limit_window_seconds,
    )
    frame_rows, frame_manifest = load_or_create_frame(
        collector,
        args.frame_file,
        args.max_frame_size,
        args.start_date,
        args.end_date,
    )
    collection_size = args.collection_size or len(frame_rows)
    if collection_size > len(frame_rows):
        raise ValueError(
            f"collection size {collection_size} exceeds frozen frame "
            f"of {len(frame_rows)} rows"
        )
    selected_frame = frame_rows[:collection_size]
    checkpoint = Checkpoint(args.cache_root / "crawl.sqlite3")
    if not args.keep_errors:
        checkpoint.connection.execute(
            "DELETE FROM parsed WHERE stage = ? AND ok = 0",
            (HISTORY_STAGE,),
        )
        checkpoint.connection.commit()

    items = [
        (row["publication_url"], row["publication_url"])
        for row in selected_frame
    ]
    if args.max_new_publications < 0:
        raise ValueError("max new publications cannot be negative")
    if args.max_new_publications:
        completed_keys = checkpoint.keys(HISTORY_STAGE)
        cached_items = [item for item in items if item[0] in completed_keys]
        pending_items = [item for item in items if item[0] not in completed_keys]
        items = cached_items + pending_items[: args.max_new_publications]
    run_cached_stage(
        checkpoint,
        HISTORY_STAGE,
        items,
        lambda url: parse_publication_history(
            collector, url, args.start_date, args.end_date
        ),
        args.max_workers,
    )
    selected_urls = {row["publication_url"] for row in selected_frame}
    successful, failed, post_count, errors = summarize_checkpoint(
        checkpoint,
        selected_urls,
    )
    checkpointed = successful + failed
    complete_frame = checkpointed == collection_size
    status = {
        "collection_version": "substack-history-v1",
        "collection_started_at": started_at,
        "collection_completed_at": iso_now(),
        "permission_basis": args.permission_basis,
        "permission_evidence_status": "user_reported_email_not_stored_in_repository",
        "start_date": args.start_date,
        "end_date": args.end_date,
        "frame_file": str(args.frame_file),
        "frame_csv_sha256": frame_manifest.get("frame_csv_sha256", ""),
        "collection_size": collection_size,
        "successful_publications": successful,
        "failed_publications": failed,
        "checkpointed_publications": checkpointed,
        "complete_frame": complete_frame,
        "retained_posts": post_count,
        "request_count_this_run": collector.request_count,
        "http_status_counts_this_run": dict(
            sorted(collector.http_status_counts.items())
        ),
        "delay_seconds": args.delay_seconds,
        "max_workers": args.max_workers,
        "rate_limit_event_count": len(collector.rate_limit_events),
        "errors": errors,
    }
    args.cache_root.mkdir(parents=True, exist_ok=True)
    (args.cache_root / "collection_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    checkpoint.close()
    print(json.dumps(status, indent=2, sort_keys=True), flush=True)
    if not complete_frame:
        return 4
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
