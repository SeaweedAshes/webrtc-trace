#include "examples/peerconnection/client/playout_headroom_signal.h"

#include <atomic>
#include <cstdint>

namespace webrtc_wav2lip {
namespace {

std::atomic<int64_t> g_updated_ms{0};
std::atomic<int64_t> g_headroom_ms{0};
std::atomic<int64_t> g_buffer_size{0};
std::atomic<int64_t> g_rtp_timestamp{-1};

}  // namespace

void UpdateReceiverPlayoutHeadroom(int64_t updated_ms,
                                   int64_t headroom_ms,
                                   int64_t buffer_size,
                                   int64_t rtp_timestamp) {
  g_headroom_ms.store(headroom_ms, std::memory_order_relaxed);
  g_buffer_size.store(buffer_size, std::memory_order_relaxed);
  g_rtp_timestamp.store(rtp_timestamp, std::memory_order_relaxed);
  g_updated_ms.store(updated_ms, std::memory_order_release);
}

PlayoutHeadroomSnapshot GetReceiverPlayoutHeadroom() {
  PlayoutHeadroomSnapshot snapshot;
  snapshot.updated_ms = g_updated_ms.load(std::memory_order_acquire);
  snapshot.headroom_ms = g_headroom_ms.load(std::memory_order_relaxed);
  snapshot.buffer_size = g_buffer_size.load(std::memory_order_relaxed);
  snapshot.rtp_timestamp = g_rtp_timestamp.load(std::memory_order_relaxed);
  return snapshot;
}

}  // namespace webrtc_wav2lip
