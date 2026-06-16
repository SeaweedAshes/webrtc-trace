#include "examples/peerconnection/client/wav2lip_bridge.h"

#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstring>
#include <sstream>
#include <string>
#include <utility>

namespace webrtc_wav2lip {
namespace {

std::string JsonEscape(const std::string& value) {
  std::string out;
  out.reserve(value.size() + 8);
  for (char c : value) {
    switch (c) {
      case '\\':
        out += "\\\\";
        break;
      case '"':
        out += "\\\"";
        break;
      case '\n':
        out += "\\n";
        break;
      case '\r':
        out += "\\r";
        break;
      case '\t':
        out += "\\t";
        break;
      default:
        out += c;
        break;
    }
  }
  return out;
}

std::string ExtractString(const std::string& json, const std::string& key) {
  const std::string needle = "\"" + key + "\":";
  size_t pos = json.find(needle);
  if (pos == std::string::npos) {
    return "";
  }
  pos = json.find('"', pos + needle.size());
  if (pos == std::string::npos) {
    return "";
  }
  size_t end = json.find('"', pos + 1);
  if (end == std::string::npos) {
    return "";
  }
  return json.substr(pos + 1, end - pos - 1);
}

int64_t ExtractInt64(const std::string& json, const std::string& key) {
  const std::string needle = "\"" + key + "\":";
  size_t pos = json.find(needle);
  if (pos == std::string::npos) {
    return 0;
  }
  pos += needle.size();
  while (pos < json.size() && json[pos] == ' ') {
    ++pos;
  }
  size_t end = pos;
  while (end < json.size() && (json[end] == '-' || (json[end] >= '0' && json[end] <= '9'))) {
    ++end;
  }
  if (end == pos) {
    return 0;
  }
  return std::stoll(json.substr(pos, end - pos));
}

}  // namespace

Wav2LipBridge::Wav2LipBridge(std::string host, int port)
    : host_(std::move(host)), port_(port) {}

Wav2LipResponse Wav2LipBridge::Ping() const {
  return SendJsonLine("{\"type\":\"ping\"}\n");
}

Wav2LipResponse Wav2LipBridge::Generate(const Wav2LipRequest& request) const {
  std::ostringstream json;
  json << "{\"type\":\"generate\""
       << ",\"request_id\":\"" << JsonEscape(request.request_id) << "\""
       << ",\"audio_path\":\"" << JsonEscape(request.audio_path) << "\""
       << ",\"face_path\":\"" << JsonEscape(request.face_path) << "\""
       << ",\"output_path\":\"" << JsonEscape(request.output_path) << "\""
       << ",\"fps\":" << request.fps << "}\n";
  return SendJsonLine(json.str());
}

Wav2LipResponse Wav2LipBridge::SetAudioContext(
    const Wav2LipRequest& request) const {
  std::ostringstream json;
  json << "{\"type\":\"set_audio_context\""
       << ",\"request_id\":\"" << JsonEscape(request.request_id) << "\""
       << ",\"audio_path\":\"" << JsonEscape(request.audio_path) << "\"}\n";
  return SendJsonLine(json.str());
}

Wav2LipResponse Wav2LipBridge::SetFaceContext(
    const Wav2LipRequest& request) const {
  std::ostringstream json;
  json << "{\"type\":\"set_face_context\""
       << ",\"request_id\":\"" << JsonEscape(request.request_id) << "\""
       << ",\"face_path\":\"" << JsonEscape(request.face_path) << "\"}\n";
  return SendJsonLine(json.str());
}

Wav2LipResponse Wav2LipBridge::TrackFaceContext(
    const Wav2LipRequest& request) const {
  std::ostringstream json;
  json << "{\"type\":\"track_face_context\""
       << ",\"request_id\":\"" << JsonEscape(request.request_id) << "\""
       << ",\"frame_path\":\"" << JsonEscape(request.face_path) << "\""
       << ",\"allow_detector_fallback\":true"
       << ",\"force_detect\":"
       << (request.force_face_detect ? "true" : "false") << "}\n";
  return SendJsonLine(json.str());
}

Wav2LipResponse Wav2LipBridge::GenerateTail(
    const Wav2LipRequest& request) const {
  std::ostringstream json;
  json << "{\"type\":\"generate_tail\""
       << ",\"request_id\":\"" << JsonEscape(request.request_id) << "\""
       << ",\"face_path\":\"" << JsonEscape(request.face_path) << "\""
       << ",\"output_path\":\"" << JsonEscape(request.output_path) << "\""
       << ",\"fps\":" << request.fps
       << ",\"tail_ms\":" << request.tail_ms << "}\n";
  return SendJsonLine(json.str());
}

Wav2LipResponse Wav2LipBridge::SendJsonLine(const std::string& json_line) const {
  Wav2LipResponse response;

  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    response.error = "socket() failed";
    return response;
  }

  sockaddr_in addr;
  std::memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons(port_);
  if (inet_pton(AF_INET, host_.c_str(), &addr.sin_addr) != 1) {
    response.error = "inet_pton() failed for host " + host_;
    close(fd);
    return response;
  }

  if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    response.error = "connect() failed";
    close(fd);
    return response;
  }

  const char* data = json_line.data();
  size_t remaining = json_line.size();
  while (remaining > 0) {
    ssize_t sent = send(fd, data, remaining, 0);
    if (sent <= 0) {
      response.error = "send() failed";
      close(fd);
      return response;
    }
    data += sent;
    remaining -= sent;
  }

  std::string raw;
  char buf[4096];
  while (raw.find('\n') == std::string::npos) {
    ssize_t n = recv(fd, buf, sizeof(buf), 0);
    if (n <= 0) {
      break;
    }
    raw.append(buf, n);
  }
  close(fd);

  response.request_id = ExtractString(raw, "request_id");
  response.output_path = ExtractString(raw, "output_path");
  response.frames_dir = ExtractString(raw, "frames_dir");
  response.error = ExtractString(raw, "error");
  response.latency_ms = ExtractInt64(raw, "latency_ms");
  response.frame_count = ExtractInt64(raw, "frame_count");
  response.context_ms = ExtractInt64(raw, "context_ms");
  response.ok = raw.find("\"status\":\"ok\"") != std::string::npos ||
                raw.find("\"status\": \"ok\"") != std::string::npos;
  if (!response.ok && response.error.empty()) {
    response.error = "server returned non-ok response: " + raw;
  }
  return response;
}

}  // namespace webrtc_wav2lip
