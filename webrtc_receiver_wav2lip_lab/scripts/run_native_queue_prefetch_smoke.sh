#!/usr/bin/env bash
set -euo pipefail

LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"
WEBRTC_BIN="${WEBRTC_BIN:-/home/widen/webrtc-receiver-wav2lip-native/src/out/ReceiverWav2Lip}"
RUN_ID="${RUN_ID:-native_queue_prefetch_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-${LAB_DIR}/runtime/verify/${RUN_ID}}"
WAV2LIP_PORT="${WAV2LIP_PORT:-19118}"
SIGNAL_PORT="${SIGNAL_PORT:-9918}"
WARMUP_SEC="${WARMUP_SEC:-8}"
FREEZE_SEC="${FREEZE_SEC:-5}"
TEST_FREEZE_AFTER_MS="${TEST_FREEZE_AFTER_MS:-3000}"
STOP_SENDER="${STOP_SENDER:-0}"
FACE_IMAGE_PATH="${FACE_IMAGE_PATH-/home/widen/Wav2Lip/me.jpg}"
USE_FACE_CONTEXT="${USE_FACE_CONTEXT:-1}"
FACE_PUSH_INTERVAL_MS="${FACE_PUSH_INTERVAL_MS:-40}"
FACE_DETECT_INTERVAL_MS="${FACE_DETECT_INTERVAL_MS:-500}"
TAIL_MS="${TAIL_MS:-160}"
PREFETCH_THRESHOLD_FRAMES="${PREFETCH_THRESHOLD_FRAMES:-${LOW_WATERMARK_FRAMES:-3}}"
ALWAYS_ON="${ALWAYS_ON:-0}"
ALWAYS_ON_INTERVAL_MS="${ALWAYS_ON_INTERVAL_MS:-400}"
ALWAYS_ON_STARTUP_DELAY_MS="${ALWAYS_ON_STARTUP_DELAY_MS:-1500}"
GENERATION_RISK_MS="${GENERATION_RISK_MS:-60}"
PLAYOUT_HEADROOM_PREFETCH_MS="${PLAYOUT_HEADROOM_PREFETCH_MS:--1}"
PLAYOUT_HEADROOM_STALE_MS="${PLAYOUT_HEADROOM_STALE_MS:-100}"
SWITCH_MIN_GAP_MS="${SWITCH_MIN_GAP_MS:-80}"
SWITCH_GAP_MS="${SWITCH_GAP_MS:-}"
SWITCH_SLACK_MS="${SWITCH_SLACK_MS:-40}"
RETURN_IMMEDIATE="${RETURN_IMMEDIATE:-0}"
RETURN_CONSECUTIVE_REAL_FRAMES="${RETURN_CONSECUTIVE_REAL_FRAMES:-3}"
RETURN_STABLE_MS="${RETURN_STABLE_MS:-120}"
RETURN_STABLE_GAP_MS="${RETURN_STABLE_GAP_MS:-80}"
DISPLAY="${DISPLAY:-:0}"

mkdir -p "${RUN_DIR}"

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill -TERM "${pid}" >/dev/null 2>&1 || true
    fi
  done
  wait >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[native-queue-smoke] run_dir=${RUN_DIR}"
echo "[native-queue-smoke] wav2lip_port=${WAV2LIP_PORT} signal_port=${SIGNAL_PORT}"
echo "[native-queue-smoke] generation_risk_ms=${GENERATION_RISK_MS} switch_min_gap_ms=${SWITCH_MIN_GAP_MS} switch_gap_ms=${SWITCH_GAP_MS:-default} switch_slack_ms=${SWITCH_SLACK_MS}"
echo "[native-queue-smoke] return_immediate=${RETURN_IMMEDIATE} return_consecutive_real_frames=${RETURN_CONSECUTIVE_REAL_FRAMES} return_stable_ms=${RETURN_STABLE_MS}"

PORT="${WAV2LIP_PORT}" \
LOG_PATH="${RUN_DIR}/wav2lip_persistent_requests.csv" \
REQUIRE_GPU="${REQUIRE_GPU:-1}" \
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
WARMUP_TAIL_MS="${TAIL_MS}" \
WARMUP_FACE_PATH="${FACE_IMAGE_PATH}" \
"${LAB_DIR}/scripts/run_wav2lip_persistent_server.sh" \
  >"${RUN_DIR}/wav2lip_persistent_server.log" 2>&1 &
pids+=("$!")

for _ in $(seq 1 90); do
  if "${LAB_DIR}/scripts/wav2lip_tcp_client.py" --port "${WAV2LIP_PORT}" \
      --timeout-sec 1 ping >"${RUN_DIR}/ping.log" 2>&1; then
    break
  fi
  sleep 1
done

"${WEBRTC_BIN}/peerconnection_server" --port="${SIGNAL_PORT}" \
  >"${RUN_DIR}/peerconnection_server.log" 2>&1 &
pids+=("$!")
sleep 1

