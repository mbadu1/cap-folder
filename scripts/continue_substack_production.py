#!/usr/bin/env python3
"""Advance the Substack history collection by one bounded, resumable chunk.

This continuation command never builds, commits, or publishes monthly data.
It periodically audits creator-level coverage and stops at the frozen
publication-frame boundary so an undersized creator frame cannot be mistaken
for a completed monthly sample.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_STAGE = "publication_history_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-size", type=int, default=20000)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--branch", default="zherui-substack-month-pilot")
    parser.add_argument("--audit-step", type=int, default=2500)
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument(
        "--frame-file",
        type=Path,
        default=Path(
            "data/monthly_full/frame/"
            "substack_publications_2026-09-21.csv"
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


def counts(cache_root: Path) -> tuple[int, int, int]:
    path = cache_root / "crawl.sqlite3"
    if not path.is_file():
        return 0, 0, 0
    connection = sqlite3.connect(path)
    row = connection.execute(
        """
        SELECT COUNT(*), SUM(ok), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END)
        FROM parsed WHERE stage = ?
        """,
        (HISTORY_STAGE,),
    ).fetchone()
    connection.close()
    return tuple(int(value or 0) for value in row)


def load_audit_manifest(output_dir: Path) -> dict:
    path = output_dir / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def audit_is_due(
    manifest: dict, checkpointed: int, frame_size: int, audit_step: int
) -> bool:
    if not manifest:
        return True
    audited = int(manifest.get("checkpointed_publications") or 0)
    return checkpointed >= frame_size or checkpointed - audited >= audit_step


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
    if args.chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    if args.audit_step <= 0:
        raise ValueError("audit step must be positive")

    total, successful, failed = counts(args.history_cache_root)
    if total < args.frame_size:
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
            "2.0",
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
        total, successful, failed = counts(args.history_cache_root)

    manifest = load_audit_manifest(args.audit_output_dir)
    if audit_is_due(manifest, total, args.frame_size, args.audit_step):
        if run_audit(args) != 0:
            return 5
        manifest = load_audit_manifest(args.audit_output_dir)

    frame_complete = total >= args.frame_size
    creator_frame_ready = bool(manifest.get("ready_for_final_month_build"))
    if not frame_complete:
        status = "history_in_progress"
    elif creator_frame_ready:
        status = "creator_frame_ready_for_month_build"
    else:
        status = "frame_expansion_required"
    print(
        json.dumps(
            {
                "status": status,
                "checkpointed_publications": total,
                "successful_publications": successful,
                "failed_publications": failed,
                "frame_size": args.frame_size,
                "audited_publications": int(
                    manifest.get("checkpointed_publications") or 0
                ),
                "deduplicated_numeric_creators": int(
                    manifest.get("deduplicated_numeric_creators") or 0
                ),
                "all_months_target_achieved": bool(
                    manifest.get("all_months_target_achieved")
                ),
                "all_months_topic_floors_achieved": bool(
                    manifest.get("all_months_topic_floors_achieved")
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
