#!/usr/bin/env python3
"""Prepare, collect, export, and merge disjoint Substack publication assignments.

All commands run from an ordinary repository checkout. Only `run` makes network
requests. The existing history parser is shared by every worker.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import urllib.error

from collect_substack_history import HISTORY_STAGE, parse_publication_history
from build_substack_platform_month import Checkpoint, Collector, CrawlCircuitOpen, iso_now

ROOT = Path(__file__).resolve().parents[2]
START, END = "2020-01-01", "2026-09-17"
CODE = ("team_collection.py", "collect_substack_history.py", "build_substack_platform_month.py")
FIELDS = ["publication_url", "frame_lastmod", "frame_priority"]
DB_FIELDS = ["stage", "item_key", "ok", "payload", "error_type", "error_message", "observed_at"]
PART_BYTES = 8 * 1024 * 1024


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, fields, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def checkpoint_keys(path):
    if not Path(path).is_file():
        return {}
    with readonly(path) as db:
        return dict(db.execute("SELECT item_key, ok FROM parsed WHERE stage=?", (HISTORY_STAGE,)))


def code_hashes():
    return {name: sha_file(Path(__file__).parent / name) for name in CODE}


@contextmanager
def cache_lock(cache):
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "collector.lock").open("a+b") as f:
        try:
            if os.name == "nt":
                import msvcrt
                if f.tell() == 0:
                    f.write(b"0")
                    f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another process holds this shard's checkpoint lock") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def shard_numbers(manifest):
    names = set(manifest["shard_counts"])
    if names not in ({"shard-1", "shard-2"}, {"shard-1", "shard-2", "shard-3"}):
        raise ValueError("A batch must declare exactly two or three consecutive shards")
    return tuple(range(1, len(names) + 1))


def validate_batch(batch, check_code=True):
    batch = Path(batch)
    m = read_json(batch / "manifest.json")
    if (m["start_date"], m["end_date"]) != (START, END):
        raise ValueError("Assignment study dates do not match the collector")
    for name, digest in m["files"].items():
        if Path(name).name != name or sha_file(batch / name) != digest:
            raise ValueError("Assignment file missing or checksum mismatch: " + name)
    if check_code and m["code_sha256"] != code_hashes():
        raise ValueError("Collector code differs from the frozen handoff; use its shared revision")
    frame = read_csv(batch / "source_frame.csv")
    all_urls = {r["publication_url"] for r in frame}
    if len(frame) != len(all_urls) or len(frame) != m["frame_count"]:
        raise ValueError("Parent frame contains duplicates or has the wrong count")
    original = {r["publication_url"]: r for r in frame}
    for r in frame:
        expected = hashlib.sha256(("publication-frame-v1|substack|" + r["publication_url"]).encode()).hexdigest()
        if r["frame_priority"] != expected:
            raise ValueError("Parent publication priority mismatch")
    attempted = read_csv(batch / "already_attempted.csv")
    seen = {r["publication_url"] for r in attempted}
    if len(seen) != len(attempted) or not seen <= all_urls or len(seen) != m["baseline_count"]:
        raise ValueError("Baseline inventory mismatch")
    shards = {}
    for n in shard_numbers(m):
        name = f"shard-{n}"
        rows = read_csv(batch / (name + ".csv"))
        sm = read_json(batch / (name + ".manifest.json"))
        urls = {r["publication_url"] for r in rows}
        if len(urls) != len(rows) or seen & urls or not urls <= all_urls:
            raise ValueError("Assignments overlap or contain invalid URLs")
        if (sm["shard"], sm["batch_id"], sm["frozen_publications"]) != (name, m["batch_id"], len(rows)):
            raise ValueError("Shard identity or count mismatch")
        if m["shard_counts"][name] != len(rows):
            raise ValueError("Batch shard count mismatch")
        if sm["frame_csv_sha256"] != m["files"][name + ".csv"]:
            raise ValueError("Shard manifest checksum mismatch")
        if (sm["study_start_date"], sm["study_end_date"]) != (START, END):
            raise ValueError("Shard manifest study dates mismatch")
        if any(r != original[r["publication_url"]] for r in rows):
            raise ValueError("Shard metadata differs from the original frame")
        seen.update(urls)
        shards[n] = rows
    if seen != all_urls:
        raise ValueError("Baseline plus shards do not cover the parent frame")
    if sum(map(len, shards.values())) != m["remaining_count"]:
        raise ValueError("Batch remaining count mismatch")
    return m, shards


def prepare(frame, baseline, output, shard_count=3):
    if shard_count not in (2, 3):
        raise ValueError("Choose two or three shards")
    frame, baseline, output = map(Path, (frame, baseline, output))
    if not baseline.is_file():
        raise FileNotFoundError(baseline)
    rows = read_csv(frame)
    fm = read_json(frame.with_suffix(".manifest.json"))
    if fm["frame_csv_sha256"] != sha_file(frame):
        raise ValueError("Original frozen-frame checksum mismatch")
    rows.sort(key=lambda r: (r["frame_priority"], r["publication_url"]))
    urls = {r["publication_url"] for r in rows}
    if len(rows) != len(urls):
        raise ValueError("Original frame has duplicate publication URLs")
    keys = checkpoint_keys(baseline)
    attempted = [{"publication_url": u, "ok": int(ok)} for u, ok in sorted(keys.items()) if u in urls]
    pending = [r for r in rows if r["publication_url"] not in keys]
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "source_frame.csv", FIELDS, rows)
    write_csv(output / "already_attempted.csv", ["publication_url", "ok"], attempted)
    write_csv(output / "existing_errors.csv", ["publication_url", "ok"], [r for r in attempted if not r["ok"]])
    counts = {}
    for n in range(1, shard_count + 1):
        assigned = pending[n - 1::shard_count]
        name = f"shard-{n}"
        path = output / (name + ".csv")
        write_csv(path, FIELDS, assigned)
        counts[name] = len(assigned)
        write_json(path.with_suffix(".manifest.json"), {
            "batch_id": output.name, "shard": name, "created_at": iso_now(),
            "study_start_date": START, "study_end_date": END,
            "frame_csv_sha256": sha_file(path), "frozen_publications": len(assigned),
            "parent_frame_sha256": fm["frame_csv_sha256"],
        })
    m = {"batch_id": output.name, "created_at": iso_now(), "start_date": START, "end_date": END,
         "frame_count": len(rows), "baseline_count": len(attempted),
         "baseline_successes": sum(r["ok"] for r in attempted),
         "baseline_errors": sum(not r["ok"] for r in attempted),
         "baseline_out_of_frame": len(set(keys) - urls), "remaining_count": len(pending),
         "shard_counts": counts, "assignment_rule": "existing_priority_sorted_round_robin_v1",
         "min_delay_seconds_per_worker": 6.0, "code_sha256": code_hashes(),
         "parent_frame_original_sha256": fm["frame_csv_sha256"],
         "files": {p.name: sha_file(p) for p in sorted(output.iterdir())}}
    write_json(output / "manifest.json", m)
    validate_batch(output)
    return m


def repartition(source_batch, output):
    """Retain owners 1/2 and distribute former shard 3 without moving their work."""
    source_batch, output = Path(source_batch), Path(output)
    source, shards = validate_batch(source_batch, check_code=False)
    if set(shards) != {1, 2, 3}:
        raise ValueError("Repartition requires a three-shard source batch")
    # Only the orchestration changes; cached parser output must remain compatible.
    current = code_hashes()
    for name in CODE:
        if name != "team_collection.py" and source["code_sha256"][name] != current[name]:
            raise ValueError("Source parser differs; cannot reuse its checkpoints")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("source_frame.csv", "already_attempted.csv", "existing_errors.csv"):
        shutil.copyfile(source_batch / name, output / name)
    counts = {}
    former_three = sorted(shards[3], key=lambda r: (r["frame_priority"], r["publication_url"]))
    for n in (1, 2):
        rows = sorted(shards[n] + former_three[n - 1::2],
                      key=lambda r: (r["frame_priority"], r["publication_url"]))
        name = f"shard-{n}"
        path = output / (name + ".csv")
        write_csv(path, FIELDS, rows)
        counts[name] = len(rows)
        write_json(path.with_suffix(".manifest.json"), {
            "batch_id": output.name, "shard": name, "created_at": iso_now(),
            "study_start_date": START, "study_end_date": END,
            "frame_csv_sha256": sha_file(path), "frozen_publications": len(rows),
            "parent_frame_sha256": source["parent_frame_original_sha256"],
        })
    manifest = dict(source, batch_id=output.name, created_at=iso_now(), shard_counts=counts,
                    assignment_rule="retain_shards_1_2_split_former_3_priority_alternating_v2",
                    code_sha256=current, supersedes_batch={
                        "batch_id": source["batch_id"],
                        "manifest_sha256": sha_file(source_batch / "manifest.json"),
                        "migration_owners": {"1": 1, "2": 2},
                    }, files={p.name: sha_file(p) for p in sorted(output.iterdir())})
    write_json(output / "manifest.json", manifest)
    validate_batch(output)
    return manifest


def setup(args):
    batch, shards = validate_batch(args.batch)
    if args.shard not in shards:
        raise ValueError("Shard is not part of this batch")
    cache = args.cache_root or ROOT / ".cache/substack_shards" / batch["batch_id"] / f"shard-{args.shard}"
    expected = {"batch_id": batch["batch_id"], "shard": args.shard,
                "batch_manifest_sha256": sha_file(args.batch / "manifest.json"),
                "assignment_sha256": batch["files"][f"shard-{args.shard}.csv"],
                "code_sha256": batch["code_sha256"]}
    return batch, shards[args.shard], cache, expected


def migrate(args):
    """Copy an existing owner's frozen checkpoint into a new, larger assignment."""
    batch, rows, cache, expected = setup(args)
    source, old_shards = validate_batch(args.source_batch, check_code=False)
    parent = batch.get("supersedes_batch", {})
    if (parent.get("batch_id") != source["batch_id"] or
            parent.get("manifest_sha256") != sha_file(args.source_batch / "manifest.json") or
            parent.get("migration_owners", {}).get(str(args.source_shard)) != args.shard):
        raise ValueError("Migration source batch or owner does not match this cutover")
    for name in CODE:
        if name != "team_collection.py" and source["code_sha256"][name] != batch["code_sha256"][name]:
            raise ValueError("Cannot migrate between different parsers")
    old_rows = old_shards[args.source_shard]
    if not {r["publication_url"] for r in old_rows} <= {r["publication_url"] for r in rows}:
        raise ValueError("New assignment does not contain all of the source owner's URLs")
    old_cache = args.source_cache
    old_expected = {"batch_id": source["batch_id"], "shard": args.source_shard,
                    "batch_manifest_sha256": parent["manifest_sha256"],
                    "assignment_sha256": source["files"][f"shard-{args.source_shard}.csv"],
                    "code_sha256": source["code_sha256"]}
    if not (old_cache / "crawl.sqlite3").is_file():
        raise FileNotFoundError("No old checkpoint to migrate; use a fresh new-batch run instead")
    if cache.resolve() == old_cache.resolve():
        raise ValueError("Migration needs a separate new cache")
    with cache_lock(old_cache), cache_lock(cache):
        check_binding(old_cache, old_expected)
        if (old_cache / "RETIRED.json").exists():
            raise RuntimeError("Source checkpoint was already migrated; use its recorded destination")
        if any(p.name != "collector.lock" for p in cache.iterdir()):
            raise FileExistsError("New cache must be empty; preserve existing work instead of overwriting")
        keys = checkpoint_keys(old_cache / "crawl.sqlite3")
        counts(old_rows, keys)
        summary = counts(rows, keys)
        # Copy exact checkpoint bytes through SQLite's consistent backup API.
        # Keep a failed migration unbound so run() cannot accidentally use it.
        destination = sqlite3.connect(cache / "crawl.sqlite3")
        try:
            with readonly(old_cache / "crawl.sqlite3") as db:
                db.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Migrated checkpoint failed SQLite integrity check")
        finally:
            destination.close()
        for name in ("STOP", "PAUSED.json"):
            if (old_cache / name).exists():
                shutil.copyfile(old_cache / name, cache / name)
        state = "access_or_rate_stop" if (cache / "PAUSED.json").exists() else "stopped" if (cache / "STOP").exists() else "migrated"
        result = dict(expected, **summary, updated_at=iso_now(), state=state, worker_pid=None,
                      migrated_from=str(old_cache.resolve()), source_batch_id=source["batch_id"],
                      source_shard=args.source_shard, request_count_this_run=0,
                      reason="Offline migration; existing stop/pause markers preserved")
        write_json(cache / "migration.json", result)
        write_json(cache / "collection_status.json", result)
        # Old revisions recognize STOP too. Both caches must never collect in parallel.
        (old_cache / "STOP").write_text("Retired after migration to " + str(cache.resolve()) + "\n", encoding="utf-8")
        write_json(old_cache / "RETIRED.json", {"new_batch": batch["batch_id"], "new_cache": str(cache.resolve()), "at": iso_now()})
        write_json(cache / "assignment.json", expected)
        return result


