# Released Medium window — Ziyang shard 2

The user explicitly authorized transferring the exclusive Medium scraping window to **Ziyang Qin, shard 2**, for frozen batch `2026-10-03-team-v1`. `release.json` and `../CURRENT_WINDOW.json` record the current reservation. Verify them against the latest branch before live work.

Zherui's local legacy collector and DCC shard-1 collector/watcher have been deliberately stopped, their process/lock state verified, and their STOP/MONITOR_STOP evidence preserved. The stopped shard-1 export was hash checked and merged into a **new** baseline checkpoint; the original caches are unchanged. `final-ledger.json.gz` is reconciled from that merged state. Its receipt and `stop-verification.json` are pinned in the release. Only public task metadata, candidate keys and operational receipts are shipped; article bodies, raw responses, databases and private logs stay outside Git.

The window covers Ziyang's staged 1/10/20-worker validation, assigned 40-attempt pilot and continuous shard-2 run after the required checks. It has **no automatic expiry**. Zherui must remain stopped; another owner requires an explicit later release after Ziyang's collector exits and its output is reconciled. Compute allocation expiry does not transfer ownership. The ongoing stopped-baseline data transfer makes no Medium requests and can continue separately. Zherui's automation monitors only that transfer and forbids collector restart.

Use the full [startup prompt](../../docs/ZIYANG_START_PROMPT.md). All baseline-comparison metadata are already shipped in the [v3 validation directory](../../validation/2026-10-03-baseline-40-v3/README.md). Personal authenticated GitHub/DCC access is still required.

## Verify the published release

Run from the repository root with the prepared Python environment:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib, json
root = Path('.')
folder = root / 'medium/handoff/2026-10-03-ziyang-shard-2'
release = json.loads((folder / 'release.json').read_text())
current = json.loads((root / 'medium/handoff/CURRENT_WINDOW.json').read_text())
assert release['status'] == current['status'] == 'RELEASED'
assert release['owner'] == current['owner'] == 'Ziyang'
assert release['shard'] == current['shard'] == 2
assert current['release_path'] == str(folder / 'release.json')
assert hashlib.sha256((folder / 'release.json').read_bytes()).hexdigest() == current['release_sha256']
for name, expected in release['files'].items():
    assert Path(name).name == name
    assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected, name
for name, expected in release['source_hashes'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
receipt = json.loads((folder / 'final-ledger.json.gz.receipt.json').read_text())
assert receipt['sha256'] == release['files']['final-ledger.json.gz']
assert receipt['batch_manifest_sha256'] == release['batch_manifest_sha256']
assert hashlib.sha256((root / 'medium/assignments/2026-10-03-team-v1/manifest.json').read_bytes()).hexdigest() == release['batch_manifest_sha256']
print('PASS: released shard-2 window, ledger, receipt, stop evidence and source pins')
PY
```

Verify the latest remote branch has not replaced `CURRENT_WINDOW.json` before claiming the window; do not silently change a bound execution checkout. Then set:

```sh
task_ledger=medium/handoff/2026-10-03-ziyang-shard-2/final-ledger.json.gz
task_cache=.cache/medium_shards/2026-10-03-team-v1/shard-2-v3-released
.venv/bin/python medium/scripts/parallel_medium_collection.py prepare \
  --shard 2 --reconciliation "$task_ledger" --cache-root "$task_cache"
```

Preparation makes no requests. Use a fresh private cache; do not rebind a previous preparation cache. Complete the [staged validation](../../docs/DCC_VALIDATION_PLAN.md), pilot and verified [monitoring](../../docs/MONITORING.md) before unattended production. Keep the 1.5-second shared/six-second worker timers, ordinary-error skip/continue and shared minimum 60-second 429 cooldown/retry. Preserve inherited failures, route blocks and cooldown state.

The ledger's generic snapshot note is unchanged; this release adds the actual stopped-owner and merged-state evidence it requires. Acquisition remains partial, and neither a released window nor the candidate count establishes complete histories or final sample eligibility.
