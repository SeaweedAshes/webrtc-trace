#!/usr/bin/env bash
set -euo pipefail

LAB_DIR="${LAB_DIR:-/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab}"

# Temporary smoke-test policy:
# - start generation early on a small render gap
# - switch visibly after a simple render-gap threshold
# - return immediately when the real stream renders again
GENERATION_RISK_MS="${GENERATION_RISK_MS:-40}" \
SWITCH_MIN_GAP_MS="${SWITCH_MIN_GAP_MS:-80}" \
SWITCH_GAP_MS="${SWITCH_GAP_MS:-80}" \
SWITCH_SLACK_MS="${SWITCH_SLACK_MS:-0}" \
RETURN_IMMEDIATE="${RETURN_IMMEDIATE:-1}" \
RETURN_CONSECUTIVE_REAL_FRAMES="${RETURN_CONSECUTIVE_REAL_FRAMES:-1}" \
RETURN_STABLE_MS="${RETURN_STABLE_MS:-0}" \
RETURN_STABLE_GAP_MS="${RETURN_STABLE_GAP_MS:-120}" \
PLAYOUT_HEADROOM_PREFETCH_MS="${PLAYOUT_HEADROOM_PREFETCH_MS:--1}" \
ALWAYS_ON="${ALWAYS_ON:-0}" \
TAIL_MS="${TAIL_MS:-160}" \
RUN_ID="${RUN_ID:-native_naive_concealment_$(date +%Y%m%d_%H%M%S)}" \
"${LAB_DIR}/scripts/run_native_queue_prefetch_smoke.sh"
