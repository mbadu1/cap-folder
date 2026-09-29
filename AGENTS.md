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

1. [Team scraping plan](docs/SUBSTACK_TEAM_SCRAPING_PLAN.md): ownership, assignments and handoff.
2. [DCC 20-worker rollout design](docs/SUBSTACK_DCC_20_WORKER_PLAN.md): rationale, pacing and validation gates.
3. [Compatibility guide](docs/SUBSTACK_COMPATIBILITY.md): offline and actual-export checks.
4. [Parallel runbook](docs/SUBSTACK_PARALLEL_RUNBOOK.md): executable setup, validation, collection and export commands.

Use the parallel runbook for the 20-worker route; the team plan also retains the original single-worker commands.

- Active assignment batch: `2026-09-28-team-v2`. Zherui owns shard 1; Ziyang owns shard 2. Preserve the frozen assignment files and manifests.
- Initial validation uses the checked-in [40-URL sample](data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json) from the original 93,799-attempt local baseline. The 22 attempts in Zherui's shard-1 checkpoint are separate from that baseline.
- Run the offline compatibility check and shard preflight before live validation. Rescrape the 40 URLs into a separate validation cache and export the results for Zherui to compare with his unchanged local baseline. All starting inputs are in this repository; Ziyang does not need the baseline database or shard-1 checkpoint.
- Complete the baseline comparison and review any differences before production collection. `PASS_EXACT` applies only to the sampled payloads. Never rewrite the historical reference or expected fixtures to force a pass.
- Keep the six-second minimum per worker and at least 0.6 seconds between request starts within the process, subject to the permission terms in the runbook. Run the two shards at separate times until a shared aggregate gate is implemented and verified.
- Confirmed invite-only 403s are recorded as failed histories by the current parallel runner and do not pause other assignments. Unexplained access failures, challenges, 401/429 and circuit stops still pause collection. A changed runner must use the checked separate-cache upgrade in the runbook; never rewrite a prior cache binding.
- Preserve `PAUSED.json`, `STOP`, retirement markers and committed errors. Diagnose an access/rate stop before deliberate resumption; do not automatically delete markers or reset caches.
- The runner pins its source hash to each cache. Preserve that binding and use the pinned revision when resuming; do not edit the binding to bypass a mismatch.
- Use one collector per shard cache. Keep validation records outside production exports and the final merge. Use the existing export/check/merge commands and report incomplete or failed results accurately.
- Follow the runbook's monitoring procedure: observe validation in the active session and verify a persistent monitor before leaving authorized production unattended. Check actual process/job and request progress alongside publication-boundary status. Do not claim a monitor is active merely because telemetry files exist.

## Files and verification

- `.cache/` contains durable scraping data and progress. Preserve it; it is not disposable build output.
- Keep databases, raw corpus payloads, credentials and logs out of Git. The URL-only baseline sample is intentionally tracked. Follow the team plan for private data exports.
- Preserve unrelated local changes. Stage only files belonging to the requested task.
- Run checks appropriate to the change. Documentation-only changes need link/path and diff checks; collector changes need the relevant offline compatibility tests. Do not make live scraping requests merely to test documentation.
