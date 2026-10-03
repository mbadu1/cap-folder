# Capstone scraping tools

Platform workflows are organized in two folders:

| Folder | Contents |
| --- | --- |
| [substack/](substack/README.md) | Substack scripts, docs, tests, fixtures and frozen assignments |
| [medium/](medium/README.md) | Medium scripts, docs, tests, dependencies, frozen assignments and metadata handoff |
| scripts/shared/ | Older combined-platform toy scraper and monthly-schema migration |

For Medium's new 20-thread DCC handoff, start with the [team plan](medium/docs/TEAM_SCRAPING_PLAN.md), [runbook](medium/docs/DCC_RUNBOOK.md) and [Ziyang prompt](medium/docs/ZIYANG_START_PROMPT.md). Zherui owns shard 1; Ziyang owns shard 2. Each has 96,160 primary profiles and 25,000 reserves. Twenty threads share a 1.5-second global request-start gate plus six seconds per worker. Coordinate one exclusive Medium run across the team and refresh the baseline ledger after stopping the old collector before live use.

For Substack's existing workflow, read the [team plan](substack/docs/SUBSTACK_TEAM_SCRAPING_PLAN.md), [compatibility guide](substack/docs/SUBSTACK_COMPATIBILITY.md) and [parallel runbook](substack/docs/SUBSTACK_PARALLEL_RUNBOOK.md). Active batch: `2026-09-28-team-v2`; Zherui has 74,127 publications and Ziyang 74,126. Existing pinned collector code and frozen files are preserved.

Use Python 3.10+ (tested locally with 3.12) on macOS/Linux/WSL. From this repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r medium/requirements.txt
.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'
.venv/bin/python -m unittest discover -s substack/tests -p 'test_*.py'
```

All behavior checks use offline fixtures. One optional Medium artifact check skips when its private toy corpus is absent. GitHub Actions runs these checks; local PASS does not assert a completed remote CI run or live DCC feasibility.

Compatibility symlinks retain old `scripts/substack`, `scripts/medium`, `docs/SUBSTACK_*`, `tests/test_*`, `tests/fixtures`, `data/substack_assignments`, `data/medium_assignments` and `requirements-medium.txt` paths. Run old commands from the repository root. Keep the exact pinned revision for any existing bound checkpoint; moving folders does not authorize rebinding or restarting it.

`.cache/` contains durable collection data and resume checkpoints. Preserve it. Keep raw responses, article bodies, databases, secrets and logs outside Git. The checked-in assignment and handoff files contain public profile/publication metadata only. Transfer hash-verified corpus exports privately and preserve incomplete coverage/failure flags.

The published handoff branch is `zherui-substack-month-pilot`; updates do not merge or overwrite `main`. Pulling this repository installs no monitoring schedule and starts no scraper.
