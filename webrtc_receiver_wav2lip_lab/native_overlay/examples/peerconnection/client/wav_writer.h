#ifndef EXAMPLES_PEERCONNECTION_CLIENT_WAV_WRITER_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_WAV_WRITER_H_

#include <cstdint>
#include <string>
#include <vector>

namespace webrtc_wav2lip {

bool WritePcm16MonoWav(const std::string& path,
                       int sample_rate_hz,
                       const std::vector<int16_t>& samples,
                       std::string* error);

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_WAV_WRITER_H_
