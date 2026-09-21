#!/usr/bin/env python3
"""Build one authorized, frame-sampled Substack platform-month partition.

The collector uses Substack's public XML publication indexes and public JSON
post records, with publication-sitemap/post-page fallback only when necessary.
It never authenticates or bypasses a paywall. Paid post bodies are not retained.
Parsed checkpoints are stored in SQLite so a long run can resume without
repeating completed requests.

This is a two-stage frame pilot: a permanent-random sample of publications is
screened first, then verified individual byline IDs are selected within the
observed frame by permanent creator priority and the topic allocation in
DATASET_DESIGN.md. Because publication multiplicity is not yet corrected, the
result must not receive platform-representative weights.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import html as html_lib
import json
import math
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


PLATFORM = "substack"
PARSER_VERSION = "build_substack_platform_month_v2"
SCHEMA_VERSION = "monthly-csv-v2"
USER_AGENT = (
    "Duke-Capstone-Research/0.1 "
    "(authorized academic collection; public pages only)"
)
GLOBAL_SITEMAPS = [
    "https://substack.com/sitemap-tt.xml.gz",
    "https://substack.com/sitemap-tt-1.xml.gz",
    "https://substack.com/sitemap-tt-2.xml.gz",
    "https://substack.com/sitemap-tt-3.xml.gz",
]
PRELOAD_RE = re.compile(
    r"window\._preloads\s*=\s*JSON\.parse\((\"(?:\\.|[^\"\\])*\")\)"
)
XML_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

TOPICS = [
    "Politics and current affairs",
    "Society, history and ideas",
    "Business, finance and economics",
    "Technology, science, education and environment",
    "Literature and creative writing",
    "Arts, culture, media and design",
    "Health, family and personal life",
    "Lifestyle, food, travel, home and leisure",
]
MACRO_DOMAINS = {
    TOPICS[0]: "Public affairs",
    TOPICS[1]: "Public affairs",
    TOPICS[2]: "Knowledge/professional",
    TOPICS[3]: "Knowledge/professional",
    TOPICS[4]: "Culture/creative",
    TOPICS[5]: "Culture/creative",
    TOPICS[6]: "Lifestyle/personal expertise",
    TOPICS[7]: "Lifestyle/personal expertise",
}
TOPIC_KEYWORDS = {
    TOPICS[0]: {
        "politics", "political", "election", "government", "policy", "law",
        "geopolitics", "democracy", "congress", "president", "war", "news",
        "international", "diplomacy", "campaign", "current affairs",
    },
    TOPICS[1]: {
        "history", "historical", "society", "social", "philosophy", "faith",
        "religion", "ideas", "culture war", "ethics", "public intellectual",
        "anthropology", "sociology", "civilization", "theology",
    },
    TOPICS[2]: {
        "business", "finance", "financial", "economics", "economy", "market",
        "investing", "investment", "stocks", "crypto", "management", "career",
        "startup", "entrepreneur", "money", "trade", "capital", "industry",
    },
    TOPICS[3]: {
        "technology", "tech", "science", "education", "climate", "environment",
        "software", "engineering", "artificial intelligence", "machine learning",
        "research", "energy", "space", "biology", "physics", "data", "school",
    },
    TOPICS[4]: {
        "literature", "fiction", "poetry", "poem", "novel", "books", "writing",
        "writer", "author", "creative writing", "short story", "memoir", "essay",
    },
    TOPICS[5]: {
        "art", "arts", "culture", "film", "movie", "television", "music",
        "media", "design", "comics", "humor", "photography", "theater",
        "fashion", "architecture", "criticism", "podcast",
    },
    TOPICS[6]: {
        "health", "wellness", "medicine", "medical", "parenting", "family",
        "relationships", "psychology", "mental health", "fitness", "nutrition",
        "personal development", "therapy", "children", "motherhood", "fatherhood",
    },
    TOPICS[7]: {
        "food", "cooking", "recipe", "travel", "home", "garden", "sports",
        "leisure", "hobby", "restaurant", "wine", "outdoors", "running",
        "cycling", "beauty", "style", "games", "craft", "local",
    },
}
ORGANIZATION_MARKERS = {
    " team", " staff", " newsroom", " editors", " editorial", " magazine",
    " institute", " foundation", " association", " university", " company",
    " llc", " inc", " collective", " network", " media", " press",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", default="2026-08")
    parser.add_argument("--frame-size", type=int, default=20000)
    parser.add_argument(
        "--frame-file",
        type=Path,
        help=(
            "Optional frozen publication-frame CSV. When provided, no live "
            "global sitemap request is made."
        ),
    )
    parser.add_argument("--target-creators", type=int, default=2500)
    parser.add_argument("--topic-floor", type=int, default=150)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=2.0,
        help="Minimum interval between request starts across all workers.",
    )
    parser.add_argument(
        "--max-rate-limit-events",
        type=int,
        default=3,
        help="Abort without caching remaining items after this many HTTP 429s in the window.",
    )
    parser.add_argument(
        "--rate-limit-window-seconds",
        type=int,
        default=600,
        help="Rolling window used by the HTTP 429 circuit breaker.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/monthly_full/substack"),
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".cache/substack_platform_month"),
    )
    parser.add_argument(
        "--permission-basis",
        default="user_reported_written_substack_permission",
    )
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit(
        (parsed.scheme or "https", parsed.netloc.lower(), "", "", "")
    ).rstrip("/")


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


def parse_preloads(page_bytes: bytes) -> dict[str, Any]:
    page = page_bytes.decode("utf-8", errors="replace")
    match = PRELOAD_RE.search(page)
    if not match:
        raise ValueError("window._preloads JSON not found")
    return json.loads(json.loads(match.group(1)))


class GlobalThrottle:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.lock = threading.Lock()
        self.next_start = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_start - now)
            if delay:
                time.sleep(delay)
            self.next_start = time.monotonic() + self.interval

    def pause_for(self, seconds: float) -> None:
        """Pause request starts globally, including work in other threads."""
        with self.lock:
            self.next_start = max(self.next_start, time.monotonic() + seconds)


class CrawlCircuitOpen(RuntimeError):
    """Raised when platform rate-limit signals require the crawl to stop."""


class Collector:
    def __init__(
        self,
        delay_seconds: float,
        max_rate_limit_events: int,
        rate_limit_window_seconds: int,
    ) -> None:
        self.throttle = GlobalThrottle(delay_seconds)
        self.count_lock = threading.Lock()
        self.request_count = 0
        self.http_status_counts: Counter[str] = Counter()
        self.state_lock = threading.Lock()
        self.rate_limit_events: list[float] = []
        self.max_rate_limit_events = max_rate_limit_events
        self.rate_limit_window_seconds = rate_limit_window_seconds
        self.circuit_reason = ""

    def _check_circuit(self) -> None:
        with self.state_lock:
            reason = self.circuit_reason
        if reason:
            raise CrawlCircuitOpen(reason)

    @staticmethod
    def _retry_after_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
        value = exc.headers.get("Retry-After", "").strip()
        if value:
            try:
                return max(1.0, float(value))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return max(
                        1.0,
                        (parsed - datetime.now(timezone.utc)).total_seconds(),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(600.0, 60.0 * (2 ** attempt))

    def _record_rate_limit(self, wait_seconds: float) -> None:
        now = time.monotonic()
        self.throttle.pause_for(wait_seconds)
        with self.state_lock:
            cutoff = now - self.rate_limit_window_seconds
            self.rate_limit_events = [
                value for value in self.rate_limit_events if value >= cutoff
            ]
            self.rate_limit_events.append(now)
            if len(self.rate_limit_events) >= self.max_rate_limit_events:
                self.circuit_reason = (
                    "rate-limit circuit opened after "
                    f"{len(self.rate_limit_events)} HTTP 429 responses within "
                    f"{self.rate_limit_window_seconds} seconds; rerun later to resume"
                )
                raise CrawlCircuitOpen(self.circuit_reason)

    def fetch(self, url: str) -> bytes:
        last_error: Optional[Exception] = None
        for attempt in range(4):
            self._check_circuit()
            self.throttle.wait()
            with self.count_lock:
                self.request_count += 1
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xml;q=0.9,*/*;q=0.5",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = response.read()
                    with self.count_lock:
                        self.http_status_counts[str(response.status)] += 1
                    return body
            except urllib.error.HTTPError as exc:
                last_error = exc
                with self.count_lock:
                    self.http_status_counts[str(exc.code)] += 1
                if exc.code == 429:
                    self._record_rate_limit(
                        self._retry_after_seconds(exc, attempt)
                    )
                    continue
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if isinstance(
                    getattr(exc, "reason", None),
                    socket.gaierror,
                ):
                    break
            if attempt < 3:
                self.throttle.pause_for((2, 5, 10)[attempt])
        if last_error:
            raise last_error
        raise RuntimeError(f"request failed without exception: {url}")


class Checkpoint:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS parsed (
                stage TEXT NOT NULL,
                item_key TEXT NOT NULL,
                ok INTEGER NOT NULL,
                payload TEXT,
                error_type TEXT,
                error_message TEXT,
                observed_at TEXT NOT NULL,
                PRIMARY KEY (stage, item_key)
            )
            """
        )
        self.connection.commit()

    def keys(self, stage: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT item_key FROM parsed WHERE stage = ?", (stage,)
        )
        return {str(row[0]) for row in rows}

    def put(
        self,
        stage: str,
        item_key: str,
        ok: bool,
        payload: Optional[dict[str, Any]],
        error: Optional[Exception],
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO parsed
            (stage, item_key, ok, payload, error_type, error_message, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stage,
                item_key,
                int(ok),
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                type(error).__name__ if error else None,
                str(error)[:2000] if error else None,
                iso_now(),
            ),
        )
        self.connection.commit()

    def rows(self, stage: str) -> list[dict[str, Any]]:
        values = self.connection.execute(
            """
            SELECT item_key, ok, payload, error_type, error_message, observed_at
            FROM parsed WHERE stage = ? ORDER BY item_key
            """,
            (stage,),
        )
        output = []
        for key, ok, payload, error_type, error_message, observed_at in values:
            output.append(
                {
                    "item_key": key,
                    "ok": bool(ok),
                    "payload": json.loads(payload) if payload else None,
                    "error_type": error_type or "",
                    "error_message": error_message or "",
                    "observed_at": observed_at,
                }
            )
        return output

    def close(self) -> None:
        self.connection.close()


