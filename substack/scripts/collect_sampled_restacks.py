#!/usr/bin/env python3
"""Prepare, collect and export a separately versioned sampled-only restack snapshot.

No requests in prepare/status/export. collect defaults to a bounded pilot.
Only selected ID/count/provenance records are stored; response bodies are discarded.
The original delivered dataset, sampler, source checkpoints and ZIP are read-only.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "sampled-restacks-v1"
USER_AGENT = "Duke-Capstone-Research/1.0 (public sampled-post engagement; no authentication)"
MAX_BYTES = 32 * 1024 * 1024
PRELOAD_RE = re.compile(r'window\._preloads\s*=\s*JSON\.parse\(("(?:\\.|[^"\\])*")\)', re.S)
FIELDS = ["platform", "post_id", "publication_id", "publication_url", "canonical_url",
          "year_month", "restacks_count", "status", "observed_at_utc", "source_type",
          "request_url", "response_url", "response_sha256", "error_type", "error_message"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def public_url(value, base=False):
    p = urllib.parse.urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Require an HTTPS public URL without credentials or custom port")
    # Input comes exclusively from checksum-verified sampled metadata.
    if p.hostname in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Local network targets are not supported")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, "" if base else p.path,
                                   "" if base else p.query, ""))


def verified_rows(root, manifest, name):
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Invalid delivery file path")
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("Delivery file escaped dataset root")
    expected = manifest["files"][name]
    data = path.read_bytes()
    if len(data) != expected["bytes"] or sha(data) != expected["sha256"]:
        raise ValueError("Delivered input checksum mismatch: " + name)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def load_targets(dataset):
    root = dataset.resolve()
    blob = (root / "DELIVERY_MANIFEST.json").read_bytes()
    m = json.loads(blob)
    names = sorted(k for k in m["files"] if re.fullmatch(r"substack/\d{4}/\d{2}/posts_meta\.csv", k))
    if len(names) != len(m["months"]) or not names:
        raise ValueError("Monthly inventory differs from delivery manifest")
    targets, month_set, assignments = {}, set(), 0
    for name in names:
        rows = verified_rows(root, m, name)
        assigned = verified_rows(root, m, name.replace("posts_meta.csv", "sampling_assignments.csv"))
        selected = {r["post_id"] for r in assigned}
        ids = {r["post_id"] for r in rows}
        if len(ids) != len(rows) or ids != selected:
            raise ValueError("Metadata must contain exactly the distinct assigned post IDs")
        month = name.split("/")[1] + "-" + name.split("/")[2]
        month_set.add(month)
        assignments += len(assigned)
        for r in rows:
            if not r["post_id"].isdigit() or not r["publication_id"].isdigit() or r["year_month"] != month:
                raise ValueError("Invalid sampled ID/month")
            t = {k: r[k] for k in ("post_id", "publication_id", "publication_url", "canonical_url", "year_month")}
            t["publication_url"] = public_url(t["publication_url"], base=True)
            t["canonical_url"] = public_url(t["canonical_url"])
            if t["post_id"] in targets:
                raise ValueError("Repeated native post ID across delivered months")
            targets[t["post_id"]] = t
    manifest_months = {x["month"] if isinstance(x, dict) else x for x in m["months"]}
    if month_set != manifest_months or len(targets) != m["unique_selected_text_rows"] or assignments != m["selected_text_assignments"]:
        raise ValueError("Selected counts/months differ from delivery manifest")
    ordered = sorted(targets.values(), key=lambda r: (r["publication_url"], r["post_id"]))
    binding = dict(version=VERSION, dataset=m["dataset"], delivery_manifest_sha256=sha(blob),
                   target_sha256=sha(canonical(ordered)), selected_posts=len(ordered),
                   selected_publications=len({r["publication_url"] for r in ordered}),
                   months=sorted(month_set), script_sha256=sha(Path(__file__).read_bytes()),
                   source_binding=m["source_binding"], provisional=m["provisional"],
                   final_sampling_ready=m["final_sampling_ready"])
    return ordered, binding


class Attention(Exception):
    pass


class BudgetReached(Exception):
    pass


class Transport:
    """One sequential process-wide gate; persist cooldowns and every HTTP attempt."""
    def __init__(self, store, max_requests=100, delay=3.0, opener=None, clock=None, sleep=None):
        self.store, self.max_requests, self.delay = store, max_requests, delay
        self.opener = opener or urllib.request.urlopen
        self.clock, self.sleep = clock or time.time, sleep or time.sleep
        self.count, self.next_start = 0, 0.0

    def wait(self):
        if self.max_requests and self.count >= self.max_requests:
            raise BudgetReached()
        until = max(self.next_start, float(self.store.state("cooldown_until", "0")))
        while self.clock() < until:
            if (self.store.root / "STOP").exists():
                raise BudgetReached()
            self.sleep(min(0.25, until - self.clock()))
        if (self.store.root / "STOP").exists():
            raise BudgetReached()
        self.count += 1
        self.next_start = self.clock() + self.delay

    def fetch(self, url):
        url = public_url(url)
        attempts = 0
        while True:
            self.wait()
            stamp, started = now(), self.clock()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                "Accept": "application/json,text/html;q=0.8"})
            try:
                with self.opener(request, timeout=45) as resp:
                    final_url = public_url(resp.geturl())
                    body = resp.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES:
                        raise Attention("Response exceeded 32 MiB bound")
                    self.store.request(url, stamp, resp.status, len(body), sha(body), self.clock()-started)
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    prefix = body[:16384].lower()
                    if headers.get("cf-mitigated") == "challenge" or any(x in prefix for x in
                            (b"cf-chl-", b"challenge-platform", b"<title>just a moment", b"<title>captcha")):
                        raise Attention("Access challenge; no bypass or automatic retry")
                    return dict(body=body, observed_at=now(), request_url=url,
                                response_url=final_url, response_sha256=sha(body))
            except urllib.error.HTTPError as e:
                prefix = e.read(16384)
                headers = {k.lower(): v for k, v in (e.headers or {}).items()}
                self.store.request(url, stamp, e.code, len(prefix), sha(prefix), self.clock()-started)
                e.close()
                if e.code == 429:
                    value = headers.get("retry-after")
                    seconds, source = 60.0, "missing_or_invalid_60_second_fallback"
                    try:
                        seconds = float(value)
                        if not math.isfinite(seconds) or seconds < 0:
                            raise ValueError()
                        source = "retry_after_seconds"
                    except (ValueError, TypeError):
                        try:
                            d = parsedate_to_datetime(value)
                            if d.tzinfo is None:
                                d = d.replace(tzinfo=timezone.utc)
                            seconds, source = max(0.0, d.timestamp()-self.clock()), "retry_after_http_date"
                        except (ValueError, TypeError, OverflowError):
                            seconds = 60.0
                    until = self.clock() + seconds
                    self.store.set_state("cooldown_until", str(until))
                    write_json(self.store.root / "cooldown.json", dict(url=url, http_status=429,
                        retry_after=value, wait_seconds=seconds, source=source,
                        cooldown_until_utc=datetime.fromtimestamp(until, timezone.utc).isoformat()))
                    continue
                rate403 = e.code == 403 and ("retry-after" in headers or any(headers.get(k) == "0" for k in
                    ("ratelimit-remaining", "x-ratelimit-remaining", "x-rate-limit-remaining")) or
                    any(x in prefix.lower() for x in (b"rate limit", b"rate_limit", b"rate-limit", b"too many requests")))
                if e.code == 401 or rate403 or headers.get("cf-mitigated") or any(x in prefix.lower() for x in
                    (b"challenge-platform", b"cf-chl-", b"<title>just a moment", b"<title>captcha")):
                    raise Attention("Authentication, challenge or rate-limited HTTP error: " + str(e.code)) from e
                attempts += 1
                if e.code in (408, 425, 500, 502, 503, 504) and attempts < 3:
                    self.next_start = max(self.next_start, self.clock() + (2, 5)[attempts-1])
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                self.store.request(url, stamp, None, 0, "", self.clock()-started)
                attempts += 1
                if attempts >= 3:
                    raise
                self.next_start = max(self.next_start, self.clock() + (2, 5)[attempts-1])


def extract_post(response, html=False):
    try:
        if html:
            match = PRELOAD_RE.search(response["body"].decode("utf-8"))
            if not match:
                raise ValueError("Public page preloads missing")
            data = json.loads(json.loads(match.group(1)))
        else:
            data = json.loads(response["body"])
        p = data["post"]
        if not isinstance(p, dict):
            raise ValueError("post must be an object")
        return p
    except (UnicodeError, ValueError, KeyError, TypeError) as e:
        raise Attention("Unsupported upstream post schema: " + str(e)) from e


def metric_record(target, p, response, source_type):
    if str(p.get("id")) != target["post_id"] or str(p.get("publication_id")) != target["publication_id"]:
        raise Attention("Returned post/publication ID differs from frozen sampled target")
    count = p.get("restacks")
    if count is not None and (type(count) is not int or count < 0):
        raise Attention("restacks must be a nonnegative integer or missing")
    return dict(target, platform="substack", restacks_count=count,
                status="ok" if count is not None else "missing_field", observed_at_utc=response["observed_at"],
                source_type=source_type, request_url=response["request_url"],
                response_url=response["response_url"], response_sha256=response["response_sha256"],
                error_type="" if count is not None else "MissingRestacks",
                error_message="" if count is not None else "Upstream restacks is absent/null; not an observed zero")


class Store:
    def __init__(self, root):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / "collector.lock").open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            raise
        self.db = sqlite3.connect(self.root / "restacks.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS targets(post_id TEXT PRIMARY KEY, publication_url TEXT, metadata TEXT);
        CREATE INDEX IF NOT EXISTS target_publication ON targets(publication_url,post_id);
        CREATE TABLE IF NOT EXISTS results(post_id TEXT PRIMARY KEY REFERENCES targets(post_id), record TEXT);
        CREATE TABLE IF NOT EXISTS batches(publication_url TEXT PRIMARY KEY, pages INTEGER, offset INTEGER, done INTEGER);
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, url TEXT, started_at TEXT,
          http_status INTEGER, response_bytes INTEGER, response_sha256 TEXT, elapsed_seconds REAL);
        """)
        self.db.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self.db.close()
        self.lock.close()

    def state(self, key, default=""):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, value))
        self.db.commit()

    def bind(self, targets, binding):
        expected = canonical(binding).decode()
        old = self.state("binding")
        if old and old != expected:
            raise ValueError("Resume binding mismatch; use pinned code and unchanged dataset or a new output")
        if not old:
            with self.db:
                self.db.executemany("INSERT INTO targets VALUES(?,?,?)", ((r["post_id"], r["publication_url"],
                    canonical(r).decode()) for r in targets))
                self.db.execute("INSERT INTO state VALUES('binding',?)", (expected,))
            write_json(self.root / "binding.json", binding)
        elif not (self.root / "binding.json").exists() or json.loads((self.root / "binding.json").read_text()) != binding:
            raise ValueError("External binding receipt differs from database")
        stored = []
        for row in self.db.execute("SELECT post_id,publication_url,metadata FROM targets ORDER BY publication_url,post_id"):
            t = json.loads(row[2])
            if row[0] != t["post_id"] or row[1] != t["publication_url"]:
                raise ValueError("Stored sampled target columns changed")
            stored.append(t)
        if sha(canonical(stored)) != binding["target_sha256"]:
            raise ValueError("Stored sampled target set changed")
        for row in self.db.execute("SELECT t.metadata,r.post_id,r.record FROM results r JOIN targets t USING(post_id)"):
            self.validate_result(json.loads(row[0]), row[1], json.loads(row[2]))

    def request(self, url, stamp, status, size, digest, elapsed):
        with self.db:
            self.db.execute("INSERT INTO requests VALUES(NULL,?,?,?,?,?,?)", (url, stamp, status, size, digest, round(elapsed,3)))

    @staticmethod
    def validate_result(target, ident, record):
        if ident != target["post_id"] or any(record.get(k) != v for k, v in target.items()):
            raise ValueError("Result escaped or changed the sampled target")
        if record.get("status") not in ("ok", "missing_field", "http_error", "transport_error"):
            raise ValueError("Invalid result status")
        count = record.get("restacks_count")
        if record["status"] == "ok":
            if type(count) is not int or count < 0 or not re.fullmatch("[0-9a-f]{64}", record.get("response_sha256", "")):
                raise ValueError("Successful result needs an observed integer count and response hash")
        elif count is not None:
            raise ValueError("Unavailable result must not be an observed zero")

    def save(self, record):
        target = self.db.execute("SELECT metadata FROM targets WHERE post_id=?", (record["post_id"],)).fetchone()
        if target is None:
            raise ValueError("Unselected result")
        self.validate_result(json.loads(target[0]), record["post_id"], record)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO results VALUES(?,?)", (record["post_id"], canonical(record).decode()))

    def pending(self, publication=None, post_ids=None):
        q = "SELECT t.metadata FROM targets t LEFT JOIN results r USING(post_id) WHERE r.post_id IS NULL"
        args = []
        if publication:
            q += " AND t.publication_url=?"; args.append(publication)
        if post_ids:
            q += " AND t.post_id IN (" + ",".join("?" for _ in post_ids) + ")"; args.extend(post_ids)
        q += " ORDER BY t.publication_url,t.post_id"
        return [json.loads(r[0]) for r in self.db.execute(q, args)]

    def status(self):
        counts = {"ok": 0, "missing_field": 0, "http_error": 0, "transport_error": 0}
        for status, n in self.db.execute("SELECT json_extract(record,'$.status'),count(*) FROM results GROUP BY 1"):
            counts[status] = n
        total = self.db.execute("SELECT count(*) FROM targets").fetchone()[0]
        terminal = sum(counts.values())
        value = dict(at_utc=now(), selected_posts=total, terminal_posts=terminal,
                     pending=total-terminal, counts=counts,
                     phase="PAUSED_FOR_REVIEW" if (self.root / "PAUSED.json").exists() else
                           "STOPPED" if (self.root / "STOP").exists() else
                           "ALL_TARGETS_ATTEMPTED" if total and terminal == total else "PREPARED_OR_PARTIAL",
                     requests=self.db.execute("SELECT count(*) FROM requests").fetchone()[0],
                     provisional=True, final_sampling_ready=False)
        write_json(self.root / "status.json", value)
        return value

    def export(self):
        dest = self.root / "post_restacks.csv"
        temp = dest.with_suffix(".csv.partial")
        with temp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
            for row in self.db.execute("SELECT t.metadata,r.post_id,r.record FROM targets t LEFT JOIN results r USING(post_id) ORDER BY t.post_id"):
                target = json.loads(row[0])
                result = json.loads(row[2]) if row[2] else dict(target, platform="substack", status="pending")
                if row[2]:
                    self.validate_result(target, row[1], result)
                w.writerow({k: "" if result.get(k) is None else result.get(k, "") for k in FIELDS})
        temp.replace(dest)
        status = self.status()
        binding = json.loads(self.state("binding"))
        write_json(self.root / "export_manifest.json", dict(version=VERSION, generated_at=now(),
            binding=binding, status=status, file=dest.name, sha256=sha(dest.read_bytes()), bytes=dest.stat().st_size,
            complete_success=status["counts"]["ok"] == status["selected_posts"],
            field_definition="restacks_count is raw public post.restacks; not shares, likes, or a list-derived count",
            snapshot_definition="Cumulative counts at response observation time, not original month-end counts",
            missing_definition="Empty count means pending/unavailable/missing, never zero",
            retained_scope="Sampled IDs/counts/provenance only; no response bodies or unselected post records"))
        return status


