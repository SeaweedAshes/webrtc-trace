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
LOG_PATH="${LOG_PATH:-${LAB_DIR}/runtime/wav2lip_requests.csv}"
CUDA_VISIBLE_DEVICES_ARG=()
if [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]]; then
  CUDA_VISIBLE_DEVICES_ARG=(--cuda-visible-devices "${CUDA_VISIBLE_DEVICES}")
fi
REQUIRE_GPU_ARG=()
if [[ "${REQUIRE_GPU:-0}" == "1" ]]; then
  REQUIRE_GPU_ARG=(--require-gpu)
fi

mkdir -p "${LAB_DIR}/runtime"

exec "${PYTHON_BIN}" "${LAB_DIR}/scripts/wav2lip_tcp_server.py" \
  --host "${HOST}" \
  --port "${PORT}" \
  --wav2lip-dir "${WAV2LIP_DIR}" \
  --checkpoint-path "${CHECKPOINT}" \
  --log "${LOG_PATH}" \
  "${CUDA_VISIBLE_DEVICES_ARG[@]}" \
  "${REQUIRE_GPU_ARG[@]}"
