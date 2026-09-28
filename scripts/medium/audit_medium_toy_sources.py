#!/usr/bin/env python3
"""Bounded identity/feed checks for the two Medium toy authors.

No retries or redirects. Existing cached requests are replayed, and challenged
profile routes stop further profile fetches. Full responses stay in .cache.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import requests


TARGETS = [
    ("tutorial_feed", "feed", "https://medium.com/feed/@mrityunjaytiwari1873"),
    ("historical_profile", "profile", "https://medium.com/@efecankursun"),
    ("tutorial_profile", "profile", "https://medium.com/@mrityunjaytiwari1873"),
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--cache-dir", type=Path, default=Path(__file__).resolve().parents[2] / ".cache/medium_access/2026-09-24-toy")
    args = p.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    logpath = args.cache_dir / "request_log.json"
    logs = json.loads(logpath.read_text()) if logpath.exists() else []
    done = {r["name"] for r in logs}
    blocked = {r["route"] for r in logs if r.get("status") != 200 or r.get("cf_mitigated") == "challenge"}
    previous = 0.0
    if args.live:
        for name, route, url in TARGETS:
            if name in done or route in blocked:
                continue
            time.sleep(max(0, 3.1 - (time.monotonic() - previous)))
            previous = time.monotonic()
            row = {"name": name, "route": route, "url": url, "requested_at": datetime.now(timezone.utc).isoformat()}
            try:
                response = requests.get(url, headers={"User-Agent": "DukeCapstoneMediumPilot/0.3 (user-reported research permission; bounded public-source checks)", "Accept": "application/rss+xml,text/html;q=0.9,*/*;q=0.5"}, timeout=25, allow_redirects=False)
                row.update(status=response.status_code, bytes=len(response.content), response_sha256=hashlib.sha256(response.content).hexdigest(), cf_mitigated=response.headers.get("cf-mitigated"), content_type=response.headers.get("content-type"), retry_after=response.headers.get("retry-after"), location=response.headers.get("location"))
                if response.status_code == 200:
                    filename = name + (".xml" if route == "feed" else ".html")
                    (args.cache_dir / filename).write_bytes(response.content)
                    row["cache_file"] = filename
            except requests.RequestException as error:
                row.update(status=0, error_type=type(error).__name__)
            if row.get("status") != 200 or row.get("cf_mitigated") == "challenge":
                blocked.add(route)
            logs.append(row)
            logpath.write_text(json.dumps(logs, indent=2) + "\n")
    for row in logs:
        if row.get("cache_file"):
            raw = (args.cache_dir / row["cache_file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["response_sha256"]:
                raise ValueError("Source cache hash mismatch: " + row["name"])
    print(json.dumps({"requests": logs, "skipped_targets": [name for name, _, _ in TARGETS if name not in {r["name"] for r in logs}]}, indent=2))


if __name__ == "__main__":
    main()
