# Project instructions

These instructions apply throughout this repository. Use the current checkout and the current user's DCC account and work directory; do not assume another team member's paths or credentials.

## Project memory

The full project workspace keeps canonical durable memory in:

- `research/PROJECT_CONTEXT.md`: goals, scope, stakeholders, constraints and open questions.
- `research/MODEL_DECISIONS.md`: approved measurement, sampling and validation decisions.
- `research/RESULTS_LOG.md`: verified results, caveats and reproducibility references.
- `research/literature/`: paper notes, index and supported synthesis.

When these files are available in this checkout or its enclosing project workspace, read `PROJECT_CONTEXT.md` before substantive work and the decisions/results files when the task touches methods or findings. For literature work, first read `research/literature/README.md`, `PAPER_INDEX.md` and `SYNTHESIS.md`.

A standalone clone of this repository does not include the enclosing workspace's research memory. Use the shipped task documentation below for the Substack handoff; those missing outer files do not block the documented setup and validation workflow. Do not invent their contents or copy unrelated private workspace files into the repository.

Record durable changes in the relevant available memory or task documentation using ISO dates. Keep confirmed facts separate from assumptions; identify data/version, method, evidence and caveats for results. Revise conflicting active entries. Do not record secrets, credentials, raw confidential data or client-restricted content in Markdown memory.

## DCC execution

- Never run project work on `dcc-login.oit.duke.edu` or another DCC login node.
- From a local shell, use `ssh dcc-agent '<command>'` for CPU work and `ssh dcc-agent-gpu '<command>'` for work that needs a GPU. These configured profiles provision or reuse a Slurm allocation; do not manually invoke `sbatch`, `salloc` or `srun` as a substitute.
- A remote project opened through one of these SSH aliases already uses that route. Verify that its shell is on a compute node within an active Slurm allocation, then use that remote shell directly rather than creating nested SSH connections. If it is on a login node, reconnect through the compute alias before project work.
- If a required alias is missing, obtain the user's setup information; do not invent cluster hosts, accounts or access settings.
- Keep code, checkpoints and outputs in the current user's designated DCC work directory. Confirm the checkout path, compute node and Slurm job when starting remote work.
- Initial connection after inactivity can take time while Slurm provisions a job. Do not treat that delay alone as failure.
- Never use `scancel` on agent jobs unless the user explicitly asks. The idle watchdog cleans up unused jobs; preserve checkpoints for preemption or walltime expiry.

### SSH sockets

On the machine initiating SSH, profiles may use `ControlPath ~/.ssh/sockets/%r@%h-%p`. If SSH reports that this parent directory is missing, fix it locally before retrying:

```sh
mkdir -p ~/.ssh/sockets && chmod 700 ~/.ssh/sockets
```

Mode 700 protects the authenticated control sockets. Do not mistake this local filesystem problem for a DCC allocation failure.

### Resources

Use the smallest allocation sufficient for the task. Substack collection is CPU-only network work: 20 worker threads do not imply 20 CPUs, and no GPU is needed.

The launcher supports `--partition`, `--account`, `--qos`, `--gres`, `--cpus`, `--mem`, `--time`, `--idle-min`, `--wait`, `--name` and `--log`. A modest CPU configuration example is:

```text
--name agent --cpus 4 --mem 16G --time 8:00:00 --gres none
```

When the task requires different resources, adjust only the needed launcher arguments in the existing profile's `ProxyCommand`. Preserve unrelated SSH settings and credentials. Do not invent or change accounts, QoS or partition access without confirmed cluster information. Changes do not resize an existing allocation; verify its effective resources and never cancel it merely to force a resize. Request a GPU only for workloads that benefit from it.

## Substack team workflow

Read these documents in order before working on the DCC parallel rollout:

1. [Team scraping plan](substack/docs/SUBSTACK_TEAM_SCRAPING_PLAN.md): ownership, assignments and handoff.
2. [DCC 20-worker rollout design](substack/docs/SUBSTACK_DCC_20_WORKER_PLAN.md): rationale, pacing and validation gates.
3. [Compatibility guide](substack/docs/SUBSTACK_COMPATIBILITY.md): offline and actual-export checks.
4. [Parallel runbook](substack/docs/SUBSTACK_PARALLEL_RUNBOOK.md): executable setup, validation, collection and export commands.

Use the parallel runbook for the 20-worker route; the team plan also retains the original single-worker commands.

