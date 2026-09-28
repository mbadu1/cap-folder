# Three-person Substack scraping handoff

Prepared 2026-09-28. All three people perform the same task with the same parser and different publication URL lists. Everything needed to start is in this repository on branch `zherui-substack-month-pilot`. Record the shared handoff commit before collection; the launcher also verifies the exact collector file hashes.

## Assignments

Batch: `2026-09-28-team-v1`, frozen at 2026-09-28T23:25:53Z.

| Person | Shard argument | Publications |
|---|---|---:|
| Zherui | `--shard 1` | 49,418 |
| Teammate 1 | `--shard 2` | 49,418 |
| Teammate 2 | `--shard 3` | 49,417 |

The original frozen frame has 242,052 publications. The preserved baseline has 93,799 in-frame committed results: 92,452 successes and 1,347 errors. An additional out-of-frame error stays in the private baseline. The remaining 148,253 publications are sorted by their existing hash priority and assigned round-robin. The three lists are pairwise disjoint and exclude every baseline result. Counts balance publications, not history length or runtime.

Each person collects the full available history for their assigned publications from 2020-01-01 through 2026-09-17. September 2026 is a 17-day partial month. Keep public/free bodies and paid metadata according to the shared parser. Numeric author IDs and post IDs are resolved across the combined results later. A successful publication result can be empty and does not certify a complete historical archive.

## Setup and validation

Clone the repository or fetch the shared branch into an existing checkout. Preserve any local changes before switching branches. For a fresh checkout:

```sh
git clone --branch zherui-substack-month-pilot git@github.com:mbadu1/cap-folder.git
cd cap-folder
git rev-parse HEAD
python3 --version
python3 -m unittest discover -s tests -p 'test_substack*.py'
```

Python 3.10 or newer is recommended; the Substack tools need no third-party packages. Use `python` if that is your Python 3 executable. The Medium files are organized separately and are not part of these assignments.

Run the [compatibility check](SUBSTACK_COMPATIBILITY.md) before the live pilot: `python3 scripts/substack/check_substack_compatibility.py --shard 2` (or `--shard 3` for teammate 2). Require `PASS`; share the JSON report with Zherui. The added checks leave the frozen collector and assignment hashes unchanged, so existing checkpoints remain compatible with the updated branch.

The rest of the commands show **teammate 1 / shard 2**. Teammate 2 changes every shard `2` to `3`; Zherui uses `1`. Do not change the batch ID, frame files, scraper logic, dates, or hashes. No one needs Zherui's original database or outer project workspace.

```sh
python3 scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v1 --shard 2 --check
```

This makes no network requests. It verifies all frame/assignment files, checksums, collector code, nonoverlap, coverage, and the checkpoint binding if one exists. Missing input causes an error; it cannot silently fetch a new frame.

## Pilot, run, and resume

Start with five new publication histories:

```sh
python3 -u scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v1 --shard 2 --limit 5
```

Review the status, then continue with the same cache:

```sh
python3 scripts/substack/team_collection.py status --batch data/substack_assignments/2026-09-28-team-v1 --shard 2
python3 -u scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v1 --shard 2 --continuous
```

The default cache is `.cache/substack_shards/2026-09-28-team-v1/shard-2/`. It contains the actual data; never delete it to resume. The worker checkpoints every finished publication, preserves failures, reports progress, and refuses another process using the same checkpoint. It uses the existing `parse_publication_history()` and post parser, with no separately rewritten scraper. A bounded run without `--continuous` defaults to at most 500 new publications.

Keep the machine awake and retain the process output as a log. On macOS/Linux, a durable background launch after the pilot can use:

```sh
nohup python3 -u scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v1 --shard 2 --continuous >> .cache/substack_shards/2026-09-28-team-v1/shard-2/collection.log 2>&1 &
```

This continues while the machine is running. A shell window or agent conversation is not a substitute for checking actual process and checkpoint progress. Do not launch a second copy because the first is quiet. `collection_status.json` updates at publication boundaries; one long history can take time.

Request a graceful stop after the current publication:

```sh
touch .cache/substack_shards/2026-09-28-team-v1/shard-2/STOP
```

Ctrl-C or SIGTERM also requests a stop at the publication boundary. Remove only that `STOP` marker when deliberately resuming; keep the database and assignment binding. Then repeat the same run command. Earlier successes and failures are skipped automatically.

The initial team setting is **one worker per person, at least six seconds between request starts per worker**. Three active workers average at most about 0.5 requests/second in steady state; their timing does not enforce a strict global minimum gap. Stagger starts, honor `Retry-After`, and report rate-limit events to the team. This setting does not guarantee a threefold speedup or establish a platform rate entitlement.

HTTP 401/403/429 failures or an open rate-limit circuit create `PAUSED.json` and stop the shard. Diagnose the cause and honor the required cooldown before deliberately removing that marker. Do not bypass challenges, repeatedly restart a blocked route, or increase concurrency. Ordinary parsing errors remain recorded as failures; they are not silently retried.

| Exit | Meaning |
|---:|---|
| 0 | Assignment attempted completely, without recorded failures |
| 2 | Assignment attempted completely, with recorded failures |
| 3 | Access/rate pause requiring inspection |
| 4 | Normal bounded chunk finished; pending URLs remain |
| 5 | Intentional stop; pending URLs remain |

