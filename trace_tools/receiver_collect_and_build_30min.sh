#!/bin/bash

set -euo pipefail

REPO_URL="https://github.com/SeaweedAshes/webrtc-trace.git"
BRANCH="main"
REPO_DIR="$HOME/webrtc-trace"
WORKDIR="$HOME/webrtc-trace-bootstrap"
LOG_ROOT="$HOME/webrtc-logs-recv"
RUNS_ROOT="$HOME/trace-runs"
PORT=8888
DURATION_SEC=1800
DURATION_MIN=""
WAIT_TIMEOUT_SEC=900
RECEIVER_OFFSET_MS=0
SENDER_SSH_PORT_ON_RECEIVER=22022
SIGNAL_SERVER_ROLE="receiver"
RUN_DATE=""
RUN_ID=""
SENDER_HOST=""

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --sender-host HOST          Sender hostname or address when sender hosts signaling
  --run-date YYYYMMDD         Shared run date directory (default: today's local date)
  --run-id ID                 Shared run id for sender/receiver scripts
  --repo-url URL              Trace repo URL (default: $REPO_URL)
  --branch NAME               Git branch (default: $BRANCH)
  --repo-dir PATH             Local repo checkout path (default: $REPO_DIR)
  --workdir PATH              Bootstrap workdir (default: $WORKDIR)
  --log-root PATH             Receiver log root (default: $LOG_ROOT)
  --runs-root PATH            Run bundle root (default: $RUNS_ROOT)
  --duration-sec N            Collection duration (default: $DURATION_SEC)
  --duration-min N            Collection duration in minutes
  --wait-timeout-sec N        Wait time for sender bundle (default: $WAIT_TIMEOUT_SEC)
  --receiver-offset-ms N      Passed to build_trace_from_logs.py
  --sender-ssh-port-on-receiver N  Reverse SSH port for pulling sender logs
  --signal-server-role ROLE   sender|receiver (default: $SIGNAL_SERVER_ROLE)
  --port N                    Signaling port for peerconnection_server/client (default: $PORT)
EOF
  exit 1
}

log() {
  printf '[receiver-collect] %s\n' "$*"
}

