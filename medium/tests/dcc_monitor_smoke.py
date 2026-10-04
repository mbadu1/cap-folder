#!/usr/bin/env python3
"""Private DCC monitor lifecycle fixture: synthetic errors and zero HTTP starts."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "medium/scripts"))
sys.path.insert(0, str(ROOT / "medium/tests"))
import monitor_dcc as monitor
import parallel_medium_collection as runner
from collect_medium_history import atomic_json, now
from test_medium_parallel import MediumParallelTests


def smoke(output):
    if not os.environ.get("SLURM_JOB_ID") or "login" in os.uname().nodename.lower():
        raise ValueError("Use dcc-agent on a verified compute allocation")
    output = output.resolve()
    output.mkdir(parents=True)  # Immutable fixture directory; do not reuse.
    class DurableFixture:
        name = str(output)
        def cleanup(self): pass  # Retain evidence; no production data involved.
    case = MediumParallelTests("test_production_defaults_and_both_pacing_values_are_pinned")
    with patch("test_medium_parallel.tempfile.TemporaryDirectory", return_value=DurableFixture()):
        case.setUp()
    case.doCleanups()
    cache = output / "live"
    c = runner.TeamCollector(cache, cache / "report", min_free_gb=20, max_cache_gb=0)
    try:
        c.prepare(case.batch, 2, case.ledger)
        for code in (403, 500, 429):
            c.db.execute("INSERT INTO requests(url,kind,item_key,started_at,status,error,retry_after) VALUES(?,?,?,?,?,?,?)",
                         ("https://medium.com/feed/@offline_fixture", "feed", "synthetic-fixture", now(), code,
                          f"HTTP {code} (synthetic monitor fixture)", "60" if code == 429 else None))
        c.db.commit()
    finally:
        c.db.close()
    # Every worker is gated for an hour, while this bounded test stops both
    # processes within seconds. No HTTP request can start in this fixture.
    atomic_json(cache / "request_gate.json", dict(last_start=0, cooldown_until=time.time()+3600, rate_events=[]))
    command = [sys.executable, "-u", str(ROOT / "medium/scripts/parallel_medium_collection.py"), "run",
               "--batch", str(case.batch), "--shard", "2", "--reconciliation", str(case.ledger),
               "--cache-root", str(cache), "--workers", "2", "--continuous", "--min-free-gb", "20",
               "--max-cache-gb", "0", "--require-dcc"]
    registered = None
    with (cache / "fixture_collector.log").open("ab") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    try:
        deadline = time.monotonic()+15
        while not (cache / "report/heartbeat.json").exists():
            if child.poll() is not None: raise RuntimeError("Fixture collector exited; inspect its log")
            if time.monotonic()>deadline: raise RuntimeError("Fixture heartbeat not ready")
            time.sleep(0.1)
        registered = monitor.start(cache,600,1800)
        second = monitor.start(cache,600,1800)
        assert registered["registration"]["pid"] == second["registration"]["pid"]
        assert registered["monitor_process_alive"] and registered["monitor_command_matches"] and registered["monitor_lock_held"]
        assert registered["monitor_state"]["health"] == "cooldown_auto_resume"
        events = [json.loads(s) for s in (cache / "monitor_events.jsonl").read_text().splitlines()]
        errors = [e for e in events if e["kind"] in ("request_error","rate_limit")]
        assert [e["request"]["status"] for e in errors] == [403,500,429]
        subprocess.run([sys.executable, str(ROOT / "medium/scripts/monitor_dcc.py"), "stop", "--cache-root", str(cache)],
                       check=True, stdout=subprocess.DEVNULL)
        deadline=time.monotonic()+10
        while monitor.monitor_status(cache)["monitor_lock_held"]:
            if time.monotonic()>deadline: raise RuntimeError("Fixture watcher did not release its lock")
            time.sleep(0.1)
        os.waitpid(registered["registration"]["pid"], 0)
        assert child.poll() is None  # Stopping monitor leaves collector alive.
    finally:
        if registered and not (cache / "MONITOR_STOP").exists(): (cache / "MONITOR_STOP").write_text(now()+" fixture cleanup\n")
        (cache / "STOP").write_text(now()+" deliberate offline fixture end\n")
        child.wait(timeout=20)
    db=sqlite3.connect((cache/"crawl.sqlite3").as_uri()+"?mode=ro",uri=True)
    try:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 3
    finally:
        db.close()
    starts=cache/"request_starts.jsonl"
    assert not starts.exists() or not starts.read_text().strip()
    status=monitor.monitor_status(cache)
    result=dict(at=now(),status="PASS",synthetic_fixture=True,network_request_starts=0,
                synthetic_http_errors_logged=[403,500,429],cooldown_health="cooldown_auto_resume",
                monitor_singleton_verified=True,monitor_stop_kept_collector_alive=True,collector_exited=True,
                monitor_lock_free=not status["monitor_lock_held"],node=os.uname().nodename,job=os.environ["SLURM_JOB_ID"],
                registration=registered["registration"],
                source_hashes={name:runner.sha_file(ROOT/name) for name in (
                    "medium/scripts/parallel_medium_collection.py","medium/scripts/check_run.py",
                    "medium/scripts/monitor_dcc.py","medium/tests/dcc_monitor_smoke.py")})
    atomic_json(output/"monitor_smoke_receipt.json",result)
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(smoke(args.output),indent=2))