A traceback also requires diagnosis. An abnormal exit may leave a stale status file; the committed SQLite rows determine durable progress. Do not run the legacy full-frame continuation or build/publish scripts for a shard. The original baseline has a retirement marker, and its old continuation automation was replaced for the handoff.

## Export and return through GitHub

Stop the worker before export so the exporter can acquire its lock. Use a new output directory for every export:

```sh
python3 scripts/substack/team_collection.py export --batch data/substack_assignments/2026-09-28-team-v1 --shard 2 --output .cache/substack_exports/2026-09-28-team-v1-shard-2-final
```

The exporter writes the actual checkpoint records, including payload text, failures, and observation times, into ordered compressed parts plus a checksum manifest and latest collection status. Each compressed part represents at most 8 MiB of uncompressed data. A JSON line can span parts; use the merge tool, not an ad hoc CSV parser. The manifest explicitly flags a partial export if pending URLs remain. Keep the original checkpoint and log.

Before returning the final export, run `python3 scripts/substack/check_substack_compatibility.py --shard 2 --export-dir .cache/substack_exports/2026-09-28-team-v1-shard-2-final --report .cache/substack_compatibility/shard-2-final.json`. Include that report in the delivery. A complete delivery requires `export_validation.status = PASS` and zero pending publications; recorded failures remain separately reported. See the compatibility guide for interim checks.

Return these files as assets on a **draft release in the team's repository**, with one release tag per shard. This avoids putting the changing database into ordinary Git history. The release is a data-transfer artifact, not a public dataset publication. Confirm the repository remains private to the team before uploading retained text. Using the GitHub CLI, when installed and authenticated to this repository:

```sh
gh release create substack-2026-09-28-team-v1-shard-2 --repo mbadu1/cap-folder --draft --target zherui-substack-month-pilot --title "Substack team v1 shard 2 results" --notes "Internal research handoff. Inspect manifest for completeness and checksums."
gh release upload substack-2026-09-28-team-v1-shard-2 .cache/substack_exports/2026-09-28-team-v1-shard-2-final/* --repo mbadu1/cap-folder
```

The same draft release and asset upload can be created through GitHub's website. For a large number of files, upload in bounded batches. Do not overwrite earlier assets with a different export; use a new versioned tag. Report the draft release location and assigned/success/error/pending counts to Zherui. A progress report alone does not transfer the collected data. GitHub release permissions are separate from the ability to clone; if upload permission is unavailable, report that exact missing capability while keeping the complete local export.

## Combine and audit (Zherui)

Keep the original baseline and shard exports unchanged. Download all three complete exports. Merge into a new database:

```sh
python3 scripts/substack/team_collection.py merge --batch data/substack_assignments/2026-09-28-team-v1 --baseline-db .cache/substack_history/baseline_team_2026-09-28/crawl.sqlite3 --exports .cache/substack_exports/shard-1 .cache/substack_exports/shard-2 .cache/substack_exports/shard-3 --output .cache/substack_history/merged_team_v1/crawl.sqlite3
```

Use the actual export locations. The merger checks the frozen baseline inventory, shard identity, code and assignment hashes, part checksums, payload IDs/dates, duplicate keys, and full coverage. It preserves baseline failures and out-of-frame baseline rows. A failed merge leaves a clearly named partial database for inspection; it never overwrites a completed database.

After successful reconciliation, run the full creator/month/topic audit once, against the combined checkpoint and original frame:

```sh
python3 scripts/substack/audit_substack_creator_frame.py --frame-size 242052 --frame-file data/substack_assignments/2026-09-28-team-v1/source_frame.csv --history-cache-root .cache/substack_history/merged_team_v1 --output-dir data/monthly_full/frame_audit_team_v1
```

Creator and coauthor IDs must be resolved globally. The up-to-2,500 creators/month, eight topics, 150/topic floor plus proportional remainder, and one-to-three-post creator-month rules apply to the combined eligible frame. Do not sample each shard independently. Final full-text eligibility, topic/identity review, and monthly publication gates still apply.

## Paste to either teammate's AI agent

Set `MY_SHARD` to 2 or 3 and supply the exact handoff commit SHA shown by Zherui.

```text
Work in the mbadu1/cap-folder GitHub repository.
MY_SHARD = 2
HANDOFF_COMMIT = [the exact shared commit SHA]
BATCH_ID = 2026-09-28-team-v1

Read docs/SUBSTACK_TEAM_SCRAPING_PLAN.md at HANDOFF_COMMIT and docs/SUBSTACK_COMPATIBILITY.md. All starting inputs are in this checkout. Run python3 scripts/substack/check_substack_compatibility.py --shard MY_SHARD and require PASS, then run assignment preflight and the five-publication pilot for MY_SHARD. Preserve that checkpoint and continue the same assignment using scripts/substack/team_collection.py. Collect only the assigned publication URLs for 2020-01-01 through 2026-09-17 with the shared parser, one worker, and the documented six-second per-worker interval.

Use the dedicated cache, preserve completed/error rows, and verify actual process/checkpoint progress. Diagnose abnormal exits and persistent PAUSED.json before resuming; honor STOP, Retry-After, access restrictions, and rate stops. Do not discover a new frame, run the full-frame scripts, change the parser independently, or collect another shard.

Track assigned/success/error/pending counts. When finished, stop cleanly, export the actual records with checksum manifest, and return the package through the documented private draft GitHub release. Report the package location, counts, and unresolved failures. Keep the source checkpoint. If a required file, revision, or GitHub upload permission is missing, identify it precisely instead of guessing or discarding data.
```
