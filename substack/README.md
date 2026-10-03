# Substack scraping

Scripts, documentation, tests and frozen assignments live together here. The collector implementations and frozen assignment bytes are preserved during the 2026-10-03 organization change; compatibility symlinks retain existing repository-root commands and cache paths.

Read these documents in order:

1. [Team scraping plan](docs/SUBSTACK_TEAM_SCRAPING_PLAN.md).
2. [DCC 20-worker plan](docs/SUBSTACK_DCC_20_WORKER_PLAN.md).
3. [Compatibility guide](docs/SUBSTACK_COMPATIBILITY.md).
4. [Parallel runbook](docs/SUBSTACK_PARALLEL_RUNBOOK.md).

The active batch is `assignments/2026-09-28-team-v2/`; Zherui owns shard 1 and Ziyang shard 2. Use the pinned code for any existing bound checkpoint. Preserve pause, retirement and stop markers; the folder organization does not authorize restarting previous runs.

From the repository root:

```sh
python3 -m unittest discover -s substack/tests -p 'test_*.py'
python3 substack/scripts/check_substack_compatibility.py --shard 2
```

[Cached sampling](docs/SUBSTACK_CACHED_SAMPLING.md) and [sampled restacks](docs/SUBSTACK_SAMPLED_RESTACKS.md) are separate follow-on workflows. Durable `.cache/` data, corpus exports and logs stay outside Git.
