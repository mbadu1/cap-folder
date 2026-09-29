#!/usr/bin/env python3
"""Parallel, checkpointed runner for the frozen two-owner Substack assignment.

The frozen v2 collector and its manifest stay unchanged. This runner binds its
own source hash to each cache while using the existing parser, checkpoint,
export, and merge formats. Validation rescrapes use a separate cache.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
import urllib.error

import team_collection as team
from build_substack_platform_month import Collector, CrawlCircuitOpen
from collect_substack_history import HISTORY_STAGE, parse_publication_history

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH = ROOT / "data/substack_assignments/2026-09-28-team-v2"
RUNNER = Path(__file__).resolve()
MISSING = object()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Pacing:
    """One start gate tracks both aggregate and per-thread request intervals."""
    def __init__(self, per_worker_gap, global_gap, pause_event, on_start=None):
        self.per_worker_gap = per_worker_gap
        self.global_gap = global_gap
        self.pause_event = pause_event
        self.on_start = on_start
        self.lock = threading.Lock()
        self.next_global = 0.0
        self.next_worker = {}

    def wait(self):
        ident = threading.get_ident()
        while True:
            if self.pause_event.is_set():
                raise CrawlCircuitOpen("Access/rate stop; no new request starts")
            with self.lock:
                now = time.monotonic()
                start = max(now, self.next_global, self.next_worker.get(ident, 0.0))
                delay = start - now
                if delay <= 0:
                    if self.pause_event.is_set():
                        raise CrawlCircuitOpen("Access/rate stop; no new request starts")
                    self.next_global = now + self.global_gap
                    self.next_worker[ident] = now + self.per_worker_gap
                    if self.on_start is not None:
                        try:
                            self.on_start(ident)
                        except OSError as exc:
                            self.pause_event.set()
                            raise CrawlCircuitOpen("Cannot write request-start telemetry") from exc
                    return
            time.sleep(min(delay, 0.25))

    def pause_for(self, seconds):
        with self.lock:
            self.next_global = max(self.next_global, time.monotonic() + seconds)


class ParallelCollector(Collector):
    def __init__(self, per_worker_gap, pacing, pause_event):
        super().__init__(per_worker_gap, 1, 600)
        self.throttle = pacing
        self.pause_event = pause_event

    def _record_rate_limit(self, wait_seconds):
        self.pause_event.set()
        self.throttle.pause_for(wait_seconds)
        raise CrawlCircuitOpen(f"HTTP 429; honor Retry-After/cooldown of at least {wait_seconds:.1f}s")


class CollectorPool:
    def __init__(self, per_worker_gap, global_gap, pause_event, on_start=None):
        self.local = threading.local()
        self.pacing = Pacing(per_worker_gap, global_gap, pause_event, on_start)
        self.per_worker_gap = per_worker_gap
        self.pause_event = pause_event
        self.collectors = []
        self.lock = threading.Lock()

    def get(self):
        collector = getattr(self.local, "collector", None)
        if collector is None:
            collector = ParallelCollector(self.per_worker_gap, self.pacing, self.pause_event)
            self.local.collector = collector
            with self.lock:
                self.collectors.append(collector)
        return collector

    def metrics(self):
        statuses = {}
        with self.lock:
            collectors = list(self.collectors)
        requests = 0
        for collector in collectors:
            with collector.count_lock:
                requests += collector.request_count
                for key, value in collector.http_status_counts.items():
                    statuses[key] = statuses.get(key, 0) + value
        return requests, statuses


def runner_binding(batch_id, batch_sha, shard, list_sha=None):
    return {"runner_sha256": team.sha_file(RUNNER), "batch_id": batch_id,
            "batch_manifest_sha256": batch_sha, "shard": shard, "validation_list_sha256": list_sha}


def check_runner_binding(cache, binding):
    path = cache / "parallel_runner.json"
    if path.exists():
        if team.read_json(path) != binding:
            raise ValueError("Parallel runner or assignment changed; use the pinned revision/cache")
    else:
        team.write_json(path, binding)


def validate_options(args, minimum_gap):
    if not 1 <= args.workers <= 20:
        raise ValueError("--workers must be 1..20")
    if not math.isfinite(args.delay_seconds) or args.delay_seconds < minimum_gap:
        raise ValueError("Per-worker request gap is below the frozen setting")
    if not math.isfinite(args.global_gap_seconds) or args.global_gap_seconds < 0.6:
        raise ValueError("Aggregate request-start gap must be at least 0.6 seconds")
    if not args.continuous and not 1 <= args.limit <= 500:
        raise ValueError("--limit must be 1..500")


def collect(args, rows, cache, binding, expected=None):
    """One lock and one SQLite writer; network histories run in at most 20 threads."""
    cache = Path(cache)
    with team.cache_lock(cache):
        if (cache / "RETIRED.json").exists():
            raise RuntimeError("Retired cache; use its recorded replacement")
        if (cache / "PAUSED.json").exists():
            raise RuntimeError("Access/rate pause is present; inspect it before deliberate resumption")
        if expected is not None:
            team.check_binding(cache, expected, create=True)
        check_runner_binding(cache, binding)
        checkpoint = team.Checkpoint(cache / "crawl.sqlite3")
        keys = team.checkpoint_keys(cache / "crawl.sqlite3")
        team.counts(rows, keys)
        remaining = [row["publication_url"] for row in rows if row["publication_url"] not in keys]
        if not args.continuous:
            remaining = remaining[:args.limit]
        pause_event, stop_event = threading.Event(), threading.Event()
        event_log = (cache / "request_starts.jsonl").open("a", encoding="utf-8")

        def log_start(ident):
            event_log.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                        "thread_id": ident}) + "\n")
            event_log.flush()

        pool = CollectorPool(args.delay_seconds, args.global_gap_seconds, pause_event, log_start)
        fetched, reason, state = 0, None, "running"
        prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        for sig in prior:
            signal.signal(sig, lambda *_: stop_event.set())

        def report():
            requests, statuses = pool.metrics()
            value = dict(binding, **team.counts(rows, keys), updated_at=team.iso_now(),
                         worker_pid=os.getpid(), state=state, reason=reason,
                         workers=args.workers, delay_seconds=args.delay_seconds,
                         global_gap_seconds=args.global_gap_seconds,
                         request_count_this_run=requests,
                         http_status_counts_this_run=statuses,
                         new_publications_this_run=fetched)
            if expected is not None:
                value.update(expected)
            team.write_json(cache / "collection_status.json", value)
            return value

        def task(url):
            try:
                return url, True, parse_publication_history(pool.get(), url, team.START, team.END), None
            except Exception as exc:
                return url, False, None, exc

        try:
            report()
            source = iter(remaining)
            futures = {}
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                def submit():
                    if pause_event.is_set() or stop_event.is_set() or (cache / "STOP").exists():
                        return False
                    try:
                        url = next(source)
                    except StopIteration:
                        return False
                    futures[executor.submit(task, url)] = url
                    return True

                for _ in range(args.workers):
                    if not submit():
                        break
                while futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        futures.pop(future)
                        url, ok, payload, error = future.result()
                        if isinstance(error, CrawlCircuitOpen):
                            pause_event.set()
                            reason = reason or str(error)
                            state = "rate_limit_stop"
                        else:
                            checkpoint.put(HISTORY_STAGE, url, ok, payload, error)
                            keys[url] = int(ok)
                            fetched += 1
                            if isinstance(error, urllib.error.HTTPError) and error.code in (401, 403, 429):
                                pause_event.set()
                                reason = reason or str(error)
                                state = "access_or_rate_stop"
                            print(json.dumps({"publication_url": url, "ok": int(ok),
                                              **team.counts(rows, keys)}), flush=True)
                        report()
                    while len(futures) < args.workers and submit():
                        pass
            if pause_event.is_set():
                state = state if state.endswith("stop") else "rate_limit_stop"
            elif stop_event.is_set() or (cache / "STOP").exists():
                state = "stopped"
            elif team.counts(rows, keys)["pending"]:
                state = "chunk_complete"
            else:
                state = "attempted_complete"
            result = report()
            if state.endswith("stop"):
                team.write_json(cache / "PAUSED.json", {"state": state, "reason": reason,
                                "at": team.iso_now(), "action": "Inspect access/rate response and cooldown before resuming."})
        finally:
            checkpoint.close()
            event_log.close()
            for sig, handler in prior.items():
                signal.signal(sig, handler)
        code = 3 if state.endswith("stop") else 5 if state == "stopped" else 4 if result["pending"] else 2 if result["failed"] else 0
        return result, code


def production(args):
    batch, rows, cache, expected = team.setup(args)
    validate_options(args, batch["min_delay_seconds_per_worker"])
    binding = runner_binding(batch["batch_id"], team.sha_file(args.batch / "manifest.json"), args.shard)
    if args.check:
        result = {"validation": "passed", "cache": str(cache), "runner": binding,
                  "assignment": expected, "assigned": len(rows)}
        runner_file = cache / "parallel_runner.json"
        if runner_file.exists() and team.read_json(runner_file) != binding:
            raise ValueError("Parallel runner or assignment differs from the bound cache")
        if (cache / "assignment.json").exists():
            team.check_binding(cache, expected)
            result.update(team.counts(rows, team.checkpoint_keys(cache / "crawl.sqlite3")))
        return result, 0
    return collect(args, rows, cache, binding, expected)


def make_validation_list(args):
    batch, shards = team.validate_batch(args.batch)
    allowed = {r["publication_url"] for r in shards[1]}
    with team.readonly(args.reference_db) as db:
        urls = [r[0] for r in db.execute("SELECT item_key FROM parsed WHERE stage=? AND ok=1 ORDER BY item_key", (HISTORY_STAGE,))]
    if len(urls) != 21 or not set(urls) <= allowed:
        raise ValueError("Expected exactly 21 successful local shard-1 histories")
    if args.output.exists():
        raise FileExistsError(args.output)
    value = {"batch_id": batch["batch_id"], "shard": 1, "urls": urls,
             "urls_sha256": digest(urls), "created_at": team.iso_now()}
    team.write_json(args.output, value)
    return {"output": str(args.output), "count": len(urls), "urls_sha256": value["urls_sha256"]}


def validation_list(path, batch_path):
    value = team.read_json(path)
    batch, shards = team.validate_batch(batch_path)
    urls = value.get("urls")
    allowed = {r["publication_url"] for r in shards[1]}
    if (value.get("batch_id"), value.get("shard")) != (batch["batch_id"], 1):
        raise ValueError("Validation list belongs to another assignment")
    if not isinstance(urls, list) or len(urls) != 21 or len(set(urls)) != 21 or not set(urls) <= allowed:
        raise ValueError("Validation list must contain exactly 21 distinct shard-1 URLs")
    if value.get("urls_sha256") != digest(urls):
        raise ValueError("Validation list checksum mismatch")
    return value


def validation_run(args):
    value = validation_list(args.urls, args.batch)
    validate_options(args, team.read_json(args.batch / "manifest.json")["min_delay_seconds_per_worker"])
    cache = args.cache_root
    for shard in (1, 2):
        production_cache = ROOT / ".cache/substack_shards" / value["batch_id"] / f"shard-{shard}"
        if cache.resolve() == production_cache.resolve():
            raise ValueError("Validation cache must be separate from production caches")
    if (cache / "assignment.json").exists():
        raise ValueError("Validation cache is bound to a production assignment")
    rows = [{"publication_url": url} for url in value["urls"]]
    binding = runner_binding(value["batch_id"], team.sha_file(args.batch / "manifest.json"),
                             "validation", value["urls_sha256"])
    return collect(args, rows, cache, binding)


def validation_export(args):
    value = validation_list(args.urls, args.batch)
    cache = args.cache_root
    binding = runner_binding(value["batch_id"], team.sha_file(args.batch / "manifest.json"),
                             "validation", value["urls_sha256"])
    with team.cache_lock(cache):
        if team.read_json(cache / "parallel_runner.json") != binding:
            raise ValueError("Validation cache does not match list/runner")
        rows = []
        with team.readonly(cache / "crawl.sqlite3") as db:
            db.row_factory = sqlite3.Row
            for row in db.execute("SELECT * FROM parsed WHERE stage=? ORDER BY item_key", (HISTORY_STAGE,)):
                rows.append(dict(row))
        summary = team.counts([{"publication_url": u} for u in value["urls"]],
                              {r["item_key"]: r["ok"] for r in rows})
        args.output.mkdir(parents=True, exist_ok=False)
        records = args.output / "records.jsonl.gz"
        with gzip.open(records, "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        manifest = dict(binding, **summary, records_sha256=team.sha_file(records),
                        exported_at=team.iso_now(), status="PARTIAL" if summary["pending"] else "COMPLETE")
        team.write_json(args.output / "manifest.json", manifest)
        return manifest


def field_differences(left, right, path="", limit=100):
    if left is not MISSING and right is not MISSING and left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        output = []
        for key in sorted(set(left) | set(right)):
            output.extend(field_differences(left.get(key, MISSING), right.get(key, MISSING),
                                            f"{path}/{key}", limit))
            if len(output) >= limit:
                return output[:limit]
        return output
    if isinstance(left, list) and isinstance(right, list):
        output = []
        for i in range(max(len(left), len(right))):
            a = left[i] if i < len(left) else MISSING
            b = right[i] if i < len(right) else MISSING
            output.extend(field_differences(a, b, f"{path}/{i}", limit))
            if len(output) >= limit:
                return output[:limit]
        return output
    return [{"path": path or "/",
             "local_sha256": None if left is MISSING else digest(left),
             "dcc_sha256": None if right is MISSING else digest(right)}]


def validation_compare(args):
    value = validation_list(args.urls, args.batch)
    manifest = team.read_json(args.export_dir / "manifest.json")
    required = runner_binding(value["batch_id"], team.sha_file(args.batch / "manifest.json"),
                              "validation", value["urls_sha256"])
    if any(manifest.get(key) != val for key, val in required.items()):
        raise ValueError("Test export belongs to a different URL list, batch, or runner")
    records = args.export_dir / "records.jsonl.gz"
    if team.sha_file(records) != manifest.get("records_sha256"):
        raise ValueError("Test export checksum mismatch")
    dcc = {}
    with gzip.open(records, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if set(row) != set(team.DB_FIELDS) or row.get("ok") not in (0, 1):
                raise ValueError("Malformed test record")
            key = row["item_key"]
            if key in dcc or key not in value["urls"] or row["stage"] != HISTORY_STAGE:
                raise ValueError("Duplicate, out-of-list, or wrong-stage test record")
            dcc[key] = row
    summary = team.counts([{"publication_url": u} for u in value["urls"]],
                          {key: row["ok"] for key, row in dcc.items()})
    if any(manifest.get(key) != val for key, val in summary.items()):
        raise ValueError("Test export manifest counts differ from records")
    if manifest.get("status") != ("PARTIAL" if summary["pending"] else "COMPLETE"):
        raise ValueError("Test export completeness label differs from records")
    local = {}
    with team.readonly(args.reference_db) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM parsed WHERE stage=? AND ok=1", (HISTORY_STAGE,)):
            if row["item_key"] in value["urls"]:
                local[row["item_key"]] = dict(row)
    if set(local) != set(value["urls"]):
        raise ValueError("Local reference no longer matches the frozen 21-URL list")
    comparisons = []
    for url in value["urls"]:
        old, new = local[url], dcc.get(url)
        if new is None or new.get("ok") != 1 or new.get("payload") is None:
            comparisons.append({"url": url, "status": "MISSING_OR_FAILED", "dcc_error_type": new.get("error_type") if new else None})
            continue
        before, after = json.loads(old["payload"]), json.loads(new["payload"])
        differences = field_differences(before, after)
        comparisons.append({"url": url, "status": "EXACT" if not differences else "DIFFERENT",
                            "local_payload_sha256": digest(before), "dcc_payload_sha256": digest(after),
                            "local_observed_at": old["observed_at"], "dcc_observed_at": new["observed_at"],
                            "differences": differences})
    exact = sum(c["status"] == "EXACT" for c in comparisons)
    report = {"status": "PASS_EXACT" if exact == 21 and len(dcc) == 21 else "REVIEW_REQUIRED",
              "exact": exact, "expected": 21, "dcc_rows": len(dcc),
              "urls_sha256": value["urls_sha256"], "test_export_manifest_sha256": team.sha_file(args.export_dir / "manifest.json"),
              "checked_at": datetime.now(timezone.utc).isoformat(), "comparisons": comparisons}
    team.write_json(args.report, report)
    return report, 0 if report["status"] == "PASS_EXACT" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Collect assigned shard with one 20-thread process")
    run.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    run.add_argument("--shard", type=int, choices=(1, 2), required=True)
    run.add_argument("--cache-root", type=Path)
    run.add_argument("--check", action="store_true")
    make = commands.add_parser("validation-list", help="Local owner: export only 21 successful URLs")
    make.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    make.add_argument("--reference-db", type=Path, required=True)
    make.add_argument("--output", type=Path, required=True)
    vr = commands.add_parser("validation-run", help="DCC: rescrape 21 URLs to a separate test cache")
    vr.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    vr.add_argument("--urls", type=Path, required=True)
    vr.add_argument("--cache-root", type=Path, required=True)
    ve = commands.add_parser("validation-export", help="DCC: export the separate test cache")
    ve.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    ve.add_argument("--urls", type=Path, required=True)
    ve.add_argument("--cache-root", type=Path, required=True)
    ve.add_argument("--output", type=Path, required=True)
    vc = commands.add_parser("validation-compare", help="Local owner: compare DCC test export with local checkpoint")
    vc.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    vc.add_argument("--urls", type=Path, required=True)
    vc.add_argument("--reference-db", type=Path, required=True)
    vc.add_argument("--export-dir", type=Path, required=True)
    vc.add_argument("--report", type=Path, required=True)
    for command in (run, vr):
        command.add_argument("--workers", type=int, default=20)
        command.add_argument("--delay-seconds", type=float, default=6.0)
        command.add_argument("--global-gap-seconds", type=float, default=0.6)
        command.add_argument("--limit", type=int, default=500)
        command.add_argument("--continuous", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        result, code = production(args)
    elif args.command == "validation-list":
        result, code = make_validation_list(args), 0
    elif args.command == "validation-run":
        result, code = validation_run(args)
    elif args.command == "validation-export":
        result, code = validation_export(args), 0
    else:
        result, code = validation_compare(args)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
