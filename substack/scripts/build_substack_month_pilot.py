#!/usr/bin/env python3
"""Build one monthly Substack CSV pilot from a normalized source CSV.

This parser performs no network requests. It keeps metadata for every source
post in the requested month and retains at most three length-qualified texts
using the fixed post-priority rule from DATASET_DESIGN.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PLATFORM = "substack"
CREATOR_ID = "2779232"
CREATOR_NAME = "Adam Tooze"
CREATOR_PROFILE_URL = "https://substack.com/@adamtooze"
PUBLICATION_ID = "substack:publication:192845"
PUBLICATION_NAME = "Chartbook"
PUBLICATION_URL = "https://adamtooze.substack.com"
TOPIC_FAMILY = "Business, finance and economics"
MACRO_DOMAIN = "Knowledge/professional"
SAMPLE_VERSION = "pilot-substack-2026-09-v1"
PARSER_VERSION = "build_substack_month_pilot_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--month", default="2026-09")
    parser.add_argument("--cutoff-date", default="2026-09-17")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/monthly_pilot/substack"),
    )
    return parser.parse_args()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def post_priority(post_id: str) -> str:
    return sha256_text(f"post-priority-v1|{PLATFORM}|{post_id}")


def write_csv(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def upsert_csv(
    path: Path,
    fieldnames: list[str],
    rows: Iterable[dict[str, Any]],
    key_fields: tuple[str, ...],
) -> None:
    """Add or update global lookup rows without duplicating stable keys."""
    indexed: dict[tuple[str, ...], dict[str, Any]] = {}
    if path.is_file():
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                indexed[tuple(row[field] for field in key_fields)] = row
    for row in rows:
        indexed[tuple(str(row[field]) for field in key_fields)] = row
    ordered = [indexed[key] for key in sorted(indexed)]
    write_csv(path, fieldnames, ordered)


def read_source(
    input_path: Path, month: str, cutoff_date: str
) -> list[dict[str, str]]:
    cutoff = datetime.fromisoformat(f"{cutoff_date}T23:59:59+00:00")
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        rows = []
        for row in csv.DictReader(handle):
            published = row.get("published_at", "")
            if not published.startswith(month):
                continue
            if parse_timestamp(published) > cutoff:
                continue
            rows.append(row)
    return sorted(rows, key=lambda row: (row["published_at"], row["post_id"]))


def build(
    input_path: Path,
    month: str,
    cutoff_date: str,
    output_root: Path,
) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("--month must use YYYY-MM")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    source_rows = read_source(input_path, month, cutoff_date)
    if not source_rows:
        raise ValueError(f"No source rows found for {month} through {cutoff_date}")

    year, month_number = month.split("-")
    month_dir = output_root / year / month_number
    lookup_dir = output_root.parent / "lookup"
    observed_at = iso_now()

    enriched: list[dict[str, Any]] = []
    for source in source_rows:
        word_count = int(source.get("word_count") or 0)
        post_id = source["post_id"]
        completeness = "unverified_fullness" if word_count >= 200 else "preview"
        enriched.append(
            {
                "platform": PLATFORM,
                "creator_id": CREATOR_ID,
                "publication_id": PUBLICATION_ID,
                "post_id": post_id,
                "url": source["url"],
                "published_at": source["published_at"],
                "year_month": month,
                "title": source.get("title", ""),
                "subtitle": source.get("subtitle", ""),
                "byline": source.get("author", ""),
                "post_type": source.get("post_type", ""),
                "audience": source.get("audience", ""),
                "is_paywalled": "unknown",
                "word_count": word_count,
                "language": "en",
                "topic_family": TOPIC_FAMILY,
                "claps_or_likes": "",
                "comments_count": "",
                "post_priority": post_priority(post_id),
                "source_completeness": completeness,
                "source_type": source.get("source_type", "substack_rss"),
                "source_url": source.get("source_ref", ""),
                "source_sha256": source.get("source_sha256", ""),
                "full_text": source.get("text", ""),
                "text_sha256": source.get("text_sha256", ""),
            }
        )

    candidates = sorted(
        [row for row in enriched if row["word_count"] >= 200 and row["full_text"]],
        key=lambda row: row["post_priority"],
    )
    selected_ids = {row["post_id"] for row in candidates[:3]}
    for row in enriched:
        row["selected_primary_post"] = row["post_id"] in selected_ids
        row["has_full_text"] = row["selected_primary_post"]

    posts_meta_fields = [
        "platform",
        "creator_id",
        "publication_id",
        "post_id",
        "url",
        "published_at",
        "year_month",
        "title",
        "subtitle",
        "byline",
        "post_type",
        "audience",
        "is_paywalled",
        "has_full_text",
        "word_count",
        "language",
        "topic_family",
        "claps_or_likes",
        "comments_count",
        "post_priority",
        "selected_primary_post",
        "source_completeness",
        "source_type",
        "source_url",
        "source_sha256",
    ]
    posts_meta_rows = [
        {field: row[field] for field in posts_meta_fields} for row in enriched
    ]

    posts_text_fields = [
        "platform",
        "post_id",
        "full_text",
        "word_count",
        "text_sha256",
        "source_completeness",
        "text_source",
        "source_url",
    ]
    posts_text_rows = [
        {
            "platform": row["platform"],
            "post_id": row["post_id"],
            "full_text": row["full_text"],
            "word_count": row["word_count"],
            "text_sha256": row["text_sha256"],
            "source_completeness": row["source_completeness"],
            "text_source": row["source_type"],
            "source_url": row["source_url"],
        }
        for row in enriched
        if row["selected_primary_post"]
    ]

    creator_priority = sha256_text(
        f"creator-priority-v1|{PLATFORM}|{CREATOR_ID}"
    )
    creators_rows = [
        {
            "platform": PLATFORM,
            "creator_id": CREATOR_ID,
            "creator_id_type": "substack_numeric_user_id",
            "identity_quality": "verified_stable",
            "handle_or_slug": "adamtooze",
            "display_name": CREATOR_NAME,
            "creator_type": "individual",
            "topic_family": TOPIC_FAMILY,
            "creator_priority": creator_priority,
            "profile_url": CREATOR_PROFILE_URL,
        }
    ]
    publications_rows = [
        {
            "platform": PLATFORM,
            "publication_id": PUBLICATION_ID,
            "publication_name": PUBLICATION_NAME,
            "publication_url": PUBLICATION_URL,
            "publication_type": "single_author_verified_pilot",
        }
    ]
    creator_publications_rows = [
        {
            "platform": PLATFORM,
            "creator_id": CREATOR_ID,
            "publication_id": PUBLICATION_ID,
            "relationship_role": "author_owner",
            "relationship_quality": "verified_for_pilot",
        }
    ]
    selected_words = [row["word_count"] for row in posts_text_rows]
    creator_month_rows = [
        {
            "sample_version": SAMPLE_VERSION,
            "platform": PLATFORM,
            "creator_id": CREATOR_ID,
            "year_month": month,
            "topic_family": TOPIC_FAMILY,
            "macro_domain": MACRO_DOMAIN,
            "sample_stratum": "permanent_random_pilot",
            "creator_priority": creator_priority,
            "eligible": True,
            "selected": True,
            "inclusion_weight": "",
            "frame_id": "chartbook-rss-smoke-2026-09-18",
            "metadata_post_count": len(posts_meta_rows),
            "candidate_text_count": len(candidates),
            "selected_text_count": len(posts_text_rows),
            "selected_word_count_total": sum(selected_words),
            "selected_word_count_mean": round(
                sum(selected_words) / len(selected_words), 1
            ),
            "first_post_at": enriched[0]["published_at"],
            "last_post_at": enriched[-1]["published_at"],
            "is_partial_month": True,
            "observed_days": 17,
        }
    ]
    coverage_rows = [
        {
            "sample_version": SAMPLE_VERSION,
            "platform": PLATFORM,
            "year_month": month,
            "topic_family": TOPIC_FAMILY,
            "frame_id": "chartbook-rss-smoke-2026-09-18",
            "sample_stratum": "permanent_random_pilot",
            "target_creators": 2500,
            "eligible_creators": 1,
            "sampled_creators": 1,
            "metadata_posts": len(posts_meta_rows),
            "candidate_texts_200_words": len(candidates),
            "selected_texts": len(posts_text_rows),
            "preview_posts": sum(
                row["source_completeness"] == "preview" for row in enriched
            ),
            "confirmed_full_posts": 0,
            "unverified_fullness_posts": sum(
                row["source_completeness"] == "unverified_fullness"
                for row in enriched
            ),
            "scrape_errors": 0,
            "is_partial_month": True,
            "coverage_note": (
                "One verified single-author RSS pilot; text length does not prove "
                "canonical full-text completeness"
            ),
        }
    ]

    lookup_outputs: list[
        tuple[str, list[str], list[dict[str, Any]], tuple[str, ...]]
    ] = [
        (
            "creators.csv",
            list(creators_rows[0]),
            creators_rows,
            ("platform", "creator_id"),
        ),
        (
            "publications.csv",
            list(publications_rows[0]),
            publications_rows,
            ("platform", "publication_id"),
        ),
        (
            "creator_publications.csv",
            list(creator_publications_rows[0]),
            creator_publications_rows,
            ("platform", "creator_id", "publication_id"),
        ),
    ]
    monthly_outputs: list[tuple[str, list[str], list[dict[str, Any]]]] = [
        ("posts_meta.csv", posts_meta_fields, posts_meta_rows),
        ("posts_text.csv", posts_text_fields, posts_text_rows),
        ("creator_month.csv", list(creator_month_rows[0]), creator_month_rows),
        ("coverage.csv", list(coverage_rows[0]), coverage_rows),
        (
            "scrape_errors.csv",
            [
                "platform",
                "creator_id",
                "post_id",
                "stage",
                "error_type",
                "error_message",
                "source_url",
                "observed_at",
            ],
            [],
        ),
    ]
    for filename, fields, rows, keys in lookup_outputs:
        upsert_csv(lookup_dir / filename, fields, rows, keys)
    for filename, fields, rows in monthly_outputs:
        write_csv(month_dir / filename, fields, rows)

    generated_monthly = [
        month_dir / filename for filename, _, _ in monthly_outputs
    ]
    generated_lookup = [
        lookup_dir / filename for filename, _, _, _ in lookup_outputs
    ]
    manifest = {
        "dataset_version": SAMPLE_VERSION,
        "schema_version": "monthly-csv-v2",
        "created_at": observed_at,
        "platform": PLATFORM,
        "month": month,
        "cutoff_date": cutoff_date,
        "source_file": str(input_path),
        "source_filename": input_path.name,
        "source_sha256": sha256_file(input_path),
        "source_method": "existing_verified_rss_smoke_test",
        "network_requests": 0,
        "parser_version": PARSER_VERSION,
        "sampling_fields_location": "creator_month.csv",
        "batch_metadata_location": "manifest.json",
        "counts": {
            "creators": len(creators_rows),
            "metadata_posts": len(posts_meta_rows),
            "selected_texts": len(posts_text_rows),
            "scrape_errors": 0,
        },
        "limitations": [
            "One convenience creator and publication; not representative.",
            "September 2026 is partial and includes September 1-17 only.",
            "RSS text of at least 200 words is marked unverified_fullness, not confirmed full.",
            "RSS cannot backfill the full historical study period.",
        ],
        "files": {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in generated_monthly
        },
        "lookup_files": {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in generated_lookup
        },
    }
    manifest_path = month_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    readme = f"""# Substack monthly pilot: {month}

