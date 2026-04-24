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

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
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

inline FILE* OpenCsvLog(const char* filename, const char* header) {
  char path[512];
  snprintf(path, sizeof(path), "%s/%s", CsvSessionDir(), filename);
  FILE* f = fopen(path, "w");
  if (f) {
    // Line-buffered: each fprintf call ending with '\n' flushes immediately,
    // so the last line is never lost when the process exits.
    setvbuf(f, nullptr, _IOLBF, 0);
    if (header) {
      fputs(header, f);
    }
  }
  return f;
}

}  // namespace rtc

#endif  // RTC_BASE_CSV_LOG_H_
