#!/usr/bin/env python3
"""Read lightweight Medium telemetry and verify the recorded local process/binding."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time

from parallel_medium_collection import ROOT, sha_file


def check(cache):
    cache = Path(cache)
    heartbeat = json.loads((cache / "report/heartbeat.json").read_text())
    binding = json.loads((cache / "binding.json").read_text())
    here = os.uname().nodename
    same_node = heartbeat.get("node", here) == here
    pid = heartbeat["worker_pid"]
    alive = None
    process_matches = None
    if same_node:
        try:
            os.kill(pid, 0)
            alive = True
            cmdline = Path(f"/proc/{pid}/cmdline")
            process_matches = "parallel_medium_collection.py" in cmdline.read_text() if cmdline.exists() else None
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
    return dict(heartbeat=heartbeat, recorded_pid_alive_on_this_node=alive, process_command_matches=process_matches,
                same_node=same_node, collector_lock_held=held, code_matches_binding=code_matches,
                cooldown_remaining_seconds=max(0, gate["cooldown_until"] - time.time()),
                stop=(cache / "STOP").exists(), pause=json.loads((cache / "PAUSED.json").read_text()) if (cache / "PAUSED.json").exists() else None,
                note="Compare successive request counts/timestamps; a telemetry file alone does not prove a live collector.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache-root", type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(check(args.cache_root), indent=2))
