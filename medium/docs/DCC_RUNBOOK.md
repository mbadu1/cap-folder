# Medium DCC runbook — 2026-10-03

> **2026-10-03 correction:** The shard-2 live release is withdrawn by the user's instruction to keep Zherui's scraping running. Read the latest `medium/handoff/CURRENT_WINDOW.json` and the preserved `withdrawal.json`; do not launch Ziyang's live tests or collector from the historical release. Offline preparation remains available.

Run commands from the repository root. Use your own checkout, DCC account, designated work directory and configured `dcc-agent` alias. Never run project commands directly on a DCC login node. If Codex is already attached through `dcc-agent` to a compute allocation, use that remote shell without nested SSH.

## Prepare the machine and code

From your local shell, create the multiplexing socket directory if absent:

```sh
mkdir -p ~/.ssh/sockets
chmod 700 ~/.ssh/sockets
ssh dcc-agent 'hostname; printenv SLURM_JOB_ID'
```

Verify a compute node and active allocation. The CPU launcher should use modest resources such as `--cpus 4 --mem 16G --gres none`; retain your confirmed partition/account/QoS. Twenty network threads do not need 20 CPUs or a GPU. Verify the effective allocation's CPUs, memory and remaining walltime with the established DCC tools on the compute node. Resource edits only affect new jobs; never cancel an existing allocation to resize it.

On the compute node, clone or update the team repository and pin the handoff revision:

```sh
git clone --branch zherui-substack-month-pilot git@github.com:mbadu1/cap-folder.git
cd cap-folder
git rev-parse HEAD
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r medium/requirements.txt
.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'
```

If the checkout exists, inspect `git status` before pulling; preserve local changes and checkpoints. Use Python 3.10+. Save the exact HEAD for every run and keep that revision throughout resume. Do not run the standalone old collector for either assigned shard.

## Refresh the baseline ledger — Zherui

Before a live handoff, Zherui gracefully stops the prior local Medium worker, waits for its report to finish, verifies its exact PID has exited and the lock is free, and ensures its existing monitor respects the deliberate stop. Keep the baseline and STOP evidence. The preparation ledger checked into this repository was captured while the old worker was running; it is suitable for offline setup only until refreshed.

From Zherui's checkout, generate a new immutable metadata ledger after the stop:

```sh
.venv/bin/python medium/scripts/parallel_medium_collection.py reconcile \
  --checkpoint .cache/medium_history/history_v1/crawl.sqlite3 \
  --output .cache/medium_handoff/final-before-ziyang.json.gz
```

Use the enclosing workspace's Python environment if that is where dependencies are installed. Publish the metadata-only ledger, its `.receipt.json` and a current exclusive-window release for the next owner in `medium/handoff/`, or use the agreed private channel. A self-service clone requires the published option; article bodies, raw responses and databases remain private. If another shard has started since the local stop, that old receipt is insufficient: wait for the current owner to stop and refresh from the merged state. Verify the receipt's SHA-256 on Ziyang's machine. Do not replace the checked-in preparation snapshot, assignment CSVs or a previously used ledger. Coordinate the exclusive team window explicitly; a timestamp alone does not prove the other process stopped.

## No-network preflight — Ziyang

The current self-service release is `medium/handoff/2026-10-03-ziyang-shard-2/release.json`. Verify its status/owner/batch/source pins and hashes for `final-ledger.json.gz`, its receipt and `stop-verification.json`. This release follows the stopped shard-1 export, a new-copy baseline merge and reconciliation. Zherui remains intentionally stopped and the external automation monitors only the separate baseline transfer. The window stays reserved for Ziyang until an explicit later handoff; it does not expire when a compute allocation ends.

Use the verified shipped ledger in your own checkout and choose a fresh durable cache:

```sh
task_ledger=medium/handoff/2026-10-03-ziyang-shard-2/final-ledger.json.gz
task_cache=.cache/medium_shards/2026-10-03-team-v1/shard-2-v3
.venv/bin/python medium/scripts/parallel_medium_collection.py prepare \
  --shard 2 --reconciliation "$task_ledger" --cache-root "$task_cache"
```

The command verifies all assignment hashes, ownership, dates, URLs and source bindings, imports completed/failed metadata exclusions and writes the pinned cache binding. It makes no HTTP requests. A new source/ledger/configuration cannot overwrite an existing cache binding. The v3 runner keeps the 1.5-second global/six-second worker timers and pins the user's updated HTTP policy: ordinary errors are skipped, 429 attempts stay pending and automatically retry after the shared cooldown. Keep v1/v2 caches with their original revision; use a fresh v3 path. Do not requeue old failures or reopen prior blocked routes. See [MONITORING.md](MONITORING.md).

## Pilot, inspect and continue

