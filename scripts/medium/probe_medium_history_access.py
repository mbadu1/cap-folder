#!/usr/bin/env python3
"""Bounded Medium access check; replay cached evidence unless --live is supplied.

This is a feasibility probe, not a historical text collector. A live run makes
at most five sequential GETs and stops on any failed response or challenge.
It stores raw robots/XML responses but never stores the sampled story body.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
import feedparser
from bs4 import BeautifulSoup


VERSION = "medium-history-access-probe-v1"
USER_AGENT = (
    "DukeCapstoneMediumAccessPilot/0.1 "
    "(research; platform permission reported 2026-09-24)"
)
PERMISSION_BASIS = "user_reported_medium_permission"
ROOT_URL = "https://medium.com/sitemap/sitemap.xml"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locations(data: bytes) -> list[str]:
    root = ET.fromstring(data)
    return [
        element.text
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "loc" and element.text
    ]


def candidates(urls: list[str]) -> list[str]:
    return sorted(
        set(urls),
        key=lambda url: (digest(("medium-access-pilot-v1|" + url).encode()), url),
    )[:3]


def blocked(row: dict) -> bool:
    return (
        row["status"] in {401, 403, 429}
        or row.get("cf_mitigated") == "challenge"
        or row.get("title", "") == "Just a moment..."
    )


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def collect(cache: Path, delay: float) -> None:
    if (cache / "request_log.json").exists():
        raise ValueError("Use a new cache directory for a new live run; preserve prior evidence.")
    cache.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    log = []
    last_start = 0.0

    def fetch(url: str, filename: str | None = None) -> bytes | None:
        nonlocal last_start
        time.sleep(max(0.0, delay - (time.monotonic() - last_start)))
        last_start = time.monotonic()
        row = {"url": url, "observed_at": datetime.now(timezone.utc).isoformat()}
        try:
            # Redirects require separate review; do not silently change the host.
            response = session.get(url, timeout=30, allow_redirects=False)
            row.update(
                final_url=response.url,
                status=response.status_code,
                content_type=response.headers.get("Content-Type"),
                bytes=len(response.content),
                sha256=digest(response.content),
                cf_mitigated=response.headers.get("cf-mitigated"),
                retry_after=response.headers.get("Retry-After"),
            )
            if "html" in (row["content_type"] or ""):
                soup = BeautifulSoup(response.content, "html.parser")
                row["title"] = soup.title.get_text() if soup.title else None
        except requests.RequestException as error:
            row.update(status=0, error_type=type(error).__name__)
            log.append(row)
            write_json(cache / "request_log.json", log)
            return None
        log.append(row)
        write_json(cache / "request_log.json", log)
        if blocked(row) or row["status"] != 200:
            return None
        if filename:
            (cache / filename).write_bytes(response.content)
        return response.content

    if fetch("https://medium.com/robots.txt", "robots.txt") is None:
        return
    index = fetch(ROOT_URL, "sitemap_index.xml")
    if index is None:
        return
    index_urls = locations(index)
    selected = {}
    for kind in ["posts", "users"]:
        children = sorted(url for url in index_urls if f"/{kind}/2020/" in url)
        if not children:
            write_json(cache / "selection_error.json", {"missing_partition": kind})
            return
        body = fetch(children[0], f"{kind}_2020_child.xml")
        if body is None:
            return
        selected[kind] = candidates(locations(body))
        write_json(cache / "candidates.json", selected)
    if selected["posts"]:
        fetch(selected["posts"][0])


def report(cache: Path, output: Path) -> dict:
    log = json.loads((cache / "request_log.json").read_text())
    for filename in ["feed_request_log.json", "identity_request_log.json"]:
        if (cache / filename).exists():
            log.extend(json.loads((cache / filename).read_text()))
    selected_path = cache / "candidates.json"
    selected = json.loads(selected_path.read_text()) if selected_path.exists() else {}
    snapshot_files = {
        "robots": "robots.txt",
        "sitemap_index": "sitemap_index.xml",
        "posts_child": "posts_2020_child.xml",
        "users_child": "users_2020_child.xml",
    }
    snapshot_files.update({path.stem: path.name for path in sorted(cache.glob("profile_feed_*.xml"))})
    snapshots = {}
    for key, filename in snapshot_files.items():
        path = cache / filename
        if not path.exists():
            continue
        data = path.read_bytes()
        sha256 = digest(data)
        matches = [row for row in log if row.get("sha256") == sha256 and row["status"] == 200]
        if len(matches) != 1:
            raise ValueError(f"Snapshot cannot be reconciled to one successful request: {filename}")
        snapshots[key] = {
            "source_url": matches[0]["url"],
            "raw_cache_path": str(path.resolve()),
            "sha256": sha256,
            "bytes": len(data),
        }
        if filename.endswith(".xml") and not key.startswith("profile_feed_"):
            urls = locations(data)
            snapshots[key]["location_count"] = len(urls)
            if key.endswith("_child"):
                kind = "posts" if key == "posts_child" else "users"
                if selected.get(kind) != candidates(urls):
                    raise ValueError("Candidate selection does not reproduce from the frozen sitemap.")
    has_block = any(blocked(row) for row in log)
    sampled_story = (selected.get("posts") or [None])[0]
    story_row = next((row for row in log if row["url"] == sampled_story), None)
    status = "incomplete_access_probe"
    if has_block:
        status = "access_blocked"
    elif story_row and story_row["status"] == 200:
        status = "story_page_accessible_parser_unvalidated"
    result = {
        "probe_version": VERSION,
        "permission_basis": PERMISSION_BASIS,
        "permission_conditions_independently_reviewed": False,
        "status": status,
        "requests": len(log),
        "http_status_counts": dict(Counter(str(row["status"]) for row in log)),
        "study_start": "2020-01-01",
        "study_end": "2026-09-17",
        "full_text_records_collected": 0,
        "stable_creator_ids_verified": 0,
        "history_pagination_verified": False,
        "snapshots": snapshots,
        "selected_candidates": selected,
        "limitations": [
            "Sitemap partition dates are not verified story publication dates.",
            "A single sampled story does not estimate platform access or coverage rates.",
            "HTTP success alone does not verify text completeness, IDs, dates, or history traversal.",
            "No challenge bypass, alternate-IP retry, authentication, or paywall extraction was attempted.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    feed_rows, post_rows = [], []
    for path in sorted(cache.glob("profile_feed_*.xml")):
        parsed = feedparser.parse(path.read_bytes())
        profile = parsed.feed.get("link", "")
        source = parse_qs(urlsplit(profile).query).get("source", [""])[0]
        match = re.fullmatch(r"rss-([A-Za-z0-9]+)-+\d+", source)
        clean_profile = urlunsplit(urlsplit(profile)._replace(query="", fragment=""))
        candidate_id = match.group(1) if match else ""
        feed_rows.append({
            "profile_url": clean_profile,
            "candidate_author_id": candidate_id,
            "identity_quality": "provisional_profile",
            "id_evidence": "feed_tracking_parameter_unverified",
            "entry_count": len(parsed.entries),
            "history_coverage_status": "rss_only_unknown_completeness",
            "source_sha256": digest(path.read_bytes()),
        })
        for entry in parsed.entries:
            html = "\n".join(block.get("value", "") for block in entry.get("content", []))
            body = BeautifulSoup(html, "html.parser")
            for element in body(["script", "style"]):
                element.decompose()
            words = re.findall(r"\b[\w]+(?:['’\-][\w]+)*\b", body.get_text(" ", strip=True))
            guid = entry.get("id", "")
            post_id = guid.rsplit("/", 1)[-1] if guid.startswith("https://medium.com/p/") else ""
            post_rows.append({
                "platform": "medium",
                "post_id": post_id,
                "guid": guid,
                "story_url": urlunsplit(urlsplit(entry.get("link", ""))._replace(query="", fragment="")),
                "title": entry.get("title", ""),
                "author_display_name": entry.get("author", ""),
                "profile_url": clean_profile,
                "candidate_author_id": candidate_id,
                "published_at_source": entry.get("published", ""),
                "source_method": "official_profile_rss",
                "syndicated_word_count": len(words),
                "text_status": "unverified_fullness",
                "date_quality": "rss_reported_import_status_unverified",
                "selected_primary_post": False,
                "source_sha256": digest(path.read_bytes()),
            })
    for filename, rows in [("profile_feed_audit.csv", feed_rows), ("feed_posts_meta.csv", post_rows)]:
        if rows:
            with (output / filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    result.update(
        official_profile_feeds_checked=len(feed_rows),
        rss_story_metadata_rows=len(post_rows),
        rss_syndicated_word_counts=[row["syndicated_word_count"] for row in post_rows],
        identity_tracking_parameters_verified=False,
    )
    write_json(output / "request_log.json", log)
    write_json(output / "manifest.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Make a new bounded probe; default is offline replay.")
    parser.add_argument("--delay-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if args.delay_seconds < 3.0:
        parser.error("The initial Medium probe requires at least three seconds between request starts.")
    if args.live:
        collect(args.cache_dir, args.delay_seconds)
    result = report(args.cache_dir, args.output_dir)
    print(json.dumps({key: result[key] for key in ["status", "requests", "http_status_counts", "full_text_records_collected"]}))


if __name__ == "__main__":
    main()
