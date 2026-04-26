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
DURATION_MIN=""
REVERSE_SSH_PORT=22022
SIGNAL_SERVER_ROLE="receiver"
RUN_DATE=""
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
  --run-date YYYYMMDD         Shared run date directory (default: today's local date)
  --run-id ID                 Shared run id for sender/receiver scripts
  --repo-url URL              Trace repo URL (default: $REPO_URL)
  --branch NAME               Git branch (default: $BRANCH)
  --repo-dir PATH             Local repo checkout path (default: $REPO_DIR)
  --workdir PATH              Bootstrap workdir (default: $WORKDIR)
  --log-root PATH             Sender log root (default: $LOG_ROOT)
  --runs-root PATH            Local run bundle root (default: $RUNS_ROOT)
  --duration-sec N            Collection duration (default: $DURATION_SEC)
  --duration-min N            Collection duration in minutes
  --reverse-ssh-port N        Port exposed on receiver for pulling sender logs
  --signal-server-role ROLE   sender|receiver (default: $SIGNAL_SERVER_ROLE)
  --port N                    Signaling port for peerconnection_server/client (default: $PORT)
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
      --reverse-ssh-port) REVERSE_SSH_PORT="$2"; shift 2 ;;
      --signal-server-role) SIGNAL_SERVER_ROLE="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  [[ -n "$RECEIVER_HOST" && -n "$RECEIVER_USER" ]] || usage
  if [[ -n "$DURATION_MIN" ]]; then
    [[ "$DURATION_MIN" =~ ^[0-9]+$ ]] || die "duration-min must be numeric"
    DURATION_SEC="$((DURATION_MIN * 60))"
  fi
  [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "duration-sec must be numeric"
  (( DURATION_SEC > 0 )) || die "duration must be greater than zero"
  [[ "$REVERSE_SSH_PORT" =~ ^[0-9]+$ ]] || die "reverse-ssh-port must be numeric"
  [[ "$PORT" =~ ^[0-9]+$ ]] || die "port must be numeric"
  [[ "$SIGNAL_SERVER_ROLE" =~ ^(sender|receiver)$ ]] || die "signal-server-role must be sender or receiver"
  if [[ -z "$RUN_DATE" ]]; then
    RUN_DATE="$(date +%Y%m%d)"
  fi
  [[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || die "run-date must be YYYYMMDD"
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

start_reverse_ssh_tunnel() {
  log "opening reverse SSH tunnel on receiver port $REVERSE_SSH_PORT"
  ssh -fNT \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=accept-new \
    -R "${REVERSE_SSH_PORT}:localhost:22" \
    "${RECEIVER_USER}@${RECEIVER_HOST}"
}

main() {
  parse_args "$@"
  ensure_repo_and_build

  local run_root="$RUNS_ROOT/$RUN_DATE/$RUN_ID"
  mkdir -p "$LOG_ROOT" "$run_root"

  local src_root="$WORKDIR/src"
  local server_pid
  local latest_dir
  local run_dir="$run_root/sender"
  local server_log="$HOME/webrtc-server-$RUN_DATE-$RUN_ID.log"
  local client_log="$HOME/webrtc-sender-$RUN_DATE-$RUN_ID.log"
  local client_server_host

  cd "$src_root"
  if [[ "$SIGNAL_SERVER_ROLE" == "sender" ]]; then
    log "starting peerconnection_server on sender port $PORT"
    log "server log: $server_log"
    timeout "$((DURATION_SEC + 120))" ./out/Trace/peerconnection_server --port="$PORT" > "$server_log" 2>&1 &
    server_pid=$!
    client_server_host="localhost"
    sleep 1
  else
    client_server_host="$RECEIVER_HOST"
  fi

  start_reverse_ssh_tunnel

  log "collecting sender logs for ${DURATION_SEC}s (run_date=$RUN_DATE, run_id=$RUN_ID, server=$client_server_host, port=$PORT)"
  log "client log: $client_log"
  timeout "$((DURATION_SEC + 120))" env WEBRTC_LOG_DIR="$LOG_ROOT" \
    xvfb-run -a ./out/Trace/peerconnection_client \
      --server="$client_server_host" \
      --port="$PORT" \
      --autoconnect \
      --autocall > "$client_log" 2>&1 || true

  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi

  latest_dir="$(latest_log_dir "$LOG_ROOT")"
  [[ -n "$latest_dir" ]] || die "no sender log directory found under $LOG_ROOT; check $client_log and $server_log"

  rm -rf "$run_dir"
  mkdir -p "$run_dir"
  cp -a "$latest_dir/." "$run_dir/"

  cat > "$run_dir/TUNNEL_INFO.txt" <<EOF
receiver_host=$RECEIVER_HOST
receiver_user=$RECEIVER_USER
reverse_ssh_port=$REVERSE_SSH_PORT
signal_server_role=$SIGNAL_SERVER_ROLE
run_date=$RUN_DATE
run_id=$RUN_ID
port=$PORT
EOF

  log "done"
  log "  run_date=$RUN_DATE"
  log "  run_id=$RUN_ID"
  log "  local_sender_bundle=$run_dir"
  log "  receiver pulls via: scp -P $REVERSE_SSH_PORT localhost:trace-runs/$RUN_DATE/$RUN_ID/sender/..."
}

main "$@"