def check_binding(cache, expected, create=False):
    binding = cache / "assignment.json"
    if binding.exists():
        if read_json(binding) != expected:
            raise ValueError("Checkpoint belongs to a different assignment or code version")
    elif (cache / "crawl.sqlite3").exists():
        raise ValueError("Unbound database: refusing to attach it to an assignment")
    elif create:
        write_json(binding, expected)
    else:
        raise FileNotFoundError("Shard has not been initialized: " + str(cache))


def counts(rows, keys):
    assigned = {r["publication_url"] for r in rows}
    if set(keys) - assigned:
        raise ValueError("Checkpoint contains URLs outside the assigned shard")
    good = sum(bool(v) for v in keys.values())
    return dict(assigned=len(rows), successful=good, failed=len(keys) - good,
                pending=len(rows) - len(keys), attempted_complete=len(keys) == len(rows))


def run(args):
    batch, rows, cache, expected = setup(args)
    if args.limit < 1 or args.limit > 500:
        raise ValueError("--limit must be between 1 and 500")
    if not math.isfinite(args.delay_seconds) or args.delay_seconds < batch["min_delay_seconds_per_worker"]:
        raise ValueError("Request interval is below the frozen team setting")
    if args.check:
        result = {"validation": "passed", "assignment": expected, "assigned": len(rows), "cache": str(cache)}
        if (cache / "assignment.json").exists():
            check_binding(cache, expected)
            result.update(counts(rows, checkpoint_keys(cache / "crawl.sqlite3")))
        return result, 0
    with cache_lock(cache):
        if (cache / "RETIRED.json").exists():
            raise RuntimeError("This checkpoint was retired by a migration; use the new batch/cache")
        if (cache / "PAUSED.json").exists():
            raise RuntimeError("This shard has an access/rate pause; inspect PAUSED.json before deliberate resumption")
        check_binding(cache, expected, create=True)
        checkpoint = Checkpoint(cache / "crawl.sqlite3")
        collector = Collector(args.delay_seconds, 3, 600)
        stopped = [False]
        old_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        for sig in old_handlers:
            signal.signal(sig, lambda *_: stopped.__setitem__(0, True))
        reason, state = None, "running"
        keys = checkpoint_keys(cache / "crawl.sqlite3")
        counts(rows, keys)
        fetched = 0

        def report():
            result = dict(expected, **counts(rows, keys), updated_at=iso_now(), worker_pid=os.getpid(),
                          state=state, reason=reason, request_count_this_run=collector.request_count,
                          http_status_counts_this_run=dict(collector.http_status_counts),
                          delay_seconds=args.delay_seconds, new_publications_this_run=fetched)
            write_json(cache / "collection_status.json", result)
            return result

        try:
            report()
            for row in rows:
                if row["publication_url"] in keys:
                    continue
                if stopped[0] or (cache / "STOP").exists():
                    state = "stopped"
                    break
                if not args.continuous and fetched >= args.limit:
                    state = "chunk_complete"
                    break
                url = row["publication_url"]
                try:
                    payload = parse_publication_history(collector, url, START, END)
                    checkpoint.put(HISTORY_STAGE, url, True, payload, None)
                    keys[url] = 1
                except CrawlCircuitOpen as exc:
                    state, reason = "rate_limit_stop", str(exc)
                    break
                except Exception as exc:
                    checkpoint.put(HISTORY_STAGE, url, False, None, exc)
                    keys[url] = 0
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 429):
                        state, reason = "access_or_rate_stop", str(exc)
                fetched += 1
                result = report()
                print(json.dumps({"publication_url": url, "ok": keys[url], **counts(rows, keys)}), flush=True)
                if state == "access_or_rate_stop":
                    break
            else:
                state = "attempted_complete"
            if not counts(rows, keys)["pending"]:
                state = "attempted_complete"
            result = report()
            if state.endswith("stop"):
                write_json(cache / "PAUSED.json", {"state": state, "reason": reason, "at": iso_now(),
                           "action": "Inspect the failure and honor Retry-After/cooldown before deliberately removing this marker."})
        finally:
            checkpoint.close()
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
        code = 3 if state.endswith("stop") else 5 if state == "stopped" else 4 if result["pending"] else 2 if result["failed"] else 0
        return result, code


