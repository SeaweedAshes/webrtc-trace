#!/usr/bin/env bash
set -euo pipefail

# Receiver-side live WebRTC client. This is the only side that enables
# audio-driven talking-face concealment.

LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"
WEBRTC_BIN="${WEBRTC_BIN:-/home/widen/webrtc-receiver-wav2lip-native/src/out/ReceiverWav2Lip}"
RUN_ID="${RUN_ID:-receiver_concealment_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-${LAB_DIR}/runtime/live/${RUN_ID}}"
SERVER_HOST="${SERVER_HOST:-localhost}"
SIGNAL_PORT="${SIGNAL_PORT:-8884}"
WAV2LIP_PORT="${WAV2LIP_PORT:-19090}"
DISPLAY="${DISPLAY:-:0}"

# Temporary visible switch policy. These are intentionally easy to change.
GENERATION_RISK_MS="${GENERATION_RISK_MS:-40}"
SWITCH_MIN_GAP_MS="${SWITCH_MIN_GAP_MS:-80}"
SWITCH_GAP_MS="${SWITCH_GAP_MS:-80}"
SWITCH_SLACK_MS="${SWITCH_SLACK_MS:-0}"
RETURN_IMMEDIATE="${RETURN_IMMEDIATE:-1}"
RETURN_CONSECUTIVE_REAL_FRAMES="${RETURN_CONSECUTIVE_REAL_FRAMES:-1}"
RETURN_STABLE_MS="${RETURN_STABLE_MS:-0}"
RETURN_STABLE_GAP_MS="${RETURN_STABLE_GAP_MS:-120}"

# Face context update policy: detector every 500ms, tracker between detector
# refreshes. Face contexts are pushed every 40ms by default.
FACE_PUSH_INTERVAL_MS="${FACE_PUSH_INTERVAL_MS:-40}"
FACE_DETECT_INTERVAL_MS="${FACE_DETECT_INTERVAL_MS:-500}"

TAIL_MS="${TAIL_MS:-160}"
PREFETCH_THRESHOLD_FRAMES="${PREFETCH_THRESHOLD_FRAMES:-3}"

mkdir -p "${RUN_DIR}"

echo "[receiver-concealment] run_dir=${RUN_DIR}"
echo "[receiver-concealment] server=${SERVER_HOST}:${SIGNAL_PORT}"
echo "[receiver-concealment] wav2lip_port=${WAV2LIP_PORT}"

PORT="${WAV2LIP_PORT}" \
LOG_PATH="${RUN_DIR}/wav2lip_persistent_requests.csv" \
REQUIRE_GPU="${REQUIRE_GPU:-1}" \
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
WARMUP_TAIL_MS="${TAIL_MS}" \
LAB_DIR="${LAB_DIR}" \
"${LAB_DIR}/scripts/run_wav2lip_persistent_server.sh" \
  >"${RUN_DIR}/wav2lip_persistent_server.log" 2>&1 &
server_pid=$!

cleanup() {
  if kill -0 "${server_pid}" >/dev/null 2>&1; then
    kill -TERM "${server_pid}" >/dev/null 2>&1 || true
  fi
  wait >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in $(seq 1 90); do
  if "${LAB_DIR}/scripts/wav2lip_tcp_client.py" --port "${WAV2LIP_PORT}" \
      --timeout-sec 1 ping >"${RUN_DIR}/ping.log" 2>&1; then
    break
  fi
  sleep 1
done

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
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_PREFETCH_MS="${PLAYOUT_HEADROOM_PREFETCH_MS:--1}" \
WEBRTC_WAV2LIP_SWITCH_MIN_GAP_MS="${SWITCH_MIN_GAP_MS}" \
WEBRTC_WAV2LIP_SWITCH_GAP_MS="${SWITCH_GAP_MS}" \
WEBRTC_WAV2LIP_SWITCH_SLACK_MS="${SWITCH_SLACK_MS}" \
WEBRTC_WAV2LIP_RETURN_IMMEDIATE="${RETURN_IMMEDIATE}" \
WEBRTC_WAV2LIP_RETURN_CONSECUTIVE_REAL_FRAMES="${RETURN_CONSECUTIVE_REAL_FRAMES}" \
WEBRTC_WAV2LIP_RETURN_STABLE_MS="${RETURN_STABLE_MS}" \
WEBRTC_WAV2LIP_RETURN_STABLE_GAP_MS="${RETURN_STABLE_GAP_MS}" \
WEBRTC_WAV2LIP_PREFETCH_THRESHOLD_FRAMES="${PREFETCH_THRESHOLD_FRAMES}" \
WEBRTC_WAV2LIP_FACE_IMAGE_PATH="${FACE_IMAGE_PATH:-}" \
WEBRTC_WAV2LIP_USE_FACE_CONTEXT=1 \
WEBRTC_WAV2LIP_STREAM_FACE_CONTEXT=1 \
WEBRTC_WAV2LIP_FACE_PUSH_INTERVAL_MS="${FACE_PUSH_INTERVAL_MS}" \
WEBRTC_WAV2LIP_FACE_DETECT_INTERVAL_MS="${FACE_DETECT_INTERVAL_MS}" \
"${WEBRTC_BIN}/peerconnection_client" \
  --server="${SERVER_HOST}" --port="${SIGNAL_PORT}" --autoconnect \
  >"${RUN_DIR}/receiver_peerconnection_client.log" 2>&1
