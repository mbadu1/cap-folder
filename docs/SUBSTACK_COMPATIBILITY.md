# Check that your scraper matches the team's scraper

Use the new two-person handoff on `zherui-substack-month-pilot`, batch `2026-09-28-team-v2`. Zherui owns shard 1 and Ziyang owns shard 2. This release changes the orchestration and assignment fingerprints. The article parser and fixed expected output are unchanged from the earlier handoff; use the new batch and its dedicated checkpoint.

Run from the repository root with Python 3.10 or newer. No extra packages, Substack requests, original database, or files from Zherui's computer are required.

The committed `.gitattributes` preserves LF line endings for code, assignments and fixtures across checkouts. This prevents Git's platform-specific line-ending conversion from changing the frozen file fingerprints. Use a fresh checkout of the updated branch if an older checkout has already converted these files; preserve any local work first.

## Before scraping

Ziyang:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 2
```

Zherui:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 1
```

Expected terminal output begins with `PASS` and reports **45 tests passed** on the parallel-runner release. The command writes `.cache/substack_compatibility/2026-09-28-team-v2/shard-2.json` (or `shard-1.json`). It uses temporary synthetic checkpoints and blocks accidental network connections; it does not read or alter production checkpoints. A failure returns a nonzero exit code and saves a report explaining the failing check.

The report records your Python/OS, Git revision, collector file hashes, batch hash, test outcomes, and a fixed expected-output fingerprint. Both teammates should obtain:

```text
2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded
```

Share the JSON report with Zherui. `export_validation: NOT_RUN` is expected for this initial check: it verifies your installation and parser, while actual output is checked below. If it fails, inspect the report and align with the shared revision; do not rewrite expected outputs or hashes to force a pass.

## What is checked

| Check | Problem it catches |
|---|---|
| Frozen source-code and batch fingerprints | A different scraper, edited assignment, or incorrect starting revision |
| Fixed synthetic API responses and independently specified expected records | Differences in schema, IDs, dates, Unicode text, paragraph handling, counts, hashes, coauthors and observed zero engagement |
| Date bounds and access cases | Out-of-window records; retention of paid, unknown-access or unlock-required bodies |
| Pagination and overlapping/repeated pages | Incorrect offsets, repeated posts within a history, and failure to stop at the date boundary |
| Resume, assignment binding, locks and stop handling | Refetched completed work, reused shard caches, duplicate local workers and inappropriate restarts |
| Real parser → checkpoint → split export → two-shard merge (plus legacy three-shard regression coverage) | Lost/changed records, failed serialization of Unicode/newlines, lost error provenance and incorrectly combined assignments |
| Three-to-two owner migration | Lost old records, changed timestamps/errors, reassigned old-owner URLs, overwritten target caches, or dropped pause/stop markers |
| Deliberately corrupted inputs/exports | Changed bodies, mismatched hashes/counts, wrong shards, missing/reordered parts and incomplete exports labeled complete |

The fixed examples are entirely synthetic. Compare normalized record fingerprints, not SQLite file bytes or compressed archive bytes, which may differ because of observation times, storage layout and compression.

## Check the actual collected output before returning it

Stop the shard worker and export it using the team guide. Then run, for example:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 2 --export-dir .cache/substack_exports/2026-09-28-team-v2-shard-2-final --report .cache/substack_compatibility/2026-09-28-team-v2/shard-2-final.json
```

This reads all exported records without changing them. It checks ownership, completeness, file checksums, publication/post identities, dates and months, within-publication duplicates and order, body hashes, word counts, access metadata, source fields, and preserved errors. It needs no original baseline. A large export takes longer to inspect than the initial fixture check.

For a deliberately incomplete pilot export, add `--allow-partial`. A valid incomplete result is labeled `PASS_PARTIAL`, with its pending count, and must not be presented as complete. A completed assignment with recorded failures may pass integrity checks; failures remain explicitly counted and are not successful histories.

Return the final JSON report with your export manifest and compressed record parts. The normal baseline-plus-two-shard merge and final creator/month/topic audit still apply. Passing compatibility and integrity checks does not establish that Substack supplied every historical post, that source metadata is factually correct, or that every acquired record qualifies for the final analytic sample.

## Additional exact-alignment gate for the DCC parallel collector

The commands above now include offline tests of the 20-worker runner and fixed-output equality with the existing single-worker parser. They still do not establish that a later **live** DCC rescrape equals Zherui's previously saved local results. Ziyang uses Codex locally to operate his DCC compute checkout; follow the [parallel runbook](SUBSTACK_PARALLEL_RUNBOOK.md).

The implemented `validation-list`, `validation-run`, `validation-export`, and `validation-compare` commands perform the live baseline sample test. Zherui supplies only a checksummed list of 40 successful URLs selected from his earlier 93,799-attempt local baseline by fixed hash order. Ziyang returns a checksummed export from a disposable DCC test cache. Zherui compares it locally with his unchanged baseline; Ziyang does not need that database or the shard-1 checkpoint. The test records cannot enter the production merge. `PASS_EXACT` requires all 40 complete payloads to match with no extra or missing rows. A mismatch or access stop is `REVIEW_REQUIRED` and blocks sustained collection until Zherui reviews it. The existing v2 shard checkpoints are reused directly by the parallel runner, so there is no shard migration step.

The overlap comparator checks every normalized payload field, including post order, IDs, dates, text, access values, and hashes; it excludes only run-generated observation times and SQLite file layout. Offline tests verify that the parallel and existing runners produce identical payloads from the same fixed responses, and that a changed payload yields `REVIEW_REQUIRED`. For a live mismatch, source drift must be investigated separately before attributing it to the collector. Do not rewrite the old reference to force a pass.

The current v2 fixture and its expected hash are unchanged. The compatibility report now records `parallel_runner_sha256`; the runner is pinned separately in each cache. The checker validates every actual v2 export and flags incomplete exports. The two production shard URL lists exclude all baseline attempts; the 40 baseline histories are refetched only in the disposable validation cache. This sample does not test every one of the 92,452 successful baseline histories.

## Paste to your AI agent for the current v2 collector

For the 20-worker DCC route, use the [parallel runbook](SUBSTACK_PARALLEL_RUNBOOK.md). The prompt below checks the shared parser/assignment and export integrity for either runner.

```text
In mbadu1/cap-folder on zherui-substack-month-pilot, read docs/SUBSTACK_COMPATIBILITY.md. Run python3 scripts/substack/check_substack_compatibility.py --shard MY_SHARD, replacing MY_SHARD with 1 for Zherui or 2 for Ziyang. Require PASS with no skipped tests before beginning my live collection. Give me the JSON report path and its golden_output_sha256. Diagnose a failure without changing the shared scraper, frozen assignments, or expected fixture outputs.

After I have a final export, run the same checker with --export-dir pointing to it and --report pointing to a new final report. Require export_validation.status = PASS and pending = 0 before labeling delivery complete. Preserve/report failed publications separately. For an interim export use --allow-partial and clearly label PASS_PARTIAL. Include the final report with the return package.
```
