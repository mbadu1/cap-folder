# Medium parallel scraping authorization — 2026-10-03

**2026-10-04 scope:** Collect only the 96,160 primary profiles per owner. The 25,000 reserves per shard are deferred until the user reviews sampling problems and explicitly reauthorizes them. Preserve the frozen reserve files and any prior results; do not auto-activate them.


The user explicitly authorizes Zherui shard 1 and Ziyang shard 2 to run concurrently. Read [CURRENT_WINDOW.json](CURRENT_WINDOW.json) and [PARALLEL_AUTHORIZATION.json](PARALLEL_AUTHORIZATION.json). No exclusive window, stopped-owner receipt or additional release is required. Keep Zherui's active scraper and monitoring running.

Ziyang uses the hash-checked metadata ledger and receipt under [2026-10-03-ziyang-shard-2](2026-10-03-ziyang-shard-2/README.md). It has 60,642 known candidate keys, 102,298 completed/failed tasks and merged watermark 109,911. The bytes and original provenance remain unchanged; historical exclusivity/withdrawal rules are superseded by the current parallel authorization. Earlier preparation metadata remains historical.

Each collector retains its own pacing, cooldown, counter and cache lock. The ledger is a snapshot; it does not track subsequent shard-1 progress. Reconcile aliases/story IDs and unique team candidate counts from private completed exports. Use the [current baseline validation bundle](../validation/2026-10-03-baseline-40-parallel/README.md) and [full Ziyang prompt](../docs/ZIYANG_START_PROMPT.md). Raw responses, article bodies, databases and private logs stay outside Git.
