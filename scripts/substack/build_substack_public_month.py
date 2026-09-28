#!/usr/bin/env python3
"""Build a complete observable Substack month from public sitemap and pages.

The sitemap supplies the candidate URL frame. Each candidate page is fetched
once to verify its actual publication timestamp and public metadata. Full text
is retained only for posts explicitly marked for everyone; paid-post bodies are
never retained. Requests are sequential and throttled.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_lib
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import build_substack_month_pilot as common


BASE_URL = "https://adamtooze.substack.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
CREATOR_ID = "2779232"
CREATOR_NAME = "Adam Tooze"
CREATOR_PROFILE_URL = "https://substack.com/@adamtooze"
PUBLICATION_ID = "substack:publication:192845"
PUBLICATION_NAME = "Chartbook"
TOPIC_FAMILY = "Business, finance and economics"
MACRO_DOMAIN = "Knowledge/professional"
PLATFORM = "substack"
PARSER_VERSION = "build_substack_public_month_v1"
USER_AGENT = "CapstoneResearchBot/0.1 (+academic one-month pilot)"
PRELOAD_RE = re.compile(
    r"window\._preloads\s*=\s*JSON\.parse\((\"(?:\\.|[^\"\\])*\")\)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="2026-08")
    parser.add_argument("--sitemap-url", default=SITEMAP_URL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--delay-seconds", type=float, default=0.75)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/monthly_pilot/substack"),
    )
    return parser.parse_args()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def sitemap_candidates(xml_bytes: bytes, month: str, base_url: str) -> list[str]:
    root = ET.fromstring(xml_bytes)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    rows: list[str] = []
    for node in root.findall("s:url", namespace):
        location = node.findtext("s:loc", default="", namespaces=namespace)
        lastmod = node.findtext("s:lastmod", default="", namespaces=namespace)
        if (
            lastmod.startswith(month)
            and location.startswith(f"{base_url}/p/")
            and location not in rows
        ):
            rows.append(location)
    return rows


def parse_preloads(page_bytes: bytes) -> dict[str, Any]:
    page = page_bytes.decode("utf-8", errors="replace")
    match = PRELOAD_RE.search(page)
    if not match:
        raise ValueError("window._preloads JSON not found")
    encoded_json = json.loads(match.group(1))
    return json.loads(encoded_json)


def visible_text(body_html: str) -> str:
    value = re.sub(r"(?is)<(?:script|style).*?>.*?</(?:script|style)>", " ", body_html)
    value = re.sub(
        r"(?i)</(?:p|div|h[1-6]|li|blockquote|figure|figcaption|section)>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n\n".join(line for line in lines if line)


def count_words(value: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", value, flags=re.UNICODE))


def build_page_record(url: str, page_bytes: bytes) -> dict[str, Any]:
    preloads = parse_preloads(page_bytes)
    post = preloads.get("post") or {}
    if not post:
        raise ValueError("post metadata not found in public page")

    audience = str(post.get("audience") or "unknown")
    is_public = audience == "everyone" and not post.get("free_unlock_required")
    body_text = visible_text(post.get("body_html") or "") if is_public else ""
    bylines = post.get("publishedBylines") or []
    byline_names = [str(item.get("name") or "").strip() for item in bylines]
    byline_names = [name for name in byline_names if name]
    byline_ids = [str(item.get("id")) for item in bylines if item.get("id")]
    completeness = "full" if body_text else (
        "preview" if post.get("truncated_body_text") else "metadata_only"
    )

    return {
        "platform": PLATFORM,
        "creator_id": CREATOR_ID,
        "publication_id": PUBLICATION_ID,
        "post_id": str(post.get("id") or url),
        "url": str(post.get("canonical_url") or url),
        "published_at": str(post.get("post_date") or ""),
        "year_month": str(post.get("post_date") or "")[:7],
        "title": str(post.get("title") or ""),
        "subtitle": str(post.get("subtitle") or ""),
        "byline": "; ".join(byline_names),
        "byline_creator_ids": ";".join(byline_ids),
        "post_type": str(post.get("type") or ""),
        "audience": audience,
        "is_paywalled": audience != "everyone",
        "has_full_text": bool(body_text),
        "text_retained": False,
        "word_count": post.get("wordcount") or "",
        "language": str(post.get("language") or "en"),
        "topic_family": TOPIC_FAMILY,
        "claps_or_likes": post.get("reaction_count") or 0,
        "comments_count": post.get("comment_count") or 0,
        "post_priority": common.post_priority(str(post.get("id") or url)),
        "selected_primary_post": False,
        "source_completeness": completeness,
        "source_type": "public_post_page",
        "source_url": url,
        "source_sha256": hashlib.sha256(page_bytes).hexdigest(),
        "full_text": body_text,
        "retained_text_word_count": count_words(body_text),
        "text_sha256": common.sha256_text(body_text) if body_text else "",
    }


def build(
    month: str,
    sitemap_url: str,
    base_url: str,
    delay_seconds: float,
    output_root: Path,
) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("--month must use YYYY-MM")
    if delay_seconds < 0:
        raise ValueError("--delay-seconds cannot be negative")

    observed_at = common.iso_now()
    sitemap_bytes = fetch(sitemap_url)
    candidates = sitemap_candidates(sitemap_bytes, month, base_url)
    if not candidates:
        raise ValueError(f"No sitemap candidates found for {month}")

    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, url in enumerate(candidates):
        if index:
            time.sleep(delay_seconds)
        try:
            page_bytes = fetch(url)
            record = build_page_record(url, page_bytes)
            if record["year_month"] == month:
                records.append(record)
            else:
                errors.append(
                    {
                        "platform": PLATFORM,
                        "creator_id": CREATOR_ID,
                        "post_id": record["post_id"],
                        "stage": "month_verification",
                        "error_type": "publication_month_mismatch",
                        "error_message": (
                            f"sitemap lastmod month {month}; actual publication "
                            f"month {record['year_month']}"
                        ),
                        "source_url": url,
                        "observed_at": observed_at,
                    }
                )
        except Exception as exc:  # bounded pilot continues and records every failure
            errors.append(
                {
                    "platform": PLATFORM,
                    "creator_id": CREATOR_ID,
                    "post_id": "",
                    "stage": "page_parse",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "source_url": url,
                    "observed_at": observed_at,
                }
            )

    records.sort(key=lambda row: (row["published_at"], row["post_id"]))
    if not records:
        raise ValueError(f"No page publication dates verified for {month}")

    qualifying = sorted(
        [
            row
            for row in records
            if row["source_completeness"] == "full"
            and row["retained_text_word_count"] >= 200
            and CREATOR_ID in row["byline_creator_ids"].split(";")
        ],
        key=lambda row: row["post_priority"],
    )
    selected_ids = {row["post_id"] for row in qualifying[:3]}
    for row in records:
        row["selected_primary_post"] = row["post_id"] in selected_ids
        row["text_retained"] = row["selected_primary_post"]

    year, month_number = month.split("-")
    month_dir = output_root / year / month_number
    lookup_dir = output_root.parent / "lookup"
    sample_version = f"pilot-substack-{month}-public-sitemap-v1"

    meta_fields = [
        "platform", "creator_id", "publication_id", "post_id", "url",
        "published_at", "year_month", "title", "subtitle", "byline",
        "byline_creator_ids", "post_type", "audience", "is_paywalled",
        "has_full_text", "text_retained", "word_count", "language",
        "topic_family", "claps_or_likes", "comments_count", "post_priority",
        "selected_primary_post", "source_completeness", "source_type",
        "source_url", "source_sha256",
    ]
    meta_rows = [{field: row[field] for field in meta_fields} for row in records]
    text_fields = [
        "platform", "post_id", "full_text", "word_count", "text_sha256",
        "source_completeness", "text_source", "source_url",
    ]
    text_rows = [
        {
            "platform": row["platform"],
            "post_id": row["post_id"],
            "full_text": row["full_text"],
            "word_count": row["retained_text_word_count"],
            "text_sha256": row["text_sha256"],
            "source_completeness": row["source_completeness"],
            "text_source": row["source_type"],
            "source_url": row["source_url"],
        }
        for row in records
        if row["selected_primary_post"]
    ]

    creator_priority = common.sha256_text(
        f"creator-priority-v1|{PLATFORM}|{CREATOR_ID}"
    )
    creators = [{
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
    }]
    publications = [{
        "platform": PLATFORM,
        "publication_id": PUBLICATION_ID,
        "publication_name": PUBLICATION_NAME,
        "publication_url": base_url,
        "publication_type": "single_author_verified_pilot",
    }]
    relationships = [{
        "platform": PLATFORM,
        "creator_id": CREATOR_ID,
        "publication_id": PUBLICATION_ID,
        "relationship_role": "author_owner",
        "relationship_quality": "verified_for_pilot",
    }]
    common.upsert_csv(
        lookup_dir / "creators.csv", list(creators[0]), creators,
        ("platform", "creator_id"),
    )
    common.upsert_csv(
        lookup_dir / "publications.csv", list(publications[0]), publications,
        ("platform", "publication_id"),
    )
    common.upsert_csv(
        lookup_dir / "creator_publications.csv", list(relationships[0]), relationships,
        ("platform", "creator_id", "publication_id"),
    )

    selected_words = [row["word_count"] for row in text_rows]
    creator_month = [{
        "sample_version": sample_version,
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
        "frame_id": f"chartbook-public-sitemap-{month}",
        "metadata_post_count": len(meta_rows),
        "public_full_text_available_count": len(qualifying),
        "selected_text_count": len(text_rows),
        "selected_word_count_total": sum(selected_words),
        "selected_word_count_mean": round(sum(selected_words) / len(selected_words), 1),
        "first_post_at": records[0]["published_at"],
        "last_post_at": records[-1]["published_at"],
        "is_partial_month": False,
        "observed_days": 31,
    }]
    coverage = [{
        "sample_version": sample_version,
        "platform": PLATFORM,
        "year_month": month,
        "topic_family": TOPIC_FAMILY,
        "frame_id": f"chartbook-public-sitemap-{month}",
        "sample_stratum": "permanent_random_pilot",
        "target_creators": 2500,
        "sitemap_candidates": len(candidates),
        "verified_month_posts": len(meta_rows),
        "eligible_creators": 1,
        "sampled_creators": 1,
        "metadata_posts": len(meta_rows),
        "public_full_text_posts": len(qualifying),
        "selected_texts": len(text_rows),
        "paywalled_posts": sum(row["is_paywalled"] for row in records),
        "preview_or_metadata_only_posts": sum(
            row["source_completeness"] != "full" for row in records
        ),
        "scrape_errors": len(errors),
        "is_partial_month": False,
        "coverage_note": (
            "All sitemap candidates with August lastmod were page-verified by "
            "actual post_date; deleted, unlisted, or non-indexed posts remain unobservable"
        ),
    }]

    monthly_outputs = [
        ("posts_meta.csv", meta_fields, meta_rows),
        ("posts_text.csv", text_fields, text_rows),
        ("creator_month.csv", list(creator_month[0]), creator_month),
        ("coverage.csv", list(coverage[0]), coverage),
        (
            "scrape_errors.csv",
            ["platform", "creator_id", "post_id", "stage", "error_type",
             "error_message", "source_url", "observed_at"],
            errors,
        ),
    ]
    for filename, fields, rows in monthly_outputs:
        common.write_csv(month_dir / filename, fields, rows)

    generated = [month_dir / name for name, _, _ in monthly_outputs]
    manifest = {
        "dataset_version": sample_version,
        "schema_version": "monthly-csv-v2",
        "created_at": observed_at,
        "platform": PLATFORM,
        "month": month,
        "source_method": "public_sitemap_plus_public_post_pages",
        "sitemap_url": sitemap_url,
        "sitemap_sha256": hashlib.sha256(sitemap_bytes).hexdigest(),
        "request_count": 1 + len(candidates),
        "delay_seconds": delay_seconds,
        "parser_version": PARSER_VERSION,
        "sampling_fields_location": "creator_month.csv",
        "batch_metadata_location": "manifest.json",
        "counts": {
            "sitemap_candidates": len(candidates),
            "verified_month_posts": len(meta_rows),
            "public_full_text_posts": len(qualifying),
            "selected_texts": len(text_rows),
            "paywalled_posts": sum(row["is_paywalled"] for row in records),
            "scrape_errors": len(errors),
        },
        "limitations": [
            "Observable-month census, not a platform-wide or creator-random sample.",
            "Deleted, unlisted, or non-indexed posts cannot be observed.",
            "Sitemap lastmod is only candidate discovery; page post_date verifies inclusion.",
            "Paid posts contribute metadata only; no paywalled body text is retained.",
            "Engagement counts are cumulative values observed at collection time.",
        ],
        "files": {
            path.name: {"sha256": common.sha256_file(path), "bytes": path.stat().st_size}
            for path in generated
        },
    }
    (month_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    readme = f"""# Substack complete observable-month pilot: {month}

