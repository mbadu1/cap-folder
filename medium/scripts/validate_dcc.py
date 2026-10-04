#!/usr/bin/env python3
"""Medium DCC validation on a fixed baseline sample, outside production caches."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import gzip
import json
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import time

from collect_medium_history import atomic_json, digest, now, profile_handle
from parallel_medium_collection import ROOT, TeamCollector, collect, sha_file

VERSION = "medium-dcc-validation-v1"
STAGES = {"smoke": (1, 5), "ten": (10, 20), "twenty": (20, 40)}
DEFAULT_SAMPLE = ROOT / "medium/validation/2026-10-03-baseline-40-parallel/feeds.json"


def source_hashes():
    paths = [Path(__file__), Path(__file__).with_name("parallel_medium_collection.py"),
             Path(__file__).with_name("collect_medium_history.py"),
             Path(__file__).with_name("build_medium_toy.py"), Path(__file__).with_name("medium_mirror_adapter.py"),
             ROOT / "substack/scripts/build_substack_platform_month.py"]
    return {str(p.relative_to(ROOT)): sha_file(p) for p in paths}


def load_sample(path):
    sample = json.loads(Path(path).read_text())
    if sample["version"] != VERSION or len(sample["feeds"]) != 40:
        raise ValueError("Require the fixed 40-feed validation sample")
    seen = set()
    for row in sample["feeds"]:
        handle = profile_handle(row["profile_url"])
        if (not handle or row["profile_url"] != "https://medium.com/@" + handle
                or row["feed_url"] != "https://medium.com/feed/@" + handle
                or row["profile_url"] in seen):
            raise ValueError("Invalid or duplicate validation profile/feed")
        seen.add(row["profile_url"])
    return sample


def payloads(db, records):
    """Compare normalized metadata/body hashes; no article text in the report."""
    result = {}
    by_hash = defaultdict(list)
    for row in records:
        creator = db.execute("SELECT profile_url,name,candidate_id,feed_count FROM creators WHERE profile_url=?", (row["profile_url"],)).fetchone()
        result[row["profile_url"]] = dict(creator=dict(creator) if creator else None, posts=[])
        if row.get("source_hash"):
            by_hash[row["source_hash"]].append(row["profile_url"])
    if by_hash:
        sql = """SELECT o.*,b.text_sha256,b.prose_words,b.access_status,b.fullness_status
            FROM observations o LEFT JOIN bodies b ON o.post_id=b.post_id
            AND o.source_method=b.source_method AND o.source_hash=b.source_hash
            WHERE o.source_method='official_profile_rss' AND o.source_hash IN (""" + ",".join("?" for _ in by_hash) + ")"
        for row in db.execute(sql, tuple(by_hash)):
            meta = json.loads(row["metadata_json"])
            post = dict(post_id=row["post_id"], published_at=row["published_at"], year_month=row["year_month"],
                        in_window=row["in_window"], title=row["title"],
                        candidate_creator_id=meta.get("candidate_creator_id"),
                        medium_story_url=meta.get("medium_story_url"), author_name=meta.get("author_name"),
                        updated_at_rss=meta.get("updated_at_rss"), native_tags=meta.get("native_tags"),
                        text_sha256=row["text_sha256"], prose_words=row["prose_words"],
                        access_status=row["access_status"], fullness_status=row["fullness_status"])
            for profile in by_hash[row["source_hash"]]:
                result[profile]["posts"].append(post)
    for value in result.values():
        value["posts"].sort(key=lambda r: (r["post_id"], r["published_at"]))
    return result


class ValidationCollector(TeamCollector):
    def prepare_validation(self, sample_path, stage, ledger_path=None, replay=False):
        sample = load_sample(sample_path)
        workers, count = STAGES[stage]
        self.records = sample["feeds"][:count]
        self.assigned = {r["profile_url"] for r in self.records}
        self.known_keys = set()
        binding = dict(version=VERSION, validation_only=True, stage=stage, workers=workers, expected_requests=count,
                       sample_sha256=sha_file(sample_path), global_gap_seconds=1.5, per_worker_gap_seconds=6.0,
                       source_hashes=source_hashes(), handoff_sha256=sha_file(ledger_path) if ledger_path else None,
                       offline_replay=replay)
        if not replay and not ledger_path:
            raise ValueError("Live validation needs the metadata reconciliation ledger")
        previous = self.get("validation_binding")
        if previous:
            if previous != binding:
                raise ValueError("Validation binding changed; preserve the cache and use a new one")
            self.db.execute("UPDATE tasks SET state='error',error='Interrupted validation; no automatic retry' WHERE state='inflight'")
            self.db.commit()
            return binding
        if self.db.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
            raise ValueError("Validation requires a separate empty cache")
        for row in self.records:
            self.add_profile(profile_handle(row["profile_url"]))
        self.configure_author_pool(count)
        self.set("validation_binding", binding)
        self.set("initialized", now())
        self.set("permission_basis", "user-requested bounded Medium DCC validation; baseline URLs only")
        if ledger_path:
            ledger = json.loads(gzip.decompress(Path(ledger_path).read_bytes()))
            if ledger["version"] != "medium-team-reconciliation-v1":
                raise ValueError("Invalid handoff ledger")
            for route in ledger["routes"]:
                if route["route"] == "feed" and route["state"] == "blocked":
                    raise ValueError("Handoff records a blocked feed route; review before live validation")
            atomic_json(self.cache / "request_gate.json", dict(last_start=0, cooldown_until=ledger["cooldown_until"], rate_events=ledger["rate_events"]))
        self.db.commit()
        atomic_json(self.cache / "validation_binding.json", binding)
        return binding

    def pick(self):
        # No mirrors/sitemaps/other profiles are requested during baseline tests.
        if self.blocked("feed"):
            return None
        row = self.db.execute("SELECT * FROM tasks WHERE kind='feed' AND state='pending' ORDER BY priority LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE tasks SET state='inflight' WHERE kind='feed' AND item_key=?", (row["item_key"],))
            self.db.commit()
            return dict(row)
        return None

    def heartbeat(self, state="running", reason=None):
        value = super().heartbeat(state, reason)
        value.update(validation_only=True, validation_binding=self.get("validation_binding"))
        atomic_json(self.output / "heartbeat.json", value)
        return value


def freeze(checkpoint, output, reference):
    """Read one consistent local snapshot; save public URLs and a private reference."""
    output, reference = Path(output), Path(reference)
    if output.exists() or reference.exists():
        raise FileExistsError("Use new sample/reference paths; preserve prior evidence")
    db = sqlite3.connect(Path(checkpoint).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = [dict(r) for r in db.execute("""SELECT a.profile_url,t.url feed_url,t.response_hash source_hash,
            c.candidate_id,c.feed_count,s.raw_path FROM author_pool_profiles a
            CROSS JOIN creators c ON c.profile_url=a.profile_url
            CROSS JOIN tasks t ON t.kind='feed' AND t.item_key=a.profile_url
            CROSS JOIN sources s ON s.sha256=t.response_hash
            WHERE t.state='done' AND c.source_hash=t.response_hash AND c.feed_count>0 AND s.raw_path IS NOT NULL""")]
        rows.sort(key=lambda r: digest("medium-dcc-validation-v1|" + r["profile_url"]))
        rows = rows[:40]
        if len(rows) != 40:
            raise ValueError("Need 40 successful retained baseline feeds")
        expected = payloads(db, rows)
        sample = dict(version=VERSION, generated_at=now(), snapshot_last_request_id=db.execute("SELECT MAX(id) FROM requests").fetchone()[0],
                      selection="SHA256(medium-dcc-validation-v1|profile_url), first 40 successful retained pool feeds",
                      feeds=[{k: v for k, v in r.items() if k != "raw_path"} for r in rows])
        # Save current-input replay evidence without touching the baseline DB.
        with tempfile.TemporaryDirectory(prefix="medium-validation-replay-") as temp:
            sample_path = Path(temp) / "feeds.json"
            sample_path.write_text(json.dumps(sample, indent=2) + "\n")
            c = ValidationCollector(Path(temp) / "cache", Path(temp) / "report", min_free_gb=0, max_cache_gb=0)
            try:
                c.prepare_validation(sample_path, "twenty", replay=True)
                for row in rows:
                    raw = gzip.decompress((Path(checkpoint).parent / row["raw_path"]).read_bytes())
                    if digest(raw) != row["source_hash"]:
                        raise ValueError("Baseline raw response hash mismatch")
                    c.parse(dict(kind="feed", item_key=row["profile_url"], url=row["feed_url"], bucket=""), raw, "offline-replay")
                actual = payloads(c.db, rows)
            finally:
                c.db.close()
        mismatches = [profile for profile in expected if expected[profile] != actual[profile]]
        output.mkdir(parents=True)
        atomic_json(output / "feeds.json", sample)
        ref = dict(version=VERSION, sample_sha256=sha_file(output / "feeds.json"), payloads=expected)
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(gzip.compress(json.dumps(ref, sort_keys=True).encode(), mtime=0))
        manifest = dict(version=VERSION, sample_sha256=sha_file(output / "feeds.json"), private_reference_sha256=sha_file(reference),
                        offline_replay_status="PASS_EXACT" if not mismatches else "REVIEW_REQUIRED",
                        offline_replay_mismatches=mismatches, source_hashes=source_hashes(),
                        no_network_requests=True, feeds=40, snapshot_last_request_id=sample["snapshot_last_request_id"])
        atomic_json(output / "manifest.json", manifest)
        return manifest
    finally:
        db.close()


def assess(cache):
    cache = Path(cache)
    db = sqlite3.connect((cache / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        binding = json.loads(db.execute("SELECT value FROM meta WHERE key='validation_binding'").fetchone()[0])
        requests = [dict(r) for r in db.execute("SELECT kind,item_key,status,error FROM requests ORDER BY id")]
        tasks = [dict(r) for r in db.execute("SELECT item_key,state,error FROM tasks WHERE kind='feed'")]
        starts = [json.loads(s) for s in (cache / "request_starts.jsonl").read_text().splitlines()] if (cache / "request_starts.jsonl").exists() else []
        gaps = [b["epoch"] - a["epoch"] for a, b in zip(starts, starts[1:])]
        by_worker = defaultdict(list)
        for row in starts:
            by_worker[row.get("worker_id")].append(row["epoch"])
        worker_gaps = [b - a for values in by_worker.values() for a, b in zip(values, values[1:])]
        expected = binding["expected_requests"]
        task_keys = {r["item_key"] for r in tasks}
        code_matches = all(sha_file(ROOT / name) == value for name, value in binding["source_hashes"].items())
        issues = []
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok": issues.append("SQLite integrity failure")
        if not code_matches: issues.append("Code differs from pinned validation binding")
        if len(requests) != expected or len(starts) != expected: issues.append("Incomplete or extra request/start records")
        if {r["item_key"] for r in requests} != task_keys or len(task_keys) != expected: issues.append("Requests differ from selected validation profiles")
        if any(r["kind"] != "feed" or r["status"] != 200 or r["error"] for r in requests): issues.append("HTTP/access/parser errors require review")
        if any(r["state"] != "done" for r in tasks): issues.append("Some validation feeds are not committed successful results")
        if gaps and min(gaps) + 0.001 < binding["global_gap_seconds"]: issues.append("Global request spacing violated")
        if None in by_worker or (worker_gaps and min(worker_gaps) + 0.001 < binding["per_worker_gap_seconds"]): issues.append("Per-worker request spacing missing or violated")
        if any((cache / name).exists() for name in ("STOP", "PAUSED.json")): issues.append("Stop/pause evidence requires review")
        result = dict(version=VERSION, assessed_at=now(), status="PASS" if not issues else "REVIEW_REQUIRED", issues=issues,
                      binding=binding, requests=len(requests), http_statuses={str(code): sum(r["status"] == code for r in requests) for code in {r["status"] for r in requests}},
                      minimum_global_gap_seconds=min(gaps) if gaps else None,
                      minimum_observed_worker_gap_seconds=min(worker_gaps) if worker_gaps else None,
                      workers_observed=len(by_worker), validation_only=True,
                      note="Bounded trial; PASS does not establish sustained throughput or final author/history/sample eligibility.")
        atomic_json(cache / "assessment.json", result)
        return result
    finally:
        db.close()


def compare(cache, sample_path, reference):
    sample = load_sample(sample_path)
    manifest = json.loads((Path(sample_path).parent / "manifest.json").read_text())
    if sha_file(sample_path) != manifest["sample_sha256"] or sha_file(reference) != manifest["private_reference_sha256"]:
        raise ValueError("Sample/reference hash mismatch; never rewrite the reference")
    reference_data = json.loads(gzip.decompress(Path(reference).read_bytes()))
    if reference_data["sample_sha256"] != sha_file(sample_path):
        raise ValueError("Reference belongs to a different sample")
    db = sqlite3.connect((Path(cache) / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        binding = json.loads(db.execute("SELECT value FROM meta WHERE key='validation_binding'").fetchone()[0])
        if binding["sample_sha256"] != sha_file(sample_path): raise ValueError("Cache belongs to a different sample")
        records = []
        for row in sample["feeds"][:binding["expected_requests"]]:
            task = db.execute("SELECT response_hash,state FROM tasks WHERE kind='feed' AND item_key=?", (row["profile_url"],)).fetchone()
            records.append(dict(profile_url=row["profile_url"], source_hash=task["response_hash"] if task and task["state"] == "done" else None))
        actual = payloads(db, records)
        differences = []
        raw_drift = []
        for row, current in zip(sample["feeds"], records):
            profile = row["profile_url"]
            if current["source_hash"] != row["source_hash"]: raw_drift.append(profile)
            if actual[profile] != reference_data["payloads"][profile]: differences.append(profile)
        result = dict(version=VERSION, compared_at=now(), profiles_compared=len(records),
                      status="REVIEW_REQUIRED" if differences else ("PASS_NORMALIZED" if raw_drift else "PASS_EXACT"),
                      payload_difference_profiles=differences, raw_source_changed_profiles=raw_drift,
                      sample_sha256=sha_file(sample_path), private_reference_sha256=sha_file(reference),
                      note="Observation time/raw source bytes may change; inspect drift and use same-response replay to diagnose parser differences. Never edit historical expected results.")
        atomic_json(Path(cache) / "comparison.json", result)
        return result
    finally:
        db.close()


def replay(cache, sample_path):
    """Reparse the exact DCC responses locally, without new HTTP requests."""
    cache = Path(cache)
    sample = load_sample(sample_path)
    db = sqlite3.connect((cache / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        binding = json.loads(db.execute("SELECT value FROM meta WHERE key='validation_binding'").fetchone()[0])
        if binding["sample_sha256"] != sha_file(sample_path) or binding["source_hashes"] != source_hashes():
            raise ValueError("Replay requires the original sample and pinned code")
        records = []
        for row in sample["feeds"][:binding["expected_requests"]]:
            task = db.execute("SELECT response_hash,state FROM tasks WHERE kind='feed' AND item_key=?", (row["profile_url"],)).fetchone()
            if not task or task["state"] != "done":
                raise ValueError("Replay requires all stage responses committed successfully")
            records.append(dict(row, source_hash=task["response_hash"]))
        expected = payloads(db, records)
        with tempfile.TemporaryDirectory(prefix="medium-dcc-response-replay-") as temp:
            c = ValidationCollector(Path(temp) / "cache", Path(temp) / "report", min_free_gb=0, max_cache_gb=0)
            try:
                c.prepare_validation(sample_path, binding["stage"], replay=True)
                for row in records:
                    source = db.execute("SELECT raw_path FROM sources WHERE sha256=?", (row["source_hash"],)).fetchone()
                    raw = gzip.decompress((cache / source["raw_path"]).read_bytes())
                    if digest(raw) != row["source_hash"]: raise ValueError("Retained DCC response hash mismatch")
                    c.parse(dict(kind="feed", item_key=row["profile_url"], url=row["feed_url"], bucket=""), raw, "offline-replay")
                actual = payloads(c.db, records)
            finally:
                c.db.close()
        differences = [profile for profile in expected if expected[profile] != actual[profile]]
        result = dict(version=VERSION, replayed_at=now(), profiles_replayed=len(records), no_network_requests=True,
                      status="REVIEW_REQUIRED" if differences else "PASS_EXACT", payload_difference_profiles=differences,
                      sample_sha256=sha_file(sample_path), source_hashes=source_hashes())
        atomic_json(cache / "same_response_replay.json", result)
        return result
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("freeze")
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    for name in ("run", "assess", "compare", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--cache-root", type=Path, required=True)
        if name != "assess": p.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
        if name == "compare": p.add_argument("--reference", type=Path, required=True)
        if name == "run":
            p.add_argument("--stage", choices=STAGES, required=True)
            p.add_argument("--handoff-ledger", type=Path, required=True)
            p.add_argument("--previous-assessment", type=Path)
            p.add_argument("--exclusive-team-window", action="store_true", help=argparse.SUPPRESS)  # Legacy no-op.
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.checkpoint, args.output, args.reference)
    elif args.command == "assess": result = assess(args.cache_root)
    elif args.command == "compare": result = compare(args.cache_root, args.sample, args.reference)
    elif args.command == "replay": result = replay(args.cache_root, args.sample)
    else:
        if not os.environ.get("SLURM_JOB_ID") or "login" in os.uname().nodename.lower(): parser.error("Use dcc-agent on a verified compute allocation")
        manifest = json.loads((args.sample.parent / "manifest.json").read_text())
        if (manifest["offline_replay_status"] != "PASS_EXACT" or manifest["sample_sha256"] != sha_file(args.sample)
                or manifest["source_hashes"] != source_hashes()):
            parser.error("Frozen local replay/sample/code must pass before live validation")
        if args.stage != "smoke":
            previous_stage = "smoke" if args.stage == "ten" else "ten"
            if not args.previous_assessment: parser.error("Review/pass the preceding stage and supply its assessment")
            prior = json.loads(args.previous_assessment.read_text())
            if (prior["status"] != "PASS" or prior["binding"]["stage"] != previous_stage
                    or prior["binding"]["sample_sha256"] != sha_file(args.sample)
                    or prior["binding"]["source_hashes"] != source_hashes()
                    or prior["binding"]["handoff_sha256"] != sha_file(args.handoff_ledger)):
                parser.error("Prior stage did not pass with this sample/code/handoff")
        args.cache_root.mkdir(parents=True, exist_ok=True)
        with (args.cache_root / "collector.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if any((args.cache_root / name).exists() for name in ("STOP", "PAUSED.json")): parser.error("Preserve/diagnose validation stop markers")
            c = ValidationCollector(args.cache_root, args.cache_root / "report", max_cache_gb=0)
            try:
                binding = c.prepare_validation(args.sample, args.stage, args.handoff_ledger)
                signal.signal(signal.SIGINT, lambda *_: c.active_gate.stop.set() if hasattr(c, "active_gate") else None)
                signal.signal(signal.SIGTERM, lambda *_: c.active_gate.stop.set() if hasattr(c, "active_gate") else None)
                started = time.monotonic()
                state = collect(c, binding["workers"], binding["expected_requests"])
                c.report(state)
                atomic_json(args.cache_root / "run_receipt.json", dict(state=state, elapsed_seconds=time.monotonic() - started,
                    job=os.environ.get("SLURM_JOB_ID"), node=os.uname().nodename, pid=os.getpid(), ended_at=now(), binding=binding))
            finally:
                c.db.close()
            result = assess(args.cache_root)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status", result.get("offline_replay_status")) in ("PASS", "PASS_EXACT", "PASS_NORMALIZED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
