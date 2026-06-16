#!/bin/bash

set -euo pipefail

REPO_DIR="$HOME/webrtc-trace"
RUNS_ROOT="$HOME/trace-runs"
RUN_DATE=""
RUN_ID=""
SENDER_LOG_DIR=""
RECEIVER_LOG_DIR=""
OUTPUT_PATH=""
RECEIVER_OFFSET_MS=0

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --repo-dir PATH             Local repo checkout path (default: $REPO_DIR)
  --runs-root PATH            Run bundle root (default: $RUNS_ROOT)
  --run-date YYYYMMDD         Run date directory
  --run-id ID                 Run id directory
  --sender-log-dir PATH       Sender bundle directory
  --receiver-log-dir PATH     Receiver bundle directory
  --output PATH               Output trace CSV path
  --receiver-offset-ms N      Passed to build_trace_from_logs.py (default: $RECEIVER_OFFSET_MS)

Either:
  1. provide --sender-log-dir and --receiver-log-dir
or
  2. provide --run-date and --run-id to use:
     \$RUNS_ROOT/\$RUN_DATE/\$RUN_ID/sender
     \$RUNS_ROOT/\$RUN_DATE/\$RUN_ID/receiver
EOF
  exit 1
}

log() {
  printf '[trace-build] %s\n' "$*"
}

die() {
  printf '[trace-build] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-dir) REPO_DIR="$2"; shift 2 ;;
      --runs-root) RUNS_ROOT="$2"; shift 2 ;;
      --run-date) RUN_DATE="$2"; shift 2 ;;
      --run-id) RUN_ID="$2"; shift 2 ;;
      --sender-log-dir) SENDER_LOG_DIR="$2"; shift 2 ;;
      --receiver-log-dir) RECEIVER_LOG_DIR="$2"; shift 2 ;;
      --output) OUTPUT_PATH="$2"; shift 2 ;;
      --receiver-offset-ms) RECEIVER_OFFSET_MS="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  [[ "$RECEIVER_OFFSET_MS" =~ ^-?[0-9]+$ ]] || die "receiver-offset-ms must be numeric"

  if [[ -z "$SENDER_LOG_DIR" || -z "$RECEIVER_LOG_DIR" ]]; then
    [[ -n "$RUN_DATE" && -n "$RUN_ID" ]] || usage
    SENDER_LOG_DIR="${SENDER_LOG_DIR:-$RUNS_ROOT/$RUN_DATE/$RUN_ID/sender}"
    RECEIVER_LOG_DIR="${RECEIVER_LOG_DIR:-$RUNS_ROOT/$RUN_DATE/$RUN_ID/receiver}"
  fi

  [[ -d "$SENDER_LOG_DIR" ]] || die "sender-log-dir does not exist: $SENDER_LOG_DIR"
  [[ -d "$RECEIVER_LOG_DIR" ]] || die "receiver-log-dir does not exist: $RECEIVER_LOG_DIR"

  if [[ -z "$OUTPUT_PATH" ]]; then
    if [[ -n "$RUN_DATE" && -n "$RUN_ID" ]]; then
      OUTPUT_PATH="$RUNS_ROOT/$RUN_DATE/$RUN_ID/generated_trace.csv"
    else
      OUTPUT_PATH="$PWD/generated_trace.csv"
    fi
  fi
}

main() {
  parse_args "$@"

  [[ -f "$REPO_DIR/trace_tools/build_trace_from_logs.py" ]] || die "missing build_trace_from_logs.py under $REPO_DIR/trace_tools"
  mkdir -p "$(dirname "$OUTPUT_PATH")"

  log "building trace"
  log "  sender=$SENDER_LOG_DIR"
  log "  receiver=$RECEIVER_LOG_DIR"
  log "  output=$OUTPUT_PATH"

  python3 "$REPO_DIR/trace_tools/build_trace_from_logs.py" \
    --sender-log-dir "$SENDER_LOG_DIR" \
    --receiver-log-dir "$RECEIVER_LOG_DIR" \
    --receiver-time-offset-ms "$RECEIVER_OFFSET_MS" \
    --output "$OUTPUT_PATH"

  log "done"
  log "  trace=$OUTPUT_PATH"
}

main "$@"
