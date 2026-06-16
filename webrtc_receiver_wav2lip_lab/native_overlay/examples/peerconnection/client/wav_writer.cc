#include "examples/peerconnection/client/wav_writer.h"

#include <cstdint>
#include <fstream>

namespace webrtc_wav2lip {
namespace {

void WriteLe16(std::ofstream& out, uint16_t value) {
  out.put(static_cast<char>(value & 0xff));
  out.put(static_cast<char>((value >> 8) & 0xff));
}

void WriteLe32(std::ofstream& out, uint32_t value) {
  out.put(static_cast<char>(value & 0xff));
  out.put(static_cast<char>((value >> 8) & 0xff));
  out.put(static_cast<char>((value >> 16) & 0xff));
  out.put(static_cast<char>((value >> 24) & 0xff));
}

}  // namespace

bool WritePcm16MonoWav(const std::string& path,
                       int sample_rate_hz,
                       const std::vector<int16_t>& samples,
                       std::string* error) {
  std::ofstream out(path, std::ios::binary);
  if (!out.is_open()) {
    if (error) {
      *error = "failed to open wav output: " + path;
    }
    return false;
  }

  const uint16_t channels = 1;
  const uint16_t bits_per_sample = 16;
  const uint16_t block_align = channels * bits_per_sample / 8;
  const uint32_t byte_rate = sample_rate_hz * block_align;
  const uint32_t data_bytes = samples.size() * sizeof(int16_t);
  const uint32_t riff_size = 36 + data_bytes;

  out.write("RIFF", 4);
  WriteLe32(out, riff_size);
  out.write("WAVE", 4);
  out.write("fmt ", 4);
  WriteLe32(out, 16);
  WriteLe16(out, 1);  // PCM
  WriteLe16(out, channels);
  WriteLe32(out, sample_rate_hz);
  WriteLe32(out, byte_rate);
  WriteLe16(out, block_align);
  WriteLe16(out, bits_per_sample);
  out.write("data", 4);
  WriteLe32(out, data_bytes);
  out.write(reinterpret_cast<const char*>(samples.data()), data_bytes);

  if (!out.good()) {
    if (error) {
      *error = "failed while writing wav output: " + path;
    }
    return false;
  }
  return true;
}

}  // namespace webrtc_wav2lip
