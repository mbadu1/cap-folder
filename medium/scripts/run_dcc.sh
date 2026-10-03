#!/usr/bin/env bash
set -euo pipefail

# Invoke on a compute node reached through dcc-agent; never on a login node.
# All runner options after the phase are passed as separate, quoted arguments.
phase=${1:?Usage: bash medium/scripts/run_dcc.sh pilot|production --shard N --reconciliation FILE --cache-root DIR --exclusive-team-window}
shift
case "$phase" in
  pilot) phase_options=(--max-requests 40) ;;
  production) phase_options=(--continuous) ;;
  *) echo "Phase must be pilot or production" >&2; exit 2 ;;
esac
if [[ -z "${SLURM_JOB_ID:-}" || "$(hostname)" == *login* ]]; then
  echo "Use dcc-agent on a compute node in an active Slurm allocation" >&2
  exit 2
fi
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd -- "$repo_dir"
exec .venv/bin/python -u medium/scripts/parallel_medium_collection.py run \
  --workers 20 --delay-seconds 3.1 --min-free-gb 20 --max-cache-gb 0 \
  --require-dcc "${phase_options[@]}" "$@"