def direct(store, transport, target):
    url = target["publication_url"] + "/api/v1/posts/by-id/" + target["post_id"]
    source_type = "public_post_by_id_json"
    try:
        try:
            response = transport.fetch(url)
            p = extract_post(response)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 410):
                raise
            source_type = "public_post_page_fallback"
            response = transport.fetch(target["canonical_url"])
            p = extract_post(response, html=True)
        store.save(metric_record(target, p, response, source_type))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        # Never retry saved permanent errors on ordinary resume.
        record = dict(target, platform="substack", restacks_count=None,
                      status="http_error" if isinstance(e, urllib.error.HTTPError) else "transport_error",
                      observed_at_utc=now(), source_type=source_type, request_url=getattr(e, "url", url),
                      response_url="", response_sha256="", error_type=type(e).__name__, error_message=str(e))
        store.save(record)


def batch_page(store, transport, publication, page_cap):
    state = store.db.execute("SELECT pages,offset,done FROM batches WHERE publication_url=?", (publication,)).fetchone()
    pages, offset, done = tuple(state) if state else (0, 0, 0)
    if done or pages >= page_cap:
        return False
    selected = {r["post_id"]: r for r in store.pending(publication)}
    if not selected:
        return False
    response = transport.fetch(publication + f"/api/v1/posts?limit=50&offset={offset}&sort=new")
    try:
        payload = json.loads(response["body"])
        if not isinstance(payload, list) or len(payload) > 50:
            raise ValueError("Expected a list of at most 50 posts")
        ids = [str(p["id"]) for p in payload if isinstance(p, dict)]
        if len(ids) != len(payload) or len(set(ids)) != len(ids):
            raise ValueError("Missing/repeated batch post IDs")
    except (ValueError, TypeError, KeyError) as e:
        raise Attention("Unsupported batch schema: " + str(e)) from e
    matches = [metric_record(selected[str(p["id"])], p,
                             response, "public_publication_posts_batch")
               for p in payload if str(p["id"]) in selected]
    with store.db:
        for record in matches:
            store.validate_result(selected[record["post_id"]], record["post_id"], record)
            # A list endpoint can omit this field while a direct endpoint has it.
            # Leave that target pending for the verified direct fallback.
            if record["status"] == "ok":
                store.db.execute("INSERT OR IGNORE INTO results VALUES(?,?)", (record["post_id"], canonical(record).decode()))
        store.db.execute("INSERT OR REPLACE INTO batches VALUES(?,?,?,?)", (publication, pages+1,
                         offset+len(payload), int(len(payload) < 50)))
    return bool(payload) and len(payload) == 50 and pages+1 < page_cap


