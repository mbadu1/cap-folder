# Two-person Substack scraping handoff

Prepared 2026-09-28. Zherui and Ziyang perform the same task with the same parser and different publication URL lists. Everything needed to start is in this repository on branch `zherui-substack-month-pilot`. Record the shared handoff commit before collection; the launcher also verifies the exact collector file hashes.

**2026-09-28 DCC amendment:** The team now targets **20 parallel workers per shard** on DCC, starting with Ziyang's shard 2. Ziyang uses Codex **locally** to build and run the project remotely through his DCC compute allocation. Read the [DCC implementation and exact-alignment test plan](SUBSTACK_DCC_20_WORKER_PLAN.md) and [compatibility guide](SUBSTACK_COMPATIBILITY.md#additional-exact-alignment-gate-for-the-planned-dcc-parallel-collector) before using this handoff. The commands below are the still-runnable **single-worker v2** path; they do not implement the 20-worker target. The new parallel collector requires a reviewed, separately pinned batch/release, exact same-input output comparison against the local collector, and a bounded live rescrape of the 21 successful local shard-1 histories in a disposable test cache. The production-like pilot and aggregate request gate remain required. Do not launch 20 copies of the v2 command or treat the one-minute rate probe as sustained-rate approval. Zherui's shard-1 `PAUSED.json` remains in force.

## Assignments

Batch: `2026-09-28-team-v2`, frozen at 2026-09-29T02:38:08Z.

| Person | Shard argument | Publications |
|---|---|---:|
| Zherui | `--shard 1` | 74,127 |
| Ziyang | `--shard 2` | 74,126 |

The original frozen frame has 242,052 publications. The preserved baseline has 93,799 in-frame committed results: 92,452 successes and 1,347 errors. An additional out-of-frame error stays in the private baseline. The 148,253 assigned publications retain the old shard-1 and shard-2 ownership. Former shard 3 is sorted by existing hash priority and split alternately between Zherui and Ziyang. The two lists are disjoint and exclude every baseline result. Michael has no active assignment. Ziyang has not started the old batch. Zherui’s 22 recorded attempts (21 successes, one failure) were migrated without re-fetching, leaving 74,105 pending in shard 1 at cutover. Counts balance publications, not history length or runtime.

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

Run the [compatibility check](SUBSTACK_COMPATIBILITY.md) before the live pilot: `python3 scripts/substack/check_substack_compatibility.py --shard 2`. Require `PASS`; share the JSON report with Zherui. This release changes assignment and orchestration fingerprints for the new two-person batch. The article parser, fixed expected output, and six-second pacing are unchanged. Use the new batch and its dedicated cache; never attach an old cache directly.

The rest of the commands show **Ziyang / shard 2**. Zherui changes the shard argument and shard-specific paths from `2` to `1`. Do not change the batch ID, frame files, scraper logic, dates, or hashes. No one needs Zherui's original database or outer project workspace.

```sh
python3 scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --check
```

This makes no network requests. It verifies all frame/assignment files, checksums, collector code, nonoverlap, coverage, and the checkpoint binding if one exists. Missing input causes an error; it cannot silently fetch a new frame.

## Current v2 single-worker pilot, run, and resume

These commands document the existing v2 checkpoint protocol. Ziyang's requested 20-worker DCC route is in the [DCC plan](SUBSTACK_DCC_20_WORKER_PLAN.md); use its new batch ID, release commit, commands, and cache only after that implementation passes its gates. Do not start this v2 run and a new parallel run against the same assignment at the same time.

Start with five new publication histories:

```sh
python3 -u scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --limit 5
```

Review the status, then continue with the same cache:

```sh
python3 scripts/substack/team_collection.py status --batch data/substack_assignments/2026-09-28-team-v2 --shard 2
python3 -u scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --continuous
```

The default cache is `.cache/substack_shards/2026-09-28-team-v2/shard-2/`. It contains the actual data; never delete it to resume. The worker checkpoints every finished publication, preserves failures, reports progress, and refuses another process using the same checkpoint. It uses the existing `parse_publication_history()` and post parser, with no separately rewritten scraper. A bounded run without `--continuous` defaults to at most 500 new publications.

Keep the machine awake and retain the process output as a log. On macOS/Linux, a durable background launch after the pilot can use:

```sh
nohup python3 -u scripts/substack/team_collection.py run --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --continuous >> .cache/substack_shards/2026-09-28-team-v2/shard-2/collection.log 2>&1 &
```

This continues while the machine is running. A shell window or agent conversation is not a substitute for checking actual process and checkpoint progress. Do not launch a second copy because the first is quiet. `collection_status.json` updates at publication boundaries; one long history can take time.

Request a graceful stop after the current publication:

```sh
touch .cache/substack_shards/2026-09-28-team-v2/shard-2/STOP
```

Ctrl-C or SIGTERM also requests a stop at the publication boundary. Remove only that `STOP` marker when deliberately resuming; keep the database and assignment binding. Then repeat the same run command. Earlier successes and failures are skipped automatically.

