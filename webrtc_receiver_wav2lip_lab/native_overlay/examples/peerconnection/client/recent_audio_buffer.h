#ifndef EXAMPLES_PEERCONNECTION_CLIENT_RECENT_AUDIO_BUFFER_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_RECENT_AUDIO_BUFFER_H_

#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "api/media_stream_interface.h"

namespace webrtc_wav2lip {

class RecentAudioBuffer : public webrtc::AudioTrackSinkInterface {
 public:
  explicit RecentAudioBuffer(int max_buffer_ms = 5000);
  ~RecentAudioBuffer() override = default;

  void OnData(const void* audio_data,
              int bits_per_sample,
              int sample_rate,
              size_t number_of_channels,
              size_t number_of_frames,
              std::optional<int64_t> absolute_capture_timestamp_ms) override;

  bool WriteRecentMonoWav(const std::string& path,
                          int duration_ms,
                          std::string* error) const;

  int sample_rate_hz() const;
  int buffered_ms() const;

 private:
  int max_buffer_ms_;
  mutable std::mutex mutex_;
  int sample_rate_hz_ = 0;
  std::deque<int16_t> mono_samples_;
};

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_RECENT_AUDIO_BUFFER_H_
