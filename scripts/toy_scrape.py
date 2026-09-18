#!/usr/bin/env python3
"""Toy scrapes for Substack + Medium → CSV stubs for the capstone panel.

Public endpoints only. Throttled. No paywall bypass.
"""

from __future__ import annotations

import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "toy"
UA = "CapstoneResearchBot/0.1 (+academic toy scrape; contact: local research)"
SLEEP = 1.0

# Hand-picked elite toys (known active on-platform as of probe)
SUBSTACK_TOYS = [
    {
        "creator_id": "substack:slowboring",
        "handle_or_slug": "slowboring",
        "display_name": "Slow Boring",
        "stratum": "elite",
        "topic_family": "Politics and current affairs",
        "base_url": "https://www.slowboring.com",
    },
    {
        "creator_id": "substack:lennysnewsletter",
        "handle_or_slug": "lennysnewsletter",
        "display_name": "Lenny's Newsletter",
        "stratum": "elite",
        "topic_family": "Business, finance and economics",
        "base_url": "https://lennysnewsletter.com",
    },
    {
        "creator_id": "substack:astralcodexten",
        "handle_or_slug": "astralcodexten",
        "display_name": "Astral Codex Ten",
        "stratum": "elite",
        "topic_family": "Society, history and ideas",
        "base_url": "https://astralcodexten.substack.com",
    },
]

