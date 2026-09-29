# Run the two Substack shards with 20 workers on DCC

**Code available; no live DCC full-history run has been verified.** Use `scripts/substack/parallel_team_collection.py` from the same reviewed Git commit on both machines. It uses the frozen `2026-09-28-team-v2` assignments, parser, SQLite schema, export command, and merge command. The original `team_collection.py` and v2 manifest remain unchanged. The parallel runner pins its own SHA-256 in each cache and refuses a different runner on resume.

Ziyang uses Codex locally to work in his DCC checkout through `ssh dcc-agent '<command>'`. Zherui uses the corresponding CPU compute route. These are CPU jobs; do not compute on a DCC login node. Run the commands below **inside the project checkout on a compute allocation**. `--workers 20` means 20 threads in one process and one shard cache. It does not request 20 Slurm CPUs.

## Before either production shard

Use the same pushed commit and verify it with `git rev-parse HEAD`. From the repository root:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 2
python3 scripts/substack/parallel_team_collection.py run --shard 2 --check
```

Zherui substitutes `--shard 1`. The compatibility check is offline and includes 45 tests and the unchanged golden parser fingerprint `2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded`. `--check` validates the frozen assignment without network or cache writes. Do not start shard 1 while its existing `PAUSED.json` is in place; inspect and resolve that access stop first. Do not run two collectors on one shard cache.

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

The validation run uses the same six-second minimum per worker and a 0.6-second minimum gap between request starts **within this process**. It pauses on access/rate failures. If it pauses, preserve `PAUSED.json`, diagnose the response, and return the partial test export; do not label the comparison exact. Ziyang returns the private `baseline-40-export` directory to Zherui. It has a checksum manifest and up to 40 test records; it is never merged into production. Ziyang does not need Zherui's baseline database or shard-1 checkpoint.

Zherui compares that export against his local database:

```sh
python3 scripts/substack/parallel_team_collection.py validation-compare --urls data/substack_validation/2026-09-28-team-v2/baseline-40-urls.json --reference-db .cache/substack_history/baseline_team_2026-09-28/crawl.sqlite3 --export-dir .cache/substack_parallel_validation/baseline-40-export --report .cache/substack_parallel_validation/baseline-40-comparison.json
```

The report is `PASS_EXACT` only when all 40 URLs have identical complete normalized payloads. It lists field-level differences and both observation times. `REVIEW_REQUIRED` blocks a sustained run until Zherui inspects changed source content versus a collector discrepancy. A live refetch may differ because the source changed; retain the old result and the test export instead of changing the reference to force a pass. This sampled gate checks alignment with the earlier 90,000+ baseline; it does not assert equality for every previously scraped publication.

## Pilot and resume the assigned shard

After the baseline comparison and permission/rate terms are reviewed, Ziyang starts shard 2 with a five-publication pilot:

```sh
python3 -u scripts/substack/parallel_team_collection.py run --shard 2 --workers 20 --global-gap-seconds 0.6 --limit 5
python3 scripts/substack/team_collection.py status --batch data/substack_assignments/2026-09-28-team-v2 --shard 2
```

The default production cache remains `.cache/substack_shards/2026-09-28-team-v2/shard-2/`. `request_starts.jsonl` logs each request start for pace review, and `collection_status.json` records HTTP status counts. Continue the same cache with bounded chunks or `--continuous` after checking the pilot. Resume with the **same runner revision**, shard, and cache; committed successes and failures are skipped. For shard 1, Zherui substitutes `--shard 1` and uses its existing v2 checkpoint only after resolving the preserved pause. Do not delete a cache to resume.

Both shards at `--global-gap-seconds 0.6` would have two independent gates. **Run them at separate times** until a reliable shared cross-shard gate is implemented and verified. The one-minute DCC probe does not establish a sustained two-shard aggregate rate. Keep the written Substack permission and DCC terms as the upper bounds; use a slower gap if required. A 401/403/429 or circuit stop creates `PAUSED.json`, and the runner stops new request starts while in-flight histories settle.

## Export and return

Stop the production runner, then use the existing v2 exporter and compatibility checker, substituting the owner’s shard number and an unused output directory:

```sh
python3 scripts/substack/team_collection.py export --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --output .cache/substack_exports/2026-09-28-team-v2-shard-2-parallel-final
python3 scripts/substack/check_substack_compatibility.py --shard 2 --export-dir .cache/substack_exports/2026-09-28-team-v2-shard-2-parallel-final --report .cache/substack_compatibility/2026-09-28-team-v2/shard-2-parallel-final.json
```

Require `export_validation.status = PASS` and zero pending assigned publications for a complete handoff. Failed publications remain explicit. Return the final export through the private draft release described in the [team plan](SUBSTACK_TEAM_SCRAPING_PLAN.md); merge both v2 shard exports with the preserved baseline only after both are complete. Include the parallel runner SHA, commit, request/status counts, and the baseline-sample comparison report in the handoff.
