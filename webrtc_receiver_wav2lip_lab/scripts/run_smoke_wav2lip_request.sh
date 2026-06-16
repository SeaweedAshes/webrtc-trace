#!/usr/bin/env bash
set -euo pipefail

LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-19090}"
AUDIO="${AUDIO:-/home/widen/audio.wav}"
FACE="${FACE:-/home/widen/Wav2Lip/me.jpg}"
OUT_DIR="${OUT_DIR:-${LAB_DIR}/runtime/smoke}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TIMEOUT_SEC="${TIMEOUT_SEC:-120}"

mkdir -p "${OUT_DIR}"

CHUNK_AUDIO="${OUT_DIR}/audio_1s_16k_mono.wav"
ffmpeg -y -hide_banner -loglevel error \
  -i "${AUDIO}" \
  -t 1.0 \
  -ac 1 \
  -ar 16000 \
  -sample_fmt s16 \
  "${CHUNK_AUDIO}"

"${PYTHON_BIN}" "${LAB_DIR}/scripts/wav2lip_tcp_client.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --timeout-sec "${TIMEOUT_SEC}" \
  ping

"${PYTHON_BIN}" "${LAB_DIR}/scripts/wav2lip_tcp_client.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --timeout-sec "${TIMEOUT_SEC}" \
  generate \
  --request-id "smoke_$(date +%Y%m%d_%H%M%S)" \
  --audio "${CHUNK_AUDIO}" \
  --face "${FACE}" \
  --output "${OUT_DIR}/generated_smoke.mp4" \
  --fps 25
