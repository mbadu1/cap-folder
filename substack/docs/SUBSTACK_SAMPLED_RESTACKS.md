# Sampled-post restack collection

Prepared 2026-10-02 for `substack-sampled-provisional-2026-10-02`. The user requested a script and preparation for future collection. The user subsequently authorized starting collection: the pinned full run launched on DCC at 15:11:05 UTC, with a verified 30-minute heartbeat and a separate frozen input/checkpoint. See the enclosing workspace’s `research/SUBSTACK_RESTACKS_COLLECTION_2026-10-02.md` for registration, live checks and allocation rollover. This is an independent engagement snapshot; sampling, source checkpoints, delivered CSVs/manifests, ZIP and cloud artifact are preserved.

## Verified retrieval routes

**2026-10-02 performance update:** The user subsequently authorized a speedup. The active production worker now uses a separate pinned extension importing this unchanged collector, one-second sequential pacing and adaptive capped publication batches with direct fallback. It preserves the original binding and all committed results through a separately bound execution extension. A 15-publication live benchmark saved 435 selected counts with 29 batch requests; eight direct checks matched exactly. Eight extension tests and fifteen base tests passed. Production progress and allocation rollover/export must use the enclosing workspace’s `research/SUBSTACK_RESTACKS_COLLECTION_2026-10-02.md` and current `check_restacks.py`/v2 manager. The local zero-request prepared checkpoint and original commands below are historical/bounded development examples, not current production resume commands. The 1–3 day planning estimate remains provisional.

The default requests `GET {publication_url}/api/v1/posts/by-id/{post_id}` anonymously, extracts `post.restacks`, and requires both returned native post ID and publication ID to match the frozen sampled metadata. Three selected posts spanning 2020, 2023 and 2026 returned matching IDs and counts 0, 0 and 5 in the feasibility check. The JSON responses were 34,782, 93,396 and 28,988 bytes. This route avoids retrieving an entire publication history and requests only selected posts. It is an observed public route, not a documented stable official API contract; upstream schema/access may change.

If the by-ID route returns 404/410, one canonical public-page fallback reads the embedded `post` object and applies the same ID/count validation. Ordinary 403s and exhausted transient failures are saved as unavailable without automatic replay on resume. Authentication, access challenges, explicit rate-limited 403s, schema changes and identity mismatches pause for review. Missing/null counts are empty values, never inferred zeros. No authentication, cookies, comment listing, liker lists or restacker identities are used.

Optional `--strategy hybrid --batch-page-cap N` retrieves at most N pages of 50 publication posts, stores **only matches to sampled IDs**, then directly fetches remaining selected posts. First-page feasibility probes returned all restack fields for 50, 40 and 23 posts. Response bodies and unselected metadata are discarded. Page progress and selected results commit together. A matching batch record with an absent/null count stays pending for direct lookup. The page cap prevents an unbounded full-history crawl; direct fallback avoids treating shifting pagination, deleted entries or list omissions as zero engagement. A capped list page may be an extra request for sparse/old selections. Compare match yield, bytes and requests on a bounded pilot before choosing hybrid or increasing the page cap. A batch improvement in whole-corpus runtime has not been established.

## Input and checkpoint integrity

Use the extracted dataset root containing `DELIVERY_MANIFEST.json`. Preparation verifies every listed `posts_meta.csv` and `sampling_assignments.csv` against its byte count/SHA-256, requires exact selected-ID set equality within each month, and reconciles monthly inventory and aggregate counts. It reads no text-body files. Native post IDs are deduplicated; inconsistent repeated IDs fail preparation. The prepared frame contains 413,252 posts in 81 months across 23,156 publications.

The output directory must be outside the delivered dataset. `restacks.sqlite3`, `binding.json` and an exclusive `collector.lock` hold the selected frame, results and resumable state. The binding pins delivery-manifest SHA-256, target-set hash, source binding and script SHA-256. Changed inputs/code or target/result identity tampering are rejected. Use the pinned code to resume; create a new output version for a new snapshot or revised collector. Do not edit binding files to bypass a mismatch.

Every successful record retains only sampled identity keys, `restacks_count`, UTC response observation time, source type, request/final URL and response SHA-256. Hashes record provenance; raw bodies are not saved and cannot subsequently be replayed from this checkpoint. Count values are cumulative **at collection time**, not engagement at historical month-end. Restacks are not equivalent to shares or likes. This script intentionally collects restacks only; cached likes/comments can be enriched separately.

## Commands on this Mac

Run from `/Users/zheruizhang/Desktop/capstone project`. All preparation/status/export commands make zero network requests. The prepared production checkpoint exists at `outputs/substack_restacks_2026-10-02/prepared` with all 413,252 targets pending and zero HTTP requests. The bounded implementation validation snapshot is separate under `pilot`: exactly three HTTP 200 requests returned 0, 0 and 5 restacks with matching native IDs/publication IDs. `pilot_success.csv` contains only those three verified rows. A zero-request intermediate preparation checkpoint is preserved under `prepared-before-final-review`; it is historical and must not be resumed with the final script.

