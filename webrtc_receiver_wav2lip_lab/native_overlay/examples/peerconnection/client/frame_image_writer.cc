#include "examples/peerconnection/client/frame_image_writer.h"

#include <fstream>

namespace webrtc_wav2lip {

bool WriteArgbFrameToPpm(const std::string& path,
                         const uint8_t* argb,
                         int width,
                         int height,
                         int stride_bytes,
                         std::string* error) {
  if (!argb || width <= 0 || height <= 0 || stride_bytes < width * 4) {
    if (error) {
      *error = "invalid ARGB frame";
    }
    return false;
  }

  std::ofstream out(path, std::ios::binary);
  if (!out.is_open()) {
    if (error) {
      *error = "failed to open ppm output: " + path;
    }
    return false;
  }
  out << "P6\n" << width << " " << height << "\n255\n";
  for (int y = 0; y < height; ++y) {
    const uint8_t* row = argb + y * stride_bytes;
    for (int x = 0; x < width; ++x) {
      const uint8_t* px = row + x * 4;
      // libyuv ARGB is BGRA in memory on little-endian Cairo ARGB32 paths.
      const char rgb[3] = {
          static_cast<char>(px[2]),
          static_cast<char>(px[1]),
          static_cast<char>(px[0]),
      };
      out.write(rgb, sizeof(rgb));
    }
  }
  if (!out.good()) {
    if (error) {
      *error = "failed while writing ppm output: " + path;
    }
    return false;
  }
  return true;
}

}  // namespace webrtc_wav2lip
