# Offline provisional sampling from merged Substack histories

The 2026-10-02 user instruction authorizes starting the sampling workflow on the
verified delivered corpus. This is a provisional draw: the merged delivery has
37,314 pending frozen-frame publications, and manual identity/topic and language
validation remain prerequisites for final primary sampling. Neither quota
attainment nor successful execution clears those gates.

`scripts/substack/sample_substack_cached.py` builds a reusable SQLite index once,
reading the source checkpoint in read-only immutable mode. It preserves numeric
creator IDs, deduplicates native post IDs globally, retains all observed
creator/publication relationships, and quarantines conflicting date, text-hash
or access versions from text candidacy. Source databases and pinned collector
code are never changed. No network requests are made.

Provisional text candidates require public audience, no paywall, a saved body
of at least 200 words, and declared English or unknown language. Unknown language
is explicitly provisional and is counted separately; it is not proof of English.
Creator types use the existing name heuristic; unnamed identities are unresolved.
Creator-year topics use the existing keyword scores and deterministic tie-break
on qualifying, deduplicated posts. The collection parser supplied no authoritative
native category layer. Keyword scores are computed with equivalent precompiled
needles; final topic approval requires the manual audit. The script supplies a
64-creator-year stratified review worksheet with blank human labels.

Each of the 81 months uses the existing `allocate_sample`: target up to 2,500
creators, eight topic floors of 150, and capacity-constrained deterministic
proportional largest-remainder additions. Priorities remain
`SHA256(creator-priority-v1|substack|creator_id)` and
`SHA256(post-priority-v1|substack|post_id)`. Each chosen creator-month receives up
to three qualifying posts; coauthored text is stored once and assignments are
separate. Contribution bounds apply to assignments, not deduplicated body rows.

Outputs are created as needed: shared `lookup/`, the reusable `index.sqlite3`,
coverage, and `substack/YYYY/MM/` metadata, text, creator-month, assignment,
coverage and manifest files. September 2026 is marked as 17 observed days. Every
manifest has `provisional=true`, `final_sampling_ready=false`, and
`ready_to_publish=false`. No Git commit, push or external data publication occurs.

The run binds source size/mtime, merge/frame checksums, script/classifier hashes
and selection parameters. Indexing commits every 200 publications and resumes
from the last committed key. Other stages can be deterministically regenerated.
One process holds `run.lock`; never launch a duplicate or change bound code in
place. A complete index lets a same-binding restart skip the raw indexing pass.
Selected bodies are fetched from their cached canonical publications and checked
against indexed hashes, word counts and public-access flags before export.

## Active DCC run

Root: `/work/zz428/substack-sampling/provisional-2026-10-02-v2/`.
Frozen code: its `code/` directory; outputs: its `data/` directory.
Source: `/work/zz428/substack-merge-staging/provisional-baseline-shard1-partial-shard2-2026-10-02/crawl.sqlite3`.
`launch.json` records the PID, Slurm job, node, command and hashes;
`sampling.log` records progress/errors; `data/operation_status.json` is atomically
updated during processing. Inspect the actual process alongside these files.
Use only `ssh dcc-agent` for DCC work. The CPU allocation has four CPUs and 16 GiB;
the sampler uses a 128 MiB SQLite cache and disk temporary tables. No GPU is used.

When the process finishes, require stage `complete`, all 81 monthly manifests,
and the top-level `data/manifest.json`. Failed/interrupted work is not completion.
Before a resume, verify the original process is absent and the lock is free;
use the exact saved command and frozen code. Do not cancel the Slurm job or
restart collection. A later shard delivery requires a new versioned reconciliation
and draw, not blind appending to this immutable input or its bound index.

## Validation

### 2026-10-02 performance continuation

The user authorized optimization of the active draw. The original frozen sampler
and `data/binding.json` remain unchanged. Current `launch.json` registers a
separately pinned `code/continue_sampling_optimized.py`, PID 2277938 on the same
job. Original registration is `launch-original.json`. The driver reuses the
completed index and derived eligibility/topic tables, verifies inherited creator
and assignment rows through February 2023, and resumes subsequent months. It
forces equivalent month-first joins and adds performance indexes. Execution
provenance is stored in `data/continuation_binding.json` and new manifest
`binding.execution_extension`; transactionally committed continuation progress
uses the `optimized_last_completed_month` state key. See the enclosing project's
`research/SUBSTACK_SAMPLING_DELIVERY_2026-10-02.md` for hashes, receipts and monitor
instructions. Do not restart this continuation with the obsolete original command.

`python -m unittest discover -s tests -p 'test_substack*sampling*.py'` exercises
both the existing sampling gates and new synthetic deduplication, coauthorship,
access/language exclusions, conflict quarantine, exact study cutoff, permanent
post priorities, 81 partitions, source preservation, resume and changed-source
rejection. These checks validate implementation behavior, not corpus-wide
language, human identity, historical coverage or topic accuracy.
