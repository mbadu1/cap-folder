#!/usr/bin/env python3
"""Advance the Substack history collection by one bounded, resumable chunk.

This continuation command never builds, commits, or publishes monthly data.
It collects the entire frozen publication frame before auditing creator-level
coverage once, so the audit does not slow each collection chunk.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_STAGE = "publication_history_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frame-size",
        type=int,
        default=0,
        help="Maximum frame rows; zero uses the full frozen universe.",
    )
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=2.0,
        help="Minimum interval between request starts; use at least 1.5 seconds.",
    )
    parser.add_argument("--branch", default="zherui-substack-month-pilot")
    parser.add_argument("--retry-errors", action="store_true")
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
        "--audit-output-dir",
        type=Path,
        default=Path("data/monthly_full/frame_audit"),
    )
    return parser.parse_args()


def load_frame_urls(frame_file: Path, frame_size: int = 0) -> set[str]:
    if not frame_file.is_file():
        return set()
    with frame_file.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: (row["frame_priority"], row["publication_url"]))
    if frame_size:
        rows = rows[:frame_size]
    return {row["publication_url"] for row in rows}


def counts(
    cache_root: Path,
    frame_file: Path | None = None,
    frame_size: int = 0,
) -> tuple[int, int, int]:
    path = cache_root / "crawl.sqlite3"
    if not path.is_file():
        return 0, 0, 0
    frame_urls = load_frame_urls(frame_file, frame_size) if frame_file else set()
    connection = sqlite3.connect(path)
    rows = connection.execute(
        """
        SELECT item_key, ok
        FROM parsed WHERE stage = ?
        """,
        (HISTORY_STAGE,),
    ).fetchall()
    connection.close()
    if frame_urls:
        rows = [row for row in rows if row[0] in frame_urls]
    successful = sum(bool(row[1]) for row in rows)
    return len(rows), successful, len(rows) - successful


def frozen_frame_size(frame_file: Path) -> int:
    if not frame_file.is_file():
        return 0
    with frame_file.open("r", encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def load_audit_manifest(output_dir: Path) -> dict:
    path = output_dir / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def final_audit_is_due(
    manifest: dict,
    checkpointed: int,
    frame_size: int,
    frame_file: Path,
) -> bool:
    if not frame_size or checkpointed < frame_size:
        return False
    return (
        manifest.get("frame_file") != str(frame_file)
        or int(manifest.get("frame_size_target") or 0) != frame_size
        or int(manifest.get("checkpointed_publications") or 0) != frame_size
    )


def run_audit(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "audit_substack_creator_frame.py"),
        "--frame-size",
        str(args.frame_size),
        "--frame-file",
        str(args.frame_file),
        "--history-cache-root",
        str(args.history_cache_root),
        "--output-dir",
        str(args.audit_output_dir),
    ]
    return subprocess.run(command).returncode


def main() -> int:
    args = parse_args()
    if (args.history_cache_root / "TEAM_HANDOFF.json").exists():
        raise RuntimeError("This baseline was retired for team collection; use team_collection.py and your assigned shard.")
    if args.chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    if args.delay_seconds < 1.5:
        raise ValueError("delay must be at least 1.5 seconds")
    if args.frame_size < 0:
        raise ValueError("frame size cannot be negative")

    total, successful, failed = counts(
        args.history_cache_root,
        args.frame_file,
        args.frame_size,
    )
    manifest = load_audit_manifest(args.audit_output_dir)
    effective_frame_size = frozen_frame_size(args.frame_file)
    if not effective_frame_size or total < effective_frame_size:
        command = [
            sys.executable,
            str(SCRIPT_DIR / "collect_substack_history.py"),
            "--max-frame-size",
            str(args.frame_size),
            "--collection-size",
            str(args.frame_size),
            "--max-new-publications",
            str(args.chunk_size),
            "--delay-seconds",
            str(args.delay_seconds),
            "--frame-file",
            str(args.frame_file),
            "--cache-root",
            str(args.history_cache_root),
        ]
        if not args.retry_errors:
            command.append("--keep-errors")
        result = subprocess.run(command)
        if result.returncode not in {0, 2, 4}:
            return result.returncode
        total, successful, failed = counts(
            args.history_cache_root,
            args.frame_file,
            args.frame_size,
        )
        effective_frame_size = frozen_frame_size(args.frame_file)
        if not effective_frame_size:
            raise RuntimeError("collector did not create a frozen frame")

    if final_audit_is_due(
        manifest,
        total,
        effective_frame_size,
        args.frame_file,
    ):
        args.frame_size = effective_frame_size
        if run_audit(args) != 0:
            return 5
        manifest = load_audit_manifest(args.audit_output_dir)

    frame_complete = total >= effective_frame_size
    audit_current = frame_complete and not final_audit_is_due(
        manifest,
        total,
        effective_frame_size,
        args.frame_file,
    )
    if frame_complete and not audit_current:
        raise RuntimeError("full-frame coverage audit did not produce a current manifest")
    targets_achieved = bool(
        audit_current
        and manifest.get("all_months_target_achieved")
        and manifest.get("all_months_topic_floors_achieved")
    )
    if frame_complete and targets_achieved:
        status = "target_achieved"
    elif frame_complete:
        status = "frame_exhausted_census"
    else:
        status = "history_in_progress"
    print(
        json.dumps(
            {
                "status": status,
                "checkpointed_publications": total,
                "successful_publications": successful,
                "failed_publications": failed,
                "frame_size": effective_frame_size,
                "audited_publications": int(
                    manifest.get("checkpointed_publications") or 0
                ),
                "coverage_audit_current": audit_current,
                "deduplicated_numeric_creators": int(
                    manifest.get("deduplicated_numeric_creators") or 0
                ),
                "all_months_target_achieved": (
                    bool(manifest.get("all_months_target_achieved"))
                    if audit_current else None
                ),
                "all_months_topic_floors_achieved": (
                    bool(manifest.get("all_months_topic_floors_achieved"))
                    if audit_current else None
                ),
                "monthly_build_started": False,
                "monthly_publish_started": False,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
