#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_RECORD_SCRIPT="$SCRIPT_DIR/run_record_and_build_trace.sh"

RUN_DIR=""
TRACE=""
EXTRA_ARGS=()

usage() {
  cat <<EOF
Usage: $0 --run-dir DIR --trace TRACE.csv [extra options]

This is a thin wrapper around run_record_and_build_trace.sh:
  - replays TRACE.csv during the call
  - records sender/receiver logs
  - skips rebuilding another trace
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --trace) TRACE="$2"; shift 2 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

[[ -n "$RUN_DIR" && -n "$TRACE" ]] || usage

exec "$RUN_RECORD_SCRIPT" \
  --run-dir "$RUN_DIR" \
  --record-trace "$TRACE" \
  --skip-build \
  "${EXTRA_ARGS[@]}"
