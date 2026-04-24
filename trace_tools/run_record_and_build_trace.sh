#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TRACE_LAB_SRC="${TRACE_LAB_SRC:-$REPO_ROOT/webrtc-trace-lab/src}"
TRACE_LAB_BIN_DIR="${TRACE_LAB_BIN_DIR:-$TRACE_LAB_SRC/out/Trace}"
CLIENT_BIN="$TRACE_LAB_BIN_DIR/peerconnection_client"
SERVER_BIN="$TRACE_LAB_BIN_DIR/peerconnection_server"
TRACE_REPLAY_TOOL="$REPO_ROOT/trace_tools/replay_netem_trace.py"
TRACE_BUILD_TOOL="$REPO_ROOT/trace_tools/build_trace_from_logs.py"

SETUP_NETNS_SCRIPT="${SETUP_NETNS_SCRIPT:-$HOME/pilot_experiment/scripts/setup_netns.sh}"
SETUP_PULSE_SCRIPT="${SETUP_PULSE_SCRIPT:-$HOME/pilot_experiment/scripts/setup_pulse.sh}"
REF_AUDIO="${REF_AUDIO:-$HOME/pilot_experiment/audio/reference_speech.wav}"

NS_SEND="pc-send"
NS_RECV="pc-recv"
VETH_SEND="veth-send"
IP_SEND="10.0.0.1"
PORT=8888
WINDOW_MS=100
RECEIVER_OFFSET_MS=0
RUN_DIR=""
RECORD_TRACE=""
SKIP_BUILD=0

usage() {
  cat <<EOF
Usage: $0 --run-dir DIR [options]

Options:
  --record-trace PATH         replay this trace during the recording run
  --skip-build                skip generated_trace.csv creation
  --window-ms N               aggregation window for build_trace_from_logs.py
  --receiver-offset-ms N      optional sender/receiver offset for freeze notes
  --ref-audio PATH            wav file to play into the sender
  --port N                    signaling port (default: 8888)
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --record-trace) RECORD_TRACE="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --window-ms) WINDOW_MS="$2"; shift 2 ;;
    --receiver-offset-ms) RECEIVER_OFFSET_MS="$2"; shift 2 ;;
    --ref-audio) REF_AUDIO="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$RUN_DIR" ]] || usage
[[ -x "$CLIENT_BIN" ]] || { echo "missing client binary: $CLIENT_BIN" >&2; exit 1; }
[[ -x "$SERVER_BIN" ]] || { echo "missing server binary: $SERVER_BIN" >&2; exit 1; }
[[ -f "$TRACE_BUILD_TOOL" ]] || { echo "missing build tool: $TRACE_BUILD_TOOL" >&2; exit 1; }
[[ -f "$REF_AUDIO" ]] || { echo "missing ref audio: $REF_AUDIO" >&2; exit 1; }
if [[ -n "$RECORD_TRACE" ]]; then
  [[ -f "$TRACE_REPLAY_TOOL" ]] || { echo "missing replay tool: $TRACE_REPLAY_TOOL" >&2; exit 1; }
  [[ -f "$RECORD_TRACE" ]] || { echo "missing trace file: $RECORD_TRACE" >&2; exit 1; }
fi

SEND_SINK="pilot-send-src"
SEND_SRC="${SEND_SINK}.monitor"
SEND_PLAYOUT="pilot-send-playout"
RECV_SINK="pilot-recv-sink"
RECV_MON="${RECV_SINK}.monitor"
RECV_MIC="pilot-recv-mic"
RECV_MIC_SRC="${RECV_MIC}.monitor"

PG_SERVER=""
PG_RECEIVER=""
PG_SENDER=""
PG_PAPLAY=""
PG_PAREC=""
PG_TRACE=""

mkdir -p "$RUN_DIR"

write_status() { echo "$1" > "$RUN_DIR/run.status"; }