def export(args):
    batch, rows, cache, expected = setup(args)
    with cache_lock(cache):
        check_binding(cache, expected)
        keys = checkpoint_keys(cache / "crawl.sqlite3")
        summary = counts(rows, keys)
        output = args.output
        output.mkdir(parents=True, exist_ok=False)
        part_files, buffer = [], bytearray()

        def flush():
            name = f"records-{len(part_files):05d}.jsonl.gz"
            (output / name).write_bytes(gzip.compress(bytes(buffer), mtime=0))
            part_files.append({"file": name, "sha256": sha_file(output / name)})
            buffer.clear()

        with readonly(cache / "crawl.sqlite3") as db:
            db.row_factory = sqlite3.Row
            for r in db.execute("SELECT * FROM parsed WHERE stage=? ORDER BY item_key", (HISTORY_STAGE,)):
                raw = (json.dumps(dict(r), ensure_ascii=False) + "\n").encode()
                position = 0
                while position < len(raw):
                    n = min(PART_BYTES - len(buffer), len(raw) - position)
                    buffer.extend(raw[position:position + n])
                    position += n
                    if len(buffer) == PART_BYTES:
                        flush()
        if buffer:
            flush()
        manifest = dict(expected, **summary, exported_at=iso_now(), parts=part_files,
                        format="concatenated-decompressed-utf8-jsonl-v1", partial=bool(summary["pending"]))
        status = cache / "collection_status.json"
        if status.exists():
            write_json(output / "collection_status.json", read_json(status))
            manifest["status_sha256"] = sha_file(output / "collection_status.json")
        write_json(output / "manifest.json", manifest)
        return manifest


