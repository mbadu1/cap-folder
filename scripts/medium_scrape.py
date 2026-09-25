#!/usr/bin/env python3
"""Medium-focused pilot scrape via public RSS feeds.

HTML/JSON/GraphQL are Cloudflare-blocked from this environment.
RSS reliably returns ~10 recent posts per feed with body when public.
"""

from __future__ import annotations

import csv
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "pilot"
UA = "CapstoneResearchBot/0.3 (+academic medium scrape; research use)"
SLEEP = 0.8
WINDOW_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 25, tzinfo=timezone.utc)

# Broader elite / topic coverage — users + major publications
MEDIUM_FEEDS = [
    # Tech / science
    ("medium:tomaspueyo", "tomaspueyo", "Tomas Pueyo", "Technology, science, education and environment", "https://medium.com/feed/@tomaspueyo"),
    ("medium:anildash", "anildash", "Anil Dash", "Technology, science, education and environment", "https://medium.com/feed/@anildash"),
    ("medium:ev", "ev", "Ev Williams", "Technology, science, education and environment", "https://medium.com/feed/@ev"),
    ("medium:matthewcassinelli", "matthewcassinelli", "Matthew Cassinelli", "Technology, science, education and environment", "https://medium.com/feed/@matthewcassinelli"),
    ("medium:towardsdatascience", "towardsdatascience", "Towards Data Science", "Technology, science, education and environment", "https://medium.com/feed/towards-data-science"),
    ("medium:uxdesign", "uxdesigncc", "UX Collective", "Arts, culture, media and design", "https://medium.com/feed/user-experience-design-1"),
    ("medium:javascriptscene", "javascriptscene", "JavaScript Scene", "Technology, science, education and environment", "https://medium.com/feed/javascript-scene"),
    ("medium:levelupgitconnected", "levelup", "Level Up Coding", "Technology, science, education and environment", "https://medium.com/feed/gitconnected"),
    ("medium:betterprogramming", "betterprogramming", "Better Programming", "Technology, science, education and environment", "https://medium.com/feed/better-programming"),
    ("medium:thewritingcooperative", "writingcooperative", "The Writing Cooperative", "Literature and creative writing", "https://medium.com/feed/the-writing-cooperative"),
    # Business
    ("medium:aytekintank", "aytekintank", "Aytekin Tank", "Business, finance and economics", "https://medium.com/feed/@aytekintank"),
    ("medium:timdenning", "timdenning", "Tim Denning", "Business, finance and economics", "https://medium.com/feed/@timdenning"),
    ("medium:jasonfried", "jasonfried", "Jason Fried", "Business, finance and economics", "https://medium.com/feed/@jasonfried"),
    ("medium:theascent", "theascent", "The Ascent", "Business, finance and economics", "https://medium.com/feed/the-ascent"),
    ("medium:entrepreneurshandbook", "entrepreneurshandbook", "Entrepreneur's Handbook", "Business, finance and economics", "https://medium.com/feed/entrepreneurs-handbook"),
    ("medium:swlh", "swlh", "The Startup", "Business, finance and economics", "https://medium.com/feed/swlh"),
    ("medium:marker", "marker", "Marker", "Business, finance and economics", "https://medium.com/feed/marker"),
    # Ideas / society / politics
    ("medium:umairh", "umairh", "Umair Haque", "Society, history and ideas", "https://medium.com/feed/@umairh"),
    ("medium:gen", "gen", "GEN", "Society, history and ideas", "https://medium.com/feed/gen"),
    ("medium:zora", "zora", "ZORA", "Arts, culture, media and design", "https://medium.com/feed/zora"),
    ("medium:onezero", "onezero", "OneZero", "Technology, science, education and environment", "https://medium.com/feed/one-zero"),
    ("medium:humanparts", "humanparts", "Human Parts", "Health, family and personal life", "https://medium.com/feed/human-parts"),
    # Health / personal / lifestyle
    ("medium:zulie", "zulie", "Zulie Rane", "Health, family and personal life", "https://medium.com/feed/@zulie"),
    ("medium:betterhumans", "betterhumans", "Better Humans", "Health, family and personal life", "https://medium.com/feed/better-humans"),
    ("medium:forge", "forge", "Forge", "Health, family and personal life", "https://medium.com/feed/forge"),
    ("medium:elemental", "elemental", "Elemental", "Health, family and personal life", "https://medium.com/feed/elemental"),
    ("medium:psiloveyou", "psiloveyou", "PS I Love You", "Health, family and personal life", "https://medium.com/feed/psiloveyou"),
    ("medium:mindcafe", "mindcafe", "Mind Cafe", "Health, family and personal life", "https://medium.com/feed/mind-cafe"),
    # Creative / culture
    ("medium:personalgrowth", "personal-growth", "Personal Growth", "Society, history and ideas", "https://medium.com/feed/personal-growth"),
    ("medium:illumination", "illumination", "ILLUMINATION", "Society, history and ideas", "https://medium.com/feed/illumination"),
    ("medium:curious", "curious", "Curious", "Society, history and ideas", "https://medium.com/feed/curious"),
    ("medium:thebolditalic", "thebolditalic", "The Bold Italic", "Arts, culture, media and design", "https://medium.com/feed/the-bold-italic"),
    ("medium:modus", "modus", "Modus", "Arts, culture, media and design", "https://medium.com/feed/modus"),
    ("medium:heated", "heated", "Heated", "Lifestyle, food, travel, home and leisure", "https://medium.com/feed/heated"),
    # Extra writers
    ("medium:krisgage", "krisgage", "Kris Gage", "Health, family and personal life", "https://medium.com/feed/@krisgage"),
    ("medium:nicolascole77", "nicolascole77", "Nicolas Cole", "Literature and creative writing", "https://medium.com/feed/@nicolascole77"),
    ("medium:shauntago", "shauntago", "Shaun Go", "Business, finance and economics", "https://medium.com/feed/@shauntago"),
    ("medium:alltopstartups", "alltopstartups", "All Top Startups", "Business, finance and economics", "https://medium.com/feed/@alltopstartups"),
    ("medium:benjaminhardman", "benjaminhardman", "Benjamin Hardman", "Arts, culture, media and design", "https://medium.com/feed/@benjaminhardman"),
    ("medium:ryangrimsland", "ryangrimsland", "Ryan Grimsland", "Technology, science, education and environment", "https://medium.com/feed/@ryangrimsland"),
]


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, */*"})
    with urlopen(req, timeout=40) as resp:
        return resp.read()


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_dt(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            dt = datetime.strptime(raw.replace("GMT", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def scrape_feed(creator_id: str, feed_url: str) -> tuple[list[dict], list[dict]]:
    raw = fetch(feed_url)
    root = ET.fromstring(raw)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    meta, text = [], []
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        link = (item.findtext("link") or "").split("?")[0]
        guid = item.findtext("guid") or link
        post_id = guid.rstrip("/").split("/")[-1]
        published = item.findtext("pubDate") or ""
        dt = parse_dt(published)
        if dt is None or not (WINDOW_START <= dt <= WINDOW_END):
            continue
        cats = [c.text for c in item.findall("category") if c.text]
        enc = item.findtext("content:encoded", default="", namespaces=ns) or ""
        desc = item.findtext("description") or ""
        body = strip_html(enc if enc else desc)
        has_full = len(body.split()) >= 80
        meta.append(
            {
                "platform": "medium",
                "creator_id": creator_id,
                "post_id": post_id,
                "url": link,
                "published_at": published,
                "year_month": dt.strftime("%Y-%m"),
                "title": title,
                "is_paywalled": "false",
                "has_full_text": str(has_full).lower(),
                "word_count": str(len(body.split())) if body else "",
                "tags": "|".join(cats),
                "claps_or_likes": "",
                "comments_count": "",
                "language": "",
                "audience": "",
                "text_source": "rss",
            }
        )
        if body:
            text.append(
                {
                    "platform": "medium",
                    "post_id": post_id,
                    "full_text": body,
                    "text_source": "rss",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    return meta, text


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    creators = read_csv(OUT / "creators.csv")
    posts_meta = read_csv(OUT / "posts_meta.csv")
    posts_text = read_csv(OUT / "posts_text.csv")
    errors = read_csv(OUT / "scrape_errors.csv")

    # Drop existing medium rows so this run replaces Medium slice cleanly
    medium_ids = {c[0] for c in MEDIUM_FEEDS}
    posts_meta = [r for r in posts_meta if r.get("platform") != "medium"]
    posts_text = [r for r in posts_text if r.get("platform") != "medium"]
    creators = [c for c in creators if c.get("platform") != "medium" or c.get("creator_id") not in medium_ids]
    # remove prior medium creators entirely; we'll rewrite medium creator set
    creators = [c for c in creators if c.get("platform") != "medium"]
    errors = [e for e in errors if e.get("platform") != "medium"]

    new_meta: list[dict] = []
    new_text: list[dict] = []
    new_creators: list[dict] = []

    print(f"Scraping {len(MEDIUM_FEEDS)} Medium feeds…")
    for cid, handle, name, topic, url in MEDIUM_FEEDS:
        new_creators.append(
            {
                "platform": "medium",
                "creator_id": cid,
                "handle_or_slug": handle,
                "display_name": name,
                "stratum": "elite",
                "topic_family": topic,
                "native_categories_tags": "",
                "panel_weight": "1",
                "url": url,
                "scrape_meta": "medium_pilot_v2",
            }
        )
        try:
            time.sleep(SLEEP)
            meta, text = scrape_feed(cid, url)
            new_meta.extend(meta)
            new_text.extend(text)
            print(f"  OK  {name}: posts={len(meta)} text={len(text)}")
        except (HTTPError, URLError, ET.ParseError) as exc:
            errors.append({"platform": "medium", "creator": handle, "error": str(exc)})
            print(f"  ERR {name}: {exc}")

    creators.extend(new_creators)
    posts_meta.extend(new_meta)
    posts_text.extend(new_text)

    # dedupe
    seen = set()
    dedup_meta = []
    for r in posts_meta:
        k = (r["platform"], r["post_id"])
        if k in seen:
            continue
        seen.add(k)
        dedup_meta.append(r)
    posts_meta = dedup_meta

    seen = set()
    dedup_text = []
    for r in posts_text:
        k = (r["platform"], r["post_id"])
        if k in seen:
            continue
        seen.add(k)
        dedup_text.append(r)
    posts_text = dedup_text

    write_csv(
        OUT / "creators.csv",
        creators,
        ["platform", "creator_id", "handle_or_slug", "display_name", "stratum",
         "topic_family", "native_categories_tags", "panel_weight", "url", "scrape_meta"],
    )
    write_csv(
        OUT / "posts_meta.csv",
        posts_meta,
        ["platform", "creator_id", "post_id", "url", "published_at", "year_month",
         "title", "is_paywalled", "has_full_text", "word_count", "tags",
         "claps_or_likes", "comments_count", "language", "audience", "text_source"],
    )
    write_csv(
        OUT / "posts_text.csv",
        posts_text,
        ["platform", "post_id", "full_text", "text_source", "retrieved_at"],
    )
    write_csv(OUT / "scrape_errors.csv", errors, ["platform", "creator", "error"])

    # Medium-only exports for easy inspection
    write_csv(
        OUT / "medium_creators.csv",
        new_creators,
        ["platform", "creator_id", "handle_or_slug", "display_name", "stratum",
         "topic_family", "native_categories_tags", "panel_weight", "url", "scrape_meta"],
    )
    write_csv(
        OUT / "medium_posts_meta.csv",
        new_meta,
        ["platform", "creator_id", "post_id", "url", "published_at", "year_month",
         "title", "is_paywalled", "has_full_text", "word_count", "tags",
         "claps_or_likes", "comments_count", "language", "audience", "text_source"],
    )
    write_csv(
        OUT / "medium_posts_text.csv",
        new_text,
        ["platform", "post_id", "full_text", "text_source", "retrieved_at"],
    )

    by = Counter(r["platform"] for r in posts_meta)
    ym = Counter(r["year_month"] for r in new_meta if r.get("year_month"))
    write_csv(
        OUT / "medium_month_counts.csv",
        [{"year_month": k, "n_posts": v} for k, v in sorted(ym.items())],
        ["year_month", "n_posts"],
    )
    summary = [
        {"metric": "creators_total", "value": len(creators)},
        {"metric": "posts_meta_total", "value": len(posts_meta)},
        {"metric": "posts_text_total", "value": len(posts_text)},
        {"metric": "medium_creators", "value": len(new_creators)},
        {"metric": "medium_posts", "value": len(new_meta)},
        {"metric": "medium_text_rows", "value": len(new_text)},
        {"metric": "medium_errors", "value": sum(1 for e in errors if e.get("platform") == "medium")},
        {"metric": "substack_posts", "value": by.get("substack", 0)},
    ]
    write_csv(OUT / "summary.csv", summary, ["metric", "value"])

    print("\nMedium scrape done → data/pilot/")
    for s in summary:
        print(f"  {s['metric']}={s['value']}")
    if ym:
        print(f"  medium months: {min(ym)} → {max(ym)}")


if __name__ == "__main__":
    main()
