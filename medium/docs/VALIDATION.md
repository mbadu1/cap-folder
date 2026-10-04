# Handoff validation — 2026-10-03

## Concurrent-shard policy verification — 2026-10-03

The user removed the exclusive-window requirement. Both production and validation CLI paths accept runs without the old flag; it is retained only as a no-op for older commands. A regression check still rejects a second writer to the same cache. Current platform tests passed: Medium 84 tests (83 pass, one expected private-artifact skip), Substack 71 pass. All 40 historical response bytes replayed exactly into 166 normalized posts under the changed source, with unchanged sample/reference bytes and a new source-bound bundle.

Fresh no-network shard-2 preflight preserved 937 done/29 failed feeds and 120,194 pending, recording zero requests. Read-only DCC inspection verified Zherui's original PID 1286257, 20 workers, held lock/matching bindings, live watcher and successful request 2498. No stop, restart, code/cache rebinding or Ziyang live request was performed. The existing external monitor is ACTIVE with its exclusivity rules removed. See [verification receipt](../validation/2026-10-03-parallel-policy-results.json), [parallel authorization](../handoff/PARALLEL_AUTHORIZATION.json), [baseline replay](../validation/2026-10-03-baseline-40-parallel/replay-evidence.json).


## V3 HTTP recovery and real DCC monitor

At the user's subsequent request, v3 records/skips ordinary HTTP errors and keeps scraping. Every 429 attempt remains in the ledger, while its work item stays pending for automatic retry after a shared cooldown of at least 60 seconds (longer Retry-After honored). Repeated 429s no longer permanently pause. Explicit access challenges, schema and resource stops retain their review guards. The active local legacy collector was unchanged. Old v1/v2 bindings and the earlier live trial below remain historical; new v3 caches and the [fresh v3 sample](../validation/2026-10-03-baseline-40-v3/manifest.json) are required.

The full local suite ran **151 tests: 150 passed/one expected skip**. The current Medium suite ran **80 tests: 79 passed/one expected skip** locally and on DCC Python 3.13.5. New coverage verifies ordinary 401/403/404/5xx continuation, preserved 429 attempts, shared 60-second wait and successful retry with simulated time, mirror retry queues, explicit challenge detection, every-error monitoring, cooldown versus stall, changed bindings, process/node/job checks and inspection recovery. Fresh v3 local baseline replay matched 40/40 records without network requests, snapshot watermark 107158; the original reference/sample were preserved separately.

The real watcher passed a private lifecycle fixture on compute job 57438801, node `dcc-mism-ferc-u-ab25-4-3`: verified detached PID/command/lock, first observation, configured ten-minute interval, singleton reuse, synthetic 403/500/429 error logging, expected cooldown classification and monitor-only stop while its fixture collector stayed alive. Both fixture processes then exited, with the watcher lock free. **Zero HTTP starts** occurred; the fixture gated transport for an hour and deliberately stopped it after seconds. Source hashes match the local implementation. This verifies runtime monitor mechanics, not actual live rate-limit behavior or a production monitor registration. No notification schedule was created on Ziyang's behalf; his prompt requires registering/verifying his own watcher and external schedule before unattended production.

See [v3 verification receipt](../validation/2026-10-03-v3-monitor-results.json), [monitor commands/recovery policy](MONITORING.md) and [repeatable DCC fixture](../tests/dcc_monitor_smoke.py). The previous 65-request v2 trial is not presented as a live validation of this changed v3 policy.

## Live staged DCC results

Executed pinned code commit `53e42a791fd968da11552bfc066c6f44264a3644` through `dcc-agent` on `dcc-mism-ferc-u-ab25-4-3`, job 57438801: four CPUs, 16 GiB, no GPU, Python 3.13.5. The original local worker stopped gracefully with its final report/lock verified; the refreshed ledger pinned baseline request 106475. The local checkpoint did not advance during DCC work. The full local compatibility suite ran 141 tests (140 passed/one expected skip); DCC reproduced Medium's 70 tests (69 passed/one expected skip).

| Trial | Workers observed | Successful feeds | Elapsed | Minimum global gap | Minimum worker gap | Assessment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| smoke | 1 | 5/5 | 24.47 s | 6.0007 s | 6.0007 s | PASS |
| ten | 10 | 20/20 | 29.14 s | 1.5007 s | 6.0088 s | PASS |
| twenty | 20 | 40/40 | 59.01 s | 1.5008 s | 6.0046 s | PASS |

