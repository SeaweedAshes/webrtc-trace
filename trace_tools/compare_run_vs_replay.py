#!/usr/bin/env python3
"""Compare a live/original WebRTC run against a replay run."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean, median


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-run-root", required=True, type=Path)
    parser.add_argument("--replay-run-root", required=True, type=Path)
    parser.add_argument(
        "--warmup-sec",
        type=float,
        default=0.0,
        help="drop the first N seconds from each CSV before summarizing",
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def as_int(row: dict[str, str], key: str) -> int | None:
    value = row.get(key, "").strip()
    if not value:
        return None
    return int(float(value))


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def safe_median(values: list[float]) -> float | None:
    return median(values) if values else None


def timestamp_span_ms(rows: list[dict[str, str]], key: str) -> int | None:
    vals = [as_int(r, key) for r in rows]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    return max(vals) - min(vals)


def trim_warmup(
    rows: list[dict[str, str]], timestamp_key: str, warmup_ms: int
) -> list[dict[str, str]]:
    if warmup_ms <= 0:
        return rows
    timestamps = [as_int(r, timestamp_key) for r in rows]
    timestamps = [t for t in timestamps if t is not None]
    if not timestamps:
        return rows
    cutoff = min(timestamps) + warmup_ms
    return [
        row
        for row in rows
        if (ts := as_int(row, timestamp_key)) is not None and ts >= cutoff
    ]


def per_sec(count: int, duration_ms: int | None) -> float | None:
    if not duration_ms or duration_ms <= 0:
        return None
    return count / (duration_ms / 1000.0)


def summarize_bwe(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    vals = [as_int(r, "target_bps") for r in rows]
    vals = [v for v in vals if v is not None]
    dur = timestamp_span_ms(rows, "timestamp_ms")
    return {
        "samples": len(vals),
        "duration_ms": dur,
        "mean_bps": safe_mean(vals),
        "median_bps": safe_median(vals),
        "p95_bps": percentile([float(v) for v in vals], 95.0),
        "min_bps": min(vals) if vals else None,
        "max_bps": max(vals) if vals else None,
        "first_bps": vals[0] if vals else None,
        "last_bps": vals[-1] if vals else None,
    }


def summarize_feedback(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    delays: list[float] = []
    total_bytes = 0
    for row in rows:
        send = as_int(row, "send_time_ms")
        recv = as_int(row, "recv_time_ms")
        size = as_int(row, "size_bytes")
        if send is None or recv is None or size is None or recv < send:
            continue
        delays.append(float(recv - send))
        total_bytes += size
    dur = timestamp_span_ms(rows, "recv_time_ms")
    acked_kbps = None
    if dur and dur > 0:
        acked_kbps = (total_bytes * 8.0) / dur
    return {
        "samples": len(delays),
        "duration_ms": dur,
        "mean_delay_ms": safe_mean(delays),
        "median_delay_ms": safe_median(delays),
        "p95_delay_ms": percentile(delays, 95.0),
        "max_delay_ms": max(delays) if delays else None,
        "acked_kbps_mean": acked_kbps,
    }


def summarize_frame_buffer(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    playable = [as_int(r, "playable_units") for r in rows]
    playable = [v for v in playable if v is not None]
    continuous = [as_int(r, "continuous_units") for r in rows]
    continuous = [v for v in continuous if v is not None]
    buffer_size = [as_int(r, "buffer_size") for r in rows]
    buffer_size = [v for v in buffer_size if v is not None]
    dur = timestamp_span_ms(rows, "timestamp_ms")
    return {
        "samples": len(rows),
        "duration_ms": dur,
        "playable_mean": safe_mean(playable),
        "playable_median": safe_median(playable),
        "playable_min": min(playable) if playable else None,
        "playable_p05": percentile([float(v) for v in playable], 5.0),
        "continuous_mean": safe_mean(continuous),
        "continuous_max": max(continuous) if continuous else None,
        "buffer_max": max(buffer_size) if buffer_size else None,
    }


def summarize_scheduler(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    events = Counter(r.get("event", "") for r in rows)
    dur = timestamp_span_ms(rows, "timestamp_ms")
    return {
        "samples": len(rows),
        "duration_ms": dur,
        "nodecodable_rate": per_sec(events.get("MSFR_nodecodable", 0), dur),
        "onframeready_rate": per_sec(events.get("OnFrameReady", 0), dur),
        "forcekey_rate": per_sec(events.get("MSFR_forcekey", 0), dur),
        "timeout_rate": per_sec(events.get("OnTimeout", 0), dur),
        "event_counts": dict(events),
    }


def summarize_audio(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    dur = timestamp_span_ms(rows, "wall_time_ms")
    return {
        "samples": len(rows),
        "duration_ms": dur,
        "packet_rate": per_sec(len(rows), dur),
    }


def summarize_freeze(rows: list[dict[str, str]]) -> dict[str, float | int | None]:
    durations = [as_int(r, "freeze_duration_ms") for r in rows]
    durations = [v for v in durations if v is not None]
    return {
        "count": len(durations),
        "total_duration_ms": sum(durations) if durations else 0,
        "max_duration_ms": max(durations) if durations else 0,
    }


def resolve_side_dirs(run_root: Path) -> tuple[Path, Path]:
    sender = run_root / "sender"
    receiver = run_root / "receiver"
    if sender.is_dir() and receiver.is_dir():
        return sender, receiver

    sender_txt = run_root / "sender_log_dir.txt"
    receiver_txt = run_root / "receiver_log_dir.txt"
    if sender_txt.exists() and receiver_txt.exists():
        sender = Path(sender_txt.read_text().strip())
        receiver = Path(receiver_txt.read_text().strip())
        if sender.is_dir() and receiver.is_dir():
            return sender, receiver

    raise FileNotFoundError(f"could not resolve sender/receiver dirs from {run_root}")


def summarize_run(run_root: Path, warmup_ms: int) -> dict[str, object]:
    sender_dir, receiver_dir = resolve_side_dirs(run_root)
    bwe_rows = trim_warmup(
        read_rows(sender_dir / "bwe_target.csv"), "timestamp_ms", warmup_ms
    )
    feedback_rows = trim_warmup(
        read_rows(sender_dir / "packet_feedback.csv"), "recv_time_ms", warmup_ms
    )
    frame_rows = trim_warmup(
        read_rows(receiver_dir / "frame_buffer.csv"), "timestamp_ms", warmup_ms
    )
    sched_rows = trim_warmup(
        read_rows(receiver_dir / "scheduler_events.csv"), "timestamp_ms", warmup_ms
    )
    audio_rows = trim_warmup(
        read_rows(receiver_dir / "audio_packet_inserts.csv"), "wall_time_ms", warmup_ms
    )
    freeze_rows = trim_warmup(
        read_rows(receiver_dir / "video_freeze.csv"), "timestamp_ms", warmup_ms
    )
    return {
        "sender_dir": str(sender_dir),
        "receiver_dir": str(receiver_dir),
        "bwe": summarize_bwe(bwe_rows),
        "feedback": summarize_feedback(feedback_rows),
        "frame_buffer": summarize_frame_buffer(frame_rows),
        "scheduler": summarize_scheduler(sched_rows),
        "audio": summarize_audio(audio_rows),
        "freeze": summarize_freeze(freeze_rows),
    }


def pct_diff(a: float | int | None, b: float | int | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return ((float(b) - float(a)) / float(a)) * 100.0


def build_comparison(original: dict[str, object], replay: dict[str, object]) -> dict[str, object]:
    o_bwe = original["bwe"]
    r_bwe = replay["bwe"]
    o_fb = original["feedback"]
    r_fb = replay["feedback"]
    o_fr = original["frame_buffer"]
    r_fr = replay["frame_buffer"]
    o_sc = original["scheduler"]
    r_sc = replay["scheduler"]
    o_au = original["audio"]
    r_au = replay["audio"]
    o_frz = original["freeze"]
    r_frz = replay["freeze"]
    return {
        "duration_ratio_bwe": pct_diff(o_bwe["duration_ms"], r_bwe["duration_ms"]),
        "mean_bwe_diff_pct": pct_diff(o_bwe["mean_bps"], r_bwe["mean_bps"]),
        "median_bwe_diff_pct": pct_diff(o_bwe["median_bps"], r_bwe["median_bps"]),
        "p95_delay_diff_pct": pct_diff(o_fb["p95_delay_ms"], r_fb["p95_delay_ms"]),
        "mean_delay_diff_pct": pct_diff(o_fb["mean_delay_ms"], r_fb["mean_delay_ms"]),
        "acked_kbps_diff_pct": pct_diff(o_fb["acked_kbps_mean"], r_fb["acked_kbps_mean"]),
        "playable_mean_diff_pct": pct_diff(o_fr["playable_mean"], r_fr["playable_mean"]),
        "playable_p05_diff_pct": pct_diff(o_fr["playable_p05"], r_fr["playable_p05"]),
        "nodecodable_rate_diff_pct": pct_diff(o_sc["nodecodable_rate"], r_sc["nodecodable_rate"]),
        "onframeready_rate_diff_pct": pct_diff(o_sc["onframeready_rate"], r_sc["onframeready_rate"]),
        "audio_packet_rate_diff_pct": pct_diff(o_au["packet_rate"], r_au["packet_rate"]),
        "freeze_count_original": o_frz["count"],
        "freeze_count_replay": r_frz["count"],
    }


def print_summary(label: str, summary: dict[str, object]) -> None:
    print(f"[{label}] sender_dir={summary['sender_dir']}")
    print(f"[{label}] receiver_dir={summary['receiver_dir']}")
    bwe = summary["bwe"]
    fb = summary["feedback"]
    fr = summary["frame_buffer"]
    sc = summary["scheduler"]
    au = summary["audio"]
    frz = summary["freeze"]
    print(
        f"[{label}] bwe mean={bwe['mean_bps']:.0f} median={bwe['median_bps']:.0f} "
        f"min={bwe['min_bps']} max={bwe['max_bps']} duration_ms={bwe['duration_ms']}"
    )
    print(
        f"[{label}] feedback mean_delay={fb['mean_delay_ms']:.1f} p95_delay={fb['p95_delay_ms']:.1f} "
        f"acked_kbps={fb['acked_kbps_mean']:.1f} samples={fb['samples']}"
    )
    print(
        f"[{label}] frame_buffer playable_mean={fr['playable_mean']:.2f} "
        f"playable_p05={fr['playable_p05']:.2f} buffer_max={fr['buffer_max']}"
    )
    print(
        f"[{label}] scheduler nodecodable_rate={sc['nodecodable_rate']:.2f}/s "
        f"onframeready_rate={sc['onframeready_rate']:.2f}/s forcekey_rate={sc['forcekey_rate']:.2f}/s"
    )
    print(
        f"[{label}] audio packet_rate={au['packet_rate']:.2f}/s freeze_count={frz['count']} "
        f"freeze_total_ms={frz['total_duration_ms']}"
    )


def main() -> int:
    args = parse_args()
    warmup_ms = int(args.warmup_sec * 1000)
    original = summarize_run(args.original_run_root, warmup_ms)
    replay = summarize_run(args.replay_run_root, warmup_ms)
    comparison = build_comparison(original, replay)

    print_summary("original", original)
    print_summary("replay", replay)
    print("[comparison]" + json.dumps(comparison, ensure_ascii=True))

    if args.output_json:
        payload = {
            "original": original,
            "replay": replay,
            "comparison": comparison,
        }
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
