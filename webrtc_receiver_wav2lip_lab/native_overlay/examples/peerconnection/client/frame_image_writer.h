#ifndef EXAMPLES_PEERCONNECTION_CLIENT_FRAME_IMAGE_WRITER_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_FRAME_IMAGE_WRITER_H_

#include <cstdint>
#include <string>

namespace webrtc_wav2lip {

// Writes a PPM image from WebRTC's little-endian ARGB memory layout.
// The Python Wav2Lip server converts this to PNG before invoking Wav2Lip.
bool WriteArgbFrameToPpm(const std::string& path,
                         const uint8_t* argb,
                         int width,
                         int height,
                         int stride_bytes,
                         std::string* error);

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_FRAME_IMAGE_WRITER_H_
