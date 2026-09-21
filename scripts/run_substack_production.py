#!/usr/bin/env python3
"""Audit a complete Substack history frame and optionally publish months.

Monthly build/publish is opt-in and remains blocked unless the creator-level
coverage audit confirms all monthly targets and topic floors.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_STAGE = "publication_history_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-month", default="2020-01")
    parser.add_argument("--end-month", default="2026-09")
    parser.add_argument("--frame-size", type=int, default=20000)
    parser.add_argument("--target-creators", type=int, default=2500)
    parser.add_argument("--topic-floor", type=int, default=150)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--cooldown-seconds", type=int, default=1800)
    parser.add_argument("--error-retry-attempts", type=int, default=3)
    parser.add_argument(
        "--publish-months",
        action="store_true",
        help="Build, commit, and push months after the creator coverage gate passes.",
    )
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch")
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
        "--month-cache-root",
        type=Path,
        default=Path(".cache/substack_platform_month"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/monthly_full/substack"),
    )
    parser.add_argument(
        "--lookup-dir",
        type=Path,
        default=Path("data/monthly_full/lookup"),
    )
    parser.add_argument(
        "--audit-output-dir",
        type=Path,
        default=Path("data/monthly_full/frame_audit"),
    )
    return parser.parse_args()


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = (int(value) for value in start.split("-"))
    end_year, end_month = (int(value) for value in end.split("-"))
    current = date(start_year, start_month, 1)
    final = date(end_year, end_month, 1)
    values = []
    while current <= final:
        values.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return values


def run(command: list[str], check: bool = False) -> int:
    print("RUN " + " ".join(command), flush=True)
    result = subprocess.run(command)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result.returncode


def history_counts(cache_root: Path) -> tuple[int, int, int]:
    path = cache_root / "crawl.sqlite3"
    if not path.is_file():
        return 0, 0, 0
    connection = sqlite3.connect(path)
    total, successful, failed = connection.execute(
        """
        SELECT COUNT(*), SUM(ok), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END)
        FROM parsed WHERE stage = ?
        """,
        (HISTORY_STAGE,),
    ).fetchone()
    connection.close()
    return int(total or 0), int(successful or 0), int(failed or 0)


def collect_history(args: argparse.Namespace) -> None:
    completed_error_attempts = 0
    while True:
        command = [
            sys.executable,
            str(SCRIPT_DIR / "collect_substack_history.py"),
            "--max-frame-size",
            str(args.frame_size),
            "--collection-size",
            str(args.frame_size),
            "--delay-seconds",
            str(args.delay_seconds),
            "--frame-file",
            str(args.frame_file),
            "--cache-root",
            str(args.history_cache_root),
        ]
        if not args.retry_errors:
            command.append("--keep-errors")
        code = run(command)
        total, successful, failed = history_counts(args.history_cache_root)
        print(
            json.dumps(
                {
                    "history_rows": total,
                    "history_successes": successful,
                    "history_failures": failed,
                    "collector_exit_code": code,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if total < args.frame_size:
            print(
                f"History frame incomplete; cooling down {args.cooldown_seconds}s before resume",
                flush=True,
            )
            time.sleep(args.cooldown_seconds)
            continue
        if not args.retry_errors:
            if failed:
                print(
                    f"Keeping {failed} recorded publication errors for audit; "
                    "use --retry-errors for an explicit retry pass",
                    flush=True,
                )
            return
        if failed == 0 and code == 0:
            return
        completed_error_attempts += 1
        if completed_error_attempts >= args.error_retry_attempts:
            print(
                f"Proceeding after {completed_error_attempts} complete-frame attempts; "
                f"{failed} publications remain recorded as collection errors",
                flush=True,
            )
            return
        print(
            f"Retrying {failed} failed publications after {args.cooldown_seconds}s",
            flush=True,
        )
        time.sleep(args.cooldown_seconds)


def reset_lookup_for_production(args: argparse.Namespace) -> None:
    if not args.lookup_dir.exists():
        return
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    backup = (
        args.history_cache_root
        / "preproduction_backups"
        / f"lookup_{timestamp}"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(args.lookup_dir), str(backup))
    print(f"Moved preproduction lookup tables to {backup}", flush=True)


def build_and_publish_month(args: argparse.Namespace, month: str) -> None:
    build_command = [
        sys.executable,
        str(SCRIPT_DIR / "build_substack_month_from_history.py"),
        "--month",
        month,
        "--frame-size",
        str(args.frame_size),
        "--max-frame-size",
        str(args.frame_size),
        "--target-creators",
        str(args.target_creators),
        "--topic-floor",
        str(args.topic_floor),
        "--frame-file",
        str(args.frame_file),
        "--history-cache-root",
        str(args.history_cache_root),
        "--month-cache-root",
        str(args.month_cache_root),
        "--output-root",
        str(args.output_root),
    ]
    run(build_command, check=True)
    publish_command = [
        sys.executable,
        str(SCRIPT_DIR / "publish_substack_month.py"),
        "--month",
        month,
        "--output-root",
        str(args.output_root),
        "--lookup-dir",
        str(args.lookup_dir),
        "--frame-dir",
        str(args.frame_file.parent),
        "--remote",
        args.remote,
    ]
    if args.branch:
        publish_command.extend(["--branch", args.branch])
    run(publish_command, check=True)


def main() -> int:
    args = parse_args()
    months = month_range(args.start_month, args.end_month)
    collect_history(args)
    audit_command = [
        sys.executable,
        str(SCRIPT_DIR / "audit_substack_creator_frame.py"),
        "--start-month",
        args.start_month,
        "--end-month",
        args.end_month,
        "--frame-size",
        str(args.frame_size),
        "--target-creators",
        str(args.target_creators),
        "--topic-floor",
        str(args.topic_floor),
        "--frame-file",
        str(args.frame_file),
        "--history-cache-root",
        str(args.history_cache_root),
        "--output-dir",
        str(args.audit_output_dir),
    ]
    run(audit_command, check=True)
    audit_manifest = json.loads(
        (args.audit_output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if not audit_manifest.get("ready_for_final_month_build"):
        print(
            json.dumps(
                {
                    "status": "frame_expansion_required",
                    "monthly_build_started": False,
                    "monthly_publish_started": False,
                    "coverage_manifest": str(
                        args.audit_output_dir / "manifest.json"
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
        return 3
    if not args.publish_months:
        print(
            json.dumps(
                {
                    "status": "creator_frame_ready_for_month_build",
                    "monthly_build_started": False,
                    "monthly_publish_started": False,
                    "next_action": "rerun with --publish-months after review",
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    state_path = args.history_cache_root / "production_state.json"
    completed_months: list[str] = []
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed_months = list(state.get("completed_months") or [])
    if not completed_months:
        reset_lookup_for_production(args)
    for month in months:
        if month in completed_months:
            continue
        build_and_publish_month(args, month)
        completed_months.append(month)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "completed_months": completed_months,
                    "frame_size": args.frame_size,
                    "target_creators": args.target_creators,
                    "topic_floor": args.topic_floor,
                    "updated_at_epoch": time.time(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {"status": "complete", "months": completed_months},
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
