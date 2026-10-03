# Medium scraping

Read the [team plan](docs/TEAM_SCRAPING_PLAN.md), [DCC runbook](docs/DCC_RUNBOOK.md) and [Ziyang starter prompt](docs/ZIYANG_START_PROMPT.md).

Before sustained scraping, use the [staged validation plan](docs/DCC_VALIDATION_PLAN.md): fixed local replay, 1/10/20-worker trials, baseline comparison and replay of identical DCC responses.

| Folder | Contents |
| --- | --- |
| scripts/ | Existing parser/collector, frozen-list builder, parallel shard runner, DCC wrapper, status checker, private exporter and merge utility |
| docs/ | Ownership, execution, pacing, checkpoint handoff, tests and teammate prompt |
| tests/ | Offline parser, identity, retention, scope, transport and export/merge tests |
| assignments/2026-10-03-team-v1/ | Frozen public profile worklists, hashes and snapshot documentation |
| handoff/ | Metadata-only preparation snapshot and receipt; refresh after stopping the old owner before live use |
| validation/ | Frozen 40-feed sample, replay manifest and expected public metadata/text hashes; article bodies and caches stay private |
| requirements.txt | Pinned Python dependencies |

From the repository root, run `python3 -m venv .venv`, `.venv/bin/python -m pip install -r medium/requirements.txt`, then `.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'`. Preparation and tests make no collection requests. Use Python 3.10+ on Linux/macOS/WSL. Durable scraped data belongs in private `.cache/` checkpoints, outside Git.

The study window is 2020-01-01 through 2026-09-17. The shared target is 250,000 dated RSS author candidates, with unresolved identity/history limitations. Twenty threads share a minimum **1.5 seconds between starts across all workers, plus 6 seconds per worker**. Only one team shard can run at a time, including the previous local collector. The v3 HTTP policy records/skips ordinary errors and automatically retries 429s after a shared cooldown of at least 60 seconds. Use the packaged ten-minute [DCC monitor](docs/MONITORING.md) and verify your own notification schedule before unattended collection. A real assigned-shard DCC pilot is required before sustained collection.
