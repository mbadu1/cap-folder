# Full prompt for Ziyang

> **2026-10-03 correction:** The shard-2 live release is withdrawn by the user's instruction to keep Zherui's scraping running. Read the latest `medium/handoff/CURRENT_WINDOW.json` and the preserved `withdrawal.json`; do not launch Ziyang's live tests or collector from the historical release. Offline preparation remains available.

Paste the complete block into Ziyang's coding agent. Setup and baseline comparison need no files from Zherui. Existing personal DCC/GitHub access is required. Live work also requires a current stopped-owner ledger and exclusive-window release; the earlier preparation snapshot cannot authorize it. The historical shard-2 release/ledger are preserved in `medium/handoff/2026-10-03-ziyang-shard-2/`, but its live authorization is withdrawn. Read the latest `CURRENT_WINDOW.json` and `withdrawal.json`; prepare offline while Zherui's run continues. This prompt does not promise simultaneous collection across two machines.

```text
I am Ziyang Qin. Set up, test, start and monitor my assigned Medium scraping on my own Duke DCC account. Carry out the work, including creating any necessary setup scripts, logs, run receipts and documentation. Do not stop at giving me commands or a plan, and do not require me to prepare files or obtain a baseline database from Zherui. I authorize the bounded tests, assigned pilot, continuous collection after passing checks, and my own persistent monitoring schedule. Ask only for authentication or information that genuinely cannot be discovered. Preserve unrelated files and existing work.

Repository: git@github.com:mbadu1/cap-folder.git (HTTPS alternative: https://github.com/mbadu1/cap-folder.git).
Branch: zherui-substack-month-pilot.
Platform: Medium only.
Assignment batch: 2026-10-03-team-v1.
My assignment: shard 2, with 96,160 primary profiles and 25,000 ordered reserves. Zherui owns shard 1.
Validation sample: medium/validation/2026-10-03-baseline-40-v3/feeds.json.
Expected reference: medium/validation/2026-10-03-baseline-40-v3/reference-metadata.json.gz.
Expected reference SHA-256: 3b9d1e6b09b0d2ebc837c8309a294f1cc55c30ba70529f619ea8c11fd09bfe3a.
Current-window pointer: medium/handoff/CURRENT_WINDOW.json.
Exclusive-window release: medium/handoff/2026-10-03-ziyang-shard-2/release.json.
Final ledger: medium/handoff/2026-10-03-ziyang-shard-2/final-ledger.json.gz.
Ledger receipt: medium/handoff/2026-10-03-ziyang-shard-2/final-ledger.json.gz.receipt.json.
Stop verification: medium/handoff/2026-10-03-ziyang-shard-2/stop-verification.json.

1. Set up my own environment automatically.

Discover my current machine, existing authenticated GitHub access, SSH configuration, DCC agent alias and writable durable storage. If already inside a compute allocation reached through dcc-agent, use that shell directly. Otherwise use ssh dcc-agent for every DCC command. Never run computation on a login node or directly on dcc-login.oit.duke.edu; do not substitute manual sbatch/salloc/srun. Create ~/.ssh/sockets with mode 700 if needed. Use existing confirmed cluster settings; do not invent a NetID, account, partition, QoS or credentials. Aim for CPU-only 4 CPUs/16 GiB, with no GPU; verify effective node, Slurm job, resources and remaining walltime. Never scancel an existing job. If authentication or the agent alias is genuinely unavailable, explain that specific blocker and complete independent local setup.

Choose and record a private durable directory in my own account from actual writable storage. Clone the branch there using working authenticated Git transport; inspect and preserve existing checkout changes instead of overwriting them. Record and pin the exact Git commit. Read AGENTS.md, medium/README.md, medium/docs/TEAM_SCRAPING_PLAN.md, medium/docs/DCC_RUNBOOK.md, medium/docs/DCC_VALIDATION_PLAN.md, medium/docs/MONITORING.md and medium/handoff/README.md. Missing enclosing-workspace research files do not block a standalone clone. Create .venv using available Python 3.10+, install medium/requirements.txt, and run:

.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'

Verify the frozen assignment hashes, sample/manifest/source hashes and shipped reference checksum. The reference contains public metadata and normalized text hashes, with no article bodies. It matches the unchanged manifest's historical private_reference_sha256 field. Do not ask Zherui for another reference. Preserve older v1/v2 caches and use fresh v3 paths. Write a private setup receipt containing the chosen paths, commit, checksums, test outcomes and actual allocation.

2. Acquire the live handoff automatically, without assuming another owner is stopped.

Inspect the latest published medium/handoff/ files and documentation for a final metadata ledger, matching SHA-256 receipt and an explicit current release of the Medium window to shard 2. Verify the release covers the most recent owner, its process exit/free lock, monitor behavior and reconciled results. The file 2026-10-03-preparation.json.gz is for offline preparation only. A historical local stop does not release a later DCC run. The historical release records the earlier stop/merge, but the subsequent user correction withdraws it and restores shard 1. It is now historical/offline metadata; the next live owner needs a new final ledger after the actual current owner stops. Read the latest `CURRENT_WINDOW.json` and `release.json`, verify status RELEASED, owner Ziyang/shard 2, the pointer's release hash, batch/source hashes, ledger/receipt/stop-certificate hashes, and check that no newer handoff revokes or supersedes it. The old release is withdrawn by `withdrawal.json` and `CURRENT_WINDOW.json`. Do not treat its historical RELEASED status as permission to start. Wait for a new explicit stopped/reconciled handoff; do not overlap Zherui's restored run.

Only one Medium collector, validation stage or shard may make requests across the team at a time: the request gate coordinates threads inside one process, not two machines. Do not use --exclusive-team-window unless the actual release supports it. Do not access another account's private paths, stop its collector, or send messages to team members.

If a valid published release or final ledger is missing, finish offline setup and record WAITING_FOR_TEAM_HANDOFF. Use an available persistent scheduler to check the branch periodically without changing the pinned execution checkout or sending Medium requests. Notify me only when the handoff becomes ready or requires attention. Report clearly that live scraping has not started. Do not treat elapsed time or silence as release, invent a receipt, or require me to manually copy private files. Once a valid release is published, verify it, record its revision/checksum and proceed using the compatible pinned source revision. If no scheduler is available, report that waiting cannot continue automatically.

When released, choose fresh private validation and shard-cache paths, save the verified ledger path as task_ledger and the production path as task_cache. Prepare without network requests:

.venv/bin/python medium/scripts/parallel_medium_collection.py prepare --shard 2 --reconciliation "$task_ledger" --cache-root "$task_cache"

Never rebind an existing cache to changed code, configuration or reconciliation metadata.

3. Repeat the Substack-style staged validation on my own DCC.

Run these stages sequentially in separate fresh validation caches using validate_dcc.py run, --handoff-ledger, --sample and --exclusive-team-window:
- smoke: 1 worker, 5 feed requests.
- ten: 10 workers, 20 feed requests, with smoke/assessment.json as --previous-assessment.
- twenty: 20 workers, 40 feed requests, with ten/assessment.json as --previous-assessment.

Use the shipped v3 sample. Follow the exact CLI in DCC_VALIDATION_PLAN.md. Inspect each assessment before escalating; require PASS. Wait at least six seconds between processes. Each stage fetches only selected feeds, with no mirrors or sitemaps. Keep validation caches separate from production and never merge/export them as a production shard.

After every stage exits, run validate_dcc.py compare with --cache-root, --sample and --reference pointing to the shipped reference, then validate_dcc.py replay against the same cache/sample. Require exact same-response replay. Review any live RSS changes against immutable expectations. Document explainable feed rollover/source changes and unchanged shared records; never overwrite expectations to force a pass. Parser, identity, date, retention or unexplained text differences block escalation until resolved. A failed or incomplete transport stage also blocks escalation. Earlier tests on Zherui's node are historical evidence, not proof that my node works. Preserve all error and stop evidence.

4. Run the assigned-shard pilot, then start continuous collection.

After passing and reviewing all stages, run:

bash medium/scripts/run_dcc.sh pilot --shard 2 --reconciliation "$task_ledger" --cache-root "$task_cache" --exclusive-team-window

This pilot permits 40 request attempts and uses 20 transport threads in one collector. Observe it about once per minute. Inspect statuses, request_starts.jsonl, request_gate.json, report/status.json, report/heartbeat.json, candidate IDs/dates, retained sources and free-only mirror bodies. Verify at least 1.5 seconds between request starts across all workers and at least six seconds between starts by the same worker, including feeds and mirrors. Confirm actual node/job/process/cache/lock and code bindings. Keep successful and failed items intact.

After a healthy reviewed pilot, continue in the same pinned cache:

bash medium/scripts/run_dcc.sh production --shard 2 --reconciliation "$task_ledger" --cache-root "$task_cache" --exclusive-team-window

Use the established detached launch procedure on the compute node, retain private stdout/stderr and record the actual PID. Do not start 20 separate collectors or run shard 1. Maintain the shared 250,000 baseline-plus-new in-window RSS candidate target, known-ID deduplication, primary-before-reserve order and study dates 2020-01-01 through 2026-09-17. Fetch only assigned profiles and in-window RSS-linked story tasks. Do not launch sitemap discovery or unscoped mirrors. Retain mirror bodies only when explicitly free; preserve the 20 GiB free-space reserve.

5. Apply the HTTP recovery policy and start real monitoring.

Record every ordinary non-429 HTTP error, including 401/403/404/410/5xx; mark the item failed, skip it and keep scraping other assignments. Record each HTTP 429 attempt, keep its item pending, stop new starts across all workers for at least 60 seconds, honor a longer Retry-After, then retry automatically and continue. Repeated 429s renew the cooldown; they do not cause a permanent three-event pause. Preserve explicit access-challenge, parser/schema, disk/resource and operator stops. Do not bypass challenges, rotate IPs, clear STOP/PAUSED automatically, reopen blocked routes or requeue historical failed items.

Verify the live collector with medium/scripts/check_run.py. Start and verify the actual compute watcher:

.venv/bin/python medium/scripts/monitor_dcc.py start --cache-root "$task_cache" --interval-seconds 600
.venv/bin/python medium/scripts/monitor_dcc.py status --cache-root "$task_cache"

Check the actual watcher PID/command/lock, node/job/cache/source binding and first observation. It records newly observed HTTP/transport/parser errors and treats cooldown_auto_resume as expected. It never fetches or restarts the scraper.

Also create and verify my own persistent ten-minute external notification/expiry schedule outside the compute allocation using available scheduling tools. It must inspect actual job/collector/watcher liveness, bindings, request progress, errors, cooldown, STOP/PAUSED and disk space. Stay quiet during healthy progress and normal cooldown; notify only on meaningful changes, completion, new blockers, monitor death, allocation expiry or required input. Verify a real initial check and retain the schedule registration. DCC logs alone are not notifications; the compute watcher ends with its allocation. If external scheduling is unavailable, say so and keep the run supervised instead of claiming unattended monitoring is active.

On timeout/preemption, verify the prior process and lock are gone, inspect markers and interrupted work, and resume only a compatible checkpoint under its original revision and a still-valid exclusive window. Do not automatically restart an unexplained exit or remove a protective marker.

6. Preserve and report the result.

When complete or intentionally stopped, wait for collector exit/free lock, export with parallel_medium_collection.py export to a new private directory, verify its manifest and archive SHA-256, and preserve all responses, failures, versions and pending work. Keep databases, raw responses, article bodies and private logs out of GitHub. Record the exact commit/bindings, ledger/reference hashes, allocation/node/PIDs, stage assessments/comparisons/replays, pilot evidence, collector/watcher/schedule status, request/error counts, new deduplicated candidates, remaining tasks, archive path/checksum and coverage limitations. Update my available durable task documentation. Do not send messages to others or upload corpus data without separate authorization.

Give me a concise outcome with actual verified state: running and monitored, completed, waiting for the team window, or blocked for a specific reason. Never claim startup, successful tests, monitoring, export or complete historical coverage from a plan or files alone. Acquisition candidates are not validated people, complete histories or a finalized monthly sample.
```