The initial team setting is **one worker per person, at least six seconds between request starts per worker**. Two active workers average at most about 0.33 requests/second in steady state; their timing does not enforce a strict global minimum gap. Stagger starts, honor `Retry-After`, and report rate-limit events to the team. This setting does not guarantee a twofold speedup or establish a platform rate entitlement.

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

The commands and release tag in this section apply to a v2 single-worker export. For the parallel release, substitute the **actual new batch ID and its bound cache/export paths throughout**, and update the compatibility checker for that release before labeling its export complete. Never combine a v2 shard export with a new-batch shard export.

Stop the worker before export so the exporter can acquire its lock. Use a new output directory for every export:

```sh
python3 scripts/substack/team_collection.py export --batch data/substack_assignments/2026-09-28-team-v2 --shard 2 --output .cache/substack_exports/2026-09-28-team-v2-shard-2-final
```

The exporter writes the actual checkpoint records, including payload text, failures, and observation times, into ordered compressed parts plus a checksum manifest and latest collection status. Each compressed part represents at most 8 MiB of uncompressed data. A JSON line can span parts; use the merge tool, not an ad hoc CSV parser. The manifest explicitly flags a partial export if pending URLs remain. Keep the original checkpoint and log.

Before returning the final export, run `python3 scripts/substack/check_substack_compatibility.py --shard 2 --export-dir .cache/substack_exports/2026-09-28-team-v2-shard-2-final --report .cache/substack_compatibility/2026-09-28-team-v2/shard-2-final.json`. Include that report in the delivery. A complete delivery requires `export_validation.status = PASS` and zero pending publications; recorded failures remain separately reported. See the compatibility guide for interim checks.

Return these files as assets on a **draft release in the team's repository**, with one release tag per shard. This avoids putting the changing database into ordinary Git history. The release is a data-transfer artifact, not a public dataset publication. Confirm the repository remains private to the team before uploading retained text. Using the GitHub CLI, when installed and authenticated to this repository:

```sh
gh release create substack-2026-09-28-team-v2-shard-2 --repo mbadu1/cap-folder --draft --target zherui-substack-month-pilot --title "Substack team v2 shard 2 results" --notes "Internal research handoff. Inspect manifest for completeness and checksums."
gh release upload substack-2026-09-28-team-v2-shard-2 .cache/substack_exports/2026-09-28-team-v2-shard-2-final/* --repo mbadu1/cap-folder
```

The same draft release and asset upload can be created through GitHub's website. For a large number of files, upload in bounded batches. Do not overwrite earlier assets with a different export; use a new versioned tag. Report the draft release location and assigned/success/error/pending counts to Zherui. A progress report alone does not transfer the collected data. GitHub release permissions are separate from the ability to clone; if upload permission is unavailable, report that exact missing capability while keeping the complete local export.

## Combine and audit (Zherui)

The following paths are for v2. After the parallel release, use its actual batch and two same-release exports with the preserved original baseline; do not mix batch versions.

Keep the original baseline and shard exports unchanged. Download both complete exports for batch `2026-09-28-team-v2`. Old-batch exports are not valid inputs to this merge. Merge into a new database:

```sh
python3 scripts/substack/team_collection.py merge --batch data/substack_assignments/2026-09-28-team-v2 --baseline-db .cache/substack_history/baseline_team_2026-09-28/crawl.sqlite3 --exports .cache/substack_exports/shard-1 .cache/substack_exports/shard-2 --output .cache/substack_history/merged_team_v2/crawl.sqlite3
```

Use the actual export locations. The merger checks the frozen baseline inventory, shard identity, code and assignment hashes, part checksums, payload IDs/dates, duplicate keys, and full coverage. It preserves baseline failures and out-of-frame baseline rows. A failed merge leaves a clearly named partial database for inspection; it never overwrites a completed database.

After successful reconciliation, run the full creator/month/topic audit once, against the combined checkpoint and original frame:

```sh
python3 scripts/substack/audit_substack_creator_frame.py --frame-size 242052 --frame-file data/substack_assignments/2026-09-28-team-v2/source_frame.csv --history-cache-root .cache/substack_history/merged_team_v2 --output-dir data/monthly_full/frame_audit_team_v2
```

Creator and coauthor IDs must be resolved globally. The up-to-2,500 creators/month, eight topics, 150/topic floor plus proportional remainder, and one-to-three-post creator-month rules apply to the combined eligible frame. Do not sample each shard independently. Final full-text eligibility, topic/identity review, and monthly publication gates still apply.

## Paste to Ziyang's AI agent

Use the current [Ziyang Codex prompt](SUBSTACK_DCC_20_WORKER_PLAN.md#handoff-for-ziyangs-codex). Supply the exact reviewed parallel-release commit when it exists. The former prompt for one worker and the v2 batch is superseded for Ziyang's requested DCC route.