def run_cached_stage(
    checkpoint: Checkpoint,
    stage: str,
    items: list[tuple[str, Any]],
    function: Callable[[Any], dict[str, Any]],
    max_workers: int,
) -> None:
    completed = checkpoint.keys(stage)
    pending = [(key, item) for key, item in items if key not in completed]
    if not pending:
        print(f"{stage}: all {len(items)} items already cached", flush=True)
        return
    print(
        f"{stage}: {len(pending)} pending of {len(items)} total",
        flush=True,
    )
    finished = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(function, item): key for key, item in pending}
        for future in as_completed(futures):
            key = futures[future]
            try:
                payload = future.result()
                checkpoint.put(stage, key, True, payload, None)
            except CrawlCircuitOpen:
                for pending_future in futures:
                    pending_future.cancel()
                raise
            except Exception as exc:
                checkpoint.put(stage, key, False, None, exc)
            finished += 1
            if finished % 100 == 0 or finished == len(pending):
                print(
                    f"{stage}: completed {finished}/{len(pending)}",
                    flush=True,
                )


def load_publication_frame(
    collector: Collector,
    month: str,
    frame_size: int,
) -> tuple[list[dict[str, str]], int, dict[str, str]]:
    month_start = f"{month}-01"
    indexed: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    for url in GLOBAL_SITEMAPS:
        xml_bytes = collector.fetch(url)
        source_hashes[url] = hashlib.sha256(xml_bytes).hexdigest()
        root = ET.fromstring(xml_bytes)
        for node in root.findall("s:url", XML_NS):
            location = node.findtext("s:loc", default="", namespaces=XML_NS)
            lastmod = node.findtext("s:lastmod", default="", namespaces=XML_NS)
            if location and lastmod >= month_start:
                indexed[canonical_base_url(location)] = lastmod
    rows = [
        {
            "publication_url": url,
            "frame_lastmod": lastmod,
            "frame_priority": sha256_text(
                f"publication-frame-v1|{PLATFORM}|{url}"
            ),
        }
        for url, lastmod in indexed.items()
    ]
    rows.sort(key=lambda row: (row["frame_priority"], row["publication_url"]))
    return rows[: min(frame_size, len(rows))], len(rows), source_hashes


