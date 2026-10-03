# Fixed Medium validation sample

`feeds.json` freezes 40 previously successful retained official Medium profile feeds from a read-only local checkpoint snapshot. `manifest.json` records the exact sample, private reference and parser hashes. Same-response local replay passed exactly for all 40 profiles with no network requests. The source checkpoint request watermark is 106471; this is an input snapshot, not a current corpus count.

Use the nested first 5/20/40 profiles with [the staged DCC plan](../../docs/DCC_VALIDATION_PLAN.md). Keep these files immutable. The private normalized reference is held by Zherui under `.cache/medium_validation_reference/2026-10-03/baseline.json.gz`; this repository contains no article text or raw responses. A reference request is independent of the assigned production worklists and must never enter shard exports/merges. New live feeds may differ from historical RSS; retain and review those changes rather than editing expected results.
