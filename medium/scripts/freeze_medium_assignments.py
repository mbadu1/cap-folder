#!/usr/bin/env python3
"""Freeze read-only Medium candidate worklists; never start or reconfigure workers."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

FIELDS = ["rank", "creator_key", "profile_url", "feed_url", "priority", "stage", "owner", "shard"]
OWNERS = ("Zherui", "Ziyang")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows, fields=FIELDS):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def freeze(checkpoint, output, target=250000, reserve=50000):
    if target <= 0 or reserve < 0:
        raise ValueError("Target must be positive and reserve nonnegative")
    if output.exists():
        raise FileExistsError("Refusing to replace an existing frozen assignment directory")
    checkpoint = checkpoint.resolve()
    db = sqlite3.connect(checkpoint.as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        policy_row = db.execute("SELECT value FROM meta WHERE key='author_pool_policy'").fetchone()
        if not policy_row or json.loads(policy_row[0])["target"] != target:
            raise ValueError("Assignment target must match the existing acquisition policy")
        counter = json.loads(db.execute("SELECT value FROM meta WHERE key='author_pool_count'").fetchone()[0])
        last_request = db.execute("SELECT MAX(id) FROM requests").fetchone()[0]
        aliases = [dict(r) for r in db.execute(
            "SELECT creator_key,profile_url FROM author_pool_profiles ORDER BY creator_key,profile_url")]
        representative = {}
        for row in aliases:
            representative.setdefault(row["creator_key"], row["profile_url"])
        if len(representative) != counter:
            raise ValueError("Committed pool count does not reconcile to its profile registry")
        if counter > target:
            raise ValueError("Existing pool exceeds target; no automatic exclusion allowed")
        needed = target - counter
        # Uses pending_tasks; no full queue count or new source request.
        pending = [dict(r) for r in db.execute('''SELECT item_key,url,priority FROM tasks
            WHERE kind='feed' AND state='pending' ORDER BY priority,item_key LIMIT ?''', (needed + reserve,))]
        if len(pending) < needed:
            raise ValueError("Not enough unattempted candidates to freeze the requested frame")
        if {r["item_key"] for r in pending} & {r["profile_url"] for r in aliases}:
            raise ValueError("Already admitted profile has a pending feed; reconcile before assigning")
    finally:
        db.close()

    def record(rank, key, profile, feed, priority, stage, owner, shard):
        return dict(zip(FIELDS, (rank, key, profile, feed, priority, stage, owner, shard)))

    baseline = []
    for rank, (key, profile) in enumerate(sorted(representative.items()), 1):
        handle = profile.removeprefix("https://medium.com/@")
        priority = hashlib.sha256(f"medium-discovery-v1|feed|{profile}".encode()).hexdigest()
        baseline.append(record(rank, key, profile, "https://medium.com/feed/@" + handle,
                               priority, "existing_in_window_rss", "Zherui", "baseline"))
    assignments, reserves = [], []
    for index, row in enumerate(pending):
        is_reserve = index >= needed
        index_in_stage = index - needed if is_reserve else index
        shard = index_in_stage % 2 + 1
        entry = record(counter + index + 1, "unresolved_profile:" + row["item_key"],
                       row["item_key"], row["url"], row["priority"],
                       "reserve_candidate" if is_reserve else "primary_candidate",
                       OWNERS[shard - 1], f"shard-{shard}")
        (reserves if is_reserve else assignments).append(entry)
    primary = baseline + assignments
    assert len(primary) == target
    assert len({r["profile_url"] for r in primary + reserves}) == len(primary) + len(reserves)
    output.mkdir(parents=True)
    write_csv(output / "source_frame.csv", primary)
    write_csv(output / "already_collected.csv", baseline)
    write_csv(output / "known_aliases.csv", aliases, ["creator_key", "profile_url"])
    for shard in (1, 2):
        write_csv(output / f"shard-{shard}.csv", [r for r in assignments if r["shard"] == f"shard-{shard}"])
        write_csv(output / f"shard-{shard}-reserve.csv", [r for r in reserves if r["shard"] == f"shard-{shard}"])
    manifest = dict(version="medium-candidate-worklists-v1", frozen_at=datetime.now(timezone.utc).isoformat(),
                    target_collected_authors=target, primary_frame_rows=len(primary),
                    already_collected_author_candidates=counter, unattempted_primary_profiles=needed,
                    reserve_profiles=len(reserves), source_checkpoint=str(checkpoint),
                    snapshot_last_request_id=last_request, assignment_rule="priority_order_alternating_two_owners",
                    owners={"baseline": "Zherui", "shard-1": "Zherui", "shard-2": "Ziyang"},
                    shard_counts={f"shard-{i}": sum(r["shard"] == f"shard-{i}" for r in assignments) for i in (1, 2)},
                    reserve_counts={f"shard-{i}": sum(r["shard"] == f"shard-{i}" for r in reserves) for i in (1, 2)},
                    start_date="2020-01-01", end_date="2026-09-17",
                    runtime_bound=False, additional_workers_launched=False, execution_ready=False,
                    final_250000_qualifying_identities_known=False,
                    builder_sha256=sha256(Path(__file__)),
                    files={p.name: sha256(p) for p in sorted(output.glob("*.csv"))},
                    limitations=["Primary frame rows are author candidates, not 250000 validated eligible people.",
                                 "Failed, empty, out-of-window and duplicate-ID feeds can require reserves; reserves may also fall short.",
                                 "RSS histories and bodies may be truncated; no full-history or final eligibility claim.",
                                 "Existing collector is still autonomous and is not bound to these planning worklists.",
                                 "Reconcile post-snapshot collection and bind a shard-aware runner before execution.",
                                 "Assigned shards may run concurrently in separate caches/accounts, with one paced transport pool per cache; reconcile unique keys at merge.",
                                 "Preserve failures and prior cache; no automatic retry of failed items."])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=int, default=250000)
    parser.add_argument("--reserve", type=int, default=50000)
    args = parser.parse_args()
    print(json.dumps(freeze(args.checkpoint, args.output, args.target, args.reserve), indent=2))


if __name__ == "__main__":
    main()