def load_frozen_publication_frame(
    frame_path: Path,
    frame_size: int,
) -> tuple[list[dict[str, str]], int, dict[str, str]]:
    with frame_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"publication_url", "frame_lastmod", "frame_priority"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(
            f"frozen frame must contain {sorted(required)}: {frame_path}"
        )
    rows.sort(key=lambda row: (row["frame_priority"], row["publication_url"]))
    if frame_size > len(rows):
        raise ValueError(
            f"requested frame {frame_size} exceeds frozen frame {len(rows)}"
        )
    manifest_path = frame_path.with_suffix(".manifest.json")
    source_hashes: dict[str, str] = {}
    candidate_pool_size = len(rows)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_pool_size = int(
            manifest.get("candidate_publications") or candidate_pool_size
        )
        source_hashes = {
            str(key): str(value)
            for key, value in (
                manifest.get("global_sitemap_sha256") or {}
            ).items()
        }
    return rows[:frame_size], candidate_pool_size, source_hashes


def parse_publication_sitemap(
    collector: Collector, month: str, publication_url: str
) -> dict[str, Any]:
    sitemap_url = f"{publication_url}/sitemap.xml"
    xml_bytes = collector.fetch(sitemap_url)
    root = ET.fromstring(xml_bytes)
    candidates = []
    for node in root.findall("s:url", XML_NS):
        location = node.findtext("s:loc", default="", namespaces=XML_NS)
        lastmod = node.findtext("s:lastmod", default="", namespaces=XML_NS)
        if lastmod.startswith(month) and "/p/" in location:
            candidates.append({"url": location, "sitemap_lastmod": lastmod})
    unique = {row["url"]: row for row in candidates}
    return {
        "publication_url": publication_url,
        "sitemap_url": sitemap_url,
        "sitemap_sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "candidates": [unique[url] for url in sorted(unique)],
    }


def parse_homepage(collector: Collector, publication_url: str) -> dict[str, Any]:
    page_bytes = collector.fetch(publication_url)
    pub = parse_preloads(page_bytes).get("pub") or {}
    if not pub:
        raise ValueError("publication metadata not found")
    contributors = []
    for item in pub.get("contributors") or []:
        user_id = item.get("user_id")
        if user_id is None:
            continue
        contributors.append(
            {
                "creator_id": str(user_id),
                "name": str(item.get("name") or ""),
                "handle": str(item.get("handle") or ""),
                "role": str(item.get("role") or ""),
                "owner": bool(item.get("owner")),
                "bio": str(item.get("bio") or ""),
            }
        )
    return {
        "publication_url": publication_url,
        "publication_id": str(pub.get("id") or ""),
        "publication_name": str(pub.get("name") or ""),
        "publication_type": str(pub.get("type") or ""),
        "language": str(pub.get("language") or ""),
        "hero_text": str(pub.get("hero_text") or ""),
        "primary_creator_id": str(
            pub.get("primary_user_id") or pub.get("author_id") or ""
        ),
        "author_name": str(pub.get("author_name") or ""),
        "author_handle": str(pub.get("author_handle") or ""),
        "first_post_date": str(pub.get("first_post_date") or ""),
        "contributors": contributors,
        "source_sha256": hashlib.sha256(page_bytes).hexdigest(),
    }


def parse_post_page(
    collector: Collector,
    month: str,
    item: dict[str, str],
) -> dict[str, Any]:
    url = item["url"]
    page_bytes = collector.fetch(url)
    post = parse_preloads(page_bytes).get("post") or {}
    if not post:
        raise ValueError("post metadata not found")
    published_at = str(post.get("post_date") or "")
    bylines = []
    for byline in post.get("publishedBylines") or []:
        creator_id = byline.get("id") or byline.get("user_id")
        if creator_id is None:
            continue
        bylines.append(
            {
                "creator_id": str(creator_id),
                "name": str(byline.get("name") or ""),
                "handle": str(byline.get("handle") or ""),
            }
        )
    audience = str(post.get("audience") or "unknown")
    is_public = audience == "everyone" and not post.get("free_unlock_required")
    full_text = visible_text(str(post.get("body_html") or "")) if is_public else ""
    post_id = str(post.get("id") or url)
    return {
        "candidate_url": url,
        "source_url": url,
        "source_type": "public_post_page",
        "canonical_url": str(post.get("canonical_url") or url),
        "sitemap_lastmod": item.get("sitemap_lastmod", ""),
        "post_id": post_id,
        "publication_id": str(post.get("publication_id") or ""),
        "published_at": published_at,
        "year_month": published_at[:7],
        "month_verified": published_at.startswith(month),
        "title": str(post.get("title") or ""),
        "subtitle": str(post.get("subtitle") or ""),
        "post_type": str(post.get("type") or ""),
        "audience": audience,
        "is_paywalled": audience != "everyone",
        "language": str(post.get("language") or ""),
        "platform_word_count": post.get("wordcount") or "",
        "reaction_count": post.get("reaction_count"),
        "comment_count": post.get("comment_count"),
        "bylines": bylines,
        "full_text": full_text,
        "retained_word_count": count_words(full_text),
        "text_sha256": sha256_text(full_text) if full_text else "",
        "source_sha256": hashlib.sha256(page_bytes).hexdigest(),
    }


