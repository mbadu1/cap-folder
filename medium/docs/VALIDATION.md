# Handoff validation — 2026-10-03

## Staged validation implementation

The updated Medium suite runs **70 tests: 69 passed and one expected private-artifact skip**. Seven new tests verify immutable 40-feed freezing/replay, selected-feed scope, validation-only bindings, pacing/access review, reference tampering, separate stage bindings and exact-response replay drift detection. The shipped 40-feed local baseline sample passed exact same-response replay with zero network requests, snapshot request watermark 106471. The [staged plan](DCC_VALIDATION_PLAN.md) and Ziyang prompt now require 1/10/20-worker trials and private baseline comparison before an assigned-shard pilot. Live DCC results will be recorded separately; the historical sections below describe earlier revisions.

## Updated pacing (v2)

The user requested the final Substack history timers: **1.5 seconds globally and 6 seconds per worker**, with 20 workers. The updated Medium suite ran **63 tests: 62 passed and one expected private-artifact skip**. New checks cover simultaneous global/per-thread intervals, finite minimum bounds, defaults and pinning both configuration values. A new no-network v2 shard-2 preparation bound both timers and recorded zero requests, with the same 385 completed/14 failed/120,761 pending feed exclusions from the frozen preparation ledger. Shell syntax and whitespace checks passed. Existing v1 caches retain their original bindings and require their original revision; this change uses fresh v2 caches. No Medium DCC collector or pilot was launched.

## Original handoff (v1)

The local Python 3.12 offline suite ran **131 tests**, with **130 passing and one expected skip** for an absent private toy artifact. Medium's suite ran 60 (59 passing, one skip); Substack's suite ran 71 passing. The Substack one-command compatibility check separately reported PASS with 56 checks and golden output SHA-256 `2987d6b4406fa38ef2ac7700b726f468afff3c576943465d3505a057b16e3ded`; no actual corpus export comparison was requested.

A clean checkout made from the staged Git tree independently reproduced the 131-test result. Its no-network shard-2 preparation verified every frozen CSV hash, restored the 58,385-key baseline count, skipped 385 done feeds, retained 14 failed feeds and left 120,761 pending profiles (primary and reserve combined). It logged **zero network requests**. Git's staged CSV bytes match their frozen manifest hashes; `.gitattributes` prevents line-ending normalization. Markdown links, compatibility symlinks, shell syntax and whitespace checks passed. Existing Substack collector scripts remain byte-identical to the previously published revision.

The new runner tests exercise 20-thread gate contention, persisted cooldowns/access stops, exact pilot budgets, primary/in-flight/reserve scheduling, target-slot reservation, ID deduplication, existing failures/completed-story exclusions, parser rollback and identical-response equivalence, interrupted-task handling, disk/lock guards, private export hashes, new-copy merges and duplicate-import suppression.

At the original v1 handoff, this validated offline behavior and repository portability; no new Medium DCC pilot or production scraper had been launched. Its preparation ledger is a running-baseline snapshot; use the runbook to stop/reconcile it before live shard collection. A GitHub Actions workflow is included; its remote result must be checked separately.
