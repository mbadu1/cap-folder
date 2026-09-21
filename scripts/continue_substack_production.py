#!/usr/bin/env python3
"""Advance the Substack production run by one bounded collection chunk."""

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


def main() -> int:
    args = parse_args()
    production_state = args.history_cache_root / "production_state.json"
    if production_state.is_file():
        state = json.loads(production_state.read_text(encoding="utf-8"))
        if len(state.get("completed_months") or []) >= 81:
            print(json.dumps({"status": "complete", **state}, indent=2))
            return 0

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
        result = subprocess.run(command)
        if result.returncode not in {0, 2, 4}:
            return result.returncode
        total, successful, failed = counts(args.history_cache_root)

    print(
        json.dumps(
            {
                "status": "history_in_progress" if total < args.frame_size else "history_complete",
                "checkpointed_publications": total,
                "successful_publications": successful,
                "failed_publications": failed,
                "frame_size": args.frame_size,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    if total < args.frame_size:
        return 0

    production_command = [
        sys.executable,
        str(SCRIPT_DIR / "run_substack_production.py"),
        "--start-month",
        "2020-01",
        "--end-month",
        "2026-09",
        "--frame-size",
        str(args.frame_size),
        "--target-creators",
        "2500",
        "--topic-floor",
        "150",
        "--delay-seconds",
        "2.0",
        "--cooldown-seconds",
        "60",
        "--error-retry-attempts",
        "1",
        "--branch",
        args.branch,
        "--frame-file",
        str(args.frame_file),
        "--history-cache-root",
        str(args.history_cache_root),
    ]
    return subprocess.run(production_command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