def post_from_api_record(
    month: str,
    source_url: str,
    post: dict[str, Any],
    sitemap_lastmods: dict[str, str],
) -> dict[str, Any]:
    published_at = str(post.get("post_date") or "")
    bylines = []
    publication: dict[str, Any] = {}
    post_publication_id = str(post.get("publication_id") or "")
    for byline in post.get("publishedBylines") or []:
        creator_id = byline.get("id") or byline.get("user_id")
        if creator_id is None:
            continue
        publication_role = ""
        for membership in byline.get("publicationUsers") or []:
            member_publication = membership.get("publication") or {}
            if str(member_publication.get("id") or "") != post_publication_id:
                continue
            publication_role = str(membership.get("role") or "")
            if not publication:
                publication = {
                    "publication_id": post_publication_id,
                    "publication_name": str(member_publication.get("name") or ""),
                    "publication_type": (
                        "personal" if member_publication.get("is_personal_mode")
                        else "publication"
                    ),
                    "language": str(member_publication.get("language") or ""),
                    "hero_text": str(member_publication.get("hero_text") or ""),
                    "primary_creator_id": str(
                        member_publication.get("primary_user_id")
                        or member_publication.get("author_id")
                        or ""
                    ),
                }
            break
        bylines.append(
            {
                "creator_id": str(creator_id),
                "name": str(byline.get("name") or ""),
                "handle": str(byline.get("handle") or ""),
                "publication_role": publication_role,
            }
        )
    audience = str(post.get("audience") or "unknown")
    is_public = audience == "everyone" and not post.get("free_unlock_required")
    full_text = visible_text(str(post.get("body_html") or "")) if is_public else ""
    post_id = str(post.get("id") or post.get("canonical_url") or post.get("slug") or "")
    canonical_url = str(post.get("canonical_url") or "")
    if not canonical_url:
        parsed = urllib.parse.urlsplit(source_url)
        canonical_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, f"/p/{post.get('slug') or post_id}", "", "")
        )
    canonical_key = canonical_url.split("?", 1)[0].rstrip("/")
    serialized = json.dumps(post, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "candidate_url": canonical_url,
        "source_url": source_url,
        "source_type": "public_posts_json_endpoint",
        "canonical_url": canonical_url,
        "sitemap_lastmod": sitemap_lastmods.get(canonical_key, ""),
        "post_id": post_id,
        "publication_id": post_publication_id,
        "publication": publication,
        "published_at": published_at,
        "year_month": published_at[:7],
        "month_verified": published_at.startswith(month),
        "title": str(post.get("title") or ""),
        "subtitle": str(post.get("subtitle") or ""),
        "post_type": str(post.get("type") or ""),
        "audience": audience,
        "is_paywalled": audience != "everyone",
        "language": str(post.get("language") or ""),
        "platform_word_count": post.get("wordcount") or "",
        "reaction_count": post.get("reaction_count"),
        "comment_count": post.get("comment_count"),
        "bylines": bylines,
        "full_text": full_text,
        "retained_word_count": count_words(full_text),
        "text_sha256": sha256_text(full_text) if full_text else "",
        "source_sha256": hashlib.sha256(serialized).hexdigest(),
    }


def parse_publication_posts_api(
    collector: Collector,
    month: str,
    publication: dict[str, Any],
) -> dict[str, Any]:
    publication_url = publication["publication_url"]
    month_start = f"{month}-01"
    year, month_number = (int(value) for value in month.split("-"))
    next_year = year + (1 if month_number == 12 else 0)
    next_month = 1 if month_number == 12 else month_number + 1
    month_end_exclusive = f"{next_year:04d}-{next_month:02d}-01"
    sitemap_lastmods = {
        row["url"].split("?", 1)[0].rstrip("/"): row.get("sitemap_lastmod", "")
        for row in publication.get("candidates") or []
    }
    posts: list[dict[str, Any]] = []
    offset = 0
    batch_size = 50
    max_batches = 20
    api_failed: Optional[Exception] = None
    for _ in range(max_batches):
        source_url = (
            f"{publication_url}/api/v1/posts?limit={batch_size}"
            f"&offset={offset}&sort=new"
        )
        try:
            payload = json.loads(collector.fetch(source_url))
        except Exception as exc:
            api_failed = exc
            break
        if not isinstance(payload, list):
            api_failed = ValueError("posts endpoint did not return a list")
            break
        if not payload:
            break
        batch_dates = []
        for raw_post in payload:
            if not isinstance(raw_post, dict):
                continue
            published_at = str(raw_post.get("post_date") or "")
            if published_at:
                batch_dates.append(published_at[:10])
            if month_start <= published_at[:10] < month_end_exclusive:
                posts.append(
                    post_from_api_record(
                        month, source_url, raw_post, sitemap_lastmods
                    )
                )
        if len(payload) < batch_size:
            break
        if batch_dates and min(batch_dates) < month_start:
            break
        offset += len(payload)

    if api_failed is not None and not posts:
        fallback_publication = publication
        if not fallback_publication.get("candidates"):
            fallback_publication = parse_publication_sitemap(
                collector, month, publication_url
            )
        for candidate in fallback_publication.get("candidates") or []:
            posts.append(parse_post_page(collector, month, candidate))
        method = "public_sitemap_and_post_page_fallback"
    else:
        method = "public_posts_json_endpoint"
    unique = {post["post_id"]: post for post in posts if post["month_verified"]}
    return {
        "publication_url": publication_url,
        "method": method,
        "api_error": str(api_failed)[:1000] if api_failed else "",
        "posts": [
            unique[key]
            for key in sorted(
                unique,
                key=lambda post_id: (
                    unique[post_id]["published_at"],
                    post_id,
                ),
            )
        ],
    }


def classify_topic(text: str) -> tuple[str, str, str]:
    normalized = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    scores: dict[str, int] = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            needle = " " + re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip() + " "
            if needle in normalized:
                score += 2 if " " in keyword else 1
        scores[topic] = score
    ordered = sorted(TOPICS, key=lambda topic: (-scores[topic], TOPICS.index(topic)))
    topic = ordered[0]
    best = scores[topic]
    second = scores[ordered[1]]
    if best == 0:
        topic = TOPICS[1]
        confidence = "low"
    elif best >= 4 and best - second >= 2:
        confidence = "high"
    elif best >= 2:
        confidence = "medium"
    else:
        confidence = "low"
    return topic, "publication_description_title_keyword_v1", confidence


def creator_type(name: str) -> str:
    normalized = " " + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if any(marker in normalized for marker in ORGANIZATION_MARKERS):
        return "organization_provisional"
    return "individual_provisional"


