#!/bin/bash

set -euo pipefail

REPO_URL="https://github.com/SeaweedAshes/webrtc-trace.git"
BRANCH="main"
REPO_DIR="$HOME/webrtc-trace"
WORKDIR="$HOME/webrtc-trace-bootstrap"
LOG_ROOT="$HOME/webrtc-logs-send"
RUNS_ROOT="$HOME/trace-runs"
PORT=8888
DURATION_SEC=1800
RUN_ID=""
RECEIVER_HOST=""
RECEIVER_USER=""

usage() {
  cat <<EOF
Usage: $0 --receiver-host HOST --receiver-user USER [options]

Required:
  --receiver-host HOST        Receiver machine IP or hostname
  --receiver-user USER        Receiver machine username

Options:
  --run-id ID                 Shared run id for sender/receiver scripts
  --repo-url URL              Trace repo URL (default: $REPO_URL)
  --branch NAME               Git branch (default: $BRANCH)
  --repo-dir PATH             Local repo checkout path (default: $REPO_DIR)
  --workdir PATH              Bootstrap workdir (default: $WORKDIR)
  --log-root PATH             Sender log root (default: $LOG_ROOT)
  --runs-root PATH            Local run bundle root (default: $RUNS_ROOT)
  --duration-sec N            Collection duration (default: $DURATION_SEC)
  --port N                    peerconnection_server port (default: $PORT)
EOF
  exit 1
}

log() {
  printf '[sender-collect] %s\n' "$*"
}

die() {
  printf '[sender-collect] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --receiver-host) RECEIVER_HOST="$2"; shift 2 ;;
      --receiver-user) RECEIVER_USER="$2"; shift 2 ;;
      --run-id) RUN_ID="$2"; shift 2 ;;
      --repo-url) REPO_URL="$2"; shift 2 ;;
      --branch) BRANCH="$2"; shift 2 ;;
      --repo-dir) REPO_DIR="$2"; shift 2 ;;
      --workdir) WORKDIR="$2"; shift 2 ;;
      --log-root) LOG_ROOT="$2"; shift 2 ;;
      --runs-root) RUNS_ROOT="$2"; shift 2 ;;
      --duration-sec) DURATION_SEC="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  [[ -n "$RECEIVER_HOST" && -n "$RECEIVER_USER" ]] || usage
  [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "duration-sec must be numeric"
  if [[ -z "$RUN_ID" ]]; then
    RUN_ID="$(date +%Y%m%d_%H%M%S)"
  fi
}

ensure_repo_and_build() {
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    git clone "$REPO_URL" "$REPO_DIR"
  fi
  (
    cd "$REPO_DIR"
    git fetch --all --prune
    if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
      git checkout "$BRANCH"
      git pull --ff-only
    fi
    chmod +x trace_tools/*.sh
    ./trace_tools/bootstrap_trace_lab.sh \
      --repo-url "$REPO_URL" \
      --branch "$BRANCH" \
      --workdir "$WORKDIR"
  )
}

latest_log_dir() {
  find "$1" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1
}

main() {
  parse_args "$@"
  ensure_repo_and_build

  mkdir -p "$LOG_ROOT" "$RUNS_ROOT/$RUN_ID"

  local src_root="$WORKDIR/src"
  local server_pid
  local latest_dir
  local run_dir="$RUNS_ROOT/$RUN_ID/sender"

  cd "$src_root"
  log "starting peerconnection_server on port $PORT"
  timeout "$((DURATION_SEC + 120))" ./out/Trace/peerconnection_server --port="$PORT" > "$HOME/webrtc-server-$RUN_ID.log" 2>&1 &
  server_pid=$!
  sleep 1

  log "collecting sender logs for ${DURATION_SEC}s (run_id=$RUN_ID)"
  timeout "$((DURATION_SEC + 120))" env WEBRTC_LOG_DIR="$LOG_ROOT" \
    xvfb-run -a ./out/Trace/peerconnection_client \
      --server=localhost \
      --port="$PORT" \
      --autoconnect \
      --autocall > "$HOME/webrtc-sender-$RUN_ID.log" 2>&1 || true

  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true

  latest_dir="$(latest_log_dir "$LOG_ROOT")"
  [[ -n "$latest_dir" ]] || die "no sender log directory found under $LOG_ROOT"

  rm -rf "$run_dir"
  mkdir -p "$run_dir"
  cp -a "$latest_dir/." "$run_dir/"

  log "copying sender bundle to receiver: ${RECEIVER_USER}@${RECEIVER_HOST}:~/trace-runs/$RUN_ID/sender"
  ssh "${RECEIVER_USER}@${RECEIVER_HOST}" "mkdir -p ~/trace-runs/$RUN_ID/sender"
  scp -r "$run_dir/." "${RECEIVER_USER}@${RECEIVER_HOST}:~/trace-runs/$RUN_ID/sender/"

  log "done"
  log "  run_id=$RUN_ID"
  log "  local_sender_bundle=$run_dir"
  log "  receiver_target=~/trace-runs/$RUN_ID/sender"
}

main "$@"
