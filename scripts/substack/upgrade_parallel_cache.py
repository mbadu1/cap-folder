#!/usr/bin/env python3
"""Copy a stopped predecessor checkpoint to a separately bound successor cache.

Never rewrites a live/source runner binding or clears a stop/pause marker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import parallel_team_collection as parallel

team = parallel.team
PREDECESSOR_SHA256 = "d81df221edf001d9bf23eda0d89f1121f586fa526a472400d37a66dc6a408182"
INVITE_ONLY_PREDECESSOR_SHA256 = "087aab2c8aeebd9557336edbc7343bdd467f8008163a578597bcbad4d603a6c2"
SUPPORTED_PREDECESSORS = {PREDECESSOR_SHA256, INVITE_ONLY_PREDECESSOR_SHA256}


def checkpoint_digest(path):
    digest = hashlib.sha256()
    count = 0
    with team.readonly(path) as db:
        for row in db.execute("SELECT * FROM parsed ORDER BY stage,item_key"):
            digest.update(parallel.canonical(list(row)) + b"\n")
            count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def upgrade(args):
    batch, rows, target, expected = team.setup(args)
    source = args.source_cache
    if source.resolve() == target.resolve():
        raise ValueError("Upgrade requires a separate new cache")
    if not (source / "crawl.sqlite3").is_file():
        raise FileNotFoundError("Source checkpoint is missing")
    new_binding = parallel.runner_binding(batch["batch_id"], team.sha_file(args.batch / "manifest.json"), args.shard)
    with team.cache_lock(source), team.cache_lock(target):
        team.check_binding(source, expected)
        if (source / "RETIRED.json").exists():
            raise RuntimeError("Source cache is already retired")
        old_binding = team.read_json(source / "parallel_runner.json")
        if (old_binding.get("runner_sha256") not in SUPPORTED_PREDECESSORS
                or old_binding != dict(new_binding, runner_sha256=old_binding.get("runner_sha256"))):
            raise ValueError("Source is not the supported pinned predecessor")
        if any(p.name != "collector.lock" for p in target.iterdir()):
            raise FileExistsError("Destination must be empty")
        summary = team.counts(rows, team.checkpoint_keys(source / "crawl.sqlite3"))
        before = checkpoint_digest(source / "crawl.sqlite3")
        with sqlite3.connect(target / "crawl.sqlite3") as destination:
            with team.readonly(source / "crawl.sqlite3") as db:
                db.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Copied checkpoint failed SQLite integrity")
        after = checkpoint_digest(target / "crawl.sqlite3")
        if before != after:
            raise ValueError("Copied checkpoint rows differ")
        for name in ("PAUSED.json", "STOP"):
            if (source / name).exists():
                shutil.copyfile(source / name, target / name)
        record = dict(expected, **summary, at=team.iso_now(), source_cache=str(source.resolve()),
                      target_cache=str(target.resolve()), predecessor_binding=old_binding,
                      successor_binding=new_binding, exact_rows=after,
                      pause_preserved=(target / "PAUSED.json").exists(),
                      stop_preserved=(target / "STOP").exists())
        team.write_json(target / "runner_upgrade.json", record)
        team.write_json(target / "collection_status.json", dict(record, state="migrated", worker_pid=None))
        team.write_json(target / "parallel_runner.json", new_binding)
        # An unbound destination is unusable until all checks and source retirement finish.
        (source / "STOP").write_text("Retired after runner upgrade to " + str(target.resolve()) + "\n")
        team.write_json(source / "RETIRED.json", {"new_cache": str(target.resolve()), "at": team.iso_now()})
        team.write_json(target / "assignment.json", expected)
        return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=parallel.DEFAULT_BATCH)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    print(json.dumps(upgrade(parser.parse_args()), indent=2))
