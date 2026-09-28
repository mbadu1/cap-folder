#!/usr/bin/env python3
"""Resumable, globally throttled Medium discovery and public-text collection.

Collects the authorized study window against a frozen sitemap index. Public
RSS and explicitly free mirror bodies remain source-labeled observations.
No unverified observations are admitted to the primary research sample.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import time
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import feedparser
import requests

from build_medium_toy import normalize_body, period_for
from medium_mirror_adapter import parse_public_response, story_id
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "substack"))
from build_substack_platform_month import classify_topic, TOPICS

ROOT = Path(__file__).resolve().parents[2]
VERSION = "medium-history-v1"
START, END = "2020-01-01", "2026-09-17"
UA = "DukeCapstoneMediumCollector/1.0 (user-reported research permission; single worker; public data)"
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
SCHEDULE = ("sitemap_posts", "sitemap_users", "feed", "feed", "mirror")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def clean_url(value):
    parsed = urlsplit(value)
    return urlunsplit(parsed._replace(query="", fragment=""))


def profile_handle(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r"/@([A-Za-z0-9_.-]+)(?:/.*)?", parsed.path)
    return match.group(1) if parsed.hostname == "medium.com" and match else None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def retry_after_seconds(value, at=None):
    if not value:
        return 60
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - (at or time.time()))
        except (ValueError, TypeError, OverflowError):
            return 60


def is_html_challenge(raw):
    prefix = raw[:32768].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html")) and any(
        marker in prefix for marker in (b"/cdn-cgi/challenge-platform/", b"cf-chl-", b"cf-challenge-running")
    )


class Collector:
    def __init__(self, cache, output, delay=3.1, min_free_gb=20, max_cache_gb=20):
        if delay < 3:
            raise ValueError("Medium request interval must be at least 3 seconds")
        if not math.isfinite(max_cache_gb) or max_cache_gb < 0:
            raise ValueError("max-cache-gb must be finite and nonnegative; 0 means unlimited")
        self.cache, self.output, self.delay = Path(cache), Path(output), delay
        self.cache.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.cache / "crawl.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(kind TEXT NOT NULL,item_key TEXT NOT NULL,url TEXT NOT NULL,
          priority TEXT NOT NULL,bucket TEXT NOT NULL DEFAULT '',state TEXT NOT NULL DEFAULT 'pending',
          error TEXT,observed_at TEXT,response_hash TEXT,PRIMARY KEY(kind,item_key));
        CREATE INDEX IF NOT EXISTS pending_tasks ON tasks(kind,state,priority);
        CREATE TABLE IF NOT EXISTS routes(route TEXT PRIMARY KEY,state TEXT,reason TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,url TEXT,kind TEXT,item_key TEXT,
          started_at TEXT,status INTEGER,bytes INTEGER,response_hash TEXT,cf_mitigated TEXT,retry_after TEXT,error TEXT);
        CREATE TABLE IF NOT EXISTS sources(sha256 TEXT PRIMARY KEY,url TEXT,method TEXT,observed_at TEXT,raw_path TEXT,bytes INTEGER);
        CREATE TABLE IF NOT EXISTS creators(profile_url TEXT PRIMARY KEY,handle TEXT,candidate_id TEXT,native_id TEXT,
          name TEXT,identity_status TEXT,history_status TEXT,feed_count INTEGER,source_hash TEXT);
        CREATE INDEX IF NOT EXISTS creator_ids ON creators(candidate_id);
        CREATE TABLE IF NOT EXISTS story_frame(post_id TEXT PRIMARY KEY,url TEXT,index_bucket TEXT,discovered_at TEXT);
        CREATE TABLE IF NOT EXISTS post_aliases(post_id TEXT,url TEXT,PRIMARY KEY(post_id,url));
        CREATE TABLE IF NOT EXISTS observations(post_id TEXT,source_method TEXT,source_hash TEXT,profile_url TEXT,
          year_month TEXT,published_at TEXT,in_window INTEGER,title TEXT,metadata_json TEXT,
          PRIMARY KEY(post_id,source_method,source_hash));
        CREATE INDEX IF NOT EXISTS observed_months ON observations(year_month,in_window);
        CREATE TABLE IF NOT EXISTS bodies(post_id TEXT,source_method TEXT,source_hash TEXT,text_sha256 TEXT,
          prose_words INTEGER,text TEXT,access_status TEXT,fullness_status TEXT,
          PRIMARY KEY(post_id,source_method,source_hash));
        CREATE TABLE IF NOT EXISTS browser_proofs(profile_url TEXT PRIMARY KEY,native_id TEXT,name TEXT,post_ids TEXT,
          evidence_json TEXT);
        ''')
        self.db.commit()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.stop = False
        self.min_free = int(min_free_gb * 1024**3)
        self.max_cache = None if max_cache_gb == 0 else int(max_cache_gb * 1024**3)
        self.network_count = 0

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def route(self, kind):
        return "sitemaps" if kind.startswith("sitemap_") else kind

    def blocked(self, kind):
        row = self.db.execute("SELECT state FROM routes WHERE route=?", (self.route(kind),)).fetchone()
        return row is not None and row[0] == "blocked"

    def block(self, kind, reason):
        self.db.execute("INSERT OR REPLACE INTO routes VALUES(?,?,?,?)", (self.route(kind), "blocked", reason, now()))

    def schema_outcome(self, kind, error=None):
        key = "schema_failure_streak:" + self.route(kind)
        streak = self.get(key, 0) + 1 if error else 0
        self.set(key, streak)
        if streak >= 3:
            self.block(kind, "Three consecutive schema failures: " + error)

    def repair_isolated_parser_stops(self):
        """One-time migration of the two diagnosed single-response route stops.

        Failed tasks stay failed; this only permits new queue items. Access
        blocks and repeated-failure blocks are never reopened by this repair.
        """
        if self.get("isolated_parser_stop_repair"):
            return
        known = {
            "mirror": "Parser schema failure: ValueError: No explicit Markdown body; heuristic string fallback is disabled.",
            "feed": "Parser schema failure: ValueError: Invalid feed or incompatible RSS schema",
        }
        changed = []
        for route, reason in known.items():
            if self.db.execute("DELETE FROM routes WHERE route=? AND reason=?", (route, reason)).rowcount:
                changed.append(dict(route=route,previous_reason=reason))
                self.schema_outcome(route)
        self.set("isolated_parser_stop_repair",dict(at=now(),reopened=changed,failed_tasks_requeued=0))
        self.db.commit()

    def enqueue(self, kind, key, url, bucket=""):
        priority = digest(f"medium-discovery-v1|{kind}|{key}")
        self.db.execute("INSERT OR IGNORE INTO tasks(kind,item_key,url,priority,bucket) VALUES(?,?,?,?,?)", (kind,key,url,priority,bucket))

    def add_profile(self, handle):
        url = "https://medium.com/@" + handle
        self.enqueue("feed", url, "https://medium.com/feed/@" + quote(handle, safe="._-"))
        self.db.execute("INSERT OR IGNORE INTO creators(profile_url,handle,identity_status,history_status) VALUES(?,?,?,?)",
                        (url,handle,"unresolved","not_collected"))

    def add_story(self, url, bucket=""):
        try:
            post_id = story_id(url)
        except (ValueError, TypeError):
            return
        url = clean_url(url)
        self.db.execute("INSERT OR IGNORE INTO story_frame VALUES(?,?,?,?)", (post_id,url,bucket,now()))
        self.db.execute("INSERT OR IGNORE INTO post_aliases VALUES(?,?)", (post_id,url))
        # Canonical ID route avoids fetching arbitrary hosts from sitemap text.
        mirror = f"https://freedium-mirror.cfd/https://medium.com/p/{post_id}/__data.json?x-sveltekit-invalidated=01"
        self.enqueue("mirror",post_id,mirror,bucket)
        handle = profile_handle(url)
        if handle:
            self.add_profile(handle)

    def source(self, raw, url, method, stamp, retain=True):
        sha = digest(raw)
        raw_path = None
        if retain:
            path = self.cache / "raw" / sha[:2] / (sha + ".gz")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                temp = path.with_suffix(".tmp")
                temp.write_bytes(gzip.compress(raw, mtime=0))
                temp.replace(path)
            raw_path = str(path.relative_to(self.cache))
        self.db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?)", (sha,url,method,stamp,raw_path,len(raw)))
        return sha

    def store_observation(self, post_id, method, sha, profile, published, title, metadata, body=None, access=""):
        month, within = period_for(published)
        metadata.update(primary_eligible=False, import_status="unknown", first_medium_date_verified=False,
                        historical_snapshot_verified=False, published_at=published, year_month=month)
        self.db.execute("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?,?,?,?,?)",
                        (post_id,method,sha,profile,month,published,int(within),title,json.dumps(metadata,ensure_ascii=False)))
        if body is not None and within:
            parsed = normalize_body(body,title)
            if parsed["text"].strip():
                self.db.execute("INSERT OR REPLACE INTO bodies VALUES(?,?,?,?,?,?,?,?)",
                                (post_id,method,sha,parsed["text_sha256"],parsed["prose_word_count"],parsed["text"],access,"unverified_fullness"))

    def parse(self, task, raw, stamp):
        kind, url = task["kind"], task["url"]
        if kind.startswith("sitemap_"):
            tree = ET.fromstring(raw)
            if tree.tag != "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset":
                raise ValueError("Unexpected sitemap schema")
            for row in tree.findall("s:url", SITEMAP_NS):
                loc = row.findtext("s:loc", namespaces=SITEMAP_NS)
                if not loc:
                    continue
                if kind == "sitemap_users":
                    handle = profile_handle(loc)
                    if handle:
                        self.add_profile(handle)
                else:
                    self.add_story(loc,task["bucket"])
            return self.source(raw,url,kind,stamp)
        if kind == "feed":
            feed = feedparser.parse(raw)
            if feed.bozo or feed.version not in {"rss20", "rss10", "atom10"}:
                raise ValueError("Invalid feed or incompatible RSS schema")
            profile = clean_url(feed.feed.get("link", ""))
            expected = task["item_key"]
            if profile != expected:
                raise ValueError("Profile redirect/alias requires explicit reconciliation")
            match = re.search(r"rss-([0-9a-f]{1,12})-", parse_qs(urlsplit(feed.feed.get("link", "")).query).get("source", [""])[0])
            candidate = match.group(1) if match else None
            name = re.sub(r"^Stories by | on Medium$", "", feed.feed.get("title", ""))
            proof = self.db.execute("SELECT * FROM browser_proofs WHERE profile_url=?", (profile,)).fetchone()
            entry_ids = {story_id(e.id) for e in feed.entries}
            native = proof["native_id"] if proof and proof["native_id"] == candidate else None
            history = "visible_profile_exhausted" if native and set(json.loads(proof["post_ids"])) == entry_ids else "rss_only_unknown_completeness"
            sha = self.source(raw,url,"official_profile_rss",stamp)
            self.db.execute("UPDATE creators SET candidate_id=?,native_id=?,name=?,identity_status=?,history_status=?,feed_count=?,source_hash=? WHERE profile_url=?",
                            (candidate,native,name,"browser_verified" if native else "rss_candidate_id",history,len(feed.entries),sha,profile))
            for entry in feed.entries:
                post_id = story_id(entry.id)
                if story_id(entry.link) != post_id:
                    raise ValueError("RSS GUID and link IDs disagree")
                published = parsedate_to_datetime(entry.published).isoformat()
                tags = [t.term for t in entry.get("tags", [])]
                topic, topic_method, confidence = classify_topic(" ".join([entry.title] + tags))
                metadata = dict(post_id=post_id,medium_story_url=clean_url(entry.link),author_name=entry.get("author"),
                                candidate_creator_id=candidate,native_creator_id=native,source_url=url,observed_at=stamp,
                                updated_at_rss=entry.get("updated"),native_tags=tags,topic_family=topic,
                                topic_method=topic_method,topic_confidence=confidence,language="unclassified")
                body = "\n".join(c.value for c in entry.get("content", []))
                self.store_observation(post_id,"official_profile_rss",sha,profile,published,entry.title,metadata,body,"officially_syndicated")
                self.add_story(entry.link)
            return sha
        if kind == "mirror":
            medium_url = "https://medium.com/p/" + task["item_key"]
            parsed = parse_public_response(raw,medium_url,url)
            # No raw response or body is retained for paid/unknown stories.
            published = parsed.get("published_at_source")
            if not published:
                raise ValueError("Mirror lacks publication timestamp")
            _, within = period_for(published)
            free = parsed.get("mirror_is_free") is True
            sha = self.source(raw,url,"freedium_mirror_public_story",stamp,retain=free and within)
            metadata = {k:v for k,v in parsed.items() if k not in {"html","markdown"}}
            metadata["observed_at"] = stamp
            handle = profile_handle(parsed["canonical_medium_url"])
            profile = "https://medium.com/@" + handle if handle else None
            metadata["profile_from_url_unverified"] = profile
            self.store_observation(parsed["post_id"],"freedium_mirror_public_story",sha,profile,published,parsed.get("title"),metadata,
                                   parsed.get("html") if free else None,"mirror_reports_free" if free else "not_retained")
            self.add_story(parsed["canonical_medium_url"],task["bucket"])
            return sha
        raise ValueError("Unknown task kind")

    def initialize(self):
        self.repair_isolated_parser_stops()
        if self.get("initialized"):
            if not self.get("empty_body_cleanup_complete"):
                removed=self.db.execute("DELETE FROM bodies WHERE LENGTH(TRIM(text))=0").rowcount
                self.set("empty_body_cleanup_complete",dict(at=now(),removed_empty_derived_rows=removed))
                self.db.commit()
            if not self.get("short_story_id_replay_complete"):
                rows = self.db.execute("SELECT t.*,s.raw_path FROM tasks t JOIN sources s ON t.response_hash=s.sha256 WHERE t.kind='sitemap_posts' AND t.state='done'").fetchall()
                for row in rows:
                    self.parse(dict(row),gzip.decompress((self.cache/row["raw_path"]).read_bytes()),row["observed_at"])
                # Retry only responses affected by this confirmed parser bug;
                # their original HTTP 200/error request records remain intact.
                old_error="ValueError: A 12-character Medium story ID is required."
                self.db.execute("UPDATE tasks SET state='pending',error=NULL WHERE error=?",(old_error,))
                self.db.execute("DELETE FROM routes WHERE reason=?",("Parser schema failure: "+old_error,))
                self.set("short_story_id_replay_complete",now())
                self.db.commit()
            return
        seed = ROOT / ".cache/medium_access/2026-09-24"
        legacy_manifest = ROOT / "data/access_pilot/medium/2026-09-24/manifest.json"
        reuse_legacy = legacy_manifest.is_file() and (seed / "sitemap_index.xml").is_file()
        if reuse_legacy:
            manifest = json.loads(legacy_manifest.read_text())
            raw = (seed / "sitemap_index.xml").read_bytes()
            expected_index_hash = manifest["snapshots"]["sitemap_index"]["sha256"]
        else:
            portable_seed = ROOT / "data/seeds/medium/2026-09-24"
            frozen = json.loads((portable_seed / "manifest.json").read_text())
            raw = gzip.decompress((portable_seed / "sitemap_index.xml.gz").read_bytes())
            expected_index_hash = frozen["decompressed_sha256"]
            manifest = {"snapshots": {"sitemap_index": {"observed_at": frozen["observed_date"]}}}
        if digest(raw) != expected_index_hash:
            raise ValueError("Frozen sitemap-index hash mismatch")
        labels = Counter()
        for e in ET.fromstring(raw).iter():
            if not e.tag.endswith("loc") or not e.text:
                continue
            m = re.fullmatch(r"https://medium\.com/sitemap/(users|posts)/(\d{4})/(?:users|posts)-(\d{4}-\d{2}-\d{2})\.xml", e.text)
            if m and (m[1] == "users" or START <= m[3] <= END):
                self.enqueue("sitemap_"+m[1], e.text, e.text, m[3][:7])
                labels[m[1]] += 1
        self.source(raw,"https://medium.com/sitemap/sitemap.xml","sitemap_index",manifest["snapshots"]["sitemap_index"].get("observed_at", "2026-09-24"))
        self.set("frame",dict(index_sha256=digest(raw),index_frozen_on="2026-09-24",sitemap_partitions=dict(labels),
                              date_partition_semantics="discovery_labels_not_verified_publication_dates",study_start=START,study_end=END))
        proofs = ROOT / "data/access_pilot/medium/2026-09-24-production/browser_identity_checks.json"
        if proofs.exists():
            for proof in json.loads(proofs.read_text()):
                self.db.execute("INSERT OR REPLACE INTO browser_proofs VALUES(?,?,?,?,?)",(proof["profile_url"],proof["native_creator_id"],proof["name"],json.dumps(proof["post_ids"]),json.dumps(proof)))
        legacy_required = [seed / "posts_2020_child.xml", seed / "users_2020_child.xml", seed / "profile_feed_0.xml", seed / "profile_feed_1.xml", ROOT / "data/access_pilot/medium/2026-09-24/request_log.json", ROOT / ".cache/medium_access/2026-09-24-toy/tutorial_feed.xml", ROOT / ".cache/medium_access/2026-09-24-toy/request_log.json"]
        for name in ("historical_2020", "tutorial_corrected"):
            legacy_required.extend([ROOT / "data/access_pilot/medium/2026-09-24-fallbacks" / (name + ".json"), ROOT / ".cache/medium_access/2026-09-24-fallbacks" / name / "response.json"])
        if reuse_legacy and all(path.is_file() for path in legacy_required):
            for kind,key in [("posts","posts_child"),("users","users_child")]:
                src = manifest["snapshots"][key]
                blob = (seed / (kind+"_2020_child.xml")).read_bytes()
                if digest(blob) != src["sha256"]:
                    raise ValueError("Seed sitemap hash mismatch")
                task = dict(kind="sitemap_"+kind,item_key=src["source_url"],url=src["source_url"],bucket="2020-01")
                sha = self.parse(task,blob,"2026-09-24")
                self.db.execute("UPDATE tasks SET state='done',response_hash=?,observed_at=? WHERE kind=? AND item_key=?",(sha,"2026-09-24",task["kind"],task["item_key"]))
            # Reuse all three previously collected feeds, including the empty feed.
            oldlog = json.loads((ROOT / "data/access_pilot/medium/2026-09-24/request_log.json").read_text())
            feed_inputs=[]
            for i in (0,1):
                blob=(seed / f"profile_feed_{i}.xml").read_bytes()
                log=next(r for r in oldlog if r.get("sha256")==digest(blob))
                feed_inputs.append((blob,log["url"],log.get("observed_at",log.get("requested_at"))))
            toy=ROOT / ".cache/medium_access/2026-09-24-toy"
            blob=(toy/"tutorial_feed.xml").read_bytes()
            log=next(r for r in json.loads((toy/"request_log.json").read_text()) if r.get("response_sha256")==digest(blob))
            feed_inputs.append((blob,log["url"],log["requested_at"]))
            for blob,url,stamp in feed_inputs:
                profile=clean_url(feedparser.parse(blob).feed.link)
                self.add_profile(profile_handle(profile))
                task=dict(kind="feed",item_key=profile,url=url,bucket="")
                sha=self.parse(task,blob,stamp)
                self.db.execute("UPDATE tasks SET state='done',response_hash=?,observed_at=? WHERE kind='feed' AND item_key=?",(sha,stamp,profile))
            for name in ("historical_2020","tutorial_corrected"):
                record=json.loads((ROOT / "data/access_pilot/medium/2026-09-24-fallbacks" / (name+".json")).read_text())
                blob=(ROOT / ".cache/medium_access/2026-09-24-fallbacks" / name / "response.json").read_bytes()
                if digest(blob)!=record["source_response_sha256"]:
                    raise ValueError("Cached mirror hash mismatch")
                task=dict(kind="mirror",item_key=record["post_id"],url=record["mirror_source_url"],bucket="")
                sha=self.parse(task,blob,"2026-09-24")
                self.db.execute("UPDATE tasks SET state='done',response_hash=?,observed_at=? WHERE kind='mirror' AND item_key=?",(sha,"2026-09-24",record["post_id"]))
        self.block("graphql","2026-09-24 read-only profile query returned HTTP 403 / Cloudflare challenge")
        self.block("profile_html","Earlier direct profile HTTP challenge; browser evidence is a separate source")
        self.set("initialized",now())
        self.set("short_story_id_replay_complete",now())
        self.set("empty_body_cleanup_complete",dict(at=now(),removed_empty_derived_rows=0))
        self.set("permission_basis","user_reported_medium_permission_and_full_collection_request_2026-09-24")
        self.db.commit()

    def pick(self):
        sequence=self.get("schedule_cursor",0)
        for offset in range(len(SCHEDULE)):
            kind=SCHEDULE[(sequence+offset)%len(SCHEDULE)]
            if self.blocked(kind):
                continue
            row=self.db.execute("SELECT * FROM tasks WHERE kind=? AND state='pending' ORDER BY priority LIMIT 1",(kind,)).fetchone()
            if row:
                self.set("schedule_cursor",sequence+offset+1)
                self.db.commit()
                return dict(row)
        return None

    def wait(self, seconds):
        end=time.time()+seconds
        while time.time()<end and not self.stop and not (self.cache/"STOP").exists():
            time.sleep(max(0,min(1,end-time.time())))

    def fetch_one(self, task):
        if shutil.disk_usage(self.cache).free < self.min_free:
            raise RuntimeError("Minimum free-disk reserve reached")
        if self.max_cache is not None:
            # Conservative bound on retained data plus existing database, without walking the blob tree.
            retained=self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM sources WHERE raw_path IS NOT NULL").fetchone()[0]
            if retained + (self.cache/"crawl.sqlite3").stat().st_size > self.max_cache:
                raise RuntimeError("Configured cache budget reached")
        self.wait(max(self.get("cooldown_until",0),self.get("last_request_start",0)+self.delay)-time.time())
        if self.stop or (self.cache/"STOP").exists():
            return False
        stamp=now()
        self.set("last_request_start",time.time())
        self.db.commit()
        status,size,sha,cf,retry,error,raw=0,0,None,None,None,None,None
        try:
            self.session.cookies.clear()
            with self.session.get(task["url"],timeout=(10,30),allow_redirects=False,stream=True) as response:
                status=response.status_code
                cf=response.headers.get("cf-mitigated")
                retry=response.headers.get("retry-after")
                if status==200:
                    chunks=[]
                    for chunk in response.iter_content(65536):
                        size+=len(chunk)
                        if size>32*1024**2:
                            raise ValueError("Response exceeds 32 MiB bound")
                        chunks.append(chunk)
                    raw=b"".join(chunks)
                    sha=digest(raw)
                else:
                    error=f"HTTP {status}"
            self.session.cookies.clear()
        except (requests.RequestException,ValueError) as exc:
            error=type(exc).__name__+": "+str(exc)[:180]
        self.network_count+=1
        if status in (401,403) or cf=="challenge":
            error=error or "Access challenge response"
            self.block(task["kind"],error or "Challenge response")
        elif raw is not None and is_html_challenge(raw):
            error="HTML access challenge response"
            self.block(task["kind"],error)
        if status==429:
            t=time.time()
            rate_events=[v for v in self.get("rate_events",[]) if t-v<600]+[t]
            self.set("rate_events",rate_events)
            self.set("cooldown_until",t+max(60,retry_after_seconds(retry)))
            if len(rate_events)>=3:
                self.block(task["kind"],"Three 429 responses within ten minutes")
        if raw is not None and error is None:
            try:
                self.db.execute("SAVEPOINT parse_response")
                sha=self.parse(task,raw,stamp)
                self.db.execute("RELEASE parse_response")
                self.schema_outcome(task["kind"])
            except Exception as exc:
                self.db.execute("ROLLBACK TO parse_response")
                self.db.execute("RELEASE parse_response")
                error=type(exc).__name__+": "+str(exc)[:180]
                # Quarantine a single bad response; persistent schema changes
                # still stop the route after three consecutive failures.
                if not any(s in str(exc) for s in ("Profile redirect/alias", "failed eager article", "publication timestamp")):
                    self.schema_outcome(task["kind"],error)
                else:
                    self.schema_outcome(task["kind"])
        else:
            self.schema_outcome(task["kind"])
        state="done" if error is None else "error"
        self.db.execute("UPDATE tasks SET state=?,error=?,observed_at=?,response_hash=? WHERE kind=? AND item_key=?",(state,error,stamp,sha,task["kind"],task["item_key"]))
        self.db.execute("INSERT INTO requests(url,kind,item_key,started_at,status,bytes,response_hash,cf_mitigated,retry_after,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (task["url"],task["kind"],task["item_key"],stamp,status,size,sha,cf,retry,error))
        self.db.commit()
        print(json.dumps(dict(at=stamp,kind=task["kind"],key=task["item_key"],http=status,state=state,error=error)),flush=True)
        return True

    def heartbeat(self, state="running", reason=None):
        """Publish activity without scanning the discovery or coverage tables."""
        last_request = self.db.execute(
            "SELECT id,kind,started_at,status,error FROM requests ORDER BY id DESC LIMIT 1"
        ).fetchone()
        text_routes = [kind for kind in ("feed", "mirror") if not self.blocked(kind)]
        result = dict(
            version=VERSION, updated_at=now(), worker_pid=os.getpid(), state=state, reason=reason,
            requests_this_run=self.network_count,
            last_request=dict(last_request) if last_request else None,
            routes=[dict(r) for r in self.db.execute("SELECT * FROM routes")],
            text_routes_available=text_routes,
            collection_activity=("discovery_and_text" if text_routes else "discovery_only")
                if state == "running" else "not_running",
            reporting_policy="full_report_on_exit_only",
            minimum_request_interval_seconds=self.delay,
        )
        atomic_json(self.output / "heartbeat.json", result)
        return result

    def report(self, state="running", reason=None):
        """Recount the corpus once after collection exits, never between requests."""
        task_counts=[dict(r) for r in self.db.execute("SELECT kind,state,COUNT(*) AS count FROM tasks GROUP BY kind,state")]
        counts=dict(
            profile_candidates=self.db.execute("SELECT COUNT(*) FROM creators").fetchone()[0],
            feeds_collected=self.db.execute("SELECT COUNT(*) FROM creators WHERE feed_count IS NOT NULL").fetchone()[0],
            browser_verified_creators=self.db.execute("SELECT COUNT(*) FROM creators WHERE native_id IS NOT NULL").fetchone()[0],
            discovered_story_ids=self.db.execute("SELECT COUNT(*) FROM story_frame").fetchone()[0],
            observed_in_window_stories=self.db.execute("SELECT COUNT(DISTINCT post_id) FROM observations WHERE in_window=1").fetchone()[0],
            retained_body_versions=self.db.execute("SELECT COUNT(*) FROM bodies").fetchone()[0],
            unique_stories_with_body=self.db.execute("SELECT COUNT(DISTINCT post_id) FROM bodies").fetchone()[0],
            length_qualified_stories=self.db.execute("SELECT COUNT(DISTINCT post_id) FROM bodies WHERE prose_words>=200").fetchone()[0],
            primary_sample_records=0,
        )
        rows=[dict(r) for r in self.db.execute('''SELECT o.year_month,COUNT(DISTINCT o.post_id) metadata_stories,
            COUNT(DISTINCT o.profile_url) observed_profiles,COUNT(DISTINCT b.post_id) stories_with_body,
            COUNT(DISTINCT CASE WHEN b.prose_words>=200 THEN b.post_id END) length_qualified_stories
            FROM observations o LEFT JOIN bodies b ON o.post_id=b.post_id AND o.source_method=b.source_method AND o.source_hash=b.source_hash
            WHERE o.in_window=1 GROUP BY o.year_month''')]
        by_month={r["year_month"]:r for r in rows}
        coverage=[]
        topic_coverage=[]
        observed_topics=defaultdict(set)
        for row in self.db.execute("SELECT year_month,post_id,metadata_json FROM observations WHERE in_window=1"):
            topic=json.loads(row["metadata_json"]).get("topic_family")
            if topic in TOPICS:
                observed_topics[(row["year_month"],topic)].add(row["post_id"])
        for year in range(2020,2027):
            for month in range(1,13):
                ym=f"{year}-{month:02d}"
                if ym>"2026-09":
                    continue
                item=by_month.get(ym,dict(year_month=ym,metadata_stories=0,observed_profiles=0,stories_with_body=0,length_qualified_stories=0))
                item.update(primary_eligible_creators=0,coverage_status="collection_incomplete",unobserved_is_inactive=False,is_partial_month=ym=="2026-09")
                coverage.append(item)
                for topic in TOPICS:
                    topic_coverage.append(dict(year_month=ym,topic_family=topic,
                        provisionally_tagged_stories=len(observed_topics[(ym,topic)]),
                        primary_eligible_creators=0,proposed_creator_floor=150,
                        floor_achieved=False,topic_labels_validated=False))
        result=dict(version=VERSION,updated_at=now(),worker_pid=os.getpid(),state=state,reason=reason,frame=self.get("frame"),
                    permission_basis=self.get("permission_basis"),minimum_request_interval_seconds=self.delay,counts=counts,tasks=task_counts,
                    requests=[dict(r) for r in self.db.execute("SELECT status,COUNT(*) count FROM requests GROUP BY status")],
                    routes=[dict(r) for r in self.db.execute("SELECT * FROM routes")],cache=str(self.cache),
                    all_months_target_achieved=False,complete_historical_archive=False,
                    collection_notes=["Current sitemap/feed/mirror observations; deleted/unlisted content and feed-truncated histories may be absent.",
                                      "Primary identity, full-text, language/topic, import and coverage gates are not bypassed.",
                                      "No automatic monthly release, commit, push, or external full-text publication."])
        result["text_routes_available"]=[kind for kind in ("feed","mirror") if not self.blocked(kind)]
        result["collection_activity"]=("discovery_and_text" if result["text_routes_available"] else "discovery_only") if state=="running" else "not_running"
        atomic_json(self.output/"status.json",result)
        atomic_json(self.output/"monthly_coverage.json",coverage)
        atomic_json(self.output/"monthly_topic_coverage.json",topic_coverage)
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-root",type=Path,default=ROOT/".cache/medium_history/history_v1")
    p.add_argument("--output",type=Path,default=ROOT/"data/monthly_full/medium")
    p.add_argument("--delay-seconds",type=float,default=3.1)
    p.add_argument("--max-requests",type=int,default=100)
    p.add_argument("--continuous",action="store_true",help="Continue checkpointed chunks until exhaustion, route blocks, STOP or resource guard")
    p.add_argument("--max-cache-gb",type=float,default=20,
                   help="Cache accounting limit in GiB (default: 20); 0 removes the cap, preserving the free-disk reserve")
    p.add_argument("--min-free-gb",type=float,default=20)
    p.add_argument("--init-only",action="store_true")
    args=p.parse_args()
    if args.max_requests<=0:
        p.error("max-requests must be positive")
    args.cache_root.mkdir(parents=True,exist_ok=True)
    with (args.cache_root/"collector.lock").open("w") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("An existing Medium collector holds the lock.",flush=True)
            return 3
        c=Collector(args.cache_root,args.output,args.delay_seconds,args.min_free_gb,args.max_cache_gb)
        signal.signal(signal.SIGTERM,lambda *_:setattr(c,"stop",True))
        signal.signal(signal.SIGINT,lambda *_:setattr(c,"stop",True))
        state,reason="chunk_complete",None
        try:
            c.initialize()
            if not args.init_only:
                c.heartbeat()
                while not c.stop and not (c.cache/"STOP").exists():
                    task=c.pick()
                    if not task:
                        pending=c.db.execute("SELECT 1 FROM tasks WHERE state='pending' LIMIT 1").fetchone()
                        state="blocked" if pending else "discovery_exhausted_with_gaps"
                        break
                    if not c.fetch_one(task):
                        break
                    c.heartbeat()
                    if not args.continuous and c.network_count >= args.max_requests:
                        break
                if c.stop or (c.cache/"STOP").exists():
                    state="stopped_by_signal_or_file"
            else:
                state="initialized"
        except Exception as exc:
            state,reason="stopped_error",type(exc).__name__+": "+str(exc)
        finally:
            try:
                c.heartbeat("reporting",reason)
                result=c.report(state,reason)
                c.heartbeat(state,reason)
            finally:
                c.db.close()
            print(json.dumps(result,ensure_ascii=False),flush=True)
        return 2 if state in {"blocked","stopped_error"} else 0


if __name__=="__main__":
    raise SystemExit(main())