def collect(store, transport, strategy="direct", page_cap=1, post_ids=None):
    if (store.root / "PAUSED.json").exists():
        raise Attention("Preserved PAUSED.json requires deliberate review before resumption")
    if post_ids and store.db.execute("SELECT count(*) FROM targets WHERE post_id IN (" +
        ",".join("?" for _ in post_ids) + ")", post_ids).fetchone()[0] != len(set(post_ids)):
        raise ValueError("Pilot restriction includes an unselected post ID")
    try:
        if strategy == "hybrid":
            if post_ids:
                raise ValueError("Explicit post-ID pilots use direct strategy")
            pubs = sorted({r["publication_url"] for r in store.pending()})
            for publication in pubs:
                try:
                    while batch_page(store, transport, publication, page_cap):
                        pass
                except urllib.error.HTTPError as e:
                    if e.code not in (404, 410):
                        raise Attention("Batch HTTP error requires review: " + str(e.code)) from e
                for t in store.pending(publication):
                    direct(store, transport, t)
        else:
            for t in store.pending(post_ids=post_ids):
                direct(store, transport, t)
    except BudgetReached:
        pass
    except Attention as e:
        write_json(store.root / "PAUSED.json", dict(at_utc=now(), reason=str(e), automatic_retry=False))
        raise
    finally:
        store.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "collect", "status", "export"))
    parser.add_argument("--dataset", type=Path, required=True, help="Extracted delivered sampled dataset root")
    parser.add_argument("--output", type=Path, required=True, help="New separate engagement snapshot directory")
    parser.add_argument("--max-requests", type=int, default=100, help="All HTTP attempts including retries; 0 explicitly permits unlimited")
    parser.add_argument("--delay-seconds", type=float, default=3.0, help="Minimum sequential request-start interval")
    parser.add_argument("--strategy", choices=("direct", "hybrid"), default="direct")
    parser.add_argument("--batch-page-cap", type=int, default=1, help="Bounded 50-post pages per publication before direct fallback")
    parser.add_argument("--post-ids", help="Comma-separated sampled IDs for a bounded direct pilot")
    args = parser.parse_args()
    if args.max_requests < 0 or not math.isfinite(args.delay_seconds) or args.delay_seconds < 1 or args.batch_page_cap < 1:
        parser.error("Require nonnegative budget, delay >= 1 second and positive page cap")
    dataset, output = args.dataset.resolve(), args.output.resolve()
    if output == dataset or dataset in output.parents:
        parser.error("Engagement output must be outside the immutable delivered dataset")
    targets, binding = load_targets(dataset)
    store = Store(output)
    try:
        store.bind(targets, binding)
        if args.command == "collect":
            ids = list(dict.fromkeys(args.post_ids.split(","))) if args.post_ids else None
            collect(store, Transport(store, args.max_requests, args.delay_seconds), args.strategy, args.batch_page_cap, ids)
        status = store.export() if args.command == "export" else store.status()
        print(json.dumps(status))
    except (Attention, ValueError) as e:
        parser.exit(2, str(e) + "\n")
    except KeyboardInterrupt:
        store.status()
        parser.exit(130, "Stopped; committed results retained and unfinished IDs remain pending.\n")
    finally:
        store.close()


if __name__ == "__main__":
    main()