All **65 requests** returned HTTP 200 without access/parser errors or stop markers. Minimum global spacing across all three trials, including process transitions, was 1.5007 seconds. Exact-response replay on local Python 3.12 matched every DCC normalized record in each trial, with zero additional requests. Five/20-feed historical comparisons returned PASS_NORMALIZED: only RSS build timestamps changed. The final trial matched **39/40 historical profile payloads**; its original comparison remains REVIEW_REQUIRED, with the source change inspected and documented below.

`@abewbinnie` kept the same candidate creator ID and ten feed entries. Since the baseline fetch on September 29, one new September 30 post (outside the frozen September 17 study cutoff) displaced an older September 14, 2025 post (inside the study window). All nine shared post records are unchanged. This is a reviewed live-source change: the same DCC bytes reparse exactly locally, and the immutable historical reference and displaced baseline record are preserved. No expected result was rewritten to obtain agreement.

All three DCC PIDs exited and locks were free before the original local collector resumed deliberately with its original 3.1-second setting, 250,000 author-candidate target and retention/resource policy. Fresh HTTP 200 progress and its lock/heartbeat were verified. Only the intentional test STOP was archived. No continuous DCC production or assigned feed/mirror pilot was launched. Validation caches remain separate from production. The test ledger became historical after local resume. The later concurrent-shard authorization supersedes the earlier stopped-owner requirement; use the shipped metadata exclusions and current parallel-policy validation bundle. Ziyang must repeat the staged validation on his own node/account. Bounded RSS trials do not establish sustained throughput, full histories or sampling eligibility.

See [machine-readable results and artifact hashes](../validation/2026-10-03-dcc-results.json), [immutable sample/replay manifest](../validation/2026-10-03-baseline-40/manifest.json) and [reproduction commands](DCC_VALIDATION_PLAN.md). Raw responses, bodies, SQLite caches and the expected reference remain private.

## Staged validation implementation

The updated Medium suite runs **70 tests: 69 passed and one expected private-artifact skip**. Seven new tests verify immutable 40-feed freezing/replay, selected-feed scope, validation-only bindings, pacing/access review, reference tampering, separate stage bindings and exact-response replay drift detection. The shipped 40-feed local baseline sample passed exact same-response replay with zero network requests, snapshot request watermark 106471. The [staged plan](DCC_VALIDATION_PLAN.md) and Ziyang prompt now require 1/10/20-worker trials and private baseline comparison before an assigned-shard pilot. The historical sections below describe earlier revisions.

## Updated pacing (v2)

The user requested the final Substack history timers: **1.5 seconds globally and 6 seconds per worker**, with 20 workers. The updated Medium suite ran **63 tests: 62 passed and one expected private-artifact skip**. New checks cover simultaneous global/per-thread intervals, finite minimum bounds, defaults and pinning both configuration values. A new no-network v2 shard-2 preparation bound both timers and recorded zero requests, with the same 385 completed/14 failed/120,761 pending feed exclusions from the frozen preparation ledger. Shell syntax and whitespace checks passed. Existing v1 caches retain their original bindings and require their original revision; this change uses fresh v2 caches. No Medium DCC collector or pilot was launched.

## Original handoff (v1)

The local Python 3.12 offline suite ran **131 tests**, with **130 passing and one expected skip** for an absent private toy artifact. Medium's suite ran 60 (59 passing, one skip); Substack's suite ran 71 passing. The Substack one-command compatibility check separately reported PASS with 56 checks and golden output SHA-256 `2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded`; no actual corpus export comparison was requested.

A clean checkout made from the staged Git tree independently reproduced the 131-test result. Its no-network shard-2 preparation verified every frozen CSV hash, restored the 58,385-key baseline count, skipped 385 done feeds, retained 14 failed feeds and left 120,761 pending profiles (primary and reserve combined). It logged **zero network requests**. Git's staged CSV bytes match their frozen manifest hashes; `.gitattributes` prevents line-ending normalization. Markdown links, compatibility symlinks, shell syntax and whitespace checks passed. Existing Substack collector scripts remain byte-identical to the previously published revision.

The new runner tests exercise 20-thread gate contention, persisted cooldowns/access stops, exact pilot budgets, primary/in-flight/reserve scheduling, target-slot reservation, ID deduplication, existing failures/completed-story exclusions, parser rollback and identical-response equivalence, interrupted-task handling, disk/lock guards, private export hashes, new-copy merges and duplicate-import suppression.

At the original v1 handoff, this validated offline behavior and repository portability; no new Medium DCC pilot or production scraper had been launched. Its preparation ledger was a running-baseline snapshot. The current parallel authorization supplies the later merged metadata ledger and removes the former stop/reconcile prerequisite for starting the other shard. A GitHub Actions workflow is included; its remote result must be checked separately.
