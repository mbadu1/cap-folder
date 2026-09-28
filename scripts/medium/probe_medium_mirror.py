#!/usr/bin/env python3
"""One user-authorized MediumScraper test on a separately verified public story.

Imports the reviewed repository helpers; skips the CLI's PDF POST, keeps TLS
verification, records the original Medium URL separately, and makes one GET.
No result is automatically admitted to the primary text corpus.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from medium_mirror_adapter import parse_public_article, story_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--public-source-evidence", required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-json", type=Path, help="Parse a saved mirror response without network access.")
    args = parser.parse_args()
    if urlsplit(args.url).hostname != "medium.com":
        parser.error("This bounded probe accepts one medium.com story URL.")
    if args.output.exists():
        parser.error("Use a new output file; prior evidence is immutable.")
    spec = importlib.util.spec_from_file_location("reviewed_medium_scraper", args.source_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HEADERS["user-agent"] += " DukeCapstoneMediumPilot/0.2"
    mirror_url, _, post_id = module.parse_url_to_slugs(args.url)
    original_get = requests.get
    log = []
    raw = None

    def logged_get(url, **kwargs):
        nonlocal raw
        if log:
            raise RuntimeError("The mirror pilot permits exactly one GET.")
        if url != mirror_url or kwargs.get("verify") is False:
            raise RuntimeError("Unexpected route or disabled TLS verification.")
        row = {"url": url, "requested_at": datetime.now(timezone.utc).isoformat()}
        log.append(row)
        try:
            response = original_get(url, **kwargs, allow_redirects=False)
            row.update(
                status=response.status_code,
                content_type=response.headers.get("Content-Type"),
                cf_mitigated=response.headers.get("cf-mitigated"),
                retry_after=response.headers.get("Retry-After"),
                bytes=len(response.content),
                response_sha256=hashlib.sha256(response.content).hexdigest(),
            )
            if response.status_code != 200 or row["cf_mitigated"] == "challenge":
                raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
            raw = response.content
            return response
        except requests.RequestException as error:
            row.setdefault("status", 0)
            row["error_type"] = type(error).__name__
            raise

    result = {
        "adapter": "MediumScraper-29637a8-reference-graph-adapter-v2",
        "permission_basis": "user_reported_medium_permission_and_explicit_fallback_tool_request",
        "canonical_medium_url": args.url,
        "mirror_source_url": mirror_url,
        "public_source_evidence": args.public_source_evidence,
        "post_id_from_input_url": story_id(args.url),
        "upstream_filename_id_hint": post_id,
        "status": "unresolved",
        "request_log": log,
        "pdf_requested": False,
        "tls_verification": True,
        "full_text_primary_records": 0,
    }
    requests.get = logged_get
    try:
        if args.replay_json:
            raw = args.replay_json.read_bytes()
            payload = json.loads(raw)
        else:
            payload = module.fetch_json(mirror_url, verify_ssl=True)
        article = parse_public_article(payload, args.url, mirror_url)
        metadata = {key: value for key, value in article.items() if key not in {"markdown", "html"}}
        result.update(metadata)
        result.update(
            status="public_mirror_body_parsed" if "markdown" in article else "metadata_only_access_unverified",
            source_response_sha256=hashlib.sha256(raw).hexdigest(),
            replayed_from=str(args.replay_json) if args.replay_json else None,
        )
        if "markdown" in article:
            body = BeautifulSoup(article["html"], "html.parser")
            for element in body(["script", "style"]):
                element.decompose()
            text = body.get_text(" ", strip=True)
            result.update(
                markdown_characters=len(article["markdown"]),
                markdown_word_count=len(re.findall(r"\b\w+\b", article["markdown"])),
                markdown_sha256=hashlib.sha256(article["markdown"].encode()).hexdigest(),
                plain_text_word_count=len(re.findall(r"\b\w+\b", text)),
                plain_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
            args.cache_dir.mkdir(parents=True, exist_ok=True)
            (args.cache_dir / "response.json").write_bytes(raw)
            (args.cache_dir / "parsed_private.json").write_text(json.dumps(article, indent=2) + "\n")
    except Exception as error:
        result.update(status="mirror_retrieval_or_parse_failed", error_type=type(error).__name__, error=str(error))
    finally:
        requests.get = original_get
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