```sh
.venv/bin/python cap-folder/scripts/substack/collect_sampled_restacks.py prepare \
  --dataset outputs/substack_restacks_2026-10-02/input-frozen \
  --output outputs/substack_restacks_2026-10-02/prepared
```

Future bounded collection: repeat this command to resume pending targets; successful and saved unavailable targets are skipped.

```sh
.venv/bin/python cap-folder/scripts/substack/collect_sampled_restacks.py collect \
  --dataset outputs/substack_restacks_2026-10-02/input-frozen \
  --output outputs/substack_restacks_2026-10-02/prepared \
  --max-requests 100 --delay-seconds 3 --strategy direct
```

Explicit `--max-requests 0` permits an unbounded future invocation. It is **not** the default; the subsequently authorized full DCC invocation explicitly uses it. The request budget counts every HTTP attempt, including retries/fallbacks; reaching it leaves unfinished targets pending. Default pacing is one sequential worker with at least three seconds between starts; the CLI refuses less than one second. Pacing for a larger run must follow the user's permission terms and coordinate with other Substack collectors; there is no cross-process shared rate gate. One by-ID request per selected post at a three-second start interval has a 344.38-hour scheduling bound before latency/retries, so no fast full-run ETA is promised. GPUs are unnecessary.

HTTP 429 persists its cooldown and retries the same request after `Retry-After` (seconds or HTTP date); a missing/invalid header uses 60 seconds. Request limits still apply. Restart restores the cooldown. Interrupt with Ctrl-C or create `STOP` in the engagement output: committed results remain, and the marker is preserved. HTTP waits check STOP; an in-flight request may take up to the timeout to return. A `PAUSED.json` marker blocks ordinary resume. Investigate its reason; after deliberate review, archive that marker with its original contents rather than deleting it or adjusting bindings. There is no unattended recovery of authentication/challenge/integrity failures.

Export a joinable snapshot and status:

```sh
.venv/bin/python cap-folder/scripts/substack/collect_sampled_restacks.py export \
  --dataset outputs/substack_restacks_2026-10-02/input-frozen \
  --output outputs/substack_restacks_2026-10-02/prepared
```

`post_restacks.csv` contains **one row for every sampled post**, including pending/unavailable entries with empty counts. Join by `post_id` with monthly `posts_meta.csv`; retain `status` and observation time. `export_manifest.json` records the CSV checksum, input binding, exact status counts and `complete_success`. `ALL_TARGETS_ATTEMPTED` does not mean all counts were retrieved; check `counts.ok` and missing/error coverage. Do not merge this supplement into the immutable original manifests without creating a separately versioned enriched dataset.

For DCC execution, use only the existing `ssh dcc-agent` alias and the current user's confirmed work directory; never compute on the login node or cancel allocations. Transfer this standalone script and the selected metadata/assignment files plus their delivery manifest (not the raw corpus); preserve their relative paths/checksums. Use a separate new engagement output and modest CPU resources. No DCC restack collector or new monitor was launched during preparation; both were subsequently authorized and started as described above.

## Validation and evidence

Offline checks cover selected-only input/hash joins, native-ID/publication mismatches, zero versus null, batch exclusion/body discard, offset resume, idempotent success/error reuse, binding and result tampering, preserved STOP/PAUSED markers, 404 fallback, request budgets, Retry-After restoration, challenges and bounded transient retries. Run:

```sh
.venv/bin/python -m unittest discover -s cap-folder/tests -p test_sampled_restacks.py -v
.venv/bin/python -m unittest discover -s cap-folder/tests -p test_substack_parser_compatibility.py -v
```

The frozen standalone script copy is `outputs/substack_restacks_2026-10-02/code/collect_sampled_restacks.py`, SHA-256 `8657b4669c304351e81ecb5d007c55da20c2813a51a3c87f3097bb3bdd55d1c5`; it is byte-identical to the repository script at preparation. The final collector passed 15 offline tests and all 13 existing parser/handoff compatibility tests. `collection_plan.json` records the hashes/frame/configuration, `pilot_receipt.json` records the implementation probe, and the test logs are beside them. The enclosing workspace preserves live endpoint evidence in `research/audits/substack_restacks_by_id_probe_2026-10-02.json`, selected-page evidence in `substack_sample_engagement_live_probe_2026-10-02.json` and list-route evidence in `substack_engagement_batch_probe_2026-10-02.json`. New implementation pilot evidence is under `outputs/substack_restacks_2026-10-02/`. The existing sample remains provisional, with 37,314 pending publications and unresolved manual eligibility reviews; adding engagement does not clear those gates.

### 2026-10-02: approved pacing-timer repair

The active DCC run now executes a separately frozen `continue_restacks_timer_fixed.py`, importing both unchanged prior collectors. The user authorized the diagnosed negative-sleep repair and checkpointed resume; all 9,826 earlier records were preserved exactly. Current inspection/resume uses the hash-pinned v3 manager through the local checker, and export records both performance and timer-repair provenance. See `research/SUBSTACK_RESTACKS_COLLECTION_2026-10-02.md` in the parent workspace for current commands, hashes, receipts and safeguards. Do not run the obsolete original/batched main for this registered run.
