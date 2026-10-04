#!/usr/bin/env python3
"""Hold pending Medium reserve feeds without deleting assignments or changing bindings.

This explicit operator action serializes a short SQLite transaction with a live
collector, only while primary feeds remain pending. It never fetches or restarts.
"""
import argparse
import fcntl
import json
from pathlib import Path
import sqlite3

from check_run import check
from parallel_medium_collection import DEFAULT_BATCH, atomic_json, now, sha_file, validate_batch


def defer(cache, batch=DEFAULT_BATCH):
    cache, batch = Path(cache), Path(batch)
    if not (cache / "crawl.sqlite3").exists():
        raise ValueError("Prepare the shard cache before deferring reserves")
    with (cache / "collector.lock").open("a") as lock:
        live = False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            live = True
            inspected = check(cache)
            if not all(inspected[k] for k in ("recorded_pid_alive_on_this_node", "process_command_matches",
                                              "code_matches_binding", "file_matches_sqlite_binding")):
                raise ValueError("Held cache lock is not a verified compatible live collector")
        binding_file = json.loads((cache / "binding.json").read_text())
        if binding_file["batch_sha256"] != sha_file(batch / "manifest.json"):
            raise ValueError("Frozen batch differs from cache binding")
        # Hash/read large frozen inputs before taking SQLite's writer lock.
        _, rows = validate_batch(batch, binding_file["shard"])
        expected = {(r["profile_url"], r["stage"]) for r in rows}
        db = sqlite3.connect((cache / "crawl.sqlite3").resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            # Writer serialization prevents primary completion/dispatch from
            # changing between the scope checks and pending-reserve deferral.
            db.execute("BEGIN IMMEDIATE")
            binding = json.loads(db.execute("SELECT value FROM meta WHERE key='team_binding'").fetchone()[0])
            if binding != binding_file or binding_file != json.loads((cache / "binding.json").read_text()):
                raise ValueError("File/SQLite binding mismatch")
            if set(db.execute("SELECT profile_url,stage FROM assignments")) != expected:
                raise ValueError("Cache assignments differ from frozen shard")
            counts = {(stage, state): count for stage, state, count in db.execute("""
                SELECT a.stage,t.state,COUNT(*) FROM assignments a JOIN tasks t
                ON t.kind='feed' AND t.item_key=a.profile_url GROUP BY a.stage,t.state""")}
            if counts.get(("reserve_candidate", "inflight"), 0):
                raise ValueError("Reserve requests already in flight; preserve them and inspect before changing scope")
            if live and not counts.get(("primary_candidate", "pending"), 0):
                raise ValueError("Live deferral requires pending primary work to exclude a reserve-dispatch race")
            changed = db.execute("""UPDATE tasks SET state='deferred' WHERE kind='feed' AND state='pending'
                AND item_key IN (SELECT profile_url FROM assignments WHERE stage='reserve_candidate')""").rowcount
            receipt = dict(version="medium-reserve-deferral-v1", at=now(), shard=binding["shard"],
                           policy="primary_only_until_sampling_review", changed_pending_reserves=changed,
                           deferred_reserves=counts.get(("reserve_candidate", "deferred"), 0) + changed,
                           live_collector_continues=live, binding_sha256=sha_file(cache / "binding.json"),
                           operator_script_sha256=sha_file(Path(__file__)),
                           prior_feed_counts=[dict(stage=s, state=t, count=n) for (s,t),n in sorted(counts.items())],
                           pending_primary_feeds=counts.get(("primary_candidate", "pending"), 0),
                           no_http_requests=True, historical_results_preserved=True,
                           reactivation="Requires a later explicit user decision after sampling review")
            db.execute("CREATE TABLE IF NOT EXISTS reserve_scope_audit(at TEXT NOT NULL,receipt_json TEXT NOT NULL)")
            db.execute("INSERT INTO reserve_scope_audit VALUES(?,?)", (receipt["at"], json.dumps(receipt)))
            db.execute("INSERT OR REPLACE INTO meta VALUES('reserve_execution_policy',?)", (json.dumps(receipt),))
            db.commit()
            atomic_json(cache / "reserve_policy.json", receipt)
            return receipt
        finally:
            db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-root", type=Path, required=True)
    p.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    a = p.parse_args()
    print(json.dumps(defer(a.cache_root, a.batch), indent=2))


if __name__ == "__main__":
    main()
