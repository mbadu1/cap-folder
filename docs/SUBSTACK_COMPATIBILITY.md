# Check that your scraper matches the team's scraper

Use the latest `zherui-substack-month-pilot` branch. This test update preserves the original collector files and frozen assignments from commit `4d8d6e5`; existing checkpoints remain compatible.

Run from the repository root with Python 3.10 or newer. No extra packages, Substack requests, original database, or files from Zherui's computer are required.

The committed `.gitattributes` preserves LF line endings for code, assignments and fixtures across checkouts. This prevents Git's platform-specific line-ending conversion from changing the frozen file fingerprints. Use a fresh checkout of the updated branch if an older checkout has already converted these files; preserve any local work first.

## Before scraping

Ziyang:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 2
```

Michael:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 3
```

Zherui uses `--shard 1`. Expected terminal output begins with `PASS` and reports **30 tests passed**. The command writes `.cache/substack_compatibility/shard-2.json` (or `shard-3.json`). It uses temporary synthetic checkpoints and blocks accidental network connections; it does not read or alter production checkpoints. A failure returns a nonzero exit code and saves a report explaining the failing check.

The report records your Python/OS, Git revision, collector file hashes, batch hash, test outcomes, and a fixed expected-output fingerprint. All three teammates should obtain:

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
| Real parser → checkpoint → split export → three-shard merge | Lost/changed records, failed serialization of Unicode/newlines, lost error provenance and incorrectly combined assignments |
| Deliberately corrupted inputs/exports | Changed bodies, mismatched hashes/counts, wrong shards, missing/reordered parts and incomplete exports labeled complete |

The fixed examples are entirely synthetic. Compare normalized record fingerprints, not SQLite file bytes or compressed archive bytes, which may differ because of observation times, storage layout and compression.

## Check the actual collected output before returning it

Stop the shard worker and export it using the team guide. Then run, for example:

```sh
python3 scripts/substack/check_substack_compatibility.py --shard 2 --export-dir .cache/substack_exports/2026-09-28-team-v1-shard-2-final --report .cache/substack_compatibility/shard-2-final.json
```

This reads all exported records without changing them. It checks ownership, completeness, file checksums, publication/post identities, dates and months, within-publication duplicates and order, body hashes, word counts, access metadata, source fields, and preserved errors. It needs no original baseline. A large export takes longer to inspect than the initial fixture check.

For a deliberately incomplete pilot export, add `--allow-partial`. A valid incomplete result is labeled `PASS_PARTIAL`, with its pending count, and must not be presented as complete. A completed assignment with recorded failures may pass integrity checks; failures remain explicitly counted and are not successful histories.

Return the final JSON report with your export manifest and compressed record parts. The normal baseline-plus-three-shard merge and final creator/month/topic audit still apply. Passing compatibility and integrity checks does not establish that Substack supplied every historical post, that source metadata is factually correct, or that every acquired record qualifies for the final analytic sample.

## Paste to your AI agent

```text
In mbadu1/cap-folder on zherui-substack-month-pilot, read docs/SUBSTACK_COMPATIBILITY.md. Run python3 scripts/substack/check_substack_compatibility.py --shard MY_SHARD, replacing MY_SHARD with 2 for Ziyang or 3 for Michael. Require PASS with no skipped tests before beginning my live collection. Give me the JSON report path and its golden_output_sha256. Diagnose a failure without changing the shared scraper, frozen assignments, or expected fixture outputs.

After I have a final export, run the same checker with --export-dir pointing to it and --report pointing to a new final report. Require export_validation.status = PASS and pending = 0 before labeling delivery complete. Preserve/report failed publications separately. For an interim export use --allow-partial and clearly label PASS_PARTIAL. Include the final report with the return package.
```
