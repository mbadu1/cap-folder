#!/usr/bin/env python3
"""Read lightweight Medium telemetry and verify the recorded local process/binding."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

from parallel_medium_collection import ROOT, sha_file


def check(cache):
    cache = Path(cache)
    heartbeat = json.loads((cache / "report/heartbeat.json").read_text())
    binding = json.loads((cache / "binding.json").read_text())
    here = os.uname().nodename
    same_node = heartbeat.get("node", here) == here
    same_job = not os.environ.get("SLURM_JOB_ID") or heartbeat.get("slurm_job_id") == os.environ["SLURM_JOB_ID"]
    pid = heartbeat["worker_pid"]
    alive = None
    process_matches = None
    if same_node:
        try:
            os.kill(pid, 0)
            alive = True
            cmdline = Path(f"/proc/{pid}/cmdline")
            if cmdline.exists():
                args = cmdline.read_text().split("\0")
                process_cwd = Path(f"/proc/{pid}/cwd").resolve()
                expected_script = ROOT / "medium/scripts/parallel_medium_collection.py"
                process_matches = any((process_cwd / a).resolve() == expected_script.resolve()
                                      for a in args if Path(a).name == expected_script.name)
                configured = next((a.split("=", 1)[1] for a in args if a.startswith("--cache-root=")), None)
                if "--cache-root" in args:
                    configured = args[args.index("--cache-root") + 1]
                if configured:
                    process_matches = process_matches and (process_cwd / configured).resolve() == cache.resolve()
                else:
                    process_matches = False
            else:
                process_matches = None
        except ProcessLookupError:
            alive = False
    with (cache / "collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = False
        except BlockingIOError:
            held = True
    sources = {name: ROOT / "medium/scripts" / name for name in binding["sources"]}
    sources["build_substack_platform_month.py"] = ROOT / "substack/scripts/build_substack_platform_month.py"
    code_matches = all(sha_file(sources[name]) == expected for name, expected in binding["sources"].items())
    gate = json.loads((cache / "request_gate.json").read_text())
    db = sqlite3.connect((cache / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        latest = db.execute("SELECT id,kind,item_key,started_at,status,error FROM requests ORDER BY id DESC LIMIT 1").fetchone()
        sqlite_binding = json.loads(db.execute("SELECT value FROM meta WHERE key='team_binding'").fetchone()[0])
    finally:
        db.close()
    return dict(heartbeat=heartbeat, recorded_pid_alive_on_this_node=alive, process_command_matches=process_matches,
                same_node=same_node, same_job=same_job, collector_lock_held=held, code_matches_binding=code_matches,
                file_matches_sqlite_binding=binding == sqlite_binding,
                latest_request=dict(latest) if latest else None,
                cooldown_remaining_seconds=max(0, gate["cooldown_until"] - time.time()),
                stop=(cache / "STOP").exists(), pause=json.loads((cache / "PAUSED.json").read_text()) if (cache / "PAUSED.json").exists() else None,
                note="Compare successive request counts/timestamps; a telemetry file alone does not prove a live collector.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-root", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(check(args.cache_root), indent=2))
