#!/usr/bin/env python3
"""Build a replayable netem trace from WebRTC sender/receiver CSV logs."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict, deque
from pathlib import Path
from statistics import median, pstdev


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender-log-dir", required=True, type=Path)
    parser.add_argument("--receiver-log-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--window-ms", type=int, default=50)
    parser.add_argument("--base-delay-percentile", type=float, default=5.0)
    parser.add_argument(
        "--delay-source",
        choices=("feedback", "receiver-arrival"),
        default="feedback",
        help=(
            "feedback=sender packet_feedback delay, "
            "receiver-arrival=match sender rtp_send seq_num to receiver packet_buffer_inserts"
        ),
    )
    parser.add_argument(
        "--rate-source",
        choices=("auto", "none", "acked", "bwe", "send"),
        default="auto",
        help="acked=packet_feedback, bwe=bwe_target, send=rtp_send",
    )
    parser.add_argument("--rate-headroom", type=float, default=1.05)
    parser.add_argument("--delay-quantum-ms", type=int, default=5)
    parser.add_argument("--jitter-quantum-ms", type=int, default=5)
    parser.add_argument("--rate-quantum-kbit", type=int, default=50)
    parser.add_argument("--min-rate-kbit", type=int, default=150)
    parser.add_argument("--loss-quantum-pct", type=float, default=0.5)
    parser.add_argument("--max-loss-pct", type=float, default=30.0)
    parser.add_argument(
        "--drop-warmup-sec",
        type=float,
        default=0.0,
        help="drop the first N seconds from log-derived trace inputs",
    )
    parser.add_argument(
        "--arrival-shift-ms",
        type=int,
        default=0,
        help=(
            "for receiver-arrival traces, shift receiver arrival timing earlier "
            "before binning for sender-egress replay"
        ),
    )
    parser.add_argument(
        "--disable-loss",
        action="store_true",
        help="do not emit inferred loss_pct values",
    )
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


def quantize_float(value: float, quantum: float) -> float:
    if quantum <= 0:
        return value
    return round(round(value / quantum) * quantum, 3)


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


def receiver_arrival_origin_ms(receiver_packet_rows: list[dict[str, str]]) -> int:
    candidates = [
        ts
        for row in receiver_packet_rows
        for ts in [as_int(row, "timestamp_ms")]
        if ts is not None
    ]
    if not candidates:
        raise ValueError("no receiver packet insert timestamps found")
    return min(candidates)


def drop_warmup_rows(
    rows: list[dict[str, str]], timestamp_key: str, warmup_ms: int
) -> list[dict[str, str]]:
    if warmup_ms <= 0:
        return rows
    timestamps = [
        ts
        for row in rows
        for ts in [as_int(row, timestamp_key)]
        if ts is not None
    ]
    if not timestamps:
        return rows
    cutoff = min(timestamps) + warmup_ms
    return [
        row
        for row in rows
        if (ts := as_int(row, timestamp_key)) is not None and ts >= cutoff
    ]


def is_video_send_row(row: dict[str, str]) -> bool:
    if row.get("is_audio") not in ("", "0"):
        return False
    packet_type = row.get("packet_type", "").strip()
    # RtpPacketMediaType::kVideo is 1. Older logs may omit packet_type.
    return packet_type in ("", "1")


def build_sender_packet_indexes(
    send_rows: list[dict[str, str]]
) -> tuple[
    dict[tuple[int, int, int], deque[dict[str, int]]],
    dict[tuple[int, int], deque[dict[str, int]]],
    dict[int, deque[dict[str, int]]],
]:
    by_ssrc_seq_rtp: dict[tuple[int, int, int], deque[dict[str, int]]] = defaultdict(deque)
    by_ssrc_seq: dict[tuple[int, int], deque[dict[str, int]]] = defaultdict(deque)
    by_seq: dict[int, deque[dict[str, int]]] = defaultdict(deque)
    packet_id = 0
    for row in send_rows:
        if not is_video_send_row(row):
            continue
        seq_num = as_int(row, "seq_num")
        send_time = as_int(row, "send_time_ms")
        if seq_num is None or send_time is None:
            continue
        candidate = {
            "id": packet_id,
            "send_time_ms": send_time,
            "rtp_timestamp": as_int(row, "rtp_timestamp") or -1,
        }
        packet_id += 1
        ssrc = as_int(row, "ssrc")
        if ssrc is not None:
            if candidate["rtp_timestamp"] >= 0:
                by_ssrc_seq_rtp[(ssrc, seq_num, candidate["rtp_timestamp"])].append(
                    candidate
                )
            by_ssrc_seq[(ssrc, seq_num)].append(candidate)
        by_seq[seq_num].append(candidate)
    return by_ssrc_seq_rtp, by_ssrc_seq, by_seq


def pop_unused_candidate(
    candidates: deque[dict[str, int]] | None, used_packet_ids: set[int]
) -> dict[str, int] | None:
    if not candidates:
        return None
    while candidates and candidates[0]["id"] in used_packet_ids:
        candidates.popleft()
    if not candidates:
        return None
    candidate = candidates.popleft()
    used_packet_ids.add(candidate["id"])
    return candidate


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
    receiver_packet_rows = (
        read_rows(receiver_dir / "packet_buffer_inserts.csv")
        if receiver_dir
        else []
    )
    warmup_ms = int(args.drop_warmup_sec * 1000.0)

    rate_source = pick_rate_source(args, sender_paths)
    if args.delay_source == "receiver-arrival":
        if not receiver_dir:
            raise ValueError("--delay-source receiver-arrival requires --receiver-log-dir")
        if args.rate_source != "auto" and rate_source != "none":
            raise ValueError(
                "--delay-source receiver-arrival currently requires --rate-source none"
            )
        rate_source = "none"
        args.disable_loss = True
        receiver_packet_rows = drop_warmup_rows(
            receiver_packet_rows, "timestamp_ms", warmup_ms
        )
        origin_ms = receiver_arrival_origin_ms(receiver_packet_rows)
    else:
        packet_feedback_rows = drop_warmup_rows(
            packet_feedback_rows, "recv_time_ms", warmup_ms
        )
        bwe_rows = drop_warmup_rows(bwe_rows, "timestamp_ms", warmup_ms)
        send_rows = drop_warmup_rows(send_rows, "send_time_ms", warmup_ms)
        origin_ms = resolve_origin_ms(packet_feedback_rows, bwe_rows, send_rows)
    window_ms = args.window_ms

    delay_bins: dict[int, list[float]] = defaultdict(list)
    acked_bins: dict[int, int] = defaultdict(int)
    acked_send_bins: dict[int, int] = defaultdict(int)
    bwe_bins: dict[int, list[float]] = defaultdict(list)
    send_bins: dict[int, int] = defaultdict(int)
    jitter_bins: dict[int, list[float]] = defaultdict(list)
    loss_bins: dict[int, float] = {}
    note_bins: dict[int, list[str]] = defaultdict(list)
    all_delay_samples: list[float] = []

    if args.delay_source == "feedback":
        for row in packet_feedback_rows:
            send_time = as_int(row, "send_time_ms")
            recv_time = as_int(row, "recv_time_ms")
            size_bytes = as_int(row, "size_bytes")
            if send_time is None or recv_time is None or size_bytes is None:
                continue
            if recv_time < send_time:
                continue
            idx = max(0, (recv_time - origin_ms) // window_ms)
            send_idx = max(0, (send_time - origin_ms) // window_ms)
            sample_delay = float(recv_time - send_time)
            all_delay_samples.append(sample_delay)
            delay_bins[idx].append(sample_delay)
            acked_bins[idx] += size_bytes
            acked_send_bins[send_idx] += size_bytes
    else:
        (
            sender_by_ssrc_seq_rtp,
            sender_by_ssrc_seq,
            sender_by_seq,
        ) = build_sender_packet_indexes(send_rows)

        matched_packets = 0
        exact_matches = 0
        ssrc_seq_matches = 0
        seq_only_matches = 0
        rtp_timestamp_mismatches = 0
        used_packet_ids: set[int] = set()
        for row in receiver_packet_rows:
            seq_num = as_int(row, "seq_num")
            recv_time = as_int(row, "timestamp_ms")
            if seq_num is None or recv_time is None:
                continue
            candidate = None
            ssrc = as_int(row, "ssrc")
            recv_rtp_timestamp = as_int(row, "rtp_timestamp")
            if ssrc is not None and recv_rtp_timestamp is not None:
                candidate = pop_unused_candidate(
                    sender_by_ssrc_seq_rtp.get((ssrc, seq_num, recv_rtp_timestamp)),
                    used_packet_ids,
                )
                if candidate is not None:
                    exact_matches += 1
            if candidate is None and ssrc is not None:
                candidate = pop_unused_candidate(
                    sender_by_ssrc_seq.get((ssrc, seq_num)), used_packet_ids
                )
                if candidate is not None:
                    ssrc_seq_matches += 1
            if candidate is None:
                candidate = pop_unused_candidate(sender_by_seq.get(seq_num), used_packet_ids)
                if candidate is not None:
                    seq_only_matches += 1
            if candidate is None:
                continue
            send_rtp_timestamp = candidate.get("rtp_timestamp", -1)
            if (
                recv_rtp_timestamp is not None
                and send_rtp_timestamp >= 0
                and recv_rtp_timestamp != send_rtp_timestamp
            ):
                rtp_timestamp_mismatches += 1
            send_time = candidate["send_time_ms"]
            idx = max(0, (recv_time - origin_ms - args.arrival_shift_ms) // window_ms)
            sample_delay = float(recv_time - send_time)
            all_delay_samples.append(sample_delay)
            delay_bins[idx].append(sample_delay)
            matched_packets += 1
        if matched_packets == 0:
            raise ValueError(
                "receiver-arrival delay source found no matching seq_num rows"
            )
        note_bins[0].append(
            "receiver_arrival_matches:"
            f"exact={exact_matches},ssrc_seq={ssrc_seq_matches},"
            f"seq_only={seq_only_matches},"
            f"rtp_ts_mismatch={rtp_timestamp_mismatches}"
        )

    if rate_source == "acked" or not args.disable_loss:
        for row in packet_feedback_rows:
            send_time = as_int(row, "send_time_ms")
            recv_time = as_int(row, "recv_time_ms")
            size_bytes = as_int(row, "size_bytes")
            if send_time is None or recv_time is None or size_bytes is None:
                continue
            if recv_time < send_time:
                continue
            idx = max(0, (recv_time - origin_ms) // window_ms)
            send_idx = max(0, (send_time - origin_ms) // window_ms)
            acked_bins[idx] += size_bytes
            acked_send_bins[send_idx] += size_bytes

    base_delay_ms = (
        percentile(all_delay_samples, args.base_delay_percentile)
        if all_delay_samples
        else 0.0
    )
    if all_delay_samples:
        for idx, samples in list(delay_bins.items()):
            adjusted = [max(0.0, sample - base_delay_ms) for sample in samples]
            delay_bins[idx] = adjusted
            if len(adjusted) > 1:
                jitter_bins[idx] = adjusted

    if rate_source == "bwe":
        for row in bwe_rows:
            ts = as_int(row, "timestamp_ms")
            target_bps = as_int(row, "target_bps")
            if ts is None or target_bps is None:
                continue
            idx = max(0, (ts - origin_ms) // window_ms)
            bwe_bins[idx].append(target_bps / 1000.0)

    if rate_source == "send" or not args.disable_loss:
        for row in send_rows:
            ts = as_int(row, "send_time_ms")
            payload_size = as_int(row, "payload_size")
            if ts is None or payload_size is None:
                continue
            idx = max(0, (ts - origin_ms) // window_ms)
            send_bins[idx] += payload_size

    if not args.disable_loss:
        for idx, sent_bytes in send_bins.items():
            if sent_bytes <= 0:
                continue
            delivered_bytes = acked_send_bins.get(idx, 0)
            raw_loss = max(0.0, 1.0 - (delivered_bytes / float(sent_bytes)))
            loss_pct = min(args.max_loss_pct, raw_loss * 100.0)
            loss_bins[idx] = loss_pct

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
    for bucket in (delay_bins, acked_bins, acked_send_bins, bwe_bins, send_bins, jitter_bins, loss_bins, note_bins):
        if bucket:
            max_bin = max(max_bin, max(bucket))

    rows: list[dict[str, str]] = []
    previous_delay = 0.0
    previous_jitter = 0.0
    previous_loss = 0.0
    previous_rate: float | None = None
    previous_state: tuple[int, int, str, str, str] | None = None

    for idx in range(max_bin + 1):
        if idx in delay_bins and delay_bins[idx]:
            previous_delay = float(median(delay_bins[idx]))
        if idx in jitter_bins and jitter_bins[idx]:
            previous_jitter = pstdev(jitter_bins[idx]) if len(jitter_bins[idx]) > 1 else 0.0
        if idx in loss_bins:
            previous_loss = loss_bins[idx]

        current_rate: float | None = previous_rate
        if rate_source == "none":
            current_rate = None
        elif rate_source == "acked":
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
        q_jitter = max(0, quantize(previous_jitter, args.jitter_quantum_ms))
        if q_delay <= 0:
            q_jitter = 0
        q_loss_value = max(0.0, quantize_float(previous_loss, args.loss_quantum_pct))
        q_loss = "" if q_loss_value <= 0 else f"{q_loss_value:.1f}".rstrip("0").rstrip(".")
        q_rate = (
            ""
            if current_rate is None or current_rate <= 0
            else str(max(1, quantize(current_rate, args.rate_quantum_kbit)))
        )
        limit_pkts = str(args.limit_pkts) if q_rate else ""
        note = ";".join(note_bins.get(idx, []))
        state = (q_delay, q_jitter, q_loss, q_rate, limit_pkts)

        if idx == 0 or state != previous_state or note:
            rows.append(
                {
                    "at_ms": str(idx * window_ms),
                    "delay_ms": str(q_delay),
                    "jitter_ms": str(q_jitter),
                    "loss_pct": q_loss or "0",
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
        f"(delay_source={args.delay_source}, rate_source={rate_source}, "
        f"base_delay_ms={base_delay_ms:.1f}, "
        f"drop_warmup_sec={args.drop_warmup_sec}, "
        f"arrival_shift_ms={args.arrival_shift_ms})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(build_trace())