This partition uses Chartbook's public sitemap to enumerate the month and each
public post page to verify the actual publication timestamp. It includes all
{len(meta_rows)} verified August posts found through that public frame, rather
than a fixed number of recent RSS entries.

## Result

- Sitemap candidates: {len(candidates)}
- Page-verified August posts: {len(meta_rows)}
- Public full-text posts passing 200 words: {len(qualifying)}
- Deterministically selected text posts: {len(text_rows)}
- Paywalled metadata-only/preview posts: {sum(row['is_paywalled'] for row in records)}
- Parse errors: {len(errors)}

`posts_meta.csv` contains every verified observable August post. `posts_text.csv`
contains at most three selected public/free posts. Paid posts contribute metadata
only. Stable creator and publication tables live once in `data/monthly_pilot/lookup/`.
`creator_month.csv` combines creator-level sampling fields with monthly outcomes;
batch parser and collection metadata are stored once in `manifest.json`.

## Completeness boundary

This is complete relative to the public sitemap frame at collection time. It
cannot recover deleted, unlisted, or non-indexed posts and is not evidence that
the sitemap is a census of every post ever published.
"""
    (month_dir / "README.md").write_text(readme, encoding="utf-8")
    return month_dir


def main() -> int:
    args = parse_args()
    month_dir = build(
        args.month,
        args.sitemap_url,
        args.base_url,
        args.delay_seconds,
        args.output_root,
    )
    print(month_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
