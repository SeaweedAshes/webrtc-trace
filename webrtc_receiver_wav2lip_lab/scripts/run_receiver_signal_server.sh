#!/usr/bin/env bash
set -euo pipefail

WEBRTC_BIN="${WEBRTC_BIN:-/home/widen/webrtc-receiver-wav2lip-native/src/out/ReceiverWav2Lip}"
SIGNAL_PORT="${SIGNAL_PORT:-8884}"
RUN_DIR="${RUN_DIR:-/home/widen/webrtc-receiver-wav2lip-server-logs/$(date +%Y%m%d_%H%M%S)}"

mkdir -p "${RUN_DIR}"
echo "[receiver-signal-server] port=${SIGNAL_PORT}"
echo "[receiver-signal-server] run_dir=${RUN_DIR}"

exec "${WEBRTC_BIN}/peerconnection_server" --port="${SIGNAL_PORT}" \
  >"${RUN_DIR}/peerconnection_server.log" 2>&1
