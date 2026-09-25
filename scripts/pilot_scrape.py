#!/usr/bin/env python3
"""Pilot scrape: more Substack + Medium creators → data/pilot CSVs.

Substack: paginated /api/v1/archive back to WINDOW_START, then full text
for up to K free posts per creator-month via /api/v1/posts/{slug}.
Medium: public RSS (~10 recent posts per feed) — deeper history blocked
by Cloudflare on HTML/JSON paths from this environment.

Public endpoints only. Throttled. No paywall bypass.
"""

from __future__ import annotations

import csv
import json
import random
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "pilot"
UA = "CapstoneResearchBot/0.2 (+academic pilot scrape; research use)"
SLEEP = 0.7
WINDOW_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 25, tzinfo=timezone.utc)
K_TEXT_PER_MONTH = 2
MAX_ARCHIVE_POSTS = 220  # per Substack creator
ARCHIVE_PAGE = 20

SUBSTACK = [
    # Politics
    {"creator_id": "substack:slowboring", "handle_or_slug": "slowboring", "display_name": "Slow Boring", "stratum": "elite", "topic_family": "Politics and current affairs", "base_url": "https://www.slowboring.com"},
    {"creator_id": "substack:heathercoxrichardson", "handle_or_slug": "heathercoxrichardson", "display_name": "Letters from an American", "stratum": "elite", "topic_family": "Politics and current affairs", "base_url": "https://heathercoxrichardson.substack.com"},
    {"creator_id": "substack:thebulwark", "handle_or_slug": "thebulwark", "display_name": "The Bulwark", "stratum": "elite", "topic_family": "Politics and current affairs", "base_url": "https://www.thebulwark.com"},
    # Ideas / society
    {"creator_id": "substack:astralcodexten", "handle_or_slug": "astralcodexten", "display_name": "Astral Codex Ten", "stratum": "elite", "topic_family": "Society, history and ideas", "base_url": "https://astralcodexten.substack.com"},
    {"creator_id": "substack:derekthompson", "handle_or_slug": "derekthompson", "display_name": "The Abundance Agenda", "stratum": "elite", "topic_family": "Society, history and ideas", "base_url": "https://www.derekthompson.org"},
    {"creator_id": "substack:intrinsicperspective", "handle_or_slug": "theintrinsicperspective", "display_name": "The Intrinsic Perspective", "stratum": "elite", "topic_family": "Society, history and ideas", "base_url": "https://www.theintrinsicperspective.com"},
    # Business
    {"creator_id": "substack:lennysnewsletter", "handle_or_slug": "lennysnewsletter", "display_name": "Lenny's Newsletter", "stratum": "elite", "topic_family": "Business, finance and economics", "base_url": "https://lennysnewsletter.com"},
    {"creator_id": "substack:notboring", "handle_or_slug": "notboring", "display_name": "Not Boring", "stratum": "elite", "topic_family": "Business, finance and economics", "base_url": "https://www.notboring.co"},
    {"creator_id": "substack:noahpinion", "handle_or_slug": "noahpinion", "display_name": "Noahpinion", "stratum": "elite", "topic_family": "Business, finance and economics", "base_url": "https://www.noahpinion.blog"},
    # Tech / science
    {"creator_id": "substack:importai", "handle_or_slug": "importai", "display_name": "Import AI", "stratum": "elite", "topic_family": "Technology, science, education and environment", "base_url": "https://importai.substack.com"},
    {"creator_id": "substack:oneusefulthing", "handle_or_slug": "oneusefulthing", "display_name": "One Useful Thing", "stratum": "elite", "topic_family": "Technology, science, education and environment", "base_url": "https://www.oneusefulthing.org"},
    {"creator_id": "substack:constructionphysics", "handle_or_slug": "constructionphysics", "display_name": "Construction Physics", "stratum": "elite", "topic_family": "Technology, science, education and environment", "base_url": "https://www.construction-physics.com"},
    # Creative / culture / lifestyle / health
    {"creator_id": "substack:countercraft", "handle_or_slug": "countercraft", "display_name": "Counter Craft", "stratum": "elite", "topic_family": "Literature and creative writing", "base_url": "https://countercraft.substack.com"},
    {"creator_id": "substack:annfriedman", "handle_or_slug": "annfriedman", "display_name": "Ann Friedman", "stratum": "elite", "topic_family": "Arts, culture, media and design", "base_url": "https://annfriedman.substack.com"},
    {"creator_id": "substack:yourlocalepidemiologist", "handle_or_slug": "yourlocalepidemiologist", "display_name": "Your Local Epidemiologist", "stratum": "elite", "topic_family": "Health, family and personal life", "base_url": "https://yourlocalepidemiologist.substack.com"},
    {"creator_id": "substack:dinneralovestory", "handle_or_slug": "dinneralovestory", "display_name": "Dinner: A Love Story", "stratum": "elite", "topic_family": "Lifestyle, food, travel, home and leisure", "base_url": "https://dinneralovestory.substack.com"},
]