- Active assignment batch: `2026-09-28-team-v2`. Zherui owns shard 1; Ziyang owns shard 2. Preserve the frozen assignment files and manifests.
- Initial validation uses the checked-in [40-URL sample](data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json) from the original 93,799-attempt local baseline. The 22 attempts in Zherui's shard-1 checkpoint are separate from that baseline.
- Run the offline compatibility check and shard preflight before live validation. Rescrape the 40 URLs into a separate validation cache and export the results for Zherui to compare with his unchanged local baseline. All starting inputs are in this repository; Ziyang does not need the baseline database or shard-1 checkpoint.
- Complete the baseline comparison and review any differences before production collection. `PASS_EXACT` applies only to the sampled payloads. Never rewrite the historical reference or expected fixtures to force a pass.
- Keep the six-second minimum per worker and at least 0.6 seconds between request starts within the process, subject to the permission terms in the runbook. Run the two shards at separate times until a shared aggregate gate is implemented and verified.
- HTTP 403s without rate-limit evidence are recorded as failed histories by the current parallel runner and do not pause other assignments; confirmed invite-only responses retain their specific classification. HTTP 429 uses a shared automatic cooldown for Retry-After (seconds or HTTP-date; 60 seconds if missing/invalid), then retries the interrupted request. Rate-limited HTTP 403s, HTTP 401 and other circuit stops still require deliberate resumption. A changed runner must use the checked separate-cache upgrade in the runbook; never rewrite a prior cache binding.
- Preserve `PAUSED.json`, `STOP`, retirement markers and committed errors. Diagnose an access/rate stop before deliberate resumption; do not automatically delete markers or reset caches.
- The runner pins its source hash to each cache. Preserve that binding and use the pinned revision when resuming; do not edit the binding to bypass a mismatch.
- Use one collector per shard cache. Keep validation records outside production exports and the final merge. Use the existing export/check/merge commands and report incomplete or failed results accurately.
- Follow the runbook's monitoring procedure: observe validation in the active session and verify a persistent monitor before leaving authorized production unattended. Check actual process/job and request progress alongside publication-boundary status. Do not claim a monitor is active merely because telemetry files exist.

## Files and verification

- `.cache/` contains durable scraping data and progress. Preserve it; it is not disposable build output.
- Keep databases, raw corpus payloads, credentials and logs out of Git. The URL-only baseline sample is intentionally tracked. Follow the team plan for private data exports.
- Preserve unrelated local changes. Stage only files belonging to the requested task.
- Run checks appropriate to the change. Documentation-only changes need link/path and diff checks; collector changes need the relevant offline compatibility tests. Do not make live scraping requests merely to test documentation.

## Medium team workflow — 2026-10-04

- Current user scope is primary-only: 96,160 profiles per owner. Hold the 25,000 reserve feeds per shard until sampling problems are reviewed and the user explicitly reauthorizes them. Preserve reserve CSVs/hashes and any prior results. Run `medium/scripts/defer_reserves.py --cache-root PATH` after preparation; `run_dcc.sh` applies it before pilot/production. Existing live caches can receive this narrowly scoped audited metadata transaction only while primary feeds are pending; do not stop/restart/rebind a healthy collector. Keep `deferred` reserve rows out of scheduling, separate from failed/completed work. Later enabling is a separate explicit user decision, not automatic fallback.

- Read `medium/README.md`, `medium/docs/TEAM_SCRAPING_PLAN.md`, `medium/docs/DCC_RUNBOOK.md` and the current `medium/handoff/CURRENT_WINDOW.json`.
- The user explicitly authorized concurrent scraping: Zherui shard 1 and Ziyang shard 2 may run simultaneously in their own accounts and separate caches. Do not enforce exclusive windows, require another owner to stop, or wait for a new stopped-owner release. Keep Zherui's active collector and monitor running. The prior release/withdrawal artifacts are historical and superseded by this decision.
- Preserve frozen batch `2026-10-03-team-v1` CSVs/manifests. Use the shard-aware runner, one collector per cache; do not launch duplicate autonomous legacy collectors. Each collector has 20 transport threads, a 1.5-second request-start gap and six seconds per worker. Gates/cooldowns are per collector; there is no distributed timer or counter.
- Ziyang uses the shipped `medium/handoff/2026-10-03-ziyang-shard-2/final-ledger.json.gz` and its receipt for metadata exclusions, plus the current parallel authorization and source pins. Old `requires_exclusive_team_window` metadata records historical policy, not a current requirement. Preserve all known keys, completed/failed tasks, blocked routes, cooldowns and source data. New code/ledger/configuration requires a fresh cache; never rebind the running shard-1 cache.
- Run offline tests, source-bound fixed baseline replay and staged 1/10/20-worker validation, then an assigned-shard 40-attempt pilot before continuous production. The current validation bundle is `medium/validation/2026-10-03-baseline-40-parallel/`; earlier bundles stay immutable. Both shards may overlap these tests. Validation caches never enter production exports/merges.
- Record/skip ordinary non-429 HTTP errors and continue. Log every 429 attempt, retain its pending item, pause new starts within that collector for at least 60 seconds (longer Retry-After honored), then retry automatically. Repeated 429s renew cooldown without permanent pause. Preserve explicit challenge/schema/resource/operator stops and historical failures.
- Preserve study dates, assigned-feed/in-window-story scope, primary-only collection; reserves held until explicit sampling review, free-only mirror bodies and 20 GiB disk reserve. The study acquisition target is approximately 250,000 unique dated RSS candidate keys. Each independent run uses its baseline-plus-local counter; final deduplicated team count is computed at merge and can differ from either local count. No exact distributed 250,000 stop is claimed.
- Export each stopped shard privately and merge into a new baseline copy after source writes have stopped. Deduplicate cross-shard aliases/story IDs, preserve source versions/failures and report conflicts. Never merge into an active collector cache. Metadata snapshots do not contain bodies; corpus databases/raw responses/logs stay outside Git.
- Start and verify `medium/scripts/monitor_dcc.py` and the operator's own external notification/expiry schedule before unattended scraping. Check actual PID/command/lock/node/job/source/cache bindings and request progress. Watchers do not fetch or restart collectors; keep notifications quiet during healthy progress and expected cooldown.
- Use the original pinned revision when resuming an existing cache. Preserve protective markers and the separate local baseline/transfer. Never stop a teammate's scraper merely for handoff coordination or ask again whether their authorized shards may overlap.
