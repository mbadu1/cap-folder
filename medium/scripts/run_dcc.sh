#!/usr/bin/env bash
set -euo pipefail

# Invoke on a compute node reached through dcc-agent; never on a login node.
# All runner options after the phase are passed as separate, quoted arguments.
phase=${1:?Usage: bash medium/scripts/run_dcc.sh pilot|production --shard N --reconciliation FILE --cache-root DIR}
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
# Pending reserves are held until the user revisits sampling. Keep all frozen
# assignments and existing source bindings; this performs no HTTP requests.
task_cache=""
task_batch=""
task_args=("$@")
for ((i=0; i<${#task_args[@]}; i++)); do
  case "${task_args[i]}" in
    --cache-root) task_cache=${task_args[i+1]:-} ;;
    --cache-root=*) task_cache=${task_args[i]#*=} ;;
    --batch) task_batch=${task_args[i+1]:-} ;;
    --batch=*) task_batch=${task_args[i]#*=} ;;
  esac
done
if [[ -z "$task_cache" ]]; then
  echo "Provide --cache-root for the prepared shard" >&2
  exit 2
fi
scope_options=(--cache-root "$task_cache")
if [[ -n "$task_batch" ]]; then scope_options+=(--batch "$task_batch"); fi
.venv/bin/python medium/scripts/defer_reserves.py "${scope_options[@]}"
exec .venv/bin/python -u medium/scripts/parallel_medium_collection.py run \
  --workers 20 --global-gap-seconds 1.5 --per-worker-gap-seconds 6.0 --min-free-gb 20 --max-cache-gb 0 \
  --require-dcc "${phase_options[@]}" "$@"
