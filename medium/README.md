# Medium scraping

> **2026-10-03 user decision:** Zherui shard 1 and Ziyang shard 2 may scrape concurrently in separate accounts/caches. No exclusive window, stopped-owner receipt or wait for Zherui is required. Keep Zherui’s existing collector and monitor running. See `medium/handoff/CURRENT_WINDOW.json`.

Read the [team plan](docs/TEAM_SCRAPING_PLAN.md), [DCC runbook](docs/DCC_RUNBOOK.md) and [Ziyang starter prompt](docs/ZIYANG_START_PROMPT.md).

Both owners are authorized to run concurrently. The [current parallel authorization](handoff/PARALLEL_AUTHORIZATION.json) identifies the metadata ledger and source pins for Ziyang's fresh cache. The [historical handoff](handoff/2026-10-03-ziyang-shard-2/README.md) preserves the ledger's provenance.

Before sustained scraping, use the [staged validation plan](docs/DCC_VALIDATION_PLAN.md): fixed local replay, 1/10/20-worker trials, baseline comparison and replay of identical DCC responses.

| Folder | Contents |
| --- | --- |
| scripts/ | Existing parser/collector, frozen-list builder, parallel shard runner, DCC wrapper, status checker, private exporter and merge utility |
| docs/ | Ownership, execution, pacing, checkpoint handoff, tests and teammate prompt |
| tests/ | Offline parser, identity, retention, scope, transport and export/merge tests |
| assignments/2026-10-03-team-v1/ | Frozen public profile worklists, hashes and snapshot documentation |
| handoff/ | Current parallel authorization, metadata ledger, hash receipts and historical handoff evidence |
| validation/ | Frozen 40-feed sample, replay manifest and expected public metadata/text hashes; article bodies and caches stay private |
| requirements.txt | Pinned Python dependencies |

From the repository root, run `python3 -m venv .venv`, `.venv/bin/python -m pip install -r medium/requirements.txt`, then `.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'`. Preparation and tests make no collection requests. Use Python 3.10+ on Linux/macOS/WSL. Durable scraped data belongs in private `.cache/` checkpoints, outside Git.

The study window is 2020-01-01 through 2026-09-17. The shared target is 250,000 dated RSS author candidates, with unresolved identity/history limitations. Twenty threads share a minimum **1.5 seconds between starts across all workers, plus 6 seconds per worker**. Both assigned shards can run simultaneously in separate caches/accounts. Timers, cooldowns and baseline-plus-local candidate counters are independent; reconcile the final unique team count when merging both exports. The approximately 250,000 acquisition target is not enforced by a distributed counter. The v3 HTTP policy records/skips ordinary errors and automatically retries 429s after a shared cooldown of at least 60 seconds. Use the packaged ten-minute [DCC monitor](docs/MONITORING.md) and verify your own notification schedule before unattended collection. A real assigned-shard DCC pilot is required before sustained collection.
