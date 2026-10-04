# Preserved metadata handoff — Ziyang shard 2

As of 2026-10-03, the user explicitly authorizes concurrent shard-1 and shard-2 scraping. The current [parallel authorization](../PARALLEL_AUTHORIZATION.json) and [pointer](../CURRENT_WINDOW.json) supersede the exclusive-window rules in historical `release.json` and `withdrawal.json`. Neither historical file requires Ziyang to wait or Zherui to stop.

`final-ledger.json.gz` remains the immutable metadata input for Ziyang's fresh parallel-policy cache. SHA-256: `a897f8a274aa3d02b3ebd5a4dbc6d54cf9696a0366fbb58e0fda00b0a7aa3299`. It has 60,642 known candidate keys, 102,298 completed/failed tasks and merged watermark 109,911. Verify its matching receipt and the new authorization's source hashes. The older `requires_exclusive_team_window` field describes the superseded policy; do not modify the ledger or receipt to change it.

`stop-verification.json`, `merge-receipt.json`, the historical release and withdrawal preserve what happened earlier. Those receipts are provenance, not current stopping prerequisites. Zherui's restored shard 1 continues under its original source/cache binding. Ziyang uses the latest code and a new cache after offline tests, staged validation and pilot.

The snapshot does not update while shard 1 continues. Gates, cooldowns and counters are independent; final private exports must reconcile aliases, versions, story IDs and unique candidates. There is no exact distributed 250,000 stop. Keep article bodies and databases private. Follow the [runbook](../../docs/DCC_RUNBOOK.md) and [full prompt](../../docs/ZIYANG_START_PROMPT.md).
