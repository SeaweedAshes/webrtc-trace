#ifndef EXAMPLES_PEERCONNECTION_CLIENT_CONCEALMENT_POLICY_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_CONCEALMENT_POLICY_H_

#include <cstdint>
#include <string>

namespace webrtc_wav2lip {

enum class ConcealmentState {
  kNormal,
  kRisk,
  kGenerated,
  kRecovery,
};

struct TriggerConfig {
  bool enable_render_gap = true;
  bool enable_packet_gap = true;
  bool enable_frame_decode_gap = true;
  bool enable_playout_headroom = false;
  int64_t packet_gap_ms = 120;
  int64_t frame_decode_gap_ms = 120;
  int64_t render_gap_ms = 180;
  int64_t playout_headroom_ms = 0;
};

struct RecoveryConfig {
  bool enable_consecutive_real_frames = true;
  int consecutive_real_frames = 5;
  int64_t stable_gap_ms = 60;
  int64_t stable_duration_ms = 200;
  bool enable_crossfade = true;
  int64_t crossfade_ms = 120;
};

struct ReceiverSignals {
  int64_t video_packet_gap_ms = 0;
  int64_t frame_decode_gap_ms = 0;
  int64_t render_gap_ms = 0;
  int64_t playout_headroom_ms = 1000;
  int consecutive_stable_real_frames = 0;
  int64_t stable_real_duration_ms = 0;
};

struct PolicyDecision {
  ConcealmentState next_state = ConcealmentState::kNormal;
  bool trigger_generation = false;
  bool start_crossfade = false;
  std::string reason;
};

class ConcealmentPolicy {
 public:
  ConcealmentPolicy(TriggerConfig trigger_config,
                    RecoveryConfig recovery_config);

  PolicyDecision Evaluate(ConcealmentState current,
                          const ReceiverSignals& signals) const;

 private:
  bool ShouldTrigger(const ReceiverSignals& signals,
                     std::string* reason) const;
  bool ShouldRecover(const ReceiverSignals& signals,
                     std::string* reason) const;

  TriggerConfig trigger_config_;
  RecoveryConfig recovery_config_;
};

const char* ToString(ConcealmentState state);

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_CONCEALMENT_POLICY_H_