MEDIUM_TOYS = [
    {
        "creator_id": "medium:zulie",
        "handle_or_slug": "zulie",
        "display_name": "Zulie Rane",
        "stratum": "elite",
        "topic_family": "Health, family and personal life",
        "feed_url": "https://medium.com/feed/@zulie",
    },
    {
        "creator_id": "medium:tomaspueyo",
        "handle_or_slug": "tomaspueyo",
        "display_name": "Tomas Pueyo",
        "stratum": "elite",
        "topic_family": "Technology, science, education and environment",
        "feed_url": "https://medium.com/feed/@tomaspueyo",
    },
    {
        "creator_id": "medium:uxdesigncc",
        "handle_or_slug": "uxdesigncc",
        # publication feed as secondary toy path
        "display_name": "UX Collective (publication feed toy)",
        "stratum": "elite",
        "topic_family": "Arts, culture, media and design",
        "feed_url": "https://medium.com/feed/user-experience-design-1",
    },
]


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def year_month(iso_or_rfc: str) -> str:
    raw = iso_or_rfc.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
    ):
        try:
            dt = datetime.strptime(raw.replace("GMT", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m")
        except ValueError:
            continue
    # fallback: first 7 chars if looks like ISO date
    if re.match(r"\d{4}-\d{2}", raw):
        return raw[:7]
    return ""


def scrape_substack_archive(creator: dict, limit: int = 12) -> tuple[list[dict], list[dict]]:
    base = creator["base_url"].rstrip("/")
    url = f"{base}/api/v1/archive?sort=new&limit={limit}"
    data = json.loads(fetch(url).decode("utf-8"))
    meta_rows, text_rows = [], []
    for post in data:
        audience = (post.get("audience") or "").lower()
        is_paywalled = audience in {"only_paid", "founding"}
        post_id = str(post.get("id") or post.get("slug"))
        canonical = post.get("canonical_url") or f"{base}/p/{post.get('slug')}"
        title = post.get("title") or ""
        published = post.get("post_date") or ""
        # archive payload usually lacks full body; pull RSS item match later if needed
        description = post.get("description") or post.get("subtitle") or ""
        has_full = bool(description) and not is_paywalled
        meta_rows.append(
            {
                "platform": "substack",
                "creator_id": creator["creator_id"],
                "post_id": post_id,
                "url": canonical,
                "published_at": published,
                "year_month": year_month(published),
                "title": title,
                "is_paywalled": str(is_paywalled).lower(),
                "has_full_text": str(has_full).lower(),
                "word_count": "",
                "tags": "",
                "claps_or_likes": post.get("reaction_count") or post.get("like_count") or "",
                "comments_count": post.get("comment_count") or "",
                "language": "",
                "audience": audience,
                "text_source": "archive_api",
            }
        )
        if has_full:
            text_rows.append(
                {
                    "platform": "substack",
                    "post_id": post_id,
                    "full_text": description,
                    "text_source": "archive_api_description",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    # Enrich with RSS full content for free posts when available
    try:
        time.sleep(SLEEP)
        rss = fetch(f"{base}/feed")
        root = ET.fromstring(rss)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        by_link = {}
        for item in root.findall("./channel/item"):
            link = (item.findtext("link") or "").split("?")[0]
            encoded = item.findtext("content:encoded", default="", namespaces=ns) or ""
            by_link[link] = strip_html(encoded)
        for row in meta_rows:
            url0 = row["url"].split("?")[0]
            body = by_link.get(url0) or ""
            # fuzzy: slug in link
            if not body:
                slug = url0.rstrip("/").split("/")[-1]
                for link, text in by_link.items():
                    if slug and slug in link:
                        body = text
                        break
            if body and row["is_paywalled"] == "false":
                row["has_full_text"] = "true"
                row["word_count"] = str(len(body.split()))
                row["text_source"] = "rss"
                # replace/add text row
                text_rows = [t for t in text_rows if t["post_id"] != row["post_id"]]
                text_rows.append(
                    {
                        "platform": "substack",
                        "post_id": row["post_id"],
                        "full_text": body,
                        "text_source": "rss",
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except Exception as exc:  # noqa: BLE001 - toy scrape should continue
        print(f"  RSS enrich failed for {creator['handle_or_slug']}: {exc}")
    return meta_rows, text_rows


def scrape_medium_feed(creator: dict, limit: int = 10) -> tuple[list[dict], list[dict]]:
    raw = fetch(creator["feed_url"])
    root = ET.fromstring(raw)
    ns = {
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "atom": "http://www.w3.org/2005/Atom",
    }
    meta_rows, text_rows = [], []
    items = root.findall("./channel/item")[:limit]
    for item in items:
        title = item.findtext("title") or ""
        link = (item.findtext("link") or "").split("?")[0]
        guid = item.findtext("guid") or link
        post_id = guid.rstrip("/").split("/")[-1]
        published = item.findtext("pubDate") or ""
        categories = [c.text for c in item.findall("category") if c.text]
        encoded = item.findtext("content:encoded", default="", namespaces=ns) or ""
        desc = item.findtext("description") or ""
        body = strip_html(encoded) if encoded else strip_html(desc)
        # Medium RSS often omits full member-only body; treat short stubs carefully
        is_paywalled = "false"
        has_full = len(body.split()) >= 80
        meta_rows.append(
            {
                "platform": "medium",
                "creator_id": creator["creator_id"],
                "post_id": post_id,
                "url": link,
                "published_at": published,
                "year_month": year_month(published),
                "title": title,
                "is_paywalled": is_paywalled,
                "has_full_text": str(has_full).lower(),
                "word_count": str(len(body.split())) if body else "",
                "tags": "|".join(categories),
                "claps_or_likes": "",
                "comments_count": "",
                "language": "",
                "audience": "",
                "text_source": "rss",
            }
        )
        if body:
            text_rows.append(
                {
                    "platform": "medium",
                    "post_id": post_id,
                    "full_text": body,
                    "text_source": "rss",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    return meta_rows, text_rows


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main() -> None:
    creators: list[dict] = []
    posts_meta: list[dict] = []
    posts_text: list[dict] = []
    errors: list[dict] = []

    print("=== Substack toy scrape ===")
    for c in SUBSTACK_TOYS:
        creators.append(
            {
                "platform": "substack",
                "creator_id": c["creator_id"],
                "handle_or_slug": c["handle_or_slug"],
                "display_name": c["display_name"],
                "stratum": c["stratum"],
                "topic_family": c["topic_family"],
                "native_categories_tags": "",
                "panel_weight": "1",
                "url": c["base_url"],
                "scrape_meta": "toy_v1",
            }
        )
        try:
            print(f"- {c['display_name']}")
            meta, text = scrape_substack_archive(c, limit=12)
            posts_meta.extend(meta)
            posts_text.extend(text)
            print(f"  posts={len(meta)} text_rows={len(text)}")
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            errors.append({"platform": "substack", "creator": c["handle_or_slug"], "error": str(exc)})
            print(f"  ERROR: {exc}")
        time.sleep(SLEEP)

    print("=== Medium toy scrape ===")
    for c in MEDIUM_TOYS:
        creators.append(
            {
                "platform": "medium",
                "creator_id": c["creator_id"],
                "handle_or_slug": c["handle_or_slug"],
                "display_name": c["display_name"],
                "stratum": c["stratum"],
                "topic_family": c["topic_family"],
                "native_categories_tags": "",
                "panel_weight": "1",
                "url": c["feed_url"],
                "scrape_meta": "toy_v1",
            }
        )
        try:
            print(f"- {c['display_name']}")
            meta, text = scrape_medium_feed(c, limit=10)
            posts_meta.extend(meta)
            posts_text.extend(text)
            print(f"  posts={len(meta)} text_rows={len(text)}")
        except (HTTPError, URLError, ET.ParseError) as exc:
            errors.append({"platform": "medium", "creator": c["handle_or_slug"], "error": str(exc)})
            print(f"  ERROR: {exc}")
        time.sleep(SLEEP)

    write_csv(
        OUT / "creators.csv",
        creators,
        [
            "platform",
            "creator_id",
            "handle_or_slug",
            "display_name",
            "stratum",
            "topic_family",
            "native_categories_tags",
            "panel_weight",
            "url",
            "scrape_meta",
        ],
    )
    write_csv(
        OUT / "posts_meta.csv",
        posts_meta,
        [
            "platform",
            "creator_id",
            "post_id",
            "url",
            "published_at",
            "year_month",
            "title",
            "is_paywalled",
            "has_full_text",
            "word_count",
            "tags",
            "claps_or_likes",
            "comments_count",
            "language",
            "audience",
            "text_source",
        ],
    )
    write_csv(
        OUT / "posts_text.csv",
        posts_text,
        ["platform", "post_id", "full_text", "text_source", "retrieved_at"],
    )
    write_csv(
        OUT / "scrape_errors.csv",
        errors,
        ["platform", "creator", "error"],
    )

    # tiny monthly density summary for the toy
    from collections import Counter

    ym = Counter(r["year_month"] for r in posts_meta if r.get("year_month"))
    summary = [{"year_month": k, "n_posts": v} for k, v in sorted(ym.items())]
    write_csv(OUT / "toy_month_counts.csv", summary, ["year_month", "n_posts"])

    print(f"\nWrote CSVs to {OUT}")
    print(f"creators={len(creators)} posts_meta={len(posts_meta)} posts_text={len(posts_text)} errors={len(errors)}")


if __name__ == "__main__":
    main()