def allocate_sample(
    creators: list[dict[str, Any]], target: int, topic_floor: int
) -> tuple[set[str], dict[str, dict[str, int]]]:
    grouped: dict[str, list[dict[str, Any]]] = {topic: [] for topic in TOPICS}
    for creator in creators:
        grouped[creator["topic_family"]].append(creator)
    for topic in TOPICS:
        grouped[topic].sort(
            key=lambda row: (row["creator_priority"], row["creator_id"])
        )

    target = min(target, len(creators))
    allocations = {
        topic: min(topic_floor, len(grouped[topic])) for topic in TOPICS
    }
    remaining = target - sum(allocations.values())
    capacities = {
        topic: len(grouped[topic]) - allocations[topic] for topic in TOPICS
    }
    while remaining > 0 and sum(capacities.values()) > 0:
        capacity_total = sum(capacities.values())
        quotas = {
            topic: remaining * capacities[topic] / capacity_total for topic in TOPICS
        }
        additions = {
            topic: min(capacities[topic], int(math.floor(quotas[topic])))
            for topic in TOPICS
        }
        assigned = sum(additions.values())
        leftovers = remaining - assigned
        order = sorted(
            TOPICS,
            key=lambda topic: (
                -(quotas[topic] - math.floor(quotas[topic])),
                TOPICS.index(topic),
            ),
        )
        for topic in order:
            if leftovers <= 0:
                break
            if capacities[topic] > additions[topic]:
                additions[topic] += 1
                leftovers -= 1
        progress = sum(additions.values())
        if progress == 0:
            break
        for topic in TOPICS:
            allocations[topic] += additions[topic]
            capacities[topic] -= additions[topic]
        remaining -= progress

    selected: set[str] = set()
    audit: dict[str, dict[str, int]] = {}
    for topic in TOPICS:
        rows = grouped[topic]
        count = allocations[topic]
        selected.update(row["creator_id"] for row in rows[:count])
        audit[topic] = {
            "eligible": len(rows),
            "allocated": count,
            "floor_target": topic_floor,
            "shortfall": max(0, topic_floor - len(rows)),
        }
    return selected, audit


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
    indexed: dict[tuple[str, ...], dict[str, Any]] = {}
    if path.is_file():
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                indexed[tuple(row[field] for field in key_fields)] = row
    for row in rows:
        indexed[tuple(str(row[field]) for field in key_fields)] = row
    write_csv(path, fieldnames, [indexed[key] for key in sorted(indexed)])


