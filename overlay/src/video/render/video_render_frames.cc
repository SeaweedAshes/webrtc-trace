/*
 *  Copyright (c) 2012 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#include "video/render/video_render_frames.h"

#include <cstddef>
#include <cstdint>
#include <optional>
#include <utility>

#include "api/environment/environment.h"
#include "api/video/video_frame.h"
#include "rtc_base/csv_log.h"
#include "rtc_base/logging.h"
#include "system_wrappers/include/metrics.h"

namespace webrtc {
namespace {
// Don't render frames with timestamp older than 500ms from now.
const int kOldRenderTimestampMS = 500;
// Don't render frames with timestamp more than 10s into the future.
const int kFutureRenderTimestampMS = 10000;

const uint32_t kEventMaxWaitTimeMs = 200;
const uint32_t kMinRenderDelayMs = 10;
const uint32_t kMaxRenderDelayMs = 500;
const size_t kMaxIncomingFramesBeforeLogged = 100;

uint32_t EnsureValidRenderDelay(uint32_t render_delay) {
  return (render_delay < kMinRenderDelayMs || render_delay > kMaxRenderDelayMs)
             ? kMinRenderDelayMs
             : render_delay;
}

FILE *RenderQueueLog() {
  static FILE *f = rtc::OpenCsvLog(
      "render_queue.csv",
      "timestamp_ms,event,rtp_timestamp,render_time_ms,render_delay_ms,"
      "queue_size_before,queue_size_after,time_to_next_release_ms,"
      "frames_dropped,total_frames_dropped\n");
  return f;
}

void LogRenderQueue(int64_t now_ms, const char *event, uint32_t rtp_timestamp,
                    int64_t render_time_ms, uint32_t render_delay_ms,
                    size_t queue_size_before, size_t queue_size_after,
                    uint32_t time_to_next_release_ms, int frames_dropped,
                    size_t total_frames_dropped) {
  if (FILE *f = RenderQueueLog()) {
    fprintf(f, "%lld,%s,%u,%lld,%u,%zu,%zu,%u,%d,%zu\n", (long long)now_ms,
            event, rtp_timestamp, (long long)render_time_ms, render_delay_ms,
            queue_size_before, queue_size_after, time_to_next_release_ms,
            frames_dropped, total_frames_dropped);
  }
}
} // namespace

VideoRenderFrames::VideoRenderFrames(const Environment &env,
                                     uint32_t render_delay_ms)
    : env_(env), render_delay_ms_(EnsureValidRenderDelay(render_delay_ms)) {}

VideoRenderFrames::~VideoRenderFrames() {
  frames_dropped_ += incoming_frames_.size();
  RTC_HISTOGRAM_COUNTS_1000("WebRTC.Video.DroppedFrames.RenderQueue",
                            frames_dropped_);
  RTC_LOG(LS_INFO) << "WebRTC.Video.DroppedFrames.RenderQueue "
                   << frames_dropped_;
}

int32_t VideoRenderFrames::AddFrame(VideoFrame &&new_frame) {
  const int64_t time_now = env_.clock().TimeInMilliseconds();
  const size_t queue_size_before = incoming_frames_.size();
  const uint32_t rtp_timestamp = new_frame.rtp_timestamp();
  const int64_t render_time_ms = new_frame.render_time_ms();

  // Drop old frames only when there are other frames in the queue, otherwise, a
  // really slow system never renders any frames.
  if (!incoming_frames_.empty() &&
      new_frame.render_time_ms() + kOldRenderTimestampMS < time_now) {
    RTC_LOG(LS_WARNING) << "Too old frame, timestamp="
                        << new_frame.rtp_timestamp();
    ++frames_dropped_;
    LogRenderQueue(time_now, "drop_old", rtp_timestamp, render_time_ms,
                   render_delay_ms_, queue_size_before, incoming_frames_.size(),
                   TimeToNextFrameRelease(), 1, frames_dropped_);
    return -1;
  }

  if (new_frame.render_time_ms() > time_now + kFutureRenderTimestampMS) {
    RTC_LOG(LS_WARNING) << "Frame too long into the future, timestamp="
                        << new_frame.rtp_timestamp();
    ++frames_dropped_;
    LogRenderQueue(time_now, "drop_future", rtp_timestamp, render_time_ms,
                   render_delay_ms_, queue_size_before, incoming_frames_.size(),
                   TimeToNextFrameRelease(), 1, frames_dropped_);
    return -1;
  }

  if (new_frame.render_time_ms() < last_render_time_ms_) {
    RTC_LOG(LS_WARNING) << "Frame scheduled out of order, render_time="
                        << new_frame.render_time_ms()
                        << ", latest=" << last_render_time_ms_;
    // For more details, see bug:
    // https://bugs.chromium.org/p/webrtc/issues/detail?id=7253
    ++frames_dropped_;
    LogRenderQueue(time_now, "drop_out_of_order", rtp_timestamp, render_time_ms,
                   render_delay_ms_, queue_size_before, incoming_frames_.size(),
                   TimeToNextFrameRelease(), 1, frames_dropped_);
    return -1;
  }

  last_render_time_ms_ = new_frame.render_time_ms();
  incoming_frames_.emplace_back(std::move(new_frame));

  if (incoming_frames_.size() > kMaxIncomingFramesBeforeLogged) {
    RTC_LOG(LS_WARNING) << "Stored incoming frames: "
                        << incoming_frames_.size();
  }
  LogRenderQueue(time_now, "add_frame", rtp_timestamp, render_time_ms,
                 render_delay_ms_, queue_size_before, incoming_frames_.size(),
                 TimeToNextFrameRelease(), 0, frames_dropped_);
  return static_cast<int32_t>(incoming_frames_.size());
}

std::optional<VideoFrame> VideoRenderFrames::FrameToRender() {
  std::optional<VideoFrame> render_frame;
  // Get the newest frame that can be released for rendering.
  while (!incoming_frames_.empty() && TimeToNextFrameRelease() <= 0) {
    const int64_t time_now = env_.clock().TimeInMilliseconds();
    const size_t queue_size_before = incoming_frames_.size();
    const uint32_t rtp_timestamp = incoming_frames_.front().rtp_timestamp();
    const int64_t render_time_ms = incoming_frames_.front().render_time_ms();
    if (render_frame) {
      ++frames_dropped_;
      LogRenderQueue(time_now, "drop_superseded", rtp_timestamp, render_time_ms,
                     render_delay_ms_, queue_size_before, queue_size_before - 1,
                     0, 1, frames_dropped_);
    }
    render_frame = std::move(incoming_frames_.front());
    incoming_frames_.pop_front();
    LogRenderQueue(time_now, "frame_to_render", rtp_timestamp, render_time_ms,
                   render_delay_ms_, queue_size_before, incoming_frames_.size(),
                   TimeToNextFrameRelease(), 0, frames_dropped_);
  }
  return render_frame;
}

uint32_t VideoRenderFrames::TimeToNextFrameRelease() {
  if (incoming_frames_.empty()) {
    return kEventMaxWaitTimeMs;
  }
  const int64_t time_to_release = incoming_frames_.front().render_time_ms() -
                                  render_delay_ms_ -
                                  env_.clock().TimeInMilliseconds();
  return time_to_release < 0 ? 0u : static_cast<uint32_t>(time_to_release);
}

bool VideoRenderFrames::HasPendingFrames() const {
  return !incoming_frames_.empty();
}

} // namespace webrtc
