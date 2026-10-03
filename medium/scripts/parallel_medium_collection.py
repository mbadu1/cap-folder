#!/usr/bin/env python3
"""Frozen Medium shards: offline preparation, one paced transport pool, private export.

Only the coordinator owns SQLite and parses responses. Worker threads own their
HTTP sessions and share one request-start gate. Run only one team shard at a time.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import threading
import time

import requests

from collect_medium_history import (Collector, ROOT, START, END, atomic_json,
                                    digest, now, profile_handle, is_html_challenge,
                                    retry_after_seconds)
from medium_mirror_adapter import story_id

VERSION = "medium-parallel-team-v2"
DEFAULT_BATCH = ROOT / "data/medium_assignments/2026-10-03-team-v1"


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_batch(batch, shard):
    batch = Path(batch)
    manifest = json.loads((batch / "manifest.json").read_text())
    if manifest["version"] != "medium-candidate-worklists-v1" or shard not in (1, 2):
        raise ValueError("Unsupported assignment version or shard")
    if (manifest["start_date"], manifest["end_date"]) != (START, END):
        raise ValueError("Study dates differ")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or sha_file(batch / name) != expected:
            raise ValueError("Frozen assignment hash mismatch: " + name)
    rows = []
    for suffix in ("", "-reserve"):
        name = f"shard-{shard}{suffix}.csv"
        with (batch / name).open(newline="", encoding="utf-8") as stream:
            part = list(csv.DictReader(stream))
        expected_count = manifest["reserve_counts" if suffix else "shard_counts"][f"shard-{shard}"]
        if len(part) != expected_count:
            raise ValueError("Assignment row count mismatch")
        for row in part:
            handle = profile_handle(row["profile_url"])
            if (not handle or row["profile_url"] != "https://medium.com/@" + handle
                    or row["feed_url"] != "https://medium.com/feed/@" + handle
                    or row["shard"] != f"shard-{shard}"
                    or row["owner"] != manifest["owners"][f"shard-{shard}"]
                    or row["stage"] != ("reserve_candidate" if suffix else "primary_candidate")
                    or row["priority"] != digest("medium-discovery-v1|feed|" + row["profile_url"])):
                raise ValueError("Invalid frozen profile, owner, stage or priority")
        rows.extend(part)
    if len({r["profile_url"] for r in rows}) != len(rows):
        raise ValueError("Duplicate assigned profiles")
    return manifest, rows


def reconcile(checkpoint, output, batch):
    """Publish metadata exclusions from one read-only snapshot; never copy bodies."""
    output = Path(output)
    if (Path(checkpoint).parent / "MERGE_INCOMPLETE.json").exists():
        raise ValueError("Incomplete merge checkpoint; inspect preserved evidence")
    if output.exists():
        raise FileExistsError("Use a new reconciliation path; never overwrite a ledger")
    db = sqlite3.connect(Path(checkpoint).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        policy = json.loads(db.execute("SELECT value FROM meta WHERE key='author_pool_policy'").fetchone()[0])
        keys = [r[0] for r in db.execute("SELECT creator_key FROM author_pool_creators ORDER BY creator_key")]
        count = json.loads(db.execute("SELECT value FROM meta WHERE key='author_pool_count'").fetchone()[0])
        if count != len(keys):
            raise ValueError("Pool counter does not reconcile")
        if policy["target"] != json.loads((Path(batch) / "manifest.json").read_text())["target_collected_authors"]:
            raise ValueError("Pool target differs from batch")
        attempts = [dict(r) for r in db.execute("""SELECT kind,item_key,url,priority,bucket,state,error,observed_at,response_hash
            FROM tasks INDEXED BY pending_tasks WHERE kind IN ('feed','mirror') AND state IN ('done','error') ORDER BY kind,item_key""")]
        ledger = dict(version="medium-team-reconciliation-v1", generated_at=now(),
                      batch_manifest_sha256=sha_file(Path(batch) / "manifest.json"),
                      snapshot_last_request_id=db.execute("SELECT MAX(id) FROM requests").fetchone()[0],
                      target=policy["target"], known_creator_keys=keys, attempts=attempts,
                      routes=[dict(r) for r in db.execute("SELECT * FROM routes")],
                      cooldown_until=json.loads((db.execute("SELECT value FROM meta WHERE key='cooldown_until'").fetchone() or ["0"])[0]),
                      rate_events=json.loads((db.execute("SELECT value FROM meta WHERE key='rate_events'").fetchone() or ["[]"])[0]),
                      requires_exclusive_team_window=True,
                      note="Snapshot only; refresh after stopping prior owner. Existing bodies remain in the prior checkpoint.")
    finally:
        db.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(ledger, separators=(",", ":")).encode(), mtime=0))
    receipt = {k: v for k, v in ledger.items() if k not in ("attempts", "known_creator_keys")}
    receipt.update(sha256=sha_file(output), known_creators=len(keys), completed_or_failed_tasks=len(attempts))
    atomic_json(output.with_suffix(output.suffix + ".receipt.json"), receipt)
    return receipt


class Gate:
    def __init__(self, cache, delay, stop, min_free, *, per_worker_gap=6.0, enforce_minimum=True):
        if not math.isfinite(delay) or delay <= 0 or (enforce_minimum and delay < 1.5):
            raise ValueError("Global gap must be finite and at least 1.5 seconds")
        if not math.isfinite(per_worker_gap) or per_worker_gap < 0 or (enforce_minimum and per_worker_gap < 6.0):
            raise ValueError("Per-worker gap must be finite and at least 6 seconds")
        self.cache, self.delay, self.stop, self.min_free = Path(cache), delay, stop, min_free
        self.per_worker_gap = per_worker_gap
        self.last_worker_start = {}
        self.lock = threading.RLock()
        path = self.cache / "request_gate.json"
        self.state = json.loads(path.read_text()) if path.exists() else dict(last_start=0, cooldown_until=0, rate_events=[])

    def save(self):
        atomic_json(self.cache / "request_gate.json", self.state)

    def stopped(self):
        return self.stop.is_set() or any((self.cache / n).exists() for n in ("STOP", "PAUSED.json"))

    def start(self, task):
        worker = threading.get_ident()
        while not self.stopped():
            with self.lock:
                if self.stopped():
                    return None
                remaining = max(self.state["last_start"] + self.delay,
                                self.last_worker_start.get(worker, 0) + self.per_worker_gap,
                                self.state["cooldown_until"]) - time.time()
                if remaining <= 0:
                    if shutil.disk_usage(self.cache).free < self.min_free:
                        self.pause("Minimum free-disk reserve reached")
                        return None
                    started = time.time()
                    self.state["last_start"] = started
                    self.last_worker_start[worker] = started
                    self.save()
                    stamp = now()
                    with (self.cache / "request_starts.jsonl").open("a") as stream:
                        stream.write(json.dumps(dict(at=stamp, epoch=started, worker_id=worker,
                                                     kind=task["kind"], key=task["item_key"])) + "\n")
                    return stamp
            self.stop.wait(min(0.2, max(0, remaining)))
        return None

    def pause(self, reason):
        with self.lock:
            if not (self.cache / "PAUSED.json").exists():
                atomic_json(self.cache / "PAUSED.json", dict(at=now(), reason=reason))
            self.stop.set()

    def outcome(self, status, cf, retry, raw):
        with self.lock:
            if status in (401, 403) or cf == "challenge" or (raw is not None and is_html_challenge(raw)):
                self.pause("Access safeguard: HTTP " + str(status))
            if status == 429:
                t = time.time()
                self.state["rate_events"] = [v for v in self.state["rate_events"] if t - v < 600] + [t]
                delay = retry_after_seconds(retry)
                if not math.isfinite(delay):
                    delay = 60
                self.state["cooldown_until"] = max(self.state["cooldown_until"], t + max(60, delay))
                self.save()
                if len(self.state["rate_events"]) >= 3:
                    self.pause("Three 429 responses within ten minutes")


def fetch(task, gate):
    with requests.Session() as session:
        session.headers.update({"User-Agent": f"DukeCapstoneMediumCollector/1.0 (research permission; shared {gate.delay}s gate; {gate.per_worker_gap}s per worker; public data)"})
        stamp = gate.start(task)
        result = dict(task=task, stamp=stamp, status=0, size=0, raw=None, sha=None, cf=None, retry=None, error=None)
        if stamp is None:
            return result
        try:
            with session.get(task["url"], timeout=(10, 30), allow_redirects=False, stream=True) as response:
                result.update(status=response.status_code, cf=response.headers.get("cf-mitigated"), retry=response.headers.get("retry-after"))
                # Block starts promptly on headers, before consuming a response.
                gate.outcome(result["status"], result["cf"], result["retry"], None)
                if result["status"] == 200:
                    chunks = []
                    for chunk in response.iter_content(65536):
                        result["size"] += len(chunk)
                        if result["size"] > 32 * 1024**2:
                            raise ValueError("Response exceeds 32 MiB bound")
                        chunks.append(chunk)
                    result["raw"] = b"".join(chunks)
                    result["sha"] = digest(result["raw"])
                    if is_html_challenge(result["raw"]):
                        gate.outcome(200, None, None, result["raw"])
                        result["error"] = "HTML access challenge response"
                else:
                    result["error"] = "HTTP " + str(result["status"])
                if result["cf"] == "challenge":
                    result["error"] = "Access challenge response"
        except (requests.RequestException, ValueError) as exc:
            result["error"] = type(exc).__name__ + ": " + str(exc)[:180]
    return result


class TeamCollector(Collector):
    def __init__(self, cache, output, delay=1.5, min_free_gb=20, max_cache_gb=20, per_worker_gap=6.0):
        if not math.isfinite(delay) or delay < 1.5:
            raise ValueError("Global gap must be finite and at least 1.5 seconds")
        if not math.isfinite(per_worker_gap) or per_worker_gap < 6.0:
            raise ValueError("Per-worker gap must be finite and at least 6 seconds")
        # The legacy sequential collector keeps its original delay policy.
        # This runner owns transport and enforces its two timers in Gate.
        super().__init__(cache, output, max(3.1, delay), min_free_gb, max_cache_gb)
        self.delay, self.per_worker_gap = delay, per_worker_gap

    def heartbeat(self, state="running", reason=None):
        result = super().heartbeat(state, reason)
        result.update(version=VERSION, binding=self.get("team_binding"),
                      per_worker_gap_seconds=self.per_worker_gap,
                      slurm_job_id=os.environ.get("SLURM_JOB_ID"), node=os.uname().nodename)
        atomic_json(self.output / "heartbeat.json", result)
        return result

    def add_profile(self, handle):
        if "https://medium.com/@" + handle in self.assigned:
            super().add_profile(handle)

    def admit_pool_profile(self, profile, candidate):
        key = "rss_candidate:" + candidate if candidate else "profile:" + profile
        if key in self.known_keys:
            return False
        return super().admit_pool_profile(profile, candidate)

    def prepare(self, batch, shard, ledger_path, delay=1.5):
        manifest, rows = validate_batch(batch, shard)
        ledger = json.loads(gzip.decompress(Path(ledger_path).read_bytes()))
        if (ledger["version"] != "medium-team-reconciliation-v1"
                or ledger["batch_manifest_sha256"] != sha_file(Path(batch) / "manifest.json")
                or ledger["target"] != manifest["target_collected_authors"]):
            raise ValueError("Reconciliation does not match frozen batch")
        self.assigned = {r["profile_url"] for r in rows}
        self.known_keys = set(ledger["known_creator_keys"])
        if len(self.known_keys) != len(ledger["known_creator_keys"]) or len(self.known_keys) > ledger["target"]:
            raise ValueError("Invalid known-creator registry")
        sources = [Path(__file__), Path(__file__).with_name("collect_medium_history.py"),
                   Path(__file__).with_name("build_medium_toy.py"), Path(__file__).with_name("medium_mirror_adapter.py"),
                   ROOT / "scripts/substack/build_substack_platform_month.py"]
        binding = dict(version=VERSION, shard=shard, batch_sha256=sha_file(Path(batch) / "manifest.json"),
                       reconciliation_sha256=sha_file(ledger_path), delay_seconds=delay,
                       per_worker_gap_seconds=self.per_worker_gap,
                       min_free_bytes=self.min_free, max_cache_bytes=self.max_cache,
                       sources={p.name: sha_file(p) for p in sources})
        previous = self.get("team_binding")
        if previous and previous != binding:
            raise ValueError("Pinned cache binding differs; use original code/ledger/configuration")
        if not previous and self.db.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
            raise ValueError("Use a separate empty team cache; never rebind a legacy checkpoint")
        if previous:
            if not (self.cache / "binding.json").exists() or json.loads((self.cache / "binding.json").read_text()) != binding:
                raise ValueError("File/SQLite binding mismatch")
            self.db.execute("UPDATE tasks SET state='error',error='Interrupted transport; no automatic retry' WHERE state='inflight'")
            self.db.execute("UPDATE author_pool_mirrors SET state='error' WHERE post_id IN (SELECT item_key FROM tasks WHERE kind='mirror' AND state='error')")
            self.db.commit()
            return
        self.db.executescript("""CREATE TABLE assignments(profile_url TEXT PRIMARY KEY,stage TEXT NOT NULL);
            CREATE INDEX assignment_stage ON assignments(stage,profile_url);
            CREATE TABLE prior_attempts(kind TEXT,item_key TEXT,url TEXT,priority TEXT,bucket TEXT,state TEXT,
                error TEXT,observed_at TEXT,response_hash TEXT,PRIMARY KEY(kind,item_key));""")
        for r in rows:
            self.add_profile(profile_handle(r["profile_url"]))
            self.db.execute("INSERT INTO assignments VALUES(?,?)", (r["profile_url"], r["stage"]))
        columns = ("kind", "item_key", "url", "priority", "bucket", "state", "error", "observed_at", "response_hash")
        self.db.executemany("INSERT INTO prior_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                            (tuple(r[k] for k in columns) for r in ledger["attempts"]))
        self.db.execute("""UPDATE tasks SET (state,error,observed_at,response_hash)=(SELECT state,error,observed_at,response_hash
            FROM prior_attempts p WHERE p.kind=tasks.kind AND p.item_key=tasks.item_key)
            WHERE EXISTS(SELECT 1 FROM prior_attempts p WHERE p.kind=tasks.kind AND p.item_key=tasks.item_key)""")
        self.configure_author_pool(ledger["target"])
        # Local increments plus the frozen team registry enforce the team target.
        self.set("author_pool_count", len(self.known_keys))
        self.set("team_baseline_count", len(self.known_keys))
        self.set("team_binding", binding)
        self.set("initialized", now())
        self.set("permission_basis", "user-reported Medium permission; team DCC rollout requested 2026-10-03")
        for route in ledger["routes"]:
            self.db.execute("INSERT OR REPLACE INTO routes VALUES(?,?,?,?)", tuple(route[k] for k in ("route", "state", "reason", "updated_at")))
        self.db.commit()
        atomic_json(self.cache / "binding.json", binding)
        atomic_json(self.cache / "request_gate.json", dict(last_start=0, cooldown_until=ledger["cooldown_until"], rate_events=ledger["rate_events"]))

    def add_story(self, url, bucket=""):
        super().add_story(url, bucket)
        # Completed and failed baseline story tasks never become new requests.
        if self.get("team_binding"):
            self.db.execute("""UPDATE tasks SET (state,error,observed_at,response_hash)=(SELECT state,error,observed_at,response_hash
                FROM prior_attempts p WHERE p.kind=tasks.kind AND p.item_key=tasks.item_key)
                WHERE kind='mirror' AND item_key=? AND state='pending' AND EXISTS(SELECT 1 FROM prior_attempts p
                    WHERE p.kind=tasks.kind AND p.item_key=tasks.item_key)""", (story_id(url),))

    def pick(self):
        # Base pick is restricted to feed and admitted in-window story tasks.
        # Keep reserves unavailable while ANY primary feed is pending/in flight.
        row = self.db.execute("""SELECT 1 FROM tasks t JOIN assignments a ON a.profile_url=t.item_key
            WHERE t.kind='feed' AND a.stage='primary_candidate' AND t.state IN ('pending','inflight') LIMIT 1""").fetchone()
        stage = "primary_candidate" if row else "reserve_candidate"
        schedule = ("feed", "feed", "mirror")
        sequence = self.get("schedule_cursor", 0)
        for offset in range(3):
            kind = schedule[(sequence + offset) % 3]
            if self.blocked(kind):
                continue
            if kind == "feed":
                # Reserve a target slot for every in-flight feed (unknown yield).
                inflight = self.db.execute("SELECT COUNT(*) FROM tasks WHERE kind='feed' AND state='inflight'").fetchone()[0]
                if self.get("author_pool_count", 0) + inflight >= self.author_pool_target:
                    continue
                task = self.db.execute("""SELECT t.* FROM tasks t JOIN assignments a ON a.profile_url=t.item_key
                    WHERE t.kind='feed' AND t.state='pending' AND a.stage=? ORDER BY t.priority LIMIT 1""", (stage,)).fetchone()
            else:
                task = self.db.execute("""SELECT t.* FROM author_pool_mirrors a INDEXED BY author_pool_pending
                    CROSS JOIN tasks t ON t.kind='mirror' AND t.item_key=a.post_id
                    WHERE a.state='pending' AND t.state='pending' ORDER BY a.priority LIMIT 1""").fetchone()
            if task:
                self.set("schedule_cursor", sequence + offset + 1)
                self.db.execute("UPDATE tasks SET state='inflight' WHERE kind=? AND item_key=?", (kind, task["item_key"]))
                self.db.commit()
                return dict(task)
        return None

    def commit_result(self, result, gate):
        task, stamp, error, sha = result["task"], result["stamp"], result["error"], result["sha"]
        if stamp is None:
            self.db.execute("UPDATE tasks SET state='pending' WHERE kind=? AND item_key=?", (task["kind"], task["item_key"]))
            self.db.commit()
            return
        if result["status"] in (401, 403) or result["cf"] == "challenge" or error == "HTML access challenge response":
            self.block(task["kind"], error or "Access challenge")
        if result["status"] == 429 and len(gate.state["rate_events"]) >= 3:
            self.block(task["kind"], "Three 429 responses within ten minutes")
        if result["raw"] is not None and error is None:
            self.db.execute("SAVEPOINT parse_response")
            try:
                sha = self.parse(task, result["raw"], stamp)
                self.db.execute("RELEASE parse_response")
                self.schema_outcome(task["kind"])
            except Exception as exc:
                self.db.execute("ROLLBACK TO parse_response")
                self.db.execute("RELEASE parse_response")
                error = type(exc).__name__ + ": " + str(exc)[:180]
                isolated = any(s in str(exc) for s in ("Profile redirect/alias", "failed eager article", "publication timestamp"))
                self.schema_outcome(task["kind"], None if isolated else error)
                if self.blocked(task["kind"]):
                    gate.pause("Persistent parser schema failure")
        else:
            self.schema_outcome(task["kind"])
        state = "done" if error is None else "error"
        self.db.execute("UPDATE tasks SET state=?,error=?,observed_at=?,response_hash=? WHERE kind=? AND item_key=?",
                        (state, error, stamp, sha, task["kind"], task["item_key"]))
        if task["kind"] == "mirror":
            self.db.execute("UPDATE author_pool_mirrors SET state=? WHERE post_id=?", (state, task["item_key"]))
        self.db.execute("INSERT INTO requests(url,kind,item_key,started_at,status,bytes,response_hash,cf_mitigated,retry_after,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (task["url"], task["kind"], task["item_key"], stamp, result["status"], result["size"], sha, result["cf"], result["retry"], error))
        self.network_count += 1
        self.db.commit()


def collect(c, workers, max_requests, continuous=False, transport=fetch):
    if not 1 <= workers <= 20:
        raise ValueError("workers must be between 1 and 20")
    stop = threading.Event()
    gate = Gate(c.cache, c.delay, stop, c.min_free, per_worker_gap=c.per_worker_gap)
    c.active_gate = gate
    submitted = 0
    state = "pilot_complete"
    with ThreadPoolExecutor(max_workers=workers) as executor:
        active = {}
        while active or not gate.stopped():
            while len(active) < workers and not gate.stopped() and (continuous or submitted < max_requests):
                if c.max_cache is not None:
                    retained = c.db.execute("SELECT COALESCE(SUM(bytes),0) FROM sources WHERE raw_path IS NOT NULL").fetchone()[0]
                    if retained + (c.cache / "crawl.sqlite3").stat().st_size > c.max_cache:
                        gate.pause("Configured cache budget reached")
                        break
                task = c.pick()
                if task is None:
                    break
                future = executor.submit(transport, task, gate)
                active[future] = task
                submitted += 1
            if not active:
                if gate.stopped():
                    state = "paused" if (c.cache / "PAUSED.json").exists() else "stopped"
                elif not continuous and submitted >= max_requests:
                    state = "pilot_complete"
                elif c.get("author_pool_count") >= c.author_pool_target:
                    state = "author_target_reached_with_history_gaps"
                else:
                    state = "assigned_candidates_exhausted_with_gaps"
                if c.blocked("feed") or c.blocked("mirror"):
                    state = "blocked"
                break
            completed, _ = wait(active, timeout=0.5, return_when=FIRST_COMPLETED)
            for future in completed:
                task = active.pop(future)
                try:
                    c.commit_result(future.result(), gate)
                except Exception:
                    gate.pause("Coordinator failure; preserve checkpoint and inspect interrupted tasks")
                    # Drain all completed HTTP results before propagating.
                    for remaining in list(active):
                        c.commit_result(remaining.result(), gate)
                        active.pop(remaining)
                    raise
            status = c.heartbeat("cooldown" if gate.state["cooldown_until"] > time.time() else "running")
            status.update(workers=workers, inflight=len(active), binding=c.get("team_binding"), gate=gate.state,
                          slurm_job_id=os.environ.get("SLURM_JOB_ID"), node=os.uname().nodename)
            atomic_json(c.output / "heartbeat.json", status)
    if gate.stopped():
        state = "paused" if (c.cache / "PAUSED.json").exists() else "stopped"
    c.heartbeat(state)
    return state


def export_cache(cache, output):
    """Consistent stopped snapshot including all source versions/failures and raw blobs."""
    cache, output = Path(cache), Path(output)
    if output.exists():
        raise FileExistsError("Use a new export directory")
    with (cache / "collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True)
        source = sqlite3.connect((cache / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
        dest = sqlite3.connect(output / "crawl.sqlite3")
        try:
            source.backup(dest)
            dest.execute("PRAGMA journal_mode=DELETE")
            if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Export database integrity failure")
            binding = json.loads(dest.execute("SELECT value FROM meta WHERE key='team_binding'").fetchone()[0])
            for name in ("binding.json", "request_gate.json", "request_starts.jsonl", "STOP", "PAUSED.json"):
                if (cache / name).exists():
                    shutil.copy2(cache / name, output / name)
            for path, expected in dest.execute("SELECT raw_path,sha256 FROM sources WHERE raw_path IS NOT NULL"):
                blob = cache / path
                if digest(gzip.decompress(blob.read_bytes())) != expected:
                    raise ValueError("Raw source hash mismatch")
                target = output / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(blob, target)
            tasks = [dict(kind=k, state=s, count=n) for k, s, n in dest.execute("SELECT kind,state,COUNT(*) FROM tasks GROUP BY kind,state")]
            keys = [r[0] for r in dest.execute("SELECT creator_key FROM author_pool_creators ORDER BY creator_key")]
            atomic_json(output / "new_creator_keys.json", keys)
            result = dict(version=VERSION, exported_at=now(), binding=binding, tasks=tasks,
                          new_qualifying_candidate_ids=len(keys), primary_sample_records=0,
                          complete_historical_archive=False, baseline_bodies_included=False,
                          files={str(p.relative_to(output)): sha_file(p) for p in sorted(output.rglob("*")) if p.is_file()})
            atomic_json(output / "manifest.json", result)
            return result
        finally:
            source.close()
            dest.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("reconcile")
    rec.add_argument("--checkpoint", type=Path, required=True)
    rec.add_argument("--output", type=Path, required=True)
    rec.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    for name in ("prepare", "run", "status"):
        p = sub.add_parser(name)
        p.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
        p.add_argument("--shard", type=int, choices=(1, 2), required=True)
        p.add_argument("--reconciliation", type=Path, required=True)
        p.add_argument("--cache-root", type=Path, required=True)
        p.add_argument("--delay-seconds", "--global-gap-seconds", dest="delay_seconds", type=float, default=1.5)
        p.add_argument("--per-worker-gap-seconds", type=float, default=6.0)
        p.add_argument("--min-free-gb", type=float, default=20)
        p.add_argument("--max-cache-gb", type=float, default=0)
        if name == "run":
            p.add_argument("--workers", type=int, default=20)
            p.add_argument("--max-requests", type=int, default=40)
            p.add_argument("--continuous", action="store_true")
            p.add_argument("--exclusive-team-window", action="store_true", help="Attest that all other Medium collectors are stopped for this run")
            p.add_argument("--require-dcc", action="store_true")
    exp = sub.add_parser("export")
    exp.add_argument("--cache-root", type=Path, required=True)
    exp.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "reconcile":
        print(json.dumps(reconcile(args.checkpoint, args.output, args.batch), indent=2))
        return 0
    if args.command == "export":
        print(json.dumps(export_cache(args.cache_root, args.output), indent=2))
        return 0
    if args.command == "run":
        if not args.exclusive_team_window:
            parser.error("Coordinate one exclusive Medium team window; stop the legacy collector and other shards first")
        if args.workers not in range(1, 21) or args.max_requests <= 0:
            parser.error("Require 1–20 workers and positive max-requests")
        if args.require_dcc and (not os.environ.get("SLURM_JOB_ID") or "login" in os.uname().nodename.lower()):
            parser.error("Use dcc-agent on a compute node inside an active Slurm allocation")
    if not math.isfinite(args.min_free_gb) or args.min_free_gb < 20:
        parser.error("Keep at least the 20 GiB free-disk reserve")
    if not math.isfinite(args.delay_seconds) or args.delay_seconds < 1.5:
        parser.error("Keep a finite global gap of at least 1.5 seconds")
    if not math.isfinite(args.per_worker_gap_seconds) or args.per_worker_gap_seconds < 6.0:
        parser.error("Keep a finite per-worker gap of at least 6 seconds")
    args.cache_root.mkdir(parents=True, exist_ok=True)
    with (args.cache_root / "collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("A collector or exporter already holds this cache lock")
        c = TeamCollector(args.cache_root, args.cache_root / "report", args.delay_seconds,
                          args.min_free_gb, args.max_cache_gb, args.per_worker_gap_seconds)
        try:
            c.prepare(args.batch, args.shard, args.reconciliation, args.delay_seconds)
            if args.command == "run":
                if any((c.cache / name).exists() for name in ("STOP", "PAUSED.json")):
                    parser.error("Preserve stop/pause evidence; diagnose and deliberately archive before resuming")
                signal.signal(signal.SIGTERM, lambda *_: c.active_gate.stop.set() if hasattr(c, "active_gate") else None)
                signal.signal(signal.SIGINT, lambda *_: c.active_gate.stop.set() if hasattr(c, "active_gate") else None)
                state = collect(c, args.workers, args.max_requests, args.continuous)
            else:
                state = "prepared" if args.command == "prepare" else "inspection"
            result = c.report(state)
            result["team_binding"] = c.get("team_binding")
            result["per_worker_gap_seconds"] = c.per_worker_gap
            result["tasks"] = [dict(r) for r in c.db.execute("SELECT kind,state,COUNT(*) count FROM tasks GROUP BY kind,state")]
            print(json.dumps(result, indent=2))
            return 2 if state in ("paused", "blocked") else 0
        finally:
            c.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
