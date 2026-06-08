#!/bin/bash

set -euo pipefail

REPO_URL="https://github.com/SeaweedAshes/webrtc-trace.git"
BRANCH="${WEBRTC_TRACE_BRANCH:-}"
REPO_DIR="$HOME/webrtc-trace"
WORKDIR="$HOME/webrtc-trace-bootstrap"
LOG_ROOT="$HOME/webrtc-logs-send"
RUNS_ROOT="$HOME/trace-runs"
PORT=8888
DURATION_SEC=1800
DURATION_MIN=""
RECEIVER_SSH_PORT=22
SENDER_LOCAL_SSH_PORT=22
REVERSE_SSH_PORT=22022
SIGNAL_SERVER_ROLE="receiver"
SYSTEM_LOG=1
SYSTEM_INTERVAL_SEC=1
RUN_DATE=""
RUN_ID=""
RECEIVER_HOST=""
RECEIVER_USER=""

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --receiver-host HOST        Receiver machine IP or hostname when receiver hosts signaling
  --receiver-user USER        Ignored in local-copy mode; kept for backward compatibility
  --run-date YYYYMMDD         Shared run date directory (default: today's local date)
  --run-id ID                 Shared run id for sender/receiver scripts
  --repo-url URL              Trace repo URL (default: $REPO_URL)
  --branch NAME               Git branch (default: current repo branch, or origin default on fresh clone)
  --repo-dir PATH             Local repo checkout path (default: $REPO_DIR)
  --workdir PATH              Bootstrap workdir (default: $WORKDIR)
  --log-root PATH             Sender log root (default: $LOG_ROOT)
  --runs-root PATH            Local run bundle root (default: $RUNS_ROOT)
  --duration-sec N            Collection duration (default: $DURATION_SEC)
  --duration-min N            Collection duration in minutes
  --receiver-ssh-port N       Ignored in local-copy mode; kept for backward compatibility
  --sender-local-ssh-port N   Ignored in local-copy mode; kept for backward compatibility
  --reverse-ssh-port N        Ignored in local-copy mode; kept for backward compatibility
  --signal-server-role ROLE   sender|receiver (default: $SIGNAL_SERVER_ROLE)
  --port N                    Signaling port for peerconnection_server/client (default: $PORT)
  --system-log                Enable sender system/thermal logging (default)
  --no-system-log             Disable sender system/thermal logging
  --system-interval-sec N     System metric sampling interval (default: $SYSTEM_INTERVAL_SEC)
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
      --receiver-ssh-port) RECEIVER_SSH_PORT="$2"; shift 2 ;;
      --sender-local-ssh-port) SENDER_LOCAL_SSH_PORT="$2"; shift 2 ;;
      --reverse-ssh-port) REVERSE_SSH_PORT="$2"; shift 2 ;;
      --signal-server-role) SIGNAL_SERVER_ROLE="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      --system-log) SYSTEM_LOG=1; shift ;;
      --no-system-log) SYSTEM_LOG=0; shift ;;
      --system-interval-sec) SYSTEM_INTERVAL_SEC="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  if [[ -n "$DURATION_MIN" ]]; then
    [[ "$DURATION_MIN" =~ ^[0-9]+$ ]] || die "duration-min must be numeric"
    DURATION_SEC="$((DURATION_MIN * 60))"
  fi
  [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "duration-sec must be numeric"
  (( DURATION_SEC > 0 )) || die "duration must be greater than zero"
  [[ "$RECEIVER_SSH_PORT" =~ ^[0-9]+$ ]] || die "receiver-ssh-port must be numeric"
  [[ "$SENDER_LOCAL_SSH_PORT" =~ ^[0-9]+$ ]] || die "sender-local-ssh-port must be numeric"
  [[ "$REVERSE_SSH_PORT" =~ ^[0-9]+$ ]] || die "reverse-ssh-port must be numeric"
  [[ "$PORT" =~ ^[0-9]+$ ]] || die "port must be numeric"
  [[ "$SYSTEM_LOG" =~ ^(0|1)$ ]] || die "system-log must be 0 or 1"
  [[ "$SYSTEM_INTERVAL_SEC" =~ ^[0-9]+$ ]] || die "system-interval-sec must be numeric"
  (( SYSTEM_INTERVAL_SEC > 0 )) || die "system-interval-sec must be greater than zero"
  [[ "$SIGNAL_SERVER_ROLE" =~ ^(sender|receiver)$ ]] || die "signal-server-role must be sender or receiver"
  if [[ "$SIGNAL_SERVER_ROLE" == "receiver" && -z "$RECEIVER_HOST" ]]; then
    die "--receiver-host is required when signal-server-role=receiver"
  fi
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
    if [[ -z "$BRANCH" ]]; then
      BRANCH="$(git branch --show-current)"
      if [[ -z "$BRANCH" ]]; then
        BRANCH="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
      fi
      BRANCH="${BRANCH:-main}"
    fi
    log "using repo branch $BRANCH"
    git fetch --all --prune
    if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
      git checkout "$BRANCH"
      git pull --ff-only origin "$BRANCH"
    fi
    chmod +x trace_tools/*.sh
    ./trace_tools/bootstrap_trace_lab.sh \
      --repo-url "$REPO_URL" \
      --branch "$BRANCH" \
      --workdir "$WORKDIR"
  )
}

snapshot_log_dirs() {
  find "$1" -mindepth 1 -maxdepth 1 -type d | sort
}

csv_bytes_in_dir() {
  find "$1" -maxdepth 1 -type f -name '*.csv' -printf '%s\n' | awk '{s += $1} END {print s + 0}'
}

csv_file_span_sec() {
  awk -F, '
    NR > 1 && $1 ~ /^-?[0-9]+$/ {
      if (!seen) {
        first = $1
        seen = 1
      }
      last = $1
    }
    END {
      if (seen) {
        printf "%d\n", (last - first) / 1000
      } else {
        print 0
      }
    }
  ' "$1"
}

csv_media_span_sec_in_dir() {
  local dir="$1"
  local best=0
  local name
  local span
  for name in audio_packet_inserts.csv packet_buffer_inserts.csv receiver_rendered_frames.csv rtp_send.csv; do
    if [[ -f "$dir/$name" ]]; then
      span="$(csv_file_span_sec "$dir/$name")"
      if (( span > best )); then
        best="$span"
      fi
    fi
  done
  printf '%s\n' "$best"
}

minimum_acceptable_span_sec() {
  if (( DURATION_SEC > 300 )); then
    printf '%s\n' "$((DURATION_SEC - 120))"
  else
    printf '%s\n' "$((DURATION_SEC / 2))"
  fi
}

best_new_log_dir() {
  local log_root="$1"
  local before_file="$2"
  local after_file
  local candidates=()
  local best_dir=""
  local best_score=-1
  local dir
  local score

  after_file="$(mktemp)"
  snapshot_log_dirs "$log_root" > "$after_file"
  mapfile -t candidates < <(comm -13 "$before_file" "$after_file")
  rm -f "$after_file"

  if [[ "${#candidates[@]}" -eq 0 ]]; then
    mapfile -t candidates < <(snapshot_log_dirs "$log_root")
  fi

  for dir in "${candidates[@]}"; do
    [[ -d "$dir" ]] || continue
    score="$(csv_bytes_in_dir "$dir")"
    if (( score > best_score )); then
      best_score="$score"
      best_dir="$dir"
    fi
  done

  printf '%s\n' "$best_dir"
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
  local system_tmp="$run_root/sender_system_tmp"
  local system_pid=""
  local before_dirs_file

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

  log "collecting sender logs for ${DURATION_SEC}s (run_date=$RUN_DATE, run_id=$RUN_ID, server=$client_server_host, port=$PORT)"
  log "client log: $client_log"
  if [[ "$SYSTEM_LOG" -eq 1 ]]; then
    rm -rf "$system_tmp"
    mkdir -p "$system_tmp"
    log "system log: $system_tmp/thermal_cpu.csv"
    "$REPO_DIR/trace_tools/collect_system_metrics.sh" \
      --output-dir "$system_tmp" \
      --duration-sec "$((DURATION_SEC + 120))" \
      --interval-sec "$SYSTEM_INTERVAL_SEC" \
      > "$system_tmp/collector.log" 2>&1 &
    system_pid=$!
  fi
  before_dirs_file="$(mktemp)"
  snapshot_log_dirs "$LOG_ROOT" > "$before_dirs_file"
  local client_status=0
  timeout "$DURATION_SEC" env WEBRTC_LOG_DIR="$LOG_ROOT" \
    xvfb-run -a --server-args="-screen 0 2560x1440x24" \
      ./out/Trace/peerconnection_client \
      --server="$client_server_host" \
      --port="$PORT" \
      --autoconnect \
      --autocall > "$client_log" 2>&1 || client_status=$?

  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  if [[ -n "$system_pid" ]]; then
    kill "$system_pid" 2>/dev/null || true
    wait "$system_pid" 2>/dev/null || true
  fi

  latest_dir="$(best_new_log_dir "$LOG_ROOT" "$before_dirs_file")"
  rm -f "$before_dirs_file"
  [[ -n "$latest_dir" ]] || die "no sender log directory found under $LOG_ROOT; check $client_log and $server_log"

  local media_span_sec
  local min_span_sec
  media_span_sec="$(csv_media_span_sec_in_dir "$latest_dir")"
  min_span_sec="$(minimum_acceptable_span_sec)"
  if [[ "$client_status" -ne 0 && "$client_status" -ne 124 ]]; then
    if (( media_span_sec >= min_span_sec )); then
      log "WARNING: sender client exited with status $client_status after usable ${media_span_sec}s log span; keeping bundle"
    else
      die "sender client exited with status $client_status after only ${media_span_sec}s log span; check $client_log"
    fi
  fi

  rm -rf "$run_dir"
  mkdir -p "$run_dir"
  cp -a "$latest_dir/." "$run_dir/"
  if [[ -d "$system_tmp" ]]; then
    mkdir -p "$run_dir/system"
    cp -a "$system_tmp/." "$run_dir/system/"
    rm -rf "$system_tmp"
  fi

  cat > "$run_dir/COLLECT_INFO.txt" <<EOF
receiver_host=$RECEIVER_HOST
receiver_user=$RECEIVER_USER
receiver_ssh_port=$RECEIVER_SSH_PORT
sender_local_ssh_port=$SENDER_LOCAL_SSH_PORT
reverse_ssh_port=$REVERSE_SSH_PORT
signal_server_role=$SIGNAL_SERVER_ROLE
run_date=$RUN_DATE
run_id=$RUN_ID
port=$PORT
system_log=$SYSTEM_LOG
system_interval_sec=$SYSTEM_INTERVAL_SEC
EOF

  log "done"
  log "  run_date=$RUN_DATE"
  log "  run_id=$RUN_ID"
  log "  local_sender_bundle=$run_dir"
  log "  copy this sender bundle to receiver: $run_dir"
}

main "$@"