MEDIUM = [
    {"creator_id": "medium:zulie", "handle_or_slug": "zulie", "display_name": "Zulie Rane", "stratum": "elite", "topic_family": "Health, family and personal life", "feed_url": "https://medium.com/feed/@zulie"},
    {"creator_id": "medium:tomaspueyo", "handle_or_slug": "tomaspueyo", "display_name": "Tomas Pueyo", "stratum": "elite", "topic_family": "Technology, science, education and environment", "feed_url": "https://medium.com/feed/@tomaspueyo"},
    {"creator_id": "medium:jamesclear", "handle_or_slug": "james_clear", "display_name": "James Clear", "stratum": "elite", "topic_family": "Health, family and personal life", "feed_url": "https://medium.com/feed/@james_clear"},
    {"creator_id": "medium:umairh", "handle_or_slug": "umairh", "display_name": "Umair Haque", "stratum": "elite", "topic_family": "Society, history and ideas", "feed_url": "https://medium.com/feed/@umairh"},
    {"creator_id": "medium:aytekintank", "handle_or_slug": "aytekintank", "display_name": "Aytekin Tank", "stratum": "elite", "topic_family": "Business, finance and economics", "feed_url": "https://medium.com/feed/@aytekintank"},
    {"creator_id": "medium:timdenning", "handle_or_slug": "timdenning", "display_name": "Tim Denning", "stratum": "elite", "topic_family": "Business, finance and economics", "feed_url": "https://medium.com/feed/@timdenning"},
    {"creator_id": "medium:katiecmiller", "handle_or_slug": "katiecmiller", "display_name": "Katie Miller", "stratum": "elite", "topic_family": "Arts, culture, media and design", "feed_url": "https://medium.com/feed/@katiecmiller"},
    {"creator_id": "medium:craigmod", "handle_or_slug": "craigmod", "display_name": "Craig Mod", "stratum": "elite", "topic_family": "Arts, culture, media and design", "feed_url": "https://medium.com/feed/@craigmod"},
    {"creator_id": "medium:anildash", "handle_or_slug": "anildash", "display_name": "Anil Dash", "stratum": "elite", "topic_family": "Technology, science, education and environment", "feed_url": "https://medium.com/feed/@anildash"},
    {"creator_id": "medium:ev", "handle_or_slug": "ev", "display_name": "Ev Williams", "stratum": "elite", "topic_family": "Technology, science, education and environment", "feed_url": "https://medium.com/feed/@ev"},
    {"creator_id": "medium:jasonfried", "handle_or_slug": "jasonfried", "display_name": "Jason Fried", "stratum": "elite", "topic_family": "Business, finance and economics", "feed_url": "https://medium.com/feed/@jasonfried"},
    {"creator_id": "medium:nathanbaurich", "handle_or_slug": "nbashaw", "display_name": "Nathan Baschez", "stratum": "elite", "topic_family": "Business, finance and economics", "feed_url": "https://medium.com/feed/@nbashaw"},
    {"creator_id": "medium:uxdesign", "handle_or_slug": "uxdesigncc", "display_name": "UX Collective", "stratum": "elite", "topic_family": "Arts, culture, media and design", "feed_url": "https://medium.com/feed/user-experience-design-1"},
    {"creator_id": "medium:towardsdatascience", "handle_or_slug": "towardsdatascience", "display_name": "Towards Data Science", "stratum": "elite", "topic_family": "Technology, science, education and environment", "feed_url": "https://medium.com/feed/towards-data-science"},
    {"creator_id": "medium:betterhumans", "handle_or_slug": "betterhumans", "display_name": "Better Humans", "stratum": "elite", "topic_family": "Health, family and personal life", "feed_url": "https://medium.com/feed/better-humans"},
    {"creator_id": "medium:theascent", "handle_or_slug": "theascent", "display_name": "The Ascent", "stratum": "elite", "topic_family": "Business, finance and economics", "feed_url": "https://medium.com/feed/the-ascent"},
]


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/rss+xml, */*"})
    with urlopen(req, timeout=45) as resp:
        return resp.read()


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
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
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def year_month(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m") if dt else ""


def in_window(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return WINDOW_START <= dt <= WINDOW_END


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def scrape_substack_archive(creator: dict) -> list[dict]:
    base = creator["base_url"].rstrip("/")
    offset = 0
    posts: list[dict] = []
    while len(posts) < MAX_ARCHIVE_POSTS:
        url = f"{base}/api/v1/archive?sort=new&limit={ARCHIVE_PAGE}&offset={offset}"
        time.sleep(SLEEP)
        try:
            batch = json.loads(fetch(url).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  archive error offset={offset}: {exc}")
            break
        if not batch:
            break
        stop = False
        for post in batch:
            dt = parse_dt(post.get("post_date") or "")
            if dt and dt < WINDOW_START:
                stop = True
                break
            if not in_window(dt):
                continue
            audience = (post.get("audience") or "").lower()
            is_paywalled = audience in {"only_paid", "founding"}
            slug = post.get("slug") or ""
            post_id = str(post.get("id") or slug)
            canonical = post.get("canonical_url") or (f"{base}/p/{slug}" if slug else "")
            posts.append(
                {
                    "platform": "substack",
                    "creator_id": creator["creator_id"],
                    "post_id": post_id,
                    "slug": slug,
                    "url": canonical,
                    "published_at": post.get("post_date") or "",
                    "year_month": year_month(dt),
                    "title": post.get("title") or "",
                    "is_paywalled": str(is_paywalled).lower(),
                    "has_full_text": "false",
                    "word_count": "",
                    "tags": "",
                    "claps_or_likes": post.get("reaction_count") or post.get("like_count") or "",
                    "comments_count": post.get("comment_count") or "",
                    "language": "",
                    "audience": audience,
                    "text_source": "archive_api",
                }
            )
            if len(posts) >= MAX_ARCHIVE_POSTS:
                break
        if stop or len(batch) < ARCHIVE_PAGE:
            break
        offset += ARCHIVE_PAGE
    return posts


def fetch_substack_body(base: str, slug: str) -> tuple[str, int]:
    if not slug:
        return "", 0
    url = f"{base.rstrip('/')}/api/v1/posts/{slug}"
    time.sleep(SLEEP)
    data = json.loads(fetch(url).decode("utf-8"))
    html = data.get("body_html") or ""
    text = strip_html(html)
    wc = int(data.get("wordcount") or 0) or len(text.split())
    return text, wc


def attach_substack_text(creator: dict, meta_rows: list[dict], rng: random.Random) -> list[dict]:
    """Sample up to K free posts per creator-month and fetch bodies."""
    base = creator["base_url"]
    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in meta_rows:
        if row["is_paywalled"] == "false" and row.get("slug"):
            by_month[row["year_month"]].append(row)

    chosen: list[dict] = []
    for _ym, rows in by_month.items():
        if len(rows) <= K_TEXT_PER_MONTH:
            chosen.extend(rows)
        else:
            chosen.extend(rng.sample(rows, K_TEXT_PER_MONTH))

    text_rows: list[dict] = []
    chosen_ids = {r["post_id"] for r in chosen}
    for row in meta_rows:
        if row["post_id"] not in chosen_ids:
            continue
        try:
            body, wc = fetch_substack_body(base, row["slug"])
        except Exception as exc:  # noqa: BLE001
            print(f"  body fail {row.get('slug')}: {exc}")
            continue
        if len(body.split()) < 40:
            # likely paywalled/teaser despite audience flag
            continue
        row["has_full_text"] = "true"
        row["word_count"] = str(wc)
        row["text_source"] = "posts_api"
        text_rows.append(
            {
                "platform": "substack",
                "post_id": row["post_id"],
                "full_text": body,
                "text_source": "posts_api",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return text_rows


def scrape_medium_feed(creator: dict) -> tuple[list[dict], list[dict]]:
    time.sleep(SLEEP)
    raw = fetch(creator["feed_url"])
    root = ET.fromstring(raw)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    meta_rows, text_rows = [], []
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        link = (item.findtext("link") or "").split("?")[0]
        guid = item.findtext("guid") or link
        post_id = guid.rstrip("/").split("/")[-1]
        published = item.findtext("pubDate") or ""
        dt = parse_dt(published)
        if not in_window(dt):
            continue
        categories = [c.text for c in item.findall("category") if c.text]
        encoded = item.findtext("content:encoded", default="", namespaces=ns) or ""
        desc = item.findtext("description") or ""
        body = strip_html(encoded) if encoded else strip_html(desc)
        has_full = len(body.split()) >= 80
        meta_rows.append(
            {
                "platform": "medium",
                "creator_id": creator["creator_id"],
                "post_id": post_id,
                "slug": "",
                "url": link,
                "published_at": published,
                "year_month": year_month(dt),
                "title": title,
                "is_paywalled": "false",
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


def main() -> None:
    rng = random.Random(42)
    creators: list[dict] = []
    posts_meta: list[dict] = []
    posts_text: list[dict] = []
    errors: list[dict] = []

    print("=== Substack pilot ===")
    for c in SUBSTACK:
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
                "scrape_meta": "pilot_v1",
            }
        )
        try:
            print(f"- {c['display_name']} archive…")
            meta = scrape_substack_archive(c)
            print(f"  metadata posts={len(meta)}; fetching sampled free text…")
            text = attach_substack_text(c, meta, rng)
            posts_meta.extend(meta)
            posts_text.extend(text)
            print(f"  text_rows={len(text)}")
        except Exception as exc:  # noqa: BLE001
            errors.append({"platform": "substack", "creator": c["handle_or_slug"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    print("=== Medium pilot ===")
    for c in MEDIUM:
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
                "scrape_meta": "pilot_v1",
            }
        )
        try:
            print(f"- {c['display_name']}")
            meta, text = scrape_medium_feed(c)
            posts_meta.extend(meta)
            posts_text.extend(text)
            print(f"  posts={len(meta)} text_rows={len(text)}")
        except Exception as exc:  # noqa: BLE001
            errors.append({"platform": "medium", "creator": c["handle_or_slug"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    meta_fields = [
        "platform", "creator_id", "post_id", "url", "published_at", "year_month",
        "title", "is_paywalled", "has_full_text", "word_count", "tags",
        "claps_or_likes", "comments_count", "language", "audience", "text_source",
    ]
    # drop helper slug from written meta
    for r in posts_meta:
        r.pop("slug", None)

    write_csv(
        OUT / "creators.csv",
        creators,
        ["platform", "creator_id", "handle_or_slug", "display_name", "stratum",
         "topic_family", "native_categories_tags", "panel_weight", "url", "scrape_meta"],
    )
    write_csv(OUT / "posts_meta.csv", posts_meta, meta_fields)
    write_csv(
        OUT / "posts_text.csv",
        posts_text,
        ["platform", "post_id", "full_text", "text_source", "retrieved_at"],
    )
    write_csv(OUT / "scrape_errors.csv", errors, ["platform", "creator", "error"])

    from collections import Counter

    ym = Counter(r["year_month"] for r in posts_meta if r.get("year_month"))
    write_csv(
        OUT / "month_counts.csv",
        [{"year_month": k, "n_posts": v} for k, v in sorted(ym.items())],
        ["year_month", "n_posts"],
    )
    by_plat = Counter(r["platform"] for r in posts_meta)
    summary = [
        {"metric": "creators", "value": len(creators)},
        {"metric": "posts_meta", "value": len(posts_meta)},
        {"metric": "posts_text", "value": len(posts_text)},
        {"metric": "errors", "value": len(errors)},
        {"metric": "substack_posts", "value": by_plat.get("substack", 0)},
        {"metric": "medium_posts", "value": by_plat.get("medium", 0)},
        {"metric": "paywalled", "value": sum(1 for r in posts_meta if r["is_paywalled"] == "true")},
        {"metric": "has_full_text", "value": sum(1 for r in posts_meta if r["has_full_text"] == "true")},
    ]
    write_csv(OUT / "summary.csv", summary, ["metric", "value"])

    print(f"\nWrote CSVs to {OUT}")
    for s in summary:
        print(f"  {s['metric']}={s['value']}")


if __name__ == "__main__":
    main()
