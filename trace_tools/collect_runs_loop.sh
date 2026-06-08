#!/bin/bash

set -euo pipefail

ROLE=""
COUNT=""
START_INDEX=1
RUN_PREFIX="run"
RUN_DATE=""
DURATION_MIN=30
DURATION_SEC=""
SLEEP_BETWEEN_SEC=10
CONTINUE_ON_ERROR=0
REPO_DIR="$HOME/webrtc-trace"
RUN_ARGS=()

usage() {
  cat <<EOF
Usage: $0 --role sender|receiver --count N [options] -- [collect-script-options]

Runs repeated non-replay WebRTC trace log collection. Put common collection
options after "--"; this wrapper injects --run-date, --run-id, and duration.

Options:
  --role sender|receiver       Which collector to run
  --count N                    Number of runs to collect
  --start-index N              First numeric run index (default: $START_INDEX)
  --run-prefix PREFIX          Run id prefix (default: $RUN_PREFIX; run id becomes PREFIXNN)
  --run-date YYYYMMDD          Shared run date (default: today's local date)
  --duration-min N             Duration per run in minutes (default: $DURATION_MIN)
  --duration-sec N             Duration per run in seconds
  --sleep-between-sec N        Delay between runs (default: $SLEEP_BETWEEN_SEC)
  --continue-on-error          Continue with the next run if one run fails
  --repo-dir PATH              Trace repo path (default: $REPO_DIR)

Examples:
  Receiver:
    $0 --role receiver --count 4 --run-date 20260608 --run-prefix run -- \\
      --port 8884 --signal-server-role receiver --runs-root ~/Sync/webrtc-trace-runs

  Sender:
    $0 --role sender --count 4 --run-date 20260608 --run-prefix run -- \\
      --receiver-host 211.63.206.29 --port 8884 --signal-server-role receiver \\
      --runs-root ~/Sync/webrtc-trace-runs
EOF
  exit 1
}

log() {
  printf '[collect-loop] %s\n' "$*"
}

die() {
  printf '[collect-loop] ERROR: %s\n' "$*" >&2
  exit 1
}

is_number() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --role) ROLE="$2"; shift 2 ;;
      --count) COUNT="$2"; shift 2 ;;
      --start-index) START_INDEX="$2"; shift 2 ;;
      --run-prefix) RUN_PREFIX="$2"; shift 2 ;;
      --run-date) RUN_DATE="$2"; shift 2 ;;
      --duration-min) DURATION_MIN="$2"; shift 2 ;;
      --duration-sec) DURATION_SEC="$2"; shift 2 ;;
      --sleep-between-sec) SLEEP_BETWEEN_SEC="$2"; shift 2 ;;
      --continue-on-error) CONTINUE_ON_ERROR=1; shift ;;
      --repo-dir) REPO_DIR="$2"; shift 2 ;;
      --) shift; RUN_ARGS=("$@"); break ;;
      *) usage ;;
    esac
  done

  [[ "$ROLE" =~ ^(sender|receiver)$ ]] || die "--role must be sender or receiver"
  [[ -n "$COUNT" ]] || die "--count is required"
  is_number "$COUNT" || die "--count must be numeric"
  is_number "$START_INDEX" || die "--start-index must be numeric"
  is_number "$SLEEP_BETWEEN_SEC" || die "--sleep-between-sec must be numeric"
  (( COUNT > 0 )) || die "--count must be greater than zero"

  if [[ -z "$RUN_DATE" ]]; then
    RUN_DATE="$(date +%Y%m%d)"
  fi
  [[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || die "--run-date must be YYYYMMDD"

  if [[ -n "$DURATION_SEC" ]]; then
    is_number "$DURATION_SEC" || die "--duration-sec must be numeric"
    (( DURATION_SEC > 0 )) || die "--duration-sec must be greater than zero"
  else
    is_number "$DURATION_MIN" || die "--duration-min must be numeric"
    (( DURATION_MIN > 0 )) || die "--duration-min must be greater than zero"
  fi

  [[ -d "$REPO_DIR/trace_tools" ]] || die "trace_tools not found under $REPO_DIR"
}

script_path() {
  case "$ROLE" in
    sender) printf '%s\n' "$REPO_DIR/trace_tools/sender_collect_30min.sh" ;;
    receiver) printf '%s\n' "$REPO_DIR/trace_tools/receiver_collect_logs.sh" ;;
  esac
}

run_id_for_index() {
  printf '%s%02d\n' "$RUN_PREFIX" "$1"
}

main() {
  parse_args "$@"

  local collector
  collector="$(script_path)"
  [[ -x "$collector" ]] || die "collector is not executable: $collector"

  local end_index=$((START_INDEX + COUNT - 1))
  log "role=$ROLE run_date=$RUN_DATE runs=${START_INDEX}-${end_index} duration=${DURATION_SEC:-${DURATION_MIN}m}"
  log "collector=$collector"

  local i run_id started_at status
  for ((i = START_INDEX; i <= end_index; i++)); do
    run_id="$(run_id_for_index "$i")"
    started_at="$(date '+%Y-%m-%d %H:%M:%S')"
    log "starting $RUN_DATE/$run_id at $started_at"

    status=0
    if [[ -n "$DURATION_SEC" ]]; then
      "$collector" \
        --run-date "$RUN_DATE" \
        --run-id "$run_id" \
        --duration-sec "$DURATION_SEC" \
        "${RUN_ARGS[@]}" || status=$?
    else
      "$collector" \
        --run-date "$RUN_DATE" \
        --run-id "$run_id" \
        --duration-min "$DURATION_MIN" \
        "${RUN_ARGS[@]}" || status=$?
    fi

    if [[ "$status" -ne 0 ]]; then
      log "run $RUN_DATE/$run_id failed with status $status"
      if [[ "$CONTINUE_ON_ERROR" -ne 1 ]]; then
        exit "$status"
      fi
    else
      log "finished $RUN_DATE/$run_id"
    fi

    if (( i < end_index && SLEEP_BETWEEN_SEC > 0 )); then
      log "sleeping ${SLEEP_BETWEEN_SEC}s before next run"
      sleep "$SLEEP_BETWEEN_SEC"
    fi
  done

  log "all requested runs finished"
}

main "$@"
