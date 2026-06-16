#include "examples/peerconnection/client/generated_frame_reader.h"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

namespace webrtc_wav2lip {
namespace {

bool ReadPpmToken(std::ifstream* input, std::string* token) {
  token->clear();
  char c = 0;
  while (input->get(c)) {
    if (std::isspace(static_cast<unsigned char>(c))) {
      continue;
    }
    if (c == '#') {
      std::string ignored;
      std::getline(*input, ignored);
      continue;
    }
    token->push_back(c);
    break;
  }
  while (input->get(c)) {
    if (std::isspace(static_cast<unsigned char>(c))) {
      break;
    }
    token->push_back(c);
  }
  return !token->empty();
}

bool LoadPpmFrame(const std::filesystem::path& path,
                  GeneratedArgbFrame* frame,
                  std::string* error) {
  std::ifstream input(path, std::ios::binary);
  if (!input.is_open()) {
    if (error) {
      *error = "failed to open generated frame: " + path.string();
    }
    return false;
  }

  std::string token;
  if (!ReadPpmToken(&input, &token) || token != "P6") {
    if (error) {
      *error = "generated frame is not binary PPM: " + path.string();
    }
    return false;
  }
  if (!ReadPpmToken(&input, &token)) {
    return false;
  }
  const int width = std::stoi(token);
  if (!ReadPpmToken(&input, &token)) {
    return false;
  }
  const int height = std::stoi(token);
  if (!ReadPpmToken(&input, &token)) {
    return false;
  }
  const int max_value = std::stoi(token);
  if (width <= 0 || height <= 0 || max_value != 255) {
    if (error) {
      *error = "unsupported generated PPM header: " + path.string();
    }
    return false;
  }

  std::vector<uint8_t> rgb(static_cast<size_t>(width) * height * 3);
  input.read(reinterpret_cast<char*>(rgb.data()), rgb.size());
  if (input.gcount() != static_cast<std::streamsize>(rgb.size())) {
    if (error) {
      *error = "generated PPM payload is truncated: " + path.string();
    }
    return false;
  }

  frame->width = width;
  frame->height = height;
  frame->bgra.resize(static_cast<size_t>(width) * height * 4);
  for (size_t src = 0, dst = 0; src < rgb.size(); src += 3, dst += 4) {
    frame->bgra[dst + 0] = rgb[src + 2];
    frame->bgra[dst + 1] = rgb[src + 1];
    frame->bgra[dst + 2] = rgb[src + 0];
    frame->bgra[dst + 3] = 255;
  }
  return true;
}

}  // namespace

bool LoadGeneratedPpmFrames(const std::string& frames_dir,
                            int max_frames,
                            std::vector<GeneratedArgbFrame>* frames,
                            std::string* error) {
  if (!frames) {
    if (error) {
      *error = "null frame output vector";
    }
    return false;
  }
  frames->clear();
  if (frames_dir.empty()) {
    if (error) {
      *error = "empty frames_dir";
    }
    return false;
  }

  std::vector<std::filesystem::path> paths;
  std::error_code fs_error;
  for (const auto& entry :
       std::filesystem::directory_iterator(frames_dir, fs_error)) {
    if (fs_error) {
      break;
    }
    if (entry.is_regular_file() && entry.path().extension() == ".ppm") {
      paths.push_back(entry.path());
    }
  }
  if (fs_error) {
    if (error) {
      *error = "failed to list frames_dir: " + fs_error.message();
    }
    return false;
  }
  std::sort(paths.begin(), paths.end());
  if (paths.empty()) {
    if (error) {
      *error = "no generated PPM frames in " + frames_dir;
    }
    return false;
  }

  const int limit = max_frames > 0
                        ? std::min<int>(max_frames, paths.size())
                        : static_cast<int>(paths.size());
  frames->reserve(limit);
  for (int i = 0; i < limit; ++i) {
    GeneratedArgbFrame frame;
    if (!LoadPpmFrame(paths[i], &frame, error)) {
      frames->clear();
      return false;
    }
    frames->push_back(std::move(frame));
  }
  return true;
}

}  // namespace webrtc_wav2lip
