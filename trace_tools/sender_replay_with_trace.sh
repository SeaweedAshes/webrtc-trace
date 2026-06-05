#!/bin/bash

set -euo pipefail

REPO_URL="https://github.com/SeaweedAshes/webrtc-trace.git"
BRANCH="${WEBRTC_TRACE_BRANCH:-}"
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
MEDIA_SPLIT="none"
AUDIO_TRACE_PATH=""
VIDEO_TRACE_PATH=""
AUDIO_DELAY_MS=0
VIDEO_DELAY_MS=0
AUDIO_SSRC=""
VIDEO_SSRC=""
SSRC_WAIT_SEC=20
SYSTEM_LOG=1
SYSTEM_INTERVAL_SEC=1

usage() {
  cat <<EOF
Usage: $0 --receiver-host HOST --trace PATH [options]
       $0 --receiver-host HOST --media-split ssrc [--trace PATH|--video-trace PATH] [options]

Options:
  --receiver-host HOST        Receiver machine IP/hostname that hosts signaling
  --trace PATH                netem trace CSV to replay on sender egress
  --trace-iface IFACE         Sender egress interface; default auto via ip route get
  --trace-start-delay-sec N   Wait N seconds after sender client starts before replay (default: $TRACE_START_DELAY_SEC)
                              Use this ramp-up period to avoid locking GCC at low resolution.
  --media-split MODE          none|ssrc. If ssrc, classify RTP by live audio/video SSRC.
  --audio-trace PATH          Audio netem trace CSV for --media-split ssrc
  --video-trace PATH          Video netem trace CSV for --media-split ssrc (default: --trace)
  --audio-delay-ms N          Fixed audio delay when --audio-trace is omitted (default: $AUDIO_DELAY_MS)
  --video-delay-ms N          Fixed video delay when --video-trace and --trace are omitted (default: $VIDEO_DELAY_MS)
  --audio-ssrc LIST           Comma-separated audio SSRC list; default auto from live rtp_send.csv
  --video-ssrc LIST           Comma-separated video SSRC list; default auto from live rtp_send.csv
  --ssrc-wait-sec N           Max seconds to wait for live SSRC discovery (default: $SSRC_WAIT_SEC)
  --run-date YYYYMMDD         Shared run date directory (default: today's local date)
  --run-id ID                 Shared run id
  --repo-url URL              Trace repo URL (default: $REPO_URL)
  --branch NAME               Git branch (default: current repo branch, or origin default on fresh clone)
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
      --media-split) MEDIA_SPLIT="$2"; shift 2 ;;
      --audio-trace) AUDIO_TRACE_PATH="$2"; shift 2 ;;
      --video-trace) VIDEO_TRACE_PATH="$2"; shift 2 ;;
      --audio-delay-ms) AUDIO_DELAY_MS="$2"; shift 2 ;;
      --video-delay-ms) VIDEO_DELAY_MS="$2"; shift 2 ;;
      --audio-ssrc) AUDIO_SSRC="$2"; shift 2 ;;
      --video-ssrc) VIDEO_SSRC="$2"; shift 2 ;;
      --ssrc-wait-sec) SSRC_WAIT_SEC="$2"; shift 2 ;;
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
  [[ "$MEDIA_SPLIT" =~ ^(none|ssrc)$ ]] || die "media-split must be none or ssrc"
  if [[ "$MEDIA_SPLIT" == "none" ]]; then
    [[ -n "$TRACE_PATH" ]] || usage
    [[ -f "$TRACE_PATH" ]] || die "trace file not found: $TRACE_PATH"
  else
    if [[ -z "$VIDEO_TRACE_PATH" && -n "$TRACE_PATH" ]]; then
      VIDEO_TRACE_PATH="$TRACE_PATH"
    fi
    if [[ -n "$AUDIO_TRACE_PATH" ]]; then
      [[ -f "$AUDIO_TRACE_PATH" ]] || die "audio trace file not found: $AUDIO_TRACE_PATH"
    fi
    if [[ -n "$VIDEO_TRACE_PATH" ]]; then
      [[ -f "$VIDEO_TRACE_PATH" ]] || die "video trace file not found: $VIDEO_TRACE_PATH"
    fi
  fi
  if [[ -n "$DURATION_MIN" ]]; then
    [[ "$DURATION_MIN" =~ ^[0-9]+$ ]] || die "duration-min must be numeric"
    DURATION_SEC="$((DURATION_MIN * 60))"
  fi
  [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "duration-sec must be numeric"
  (( DURATION_SEC > 0 )) || die "duration must be greater than zero"
  [[ "$TRACE_START_DELAY_SEC" =~ ^[0-9]+$ ]] || die "trace-start-delay-sec must be numeric"
  [[ "$AUDIO_DELAY_MS" =~ ^[0-9]+$ ]] || die "audio-delay-ms must be numeric"
  [[ "$VIDEO_DELAY_MS" =~ ^[0-9]+$ ]] || die "video-delay-ms must be numeric"
  [[ "$SSRC_WAIT_SEC" =~ ^[0-9]+$ ]] || die "ssrc-wait-sec must be numeric"
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

latest_log_dir() {
  find "$1" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1
}

extract_ssrcs_from_rtp_send() {
  local rtp_send="$1"
  local is_audio="$2"
  awk -F, -v is_audio="$is_audio" '
    NR > 1 && $2 ~ /^[0-9]+$/ && $7 == is_audio {
      if (!seen[$2]++) {
        out = out sep $2
        sep = ","
      }
    }
    END { print out }
  ' "$rtp_send"
}

wait_for_live_ssrcs() {
  local deadline=$((SECONDS + SSRC_WAIT_SEC))
  local latest_dir=""
  local rtp_send=""
  local audio_auto=""
  local video_auto=""

  while (( SECONDS <= deadline )); do
    latest_dir="$(latest_log_dir "$LOG_ROOT" 2>/dev/null || true)"
    rtp_send="$latest_dir/rtp_send.csv"
    if [[ -f "$rtp_send" ]]; then
      if [[ -z "$AUDIO_SSRC" ]]; then
        audio_auto="$(extract_ssrcs_from_rtp_send "$rtp_send" 1)"
      else
        audio_auto="$AUDIO_SSRC"
      fi
      if [[ -z "$VIDEO_SSRC" ]]; then
        video_auto="$(extract_ssrcs_from_rtp_send "$rtp_send" 0)"
      else
        video_auto="$VIDEO_SSRC"
      fi
      if [[ -n "$audio_auto" && -n "$video_auto" ]]; then
        AUDIO_SSRC="$audio_auto"
        VIDEO_SSRC="$video_auto"
        log "live rtp_send=$rtp_send"
        log "audio_ssrc=$AUDIO_SSRC"
        log "video_ssrc=$VIDEO_SSRC"
        return
      fi
    fi
    sleep 1
  done

  die "could not discover live audio/video SSRCs within ${SSRC_WAIT_SEC}s; pass --audio-ssrc and --video-ssrc explicitly"
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
  log "media_split=$MEDIA_SPLIT"
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
  if [[ "$MEDIA_SPLIT" == "ssrc" ]]; then
    wait_for_live_ssrcs
    replay_cmd=(
      python3 "$REPO_DIR/trace_tools/replay_media_netem_trace.py"
      --iface "$TRACE_IFACE_RESOLVED"
      --audio-ssrc "$AUDIO_SSRC"
      --video-ssrc "$VIDEO_SSRC"
      --audio-delay-ms "$AUDIO_DELAY_MS"
      --video-delay-ms "$VIDEO_DELAY_MS"
      --sudo
      --reset-at-end
    )
    if [[ -n "$AUDIO_TRACE_PATH" ]]; then
      replay_cmd+=(--audio-trace "$AUDIO_TRACE_PATH")
    fi
    if [[ -n "$VIDEO_TRACE_PATH" ]]; then
      replay_cmd+=(--video-trace "$VIDEO_TRACE_PATH")
    fi
    "${replay_cmd[@]}" > "$trace_log" 2>&1 &
  else
    python3 "$REPO_DIR/trace_tools/replay_netem_trace.py" \
      --trace "$TRACE_PATH" \
      --iface "$TRACE_IFACE_RESOLVED" \
      --sudo \
      --reset-at-end > "$trace_log" 2>&1 &
  fi
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
media_split=$MEDIA_SPLIT
audio_trace=$AUDIO_TRACE_PATH
video_trace=$VIDEO_TRACE_PATH
audio_delay_ms=$AUDIO_DELAY_MS
video_delay_ms=$VIDEO_DELAY_MS
audio_ssrc=$AUDIO_SSRC
video_ssrc=$VIDEO_SSRC
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
