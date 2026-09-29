# Run the two Substack shards with 20 workers on DCC

**2026-09-29: the runner records all HTTP 403 responses as failed publications and continues other assignments.** Use `scripts/substack/parallel_team_collection.py` from the same reviewed Git commit on both machines. It uses the frozen `2026-09-28-team-v2` assignments, parser, SQLite schema, export command, and merge command. The original `team_collection.py` and v2 manifest remain unchanged. The parallel runner pins its own SHA-256 in each cache and refuses a different runner on resume.

Read the repository's [AGENTS.md](../AGENTS.md) first. From a local chat, Ziyang operates his DCC checkout through `ssh dcc-agent '<command>'`. If his chat is attached to a remote project through that alias, verify that its shell is already on the Slurm compute allocation and run there directly without nested SSH. Zherui uses the corresponding CPU compute route. These are CPU jobs; do not compute on a DCC login node. Run the commands below **inside the project checkout on a compute allocation**. `--workers 20` means 20 threads in one process and one shard cache. It does not request 20 Slurm CPUs.

## Before either production shard

Use the same pushed commit and verify it with `git rev-parse HEAD`. From the repository root:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 2
python3 scripts/substack/parallel_team_collection.py run --shard 2 --check
```

Zherui substitutes `--shard 1`. The compatibility check is offline and includes 49 tests and the unchanged golden parser fingerprint `2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded`. `--check` validates the frozen assignment without network or cache writes. Do not start shard 1 while its existing `PAUSED.json` is in place; inspect and resolve that access stop first. Do not run two collectors on one shard cache.

## Check DCC output against the earlier local baseline

The checked-in [40-URL baseline sample](../data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json) is available to both owners after pulling the shared branch. Zherui generated it locally from his preserved 93,799-attempt baseline (92,452 successes), selecting 40 successful publications by fixed SHA-256 order, enough for two waves of 20 workers. The generation command checked the database against the frozen baseline inventory. The JSON contains URLs only, not historical payloads. To regenerate it into a new file for audit, Zherui can run:

```sh
python3 scripts/substack/parallel_team_collection.py validation-list --reference-db .cache/substack_history/baseline_team_2026-09-28/crawl.sqlite3 --output .cache/substack_parallel_validation/baseline-40-urls-regenerated.json
```

Ziyang uses the JSON directly from the pulled repository. On a DCC compute allocation, he runs the bounded test in its **own** cache:

```sh
python3 -u scripts/substack/parallel_team_collection.py validation-run --urls data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json --cache-root .cache/substack_parallel_validation/baseline-40-cache --workers 20 --global-gap-seconds 0.6 --continuous
python3 scripts/substack/parallel_team_collection.py validation-export --urls data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json --cache-root .cache/substack_parallel_validation/baseline-40-cache --output .cache/substack_parallel_validation/baseline-40-export
```

The validation run uses the same six-second minimum per worker and a 0.6-second minimum gap between request starts **within this process**. It records every HTTP 403 as a failed history and continues; HTTP 401/429 and circuit stops pause it. If it pauses, preserve `PAUSED.json`, diagnose the response, and return the partial test export; do not label the comparison exact. Ziyang returns the private `baseline-40-export` directory to Zherui. It has a checksum manifest and up to 40 test records; it is never merged into production. Ziyang does not need Zherui's baseline database or shard-1 checkpoint.

Zherui compares that export against his local database:

```sh
python3 scripts/substack/parallel_team_collection.py validation-compare --urls data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json --reference-db .cache/substack_history/baseline_team_2026-09-28/crawl.sqlite3 --export-dir .cache/substack_parallel_validation/baseline-40-export --report .cache/substack_parallel_validation/baseline-40-comparison.json
```

The report is `PASS_EXACT` only when all 40 URLs have identical complete normalized payloads. It lists field-level differences and both observation times. `REVIEW_REQUIRED` blocks a sustained run until Zherui inspects changed source content versus a collector discrepancy. A live refetch may differ because the source changed; retain the old result and the test export instead of changing the reference to force a pass. This sampled gate checks alignment with the earlier 90,000+ baseline; it does not assert equality for every previously scraped publication.

## Monitor the active run

Before launching, record the checkout, Git commit, cache path, process ID, compute node, Slurm job ID and stdout/stderr log location. Keep observing the 40-publication validation test in the active session, checking about once per minute until completion or a pause. The production monitoring cadence is every ten minutes after production is authorized and launched.

At each check:

- Confirm the original process and Slurm allocation still exist. A new SSH connection can provision a different allocation; verify the node/job identity before interpreting a process ID. An unreachable old allocation is an incident, not evidence that collection restarted elsewhere.
- Read the active cache's `collection_status.json`, the latest entries of `request_starts.jsonl`, and a bounded tail of the run's stdout/stderr. Track committed successful/failed/pending counts, new request starts, HTTP statuses, `PAUSED.json`, `STOP`, and process exit.
- Check memory, available storage and remaining Slurm walltime when running on DCC. Report approaching resource limits, preemption, unexpected exits, access/rate stops and completion.
- Treat `collection_status.json` as a publication-boundary snapshot, not a continuously refreshed heartbeat. A long history can leave it unchanged while requests continue. If neither requests nor committed progress advance over successive checks, inspect the process and logs for a suspected stall. Do not start a duplicate collector because a status file is old.

During validation, report meaningful progress and export the completed or partial test cache once the collector exits. Scheduled production monitoring should stay quiet while healthy or unchanged and notify only on a meaningful change or needed action. Preserve checkpoint/binding/pause files; monitoring does not authorize automatic retries, restarts, rate changes, `scancel`, or advancement from validation to production.

If production will continue beyond the active session, configure and verify a persistent monitor in the operator's own environment before leaving it unattended. Record the monitor's schedule, run identity and notification destination. The repository supplies these instructions and the collector telemetry; pulling it does not install a scheduler or transfer Zherui's existing local monitor to Ziyang. Do not report monitoring as active until its registration and an initial successful check are verified.

## Pilot and resume the assigned shard

After the baseline comparison and permission/rate terms are reviewed, Ziyang starts shard 2 with a five-publication pilot:

```sh
python3 -u scripts/substack/parallel_team_collection.py run --shard 2 --workers 20 --global-gap-seconds 0.6 --limit 5
python3 scripts/substack/team_collection.py status --batch data/substack_assignments/2026-09-28-team-v2 --shard 2
```

The default production cache remains `.cache/substack_shards/2026-09-28-team-v2/shard-2/`. `request_starts.jsonl` logs each request start for pace review, and `collection_status.json` records HTTP status counts. Continue the same cache with bounded chunks or `--continuous` after checking the pilot. Resume with the **same runner revision**, shard, and cache; committed successes and failures are skipped. For shard 1, Zherui substitutes `--shard 1` and uses its existing v2 checkpoint only after resolving the preserved pause. Do not delete a cache to resume.

Both shards at `--global-gap-seconds 0.6` would have two independent gates. **Run them at separate times** until a reliable shared cross-shard gate is implemented and verified. The one-minute DCC probe does not establish a sustained two-shard aggregate rate. Keep the written Substack permission and DCC terms as the upper bounds; use a slower gap if required. Any 401/429 or circuit stop creates `PAUSED.json`; new request starts stop while in-flight histories settle. Every HTTP 403 is recorded as a failure and skipped without retrying that publication, including unexplained or challenge responses. `http_403_failures_this_run` counts all such failures. A 403 is classified as invite-only only when its complete bounded HTML/plain-text response has the exact known invitation message and has no Retry-After, challenge header, or recognized challenge marker. It is saved as `InviteOnlyPublication` with the response hash, remains a failure, and is skipped on resume. No restricted content is fetched and no extra diagnostic request is made by this classification.

## Upgrade an existing stopped parallel cache

The expanded HTTP 403 handling changes the runner hash. Do not rewrite an existing `parallel_runner.json`. Use the checked migration for either supported pinned predecessor (`d81df221…` or `087aab2c…`), preserving its database, binding, and pause evidence:

```sh
python3 scripts/substack/upgrade_parallel_cache.py --shard 1 --source-cache .cache/substack_shards/2026-09-28-team-v2/shard-1-invite-only-v1 --cache-root .cache/substack_shards/2026-09-28-team-v2/shard-1-http403-v2
```

The command verifies source assignment and runner bindings, locks both caches, copies via SQLite backup, checks exact ordered rows and integrity, preserves STOP/PAUSED markers, binds the separate successor, and retires the source. It refuses an existing destination or unexpected predecessor. It never clears a pause. After the carried pause is diagnosed and deliberately archived under operator authorization, run with the **explicit successor cache**:

```sh
python3 scripts/substack/parallel_team_collection.py run --shard 1 --cache-root .cache/substack_shards/2026-09-28-team-v2/shard-1-http403-v2 --workers 20 --global-gap-seconds 0.6 --continuous
```

Use this same `--cache-root` for status/export commands after migration. Historical exports and validation reports retain their original runner provenance; the original 40-URL validation remains unchanged. Successful payload parsing, frozen assignments, SQLite/export schema, rate limits and HTTP 401/429 stops are unchanged. The 50 offline checks cover all-403 continuation, retained failures/no refetch, correct invite-only classification, HTTP 401/429 stops, and exact stopped-cache upgrade with preserved markers and source binding.

## Export and return

Stop the production runner, then use the existing v2 exporter and compatibility checker, substituting the owner’s shard number and an unused output directory:

```sh
python3 scripts/substack/team_collection.py export --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --output .cache/substack_exports/2026-09-28-team-v2-shard-2-parallel-final
python3 scripts/substack/check_substack_compatibility.py --shard 2 --export-dir .cache/substack_exports/2026-09-28-team-v2-shard-2-parallel-final --report .cache/substack_compatibility/2026-09-28-team-v2/shard-2-parallel-final.json
```

Require `export_validation.status = PASS` and zero pending assigned publications for a complete handoff. Failed publications remain explicit. Return the final export through the private draft release described in the [team plan](SUBSTACK_TEAM_SCRAPING_PLAN.md); merge both v2 shard exports with the preserved baseline only after both are complete. Include the parallel runner SHA, commit, request/status counts, and the baseline-sample comparison report in the handoff.
