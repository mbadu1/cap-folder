#!/usr/bin/env python3
"""Detached, read-only Medium DCC monitor. Never restarts or fetches for the collector."""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from check_run import check
from collect_medium_history import atomic_json, now
from parallel_medium_collection import ROOT, sha_file

VERSION = "medium-dcc-monitor-v1"
FINISHED = {"author_target_reached_with_history_gaps", "assigned_candidates_exhausted_with_gaps", "pilot_complete"}


def classify(snapshot, previous=None, epoch=None, stale_seconds=1800):
    epoch = time.time() if epoch is None else epoch
    if snapshot.get("inspection_error"): return "inspection_error"
    if not snapshot["code_matches_binding"] or not snapshot["file_matches_sqlite_binding"]: return "binding_mismatch"
    if snapshot["pause"]: return "paused_for_review"
    if snapshot["stop"]: return "operator_stopped"
    if not snapshot["same_node"]: return "inspect_registered_node"
    if not snapshot.get("same_job", True): return "inspect_registered_job"
    heartbeat = snapshot["heartbeat"]
    if not snapshot["recorded_pid_alive_on_this_node"]:
        return "finished" if heartbeat["state"] in FINISHED and not snapshot["collector_lock_held"] else "worker_exited"
    if snapshot["process_command_matches"] is not True or not snapshot["collector_lock_held"]: return "process_or_lock_mismatch"
    if snapshot["cooldown_remaining_seconds"] > 0: return "cooldown_auto_resume"
    if heartbeat["state"] in FINISHED or heartbeat["state"] == "reporting": return "finishing_report"
    age = epoch - datetime.fromisoformat(heartbeat["updated_at"]).timestamp()
    if age > stale_seconds: return "stale_heartbeat"
    latest = (snapshot["latest_request"] or {}).get("id", 0)
    if previous and latest == previous.get("last_request_id") and epoch - previous.get("last_progress_epoch", epoch) > stale_seconds:
        return "request_progress_stalled"
    return "running"


