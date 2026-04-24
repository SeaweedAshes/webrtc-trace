#!/usr/bin/env python3
"""Build a replayable netem trace from WebRTC sender/receiver CSV logs."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import median


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender-log-dir", required=True, type=Path)
    parser.add_argument("--receiver-log-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--window-ms", type=int, default=100)
    parser.add_argument("--base-delay-percentile", type=float, default=5.0)
    parser.add_argument(
        "--rate-source",
        choices=("auto", "acked", "bwe", "send"),
        default="auto",
        help="acked=packet_feedback, bwe=bwe_target, send=rtp_send",
    )
    parser.add_argument("--rate-headroom", type=float, default=1.05)
    parser.add_argument("--delay-quantum-ms", type=int, default=5)
    parser.add_argument("--rate-quantum-kbit", type=int, default=50)
    parser.add_argument("--min-rate-kbit", type=int, default=150)
    parser.add_argument("--limit-pkts", type=int, default=1000)
    parser.add_argument(
        "--receiver-time-offset-ms",
        type=int,
        default=0,
        help="optional manual offset when receiver and sender timelines differ",
    )
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


def quantize(value: float, quantum: int) -> int:
    if quantum <= 1:
        return int(round(value))
    return int(round(value / quantum) * quantum)


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values available for percentile")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def pick_rate_source(args: argparse.Namespace, paths: dict[str, Path]) -> str:
    if args.rate_source != "auto":
        return args.rate_source
    if paths["packet_feedback"].exists():
        return "acked"
    if paths["bwe_target"].exists():
        return "bwe"
    if paths["rtp_send"].exists():
        return "send"
    raise FileNotFoundError("no sender log usable for throughput trace")


def resolve_origin_ms(
    packet_feedback_rows: list[dict[str, str]],
    bwe_rows: list[dict[str, str]],
    send_rows: list[dict[str, str]],
) -> int:
    candidates: list[int] = []
    for row in packet_feedback_rows:
        recv_time = as_int(row, "recv_time_ms")
        if recv_time is not None:
            candidates.append(recv_time)
    for row in bwe_rows:
        ts = as_int(row, "timestamp_ms")
        if ts is not None:
            candidates.append(ts)
    for row in send_rows:
        ts = as_int(row, "send_time_ms")
        if ts is not None:
            candidates.append(ts)
    if not candidates:
        raise ValueError("no timestamped sender rows found")
    return min(candidates)


def build_trace() -> int:
    args = parse_args()
    sender_dir = args.sender_log_dir
    receiver_dir = args.receiver_log_dir
    if not sender_dir.is_dir():
        raise NotADirectoryError(f"sender log dir not found: {sender_dir}")
    if receiver_dir and not receiver_dir.is_dir():
        raise NotADirectoryError(f"receiver log dir not found: {receiver_dir}")

    sender_paths = {
        "packet_feedback": sender_dir / "packet_feedback.csv",
        "bwe_target": sender_dir / "bwe_target.csv",
        "rtp_send": sender_dir / "rtp_send.csv",
    }
    packet_feedback_rows = read_rows(sender_paths["packet_feedback"])
    bwe_rows = read_rows(sender_paths["bwe_target"])
    send_rows = read_rows(sender_paths["rtp_send"])

    rate_source = pick_rate_source(args, sender_paths)
    origin_ms = resolve_origin_ms(packet_feedback_rows, bwe_rows, send_rows)
    window_ms = args.window_ms

    delay_bins: dict[int, list[float]] = defaultdict(list)
    acked_bins: dict[int, int] = defaultdict(int)
    bwe_bins: dict[int, list[float]] = defaultdict(list)
    send_bins: dict[int, int] = defaultdict(int)
    note_bins: dict[int, list[str]] = defaultdict(list)
    all_delay_samples: list[float] = []

    for row in packet_feedback_rows:
        send_time = as_int(row, "send_time_ms")
        recv_time = as_int(row, "recv_time_ms")
        size_bytes = as_int(row, "size_bytes")
        if send_time is None or recv_time is None or size_bytes is None:
            continue
        if recv_time < send_time:
            continue
        idx = max(0, (recv_time - origin_ms) // window_ms)
        sample_delay = float(recv_time - send_time)
        all_delay_samples.append(sample_delay)
        delay_bins[idx].append(sample_delay)
        acked_bins[idx] += size_bytes

    base_delay_ms = (
        percentile(all_delay_samples, args.base_delay_percentile)
        if all_delay_samples
        else 0.0
    )
    if all_delay_samples:
        for idx, samples in list(delay_bins.items()):
            delay_bins[idx] = [max(0.0, sample - base_delay_ms) for sample in samples]

    for row in bwe_rows:
        ts = as_int(row, "timestamp_ms")
        target_bps = as_int(row, "target_bps")
        if ts is None or target_bps is None:
            continue
        idx = max(0, (ts - origin_ms) // window_ms)
        bwe_bins[idx].append(target_bps / 1000.0)

    for row in send_rows:
        ts = as_int(row, "send_time_ms")
        payload_size = as_int(row, "payload_size")
        if ts is None or payload_size is None:
            continue
        idx = max(0, (ts - origin_ms) // window_ms)
        send_bins[idx] += payload_size

    if receiver_dir:
        freeze_rows = read_rows(receiver_dir / "video_freeze.csv")
        freeze_origin = None
        for row in freeze_rows:
            ts = as_int(row, "timestamp_ms")
            if ts is not None:
                freeze_origin = ts if freeze_origin is None else min(freeze_origin, ts)
        if freeze_origin is not None:
            for row in freeze_rows:
                ts = as_int(row, "timestamp_ms")
                duration = as_int(row, "freeze_duration_ms")
                if ts is None or duration is None:
                    continue
                rel_ms = max(
                    0,
                    (ts - freeze_origin) + args.receiver_time_offset_ms,
                )
                idx = rel_ms // window_ms
                note_bins[idx].append(f"freeze:{duration}ms")

    max_bin = 0
    for bucket in (delay_bins, acked_bins, bwe_bins, send_bins, note_bins):
        if bucket:
            max_bin = max(max_bin, max(bucket))

    rows: list[dict[str, str]] = []
    previous_delay = 0.0
    previous_rate: float | None = None
    previous_state: tuple[int, str, str] | None = None

    for idx in range(max_bin + 1):
        if idx in delay_bins and delay_bins[idx]:
            previous_delay = float(median(delay_bins[idx]))

        current_rate: float | None = previous_rate
        if rate_source == "acked":
            if idx in acked_bins:
                current_rate = (acked_bins[idx] * 8.0 / window_ms) * args.rate_headroom
        elif rate_source == "bwe":
            if idx in bwe_bins and bwe_bins[idx]:
                current_rate = float(median(bwe_bins[idx])) * args.rate_headroom
        elif rate_source == "send":
            if idx in send_bins:
                current_rate = (send_bins[idx] * 8.0 / window_ms) * args.rate_headroom

        if current_rate is not None and current_rate > 0:
            current_rate = max(float(args.min_rate_kbit), current_rate)

        q_delay = max(0, quantize(previous_delay, args.delay_quantum_ms))
        q_rate = (
            ""
            if current_rate is None or current_rate <= 0
            else str(max(1, quantize(current_rate, args.rate_quantum_kbit)))
        )
        limit_pkts = str(args.limit_pkts) if q_rate else ""
        note = ";".join(note_bins.get(idx, []))
        state = (q_delay, q_rate, limit_pkts)

        if idx == 0 or state != previous_state or note:
            rows.append(
                {
                    "at_ms": str(idx * window_ms),
                    "delay_ms": str(q_delay),
                    "jitter_ms": "0",
                    "loss_pct": "0",
                    "rate_kbit": q_rate,
                    "limit_pkts": limit_pkts,
                    "note": note or "baseline",
                }
            )
            previous_state = state

        previous_rate = current_rate

    if not rows:
        raise ValueError("trace generation produced no rows")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "at_ms",
                "delay_ms",
                "jitter_ms",
                "loss_pct",
                "rate_kbit",
                "limit_pkts",
                "note",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"wrote {len(rows)} rows to {args.output} "
        f"(rate_source={rate_source}, base_delay_ms={base_delay_ms:.1f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(build_trace())
