#!/usr/bin/env python3
"""Scrape public Medium full-text articles and sample a training corpus.

Rules:
- Public RSS only (no login, no paywall bypass)
- Keep only articles with real body text (>= MIN_WORDS)
- Drop member-only stubs ("Continue reading on Medium")
- Stratified sample by topic_family → train/val/test CSVs

Output: data/training/medium_*
"""

from __future__ import annotations

import csv
import hashlib
import random
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "training"
UA = "CapstoneResearchBot/0.4 (+academic training corpus; public RSS only)"
SLEEP = 1.5
MIN_WORDS = 200
TRAIN_FRAC, VAL_FRAC = 0.70, 0.15  # test = remainder
SEED = 42
WINDOW_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 2, tzinfo=timezone.utc)

# Prefer publications/authors that often put free full bodies in RSS
FEEDS = [
    # Tech
    ("medium:anildash", "anildash", "Anil Dash", "Technology, science, education and environment", "https://medium.com/feed/@anildash"),
    ("medium:ev", "ev", "Ev Williams", "Technology, science, education and environment", "https://medium.com/feed/@ev"),
    ("medium:towardsdatascience", "towardsdatascience", "Towards Data Science", "Technology, science, education and environment", "https://medium.com/feed/towards-data-science"),
    ("medium:betterprogramming", "betterprogramming", "Better Programming", "Technology, science, education and environment", "https://medium.com/feed/better-programming"),
    ("medium:javascriptscene", "javascriptscene", "JavaScript Scene", "Technology, science, education and environment", "https://medium.com/feed/javascript-scene"),
    ("medium:gitconnected", "gitconnected", "Level Up Coding", "Technology, science, education and environment", "https://medium.com/feed/gitconnected"),
    ("medium:onezero", "onezero", "OneZero", "Technology, science, education and environment", "https://medium.com/feed/one-zero"),
    ("medium:uxdesign", "uxdesign", "UX Collective", "Arts, culture, media and design", "https://medium.com/feed/user-experience-design-1"),
    ("medium:hackernoon", "hackernoon", "HackerNoon", "Technology, science, education and environment", "https://medium.com/feed/hackernoon"),
    ("medium:codenewbie", "codenewbie", "CodeNewbie", "Technology, science, education and environment", "https://medium.com/feed/codenewbie"),
    # Business
    ("medium:jasonfried", "jasonfried", "Jason Fried", "Business, finance and economics", "https://medium.com/feed/@jasonfried"),
    ("medium:aytekintank", "aytekintank", "Aytekin Tank", "Business, finance and economics", "https://medium.com/feed/@aytekintank"),
    ("medium:swlh", "swlh", "The Startup", "Business, finance and economics", "https://medium.com/feed/swlh"),
    ("medium:theascent", "theascent", "The Ascent", "Business, finance and economics", "https://medium.com/feed/the-ascent"),
    ("medium:marker", "marker", "Marker", "Business, finance and economics", "https://medium.com/feed/marker"),
    ("medium:entrepreneurshandbook", "entrepreneurshandbook", "Entrepreneur's Handbook", "Business, finance and economics", "https://medium.com/feed/entrepreneurs-handbook"),
    ("medium:timdenning", "timdenning", "Tim Denning", "Business, finance and economics", "https://medium.com/feed/@timdenning"),
    # Society / ideas
    ("medium:umairh", "umairh", "Umair Haque", "Society, history and ideas", "https://medium.com/feed/@umairh"),
    ("medium:gen", "gen", "GEN", "Society, history and ideas", "https://medium.com/feed/gen"),
    ("medium:illumination", "illumination", "ILLUMINATION", "Society, history and ideas", "https://medium.com/feed/illumination"),
    ("medium:curious", "curious", "Curious", "Society, history and ideas", "https://medium.com/feed/curious"),
    ("medium:personalgrowth", "personal-growth", "Personal Growth", "Society, history and ideas", "https://medium.com/feed/personal-growth"),
    # Health / personal
    ("medium:zulie", "zulie", "Zulie Rane", "Health, family and personal life", "https://medium.com/feed/@zulie"),
    ("medium:betterhumans", "betterhumans", "Better Humans", "Health, family and personal life", "https://medium.com/feed/better-humans"),
    ("medium:humanparts", "humanparts", "Human Parts", "Health, family and personal life", "https://medium.com/feed/human-parts"),
    ("medium:forge", "forge", "Forge", "Health, family and personal life", "https://medium.com/feed/forge"),
    ("medium:mindcafe", "mindcafe", "Mind Cafe", "Health, family and personal life", "https://medium.com/feed/mind-cafe"),
    ("medium:krisgage", "krisgage", "Kris Gage", "Health, family and personal life", "https://medium.com/feed/@krisgage"),
    # Arts / creative / lifestyle
    ("medium:zora", "zora", "ZORA", "Arts, culture, media and design", "https://medium.com/feed/zora"),
    ("medium:thebolditalic", "thebolditalic", "The Bold Italic", "Arts, culture, media and design", "https://medium.com/feed/the-bold-italic"),
    ("medium:modus", "modus", "Modus", "Arts, culture, media and design", "https://medium.com/feed/modus"),
    ("medium:nicolascole77", "nicolascole77", "Nicolas Cole", "Literature and creative writing", "https://medium.com/feed/@nicolascole77"),
    ("medium:writingcooperative", "writingcooperative", "The Writing Cooperative", "Literature and creative writing", "https://medium.com/feed/the-writing-cooperative"),
    ("medium:heated", "heated", "Heated", "Lifestyle, food, travel, home and leisure", "https://medium.com/feed/heated"),
    ("medium:psiloveyou", "psiloveyou", "PS I Love You", "Health, family and personal life", "https://medium.com/feed/psiloveyou"),
]


