# Capstone collection tools

Start with [the Substack team handoff](docs/SUBSTACK_TEAM_SCRAPING_PLAN.md). The three workers use one collector version with disjoint publication assignments. Run commands from this repository root, not its parent directory.

Before scraping, run the [one-command compatibility check](docs/SUBSTACK_COMPATIBILITY.md): `python3 scripts/substack/check_substack_compatibility.py --shard 2` (Ziyang) or `--shard 3` (Michael). It checks your installation against fixed expected outputs and writes a shareable PASS/FAIL report. The same command can validate your actual exported records before delivery.

## Scripts

| Folder | Contents |
|---|---|
| `scripts/substack/` | History collection, three-person assignment/run/export/merge tooling, creator audits, monthly builders, publication helpers |
| `scripts/medium/` | Historical collector, mirror adapter, access probes, diagnostic toy and source audits |
| `scripts/shared/` | Older combined-platform toy scraper and monthly-schema migration utility |

The Substack history and team collection tools use the Python standard library. Use Python 3.10 or newer; the handoff was tested with Python 3.12. The Medium collector supports macOS/Linux (or WSL) and uses the dependencies below:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-medium.txt
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Substack-only offline tests require no package installation:

```sh
python3 -m unittest discover -s tests -p 'test_substack*.py'
```

One optional Medium delivered-artifact test is skipped when its separately retained toy corpus is absent. All behavioral/unit tests use synthetic or public metadata fixtures. `.cache/` contains durable acquired data and resume checkpoints; do not delete it to restart a run.

## Medium

The collector can initialize a fresh checkpoint from the checksum-verified discovery index in `data/seeds/medium/2026-09-24/`; Zherui's private pilot cache is optional. Existing initialized checkpoints retain their original discovery state. An initialization-only command makes no network requests:

```sh
.venv/bin/python scripts/medium/collect_medium_history.py --init-only --cache-root .cache/medium_init_check --output .cache/medium_init_check/report
```

The existing Medium production worker is a separate project run. Do not launch a duplicate Medium crawl as part of the Substack team handoff. Its updated entry point is `scripts/medium/collect_medium_history.py`. Diagnostic scripts named `probe_*` may make network requests when invoked; inspect their CLI and the project's access decision before running them. The two `.mjs` package-audit scripts accept explicit package/dependency directories and are optional diagnostics, not dependencies of either production collector. The Medium toy builder requires its documented cached pilot sources, which contain retained article text and are kept separately from the code repository.

## Data and delivery

The checked-in Substack batch contains publication URLs, priority hashes, and baseline success/error inventories. It contains no previous article bodies or original SQLite database. Teammates can start from this checkout alone. Return compressed exports with manifests using the delivery commands in the handoff guide; final sampling is performed after all shards and the original baseline are combined.

The current published branch is maintained by Zherui. Changes here do not merge or overwrite `main`. Each worker should pin the shared handoff commit before running, and should coordinate any subsequent code update across the team.