start_in_ns() {
  local ns="$1"; shift
  local logfile="$1"; shift
  local ps="${PULSE_SINK:-}"
  local psrc="${PULSE_SOURCE:-}"
  local wld="${WEBRTC_LOG_DIR:-}"
  local xdg="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  setsid sudo -n ip netns exec "$ns" \
    sudo -n -u "$USER" \
      env HOME="$HOME" USER="$USER" LOGNAME="$USER" \
        XDG_RUNTIME_DIR="$xdg" \
        PULSE_SINK="$ps" PULSE_SOURCE="$psrc" \
        WEBRTC_LOG_DIR="$wld" \
        "$@" >"$logfile" 2>&1 &
  echo $!
}

start_pg() {
  local logfile="$1"; shift
  setsid bash -c "exec \"\$@\" >\"$logfile\" 2>&1" _ "$@" &
  echo $!
}

ensure_prereqs() {
  if ! ip netns list | grep -q "^${NS_SEND}\b"; then
    "$SETUP_NETNS_SCRIPT" up
  fi
  if ! pactl list short sinks | awk '{print $2}' | grep -qx "$SEND_SINK"; then
    "$SETUP_PULSE_SCRIPT" up
  fi
}

resolve_log_dir() {
  local root="$1"
  if compgen -G "$root/*.csv" > /dev/null; then
    echo "$root"
    return
  fi
  find "$root" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1
}

cleanup() {
  for pg in "$PG_TRACE" "$PG_PAREC" "$PG_PAPLAY" "$PG_SENDER" "$PG_RECEIVER" "$PG_SERVER"; do
    if [[ -n "$pg" ]]; then
      kill -TERM -- "-$pg" 2>/dev/null || true
      sudo -n kill -TERM -- "-$pg" 2>/dev/null || true
    fi
  done
  sleep 0.3
  for pg in "$PG_TRACE" "$PG_PAREC" "$PG_PAPLAY" "$PG_SENDER" "$PG_RECEIVER" "$PG_SERVER"; do
    if [[ -n "$pg" ]]; then
      kill -KILL -- "-$pg" 2>/dev/null || true
      sudo -n kill -KILL -- "-$pg" 2>/dev/null || true
    fi
  done
  sudo -n ip netns exec "$NS_SEND" tc qdisc replace dev "$VETH_SEND" root netem delay 0ms 2>/dev/null || true
}
trap cleanup EXIT INT TERM

fail() {
  echo "ERROR: $*" >&2
  write_status "error:$*"
  exit 1
}

ensure_prereqs
write_status "running"

sudo -n ip netns exec "$NS_SEND" tc qdisc replace dev "$VETH_SEND" root netem delay 0ms || fail "qdisc reset"

for i in {1..10}; do
  if ! sudo -n ip netns exec "$NS_SEND" ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    break
  fi
  sleep 0.3
done

PG_SERVER=$(start_in_ns "$NS_SEND" "$RUN_DIR/server.log" "$SERVER_BIN" --port="$PORT")
sleep 0.5
sudo -n ip netns exec "$NS_SEND" ss -ltn 2>/dev/null | grep -q ":$PORT " || fail "server not listening"

WEBRTC_LOG_DIR="$RUN_DIR/webrtc_logs_receiver" \
PULSE_SINK="$RECV_SINK" PULSE_SOURCE="$RECV_MIC_SRC" \
PG_RECEIVER=$(start_in_ns "$NS_RECV" "$RUN_DIR/receiver.log" \
  xvfb-run -a --server-args="-screen 0 640x480x24" \
  "$CLIENT_BIN" --server="$IP_SEND" --port="$PORT" --autoconnect)
sleep 2

WEBRTC_LOG_DIR="$RUN_DIR/webrtc_logs_sender" \
PULSE_SOURCE="$SEND_SRC" PULSE_SINK="$SEND_PLAYOUT" \
PG_SENDER=$(start_in_ns "$NS_SEND" "$RUN_DIR/sender.log" \
  xvfb-run -a --server-args="-screen 0 640x480x24" \
  "$CLIENT_BIN" --server=localhost --port="$PORT" --autoconnect --autocall)
sleep 2

