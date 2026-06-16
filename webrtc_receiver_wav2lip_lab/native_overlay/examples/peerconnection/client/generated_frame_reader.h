#ifndef EXAMPLES_PEERCONNECTION_CLIENT_GENERATED_FRAME_READER_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_GENERATED_FRAME_READER_H_

#include <cstdint>
#include <string>
#include <vector>

namespace webrtc_wav2lip {

struct GeneratedArgbFrame {
  int width = 0;
  int height = 0;
  std::vector<uint8_t> bgra;
};

bool LoadGeneratedPpmFrames(const std::string& frames_dir,
                            int max_frames,
                            std::vector<GeneratedArgbFrame>* frames,
                            std::string* error);

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_GENERATED_FRAME_READER_H_
