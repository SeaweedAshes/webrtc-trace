#!/usr/bin/env bash
set -euo pipefail

# Sender-side live WebRTC client. It does not start Wav2Lip and does not set any
# WEBRTC_WAV2LIP_* environment variables. It only joins the call and sends media.

WEBRTC_BIN="${WEBRTC_BIN:-/home/widen/webrtc-receiver-wav2lip-native/src/out/ReceiverWav2Lip}"
SERVER_HOST="${SERVER_HOST:?set SERVER_HOST to the receiver/signaling host}"
SIGNAL_PORT="${SIGNAL_PORT:-8884}"
DISPLAY="${DISPLAY:-:0}"
RUN_DIR="${RUN_DIR:-/home/widen/webrtc-receiver-wav2lip-sender-logs/$(date +%Y%m%d_%H%M%S)}"

mkdir -p "${RUN_DIR}"

echo "[sender-plain] server=${SERVER_HOST}:${SIGNAL_PORT}"
echo "[sender-plain] run_dir=${RUN_DIR}"

DISPLAY="${DISPLAY}" \
"${WEBRTC_BIN}/peerconnection_client" \
  --server="${SERVER_HOST}" --port="${SIGNAL_PORT}" --autoconnect --autocall \
  >"${RUN_DIR}/sender_peerconnection_client.log" 2>&1
