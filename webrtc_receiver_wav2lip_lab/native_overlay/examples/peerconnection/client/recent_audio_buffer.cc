#include "examples/peerconnection/client/recent_audio_buffer.h"

#include <algorithm>
#include <cstddef>

#include "examples/peerconnection/client/wav_writer.h"

namespace webrtc_wav2lip {

RecentAudioBuffer::RecentAudioBuffer(int max_buffer_ms)
    : max_buffer_ms_(max_buffer_ms) {}

void RecentAudioBuffer::OnData(
    const void* audio_data,
    int bits_per_sample,
    int sample_rate,
    size_t number_of_channels,
    size_t number_of_frames,
    std::optional<int64_t> /* absolute_capture_timestamp_ms */) {
  if (!audio_data || bits_per_sample != 16 || sample_rate <= 0 ||
      number_of_channels == 0 || number_of_frames == 0) {
    return;
  }

  const int16_t* input = static_cast<const int16_t*>(audio_data);
  std::vector<int16_t> mono;
  mono.reserve(number_of_frames);
  for (size_t frame = 0; frame < number_of_frames; ++frame) {
    int32_t sum = 0;
    for (size_t ch = 0; ch < number_of_channels; ++ch) {
      sum += input[frame * number_of_channels + ch];
    }
    mono.push_back(static_cast<int16_t>(sum / number_of_channels));
  }

  std::lock_guard<std::mutex> lock(mutex_);
  if (sample_rate_hz_ != sample_rate) {
    mono_samples_.clear();
    sample_rate_hz_ = sample_rate;
  }
  for (int16_t sample : mono) {
    mono_samples_.push_back(sample);
  }

  const size_t max_samples =
      static_cast<size_t>(sample_rate_hz_) * max_buffer_ms_ / 1000;
  while (mono_samples_.size() > max_samples) {
    mono_samples_.pop_front();
  }
}

bool RecentAudioBuffer::WriteRecentMonoWav(const std::string& path,
                                           int duration_ms,
                                           std::string* error) const {
  std::lock_guard<std::mutex> lock(mutex_);
  if (sample_rate_hz_ <= 0 || mono_samples_.empty()) {
    if (error) {
      *error = "no audio buffered";
    }
    return false;
  }
  const size_t requested =
      static_cast<size_t>(sample_rate_hz_) * duration_ms / 1000;
  const size_t count = std::min(requested, mono_samples_.size());
  std::vector<int16_t> recent;
  recent.reserve(count);
  auto start = mono_samples_.end() - static_cast<std::ptrdiff_t>(count);
  for (auto it = start; it != mono_samples_.end(); ++it) {
    recent.push_back(*it);
  }
  return WritePcm16MonoWav(path, sample_rate_hz_, recent, error);
}

int RecentAudioBuffer::sample_rate_hz() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return sample_rate_hz_;
}

int RecentAudioBuffer::buffered_ms() const {
  std::lock_guard<std::mutex> lock(mutex_);
  if (sample_rate_hz_ <= 0) {
    return 0;
  }
  return static_cast<int>(mono_samples_.size() * 1000 / sample_rate_hz_);
}

}  // namespace webrtc_wav2lip
