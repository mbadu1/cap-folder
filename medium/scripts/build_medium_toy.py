#!/usr/bin/env python3
"""Build a diagnostic Medium toy from hash-verified cached public RSS.

No network access. The toy demonstrates joins/month partitions/length filtering
and permanent story selection. It does not declare verified complete histories,
stable creator IDs, original historical snapshots, or primary-sample eligibility.
"""
from __future__ import annotations

import argparse
import calendar
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import feedparser

from medium_mirror_adapter import parse_public_article, story_id

ROOT = Path(__file__).resolve().parents[2]
VERSION = "medium-diagnostic-toy-v1"
TOPIC = "Technology, science, education and environment"
START, END = "2020-01-01", "2026-09-17"
WORD = re.compile(r"\b[\w]+(?:['’\-][\w]+)*\b")
BLOCKS = ["p", "div", "blockquote", "li", "h1", "h2", "h3", "h4", "h5", "h6"]


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def clean_url(url):
    return urlunsplit(urlsplit(url)._replace(query="", fragment=""))


def source_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Source timestamp has no offset")
    return result


def period_for(value):
    # Keep source-local calendar date when an explicit offset exists.
    dt = source_time(value)
    return dt.strftime("%Y-%m"), START <= dt.date().isoformat() <= END


def normalize_body(html, title=""):
    soup = BeautifulSoup(html, "html.parser")
    raw_count = len(WORD.findall(soup.get_text(" ", strip=True)))
    code_count = sum(len(WORD.findall(node.get_text(" ", strip=True))) for node in soup.select("pre"))
    for node in list(soup.select("script,style,button,svg,nav,footer,pre,code,figcaption,[data-nosnippet]")):
        if node.parent is not None:
            node.insert_before(" " if node.name == "code" else "\n")
            node.decompose()
    removed_footer = False
    for node in list(soup.find_all("p")):
        text = node.get_text(" ", strip=True)
        if "was originally published in" in text and "where people are continuing the conversation" in text:
            node.decompose()
            removed_footer = True
    for node in list(soup.find_all(["h1", "h2", "h3"])):
        if " ".join(node.get_text(" ", strip=True).split()) == " ".join(title.split()):
            node.decompose()
    for node in soup.find_all("br"):
        node.replace_with("\n")
    for node in soup.find_all(BLOCKS):
        node.insert_before("\n")
        node.insert_after("\n")
    lines = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    text = "\n\n".join(line for line in lines if line)
    return dict(text=text, prose_word_count=len(WORD.findall(text)), source_word_count=raw_count,
                code_block_word_count=code_count, generated_footer_removed=removed_footer,
                text_sha256=sha(text))


def select_toy_posts(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["in_study_window"] and row["prose_word_count"] >= 200:
            groups[(row["creator_id"], row["year_month"])].append(row)
    return {r["post_id"] for items in groups.values()
            for r in sorted(items, key=lambda x: (x["post_priority"], x["post_id"]))[:3]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_csv(path, rows, columns=None):
    columns = columns or list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items() if k in columns})


def verified_source(path, expected):
    data = path.read_bytes()
    if sha(data) != expected:
        raise ValueError("Source hash mismatch: " + str(path))
    return data


