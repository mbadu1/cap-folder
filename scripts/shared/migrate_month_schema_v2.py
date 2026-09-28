#!/usr/bin/env python3
"""Migrate an existing monthly pilot to the consolidated CSV schema."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


DROP_POST_FIELDS = {"retrieved_at", "parser_version"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("month_dir", type=Path)
    parser.add_argument("--frame-id", required=True)
    parser.add_argument("--sample-stratum", default="permanent_random_pilot")
    parser.add_argument("--target-creators", type=int, default=2500)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"Cannot infer schema for empty file: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    month_dir = args.month_dir
    for filename in ("posts_meta.csv", "posts_text.csv"):
        path = month_dir / filename
        rows = read_csv(path)
        cleaned = [
            {key: value for key, value in row.items() if key not in DROP_POST_FIELDS}
            for row in rows
        ]
        write_csv(path, cleaned)

    creator_path = month_dir / "creator_month.csv"
    creator_rows = read_csv(creator_path)
    for row in creator_rows:
        row["macro_domain"] = "Knowledge/professional"
        row["sample_stratum"] = args.sample_stratum
        row["creator_priority"] = hashlib.sha256(
            f"creator-priority-v1|{row['platform']}|{row['creator_id']}".encode()
        ).hexdigest()
        row["eligible"] = "True"
        row["selected"] = "True"
        row["inclusion_weight"] = ""
        row["frame_id"] = args.frame_id
    write_csv(creator_path, creator_rows)

    coverage_path = month_dir / "coverage.csv"
    coverage_rows = read_csv(coverage_path)
    for row in coverage_rows:
        row["frame_id"] = args.frame_id
        row["sample_stratum"] = args.sample_stratum
        row["target_creators"] = str(args.target_creators)
    write_csv(coverage_path, coverage_rows)

    assignment_path = month_dir / "sampling_assignments.csv"
    if assignment_path.exists():
        assignment_path.unlink()

    manifest_path = month_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "monthly-csv-v2"
    manifest["sampling_fields_location"] = "creator_month.csv"
    manifest["batch_metadata_location"] = "manifest.json"
    data_files = [
        month_dir / "posts_meta.csv",
        month_dir / "posts_text.csv",
        month_dir / "creator_month.csv",
        month_dir / "coverage.csv",
        month_dir / "scrape_errors.csv",
    ]
    manifest["files"] = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in data_files
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(month_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
