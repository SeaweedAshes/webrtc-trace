#!/bin/bash

set -euo pipefail

OUTPUT_DIR=""
DURATION_SEC=1800
INTERVAL_SEC=1

usage() {
  cat <<EOF
Usage: $0 --output-dir DIR [options]

Options:
  --output-dir DIR       Directory for system metric logs
  --duration-sec N       Collection duration in seconds (default: $DURATION_SEC)
  --interval-sec N       Sampling interval in seconds (default: $INTERVAL_SEC)
EOF
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --duration-sec) DURATION_SEC="$2"; shift 2 ;;
    --interval-sec) INTERVAL_SEC="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$OUTPUT_DIR" ]] || usage
[[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || { echo "duration-sec must be numeric" >&2; exit 1; }
[[ "$INTERVAL_SEC" =~ ^[0-9]+$ ]] || { echo "interval-sec must be numeric" >&2; exit 1; }
(( DURATION_SEC > 0 )) || { echo "duration-sec must be greater than zero" >&2; exit 1; }
(( INTERVAL_SEC > 0 )) || { echo "interval-sec must be greater than zero" >&2; exit 1; }

mkdir -p "$OUTPUT_DIR"

SUMMARY_CSV="$OUTPUT_DIR/thermal_cpu.csv"
RAW_LOG="$OUTPUT_DIR/thermal_cpu_raw.log"

csv_escape() {
  local value="${1:-}"
  value="${value//\"/\"\"}"
  printf '"%s"' "$value"
}

stats_for_values() {
  awk '
    NF {
      n += 1
      sum += $1
      if (n == 1 || $1 < min) min = $1
      if (n == 1 || $1 > max) max = $1
    }
    END {
      if (n == 0) {
        printf ",,,0"
      } else {
        printf "%.3f,%.3f,%.3f,%d", min, max, sum / n, n
      }
    }'
}

read_loadavg() {
  awk '{print $1 "," $2 "," $3}' /proc/loadavg 2>/dev/null || printf ',,'
}

cpu_mhz_stats() {
  awk -F: '/cpu MHz/ {gsub(/^[ \t]+/, "", $2); print $2}' /proc/cpuinfo | stats_for_values
}

scaling_khz_stats() {
  local found=0
  for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
    [[ -r "$f" ]] || continue
    cat "$f"
    found=1
  done | stats_for_values
  [[ "$found" -eq 1 ]] || true
}

thermal_millic_stats() {
  local found=0
  for f in /sys/class/thermal/thermal_zone*/temp; do
    [[ -r "$f" ]] || continue
    cat "$f" 2>/dev/null || true
    found=1
  done | stats_for_values
  [[ "$found" -eq 1 ]] || true
}

battery_status() {
  local status=""
  local capacity=""
  for b in /sys/class/power_supply/BAT*; do
    [[ -d "$b" ]] || continue
    [[ -r "$b/status" ]] && status="$(cat "$b/status")"
    [[ -r "$b/capacity" ]] && capacity="$(cat "$b/capacity")"
    break
  done
  printf '%s,%s' "$(csv_escape "$status")" "$(csv_escape "$capacity")"
}

ac_online() {
  local vals=()
  for a in /sys/class/power_supply/A{C,DP}* /sys/class/power_supply/ACAD*; do
    [[ -r "$a/online" ]] || continue
    vals+=("$(basename "$a")=$(cat "$a/online")")
  done
  local joined=""
  local v
  for v in "${vals[@]}"; do
    [[ -n "$joined" ]] && joined+=";"
    joined+="$v"
  done
  csv_escape "$joined"
}

write_raw_snapshot() {
  local now_ms="$1"
  {
    echo "===== $now_ms ====="
    echo "[uptime]"
    uptime 2>/dev/null || true
    echo "[cpu_mhz]"
    grep 'cpu MHz' /proc/cpuinfo 2>/dev/null || true
    echo "[scaling_cur_freq]"
    for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
      [[ -r "$f" ]] && echo "$f $(cat "$f")"
    done
    echo "[thermal_zone]"
    for z in /sys/class/thermal/thermal_zone*/temp; do
      [[ -r "$z" ]] && echo "$z $(cat "$z" 2>/dev/null || true)"
    done
    echo "[power_supply]"
    for p in /sys/class/power_supply/*; do
      [[ -d "$p" ]] || continue
      printf '%s ' "$(basename "$p")"
      [[ -r "$p/type" ]] && printf 'type=%s ' "$(cat "$p/type")"
      [[ -r "$p/status" ]] && printf 'status=%s ' "$(cat "$p/status")"
      [[ -r "$p/capacity" ]] && printf 'capacity=%s ' "$(cat "$p/capacity")"
      [[ -r "$p/online" ]] && printf 'online=%s ' "$(cat "$p/online")"
      echo
    done
    echo "[sensors]"
    sensors 2>/dev/null || true
    echo
  } >> "$RAW_LOG"
}

echo "timestamp_ms,load1,load5,load15,cpu_mhz_min,cpu_mhz_max,cpu_mhz_avg,cpu_mhz_count,scaling_khz_min,scaling_khz_max,scaling_khz_avg,scaling_khz_count,thermal_millic_min,thermal_millic_max,thermal_millic_avg,thermal_millic_count,battery_status,battery_capacity,ac_online" > "$SUMMARY_CSV"

end_ts=$((SECONDS + DURATION_SEC))
while (( SECONDS < end_ts )); do
  now_ms="$(date +%s%3N)"
  printf '%s,%s,%s,%s,%s,%s,%s\n' \
    "$now_ms" \
    "$(read_loadavg)" \
    "$(cpu_mhz_stats)" \
    "$(scaling_khz_stats)" \
    "$(thermal_millic_stats)" \
    "$(battery_status)" \
    "$(ac_online)" >> "$SUMMARY_CSV"
  write_raw_snapshot "$now_ms"
  sleep "$INTERVAL_SEC"
done
