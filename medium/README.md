# Medium scraping

Read the [team plan](docs/TEAM_SCRAPING_PLAN.md), [DCC runbook](docs/DCC_RUNBOOK.md) and [Ziyang starter prompt](docs/ZIYANG_START_PROMPT.md).

| Folder | Contents |
| --- | --- |
| scripts/ | Existing parser/collector, frozen-list builder, parallel shard runner, DCC wrapper, status checker, private exporter and merge utility |
| docs/ | Ownership, execution, pacing, checkpoint handoff, tests and teammate prompt |
| tests/ | Offline parser, identity, retention, scope, transport and export/merge tests |
| assignments/2026-10-03-team-v1/ | Frozen public profile worklists, hashes and snapshot documentation |
| handoff/ | Metadata-only preparation snapshot and receipt; refresh after stopping the old owner before live use |
| requirements.txt | Pinned Python dependencies |

From the repository root, run `python3 -m venv .venv`, `.venv/bin/python -m pip install -r medium/requirements.txt`, then `.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'`. Preparation and tests make no collection requests. Use Python 3.10+ on Linux/macOS/WSL. Durable scraped data belongs in private `.cache/` checkpoints, outside Git.

The study window is 2020-01-01 through 2026-09-17. The shared target is 250,000 dated RSS author candidates, with unresolved identity/history limitations. Twenty threads share a minimum **3.1 seconds between starts**. Only one team shard can run at a time, including the previous local collector. A real DCC pilot is required before sustained collection.
