#!/usr/bin/env python3
"""Resume a disk-backed, explicitly provisional draw from an immutable history DB.

No network requests or source writes. The global index is built once; bodies are
read again only for chosen source publications. Unknown language, automated
creator/topic labels and incomplete coverage never become final eligibility.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import fcntl
import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from build_substack_platform_month import (
    TOPICS, TOPIC_KEYWORDS, MACRO_DOMAINS, allocate_sample, creator_type,
    sha256_file, sha256_text,
)
from collect_substack_history import HISTORY_STAGE

VERSION = "substack-cached-provisional-sampling-v1"
START, END = "2020-01-01", "2026-09-17"
LIMITATIONS = [
    "Provisional draw from delivered histories; remaining publications can change selections.",
    "Numeric byline IDs establish keys, not independently verified human identity.",
    "Individual/organization labels and keyword creator-year topics await review.",
    "Unknown language is included only as a provisional candidate, not verified English.",
    "Current sitemap coverage does not establish a historical creator census.",
    "Conflicting dates, text versions or access metadata are excluded from candidate text.",
]
NEEDLES = {
    topic: [(" " + re.sub(r"[^a-z0-9]+", " ", k.lower()).strip() + " ",
             2 if " " in k else 1) for k in words]
    for topic, words in TOPIC_KEYWORDS.items()
}


def topic_index(text):
    """Same scores/tie-break as the shared classifier, with precompiled needles."""
    text = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    scores = [sum(weight for needle, weight in NEEDLES[t] if needle in text) for t in TOPICS]
    return max(range(8), key=lambda i: (scores[i], -i)) if max(scores) else 1


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)


def dump_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = iter(rows)
    first = next(rows, None)
    fields = fields or (list(first.keys()) if first is not None else [])
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        if first is not None:
            w.writerow(dict(first))
        for row in rows:
            w.writerow(dict(row))


def months():
    return [f"{y}-{m:02d}" for y in range(2020, 2027) for m in range(1, 13)
            if f"{y}-{m:02d}" <= "2026-09"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publications(
 publication_url TEXT PRIMARY KEY, ok INTEGER, publication_id TEXT,
 metadata TEXT, error_type TEXT, error_message TEXT, observed_at TEXT);
CREATE TABLE IF NOT EXISTS posts(
 post_id TEXT PRIMARY KEY, publication_url TEXT, published_at TEXT, year_month TEXT,
 topic INTEGER, qualifying INTEGER, conflicted INTEGER, word_count INTEGER,
 language TEXT, audience TEXT, text_sha256 TEXT, post_priority TEXT, metadata TEXT);
CREATE TABLE IF NOT EXISTS bylines(
 post_id TEXT, creator_id TEXT, name TEXT, handle TEXT, role TEXT,
 PRIMARY KEY(post_id,creator_id));
CREATE TABLE IF NOT EXISTS post_sources(
 post_id TEXT, publication_url TEXT, PRIMARY KEY(post_id,publication_url));
CREATE TABLE IF NOT EXISTS conflicts(
 post_id TEXT, publication_url TEXT, canonical_signature TEXT, other_signature TEXT,
 PRIMARY KEY(post_id,publication_url));
"""


