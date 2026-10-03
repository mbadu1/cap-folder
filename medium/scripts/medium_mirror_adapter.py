"""Decode explicitly addressed article fields from a Freedium SvelteKit payload.

The upstream scraper guesses by scanning strings. This adapter instead resolves
the response's reference graph and separates original-source metadata from the
mirror transport. Only a positively free article exposes a body to the caller.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit


def story_id(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    # Medium's own sitemap contains shorter hexadecimal IDs (e.g. 11 digits).
    # Preserve their spelling; padding or dropping them loses real stories.
    match = re.search(r"(?:/p/|-)([a-f0-9]{1,12})$", path)
    if not match:
        raise ValueError("A hexadecimal Medium story ID of at most 12 characters is required.")
    return match.group(1)


def decode_references(values: list, reference: int = 0, chunks=None, resolving=None):
    active = set()
    memo = {}
    chunks = {} if chunks is None else chunks
    resolving = set() if resolving is None else resolving

    def decode(ref):
        if not isinstance(ref, int) or isinstance(ref, bool):
            raise ValueError("Unexpected non-integer reference in SvelteKit payload.")
        if ref == -1:
            return None  # undefined; missing metadata stays missing
        if ref < 0 or ref >= len(values):
            raise ValueError("Out-of-range or unsupported serialized reference.")
        if ref in active:
            raise ValueError("Cyclic payload is outside this adapter's supported schema.")
        if ref in memo:
            return memo[ref]
        active.add(ref)
        value = values[ref]
        if isinstance(value, dict):
            resolved = {key: decode(child) for key, child in value.items()}
        elif isinstance(value, list):
            if len(value) == 2 and value[0] == "Promise":
                chunk_id = decode(value[1])
                if not isinstance(chunk_id, int) or isinstance(chunk_id, bool) or chunk_id not in chunks:
                    raise ValueError("Missing deferred article chunk.")
                if chunk_id in resolving:
                    raise ValueError("Cyclic deferred article chunk.")
                chunk = chunks[chunk_id]
                if "error" in chunk:
                    raise ValueError("Missing or failed eager article response: deferred source error.")
                resolving.add(chunk_id)
                resolved = decode_references(chunk["data"],chunks=chunks,resolving=resolving)
                resolving.remove(chunk_id)
            else:
                resolved = [decode(child) for child in value]
        else:
            resolved = value
        active.remove(ref)
        memo[ref] = resolved
        return resolved

    return decode(reference)


def parse_public_response(raw: bytes, requested_url: str, mirror_url: str) -> dict:
    """Read eager JSON or SvelteKit data/chunk records without guessing body fields.

    Protocol: sveltejs/kit runtime/server/page/data_serializer.js. Deferred
    references are addressed by chunk ID, never by scanning for long strings.
    """
    try:
        records = [json.loads(raw)]
    except json.JSONDecodeError:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not records or not isinstance(records[0],dict):
        raise ValueError("Missing initial mirror data record.")
    chunks = {}
    for record in records[1:]:
        chunk_id = record.get("id")
        if record.get("type") != "chunk" or not isinstance(chunk_id,int) or isinstance(chunk_id,bool) or chunk_id in chunks:
            raise ValueError("Unexpected or duplicate mirror chunk.")
        if not isinstance(record.get("data",record.get("error")),list):
            raise ValueError("Invalid mirror chunk reference graph.")
        chunks[chunk_id] = record
    return parse_public_article(records[0],requested_url,mirror_url,chunks=chunks)


def parse_public_article(payload: dict, requested_url: str, mirror_url: str, chunks=None) -> dict:
    nodes = payload.get("nodes", [])
    if len(nodes) < 2 or not isinstance(nodes[1], dict):
        raise ValueError("Missing article data node.")
    values = nodes[1].get("data")
    if not isinstance(values, list) or not values:
        raise ValueError("Missing serialized reference graph.")
    root = decode_references(values,chunks=chunks)
    eager = root.get("eager") if isinstance(root, dict) else None
    response_mode = "eager"
    if eager is None and isinstance(root,dict):
        eager = root.get("streamed")
        response_mode = "streamed"
    if not isinstance(eager, dict) or eager.get("error"):
        raise ValueError("Missing or failed eager article response.")
    article = eager.get("article")
    if not isinstance(article, dict):
        raise ValueError("Missing explicit article metadata.")
    original_url = root.get("originalUrl")
    article_url = article.get("url")
    requested_id = story_id(requested_url)
    if not isinstance(original_url, str) or story_id(original_url) != requested_id:
        raise ValueError("Mirror response does not identify the requested story.")
    if not isinstance(article_url, str) or story_id(article_url) != requested_id:
        raise ValueError("Article URL does not identify the requested story.")
    if urlsplit(article_url).scheme != "https":
        raise ValueError("Unexpected non-HTTPS article URL.")
    result = {
        "platform": "medium",
        "post_id": requested_id,
        "requested_medium_url": requested_url,
        "canonical_medium_url": article_url,
        "mirror_source_url": mirror_url,
        "source_method": "freedium_mirror_public_story",
        "response_mode": response_mode,
        "title": article.get("title"),
        "authors": article.get("authors") or [],
        "published_at_source": article.get("publishedAt"),
        "updated_at_source": article.get("updatedAt"),
        "mirror_is_free": article.get("isFree"),
        "identity_quality": "display_name_only",
        "import_status": "unknown",
        "text_status": "not_retained_access_unverified",
        "selected_primary_post": False,
    }
    if article.get("isFree") is not True:
        return result
    markdown = eager.get("markdown")
    html = eager.get("html")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("No explicit Markdown body; heuristic string fallback is disabled.")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("No explicit HTML body to cross-check extraction.")
    result.update(
        text_status="mirror_public_body_unverified_completeness",
        markdown=markdown,
        html=html,
    )
    return result