die() {
  printf '[receiver-collect] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --sender-host) SENDER_HOST="$2"; shift 2 ;;
      --run-date) RUN_DATE="$2"; shift 2 ;;
      --run-id) RUN_ID="$2"; shift 2 ;;
      --repo-url) REPO_URL="$2"; shift 2 ;;
      --branch) BRANCH="$2"; shift 2 ;;
      --repo-dir) REPO_DIR="$2"; shift 2 ;;
      --workdir) WORKDIR="$2"; shift 2 ;;
      --log-root) LOG_ROOT="$2"; shift 2 ;;
      --runs-root) RUNS_ROOT="$2"; shift 2 ;;
      --duration-sec) DURATION_SEC="$2"; shift 2 ;;
      --duration-min) DURATION_MIN="$2"; shift 2 ;;
      --wait-timeout-sec) WAIT_TIMEOUT_SEC="$2"; shift 2 ;;
      --receiver-offset-ms) RECEIVER_OFFSET_MS="$2"; shift 2 ;;
      --sender-ssh-port-on-receiver) SENDER_SSH_PORT_ON_RECEIVER="$2"; shift 2 ;;
      --signal-server-role) SIGNAL_SERVER_ROLE="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  if [[ -n "$DURATION_MIN" ]]; then
    [[ "$DURATION_MIN" =~ ^[0-9]+$ ]] || die "duration-min must be numeric"
    DURATION_SEC="$((DURATION_MIN * 60))"
  fi
  [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "duration-sec must be numeric"
  (( DURATION_SEC > 0 )) || die "duration must be greater than zero"
  [[ "$WAIT_TIMEOUT_SEC" =~ ^[0-9]+$ ]] || die "wait-timeout-sec must be numeric"
  [[ "$RECEIVER_OFFSET_MS" =~ ^-?[0-9]+$ ]] || die "receiver-offset-ms must be numeric"
  [[ "$SENDER_SSH_PORT_ON_RECEIVER" =~ ^[0-9]+$ ]] || die "sender-ssh-port-on-receiver must be numeric"
  [[ "$PORT" =~ ^[0-9]+$ ]] || die "port must be numeric"
  [[ "$SIGNAL_SERVER_ROLE" =~ ^(sender|receiver)$ ]] || die "signal-server-role must be sender or receiver"
  if [[ -z "$RUN_DATE" ]]; then
    RUN_DATE="$(date +%Y%m%d)"
  fi
  [[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || die "run-date must be YYYYMMDD"
  if [[ "$SIGNAL_SERVER_ROLE" == "sender" && -z "$SENDER_HOST" ]]; then
    die "--sender-host is required when signal-server-role=sender"
  fi
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

pull_sender_bundle() {
  local sender_dir="$1"
  local deadline=$((SECONDS + WAIT_TIMEOUT_SEC))
  rm -rf "$sender_dir"
  mkdir -p "$sender_dir"
  while (( SECONDS < deadline )); do
    rm -rf "$sender_dir"
    mkdir -p "$sender_dir"
    if scp \
      -P "$SENDER_SSH_PORT_ON_RECEIVER" \
      -o ConnectTimeout=10 \
      -o StrictHostKeyChecking=accept-new \
      -r "localhost:trace-runs/$RUN_DATE/$RUN_ID/sender/." "$sender_dir/" >/dev/null 2>&1; then
      if compgen -G "$sender_dir/*.csv" > /dev/null; then
        return 0
      fi
    fi
    sleep 10
  done
  return 1
}

main() {
  parse_args "$@"
  ensure_repo_and_build

  local run_root="$RUNS_ROOT/$RUN_DATE/$RUN_ID"
  mkdir -p "$LOG_ROOT" "$run_root"

  local src_root="$WORKDIR/src"
  local latest_dir
  local receiver_dir="$run_root/receiver"
  local sender_dir="$run_root/sender"
  local trace_path="$run_root/generated_trace.csv"
  local server_log="$HOME/webrtc-server-$RUN_DATE-$RUN_ID.log"
  local client_log="$HOME/webrtc-receiver-$RUN_DATE-$RUN_ID.log"
  local server_pid
  local client_server_host

  cd "$src_root"
  if [[ "$SIGNAL_SERVER_ROLE" == "receiver" ]]; then
    log "starting peerconnection_server on receiver port $PORT"
    log "server log: $server_log"
    timeout "$((DURATION_SEC + 120))" ./out/Trace/peerconnection_server --port="$PORT" > "$server_log" 2>&1 &
    server_pid=$!
    client_server_host="localhost"
    sleep 1
  else
    client_server_host="$SENDER_HOST"
  fi

  log "collecting receiver logs for ${DURATION_SEC}s (run_date=$RUN_DATE, run_id=$RUN_ID, server=$client_server_host, port=$PORT)"
  log "client log: $client_log"
  timeout "$((DURATION_SEC + 120))" env WEBRTC_LOG_DIR="$LOG_ROOT" \
    xvfb-run -a ./out/Trace/peerconnection_client \
      --server="$client_server_host" \
      --port="$PORT" \
      --autoconnect > "$client_log" 2>&1 || true

  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi

  latest_dir="$(latest_log_dir "$LOG_ROOT")"
  [[ -n "$latest_dir" ]] || die "no receiver log directory found under $LOG_ROOT; check $client_log and $server_log"

  rm -rf "$receiver_dir"
  mkdir -p "$receiver_dir"
  cp -a "$latest_dir/." "$receiver_dir/"

  log "pulling sender bundle via reverse SSH port $SENDER_SSH_PORT_ON_RECEIVER"
  pull_sender_bundle "$sender_dir" || die "failed to pull sender bundle within ${WAIT_TIMEOUT_SEC}s"

  log "building trace"
  python3 "$REPO_DIR/trace_tools/build_trace_from_logs.py" \
    --sender-log-dir "$sender_dir" \
    --receiver-log-dir "$receiver_dir" \
    --receiver-time-offset-ms "$RECEIVER_OFFSET_MS" \
    --output "$trace_path"

  log "done"
  log "  run_date=$RUN_DATE"
  log "  run_id=$RUN_ID"
  log "  receiver_bundle=$receiver_dir"
  log "  sender_bundle=$sender_dir"
  log "  trace=$trace_path"
}

main "$@"