RECV_SINK_IDX=$(pactl list short sinks | awk -v n="$RECV_SINK" '$2==n {print $1; exit}')
SEND_SRC_IDX=$(pactl list short sources | awk -v n="$SEND_SRC" '$2==n {print $1; exit}')
[[ -n "$RECV_SINK_IDX" && -n "$SEND_SRC_IDX" ]] || fail "pulse index lookup"

deadline=$((SECONDS + 15))
have_si=0
have_so=0
while :; do
  [[ $have_si -eq 0 ]] && pactl list short sink-inputs 2>/dev/null | awk -v i="$RECV_SINK_IDX" '$2==i{f=1}END{exit !f}' && have_si=1
  [[ $have_so -eq 0 ]] && pactl list short source-outputs 2>/dev/null | awk -v i="$SEND_SRC_IDX" '$2==i{f=1}END{exit !f}' && have_so=1
  [[ $have_si -eq 1 && $have_so -eq 1 ]] && break
  (( SECONDS >= deadline )) && fail "pulse routing timeout (si=$have_si so=$have_so)"
  sleep 0.25
done

PG_PAREC=$(start_pg "$RUN_DIR/parec.log" \
  parec --device="$RECV_MON" --file-format=wav --rate=48000 --channels=1 --format=s16le "$RUN_DIR/received.wav")
sleep 0.3

if [[ -n "$RECORD_TRACE" ]]; then
  PG_TRACE=$(start_pg "$RUN_DIR/trace_replay.log" \
    python3 "$TRACE_REPLAY_TOOL" --trace "$RECORD_TRACE" --namespace "$NS_SEND" --iface "$VETH_SEND" --reset-at-end)
  sleep 0.2
fi

PG_PAPLAY=$(start_pg "$RUN_DIR/paplay.log" paplay --device="$SEND_SINK" "$REF_AUDIO")

wait_count=0
while kill -0 "$PG_PAPLAY" 2>/dev/null; do
  sleep 0.5
  wait_count=$((wait_count + 1))
  [[ $wait_count -gt 30 ]] && fail "paplay timeout"
done

sleep 1.5

kill -TERM -- "-$PG_PAREC" 2>/dev/null || true
sleep 0.3
kill -KILL -- "-$PG_PAREC" 2>/dev/null || true

[[ -s "$RUN_DIR/received.wav" ]] || fail "received.wav missing or empty"

peak=$(python3 -c "
import struct, wave
with wave.open('$RUN_DIR/received.wav', 'rb') as wav:
    data = wav.readframes(wav.getnframes())
    samples = struct.unpack(f'<{len(data)//2}h', data)
    print(max(abs(x) for x in samples) if samples else 0)
")
[[ "$peak" -ge 500 ]] || fail "received audio is silent (peak=$peak)"

SENDER_LOG_DIR="$(resolve_log_dir "$RUN_DIR/webrtc_logs_sender")"
RECEIVER_LOG_DIR="$(resolve_log_dir "$RUN_DIR/webrtc_logs_receiver")"
[[ -n "$SENDER_LOG_DIR" && -d "$SENDER_LOG_DIR" ]] || fail "sender log dir not found"
[[ -n "$RECEIVER_LOG_DIR" && -d "$RECEIVER_LOG_DIR" ]] || fail "receiver log dir not found"

echo "$SENDER_LOG_DIR" > "$RUN_DIR/sender_log_dir.txt"
echo "$RECEIVER_LOG_DIR" > "$RUN_DIR/receiver_log_dir.txt"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  python3 "$TRACE_BUILD_TOOL" \
    --sender-log-dir "$SENDER_LOG_DIR" \
    --receiver-log-dir "$RECEIVER_LOG_DIR" \
    --output "$RUN_DIR/generated_trace.csv" \
    --window-ms "$WINDOW_MS" \
    --receiver-time-offset-ms "$RECEIVER_OFFSET_MS" \
    >"$RUN_DIR/trace_build.log" 2>&1 || fail "trace build failed"
fi

write_status "ok"
echo "run_dir=$RUN_DIR"
echo "sender_log_dir=$SENDER_LOG_DIR"
echo "receiver_log_dir=$RECEIVER_LOG_DIR"
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "generated_trace=$RUN_DIR/generated_trace.csv"
fi