First complete [DCC_VALIDATION_PLAN.md](DCC_VALIDATION_PLAN.md): the frozen 40-feed same-response baseline replay, five requests with one worker, 20 with ten workers, and 40 with twenty workers in separate validation caches. Review their timing/access/integrity assessments, compare against the shipped immutable `medium/validation/2026-10-03-baseline-40-v3/reference-metadata.json.gz` and replay the exact DCC responses locally. Preserve source changes for review. A failed stage blocks escalation. These feed-only validation caches are separate from the assigned-shard cache below and must never enter production exports/merges. Repeat this verification on Ziyang's own account/node before his production pilot.

After confirming Zherui's previous Medium process and every other Medium shard are stopped, run a 40-request pilot inside the active allocation:

```sh
bash medium/scripts/run_dcc.sh pilot --shard 2 \
  --reconciliation "$task_ledger" --cache-root "$task_cache" --exclusive-team-window
.venv/bin/python medium/scripts/check_run.py --cache-root "$task_cache"
```

The assigned-shard pilot takes at least roughly one minute at the global gate, with additional latency possible. Inspect `report/status.json`, `report/heartbeat.json`, `request_starts.jsonl`, `request_gate.json` and any pause. Verify starts are at least 1.5 seconds apart globally and at least 6 seconds apart for each recorded worker_id and inspect successful RSS identities/dates plus free-only mirror retention. Check job/node/PID/lock and fresh request progress. Access challenges, repeated 429s, parser changes or resource errors require diagnosis, not production continuation. See [VALIDATION.md](VALIDATION.md) for dated evidence; a prior test never substitutes for your own reviewed pilot.

After a healthy reviewed pilot, continue the same revision, ledger and cache:

```sh
bash medium/scripts/run_dcc.sh production --shard 2 \
  --reconciliation "$task_ledger" --cache-root "$task_cache" --exclusive-team-window
```

For a detached run, use your established DCC process-launch procedure or `nohup` on the compute node, direct stdout/stderr to the private cache and record the resulting PID. Do not leave it unattended until your own persistent monitor is registered and verified. Observe the pilot about once per minute; for production check about every ten minutes. The checker verifies the local recorded PID, command, lock, code hashes, cooldown and markers; compare request IDs/start times across checks. If it is on another node, process liveness is unknown and must be checked on the registered node. Telemetry files alone do not establish that monitoring or scraping is alive. Pulling this repository installs no scheduler and transfers no existing Codex automation.

After verifying the current live production process, start the actual watcher and verify its registration:

```sh
.venv/bin/python medium/scripts/monitor_dcc.py start --cache-root "$task_cache"
.venv/bin/python medium/scripts/monitor_dcc.py status --cache-root "$task_cache"
```

It checks immediately and every ten minutes, records every newly observed error, and labels 429 cooldown as expected automatic recovery. It never fetches/restarts for the scraper. Register your own external notification/expiry scheduler as described in [MONITORING.md](MONITORING.md); the compute watcher ends with its allocation and disk logs do not send notifications.

To stop gracefully, `touch "$task_cache/STOP"`; active requests settle before exit. Preserve markers and inspect the final report. Walltime/preemption interrupts the process, so return through `dcc-agent`, verify no previous worker/lock is active, inspect interrupted tasks and resume the exact bound checkpoint deliberately. Never run `scancel` or clear a pause automatically. The next owner must wait until this run has exited and its latest data are merged/reconciled.

## Export, verify and return

Once stopped, export to a new private directory:

```sh
.venv/bin/python medium/scripts/parallel_medium_collection.py export \
  --cache-root "$task_cache" --output .cache/medium_exports/shard-2-v1
tar -czf .cache/medium_exports/shard-2-v1.tar.gz -C .cache/medium_exports shard-2-v1
sha256sum .cache/medium_exports/shard-2-v1.tar.gz
```

Return the archive, checksum, pinned commit, binding, request/status summary, pending/errors, pause evidence, candidate shortfall and exact compute job/node/PID through the private team channel. Corpus bodies, raw responses and databases stay out of Git. An export with pending work is partial; zero pending assignments does not establish complete history.

Zherui merges into a new baseline copy, preserving the stopped original:

```sh
.venv/bin/python medium/scripts/merge_team_exports.py \
  --baseline-cache .cache/medium_history/history_v1 \
  --export .cache/medium_exports/shard-2-v1 \
  --output .cache/medium_history/team-merged-v1
.venv/bin/python medium/scripts/parallel_medium_collection.py reconcile \
  --checkpoint .cache/medium_history/team-merged-v1/crawl.sqlite3 \
  --output .cache/medium_handoff/after-ziyang-v1.json.gz
```

The merge requires space for a full baseline copy plus raw blobs and export data, and no exclusive baseline collector writer. A shared read lock permits a concurrent verified baseline backup/transfer while excluding collector writes. A failed merge retains `MERGE_INCOMPLETE.json` and cannot be reconciled. Existing pause/stop evidence is preserved for review. Initialize a **new** shard-1 cache using the updated ledger and the same frozen worklists; never rewrite shard-2's binding or resume the autonomous legacy collector to process the divided queues. Final candidate/coverage reconciliation is separate from monthly sampling.
