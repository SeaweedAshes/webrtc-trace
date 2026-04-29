#!/bin/bash

set -euo pipefail

REPO_URL="https://github.com/SeaweedAshes/webrtc-trace.git"
BRANCH="main"
REPO_DIR="$HOME/webrtc-trace"
WORKDIR="$HOME/webrtc-trace-bootstrap"
LOG_ROOT="$HOME/webrtc-logs-send"
RUNS_ROOT="$HOME/trace-runs"
PORT=8888
DURATION_SEC=200
DURATION_MIN=""
RUN_DATE=""
RUN_ID=""
RECEIVER_HOST=""
TRACE_PATH=""
TRACE_IFACE=""
TRACE_START_DELAY_SEC=30
SYSTEM_LOG=1
SYSTEM_INTERVAL_SEC=1

usage() {
  cat <<EOF
Usage: $0 --receiver-host HOST --trace PATH [options]

Options:
  --receiver-host HOST        Receiver machine IP/hostname that hosts signaling
  --trace PATH                netem trace CSV to replay on sender egress
  --trace-iface IFACE         Sender egress interface; default auto via ip route get
  --trace-start-delay-sec N   Wait N seconds after sender client starts before replay (default: $TRACE_START_DELAY_SEC)
                              Use this ramp-up period to avoid locking GCC at low resolution.
  --run-date YYYYMMDD         Shared run date directory (default: today's local date)
  --run-id ID                 Shared run id
  --repo-url URL              Trace repo URL (default: $REPO_URL)
  --branch NAME               Git branch (default: $BRANCH)
  --repo-dir PATH             Local repo checkout path (default: $REPO_DIR)
  --workdir PATH              Bootstrap workdir (default: $WORKDIR)
  --log-root PATH             Sender log root (default: $LOG_ROOT)
  --runs-root PATH            Local/shared run bundle root (default: $RUNS_ROOT)
  --duration-sec N            Replay duration (default: $DURATION_SEC)
  --duration-min N            Replay duration in minutes
  --port N                    Signaling port for peerconnection_server/client (default: $PORT)
  --system-log                Enable sender system/thermal logging (default)
  --no-system-log             Disable sender system/thermal logging
  --system-interval-sec N     System metric sampling interval (default: $SYSTEM_INTERVAL_SEC)
EOF
  exit 1
}

log() {
  printf '[sender-replay] %s\n' "$*"
}

die() {
  printf '[sender-replay] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --receiver-host) RECEIVER_HOST="$2"; shift 2 ;;
      --trace) TRACE_PATH="$2"; shift 2 ;;
      --trace-iface) TRACE_IFACE="$2"; shift 2 ;;
      --trace-start-delay-sec) TRACE_START_DELAY_SEC="$2"; shift 2 ;;
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
      --port) PORT="$2"; shift 2 ;;
      --system-log) SYSTEM_LOG=1; shift ;;
      --no-system-log) SYSTEM_LOG=0; shift ;;
      --system-interval-sec) SYSTEM_INTERVAL_SEC="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  [[ -n "$RECEIVER_HOST" ]] || usage
  [[ -n "$TRACE_PATH" ]] || usage
  [[ -f "$TRACE_PATH" ]] || die "trace file not found: $TRACE_PATH"
  if [[ -n "$DURATION_MIN" ]]; then
    [[ "$DURATION_MIN" =~ ^[0-9]+$ ]] || die "duration-min must be numeric"
    DURATION_SEC="$((DURATION_MIN * 60))"
  fi
  [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "duration-sec must be numeric"
  (( DURATION_SEC > 0 )) || die "duration must be greater than zero"
  [[ "$TRACE_START_DELAY_SEC" =~ ^[0-9]+$ ]] || die "trace-start-delay-sec must be numeric"
  [[ "$PORT" =~ ^[0-9]+$ ]] || die "port must be numeric"
  [[ "$SYSTEM_LOG" =~ ^(0|1)$ ]] || die "system-log must be 0 or 1"
  [[ "$SYSTEM_INTERVAL_SEC" =~ ^[0-9]+$ ]] || die "system-interval-sec must be numeric"
  (( SYSTEM_INTERVAL_SEC > 0 )) || die "system-interval-sec must be greater than zero"
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

resolve_trace_iface() {
  if [[ -n "$TRACE_IFACE" ]]; then
    printf '%s\n' "$TRACE_IFACE"
    return
  fi
  ip route get "$RECEIVER_HOST" | awk '
    {
      for (i = 1; i <= NF; i++) {
        if ($i == "dev") {
          print $(i + 1)
          exit
        }
      }
    }'
}

CLIENT_PID=""
TRACE_PID=""
SYSTEM_PID=""
RUN_ROOT=""
TRACE_IFACE_RESOLVED=""

cleanup() {
  if [[ -n "$TRACE_PID" ]]; then
    kill "$TRACE_PID" 2>/dev/null || true
    wait "$TRACE_PID" 2>/dev/null || true
  fi
  if [[ -n "$CLIENT_PID" ]]; then
    kill "$CLIENT_PID" 2>/dev/null || true
    wait "$CLIENT_PID" 2>/dev/null || true
  fi
  if [[ -n "$SYSTEM_PID" ]]; then
    kill "$SYSTEM_PID" 2>/dev/null || true
    wait "$SYSTEM_PID" 2>/dev/null || true
  fi
  if [[ -n "$TRACE_IFACE_RESOLVED" ]]; then
    sudo -n tc qdisc replace dev "$TRACE_IFACE_RESOLVED" root netem delay 0ms 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

main() {
  parse_args "$@"
  ensure_repo_and_build

  RUN_ROOT="$RUNS_ROOT/$RUN_DATE/$RUN_ID"
  local run_dir="$RUN_ROOT/sender"
  local system_tmp="$RUN_ROOT/sender_system_tmp"
  local src_root="$WORKDIR/src"
  local client_log="$HOME/webrtc-sender-replay-$RUN_DATE-$RUN_ID.log"
  local trace_log="$RUN_ROOT/sender_trace_replay.log"
  local latest_dir

  TRACE_IFACE_RESOLVED="$(resolve_trace_iface)"
  [[ -n "$TRACE_IFACE_RESOLVED" ]] || die "could not resolve trace iface for receiver host $RECEIVER_HOST"

  mkdir -p "$LOG_ROOT" "$RUN_ROOT"
  log "receiver=$RECEIVER_HOST port=$PORT"
  log "trace=$TRACE_PATH"
  log "trace_iface=$TRACE_IFACE_RESOLVED"
  log "run_root=$RUN_ROOT"

  sudo -n tc qdisc replace dev "$TRACE_IFACE_RESOLVED" root netem delay 0ms || die "tc qdisc reset failed; sudo -n may require passwordless tc"

  if [[ "$SYSTEM_LOG" -eq 1 ]]; then
    rm -rf "$system_tmp"
    mkdir -p "$system_tmp"
    "$REPO_DIR/trace_tools/collect_system_metrics.sh" \
      --output-dir "$system_tmp" \
      --duration-sec "$((DURATION_SEC + 120))" \
      --interval-sec "$SYSTEM_INTERVAL_SEC" \
      > "$system_tmp/collector.log" 2>&1 &
    SYSTEM_PID=$!
  fi

  cd "$src_root"
  log "starting sender WebRTC client"
  timeout "$DURATION_SEC" env WEBRTC_LOG_DIR="$LOG_ROOT" \
    xvfb-run -a --server-args="-screen 0 2560x1440x24" \
      ./out/Trace/peerconnection_client \
      --server="$RECEIVER_HOST" \
      --port="$PORT" \
      --autoconnect \
      --autocall > "$client_log" 2>&1 &
  CLIENT_PID=$!

  sleep "$TRACE_START_DELAY_SEC"
  log "starting trace replay after ${TRACE_START_DELAY_SEC}s"
  python3 "$REPO_DIR/trace_tools/replay_netem_trace.py" \
    --trace "$TRACE_PATH" \
    --iface "$TRACE_IFACE_RESOLVED" \
    --sudo \
    --reset-at-end > "$trace_log" 2>&1 &
  TRACE_PID=$!

  local client_status=0
  wait "$CLIENT_PID" || client_status=$?
  CLIENT_PID=""
  if [[ "$client_status" -ne 0 && "$client_status" -ne 124 ]]; then
    die "sender client exited with status $client_status; check $client_log"
  fi

  if [[ -n "$TRACE_PID" ]]; then
    kill "$TRACE_PID" 2>/dev/null || true
    wait "$TRACE_PID" 2>/dev/null || true
    TRACE_PID=""
  fi
  if [[ -n "$SYSTEM_PID" ]]; then
    kill "$SYSTEM_PID" 2>/dev/null || true
    wait "$SYSTEM_PID" 2>/dev/null || true
    SYSTEM_PID=""
  fi

  latest_dir="$(latest_log_dir "$LOG_ROOT")"
  [[ -n "$latest_dir" ]] || die "no sender log directory found under $LOG_ROOT; check $client_log"

  rm -rf "$run_dir"
  mkdir -p "$run_dir"
  cp -a "$latest_dir/." "$run_dir/"
  if [[ -d "$system_tmp" ]]; then
    mkdir -p "$run_dir/system"
    cp -a "$system_tmp/." "$run_dir/system/"
    rm -rf "$system_tmp"
  fi
  cat > "$run_dir/REPLAY_INFO.txt" <<EOF
receiver_host=$RECEIVER_HOST
trace=$TRACE_PATH
trace_iface=$TRACE_IFACE_RESOLVED
trace_start_delay_sec=$TRACE_START_DELAY_SEC
run_date=$RUN_DATE
run_id=$RUN_ID
port=$PORT
duration_sec=$DURATION_SEC
system_log=$SYSTEM_LOG
system_interval_sec=$SYSTEM_INTERVAL_SEC
EOF

  log "done"
  log "  sender_bundle=$run_dir"
  log "  trace_log=$trace_log"
}

main "$@"
