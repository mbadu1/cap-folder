# Medium staged DCC validation

Use the final Substack testing pattern: offline tests, a fixed baseline replay, a small live smoke test, a 10-worker trial, then a 20-worker trial. The Medium timers are **1.5 seconds globally and six seconds per worker**. The v3 runner uses the current [HTTP recovery and monitoring policy](MONITORING.md), preserving explicit challenge and retention guards. Each live stage uses a separate validation cache and only official profile feeds; it requests no mirrors or sitemaps and cannot be exported as a production shard.

| Stage | Workers | Maximum requests | Required evidence |
| --- | ---: | ---: | --- |
| Offline | 0 | 0 | Platform tests; exact replay of 40 retained local feeds |
| smoke | 1 | 5 | All five feeds committed, spacing and integrity pass |
| ten | 10 | 20 | smoke PASS with matching sample, code and stopped ledger |
| twenty | 20 | 40 | ten PASS with the same bindings |
| Review | 0 | 0 | Compare live outputs with local reference and replay the exact DCC responses |

The nested stages intentionally repeat the first five/20 feeds in isolated caches. Total live budget is 65 request attempts. HTTP 429 recovery counts against a bounded stage budget; any incomplete stage requires review rather than escalation. The worker count is the configured transport pool size; record actual worker IDs and observed intervals rather than assuming every thread was exercised. This is a bounded feasibility check, not sustained throughput or complete author histories.

## Freeze and replay the local reference — Zherui

From the repository root, using the environment containing the pinned dependencies:

```sh
python medium/scripts/validate_dcc.py freeze \
  --checkpoint .cache/medium_history/history_v1/crawl.sqlite3 \
  --output medium/validation/2026-10-03-baseline-40-v3 \
  --reference .cache/medium_validation_reference/2026-10-03-v3/baseline.json.gz
```

The shipped sample is already frozen; do not rerun into these paths. The command reads one consistent SQLite snapshot, selects 40 previously successful pool feeds by a deterministic profile hash, verifies retained response hashes, and reparses the same bytes in a temporary cache. It compares candidate IDs, dates, post counts, titles, tags, normalized text hashes and retention classifications. The v3 directory now also ships `reference-metadata.json.gz`, containing the exact expected public RSS metadata, classifications, word counts and text hashes. Article bodies, raw responses and caches stay private. The manifest retains its original `private_reference_sha256` field; verify the shipped reference against it. Existing expected results are immutable. `PASS_EXACT` is required before live requests; code changes require a new sample/reference directory and fresh replay.

## Stop and reconcile before live work

Follow [DCC_RUNBOOK.md](DCC_RUNBOOK.md): stop the old collector deliberately, wait for final reporting, verify PID exit and the free lock, keep stop evidence and refresh the metadata ledger. The monitor must honor the deliberate stop. Confirm one exclusive Medium window across owners. The current shard-2 release, stop evidence, refreshed merged ledger and checksums are shipped under `medium/handoff/2026-10-03-ziyang-shard-2/`; verify `release.json` and ensure no later release supersedes it. The preparation ledger in Git is insufficient for live validation. Verify the shipped final ledger against its SHA-256 receipt and the current release pins. Carry its cooldown/access state into each stage.

## Run on a verified compute allocation

Use `ssh dcc-agent` from the local machine. Commands below execute **inside its CPU compute allocation**, from the pinned checkout; do not execute them on a login node. Verify actual node/job/resources and install `medium/requirements.txt`. Four CPUs/16 GiB and no GPU suffice for the transport test. Run the offline suite first:

```sh
.venv/bin/python -m unittest discover -s medium/tests -p 'test_*.py'
task_ledger=medium/handoff/2026-10-03-ziyang-shard-2/final-ledger.json.gz
task_validation=.cache/medium_validation/2026-10-03-baseline-40-v3
.venv/bin/python medium/scripts/validate_dcc.py run --stage smoke \
  --cache-root "$task_validation/smoke" --handoff-ledger "$task_ledger" \
  --exclusive-team-window
```

Inspect `assessment.json`, `run_receipt.json`, `request_starts.jsonl`, `report/heartbeat.json` and retained responses. `PASS` requires the exact stage request count, all selected feeds HTTP 200 and successfully parsed/committed, a healthy database, original code hashes and both pacing intervals. Record errors and stop evidence; do not retry an error or clear markers automatically. If smoke passes, inspect its outputs and proceed:

```sh
.venv/bin/python medium/scripts/validate_dcc.py run --stage ten \
  --cache-root "$task_validation/ten" --handoff-ledger "$task_ledger" \
  --previous-assessment "$task_validation/smoke/assessment.json" --exclusive-team-window
.venv/bin/python medium/scripts/validate_dcc.py run --stage twenty \
  --cache-root "$task_validation/twenty" --handoff-ledger "$task_ledger" \
  --previous-assessment "$task_validation/ten/assessment.json" --exclusive-team-window
```

Execute the second command only after reviewing ten's PASS. Wait at least six seconds after a stage exits before launching the next stage or assigned-shard pilot, so process changes cannot compress the request spacing; gates in separate caches do not coordinate with one another. Any HTTP/access/parser/resource/pacing failure ends escalation. Active requests settle and their outcomes remain recorded. Preserve failed caches for diagnosis. Observe live stages about once a minute and retain the exact job/node/PID and revision. Do not start production from this validator.

## Compare and replay after every stage

The exact v3 metadata reference is shipped, so each operator can compare and replay on their own verified compute node after each stage exits. No baseline database or private reference transfer is needed. Keep all validation caches, raw responses and article bodies private; use a consistent SQLite backup for any later private transfer. From the pinned checkout:

```sh
python medium/scripts/validate_dcc.py compare \
  --cache-root "$task_validation/smoke" \
  --reference medium/validation/2026-10-03-baseline-40-v3/reference-metadata.json.gz
python medium/scripts/validate_dcc.py replay \
  --cache-root "$task_validation/smoke"
```

Repeat for ten and twenty. Comparison reports `PASS_EXACT`, `PASS_NORMALIZED` (raw bytes changed but normalized records agree), or `REVIEW_REQUIRED` with affected profiles. RSS can change between the historical fetch and live test; never rewrite the historical reference to make it pass. Same-response replay verifies identical DCC response bytes produce identical normalized records locally, without new HTTP requests. Source drift with exact replay still requires a recorded review of changed dates/IDs/posts/text; parser drift blocks rollout.

Before production, review all assessments, baseline comparisons, same-response replay and source changes. Then run the separate assigned-shard 40-request pilot from the runbook, which additionally exercises feed/mirror scheduling and free-only retention. Only that reviewed production-cache pilot permits continuous collection under the teammate prompt. Ziyang must run these stages on his own account/node; Zherui's result is evidence, not proof of Ziyang's network access. Keep all validation caches outside production exports and merges.
