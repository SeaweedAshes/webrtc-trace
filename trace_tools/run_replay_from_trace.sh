#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_RECORD_SCRIPT="$SCRIPT_DIR/run_record_and_build_trace.sh"

RUNS_ROOT="$HOME/trace-runs"
RUN_DATE=""
RUN_ID=""
RUN_DIR=""
TRACE=""
EXTRA_ARGS=()

infer_run_dir_from_trace() {
  local trace_path="$1"
  local trace_dir
  local trace_file
  trace_dir="$(dirname "$trace_path")"
  trace_file="$(basename "$trace_path")"

  if [[ "$trace_file" == "generated_trace.csv" ]]; then
    local maybe_run_id
    local maybe_run_date
    maybe_run_id="$(basename "$trace_dir")"
    maybe_run_date="$(basename "$(dirname "$trace_dir")")"
    if [[ "$maybe_run_date" =~ ^[0-9]{8}$ ]]; then
      printf '%s\n' "$(dirname "$trace_dir")/replay-$maybe_run_id"
      return
    fi
  fi

  printf '%s\n' "$trace_dir/replay-${trace_file%.csv}"
}

usage() {
  cat <<EOF
Usage: $0 [options] [extra options]

This is a thin wrapper around run_record_and_build_trace.sh:
  - replays TRACE.csv during the call
  - records sender/receiver logs
  - skips rebuilding another trace

Options:
  --trace PATH                Explicit trace CSV path
  --run-dir DIR               Explicit replay output directory
  --runs-root PATH            Run bundle root (default: $RUNS_ROOT)
  --run-date YYYYMMDD         Date directory under runs-root
  --run-id ID                 Run id directory under runs-root

If --trace is omitted, the script uses:
  \$RUNS_ROOT/\$RUN_DATE/\$RUN_ID/generated_trace.csv

If --run-dir is omitted, the script uses:
  \$RUNS_ROOT/\$RUN_DATE/replay-\$RUN_ID
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs-root) RUNS_ROOT="$2"; shift 2 ;;
    --run-date) RUN_DATE="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --trace) TRACE="$2"; shift 2 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [[ -z "$TRACE" ]]; then
  if [[ -n "$RUN_DATE" && -n "$RUN_ID" ]]; then
    TRACE="$RUNS_ROOT/$RUN_DATE/$RUN_ID/generated_trace.csv"
  else
    usage
  fi
fi

if [[ -z "$RUN_DIR" ]]; then
  if [[ -n "$RUN_DATE" && -n "$RUN_ID" ]]; then
    RUN_DIR="$RUNS_ROOT/$RUN_DATE/replay-$RUN_ID"
  else
    RUN_DIR="$(infer_run_dir_from_trace "$TRACE")"
  fi
fi

[[ -f "$TRACE" ]] || { echo "missing trace file: $TRACE" >&2; exit 1; }

echo "[replay-run] trace=$TRACE"
echo "[replay-run] run_dir=$RUN_DIR"

exec "$RUN_RECORD_SCRIPT" \
  --run-dir "$RUN_DIR" \
  --record-trace "$TRACE" \
  --skip-build \
  "${EXTRA_ARGS[@]}"
