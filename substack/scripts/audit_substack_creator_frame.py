#!/usr/bin/env python3
"""Build creator registries and monthly coverage from cached Substack histories.

This stage is read-only with respect to the history checkpoint.  It converts
publication-centred collection records into creator-centred audit tables, using
numeric byline IDs for deduplication.  The outputs are diagnostics: automated
topic and organization labels remain provisional and no monthly partition is
made publishable by this script.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
import sys

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_substack_platform_month import (  # noqa: E402
    MACRO_DOMAINS,
    PLATFORM,
    TOPICS,
    allocate_sample,
    classify_topic,
    creator_type,
    sha256_file,
    sha256_text,
    write_csv,
)
from collect_substack_history import HISTORY_STAGE  # noqa: E402


AUDIT_VERSION = "substack-creator-frame-audit-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-month", default="2020-01")
    parser.add_argument("--end-month", default="2026-09")
    parser.add_argument("--frame-size", type=int, default=20000)
    parser.add_argument("--target-creators", type=int, default=2500)
    parser.add_argument("--topic-floor", type=int, default=150)
    parser.add_argument(
        "--history-cache-root",
        type=Path,
        default=Path(".cache/substack_history/history_v1"),
    )
    parser.add_argument(
        "--frame-file",
        type=Path,
        default=Path(
            "data/monthly_full/frame/"
            "substack_publications_full_2026-09-21.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/monthly_full/frame_audit"),
    )
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = (int(value) for value in start.split("-"))
    end_year, end_month = (int(value) for value in end.split("-"))
    current = date(start_year, start_month, 1)
    final = date(end_year, end_month, 1)
    output = []
    while current <= final:
        output.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return output


def clean_text(value: Any) -> str:
    return str(value or "").encode("utf-8", errors="replace").decode("utf-8")


def declared_english_or_unknown(post: dict[str, Any]) -> bool:
    language = clean_text(post.get("language")).strip().lower()
    return not language or language == "english" or language.startswith("en")


def qualifying_text(post: dict[str, Any]) -> bool:
    return bool(post.get("full_text")) and int(
        post.get("retained_word_count") or 0
    ) >= 200 and declared_english_or_unknown(post)


def most_common(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]


def update_date_bounds(
    bounds: dict[Any, list[str]], key: Any, published_at: str
) -> None:
    if not published_at:
        return
    if key not in bounds:
        bounds[key] = [published_at, published_at]
        return
    bounds[key][0] = min(bounds[key][0], published_at)
    bounds[key][1] = max(bounds[key][1], published_at)


def write_rows(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    write_csv(path, fieldnames, rows)


def build_audit(args: argparse.Namespace) -> dict[str, Any]:
    months = month_range(args.start_month, args.end_month)
    month_set = set(months)
    checkpoint_path = args.history_cache_root / "crawl.sqlite3"
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"missing history checkpoint: {checkpoint_path}")
    if not args.frame_file.is_file():
        raise FileNotFoundError(f"missing frozen frame: {args.frame_file}")
    with args.frame_file.open("r", encoding="utf-8", newline="") as handle:
        frame_rows = list(csv.DictReader(handle))
    frame_rows.sort(
        key=lambda row: (row["frame_priority"], row["publication_url"])
    )
    if args.frame_size > len(frame_rows):
        raise ValueError(
            f"requested frame {args.frame_size} exceeds frozen frame "
            f"of {len(frame_rows)} rows"
        )
    frame_urls = {
        row["publication_url"] for row in frame_rows[:args.frame_size]
    }

    creator_names: dict[str, Counter[str]] = defaultdict(Counter)
    creator_handles: dict[str, Counter[str]] = defaultdict(Counter)
    creator_post_ids: dict[str, set[str]] = defaultdict(set)
    creator_qualifying_post_ids: dict[str, set[str]] = defaultdict(set)
    creator_publication_urls: dict[str, set[str]] = defaultdict(set)
    creator_bounds: dict[str, list[str]] = {}
    creator_year_topics: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    creator_month_posts: dict[tuple[str, str], set[str]] = defaultdict(set)
    creator_month_qualifying: dict[tuple[str, str], set[str]] = defaultdict(set)

    publication_rows: dict[str, dict[str, Any]] = {}
    publication_post_ids: dict[str, set[str]] = defaultdict(set)
    publication_bounds: dict[str, list[str]] = {}
    relationship_post_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    relationship_roles: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    relationship_bounds: dict[tuple[str, str], list[str]] = {}

    connection = sqlite3.connect(str(checkpoint_path))
    checkpointed = 0
    successful = 0
    failed = 0

    cursor = connection.execute(
        """
        SELECT item_key, ok, payload, error_type, error_message
        FROM parsed WHERE stage = ? ORDER BY item_key
        """,
        (HISTORY_STAGE,),
    )
    retained_posts = 0
    for publication_url, ok, payload_text, error_type, error_message in cursor:
        publication_url = clean_text(publication_url)
        if publication_url not in frame_urls:
            continue
        checkpointed += 1
        if ok:
            successful += 1
        else:
            failed += 1
        fallback_id = f"substack:url:{sha256_text(publication_url)[:16]}"
        if not ok or not payload_text:
            publication_rows[publication_url] = {
                "platform": PLATFORM,
                "publication_id": fallback_id,
                "publication_name": "",
                "publication_url": publication_url,
                "publication_type": "",
                "language": "",
                "primary_creator_id": "",
                "collection_status": "failed",
                "history_post_count": 0,
                "first_post_at": "",
                "last_post_at": "",
                "error_type": clean_text(error_type),
                "error_message": clean_text(error_message),
            }
            continue

        payload = json.loads(payload_text)
        posts = payload.get("posts") or []
        retained_posts += len(posts)
        publication_metadata: dict[str, Any] = {}
        publication_id = ""
        for post in posts:
            if post.get("publication") and not publication_metadata:
                publication_metadata = post.get("publication") or {}
            if not publication_id and post.get("publication_id"):
                publication_id = clean_text(post.get("publication_id"))
        publication_id = publication_id or clean_text(
            publication_metadata.get("publication_id")
        ) or fallback_id
        publication_rows[publication_url] = {
            "platform": PLATFORM,
            "publication_id": publication_id,
            "publication_name": clean_text(
                publication_metadata.get("publication_name")
            ),
            "publication_url": publication_url,
            "publication_type": clean_text(
                publication_metadata.get("publication_type")
            ),
            "language": clean_text(publication_metadata.get("language")),
            "primary_creator_id": clean_text(
                publication_metadata.get("primary_creator_id")
            ),
            "collection_status": "successful",
            "history_post_count": 0,
            "first_post_at": "",
            "last_post_at": "",
            "error_type": "",
            "error_message": "",
        }

        seen_posts: set[str] = set()
        for post in posts:
            post_id = clean_text(post.get("post_id"))
            published_at = clean_text(post.get("published_at"))
            year_month = published_at[:7]
            if not post_id or year_month not in month_set:
                continue
            if post_id not in seen_posts:
                publication_post_ids[publication_url].add(post_id)
                update_date_bounds(publication_bounds, publication_url, published_at)
                seen_posts.add(post_id)
            topic_text = " ".join(
                [
                    clean_text(publication_metadata.get("publication_name")),
                    clean_text(publication_metadata.get("hero_text")),
                    clean_text(post.get("title")),
                    clean_text(post.get("subtitle")),
                ]
            )
            topic = classify_topic(topic_text)[0]
            is_qualifying = qualifying_text(post)
            for byline in post.get("bylines") or []:
                creator_id = clean_text(
                    byline.get("creator_id") or byline.get("id")
                )
                if not creator_id.isdigit():
                    continue
                if post_id in creator_post_ids[creator_id]:
                    continue
                creator_post_ids[creator_id].add(post_id)
                creator_names[creator_id][clean_text(byline.get("name"))] += 1
                creator_handles[creator_id][clean_text(byline.get("handle"))] += 1
                creator_publication_urls[creator_id].add(publication_url)
                update_date_bounds(creator_bounds, creator_id, published_at)
                creator_year_topics[(creator_id, year_month[:4])][topic] += 1
                creator_month_posts[(creator_id, year_month)].add(post_id)
                if is_qualifying:
                    creator_qualifying_post_ids[creator_id].add(post_id)
                    creator_month_qualifying[(creator_id, year_month)].add(post_id)
                relationship_key = (creator_id, publication_url)
                relationship_post_ids[relationship_key].add(post_id)
                role = clean_text(byline.get("publication_role")) or "post_byline"
                relationship_roles[relationship_key][role] += 1
                update_date_bounds(
                    relationship_bounds, relationship_key, published_at
                )

    connection.close()

    creator_rows: list[dict[str, Any]] = []
    creator_types: dict[str, str] = {}
    creator_priorities: dict[str, str] = {}
    for creator_id in sorted(creator_post_ids, key=lambda value: int(value)):
        name = most_common(creator_names[creator_id])
        handle = most_common(creator_handles[creator_id])
        type_value = creator_type(name)
        priority = sha256_text(f"creator-priority-v1|{PLATFORM}|{creator_id}")
        creator_types[creator_id] = type_value
        creator_priorities[creator_id] = priority
        bounds = creator_bounds.get(creator_id, ["", ""])
        creator_rows.append(
            {
                "platform": PLATFORM,
                "creator_id": creator_id,
                "creator_id_type": "substack_numeric_user_id",
                "identity_quality": "verified_stable",
                "handle_or_slug": handle,
                "display_name": name,
                "creator_type": type_value,
                "creator_priority": priority,
                "profile_url": f"https://substack.com/@{handle}" if handle else "",
                "observed_publication_count": len(
                    creator_publication_urls[creator_id]
                ),
                "history_post_count": len(creator_post_ids[creator_id]),
                "qualifying_text_post_count": len(
                    creator_qualifying_post_ids[creator_id]
                ),
                "first_post_at": bounds[0],
                "last_post_at": bounds[1],
            }
        )

    publication_output = []
    publication_id_by_url = {}
    for publication_url in sorted(publication_rows):
        row = publication_rows[publication_url]
        bounds = publication_bounds.get(publication_url, ["", ""])
        row["history_post_count"] = len(publication_post_ids[publication_url])
        row["first_post_at"] = bounds[0]
        row["last_post_at"] = bounds[1]
        publication_id_by_url[publication_url] = row["publication_id"]
        publication_output.append(row)

    relationship_rows = []
    for creator_id, publication_url in sorted(relationship_post_ids):
        bounds = relationship_bounds.get((creator_id, publication_url), ["", ""])
        relationship_rows.append(
            {
                "platform": PLATFORM,
                "creator_id": creator_id,
                "publication_id": publication_id_by_url.get(
                    publication_url,
                    f"substack:url:{sha256_text(publication_url)[:16]}",
                ),
                "publication_url": publication_url,
                "relationship_role": most_common(
                    relationship_roles[(creator_id, publication_url)]
                ),
                "relationship_quality": "verified_by_public_post",
                "observed_post_count": len(
                    relationship_post_ids[(creator_id, publication_url)]
                ),
                "first_observed_at": bounds[0],
                "last_observed_at": bounds[1],
            }
        )

    creator_year_topic_rows = []
    creator_year_topic: dict[tuple[str, str], str] = {}
    for creator_id, year in sorted(creator_year_topics):
        counts_by_topic = creator_year_topics[(creator_id, year)]
        topic = sorted(
            TOPICS,
            key=lambda value: (-counts_by_topic[value], TOPICS.index(value)),
        )[0]
        ranked = sorted(counts_by_topic.values(), reverse=True)
        first = ranked[0] if ranked else 0
        second = ranked[1] if len(ranked) > 1 else 0
        confidence = "high" if first >= 3 and first > second else (
            "medium" if first > second else "low"
        )
        creator_year_topic[(creator_id, year)] = topic
        creator_year_topic_rows.append(
            {
                "platform": PLATFORM,
                "creator_id": creator_id,
                "year": year,
                "topic_family": topic,
                "macro_domain": MACRO_DOMAINS[topic],
                "topic_method": "publication_description_title_keyword_v1_plurality",
                "topic_confidence": confidence,
                "topic_post_count": first,
                "total_topic_assigned_posts": sum(counts_by_topic.values()),
            }
        )

    eligibility_rows: list[dict[str, Any]] = []
    monthly_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    monthly_active_numeric: dict[str, set[str]] = defaultdict(set)
    monthly_active_individual: dict[str, set[str]] = defaultdict(set)
    eligibility_index: dict[tuple[str, str], dict[str, Any]] = {}
    for (creator_id, year_month), post_ids in sorted(creator_month_posts.items()):
        topic = creator_year_topic[(creator_id, year_month[:4])]
        qualifying_count = len(
            creator_month_qualifying.get((creator_id, year_month), set())
        )
        type_value = creator_types[creator_id]
        primary_text_eligible = (
            type_value == "individual_provisional" and qualifying_count > 0
        )
        row = {
            "platform": PLATFORM,
            "creator_id": creator_id,
            "year_month": year_month,
            "topic_family": topic,
            "macro_domain": MACRO_DOMAINS[topic],
            "creator_type": type_value,
            "creator_priority": creator_priorities[creator_id],
            "active_post_count": len(post_ids),
            "qualifying_text_post_count": qualifying_count,
            "primary_text_eligible": primary_text_eligible,
            "selected_in_current_frame": False,
            "language_rule": "declared_english_or_unknown",
        }
        eligibility_rows.append(row)
        eligibility_index[(creator_id, year_month)] = row
        monthly_active_numeric[year_month].add(creator_id)
        if type_value == "individual_provisional":
            monthly_active_individual[year_month].add(creator_id)
        if primary_text_eligible:
            monthly_candidates[year_month].append(
                {
                    "creator_id": creator_id,
                    "topic_family": topic,
                    "creator_priority": creator_priorities[creator_id],
                }
            )

    coverage_rows: list[dict[str, Any]] = []
    all_months_target_achieved = True
    all_months_topic_floors_achieved = True
    for year_month in months:
        candidates = monthly_candidates.get(year_month, [])
        selected_ids, allocation = allocate_sample(
            candidates, args.target_creators, args.topic_floor
        )
        for creator_id in selected_ids:
            eligibility_index[(creator_id, year_month)][
                "selected_in_current_frame"
            ] = True
        floor_shortfall = sum(
            allocation[topic]["shortfall"] for topic in TOPICS
        )
        selected_count = len(selected_ids)
        target_shortfall = max(0, args.target_creators - selected_count)
        if target_shortfall:
            all_months_target_achieved = False
        if floor_shortfall:
            all_months_topic_floors_achieved = False
        checkpoint_complete = checkpointed >= args.frame_size
        if not target_shortfall and not floor_shortfall:
            status = "target_achieved"
        elif checkpoint_complete:
            status = "frame_exhausted_census"
        else:
            status = "pilot_incomplete"
        coverage_rows.append(
            {
                "platform": PLATFORM,
                "year_month": year_month,
                "topic_family": "__all__",
                "checkpointed_publications": checkpointed,
                "successful_publications": successful,
                "failed_publications": failed,
                "active_numeric_creators": len(
                    monthly_active_numeric.get(year_month, set())
                ),
                "active_provisional_individuals": len(
                    monthly_active_individual.get(year_month, set())
                ),
                "primary_text_eligible_creators": len(candidates),
                "monthly_target_creators": args.target_creators,
                "allocated_sample_creators": selected_count,
                "creator_target_shortfall": target_shortfall,
                "topic_floor_target": args.topic_floor,
                "topic_floor_shortfall": floor_shortfall,
                "selected_text_assignment_min": selected_count,
                "selected_text_assignment_max": selected_count * 3,
                "frame_status": status,
            }
        )
        for topic in TOPICS:
            topic_active = {
                creator_id
                for creator_id in monthly_active_individual.get(year_month, set())
                if creator_year_topic.get((creator_id, year_month[:4])) == topic
            }
            audit = allocation[topic]
            coverage_rows.append(
                {
                    "platform": PLATFORM,
                    "year_month": year_month,
                    "topic_family": topic,
                    "checkpointed_publications": checkpointed,
                    "successful_publications": successful,
                    "failed_publications": failed,
                    "active_numeric_creators": "",
                    "active_provisional_individuals": len(topic_active),
                    "primary_text_eligible_creators": audit["eligible"],
                    "monthly_target_creators": audit["allocated"],
                    "allocated_sample_creators": audit["allocated"],
                    "creator_target_shortfall": "",
                    "topic_floor_target": args.topic_floor,
                    "topic_floor_shortfall": audit["shortfall"],
                    "selected_text_assignment_min": audit["allocated"],
                    "selected_text_assignment_max": audit["allocated"] * 3,
                    "frame_status": status,
                }
            )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "creators.csv": (
            [
                "platform", "creator_id", "creator_id_type", "identity_quality",
                "handle_or_slug", "display_name", "creator_type",
                "creator_priority", "profile_url", "observed_publication_count",
                "history_post_count", "qualifying_text_post_count",
                "first_post_at", "last_post_at",
            ],
            creator_rows,
        ),
        "publications.csv": (
            [
                "platform", "publication_id", "publication_name",
                "publication_url", "publication_type", "language",
                "primary_creator_id", "collection_status", "history_post_count",
                "first_post_at", "last_post_at", "error_type", "error_message",
            ],
            publication_output,
        ),
        "creator_publications.csv": (
            [
                "platform", "creator_id", "publication_id", "publication_url",
                "relationship_role", "relationship_quality", "observed_post_count",
                "first_observed_at", "last_observed_at",
            ],
            relationship_rows,
        ),
        "creator_year_topics.csv": (
            [
                "platform", "creator_id", "year", "topic_family",
                "macro_domain", "topic_method", "topic_confidence",
                "topic_post_count", "total_topic_assigned_posts",
            ],
            creator_year_topic_rows,
        ),
        "creator_month_eligibility.csv": (
            [
                "platform", "creator_id", "year_month", "topic_family",
                "macro_domain", "creator_type", "creator_priority",
                "active_post_count", "qualifying_text_post_count",
                "primary_text_eligible", "selected_in_current_frame",
                "language_rule",
            ],
            eligibility_rows,
        ),
        "monthly_coverage.csv": (
            [
                "platform", "year_month", "topic_family",
                "checkpointed_publications", "successful_publications",
                "failed_publications", "active_numeric_creators",
                "active_provisional_individuals",
                "primary_text_eligible_creators", "monthly_target_creators",
                "allocated_sample_creators", "creator_target_shortfall",
                "topic_floor_target", "topic_floor_shortfall",
                "selected_text_assignment_min", "selected_text_assignment_max",
                "frame_status",
            ],
            coverage_rows,
        ),
    }
    for name, (fieldnames, rows) in outputs.items():
        write_rows(output_dir / name, fieldnames, rows)

    manifest = {
        "audit_version": AUDIT_VERSION,
        "generated_at": iso_now(),
        "platform": PLATFORM,
        "study_start_month": args.start_month,
        "study_end_month": args.end_month,
        "frame_file": str(args.frame_file),
        "frame_csv_sha256": sha256_file(args.frame_file),
        "frame_size_target": args.frame_size,
        "checkpointed_publications": checkpointed,
        "successful_publications": successful,
        "failed_publications": failed,
        "retained_posts": retained_posts,
        "deduplicated_numeric_creators": len(creator_rows),
        "creator_publication_relationships": len(relationship_rows),
        "target_creators_per_month": args.target_creators,
        "topic_floor": args.topic_floor,
        "all_months_target_achieved": all_months_target_achieved,
        "all_months_topic_floors_achieved": all_months_topic_floors_achieved,
        "frame_exhausted": checkpointed >= args.frame_size,
        "ready_for_final_month_build": (
            (all_months_target_achieved and all_months_topic_floors_achieved)
            or checkpointed >= args.frame_size
        ),
        "language_rule": (
            "Text is provisionally eligible when declared English or language is "
            "unset; a versioned detector audit remains required before final use."
        ),
        "limitations": [
            "The source frame is a current publication snapshot, not a historical creator census.",
            "Creators with several publications can have higher first-stage discovery probability.",
            "Organization and topic labels are automated provisional classifications.",
            "Current-frame selection is diagnostic until creator-frame coverage and multiplicity are approved.",
        ],
        "files": {
            name: {
                "sha256": sha256_file(output_dir / name),
                "bytes": (output_dir / name).stat().st_size,
            }
            for name in outputs
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    args = parse_args()
    manifest = build_audit(args)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