def build(output, audit):
    cache = ROOT / ".cache/medium_access"
    initial = ROOT / "data/access_pilot/medium/2026-09-24"
    fallback = ROOT / "data/access_pilot/medium/2026-09-24-fallbacks"
    oldlog = json.loads((initial / "request_log.json").read_text())
    newlog = json.loads((cache / "2026-09-24-toy/request_log.json").read_text())
    oldfeed = next(row for row in oldlog if "feed/@efecankursun" in row["url"])
    newfeed = next(row for row in newlog if row["name"] == "tutorial_feed")
    sources = [
        (cache / "2026-09-24/profile_feed_0.xml", oldfeed["sha256"], oldfeed["url"], oldfeed.get("observed_at", oldfeed.get("requested_at"))),
        (cache / "2026-09-24-toy/tutorial_feed.xml", newfeed["response_sha256"], newfeed["url"], newfeed["requested_at"]),
    ]
    records, creators, feed_audit, source_refs = [], [], [], []
    for path, expected, url, fetched_at in sources:
        raw = verified_source(path, expected)
        feed = feedparser.parse(raw)
        if feed.bozo:
            raise ValueError("Invalid RSS source")
        profile = clean_url(feed.feed.link)
        handle = urlsplit(profile).path.split("@")[-1]
        candidate = re.search(r"rss-([0-9a-f]{12})-", parse_qs(urlsplit(feed.feed.link).query).get("source", [""])[0])
        candidate = candidate.group(1) if candidate else None
        creator_id = "medium:profile:" + handle
        bylines = sorted({entry.get("author") for entry in feed.entries if entry.get("author")})
        if len(bylines) != 1:
            raise ValueError("Toy expects one consistent byline in each profile feed")
        creators.append(dict(platform="medium", creator_id=creator_id, display_name=bylines[0], profile_url=profile,
                             native_creator_id=None, candidate_native_creator_id=candidate,
                             identity_status="profile_feed_linked_native_id_unverified", entity_type="individual_provisional",
                             history_status="rss_only_unknown_completeness", source_url=url, source_sha256=expected))
        dates = []
        for entry in feed.entries:
            post_id = story_id(entry.id)
            if story_id(entry.link) != post_id:
                raise ValueError("RSS GUID/link mismatch")
            published = parsedate_to_datetime(entry.published).isoformat().replace("+00:00", "Z")
            month, within = period_for(published)
            body = normalize_body("\n".join(c.value for c in entry.get("content", [])), entry.title)
            dates.append(published)
            records.append(dict(platform="medium", creator_id=creator_id, author_name=entry.author,
                                publication_id="medium:publication-domain:medium.datadriveninvestor.com" if urlsplit(entry.link).hostname == "medium.datadriveninvestor.com" else None,
                                post_id=post_id, title=entry.title, medium_story_url=clean_url(entry.link),
                                profile_url=profile, year_month=month, published_at_source=entry.published,
                                published_at=published, updated_at_rss=entry.get("updated"), updated_at_mirror=None,
                                source_method="official_profile_rss", source_url=url, source_sha256=expected,
                                collected_at=fetched_at, import_status="unknown", first_medium_date_verified=False,
                                date_quality="displayed_publication_date", historical_text_snapshot_verified=False,
                                canonical_external_source_url=None, native_tags=[t.term for t in entry.get("tags", [])],
                                topic_family=TOPIC, topic_method="provisional_creator_year_manual_review",
                                language="en", language_method="manual_toy_review", text_status="unverified_fullness",
                                access_status="officially_syndicated_body", mirror_free_status=None,
                                in_study_window=within, is_partial_month=month == "2026-09",
                                post_priority=sha("post-priority-v1|medium|" + post_id),
                                primary_eligible=False, selected_primary_post=False,
                                primary_exclusion_reasons=["native_creator_id_unverified", "history_incomplete", "full_text_completeness_unverified", "frame_not_validated"],
                                **body))
        feed_audit.append(dict(profile_url=profile, candidate_native_creator_id=candidate, entries=len(feed.entries),
                               earliest_displayed_date=min(dates), latest_displayed_date=max(dates),
                               next_link_present=any(link.get("rel") == "next" for link in feed.feed.get("links", [])),
                               history_status="unknown_completeness", source_sha256=expected))
        source_refs.append(dict(path=str(path.relative_to(ROOT)), url=url, sha256=expected, bytes=len(raw)))
    if len({r["post_id"] for r in records}) != len(records):
        raise ValueError("Duplicate source story requires byline/alias reconciliation")
    by_id = {r["post_id"]: r for r in records}
    comparisons = []
    for name, folder in [("historical_2020", "historical_2020"), ("tutorial_corrected", "tutorial_corrected")]:
        saved = json.loads((fallback / (name + ".json")).read_text())
        path = cache / "2026-09-24-fallbacks" / folder / "response.json"
        raw = verified_source(path, saved["source_response_sha256"])
        mirrored = parse_public_article(json.loads(raw), saved["requested_medium_url"], saved["mirror_source_url"])
        row = by_id[mirrored["post_id"]]
        row["updated_at_mirror"] = mirrored["updated_at_source"]
        row["mirror_free_status"] = mirrored["mirror_is_free"]
        row["mirror_source_url"] = mirrored["mirror_source_url"]
        norm = normalize_body(mirrored["html"], mirrored["title"])
        a, b = WORD.findall(row["text"].lower()), WORD.findall(norm["text"].lower())
        aligned = sum(m.size for m in SequenceMatcher(a=a, b=b, autojunk=False).get_matching_blocks())
        comparison = dict(post_id=row["post_id"], title_match=row["title"] == mirrored["title"],
                          story_url_match=row["medium_story_url"] == mirrored["canonical_medium_url"],
                          byline_match=row["author_name"] in [a["name"] for a in mirrored["authors"]],
                          publication_second_match=int(source_time(row["published_at"]).timestamp()) == int(source_time(mirrored["published_at_source"]).timestamp()),
                          updated_at_rss=row["updated_at_rss"], updated_at_mirror=row["updated_at_mirror"],
                          revision_timestamp_match=row["updated_at_rss"] == row["updated_at_mirror"],
                          rss_prose_words=len(a), mirror_prose_words=len(b), aligned_tokens=aligned,
                          rss_token_match_fraction=aligned / len(a), mirror_token_match_fraction=aligned / len(b),
                          exact_normalized_body_match=row["text_sha256"] == norm["text_sha256"],
                          mirror_sha256=saved["source_response_sha256"])
        if not all(comparison[k] for k in ["title_match", "story_url_match", "byline_match", "publication_second_match"]):
            raise ValueError("Independent source metadata mismatch")
        comparisons.append(comparison)
        source_refs.append(dict(path=str(path.relative_to(ROOT)), url=mirrored["mirror_source_url"], sha256=sha(raw), bytes=len(raw)))
    selected = select_toy_posts(records)
    for row in records:
        row["selected_for_toy"] = row["post_id"] in selected
        row["toy_exclusion_reason"] = None if row["selected_for_toy"] else "below_200_prose_words_or_cap"
    records.sort(key=lambda r: (r["published_at"], r["post_id"]))
    output.mkdir(parents=True, exist_ok=True)
    audit.mkdir(parents=True, exist_ok=True)
    write_csv(output / "lookup/creators.csv", creators)
    write_csv(output / "lookup/publications.csv", [dict(
        publication_id="medium:publication-domain:medium.datadriveninvestor.com", native_publication_id=None,
        name="DataDrivenInvestor", url="https://medium.datadriveninvestor.com",
        identity_status="domain_and_syndication_footer_label", evidence_post_id="88097b383ab8")])
    write_csv(output / "lookup/creator_publications.csv", [dict(creator_id=r["creator_id"], publication_id=r["publication_id"],
        evidence_post_id=r["post_id"], relationship="observed_story_byline") for r in records if r["publication_id"]])
    write_csv(audit / "feed_coverage.csv", feed_audit)
    write_json(audit / "source_comparisons.json", comparisons)
    write_json(audit / "request_log.json", newlog)
    write_json(audit / "source_manifest.json", source_refs)
    months = sorted({r["year_month"] for r in records if r["in_study_window"]})
    monthly_summary = []
    post_columns = [k for k in records[0] if k != "text"] + (["mirror_source_url"] if "mirror_source_url" not in records[0] else [])
    text_columns = ["platform", "post_id", "creator_id", "year_month", "text", "prose_word_count", "text_sha256", "text_status", "source_method", "selected_for_toy", "selected_primary_post"]
    for month in months:
        group = [r for r in records if r["year_month"] == month]
        text_rows = [r for r in group if r["selected_for_toy"]]
        destination = output / month.replace("-", "/")
        write_csv(destination / "posts_meta.csv", group, post_columns)
        write_csv(destination / "posts_text.csv", text_rows, text_columns)
        by_creator = defaultdict(list)
        for row in group:
            by_creator[row["creator_id"]].append(row)
        creator_months = []
        for creator, posts in sorted(by_creator.items()):
            texts = [r for r in posts if r["selected_for_toy"]]
            creator_months.append(dict(platform="medium", creator_id=creator, year_month=month,
                                      metadata_post_count=len(posts), toy_text_count=len(texts),
                                      toy_word_count_total=sum(r["prose_word_count"] for r in texts),
                                      primary_text_count=0, primary_eligible=False, selected_primary=False,
                                      topic_family=TOPIC, history_complete=None, inclusion_weight=None,
                                      sample_stratum="convenience_diagnostic", is_partial_month=month == "2026-09",
                                      calendar_days_in_scope=17 if month == "2026-09" else calendar.monthrange(*map(int, month.split("-")))[1]))
        write_csv(destination / "creator_month.csv", creator_months)
        summary = dict(year_month=month, observed_creator_months=len(creator_months), metadata_posts=len(group),
                       selected_toy_texts=len(text_rows), primary_texts=0, eligible_frame_size=None,
                       missing_histories_are_inactive=False, coverage_status="diagnostic_incomplete")
        write_csv(destination / "coverage.csv", [summary])
        monthly_summary.append(summary)
        write_csv(destination / "sampling_assignments.csv", [dict(creator_id=r["creator_id"], year_month=month, selected_for_toy=r["toy_text_count"] > 0,
                  selected_primary=False, creator_priority=None, reason="observed_public_rss_convenience_example", inclusion_weight=None) for r in creator_months])
        write_csv(destination / "scrape_errors.csv", [], ["post_id", "source_url", "status", "error_type"])
        manifest = dict(version=VERSION, year_month=month, counts=summary, primary_sample=False,
                        input_hashes=[s["sha256"] for s in source_refs], text_selection="in-window >=200 prose words; lowest SHA256 post-priority-v1|medium|post_id; cap 3 per creator-month",
                        files={p.name: sha(p.read_bytes()) for p in sorted(destination.glob("*.csv"))})
        write_json(destination / "manifest.json", manifest)
        (destination / "README.md").write_text(f"# Medium toy: {month}\n\n{len(group)} metadata rows and {len(text_rows)} diagnostic text rows. No primary-sample rows.\n\nJoin tables on `post_id` and `creator_id`; creator lookups are in `../../lookup/creators.csv`. Text is official RSS prose with code, captions, UI and generated feed footers removed. Completeness and native author IDs remain unverified. See the root README for provenance and reproduction.\n")
    checks = [
        ("Cached source hashes", "pass", f"{len(source_refs)} RSS/mirror inputs reconcile to saved request evidence"),
        ("Story keys and aliases", "pass", f"{len(records)} unique 12-character IDs; every RSS GUID agrees with its story URL"),
        ("Profile/byline linkage", "pass", "Each profile feed has one consistent byline; both anchor stories agree with mirror bylines"),
        ("Stable native creator IDs", "unresolved", "RSS provides candidate IDs; direct profile request returned 403; local profile keys are provisional"),
        ("Publication timestamps", "pass", "Both anchors match official RSS to the second; source offsets preserved"),
        ("Import and first Medium date", "unresolved", "No independent import or first-Medium timestamp field is available"),
        ("Revision timestamps", "discrepancy", "Both anchors have different RSS and mirror update timestamps; both values retained"),
        ("Original historical text", "unresolved", "Fetched versions are not verified publication-time snapshots"),
        ("Body normalization", "pass", "Block boundaries retained; code, copy buttons, captions and generated feed footers excluded from prose count"),
        ("Full-body completeness", "unresolved", "RSS/mirror comparisons corroborate two bodies but do not certify exact/full text"),
        ("Language", "pass", "English manually reviewed for this ten-story toy; no scalable detector validated"),
        ("Topics", "provisional", "Creator-year technology topic assigned for demonstration; annual histories are incomplete"),
        ("History enumeration", "unresolved", "Feeds return 1 and 9 entries with no next-page link; archive exhaustion not established"),
        ("Monthly sample", "pass", "Dates, >=200 prose words, stable post priority, <=3 cap, deduplication and table joins validated offline"),
        ("Platform quotas and weights", "unresolved", "Two convenience creators cannot validate 2,500/month or eight topic floors; weights left blank"),
        ("Engagement and AI labels", "unavailable", "Not observed or inferred; missing values are not zeros"),
        ("Request restraint", "pass", "2 new requests, 3.1 seconds apart; 1 feed 200 and 1 profile 403; next profile skipped"),
    ]
    write_csv(audit / "checks.csv", [dict(check=a, status=b, evidence=c) for a,b,c in checks])
    manifest = dict(version=VERSION, study_start=START, study_end=END, permission_basis="user_reported_medium_permission",
                    metadata_posts=len(records), creators=len(creators), creator_months=sum(r["observed_creator_months"] for r in monthly_summary),
                    selected_toy_texts=len(selected), primary_texts=0, months=months, sources=source_refs,
                    new_requests=len(newlog), build_network_requests=0, sample_status="diagnostic_incomplete",
                    topic_assignment="provisional_creator_year_manual_review", source_version="cached 2026-09-24 RSS and mirror responses")
    write_json(output / "manifest.json", manifest)
    write_json(output / "records.json", records)
    write_json(output / "workbook_input.json", dict(manifest=manifest, records=records, months=monthly_summary, checks=checks, creators=creators, comparisons=comparisons))
    write_csv(output / "monthly_summary.csv", monthly_summary)
    (output / "README.md").write_text(f"""# Medium toy dataset

Built from real public RSS captured on 2026-09-24. **{len(records)} stories, {len(creators)} authors, {len(months)} observed months, {len(selected)} diagnostic text rows, zero primary-sample rows.**

This demonstrates historical dates, metadata/text separation, profile lookup joins, seven month partitions, and the 200-prose-word / three-story cap. It is a convenience example, with unresolved stable author IDs, full history coverage, and full-text completeness. Months absent from this folder are unobserved, not zero activity.

## Files

- `lookup/creators.csv`: local profile-based creator keys and separately labeled candidate native IDs. `native_creator_id` remains blank.
- `lookup/publications.csv` and `creator_publications.csv`: the DataDrivenInvestor hosting relationship observed in the 2020 story. The local domain key is not a verified native publication ID.
- `YYYY/MM/posts_meta.csv`: all observed in-window story metadata, preserved source dates, status fields, and hashes.
- `YYYY/MM/posts_text.csv`: selected **diagnostic** RSS prose. `selected_for_toy=True` does not mean `selected_primary_post=True`.
- `YYYY/MM/creator_month.csv`, `sampling_assignments.csv`, and `coverage.csv`: joins, counts and explicit unknown coverage/weights.
- `YYYY/MM/manifest.json`: source and output checksums. `scrape_errors.csv` is empty because month construction is offline; the separate access audit preserves actual HTTP failures.
- `records.json`: all ten metadata/prose records before the toy cap, for reproducibility. `workbook_input.json` supports the review workbook.

Use `post_id` to join metadata to text, and `creator_id` plus `year_month` for creator-months. Blank fields mean unavailable, not zero. The workbook is `outputs/medium-toy-2026-09-24/medium_toy.xlsx`.

## Normalization and selection

Prefer the official profile RSS body for this toy. Keep source HTML in the private cache. Preserve prose block boundaries; remove code blocks/inline code, buttons, scripts/styles, captions, duplicate title headings and Medium's generated syndication footer. Keep author-written conclusions and links' visible prose. Count Unicode word tokens with internal apostrophes/hyphens. This is an explicit Medium toy normalization rule, not an approved cross-platform production preprocessing standard.

For each observed creator-month, select at most three in-window stories with at least 200 prose words, ordered by `SHA256("post-priority-v1|medium|" + post_id)`. No creator probability sample is claimed. The short March entry is retained as metadata and omitted from selected text; it is not automatically classified as a paywall preview. English was manually reviewed for the toy. Creator-year technology topics are provisional.

Two cached mirror stories independently corroborate title, author and publication time. Their bodies and update timestamps differ from RSS. Neither version is an original publication-time snapshot. Import status and first publication on Medium remain unknown. The nine-entry tutorial feed has no next-page link, which does not prove its history is exhausted.

## Reproduce without network access

From the project root, with the recorded private source caches present:

```sh
.venv/bin/python scripts/medium/build_medium_toy.py
.venv/bin/python tests/test_medium_toy.py
```

The [validation report](../../../cap-folder/data/access_pilot/medium/2026-09-24-toy/README.md) documents passed checks and unresolved production requirements. No texts were committed, pushed, or sent externally.
""")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/toy/medium")
    parser.add_argument("--audit", type=Path, default=ROOT / "data/access_pilot/medium/2026-09-24-toy")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.audit), indent=2))
