#ifndef EXAMPLES_PEERCONNECTION_CLIENT_PLAYOUT_HEADROOM_SIGNAL_H_
#define EXAMPLES_PEERCONNECTION_CLIENT_PLAYOUT_HEADROOM_SIGNAL_H_

#include <cstdint>

namespace webrtc_wav2lip {

struct PlayoutHeadroomSnapshot {
  int64_t updated_ms = 0;
  int64_t headroom_ms = 0;
  int64_t buffer_size = 0;
  int64_t rtp_timestamp = -1;
};

void UpdateReceiverPlayoutHeadroom(int64_t updated_ms,
                                   int64_t headroom_ms,
                                   int64_t buffer_size,
                                   int64_t rtp_timestamp);

PlayoutHeadroomSnapshot GetReceiverPlayoutHeadroom();

}  // namespace webrtc_wav2lip

#endif  // EXAMPLES_PEERCONNECTION_CLIENT_PLAYOUT_HEADROOM_SIGNAL_H_
