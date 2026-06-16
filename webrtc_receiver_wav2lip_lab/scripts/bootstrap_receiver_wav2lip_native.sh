#!/usr/bin/env bash
set -euo pipefail

# Fresh WebRTC checkout for the receiver-side audio-driven talking-face
# concealment prototype. Existing trace/replay and wav2lip lab checkouts are not
# modified by this script.

WORKDIR="${WORKDIR:-/home/widen/webrtc-receiver-wav2lip-native}"
LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"
DEPOT_TOOLS="${DEPOT_TOOLS:-/home/widen/depot_tools}"
BUILD_DIR="${BUILD_DIR:-out/ReceiverWav2Lip}"
TARGETS="${TARGETS:-peerconnection_client peerconnection_server}"

# Known-compatible WebRTC revision from the previous native prototype. Override
# this if we intentionally want to move to a newer upstream revision.
WEBRTC_REVISION="${WEBRTC_REVISION:-f172c4f6e0ced37758d85e060e66a3e06166d9f6}"
GCLIENT_JOBS="${GCLIENT_JOBS:-4}"

log() {
  printf '[receiver-wav2lip-bootstrap] %s\n' "$*"
}

die() {
  printf '[receiver-wav2lip-bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ ! -d "${DEPOT_TOOLS}" ]]; then
  die "depot_tools not found at ${DEPOT_TOOLS}"
fi

export PATH="${DEPOT_TOOLS}:${PATH}"
mkdir -p "${WORKDIR}"

if [[ ! -f "${WORKDIR}/.gclient" ]]; then
  log "fetching fresh WebRTC checkout into ${WORKDIR}"
  cd "${WORKDIR}"
  fetch --nohooks webrtc
else
  log "using existing checkout at ${WORKDIR}"
fi

cd "${WORKDIR}"
[[ -d src ]] || die "src directory missing under ${WORKDIR}"

log "syncing WebRTC revision ${WEBRTC_REVISION}"
gclient sync --nohooks --jobs "${GCLIENT_JOBS}" --revision "src@${WEBRTC_REVISION}"
gclient runhooks

cd "${WORKDIR}/src"
log "applying receiver Wav2Lip overlay"
python3 "${LAB_DIR}/scripts/apply_native_overlay.py" \
  --src-dir "${WORKDIR}/src" \
  --lab-dir "${LAB_DIR}"

GN_ARGS="${GN_ARGS:-rtc_build_examples=true rtc_include_tests=false is_debug=false use_sysroot=false treat_warnings_as_errors=false}"
log "generating ${BUILD_DIR}"
gn gen "${BUILD_DIR}" --args="${GN_ARGS}"

log "building ${TARGETS}"
autoninja -C "${BUILD_DIR}" ${TARGETS}

log "ready"
log "  lab_dir=${LAB_DIR}"
log "  checkout=${WORKDIR}"
log "  binaries=${WORKDIR}/src/${BUILD_DIR}"
