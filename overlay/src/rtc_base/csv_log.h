/*
 *  Copyright (c) 2024 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

// Minimal helper for opening per-session CSV log files.
//
// Each process run gets its own timestamped subdirectory so logs are never
// overwritten:
//
//   $WEBRTC_LOG_DIR/
//     20240330_143052/
//       bwe_target.csv
//       trendline.csv
//       ...
//     20240330_151804/
//       ...
//
// Usage:
//   static FILE* f = rtc::OpenCsvLog("my_log.csv", "col_a,col_b\n");
//
// The base directory is taken from $WEBRTC_LOG_DIR.
// If the variable is not set, $HOME/webrtc_logs is used as fallback.

#ifndef RTC_BASE_CSV_LOG_H_
#define RTC_BASE_CSV_LOG_H_

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <sys/stat.h>

namespace rtc {

// Returns the session directory (created once per process on first call).
// Format: <base_dir>/YYYYMMDD_HHMMSS
inline const char* CsvSessionDir() {
  static char session_dir[512] = {};
  // C++11 guarantees this lambda runs exactly once, thread-safely.
  static bool initialized = [&]() {
    const char* base = std::getenv("WEBRTC_LOG_DIR");
    char fallback[512];
    if (!base || base[0] == '\0') {
      const char* home = std::getenv("HOME");
      if (!home || home[0] == '\0') {
        home = ".";
      }
      snprintf(fallback, sizeof(fallback), "%s/webrtc_logs", home);
      base = fallback;
    }
    mkdir(base, 0755);

    time_t now = time(nullptr);
    struct tm t;
    localtime_r(&now, &t);
    char ts[32];
    strftime(ts, sizeof(ts), "%Y%m%d_%H%M%S", &t);

    snprintf(session_dir, sizeof(session_dir), "%s/%s", base, ts);
    mkdir(session_dir, 0755);
    return true;
  }();
  (void)initialized;
  return session_dir;
}

inline FILE* OpenCsvLogWithBuffering(const char* filename,
                                     const char* header,
                                     bool line_buffered,
                                     const char* mode) {
  char path[512];
  snprintf(path, sizeof(path), "%s/%s", CsvSessionDir(), filename);
  bool write_header = true;
  if (mode && mode[0] == 'a') {
    struct stat st;
    write_header = stat(path, &st) != 0 || st.st_size == 0;
  }
  FILE* f = fopen(path, mode ? mode : "w");
  if (f) {
    if (line_buffered) {
      // Line-buffered: each fprintf call ending with '\n' flushes immediately,
      // so the last line is never lost when the process exits.
      setvbuf(f, nullptr, _IOLBF, 0);
    } else {
      // Packet-level logs are written by a background thread and should batch
      // disk I/O rather than flushing every row.
      setvbuf(f, nullptr, _IOFBF, 1024 * 1024);
    }
    if (header && write_header) {
      fputs(header, f);
    }
  }
  return f;
}

inline FILE* OpenCsvLog(const char* filename, const char* header) {
  return OpenCsvLogWithBuffering(filename, header, true, "w");
}

inline FILE* OpenCsvLogBuffered(const char* filename, const char* header) {
  return OpenCsvLogWithBuffering(filename, header, false, "w");
}

inline FILE* AppendCsvLog(const char* filename, const char* header) {
  return OpenCsvLogWithBuffering(filename, header, true, "a");
}

class AsyncCsvLog {
 public:
  AsyncCsvLog(const char* filename,
              const char* header,
              size_t max_queue_rows = 65536)
      : filename_(filename),
        file_(OpenCsvLogBuffered(filename, header)),
        max_queue_rows_(max_queue_rows) {
    if (file_) {
      writer_ = std::thread([this] { WriterLoop(); });
    }
  }

  AsyncCsvLog(const AsyncCsvLog&) = delete;
  AsyncCsvLog& operator=(const AsyncCsvLog&) = delete;

  ~AsyncCsvLog() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stop_ = true;
    }
    cv_.notify_one();
    if (writer_.joinable()) {
      writer_.join();
    }
    if (file_) {
      fclose(file_);
    }
    if (FILE* stats = AppendCsvLog(
            "logging_stats.csv",
            "timestamp_ms,filename,enqueued_rows,dropped_rows,max_queue_depth\n")) {
      fprintf(stats, "%lld,%s,%llu,%llu,%llu\n",
              static_cast<long long>(time(nullptr) * 1000LL),
              filename_.c_str(),
              static_cast<unsigned long long>(enqueued_rows_.load()),
              static_cast<unsigned long long>(dropped_rows_.load()),
              static_cast<unsigned long long>(max_queue_depth_.load()));
      fclose(stats);
    }
  }

  void WriteLine(const char* row, size_t len) {
    if (!file_ || row == nullptr || len == 0) {
      return;
    }
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (queue_.size() >= max_queue_rows_) {
        dropped_rows_.fetch_add(1, std::memory_order_relaxed);
        return;
      }
      queue_.emplace_back(row);
      enqueued_rows_.fetch_add(1, std::memory_order_relaxed);
      uint64_t depth = queue_.size();
      uint64_t previous = max_queue_depth_.load(std::memory_order_relaxed);
      while (depth > previous &&
             !max_queue_depth_.compare_exchange_weak(
                 previous, depth, std::memory_order_relaxed)) {
      }
    }
    cv_.notify_one();
  }

 private:
  void WriterLoop() {
    std::deque<std::string> local;
    while (true) {
      {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [this] { return stop_ || !queue_.empty(); });
        queue_.swap(local);
        if (stop_ && local.empty()) {
          break;
        }
      }
      for (const std::string& row : local) {
        fputs(row.c_str(), file_);
      }
      local.clear();
      fflush(file_);
      MaybeWriteStats();
    }
    fflush(file_);
  }

  void MaybeWriteStats() {
    uint64_t enqueued = enqueued_rows_.load(std::memory_order_relaxed);
    if (enqueued - last_stats_rows_ < 1000) {
      return;
    }
    last_stats_rows_ = enqueued;
    if (FILE* stats = AppendCsvLog(
            "logging_stats.csv",
            "timestamp_ms,filename,enqueued_rows,dropped_rows,max_queue_depth\n")) {
      fprintf(stats, "%lld,%s,%llu,%llu,%llu\n",
              static_cast<long long>(time(nullptr) * 1000LL),
              filename_.c_str(),
              static_cast<unsigned long long>(enqueued),
              static_cast<unsigned long long>(dropped_rows_.load()),
              static_cast<unsigned long long>(max_queue_depth_.load()));
      fclose(stats);
    }
  }

  std::string filename_;
  FILE* file_ = nullptr;
  const size_t max_queue_rows_;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<std::string> queue_;
  std::thread writer_;
  bool stop_ = false;
  uint64_t last_stats_rows_ = 0;
  std::atomic<uint64_t> enqueued_rows_{0};
  std::atomic<uint64_t> dropped_rows_{0};
  std::atomic<uint64_t> max_queue_depth_{0};
};

}  // namespace rtc

#endif  // RTC_BASE_CSV_LOG_H_
