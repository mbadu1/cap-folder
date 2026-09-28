#!/usr/bin/env python3
"""Commit and push one validated Substack monthly partition."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


MAX_REGULAR_GIT_FILE_BYTES = 100 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True)
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
        "--frame-dir",
        type=Path,
        default=Path("data/monthly_full/frame"),
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch")
    parser.add_argument("--min-push-interval", type=float, default=11.0)
    parser.add_argument(
        "--push-state-file",
        type=Path,
        default=Path(".cache/substack_publish_last_push.json"),
    )
    return parser.parse_args()


def run(*command: str, capture: bool = False) -> str:
    result = subprocess.run(
        list(command),
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return (result.stdout or "").strip()


def main() -> int:
    args = parse_args()
    year, month_number = args.month.split("-")
    month_dir = args.output_root / year / month_number
    validation_path = month_dir / "validation.json"
    if not validation_path.is_file():
        raise RuntimeError(f"missing validation report: {validation_path}")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if not validation.get("ready_to_publish"):
        raise RuntimeError(
            f"month is not ready to publish: {json.dumps(validation, sort_keys=True)}"
        )
    oversized = [
        path
        for path in month_dir.rglob("*")
        if path.is_file() and path.stat().st_size >= MAX_REGULAR_GIT_FILE_BYTES
    ]
    if oversized:
        raise RuntimeError(
            "regular Git rejects files at or above 100 MiB; configure Git LFS "
            f"or shard these files before publishing: {oversized}"
        )

    branch = args.branch or run("git", "branch", "--show-current", capture=True)
    if not branch:
        raise RuntimeError("cannot publish from a detached HEAD")
    run(
        "git", "add", "--",
        str(month_dir), str(args.lookup_dir), str(args.frame_dir),
    )
    staged = run(
        "git", "diff", "--cached", "--name-only", "--diff-filter=ACMR",
        capture=True,
    ).splitlines()
    allowed_prefixes = (
        str(month_dir).rstrip("/") + "/",
        str(args.lookup_dir).rstrip("/") + "/",
        str(args.frame_dir).rstrip("/") + "/",
    )
    unexpected = [
        path for path in staged
        if not any(path.startswith(prefix) for prefix in allowed_prefixes)
    ]
    if unexpected:
        raise RuntimeError(f"unexpected staged files: {unexpected}")
    if staged:
        run(
            "git",
            "commit",
            "-m",
            f"Add Substack monthly dataset for {args.month}",
        )

    last_push = 0.0
    if args.push_state_file.is_file():
        state = json.loads(args.push_state_file.read_text(encoding="utf-8"))
        last_push = float(state.get("last_push_epoch") or 0.0)
    delay = args.min_push_interval - (time.time() - last_push)
    if delay > 0:
        time.sleep(delay)
    run("git", "push", "-u", args.remote, branch)
    args.push_state_file.parent.mkdir(parents=True, exist_ok=True)
    args.push_state_file.write_text(
        json.dumps(
            {"last_push_epoch": time.time(), "month": args.month},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "month": args.month,
                "branch": branch,
                "remote": args.remote,
                "commit": run("git", "rev-parse", "HEAD", capture=True),
                "files_staged": staged,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
