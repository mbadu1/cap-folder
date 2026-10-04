# Fixed baseline replay for concurrent Medium shards — 2026-10-03

This bundle preserves the exact sample and public reference bytes from `../2026-10-03-baseline-40-v3/`. A fresh offline replay of all 40 retained source responses using the current parallel-policy code passed exactly; see `replay-evidence.json` and the source pins in `manifest.json`. No HTTP requests were made. Original bundles and expected outputs were not rewritten.

`reference-metadata.json.gz` contains public metadata/classifications/word counts/text hashes only. Article bodies, raw responses and databases stay private. Live RSS changes still require review. Both assigned shards may run concurrently; each operator must pass the staged tests and pilot in their own fresh caches. See `../../docs/DCC_VALIDATION_PLAN.md`.
