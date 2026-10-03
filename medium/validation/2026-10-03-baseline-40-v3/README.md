# Medium v3 fixed validation sample

`feeds.json` and `manifest.json` pin a read-only local baseline snapshot and exact no-network replay under the v3 HTTP recovery code. Use this directory with the current validator; earlier `2026-10-03-baseline-40` files and v2 DCC results remain historical, unchanged evidence requiring their original code revision.

The new private expected reference is held by Zherui at `.cache/medium_validation_reference/2026-10-03-v3/baseline.json.gz`. It and all corpus/cache data remain outside Git. Follow [staged validation](../../docs/DCC_VALIDATION_PLAN.md) and [HTTP recovery/monitoring](../../docs/MONITORING.md). Run the staged live checks on the operator's own DCC node before production; publishing the v3 sample does not claim a new live pilot or transfer a monitor registration.