This folder demonstrates the proposed monthly CSV structure using one verified
single-author publication. It was parsed from an existing RSS smoke-test file
and made no new network requests.

## Result

- Creator: {CREATOR_NAME} (`{CREATOR_ID}`)
- Publication: {PUBLICATION_NAME}
- Metadata posts: {len(posts_meta_rows)}
- Selected text posts: {len(posts_text_rows)}
- Cutoff: {cutoff_date}

`posts_meta.csv` contains all posts in the observed month. `posts_text.csv`
contains only the one-to-three qualifying posts selected by fixed post priority.
The two files join on `platform` and `post_id`. `toy_month_counts.csv` is not
generated because `coverage.csv` contains the relevant counts.

Stable identity tables are stored once in `data/monthly_pilot/lookup/`, not
repeated inside each monthly partition. The lookup writer upserts rows by stable
platform IDs so later months do not duplicate existing creators or publications.
`creator_month.csv` combines creator-level sampling fields with monthly outcomes;
batch parser and collection metadata are stored once in `manifest.json`.

## Important limitation

Text with at least 200 words is labeled `unverified_fullness`, not `full`,
because RSS length alone does not prove equivalence to the canonical post page.
This is a pipeline demonstration, not a representative platform sample.
"""
    (month_dir / "README.md").write_text(readme, encoding="utf-8")
    return month_dir


def main() -> int:
    args = parse_args()
    month_dir = build(args.input, args.month, args.cutoff_date, args.output_root)
    print(month_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