class Run:
    def __init__(self, args):
        self.db = None
        self.lock = None
        try:
            self.initialize(args)
        except BaseException:
            if self.db is not None:
                self.db.close()
            if self.lock is not None:
                self.lock.close()
            raise

    def initialize(self, args):
        self.args = args
        self.root = args.output
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / "run.lock").open("a+b")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.started = time.time()
        self.last_status = 0
        self.source = args.source.resolve()
        for suffix in ("-wal", "-journal"):
            if Path(str(self.source) + suffix).exists():
                raise RuntimeError("Source must be an inactive immutable checkpoint")
        self.receipt = json.loads(args.merge_manifest.read_text())
        if self.receipt.get("combined_key_and_count_reconciliation") != "PASS":
            raise ValueError("Require completed combined-key reconciliation")
        self.source_stat = self.source.stat()
        if self.source_stat.st_size != self.receipt["database_bytes"]:
            raise ValueError("Source size differs from merge receipt")
        self.binding = {
            "version": VERSION, "source": str(self.source),
            "source_bytes": self.source_stat.st_size,
            "source_mtime_ns": self.source_stat.st_mtime_ns,
            "merge_manifest_sha256": sha256_file(args.merge_manifest),
            "frame_sha256": sha256_file(args.frame),
            "script_sha256": sha256_file(Path(__file__)),
            "shared_classifier_sha256": sha256_file(Path(__file__).with_name("build_substack_platform_month.py")),
            "start": START, "end": END, "target": args.target, "topic_floor": args.topic_floor,
        }
        binding_path = self.root / "binding.json"
        if binding_path.exists():
            if json.loads(binding_path.read_text()) != self.binding:
                raise ValueError("Resume binding mismatch; never overwrite a prior run")
        else:
            write_json(binding_path, self.binding)
        self.db = sqlite3.connect(self.root / "index.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA cache_size=-131072")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.executescript(SCHEMA)
        with args.frame.open(newline="") as f:
            self.frame = {row["publication_url"] for row in csv.DictReader(f)}
        if len(self.frame) != self.receipt["full_frame_size"]:
            raise ValueError("Frozen frame size differs from receipt")

    def get(self, key, default=""):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, str(value)))

    def status(self, stage, force=False, **extra):
        if not force and time.time() - self.last_status < 20:
            return
        self.last_status = time.time()
        value = dict(stage=stage, at_utc=now(), elapsed_seconds=round(time.time()-self.started, 2),
                     pid=os.getpid(), job=os.environ.get("SLURM_JOB_ID"), node=socket.gethostname(),
                     provisional=True, final_sampling_ready=False, **extra)
        write_json(self.root / "operation_status.json", value)
        print(json.dumps(value), flush=True)

    def index(self):
        if self.get("index_complete") == "true":
            return
        total = int(self.get("histories", "0"))
        post_rows = int(self.get("post_rows", "0"))
        invalid_dates = int(self.get("invalid_dates", "0"))
        last = self.get("last_publication")
        self.status("indexing_creator_post_eligibility", True, histories=total, post_rows=post_rows)
        src = readonly(self.source)
        cursor = src.execute("SELECT item_key,ok,payload,error_type,error_message,observed_at "
                             "FROM parsed WHERE stage=? AND item_key>? ORDER BY item_key", (HISTORY_STAGE, last))
        for url, ok, payload_text, error_type, error_message, observed in cursor:
            if url not in self.frame:
                continue
            payload = json.loads(payload_text) if ok else {}
            posts = payload.get("posts") or []
            pub = next((p.get("publication") for p in posts if p.get("publication")), {})
            pub_id = next((str(p["publication_id"]) for p in posts if p.get("publication_id")), "")
            for post in posts:
                post_rows += 1
                ident = str(post.get("post_id") or "")
                stamp = str(post.get("published_at") or "")
                try:
                    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    in_window = parsed.tzinfo is not None and START <= parsed.date().isoformat() <= END
                except ValueError:
                    in_window = False
                if not ident or not in_window:
                    invalid_dates += 1
                    continue
                text = post.get("full_text") or ""
                wc = int(post.get("retained_word_count") or 0)
                language = str(post.get("language") or "").strip().lower()
                public = post.get("audience") == "everyone" and post.get("is_paywalled") is False
                qualifying = bool(text) and wc >= 200 and public and (
                    not language or language == "english" or language.startswith("en"))
                topic = topic_index(" ".join(str(x or "") for x in (
                    pub.get("publication_name"), pub.get("hero_text"), post.get("title"), post.get("subtitle"))))
                metadata = {k: v for k, v in post.items() if k != "full_text"}
                signature = (stamp, str(post.get("text_sha256") or ""), str(post.get("audience") or ""))
                prior = self.db.execute("SELECT published_at,text_sha256,audience FROM posts WHERE post_id=?", (ident,)).fetchone()
                if prior is None:
                    self.db.execute("INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        ident, url, stamp, stamp[:7], topic, int(qualifying), 0, wc, language,
                        signature[2], signature[1], sha256_text(f"post-priority-v1|substack|{ident}"),
                        json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))))
                elif tuple(prior) != signature:
                    self.db.execute("UPDATE posts SET conflicted=1 WHERE post_id=?", (ident,))
                    self.db.execute("INSERT OR REPLACE INTO conflicts VALUES(?,?,?,?)", (
                        ident, url, json.dumps(tuple(prior)), json.dumps(signature)))
                self.db.execute("INSERT OR IGNORE INTO post_sources VALUES(?,?)", (ident, url))
                for byline in post.get("bylines") or []:
                    creator = str(byline.get("creator_id") or byline.get("id") or "")
                    if creator.isdigit():
                        self.db.execute("INSERT OR IGNORE INTO bylines VALUES(?,?,?,?,?)", (
                            ident, creator, str(byline.get("name") or ""), str(byline.get("handle") or ""),
                            str(byline.get("publication_role") or "post_byline")))
            self.db.execute("INSERT OR REPLACE INTO publications VALUES(?,?,?,?,?,?,?)", (
                url, ok, pub_id, json.dumps(pub, ensure_ascii=False), error_type, error_message, observed))
            total += 1
            self.set("last_publication", url)
            self.set("histories", total)
            self.set("post_rows", post_rows)
            self.set("invalid_dates", invalid_dates)
            if total % 200 == 0:
                self.db.commit()
            self.status("indexing_creator_post_eligibility", histories=total, post_rows=post_rows,
                        last_committed_every=200)
        src.close()
        if total != self.receipt["in_frame_attempted"]:
            raise ValueError(f"Indexed {total} histories; receipt requires {self.receipt['in_frame_attempted']}")
        self.set("index_complete", "true")
        self.db.commit()

    def derive(self):
        self.status("freezing_creator_year_topics", True)
        self.db.create_function("creator_priority", 1, lambda c: sha256_text(f"creator-priority-v1|substack|{c}"))
        self.db.create_function("creator_type", 1, lambda name: creator_type(name) if name else "unresolved")
        self.db.executescript("""
        CREATE INDEX IF NOT EXISTS byline_creator ON bylines(creator_id,post_id);
        CREATE INDEX IF NOT EXISTS post_month ON posts(year_month,post_id);
        CREATE INDEX IF NOT EXISTS post_source_lookup ON posts(publication_url,post_id);
        DROP TABLE IF EXISTS creators;
        CREATE TABLE creators AS
        WITH names AS (SELECT creator_id,name,count(*) n FROM bylines GROUP BY creator_id,name),
        nr AS (SELECT *,row_number() OVER(PARTITION BY creator_id ORDER BY n DESC,name) r FROM names),
        handles AS (SELECT creator_id,handle,count(*) n FROM bylines GROUP BY creator_id,handle),
        hr AS (SELECT *,row_number() OVER(PARTITION BY creator_id ORDER BY n DESC,handle) r FROM handles)
        SELECT nr.creator_id,nr.name display_name,hr.handle handle_or_slug,
               creator_type(nr.name) creator_type,creator_priority(nr.creator_id) creator_priority
        FROM nr JOIN hr USING(creator_id) WHERE nr.r=1 AND hr.r=1;
        CREATE UNIQUE INDEX creator_id_index ON creators(creator_id);
        DROP TABLE IF EXISTS creator_year_topics;
        CREATE TABLE creator_year_topics AS
        WITH counts AS (
         SELECT b.creator_id,substr(p.year_month,1,4) year,p.topic,count(*) n
         FROM bylines b JOIN posts p USING(post_id)
         WHERE p.qualifying=1 AND p.conflicted=0 GROUP BY b.creator_id,year,p.topic),
        ranked AS (SELECT *,row_number() OVER(PARTITION BY creator_id,year ORDER BY n DESC,topic) rank,
                   sum(n) OVER(PARTITION BY creator_id,year) total FROM counts)
        SELECT creator_id,year,topic,n topic_post_count,total total_topic_assigned_posts FROM ranked WHERE rank=1;
        CREATE UNIQUE INDEX creator_year_index ON creator_year_topics(creator_id,year);
        DROP TABLE IF EXISTS eligibility;
        CREATE TABLE eligibility AS
        SELECT b.creator_id,p.year_month,t.topic,c.creator_priority,count(*) qualifying_text_post_count
        FROM bylines b JOIN posts p USING(post_id) JOIN creators c USING(creator_id)
        JOIN creator_year_topics t ON t.creator_id=b.creator_id AND t.year=substr(p.year_month,1,4)
        WHERE c.creator_type='individual_provisional' AND p.qualifying=1 AND p.conflicted=0
        GROUP BY b.creator_id,p.year_month;
        CREATE UNIQUE INDEX eligibility_index ON eligibility(year_month,creator_id);
        DROP TABLE IF EXISTS selected_creators;
        CREATE TABLE selected_creators(creator_id TEXT,year_month TEXT,topic INTEGER,creator_priority TEXT,
                     qualifying_text_post_count INTEGER,PRIMARY KEY(year_month,creator_id));
        DROP TABLE IF EXISTS assignments;
        CREATE TABLE assignments(creator_id TEXT,year_month TEXT,post_id TEXT,post_priority TEXT,
                     PRIMARY KEY(year_month,creator_id,post_id));
        CREATE INDEX assignment_post_lookup ON assignments(post_id);
        """)
        coverage = []
        for month in months():
            candidates = [dict(r) for r in self.db.execute("SELECT * FROM eligibility WHERE year_month=?", (month,))]
            for row in candidates:
                row["topic_family"] = TOPICS[row["topic"]]
            selected, allocation = allocate_sample(candidates, self.args.target, self.args.topic_floor)
            self.db.executemany("INSERT INTO selected_creators VALUES(?,?,?,?,?)", (
                (r["creator_id"],month,r["topic"],r["creator_priority"],r["qualifying_text_post_count"])
                for r in candidates if r["creator_id"] in selected))
            self.db.execute("""INSERT INTO assignments
            SELECT creator_id,year_month,post_id,post_priority FROM (
             SELECT s.creator_id,s.year_month,p.post_id,p.post_priority,
                    row_number() OVER(PARTITION BY s.creator_id,s.year_month ORDER BY p.post_priority,p.post_id) rank
             FROM selected_creators s JOIN bylines b USING(creator_id) JOIN posts p USING(post_id)
             WHERE s.year_month=? AND p.year_month=s.year_month AND p.qualifying=1 AND p.conflicted=0
            ) WHERE rank<=3""", (month,))
            for i, topic in enumerate(TOPICS):
                cell = allocation[topic]
                coverage.append(dict(platform="substack", year_month=month, topic_family=topic,
                    eligible_candidate_creators=cell["eligible"], sampled_creators=cell["allocated"],
                    topic_floor_target=self.args.topic_floor, topic_floor_shortfall=cell["shortfall"],
                    provisional=True, final_sampling_ready=False, frame_status="pilot_incomplete",
                    pending_publications=self.receipt["pending"]))
            self.db.commit()
            self.status("drawing_provisional_months", month=month, selected_creators=len(selected))
        self.coverage = coverage
        self.status("validating_creator_post_assignments", True)
        missing = self.db.execute("""SELECT count(*) FROM selected_creators s LEFT JOIN
         (SELECT creator_id,year_month,count(*) n FROM assignments GROUP BY creator_id,year_month) a
         USING(creator_id,year_month) WHERE coalesce(a.n,0) NOT BETWEEN 1 AND 3""").fetchone()[0]
        if missing:
            raise ValueError("Selected creator-months do not each have one to three assignments")

    def extract_text(self):
        self.status("extracting_selected_cached_text", True)
        self.db.executescript("DROP TABLE IF EXISTS selected_text; CREATE TABLE selected_text("
                             "post_id TEXT PRIMARY KEY, full_text TEXT,word_count INTEGER,text_sha256 TEXT);")
        src = readonly(self.source)
        completed = 0
        urls = self.db.execute("SELECT DISTINCT p.publication_url FROM assignments a JOIN posts p USING(post_id) ORDER BY 1")
        for row in urls:
            url = row[0]
            expected = {r[0]: (r[1],r[2]) for r in self.db.execute(
                "SELECT DISTINCT p.post_id,p.text_sha256,p.word_count FROM assignments a JOIN posts p USING(post_id) "
                "WHERE p.publication_url=?", (url,))}
            source = src.execute("SELECT payload FROM parsed WHERE stage=? AND item_key=? AND ok=1", (HISTORY_STAGE,url)).fetchone()
            if source is None:
                raise ValueError("Selected source locator is missing")
            found = set()
            for p in json.loads(source[0])["posts"]:
                ident = str(p.get("post_id") or "")
                if ident not in expected:
                    continue
                text = p.get("full_text") or ""
                actual_hash = sha256_text(text)
                wc = len(re.findall(r"\b[\w’'-]+\b",text,flags=re.UNICODE))
                if (actual_hash,wc) != expected[ident] or wc<200 or p.get("audience")!="everyone" or p.get("is_paywalled") is not False:
                    raise ValueError("Selected body fails source/hash/count/access checks: " + ident)
                self.db.execute("INSERT OR REPLACE INTO selected_text VALUES(?,?,?,?)", (ident,text,wc,actual_hash))
                found.add(ident)
            if found != set(expected):
                raise ValueError("Selected source did not reproduce every indexed post")
            completed += 1
            if completed % 100 == 0:
                self.db.commit()
            self.status("extracting_selected_cached_text", publications_completed=completed)
        self.db.commit()
        src.close()

    def export(self):
        self.status("writing_provisional_monthly_partitions", True)
        lookup = self.root / "lookup"
        dump_csv(lookup / "creators.csv", self.db.execute("SELECT * FROM creators ORDER BY creator_id"))
        dump_csv(lookup / "publications.csv", self.db.execute("SELECT * FROM publications ORDER BY publication_url"))
        dump_csv(lookup / "creator_publications.csv", self.db.execute("SELECT DISTINCT b.creator_id,s.publication_url FROM bylines b JOIN post_sources s USING(post_id) ORDER BY 1,2"))
        topic_rows = []
        for r in self.db.execute("SELECT * FROM creator_year_topics ORDER BY creator_id,year"):
            d = dict(r)
            d["topic_family"] = TOPICS[d.pop("topic")]
            d["topic_method"] = "publication_description_title_keyword_v1_eligible_post_plurality"
            d["topic_review_status"] = "pending_manual_review"
            topic_rows.append(d)
        dump_csv(lookup / "creator_year_topics.csv", topic_rows)
        # Eight randomly ordered creator-years per topic gives a 64-item review
        # worksheet; human labels remain blank and completion is never inferred.
        review = []
        for topic in TOPICS:
            rows = sorted((r for r in topic_rows if r["topic_family"] == topic),
                key=lambda r: sha256_text(f"topic-review-v1|{r['creator_id']}|{r['year']}"))[:8]
            review.extend(dict(r, reviewed_topic="", reviewer="", notes="") for r in rows)
        dump_csv(lookup / "topic_review_sample.csv", review)
        dump_csv(lookup / "creator_month_eligibility.csv", self.db.execute("SELECT * FROM eligibility ORDER BY year_month,creator_id"))
        dump_csv(lookup / "post_conflicts.csv", self.db.execute("SELECT * FROM conflicts ORDER BY post_id,publication_url"),
                 ["post_id","publication_url","canonical_signature","other_signature"])
        dump_csv(self.root / "monthly_coverage.csv", self.coverage)
        summaries = []
        for month in months():
            year, number = month.split("-")
            folder = self.root / "substack" / year / number
            creators = []
            for r in self.db.execute("""SELECT s.*,c.display_name,c.handle_or_slug,count(a.post_id) selected_text_count
              FROM selected_creators s JOIN creators c USING(creator_id) JOIN assignments a USING(creator_id,year_month)
              WHERE s.year_month=? GROUP BY s.creator_id ORDER BY s.creator_priority,s.creator_id""", (month,)):
                d = dict(r)
                d["topic_family"] = TOPICS[d.pop("topic")]
                d.update(platform="substack",provisional=True,is_partial_month=month=="2026-09")
                creators.append(d)
            dump_csv(folder / "creator_month.csv", creators, ["platform","creator_id","year_month","topic_family",
                "creator_priority","qualifying_text_post_count","display_name","handle_or_slug","selected_text_count","provisional","is_partial_month"])
            dump_csv(folder / "sampling_assignments.csv", self.db.execute("SELECT * FROM assignments WHERE year_month=? ORDER BY creator_id,post_priority,post_id", (month,)),
                ["creator_id","year_month","post_id","post_priority"])
            def meta_rows():
                for r in self.db.execute("""SELECT DISTINCT p.* FROM posts p JOIN bylines b USING(post_id)
                  JOIN selected_creators s ON s.creator_id=b.creator_id AND s.year_month=p.year_month
                  WHERE p.year_month=? ORDER BY p.published_at,p.post_id""", (month,)):
                    p = json.loads(r["metadata"])
                    yield dict(platform="substack",post_id=r["post_id"],publication_id=p.get("publication_id",""),
                        publication_url=r["publication_url"],canonical_url=p.get("canonical_url",""),published_at=r["published_at"],
                        year_month=month,title=p.get("title",""),subtitle=p.get("subtitle",""),word_count=r["word_count"],
                        language=r["language"],audience=r["audience"],is_paywalled=p.get("is_paywalled"),
                        text_sha256=r["text_sha256"],post_priority=r["post_priority"],conflicted=r["conflicted"],
                        source_type=p.get("source_type",""),source_sha256=p.get("source_sha256",""))
            dump_csv(folder / "posts_meta.csv", meta_rows(), ["platform","post_id","publication_id","publication_url","canonical_url","published_at","year_month","title","subtitle","word_count","language","audience","is_paywalled","text_sha256","post_priority","conflicted","source_type","source_sha256"])
            dump_csv(folder / "posts_text.csv", self.db.execute("SELECT DISTINCT t.* FROM selected_text t JOIN assignments a USING(post_id) WHERE a.year_month=? ORDER BY t.post_id", (month,)),
                ["post_id","full_text","word_count","text_sha256"])
            dump_csv(folder / "coverage.csv", [r for r in self.coverage if r["year_month"]==month])
            m = len(creators)
            a = self.db.execute("SELECT count(*) FROM assignments WHERE year_month=?", (month,)).fetchone()[0]
            t = self.db.execute("SELECT count(DISTINCT post_id) FROM assignments WHERE year_month=?", (month,)).fetchone()[0]
            if not m <= a <= 3*m:
                raise ValueError("Creator contribution bounds violated")
            files = {p.name:dict(sha256=sha256_file(p),bytes=p.stat().st_size) for p in folder.glob("*.csv")}
            manifest = dict(version=VERSION,month=month,generated_at=now(),provisional=True,
                final_sampling_ready=False,ready_to_publish=False,frame_status="pilot_incomplete",
                pending_publications=self.receipt["pending"],creator_months=m,selected_text_assignments=a,
                unique_selected_texts=t,is_partial_month=month=="2026-09",
                observed_days=17 if month=="2026-09" else calendar.monthrange(int(year),int(number))[1],
                binding=self.binding,limitations=LIMITATIONS,files=files,
                validation=dict(creator_contribution_bounds=True,selected_source_text_hash_count_access=True))
            write_json(folder / "manifest.json",manifest)
            (folder / "README.md").write_text(f"# Provisional Substack candidates: {month}\n\n"
                f"{m:,} creator-months; {a:,} creator-post assignments; {t:,} unique texts.\n\n"
                "This is a reproducible provisional draw, not an approved final primary sample.\n"
                "Language, identity and topic review remain pending; the frozen frame is incomplete.\n"
                "Coauthored text is stored once, with separate creator links in sampling_assignments.csv.\n")
            summaries.append({k:manifest[k] for k in ("month","creator_months","selected_text_assignments","unique_selected_texts")})
            self.status("writing_provisional_monthly_partitions", month=month)
        current = self.source.stat()
        if (current.st_size,current.st_mtime_ns) != (self.source_stat.st_size,self.source_stat.st_mtime_ns):
            raise ValueError("Source changed during the run")
        report = dict(version=VERSION,completed_at=now(),provisional=True,final_sampling_ready=False,
            binding=self.binding,limitations=LIMITATIONS,months=summaries,
            indexed_histories=int(self.get("histories")),publication_post_rows=int(self.get("post_rows")),
            unique_posts=self.db.execute("SELECT count(*) FROM posts").fetchone()[0],
            conflicting_post_ids=self.db.execute("SELECT count(*) FROM posts WHERE conflicted=1").fetchone()[0],
            numeric_creators=self.db.execute("SELECT count(*) FROM creators").fetchone()[0],
            unknown_language_qualifying_posts=self.db.execute("SELECT count(*) FROM posts WHERE qualifying=1 AND conflicted=0 AND language=''").fetchone()[0],
            selected_unknown_language_texts=self.db.execute("SELECT count(*) FROM selected_text t JOIN posts p USING(post_id) WHERE p.language=''").fetchone()[0],
            invalid_or_out_of_window_post_rows=int(self.get("invalid_dates")),
            pending_publications=self.receipt["pending"])
        write_json(self.root / "manifest.json", report)
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.status("complete", True, creator_months=sum(r["creator_months"] for r in summaries),
                    selected_text_assignments=sum(r["selected_text_assignments"] for r in summaries))

    def run(self):
        try:
            self.index()
            self.derive()
            self.extract_text()
            self.export()
        except BaseException as exc:
            self.db.rollback()
            self.status("failed", True, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self.db.close()
            self.lock.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--merge-manifest", type=Path, required=True)
    p.add_argument("--frame", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--target", type=int, default=2500)
    p.add_argument("--topic-floor", type=int, default=150)
    args = p.parse_args()
    if args.target < 8*args.topic_floor or args.topic_floor<0:
        p.error("Target must be at least eight topic floors")
    os.umask(0o077)
    Run(args).run()


if __name__ == "__main__":
    main()