def build(args: argparse.Namespace) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        raise ValueError("--month must use YYYY-MM")
    if args.frame_size <= 0 or args.target_creators <= 0:
        raise ValueError("frame and target sizes must be positive")
    if args.topic_floor < 0 or args.max_workers <= 0 or args.delay_seconds < 0:
        raise ValueError("invalid floor, worker, or delay setting")
    if args.max_rate_limit_events <= 0 or args.rate_limit_window_seconds <= 0:
        raise ValueError("rate-limit circuit settings must be positive")

    started_at = iso_now()
    month_year, month_number_value = (
        int(value) for value in args.month.split("-")
    )
    calendar_days = calendar.monthrange(month_year, month_number_value)[1]
    is_partial_month = args.month == "2026-09"
    observed_days = 17 if is_partial_month else calendar_days
    collector = Collector(
        args.delay_seconds,
        args.max_rate_limit_events,
        args.rate_limit_window_seconds,
    )
    # A frame-size-independent checkpoint lets a conservative pilot expand the
    # deterministic frame without repeating the already screened prefix.
    cache_dir = args.cache_root / args.month / "api_first_v2"
    checkpoint = Checkpoint(cache_dir / "crawl.sqlite3")
    if args.frame_file:
        frame_rows, candidate_pool_size, global_hashes = (
            load_frozen_publication_frame(args.frame_file, args.frame_size)
        )
    else:
        frame_rows, candidate_pool_size, global_hashes = load_publication_frame(
            collector, args.month, args.frame_size
        )
    frame_id = f"substack-publication-frame-v1-{args.month}-n{len(frame_rows)}"
    print(
        f"frame: {len(frame_rows)} sampled from {candidate_pool_size} candidates",
        flush=True,
    )

    post_items = [
        (
            publication["publication_url"],
            {
                "publication_url": publication["publication_url"],
                "candidates": [],
            },
        )
        for publication in frame_rows
    ]
    post_stage = "publication_posts_api_v2"
    run_cached_stage(
        checkpoint,
        post_stage,
        post_items,
        lambda item: parse_publication_posts_api(collector, args.month, item),
        args.max_workers,
    )
    frame_urls = {row["publication_url"] for row in frame_rows}
    post_cache = [
        row for row in checkpoint.rows(post_stage)
        if row["item_key"] in frame_urls
    ]
    active_publications = [
        row["payload"]
        for row in post_cache
        if row["ok"] and row["payload"] and row["payload"]["posts"]
    ]
    print(
        f"activity screen: {len(active_publications)} publications have verified {args.month} posts",
        flush=True,
    )
    verified_posts = []
    for row in post_cache:
        if not row["ok"] or not row["payload"]:
            continue
        publication_url = row["payload"]["publication_url"]
        for post in row["payload"]["posts"]:
            post["publication_url"] = publication_url
            verified_posts.append(post)
    post_by_id = {post["post_id"]: post for post in verified_posts}
    verified_posts = sorted(
        post_by_id.values(), key=lambda row: (row["published_at"], row["post_id"])
    )
    print(
        f"date verification: {len(verified_posts)} unique {args.month} posts",
        flush=True,
    )

    posts_by_publication: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for post in verified_posts:
        posts_by_publication[post["publication_url"]].append(post)

    publication_metadata: dict[str, dict[str, Any]] = {}
    publication_topics: dict[str, tuple[str, str, str]] = {}
    for publication_url, posts in posts_by_publication.items():
        metadata = next(
            (
                post.get("publication") or {}
                for post in posts
                if post.get("publication")
            ),
            {},
        )
        publication_metadata[publication_url] = metadata
        text = " ".join(
            [
                str(metadata.get("publication_name") or ""),
                str(metadata.get("hero_text") or ""),
                " ".join(str(post.get("title") or "") for post in posts),
                " ".join(str(post.get("subtitle") or "") for post in posts),
            ]
        )
        publication_topics[publication_url] = classify_topic(text)

    creator_posts: dict[str, set[str]] = defaultdict(set)
    creator_names: dict[str, Counter[str]] = defaultdict(Counter)
    creator_handles: dict[str, Counter[str]] = defaultdict(Counter)
    creator_publications: dict[str, set[str]] = defaultdict(set)
    creator_topic_posts: dict[str, Counter[str]] = defaultdict(Counter)
    for post in verified_posts:
        publication_url = post["publication_url"]
        topic = publication_topics[publication_url][0]
        for byline in post["bylines"]:
            creator_id = byline["creator_id"]
            creator_posts[creator_id].add(post["post_id"])
            creator_names[creator_id][byline.get("name") or ""] += 1
            creator_handles[creator_id][byline.get("handle") or ""] += 1
            creator_publications[creator_id].add(publication_url)
            creator_topic_posts[creator_id][topic] += 1

    eligible_creators = []
    excluded_organizations = 0
    for creator_id in sorted(creator_posts):
        name = creator_names[creator_id].most_common(1)[0][0]
        handle = creator_handles[creator_id].most_common(1)[0][0]
        type_value = creator_type(name)
        if type_value != "individual_provisional":
            excluded_organizations += 1
            continue
        topic_counts = creator_topic_posts[creator_id]
        topic = sorted(
            TOPICS,
            key=lambda value: (-topic_counts[value], TOPICS.index(value)),
        )[0]
        confidence_values = [
            publication_topics[url][2]
            for url in creator_publications[creator_id]
            if url in publication_topics
        ]
        confidence = (
            "high" if "high" in confidence_values
            else "medium" if "medium" in confidence_values
            else "low"
        )
        eligible_creators.append(
            {
                "platform": PLATFORM,
                "creator_id": creator_id,
                "creator_id_type": "substack_numeric_user_id",
                "identity_quality": "verified_stable",
                "handle_or_slug": handle,
                "display_name": name,
                "creator_type": type_value,
                "topic_family": topic,
                "macro_domain": MACRO_DOMAINS[topic],
                "topic_method": "publication_description_title_keyword_v1",
                "topic_confidence": confidence,
                "creator_priority": sha256_text(
                    f"creator-priority-v1|{PLATFORM}|{creator_id}"
                ),
                "profile_url": f"https://substack.com/@{handle}" if handle else "",
            }
        )

    selected_ids, allocation = allocate_sample(
        eligible_creators, args.target_creators, args.topic_floor
    )
    selected_creators = [
        row for row in eligible_creators if row["creator_id"] in selected_ids
    ]
    creator_lookup = {row["creator_id"]: row for row in eligible_creators}
    print(
        f"creator selection: {len(selected_ids)} selected from {len(eligible_creators)} eligible",
        flush=True,
    )

    selected_post_ids_by_creator: dict[str, set[str]] = defaultdict(set)
    selected_for_post: dict[str, set[str]] = defaultdict(set)
    for creator_id in selected_ids:
        candidates = []
        for post_id in creator_posts[creator_id]:
            post = post_by_id[post_id]
            if post["full_text"] and post["retained_word_count"] >= 200:
                candidates.append(post)
        candidates.sort(
            key=lambda post: sha256_text(
                f"post-priority-v1|{PLATFORM}|{post['post_id']}"
            )
        )
        for post in candidates[:3]:
            selected_post_ids_by_creator[creator_id].add(post["post_id"])
            selected_for_post[post["post_id"]].add(creator_id)

    included_posts = []
    for post in verified_posts:
        byline_ids = [item["creator_id"] for item in post["bylines"]]
        relevant = [creator_id for creator_id in byline_ids if creator_id in selected_ids]
        if not relevant:
            continue
        included_posts.append((post, relevant))

    meta_fields = [
        "platform", "creator_id", "publication_id", "post_id", "url",
        "published_at", "year_month", "title", "subtitle", "byline",
        "byline_creator_ids", "selected_creator_ids", "post_type", "audience",
        "is_paywalled", "has_full_text", "text_retained", "word_count",
        "language", "topic_family", "topic_method", "topic_confidence",
        "claps_or_likes", "comments_count", "post_priority",
        "selected_primary_post", "selected_for_creator_ids",
        "source_completeness", "source_type", "source_url", "source_sha256",
    ]
    meta_rows = []
    text_rows = []
    for post, relevant in included_posts:
        publication_url = post["publication_url"]
        topic, topic_method, topic_confidence = publication_topics[publication_url]
        byline_ids = [item["creator_id"] for item in post["bylines"]]
        byline_names = [item.get("name") or "" for item in post["bylines"]]
        selected_for = sorted(selected_for_post[post["post_id"]])
        text_retained = bool(selected_for)
        completeness = "full" if post["full_text"] else "metadata_only"
        publication_info = publication_metadata.get(publication_url, {})
        publication_id = post["publication_id"] or str(
            publication_info.get("publication_id") or ""
        )
        meta_rows.append(
            {
                "platform": PLATFORM,
                "creator_id": relevant[0],
                "publication_id": publication_id,
                "post_id": post["post_id"],
                "url": post["canonical_url"],
                "published_at": post["published_at"],
                "year_month": args.month,
                "title": post["title"],
                "subtitle": post["subtitle"],
                "byline": "; ".join(byline_names),
                "byline_creator_ids": ";".join(byline_ids),
                "selected_creator_ids": ";".join(sorted(relevant)),
                "post_type": post["post_type"],
                "audience": post["audience"],
                "is_paywalled": post["is_paywalled"],
                "has_full_text": bool(post["full_text"]),
                "text_retained": text_retained,
                "word_count": post["platform_word_count"] or post["retained_word_count"],
                "language": post["language"] or str(
                    publication_info.get("language") or ""
                ),
                "topic_family": topic,
                "topic_method": topic_method,
                "topic_confidence": topic_confidence,
                "claps_or_likes": "" if post["reaction_count"] is None else post["reaction_count"],
                "comments_count": "" if post["comment_count"] is None else post["comment_count"],
                "post_priority": sha256_text(
                    f"post-priority-v1|{PLATFORM}|{post['post_id']}"
                ),
                "selected_primary_post": text_retained,
                "selected_for_creator_ids": ";".join(selected_for),
                "source_completeness": completeness,
                "source_type": post["source_type"],
                "source_url": post["source_url"],
                "source_sha256": post["source_sha256"],
            }
        )
        if text_retained:
            text_rows.append(
                {
                    "platform": PLATFORM,
                    "post_id": post["post_id"],
                    "full_text": post["full_text"],
                    "word_count": post["retained_word_count"],
                    "text_sha256": post["text_sha256"],
                    "source_completeness": "full",
                    "text_source": post["source_type"],
                    "source_url": post["source_url"],
                }
            )

    meta_rows.sort(key=lambda row: (row["published_at"], row["post_id"]))
    text_rows.sort(key=lambda row: row["post_id"])
    sample_version = f"substack-{args.month}-authorized-frame-v1"
    creator_month_rows = []
    for creator in sorted(selected_creators, key=lambda row: row["creator_id"]):
        creator_id = creator["creator_id"]
        posts = [
            post_by_id[post_id]
            for post_id in creator_posts[creator_id]
            if post_id in post_by_id
        ]
        posts.sort(key=lambda row: (row["published_at"], row["post_id"]))
        full_candidates = [
            post for post in posts
            if post["full_text"] and post["retained_word_count"] >= 200
        ]
        selected_posts = [
            post_by_id[post_id]
            for post_id in selected_post_ids_by_creator[creator_id]
        ]
        selected_words = [post["retained_word_count"] for post in selected_posts]
        creator_month_rows.append(
            {
                "sample_version": sample_version,
                "platform": PLATFORM,
                "creator_id": creator_id,
                "year_month": args.month,
                "topic_family": creator["topic_family"],
                "macro_domain": creator["macro_domain"],
                "topic_method": creator["topic_method"],
                "topic_confidence": creator["topic_confidence"],
                "sample_stratum": "permanent_random_within_publication_frame_pilot",
                "creator_priority": creator["creator_priority"],
                "eligible": True,
                "selected": True,
                "inclusion_weight": "",
                "frame_id": frame_id,
                "metadata_post_count": len(posts),
                "public_full_text_available_count": len(full_candidates),
                "selected_text_count": len(selected_posts),
                "selected_word_count_total": sum(selected_words),
                "selected_word_count_mean": (
                    round(sum(selected_words) / len(selected_words), 1)
                    if selected_words else ""
                ),
                "first_post_at": posts[0]["published_at"],
                "last_post_at": posts[-1]["published_at"],
                "is_partial_month": is_partial_month,
                "observed_days": observed_days,
            }
        )

    coverage_rows = []
    for topic in TOPICS:
        selected_topic_ids = {
            row["creator_id"] for row in selected_creators
            if row["topic_family"] == topic
        }
        topic_meta = [
            row for row in meta_rows
            if set(row["selected_creator_ids"].split(";")) & selected_topic_ids
        ]
        coverage_rows.append(
            {
                "sample_version": sample_version,
                "platform": PLATFORM,
                "year_month": args.month,
                "topic_family": topic,
                "frame_id": frame_id,
                "sample_stratum": "permanent_random_within_publication_frame_pilot",
                "target_creators": allocation[topic]["allocated"],
                "eligible_creators": allocation[topic]["eligible"],
                "sampled_creators": len(selected_topic_ids),
                "topic_floor_target": args.topic_floor,
                "topic_floor_shortfall": allocation[topic]["shortfall"],
                "metadata_posts": len(topic_meta),
                "public_full_text_posts": sum(
                    str(row["has_full_text"]).lower() == "true" for row in topic_meta
                ),
                "selected_texts": sum(
                    bool(set(row["selected_for_creator_ids"].split(";")) & selected_topic_ids)
                    for row in topic_meta
                ),
                "paywalled_posts": sum(
                    str(row["is_paywalled"]).lower() == "true" for row in topic_meta
                ),
                "is_partial_month": is_partial_month,
                "coverage_note": (
                    "Eligible and sampled counts apply to the deterministic publication-frame "
                    "pilot; platform-wide creator inclusion weights are unavailable"
                ),
            }
        )

    publications_rows = []
    relationship_rows = []
    for publication_url, posts in sorted(posts_by_publication.items()):
        publication_info = publication_metadata.get(publication_url, {})
        pub_selected_ids = set()
        for post in posts:
            pub_selected_ids.update(
                item["creator_id"] for item in post["bylines"]
                if item["creator_id"] in selected_ids
            )
        if not pub_selected_ids:
            continue
        publication_id = str(publication_info.get("publication_id") or "")
        if not publication_id:
            for post in posts:
                if post["publication_id"]:
                    publication_id = post["publication_id"]
                    break
        if not publication_id:
            publication_id = f"substack:url:{sha256_text(publication_url)[:16]}"
        publications_rows.append(
            {
                "platform": PLATFORM,
                "publication_id": publication_id,
                "publication_name": str(
                    publication_info.get("publication_name") or ""
                ),
                "publication_url": publication_url,
                "publication_type": str(
                    publication_info.get("publication_type") or ""
                ),
            }
        )
        contributor_roles = {}
        for post in posts:
            for byline in post["bylines"]:
                if byline.get("publication_role"):
                    contributor_roles[byline["creator_id"]] = byline[
                        "publication_role"
                    ]
        for creator_id in sorted(pub_selected_ids):
            relationship_rows.append(
                {
                    "platform": PLATFORM,
                    "creator_id": creator_id,
                    "publication_id": publication_id,
                    "relationship_role": contributor_roles.get(creator_id, "post_byline"),
                    "relationship_quality": "verified_by_public_post",
                }
            )

    error_rows = []
    for stage in [post_stage]:
        for row in checkpoint.rows(stage):
            if row["item_key"] not in frame_urls:
                continue
            if row["ok"]:
                continue
            error_rows.append(
                {
                    "platform": PLATFORM,
                    "stage": stage,
                    "item_key": row["item_key"],
                    "error_type": row["error_type"],
                    "error_message": row["error_message"],
                    "observed_at": row["observed_at"],
                }
            )

    year, month_number = args.month.split("-")
    month_dir = args.output_root / year / month_number
    lookup_dir = args.output_root.parent / "lookup"
    month_dir.mkdir(parents=True, exist_ok=True)
    write_csv(month_dir / "posts_meta.csv", meta_fields, meta_rows)
    text_fields = [
        "platform", "post_id", "full_text", "word_count", "text_sha256",
        "source_completeness", "text_source", "source_url",
    ]
    write_csv(month_dir / "posts_text.csv", text_fields, text_rows)
    write_csv(
        month_dir / "creator_month.csv",
        list(creator_month_rows[0]) if creator_month_rows else [
            "sample_version", "platform", "creator_id", "year_month"
        ],
        creator_month_rows,
    )
    write_csv(month_dir / "coverage.csv", list(coverage_rows[0]), coverage_rows)
    write_csv(
        month_dir / "scrape_errors.csv",
        ["platform", "stage", "item_key", "error_type", "error_message", "observed_at"],
        error_rows,
    )

    creator_fields = [
        "platform", "creator_id", "creator_id_type", "identity_quality",
        "handle_or_slug", "display_name", "creator_type", "topic_family",
        "macro_domain", "topic_method", "topic_confidence", "creator_priority",
        "profile_url",
    ]
    upsert_csv(
        lookup_dir / "creators.csv", creator_fields, selected_creators,
        ("platform", "creator_id"),
    )
    publication_fields = [
        "platform", "publication_id", "publication_name", "publication_url",
        "publication_type",
    ]
    upsert_csv(
        lookup_dir / "publications.csv", publication_fields, publications_rows,
        ("platform", "publication_id"),
    )
    relationship_fields = [
        "platform", "creator_id", "publication_id", "relationship_role",
        "relationship_quality",
    ]
    upsert_csv(
        lookup_dir / "creator_publications.csv", relationship_fields,
        relationship_rows, ("platform", "creator_id", "publication_id"),
    )

    generated = [
        month_dir / "posts_meta.csv",
        month_dir / "posts_text.csv",
        month_dir / "creator_month.csv",
        month_dir / "coverage.csv",
        month_dir / "scrape_errors.csv",
    ]
    manifest = {
        "dataset_version": sample_version,
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "collection_started_at": started_at,
        "collection_completed_at": iso_now(),
        "platform": PLATFORM,
        "month": args.month,
        "permission_basis": args.permission_basis,
        "permission_evidence_status": "user_reported_email_not_stored_in_repository",
        "source_method": (
            "frozen_publication_frame_and_shared_authorized_history_cache"
            if args.frame_file
            else "authorized_global_sitemap_frame_and_paginated_posts_json_with_sitemap_page_fallback"
        ),
        "global_sitemaps": GLOBAL_SITEMAPS,
        "global_sitemap_sha256": global_hashes,
        "frozen_frame_file": str(args.frame_file or ""),
        "frame_id": frame_id,
        "frame_priority_spec": "SHA256(publication-frame-v1|substack|publication_url)",
        "creator_priority_spec": "SHA256(creator-priority-v1|substack|creator_id)",
        "post_priority_spec": "SHA256(post-priority-v1|substack|post_id)",
        "candidate_publications": candidate_pool_size,
        "sampled_publications": len(frame_rows),
        "delay_seconds": args.delay_seconds,
        "max_workers": args.max_workers,
        "max_rate_limit_events": args.max_rate_limit_events,
        "rate_limit_window_seconds": args.rate_limit_window_seconds,
        "request_count_this_run": collector.request_count,
        "http_status_counts_this_run": dict(
            sorted(collector.http_status_counts.items())
        ),
        "sampling_fields_location": "creator_month.csv",
        "batch_metadata_location": "manifest.json",
        "counts": {
            "active_publications_with_verified_posts": len(active_publications),
            "verified_month_posts": len(verified_posts),
            "eligible_individual_creators": len(eligible_creators),
            "excluded_provisional_organizations": excluded_organizations,
            "selected_creators": len(selected_ids),
            "metadata_posts": len(meta_rows),
            "selected_texts": len(text_rows),
            "scrape_errors": len(error_rows),
        },
        "limitations": [
            "This is a probability sample of current publication URLs, then a creator sample within that observed frame; creators with multiple publications can have higher frame inclusion probability.",
            "Platform-wide creator inclusion weights are not valid until publication multiplicity and historical frame coverage are audited.",
            "Topic families use an automated keyword heuristic and require manual validation before inferential use.",
            "Organization exclusion is provisional and requires manual audit.",
            "Sitemap lastmod is used only for candidate discovery; public page post_date verifies month inclusion.",
            "Paid posts contribute metadata only; no paywalled body text is retained.",
        ],
        "files": {
            path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in generated
        },
    }
    (month_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frame_description = (
        f"{len(frame_rows):,} publication URLs from the frozen current-publication "
        f"frame ({candidate_pool_size:,} candidates at frame creation)"
        if args.frame_file
        else (
            f"{len(frame_rows):,} publication URLs from {candidate_pool_size:,} "
            f"current sitemap candidates with `lastmod` on or after {args.month}-01"
        )
    )
    readme = f"""# Authorized Substack platform-month partition: {args.month}

This partition screens a deterministic permanent-random sample of
{frame_description}. Public JSON post records verify original publication dates
and numeric byline IDs.

## Result

- Publications with verified {args.month} posts: {len(active_publications):,}
- Date-verified {args.month} posts: {len(verified_posts):,}
- Eligible provisional individual creators: {len(eligible_creators):,}
- Selected creators: {len(selected_ids):,} of target {args.target_creators:,}
- Metadata posts for selected creators: {len(meta_rows):,}
- Selected public/full-text posts: {len(text_rows):,}
- Recorded collection errors: {len(error_rows):,}

Requests start no more frequently than once every {args.delay_seconds:g} seconds
across at most {args.max_workers} workers. HTTP 429 responses pause all workers,
honor `Retry-After` when supplied, and open a stop-and-resume circuit after
{args.max_rate_limit_events} events within {args.rate_limit_window_seconds}
seconds.

`creator_month.csv` contains sampling fields and creator-month outcomes.
`coverage.csv` reports all eight topic cells and any floor shortfalls. Parser and
collection timestamps appear once in `manifest.json`, not in every CSV row.

## Interpretation boundary

This is a publication-frame sample, not a platform-wide creator probability
sample. Creators with several publications can have higher first-stage inclusion
probability, and topic/organization labels are automated provisional
classifications. Do not use platform-representative weights until those two
audits are complete.
"""
    (month_dir / "README.md").write_text(readme, encoding="utf-8")
    checkpoint.close()
    return month_dir


def main() -> int:
    args = parse_args()
    output = build(args)
    print(output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