def exported_rows(path, manifest):
    pending = b""
    for part in manifest["parts"]:
        name = part["file"]
        if Path(name).name != name or sha_file(path / name) != part["sha256"]:
            raise ValueError("Export part path/checksum mismatch")
        pending += gzip.decompress((path / name).read_bytes())
        *lines, pending = pending.split(b"\n")
        for line in lines:
            yield json.loads(line)
    if pending:
        raise ValueError("Truncated JSONL export")


def merge(args):
    batch, shards = validate_batch(args.batch)
    if args.output.exists():
        raise FileExistsError("Refusing to overwrite an existing merged database")
    baseline_inventory = {r["publication_url"]: int(r["ok"]) for r in read_csv(args.batch / "already_attempted.csv")}
    baseline_keys = checkpoint_keys(args.baseline_db)
    if any(baseline_keys.get(k) != v for k, v in baseline_inventory.items()):
        raise ValueError("Baseline does not match the cutover inventory")
    full_frame = {r["publication_url"] for r in read_csv(args.batch / "source_frame.csv")}
    if set(baseline_keys) & full_frame != set(baseline_inventory):
        raise ValueError("Baseline has additional in-frame work after the cutover")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".partial.sqlite3")
    if temp.exists():
        raise FileExistsError("A prior partial merge exists; preserve/inspect it before retrying")
    target = Checkpoint(temp)
    seen, owners = set(), set()
    try:
        with readonly(args.baseline_db) as baseline:
            baseline.row_factory = sqlite3.Row
            for row in baseline.execute("SELECT * FROM parsed WHERE stage=?", (HISTORY_STAGE,)):
                target.connection.execute("INSERT INTO parsed VALUES(?,?,?,?,?,?,?)", tuple(row[k] for k in DB_FIELDS))
                seen.add(row["item_key"])
        target.connection.commit()
        for path in args.exports:
            m = read_json(path / "manifest.json")
            n = m["shard"]
            if n not in shards or n in owners or m["batch_manifest_sha256"] != sha_file(args.batch / "manifest.json"):
                raise ValueError("Duplicate shard or wrong export batch")
            if m["code_sha256"] != batch["code_sha256"] or m["assignment_sha256"] != batch["files"][f"shard-{n}.csv"]:
                raise ValueError("Export code/assignment does not match batch")
            owners.add(n)
            assigned = {r["publication_url"] for r in shards[n]}
            keys = {}
            for row in exported_rows(path, m):
                key = row["item_key"]
                if row["stage"] != HISTORY_STAGE or key not in assigned or key in seen or row["ok"] not in (0, 1):
                    raise ValueError("Out-of-shard, duplicate, or malformed exported record")
                if row["ok"]:
                    payload = json.loads(row["payload"])
                    if (payload["publication_url"], payload["start_date"], payload["end_date"]) != (key, START, END):
                        raise ValueError("Export payload identity or dates mismatch")
                target.connection.execute("INSERT INTO parsed VALUES(?,?,?,?,?,?,?)", tuple(row[k] for k in DB_FIELDS))
                seen.add(key)
                keys[key] = row["ok"]
            actual = counts(shards[n], keys)
            if any(m.get(k) != v for k, v in actual.items()) or actual["pending"]:
                raise ValueError("Incomplete export or incorrect exported counts")
            target.connection.commit()
        if owners != set(shards) or seen & full_frame != full_frame:
            raise ValueError("Merge does not cover the full frame and every declared shard")
        if target.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Merged database integrity check failed")
    finally:
        target.close()
    temp.replace(args.output)
    result = dict(batch_id=batch["batch_id"], merged_at=iso_now(), in_frame_publications=len(full_frame),
                  retained_out_of_frame=len(seen - full_frame), state="attempted_complete_audit_required")
    write_json(args.output.with_suffix(".manifest.json"), result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Offline: requires a stopped, backed-up baseline")
    prep.add_argument("--frame", type=Path, required=True)
    prep.add_argument("--baseline-db", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--shards", type=int, choices=(2, 3), default=3)
    repart = sub.add_parser("repartition", help="Offline: retain owners 1/2 and split former shard 3")
    repart.add_argument("--source-batch", type=Path, required=True)
    repart.add_argument("--output", type=Path, required=True)
    migration = sub.add_parser("migrate", help="Offline: copy a stopped old checkpoint to its new assignment")
    migration.add_argument("--source-batch", type=Path, required=True)
    migration.add_argument("--source-shard", type=int, choices=(1, 2), required=True)
    migration.add_argument("--source-cache", type=Path, required=True)
    migration.add_argument("--batch", type=Path, required=True)
    migration.add_argument("--shard", type=int, choices=(1, 2), required=True)
    migration.add_argument("--cache-root", type=Path)
    for command in ("run", "status", "export"):
        parser = sub.add_parser(command)
        parser.add_argument("--batch", type=Path, required=True)
        parser.add_argument("--shard", type=int, choices=(1, 2, 3), required=True)
        parser.add_argument("--cache-root", type=Path)
        if command == "run":
            parser.add_argument("--limit", type=int, default=500)
            parser.add_argument("--continuous", action="store_true")
            parser.add_argument("--delay-seconds", type=float, default=6.0)
            parser.add_argument("--check", action="store_true", help="Offline preflight only")
        if command == "export":
            parser.add_argument("--output", type=Path, required=True)
    merger = sub.add_parser("merge")
    merger.add_argument("--batch", type=Path, required=True)
    merger.add_argument("--baseline-db", type=Path, required=True)
    merger.add_argument("--exports", type=Path, nargs="+", required=True)
    merger.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    code = 0
    if args.command == "prepare":
        result = prepare(args.frame, args.baseline_db, args.output, args.shards)
    elif args.command == "repartition":
        result = repartition(args.source_batch, args.output)
    elif args.command == "migrate":
        result = migrate(args)
    elif args.command == "run":
        result, code = run(args)
    elif args.command == "export":
        result = export(args)
    elif args.command == "merge":
        result = merge(args)
    else:
        batch, rows, cache, expected = setup(args)
        check_binding(cache, expected)
        result = dict(expected, **counts(rows, checkpoint_keys(cache / "crawl.sqlite3")), cache=str(cache))
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
