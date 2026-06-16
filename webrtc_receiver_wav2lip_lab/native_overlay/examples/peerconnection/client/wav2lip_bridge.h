#ifndef EXAMPLES_PEERCONNECTION_CLIENT_WAV2LIP_BRIDGE_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_WAV2LIP_BRIDGE_H_

#include <cstdint>
#include <string>

namespace webrtc_wav2lip {

struct Wav2LipRequest {
  std::string request_id;
  std::string audio_path;
  std::string face_path;
  std::string output_path;
  double fps = 25.0;
  int tail_ms = 0;
  bool force_face_detect = false;
};

struct Wav2LipResponse {
  bool ok = false;
  std::string request_id;
  std::string output_path;
  std::string frames_dir;
  std::string error;
  int64_t latency_ms = 0;
  int64_t frame_count = 0;
  int64_t context_ms = 0;
};

class Wav2LipBridge {
 public:
  Wav2LipBridge(std::string host, int port);

  Wav2LipResponse Ping() const;
  Wav2LipResponse Generate(const Wav2LipRequest& request) const;
  Wav2LipResponse SetAudioContext(const Wav2LipRequest& request) const;
  Wav2LipResponse SetFaceContext(const Wav2LipRequest& request) const;
  Wav2LipResponse TrackFaceContext(const Wav2LipRequest& request) const;
  Wav2LipResponse GenerateTail(const Wav2LipRequest& request) const;

 private:
  Wav2LipResponse SendJsonLine(const std::string& json_line) const;

  std::string host_;
  int port_;
};

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_WAV2LIP_BRIDGE_H_
