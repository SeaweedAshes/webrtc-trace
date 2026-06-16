#include "examples/peerconnection/client/concealment_policy.h"

namespace webrtc_wav2lip {

ConcealmentPolicy::ConcealmentPolicy(TriggerConfig trigger_config,
                                     RecoveryConfig recovery_config)
    : trigger_config_(trigger_config), recovery_config_(recovery_config) {}

PolicyDecision ConcealmentPolicy::Evaluate(
    ConcealmentState current,
    const ReceiverSignals& signals) const {
  PolicyDecision decision;
  decision.next_state = current;

  std::string reason;
  switch (current) {
    case ConcealmentState::kNormal:
      if (ShouldTrigger(signals, &reason)) {
        decision.next_state = ConcealmentState::kRisk;
        decision.reason = reason;
      }
      break;
    case ConcealmentState::kRisk:
      if (ShouldTrigger(signals, &reason)) {
        decision.next_state = ConcealmentState::kGenerated;
        decision.trigger_generation = true;
        decision.reason = reason;
      } else {
        decision.next_state = ConcealmentState::kNormal;
        decision.reason = "risk_cleared";
      }
      break;
    case ConcealmentState::kGenerated:
      if (ShouldRecover(signals, &reason)) {
        decision.next_state = ConcealmentState::kRecovery;
        decision.start_crossfade = recovery_config_.enable_crossfade;
        decision.reason = reason;
      }
      break;
    case ConcealmentState::kRecovery:
      if (ShouldRecover(signals, &reason)) {
        decision.next_state = ConcealmentState::kNormal;
        decision.reason = "recovery_complete";
      } else if (ShouldTrigger(signals, &reason)) {
        decision.next_state = ConcealmentState::kGenerated;
        decision.trigger_generation = true;
        decision.reason = "retrigger_during_recovery:" + reason;
      }
      break;
  }
  return decision;
}

bool ConcealmentPolicy::ShouldTrigger(const ReceiverSignals& signals,
                                      std::string* reason) const {
  if (trigger_config_.enable_packet_gap &&
      signals.video_packet_gap_ms >= trigger_config_.packet_gap_ms) {
    *reason = "packet_gap";
    return true;
  }
  if (trigger_config_.enable_frame_decode_gap &&
      signals.frame_decode_gap_ms >= trigger_config_.frame_decode_gap_ms) {
    *reason = "frame_decode_gap";
    return true;
  }
  if (trigger_config_.enable_render_gap &&
      signals.render_gap_ms >= trigger_config_.render_gap_ms) {
    *reason = "render_gap";
    return true;
  }
  if (trigger_config_.enable_playout_headroom &&
      signals.playout_headroom_ms <= trigger_config_.playout_headroom_ms) {
    *reason = "playout_headroom";
    return true;
  }
  return false;
}

bool ConcealmentPolicy::ShouldRecover(const ReceiverSignals& signals,
                                      std::string* reason) const {
  const bool frame_count_ok =
      !recovery_config_.enable_consecutive_real_frames ||
      signals.consecutive_stable_real_frames >=
          recovery_config_.consecutive_real_frames;
  const bool stable_duration_ok =
      signals.stable_real_duration_ms >= recovery_config_.stable_duration_ms;
  const bool stable_gap_ok =
      signals.render_gap_ms > 0 &&
      signals.render_gap_ms <= recovery_config_.stable_gap_ms;
  if (frame_count_ok && stable_duration_ok && stable_gap_ok) {
    *reason = "stable_real_stream";
    return true;
  }
  return false;
}

const char* ToString(ConcealmentState state) {
  switch (state) {
    case ConcealmentState::kNormal:
      return "NORMAL";
    case ConcealmentState::kRisk:
      return "RISK";
    case ConcealmentState::kGenerated:
      return "GENERATED";
    case ConcealmentState::kRecovery:
      return "RECOVERY";
  }
  return "UNKNOWN";
}

}  // namespace webrtc_wav2lip
