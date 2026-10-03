# Medium two-owner, 20-worker plan — 2026-10-03

Zherui owns shard 1; Ziyang owns shard 2. Each shard has 96,160 primary profiles and 25,000 ordered reserves. The frozen frame also includes 57,680 previously collected in-window candidate identities. Preserve all v1 CSVs/manifests; their `execution_ready=false` fields describe the original planning snapshot. The new runtime binding lives in each separate team cache, with a separately hashed reconciliation ledger.

## Scheduling and limits

One coordinator parses and commits SQLite; up to 20 transport threads each use an isolated HTTP session. All request kinds share one gate at **3.1 seconds or slower** between starts, with persisted cooldowns. Twenty threads overlap response waits; they do not permit twenty times the request rate. The theoretical ceiling remains about 19.35 starts/minute before latency, parsing or cooldowns. Sustained DCC performance has not been measured.

**Only one Medium team run may be active at a time.** This includes the original local worker, the other shard and pilots. Separate machines do not share this gate. Coordinate an exclusive window, stop the previous collector gracefully, verify its lock/process have ended, refresh its ledger, then start the next owner. `--exclusive-team-window` records the operator's confirmation; it cannot remotely detect another person's process. Do not run two independent timers simultaneously.

## Reconciliation and ownership

1. Keep the old baseline checkpoint and texts intact. A read-only metadata ledger records its known creator keys, completed/failed feed and mirror tasks, route blocks, cooldowns and last request ID. The shipped ledger is a preparation snapshot of a still-running baseline; refresh it after the prior worker exits before actual collection.
2. Initialize a fresh team cache from the frozen files and latest ledger. Existing attempted profiles and story tasks remain done/error, including failed items. They are not requested again. The baseline's bodies remain in the baseline store rather than being copied into Ziyang's cache.
3. Resolve new RSS candidate IDs against the known registry and within the shard. An already-known identity does not advance the counter or trigger new mirror work. Profile aliases that redirect remain explicit reconciliation failures. Final merges deduplicate keys and story IDs; candidate IDs are not certified people.
4. Only assigned profiles can become feed tasks. Mirrors are scheduled only for in-window RSS-linked stories of newly admitted identities. No sitemap discovery, direct-profile/GraphQL work or unscoped mirror crawl is scheduled. Out-of-window RSS metadata remains source labeled; only in-window bodies are retained.
5. Finish primary feed attempts before starting reserves, including primary requests still in flight. Stop new feeds when the baseline-plus-new candidate counter reaches 250,000, allowing no extra target admissions from in-flight work; drain already-admitted story tasks. A shard can exhaust its candidates below target. That is a reported shortfall, never a complete-history claim.
6. Export the stopped shard privately, merge it into a **new** baseline copy, reconcile that updated copy, then hand the refreshed ledger to the next owner. Never silently rebind an old cache to a new ledger. New ledger/code/configuration requires a new team cache after merging prior results.

## Access, retention and recovery

HTTP 401/403, `cf-mitigated: challenge` and recognized HTML challenges halt new starts and preserve `PAUSED.json`. In-flight responses settle. Three consecutive unexpected parser failures also pause, with rollback of each failed parse. Isolated alias/missing-article/date errors remain item failures. HTTP 429 is recorded as an item error with no automatic retry; all threads wait for the later of the existing deadline and Retry-After, with a 60-second minimum/fallback. Three 429s in ten minutes pause. These Medium rules differ from Substack's authorized 429 retry behavior.

Requests follow the existing 32 MiB response bound, public RSS retention and explicit-free mirror retention; paid/unknown mirror bodies are not saved. TLS verification remains enabled, redirects are not followed, and cookies do not persist across tasks. Maintain a 20 GiB free-disk reserve, four CPUs/16 GiB as a modest starting DCC allocation, and no GPU. The default team run allows unlimited total cache growth, as already authorized for Medium, while preserving the free-space guard.

Resume with the exact code/assignment/ledger/configuration binding. Successful and failed tasks remain skipped. Requests cancelled before a start stay pending; tasks left in flight after an unclean exit become explicit interrupted errors and require review rather than automatic refetch. Preserve source hash bindings and stop/pause evidence. Inspect pauses before deliberately archiving them; do not remove them to force progress. No `scancel`, IP rotation, challenge solving or access bypass is part of this workflow.

## Verification and delivery

Run the offline suite and no-network `prepare` first. A 40-request DCC pilot uses the real assigned checkpoint; its successful records contribute to production and are not refetched. Review actual statuses, timestamps, raw hashes, source identity/date/body flags and pacing before using `--continuous`. The offline suite tests identical-response parser equivalence; live responses can change and are not expected to reproduce an old snapshot byte for byte.

Exports include a consistent SQLite snapshot, hash-checked retained source blobs, gate/binding/start records, failures, candidate keys and a file manifest. Transfer the export through the team's private data channel; GitHub receives code, docs, tests and public profile assignments only. The merge validates every export file, preserves source versions, refuses conflicting body/observation versions, recounts deduplicated candidate keys and leaves previous stores unchanged. Preserve collection failures and incomplete coverage in reports. Monthly statistical sampling remains separate.