DISPLAY="${DISPLAY}" \
WEBRTC_WAV2LIP_RUNTIME_DIR="${RUN_DIR}/native_bridge" \
WEBRTC_WAV2LIP_PORT="${WAV2LIP_PORT}" \
WEBRTC_WAV2LIP_STREAM_AUDIO_CONTEXT=1 \
WEBRTC_WAV2LIP_USE_AUDIO_CONTEXT=1 \
WEBRTC_WAV2LIP_AUDIO_CONTEXT_MS=500 \
WEBRTC_WAV2LIP_AUDIO_PUSH_INTERVAL_MS=120 \
WEBRTC_WAV2LIP_TAIL_MS="${TAIL_MS}" \
WEBRTC_WAV2LIP_FPS=25 \
WEBRTC_WAV2LIP_GENERATION_COOLDOWN_MS="${GENERATION_COOLDOWN_MS:-80}" \
WEBRTC_WAV2LIP_GENERATION_RISK_MS="${GENERATION_RISK_MS}" \
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_PREFETCH_MS="${PLAYOUT_HEADROOM_PREFETCH_MS}" \
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_STALE_MS="${PLAYOUT_HEADROOM_STALE_MS}" \
WEBRTC_WAV2LIP_SWITCH_MIN_GAP_MS="${SWITCH_MIN_GAP_MS}" \
WEBRTC_WAV2LIP_SWITCH_GAP_MS="${SWITCH_GAP_MS}" \
WEBRTC_WAV2LIP_SWITCH_SLACK_MS="${SWITCH_SLACK_MS}" \
WEBRTC_WAV2LIP_RETURN_IMMEDIATE="${RETURN_IMMEDIATE}" \
WEBRTC_WAV2LIP_RETURN_CONSECUTIVE_REAL_FRAMES="${RETURN_CONSECUTIVE_REAL_FRAMES}" \
WEBRTC_WAV2LIP_RETURN_STABLE_MS="${RETURN_STABLE_MS}" \
WEBRTC_WAV2LIP_RETURN_STABLE_GAP_MS="${RETURN_STABLE_GAP_MS}" \
WEBRTC_WAV2LIP_PREFETCH_THRESHOLD_FRAMES="${PREFETCH_THRESHOLD_FRAMES}" \
WEBRTC_WAV2LIP_ALWAYS_ON="${ALWAYS_ON}" \
WEBRTC_WAV2LIP_ALWAYS_ON_INTERVAL_MS="${ALWAYS_ON_INTERVAL_MS}" \
WEBRTC_WAV2LIP_ALWAYS_ON_STARTUP_DELAY_MS="${ALWAYS_ON_STARTUP_DELAY_MS}" \
WEBRTC_WAV2LIP_TEST_FREEZE_AFTER_MS="${TEST_FREEZE_AFTER_MS}" \
WEBRTC_WAV2LIP_FACE_IMAGE_PATH="${FACE_IMAGE_PATH}" \
WEBRTC_WAV2LIP_USE_FACE_CONTEXT="${USE_FACE_CONTEXT}" \
WEBRTC_WAV2LIP_STREAM_FACE_CONTEXT=1 \
WEBRTC_WAV2LIP_FACE_PUSH_INTERVAL_MS="${FACE_PUSH_INTERVAL_MS}" \
WEBRTC_WAV2LIP_FACE_DETECT_INTERVAL_MS="${FACE_DETECT_INTERVAL_MS}" \
"${WEBRTC_BIN}/peerconnection_client" \
  --server=localhost --port="${SIGNAL_PORT}" --autoconnect \
  >"${RUN_DIR}/receiver.log" 2>&1 &
receiver_pid="$!"
pids+=("${receiver_pid}")

sleep 1

DISPLAY="${DISPLAY}" \
"${WEBRTC_BIN}/peerconnection_client" \
  --server=localhost --port="${SIGNAL_PORT}" --autoconnect --autocall \
  >"${RUN_DIR}/sender.log" 2>&1 &
sender_pid="$!"
pids+=("${sender_pid}")

sleep "${WARMUP_SEC}"
if [[ "${STOP_SENDER}" == "1" ]]; then
  echo "[native-queue-smoke] stopping sender pid=${sender_pid} for ${FREEZE_SEC}s"
  kill -STOP "${sender_pid}"
  sleep "${FREEZE_SEC}"
  kill -CONT "${sender_pid}" >/dev/null 2>&1 || true
else
  echo "[native-queue-smoke] using receiver-side test freeze after ${TEST_FREEZE_AFTER_MS}ms"
  sleep "${FREEZE_SEC}"
fi
sleep 1

echo "[native-queue-smoke] request counts:"
if [[ -f "${RUN_DIR}/wav2lip_persistent_requests.csv" ]]; then
  awk -F, '{ count[$2]++ } END { for (k in count) print "  " k "=" count[k] }' \
    "${RUN_DIR}/wav2lip_persistent_requests.csv" | sort
  echo "[native-queue-smoke] generate_tail rows:"
  awk -F, '$2=="generate_tail" { print }' \
    "${RUN_DIR}/wav2lip_persistent_requests.csv"
fi

echo "[native-queue-smoke] playback ready files:"
find "${RUN_DIR}/native_bridge" -name native_playback_ready.txt -print \
  -exec cat {} \; 2>/dev/null || true