def error_rows(cache, after, through):
    """Stream every newly committed error, including 429 attempts; no corpus scans."""
    db = sqlite3.connect((Path(cache) / "crawl.sqlite3").resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        for row in db.execute("""SELECT id,url,kind,item_key,started_at,status,error,retry_after FROM requests
            WHERE id>? AND id<=? AND (status!=200 OR error IS NOT NULL) ORDER BY id""", (after, through)):
            yield dict(row)
    finally:
        db.close()


def event(cache, value):
    with (cache / "monitor_events.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(at=now(), **value)) + "\n")


def observe(cache, previous=None, *, stale_seconds=1800, inspector=check):
    cache = Path(cache)
    epoch = time.time()
    previous = previous or {}
    try:
        snapshot = inspector(cache)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        snapshot = dict(inspection_error=type(exc).__name__ + ": " + str(exc)[:240])
    health = classify(snapshot, previous, epoch, stale_seconds)
    latest = (snapshot.get("latest_request") or {}).get("id", previous.get("last_request_id", 0))
    cursor = previous.get("error_cursor", 0)
    # The checkpoint remains the complete source of HTTP/transport/parse errors.
    # Event delivery is at least once if the monitor crashes before saving cursor.
    if not snapshot.get("inspection_error"):
        for row in error_rows(cache, cursor, latest):
            event(cache, dict(kind="rate_limit" if row["status"] == 429 else "request_error", request=row))
        cursor = latest
    progress_epoch = previous.get("last_progress_epoch", epoch)
    if latest != previous.get("last_request_id"): progress_epoch = epoch
    signature = [health, snapshot.get("pause"), snapshot.get("inspection_error"), snapshot.get("heartbeat", {}).get("worker_pid")]
    if signature != previous.get("health_signature"):
        alert = dict(kind="health_change", health=health, last_request_id=latest,
                     pause=snapshot.get("pause"), inspection_error=snapshot.get("inspection_error"))
        event(cache, alert)
        print(json.dumps(alert), flush=True)
    state = dict(version=VERSION, updated_at=now(), monitor_pid=os.getpid(), monitor_running=True,
                 node=os.uname().nodename, job=os.environ.get("SLURM_JOB_ID"), health=health, snapshot=snapshot,
                 last_request_id=latest, last_progress_epoch=progress_epoch, error_cursor=cursor,
                 health_signature=signature)
    atomic_json(cache / "monitor_state.json", state)
    return state


def monitor_status(cache):
    cache = Path(cache)
    registration = json.loads((cache / "monitor_registration.json").read_text())
    same_node = registration["node"] == os.uname().nodename
    alive = None
    command_matches = None
    if same_node:
        try:
            os.kill(registration["pid"], 0)
            alive = True
            args = Path(f"/proc/{registration['pid']}/cmdline")
            if args.exists():
                argv = args.read_text().split("\0")
                cwd = Path(f"/proc/{registration['pid']}/cwd").resolve()
                expected_script = Path(__file__).resolve()
                command_matches = any((cwd / a).resolve() == expected_script for a in argv
                                      if Path(a).name == expected_script.name) and str(cache.resolve()) in argv
        except ProcessLookupError:
            alive = False
    with (cache / "monitor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = False
        except BlockingIOError:
            held = True
    hashes_match = all(sha_file(ROOT / name) == value for name, value in registration["sources"].items())
    state_path = cache / "monitor_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    return dict(registration=registration, same_node=same_node, monitor_process_alive=alive,
                monitor_command_matches=command_matches, monitor_lock_held=held, monitor_sources_match=hashes_match,
                binding_matches_registration=sha_file(cache / "binding.json") == registration["binding_sha256"],
                monitor_state=state, monitor_stop=(cache / "MONITOR_STOP").exists(),
                note="This watcher writes private logs/state; register the operator's own external notifier for UI alerts and allocation expiry.")


def run(cache, interval, stale_seconds):
    if not os.environ.get("SLURM_JOB_ID") or "login" in os.uname().nodename.lower():
        raise ValueError("Run through dcc-agent on a verified compute allocation")
    if (cache / "MONITOR_STOP").exists(): raise ValueError("Preserve and deliberately archive the previous monitor stop first")
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    with (cache / "monitor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = [Path(__file__), Path(__file__).with_name("check_run.py")]
        registration = dict(version=VERSION, at=now(), pid=os.getpid(), node=os.uname().nodename,
                            job=os.environ["SLURM_JOB_ID"], cache_root=str(cache.resolve()), interval_seconds=interval,
                            stale_seconds=stale_seconds, binding_sha256=sha_file(cache / "binding.json"),
                            sources={str(p.relative_to(ROOT)): sha_file(p) for p in paths})
        atomic_json(cache / "monitor_registration.json", registration)
        event(cache, dict(kind="monitor_registered", registration=registration))
        (cache / "monitor.pid").write_text(str(os.getpid()) + "\n")
        state = json.loads((cache / "monitor_state.json").read_text()) if (cache / "monitor_state.json").exists() else None
        try:
            def pinned_inspector(directory):
                snapshot = check(directory)
                if sha_file(directory / "binding.json") != registration["binding_sha256"]:
                    snapshot["file_matches_sqlite_binding"] = False
                return snapshot
            while not stopped.is_set() and not (cache / "MONITOR_STOP").exists():
                state = observe(cache, state, stale_seconds=stale_seconds, inspector=pinned_inspector)
                deadline = time.monotonic() + interval
                while not stopped.is_set() and not (cache / "MONITOR_STOP").exists():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: break
                    stopped.wait(min(1, remaining))
        finally:
            if state:
                state.update(monitor_running=False, monitor_ended_at=now())
                atomic_json(cache / "monitor_state.json", state)


def start(cache, interval, stale_seconds):
    if not os.environ.get("SLURM_JOB_ID") or "login" in os.uname().nodename.lower():
        raise ValueError("Start through dcc-agent on a verified compute allocation")
    snapshot = check(cache)
    if classify(snapshot) not in ("running", "cooldown_auto_resume", "finishing_report"):
        raise ValueError("Verify a live collector on this node before starting its monitor")
    if (cache / "monitor_registration.json").exists():
        existing = monitor_status(cache)
        if existing["monitor_lock_held"]:
            if not existing["same_node"] or not existing["monitor_process_alive"] or existing["monitor_command_matches"] is not True:
                raise ValueError("A registered monitor lock is held; inspect its node/process")
            if not existing["monitor_sources_match"]: raise ValueError("Running monitor source changed; preserve registration and inspect")
            if not existing["binding_matches_registration"]: raise ValueError("Collector binding changed since monitor registration")
            return existing
    with (cache / "monitor.log").open("ab") as log:
        child = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "run", "--cache-root", str(cache.resolve()),
                                  "--interval-seconds", str(interval), "--stale-seconds", str(stale_seconds)],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None: raise RuntimeError("Monitor exited during startup; inspect monitor.log")
        if (cache / "monitor_registration.json").exists() and (cache / "monitor_state.json").exists():
            result = monitor_status(cache)
            state = result["monitor_state"]
            if (result["registration"]["pid"] == child.pid and state["monitor_pid"] == child.pid
                    and result["monitor_process_alive"] and result["monitor_command_matches"] and result["monitor_lock_held"]):
                return result
        time.sleep(0.1)
    raise RuntimeError("Monitor readiness not verified; inspect registration/log before another launch")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("start", "run", "once", "status", "stop"))
    p.add_argument("--cache-root", type=Path, required=True)
    p.add_argument("--interval-seconds", type=float, default=600)
    p.add_argument("--stale-seconds", type=float, default=1800)
    args = p.parse_args()
    if not args.cache_root.is_dir(): p.error("Use an existing production cache")
    if not math.isfinite(args.interval_seconds) or args.interval_seconds < 1: p.error("Interval must be finite and at least one second")
    if not math.isfinite(args.stale_seconds) or args.stale_seconds < args.interval_seconds: p.error("Stale threshold must be at least one monitor interval")
    if args.command == "run": run(args.cache_root.resolve(), args.interval_seconds, args.stale_seconds);return
    if args.command == "start": result = start(args.cache_root.resolve(), args.interval_seconds, args.stale_seconds)
    elif args.command == "once":
        with (args.cache_root / "monitor.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            old = json.loads((args.cache_root / "monitor_state.json").read_text()) if (args.cache_root / "monitor_state.json").exists() else None
            result = observe(args.cache_root, old, stale_seconds=args.stale_seconds)
            result["monitor_running"] = False
            atomic_json(args.cache_root / "monitor_state.json", result)
    elif args.command == "stop":
        stop = args.cache_root / "MONITOR_STOP"
        if not stop.exists(): stop.write_text(now() + " deliberate monitor stop; collector unchanged\n")
        result = dict(requested=True, marker=str(stop), collector_unchanged=True)
    else: result = monitor_status(args.cache_root)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
