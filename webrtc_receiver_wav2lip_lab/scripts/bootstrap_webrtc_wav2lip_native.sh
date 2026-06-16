#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/widen/webrtc-receiver-wav2lip-native}"
LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"
DEPOT_TOOLS="${DEPOT_TOOLS:-/home/widen/depot_tools}"
BUILD_DIR="${BUILD_DIR:-out/ReceiverWav2Lip}"
TARGETS="${TARGETS:-peerconnection_client peerconnection_server}"

log() {
  printf '[webrtc-wav2lip-bootstrap] %s\n' "$*"
}

die() {
  printf '[webrtc-wav2lip-bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ ! -d "${DEPOT_TOOLS}" ]]; then
  die "depot_tools not found at ${DEPOT_TOOLS}"
fi

export PATH="${DEPOT_TOOLS}:${PATH}"
mkdir -p "${WORKDIR}"

if [[ ! -f "${WORKDIR}/.gclient" ]]; then
  log "initializing new WebRTC checkout in ${WORKDIR}"
  cd "${WORKDIR}"
  fetch --nohooks webrtc
else
  log "using existing checkout at ${WORKDIR}"
fi

cd "${WORKDIR}"
if [[ ! -d src ]]; then
  die "src directory missing after fetch"
fi

log "syncing WebRTC deps"
gclient sync --nohooks --jobs "${GCLIENT_JOBS:-4}"
gclient runhooks

cd "${WORKDIR}/src"

log "applying native overlay and BUILD.gn patch"
python3 "${LAB_DIR}/scripts/apply_native_overlay.py" \
  --src-dir "${WORKDIR}/src" \
  --lab-dir "${LAB_DIR}"

GN_ARGS="${GN_ARGS:-rtc_build_examples=true rtc_include_tests=false is_debug=false use_sysroot=false treat_warnings_as_errors=false}"
log "generating ${BUILD_DIR}"
gn gen "${BUILD_DIR}" --args="${GN_ARGS}"

log "building ${TARGETS}"
autoninja -C "${BUILD_DIR}" ${TARGETS}

log "ready: ${WORKDIR}/src/${BUILD_DIR}"
