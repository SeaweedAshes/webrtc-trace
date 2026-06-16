#!/usr/bin/env bash
set -euo pipefail

LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"
WAV2LIP_DIR="${WAV2LIP_DIR:-/home/widen/Wav2Lip}"
CHECKPOINT="${CHECKPOINT:-${WAV2LIP_DIR}/checkpoints/wav2lip_gan.pth}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-19090}"
DEFAULT_WAV2LIP_PY="/home/widen/miniconda3/envs/wav2lip/bin/python"
if [[ -z "${PYTHON_BIN:-}" && -x "${DEFAULT_WAV2LIP_PY}" ]]; then
  PYTHON_BIN="${DEFAULT_WAV2LIP_PY}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
LOG_PATH="${LOG_PATH:-${LAB_DIR}/runtime/wav2lip_persistent_requests.csv}"

CUDA_VISIBLE_DEVICES_ARG=()
if [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]]; then
  CUDA_VISIBLE_DEVICES_ARG=(--cuda-visible-devices "${CUDA_VISIBLE_DEVICES}")
fi
REQUIRE_GPU_ARG=()
if [[ "${REQUIRE_GPU:-1}" == "1" ]]; then
  REQUIRE_GPU_ARG=(--require-gpu)
fi
WRITE_MP4_ARG=()
if [[ "${WRITE_MP4:-0}" == "1" ]]; then
  WRITE_MP4_ARG=(--write-mp4)
fi
DEBUG_OVERLAY_ARG=()
if [[ "${DEBUG_OVERLAY:-1}" == "0" ]]; then
  DEBUG_OVERLAY_ARG=(--no-debug-overlay)
fi
WARMUP_ARG=()
if [[ "${WARMUP:-1}" == "0" ]]; then
  WARMUP_ARG=(--no-warmup)
fi
WARMUP_TAIL_MS="${WARMUP_TAIL_MS:-400}"
WARMUP_ITERS="${WARMUP_ITERS:-3}"
WARMUP_FACE_ARG=()
if [[ -n "${WARMUP_FACE_PATH:-}" ]]; then
  WARMUP_FACE_ARG=(--warmup-face-path "${WARMUP_FACE_PATH}")
fi

mkdir -p "${LAB_DIR}/runtime"

exec "${PYTHON_BIN}" "${LAB_DIR}/scripts/wav2lip_persistent_server.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --wav2lip-dir "${WAV2LIP_DIR}" \
  --checkpoint-path "${CHECKPOINT}" \
  --log "${LOG_PATH}" \
  --warmup-tail-ms "${WARMUP_TAIL_MS}" \
  --warmup-iters "${WARMUP_ITERS}" \
  "${WARMUP_FACE_ARG[@]}" \
  "${CUDA_VISIBLE_DEVICES_ARG[@]}" \
  "${REQUIRE_GPU_ARG[@]}" \
  "${WRITE_MP4_ARG[@]}" \
  "${DEBUG_OVERLAY_ARG[@]}" \
  "${WARMUP_ARG[@]}"
