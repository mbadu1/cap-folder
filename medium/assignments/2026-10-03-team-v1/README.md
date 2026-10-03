# Medium candidate worklists — 2026-10-03 team v1

These frozen local files prepare division between Zherui and Ziyang, using the same two owners as Substack. They contain profile/feed URLs and priorities, not article text. No additional worker was launched, and the active collector has not been bound to these planning files.

## Frame and assignments

| File | Rows | Meaning |
| --- | ---: | --- |
| source_frame.csv | 250,000 | 57,680 existing in-window RSS author candidates plus 192,320 unattempted candidate profiles |
| already_collected.csv | 57,680 | One representative URL per known RSS candidate identity; feeds already collected; available story tasks may still be pending |
| known_aliases.csv | see manifest/verification | All known pool profile-to-candidate-ID mappings; aliases must stay with their canonical owner |
| shard-1.csv | 96,160 | Zherui's primary candidate feed screening list |
| shard-2.csv | 96,160 | Ziyang's primary candidate feed screening list |
| shard-1-reserve.csv | 25,000 | Zherui's ordered replacement candidates |
| shard-2-reserve.csv | 25,000 | Ziyang's ordered replacement candidates |

The source snapshot ended at request 104,893. Selection follows the existing SHA-256 feed priority, with primary and reserve candidates alternated between owners. Existing RSS candidate IDs are deduplicated, with profile-URL fallback when missing. Candidate profiles whose feeds are unattempted have unresolved identity keys. Primary and reserve lists are disjoint by profile URL. A discovered alias can still cross shards; reconcile its RSS candidate ID before treating it as another author.

The target remains roughly 250,000 collected author candidates with at least one dated story in 2020-01-01 through 2026-09-17. A list of 250,000 candidate rows cannot guarantee that number of qualifying authors: failed, empty, duplicate and out-of-window feeds need replacements. Reserves are planning capacity, not guaranteed yield or permission to retry failures. RSS truncation and unresolved historical coverage remain; these lists are separate from the later monthly statistical draw.

## Before execution

1. Reconcile every assigned profile and its feed/story checkpoint against collection since this snapshot. Preserve successes and recorded failures; do not refetch completed feeds or automatically retry failed items.
2. Bind a shard-aware runner to the file hashes in manifest.json. The current `collect_medium_history.py` has no shard-input option and must not be independently launched for both lists.
3. Keep one worker unless a shared aggregate request gate is implemented and verified. Two independent machines with separate 3.1-second timers would exceed the existing global pacing. Do not move work to DCC merely because Substack ran there.
4. Limit mirror tasks to in-window stories from assigned feeds, reconcile story IDs/author aliases, and preserve the shared 250,000 qualifying-author target and reserve accounting. Keep access blocks, cooldowns, public/free retention and the 20 GiB free-space reserve.
5. Freeze a versioned handoff after reconciliation; do not mutate these v1 files or silently add/reassign profiles. Final merges must deduplicate candidate identities and story IDs, preserving source versions and failures.

## Reproduction and verification

The builder is `cap-folder/scripts/medium/freeze_medium_assignments.py`. It reads SQLite in a single read-only snapshot, preserves the live checkpoint, and refuses to overwrite an existing assignment directory. Future reruns on a changed checkpoint produce a new frame and must use a new directory. The manifest pins the builder and every CSV hash; verification.json independently recounts the files and checks their partition union and absence of overlaps. The original snapshot lists remain the reproducible assignment record.

```
.venv/bin/python cap-folder/scripts/medium/freeze_medium_assignments.py --checkpoint cap-folder/.cache/medium_history/history_v1/crawl.sqlite3 --output cap-folder/data/medium_assignments/NEW-BATCH --target 250000 --reserve 50000
```
