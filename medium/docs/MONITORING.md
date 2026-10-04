# Medium monitoring and HTTP recovery (v3)

The user requested Substack-style automatic rate-limit recovery on 2026-10-03. This applies to `medium/scripts/parallel_medium_collection.py` v3. It does not change the separate active legacy local collector. Keep previous code/cache bindings and use a fresh v3 production cache with the shipped hash-checked metadata ledger; both assigned shards may run concurrently, with their own gates/cooldowns and monitors. Do not rewrite bindings or retry historical failed items.

| Outcome | Recorded evidence | Collector action |
| --- | --- | --- |
| HTTP 200, parsed successfully | Request, source hashes, committed records | Continue |
| Ordinary non-429 HTTP error, including 401/403/404/410/5xx | HTTP status, URL/item, time and error in SQLite | Mark that item failed, skip it and continue other work |
| HTTP 429 | Every attempt, Retry-After and persisted shared cooldown | Keep the item pending, wait at least 60 seconds across all workers, then retry automatically |
| Repeated HTTP 429 | Every attempt and renewed deadline | Continue the same cooldown/retry cycle; no three-event permanent pause |
| Explicit `cf-mitigated: challenge` or recognized challenge HTML | Request/error, route block and `PAUSED.json` | Stop new starts for review |
| Persistent parser/schema or resource failure | Error and pause evidence | Stop for review |

Honor Retry-After longer than 60 seconds; missing/invalid/shorter headers use the 60-second floor. Requests already in flight may settle after a cooldown begins; every subsequent start uses the same deadline. Keep the 1.5-second global and six-second worker intervals. A bounded pilot counts **attempts**, including 429s; it can end with retry work pending and require review. Continuous collection handles those retries automatically. This does not retry ordinary failed tasks or reopen an old blocked route.

## Start the real watcher on Ziyang's DCC

These commands execute from the pinned repository on a verified compute allocation reached through `ssh dcc-agent`. Use a durable private cache and the operator's own `.venv`. After the assigned pilot passes, launch authorized production using the established detached process procedure. Verify its current node/job/PID, successful request progress, code/cache binding and held lock with `check_run.py`. Then:

```sh
.venv/bin/python medium/scripts/monitor_dcc.py start \
  --cache-root "$task_cache" --interval-seconds 600 --stale-seconds 1800
.venv/bin/python medium/scripts/monitor_dcc.py status --cache-root "$task_cache"
```

`start` launches a detached watcher, waits for its first observation and verifies its PID/command/lock. It refuses a missing allocation, wrong node/job, unhealthy collector or changed binding. Calling `start` again reuses a verified existing watcher. Inspect `monitor_process_alive`, `monitor_command_matches`, `monitor_lock_held`, `monitor_sources_match`, `binding_matches_registration` and `monitor_state.health`. A state file or `monitor.pid` alone is insufficient. Preserve registration/source history in `monitor_events.jsonl`. Use `once` for one read-only inspection when no watcher holds the lock.

The watcher performs an immediate first check, then checks every ten minutes. It verifies the actual collector process/cache argument, lock, code hashes, file/SQLite binding, node/job, recent request watermark, shared cooldown and stop evidence. It reads request rows by indexed ID and writes every new HTTP/transport/parser error to `monitor_events.jsonl`, with 429 labeled `rate_limit`. The SQLite request ledger remains complete; a monitor crash can duplicate an event before its cursor is saved, so events are at least once. No full corpus recount or collection request is made by the monitor.

Private files in the production cache:

| File | Purpose |
| --- | --- |
| monitor_registration.json / monitor.pid / monitor.lock | Actual watcher identity, source/cache binding, allocation and singleton lock |
| monitor_state.json | Latest observation, request/error cursors and health |
| monitor_events.jsonl | All newly observed errors, registration and meaningful health changes |
| monitor.log | Detached watcher stdout/stderr |

`cooldown_auto_resume` is expected: the scraper resumes itself when the deadline expires. Thirty minutes without request progress, outside cooldown/reporting, is reported as a stall; it is never restarted automatically. Ordinary item errors remain in the ledger while collection continues. A challenge, operator STOP, code/binding mismatch or unexplained exit is reported for review. The watcher never clears markers, changes source bindings, launches another collector or cancels a Slurm allocation.

Stop only the watcher with:

```sh
.venv/bin/python medium/scripts/monitor_dcc.py stop --cache-root "$task_cache"
```

Wait for its PID to exit and lock to release. `MONITOR_STOP` affects only the watcher; the collector keeps running. Preserve it, then deliberately archive it before a later monitor start. Stop the collector separately with its `STOP` marker when requested.

## Notifications and allocation expiry

This watcher logs to disk and ends when its Slurm allocation ends. It does not send messages or establish a Codex schedule. Before leaving production unattended, Ziyang's Codex must register and verify its own persistent ten-minute notification monitor outside the compute job using the available scheduling tool. Have it inspect the current allocation and registered watcher/collector, stay quiet during healthy progress or automatic cooldown, and notify only on completion, a new blocker, monitor death, allocation expiry or required input. Verify the scheduler registration and an actual check; do not claim it active from telemetry alone. Existing monitors on Zherui's machine do not transfer to Ziyang.

For walltime/preemption, preserve checkpoints/registration and verify the old process/lock is gone before a deliberate exact-binding resume through `dcc-agent`. Automatic 429 recovery happens inside the existing process; it does not authorize an unattended restart after job loss or an unexplained exit.

## Reproduce the bounded offline DCC fixture

From a compute node in the pinned checkout:

```sh
.venv/bin/python medium/tests/dcc_monitor_smoke.py \
  --output .cache/medium_monitor_validation/2026-10-03-v3
```

Choose a new output directory each time. This fixture creates synthetic 403/500/429 ledger rows and gates transport for an hour while it verifies watcher startup, first observation, duplicate-start reuse, error logging, cooldown classification and shutdown. It then deliberately stops its fixture collector. The receipt requires zero HTTP starts and an unchanged synthetic request count. It preserves all fixture evidence separately from production. Normal tests use simulated time to verify the actual 60-second retry interval and successful item completion without waiting a minute or provoking a platform rate limit.
