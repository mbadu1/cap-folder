# Preparation reconciliation — 2026-10-03

`2026-10-03-preparation.json.gz` contains public profile/story task metadata and RSS candidate keys only, with no article bodies, raw responses, database, credentials or access tokens. Its companion receipt pins SHA-256 `6cf39af86f796f3554359b96425edee89a3edd382fbb84e501d3c05e9ef660c3`.

The read-only snapshot ended at request **106,092**, with **58,385 known in-window RSS candidate keys** and **98,479 completed/failed feed/mirror task records**. It preserves existing direct-profile/GraphQL access blocks. It was generated while the original local Medium collector was still running. It is an **offline preparation input**, not a final stopped-baseline handoff or evidence that another owner can safely start live requests.

Before Ziyang's pilot, stop the prior Medium collector deliberately, wait for its report and lock release, refresh a new ledger and coordinate an exclusive team window as described in the [runbook](../docs/DCC_RUNBOOK.md). Use a fresh live cache for that new ledger; preserve the preparation cache and original source store. The frozen assignment manifest remains the original planning snapshot. Later ledgers and private corpus exports do not need to be pushed to GitHub.

## Self-service handoff requirements

The v3 baseline comparison no longer requires a private reference transfer: use `medium/validation/2026-10-03-baseline-40-v3/reference-metadata.json.gz`. All setup inputs are available in the clone. Live input must additionally include a newly published metadata-only ledger and hash receipt, plus an explicit current release naming the next owner after the previous owner exits and its results are reconciled. Keep raw responses, bodies and databases private. Do not confuse a local baseline stop with release of a subsequently started DCC shard.

The current exclusive Medium window belongs to **Ziyang, shard 2**, under [`2026-10-03-ziyang-shard-2/release.json`](2026-10-03-ziyang-shard-2/release.json). Use the final metadata ledger and receipt in that directory, with the published stop verification and merged-state provenance. The earlier local stop at 2026-10-03T23:41:11Z and subsequent shard-1 startup are historical; the current release follows the intentional stop and reconciliation of that newer DCC run. Zherui's collectors remain stopped with restart disabled. There is no automatic expiry or fallback to shard 1: another owner requires a new explicit stopped/reconciled handoff. See the [release instructions](2026-10-03-ziyang-shard-2/README.md).