def fetch(url: str, retries: int = 4) -> bytes:
    for i in range(retries):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, */*"})
            with urlopen(req, timeout=40) as resp:
                return resp.read()
        except HTTPError as e:
            if e.code == 429 and i < retries - 1:
                wait = 12 * (i + 1)
                print(f"    429 backoff {wait}s")
                time.sleep(wait)
                continue
            raise


def strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_dt(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(raw.replace("GMT", "+0000"), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def is_usable_body(body: str) -> bool:
    if not body:
        return False
    words = body.split()
    if len(words) < MIN_WORDS:
        return False
    low = body.lower()
    if "continue reading on medium" in low:
        return False
    if "create an account to read the full story" in low:
        return False
    if "member-only story" in low and len(words) < 400:
        return False
    return True


def scrape_feed(creator_id: str, feed_url: str) -> list[dict]:
    raw = fetch(feed_url)
    root = ET.fromstring(raw)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    rows = []
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
        if not is_usable_body(body):
            continue
        rows.append(
            {
                "platform": "medium",
                "creator_id": creator_id,
                "post_id": post_id,
                "url": link,
                "published_at": published,
                "year_month": dt.strftime("%Y-%m"),
                "title": title,
                "tags": "|".join(cats),
                "word_count": str(len(body.split())),
                "full_text": body,
                "text_source": "rss_full",
                "label": "human",  # public Medium longform; for detector training as human class
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return rows


def stratified_split(rows: list[dict], rng: random.Random) -> dict[str, list[dict]]:
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_topic[r["topic_family"]].append(r)
    splits = {"train": [], "val": [], "test": []}
    for topic, items in by_topic.items():
        items = items[:]
        rng.shuffle(items)
        n = len(items)
        n_train = max(1, int(n * TRAIN_FRAC)) if n >= 3 else n
        n_val = int(n * VAL_FRAC) if n >= 5 else (1 if n >= 3 else 0)
        # ensure leftovers go to test when possible
        if n >= 3 and n_train + n_val >= n:
            n_val = max(0, n - n_train - 1)
        train = items[:n_train]
        val = items[n_train : n_train + n_val]
        test = items[n_train + n_val :]
        splits["train"].extend(train)
        splits["val"].extend(val)
        splits["test"].extend(test)
    return splits


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    rng = random.Random(SEED)
    creators = []
    articles: list[dict] = []
    errors = []

    print(f"Scraping {len(FEEDS)} Medium feeds for full-text training data…")
    for cid, handle, name, topic, url in FEEDS:
        creators.append(
            {
                "platform": "medium",
                "creator_id": cid,
                "handle_or_slug": handle,
                "display_name": name,
                "stratum": "elite",
                "topic_family": topic,
                "url": url,
            }
        )
        try:
            time.sleep(SLEEP)
            rows = scrape_feed(cid, url)
            for r in rows:
                r["topic_family"] = topic
                r["display_name"] = name
                r["content_hash"] = content_hash(r["full_text"])
            articles.extend(rows)
            print(f"  OK  {name}: full_text={len(rows)}")
        except (HTTPError, URLError, ET.ParseError) as exc:
            errors.append({"creator": handle, "error": str(exc)})
            print(f"  ERR {name}: {exc}")

    # dedupe by post_id and content hash
    seen_id, seen_hash = set(), set()
    deduped = []
    for r in articles:
        if r["post_id"] in seen_id or r["content_hash"] in seen_hash:
            continue
        seen_id.add(r["post_id"])
        seen_hash.add(r["content_hash"])
        deduped.append(r)
    articles = deduped

    fields = [
        "split", "platform", "creator_id", "display_name", "topic_family", "post_id",
        "url", "published_at", "year_month", "title", "tags", "word_count",
        "label", "content_hash", "text_source", "retrieved_at", "full_text",
    ]

    splits = stratified_split(articles, rng)
    all_rows = []
    for split_name, rows in splits.items():
        for r in rows:
            r = dict(r)
            r["split"] = split_name
            all_rows.append(r)

    write_csv(OUT / "medium_creators.csv", creators,
              ["platform", "creator_id", "handle_or_slug", "display_name", "stratum", "topic_family", "url"])
    write_csv(OUT / "medium_articles_full.csv", all_rows, fields)
    write_csv(OUT / "medium_train.csv", [r for r in all_rows if r["split"] == "train"], fields)
    write_csv(OUT / "medium_val.csv", [r for r in all_rows if r["split"] == "val"], fields)
    write_csv(OUT / "medium_test.csv", [r for r in all_rows if r["split"] == "test"], fields)
    write_csv(OUT / "scrape_errors.csv", errors, ["creator", "error"])

    # compact metadata without full text (easier to open)
    meta_fields = [f for f in fields if f != "full_text"]
    write_csv(OUT / "medium_articles_meta.csv", all_rows, meta_fields)

    by_topic = Counter(r["topic_family"] for r in all_rows)
    by_split = Counter(r["split"] for r in all_rows)
    summary = [
        {"metric": "feeds_attempted", "value": len(FEEDS)},
        {"metric": "feeds_errors", "value": len(errors)},
        {"metric": "full_text_articles", "value": len(all_rows)},
        {"metric": "train", "value": by_split.get("train", 0)},
        {"metric": "val", "value": by_split.get("val", 0)},
        {"metric": "test", "value": by_split.get("test", 0)},
        {"metric": "min_words", "value": MIN_WORDS},
        {"metric": "median_words", "value": sorted(int(r["word_count"]) for r in all_rows)[len(all_rows)//2] if all_rows else 0},
        {"metric": "label", "value": "human"},
    ]
    for t, n in sorted(by_topic.items()):
        summary.append({"metric": f"topic::{t}", "value": n})
    write_csv(OUT / "summary.csv", summary, ["metric", "value"])

    readme = f"""# Medium training sample

Public Medium longform articles with **real full text** (RSS `content:encoded`), filtered to >= {MIN_WORDS} words.
Member-only stubs excluded. No paywall bypass.

- `medium_articles_full.csv` — all sampled articles + `full_text`
- `medium_train.csv` / `medium_val.csv` / `medium_test.csv` — stratified 70/15/15 by topic
- `medium_articles_meta.csv` — same rows without body (for browsing)
- `label` = `human` (for detector training as the human class)

Generated: {datetime.now(timezone.utc).isoformat()}
Articles: {len(all_rows)} | train={by_split.get('train',0)} val={by_split.get('val',0)} test={by_split.get('test',0)}
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    print(f"\nWrote training corpus → {OUT}")
    for s in summary[:8]:
        print(f"  {s['metric']}={s['value']}")


if __name__ == "__main__":
    main()
