#!/usr/bin/env python3
"""Build a packet-level netem trace from matched WebRTC RTP logs.

The output is compatible with replay_netem_trace.py:

    at_ms,delay_ms,jitter_ms,loss_pct,rate_kbit,limit_pkts,note

Unlike build_trace_from_logs.py, this tool does not aggregate delay into a
fixed-size time window. It emits one qdisc state change per matched receiver
video RTP packet, scheduled on the original sender-relative packet timeline by
default. This is still a live-netem replay, not an RTP packet dump replay: the
new WebRTC session may diverge if pacing, encoder output, or GCC state changes.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict, deque
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender-log-dir", required=True, type=Path)
    parser.add_argument("--receiver-log-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--drop-warmup-sec",
        type=float,
        default=0.0,
        help="drop the first N seconds of receiver packet arrivals before matching",
    )
    parser.add_argument(
        "--base-delay-percentile",
        type=float,
        default=5.0,
        help="subtract this percentile from matched one-way delay samples",
    )
    parser.add_argument(
        "--schedule-source",
        choices=("send", "arrival"),
        default="send",
        help=(
            "send=schedule qdisc changes at original sender-relative send time; "
            "arrival=schedule at receiver-arrival-relative time"
        ),
    )
    parser.add_argument(
        "--delay-quantum-ms",
        type=int,
        default=1,
        help="round delay to this many milliseconds",
    )
    parser.add_argument(
        "--min-delay-ms",
        type=int,
        default=0,
        help="floor adjusted delay before quantization",
    )
    parser.add_argument(
        "--max-delay-ms",
        type=int,
        default=5000,
        help="cap adjusted delay before quantization",
    )
    parser.add_argument(
        "--delay-scale",
        type=float,
        default=1.0,
        help="multiply adjusted delay by this factor before quantization",
    )
    parser.add_argument(
        "--delay-offset-ms",
        type=float,
        default=0.0,
        help="add this many ms to adjusted delay after scaling",
    )
    parser.add_argument(
        "--emit-duplicates",
        action="store_true",
        help="emit rows even when consecutive packet delay state is unchanged",
    )
    parser.add_argument(
        "--coalesce-ms",
        type=int,
        default=0,
        help=(
            "combine packet events in this schedule-time bucket before emitting "
            "netem rows; 0 keeps one event per matched packet"
        ),
    )
    parser.add_argument(
        "--coalesce-stat",
        choices=("median", "p75", "p95", "max"),
        default="median",
        help="delay statistic to use inside each --coalesce-ms bucket",
    )
    parser.add_argument(
        "--coalesce-by-frame",
        action="store_true",
        help=(
            "combine packets with the same SSRC/RTP timestamp into one frame-level "
            "event before optional --coalesce-ms bucketing"
        ),
    )
    parser.add_argument(
        "--delay-deadband-ms",
        type=int,
        default=0,
        help="skip emitted rows whose delay differs from the previous row by <= N ms",
    )
    parser.add_argument(
        "--arrival-shift-ms",
        type=int,
        default=0,
        help="only for --schedule-source arrival; shift schedule earlier by N ms",
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


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values available")
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def quantize(value: float, quantum: int) -> int:
    if quantum <= 1:
        return int(round(value))
    return int(round(value / quantum) * quantum)


def pick_stat(values: list[int], stat: str) -> int:
    if not values:
        raise ValueError("cannot pick statistic from empty values")
    ordered = sorted(values)
    if stat == "max":
        return ordered[-1]
    pct_by_stat = {"median": 50.0, "p75": 75.0, "p95": 95.0}
    return int(round(percentile([float(v) for v in ordered], pct_by_stat[stat])))


def is_video_send_row(row: dict[str, str]) -> bool:
    if row.get("is_audio") not in ("", "0"):
        return False
    packet_type = row.get("packet_type", "").strip()
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
            "ssrc": as_int(row, "ssrc") or -1,
            "seq_num": seq_num,
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


def main() -> int:
    args = parse_args()
    sender_dir = args.sender_log_dir
    receiver_dir = args.receiver_log_dir
    if not sender_dir.is_dir():
        raise NotADirectoryError(f"sender log dir not found: {sender_dir}")
    if not receiver_dir.is_dir():
        raise NotADirectoryError(f"receiver log dir not found: {receiver_dir}")

    send_rows = read_rows(sender_dir / "rtp_send.csv")
    receiver_packet_rows = read_rows(receiver_dir / "packet_buffer_inserts.csv")
    if not send_rows:
        raise FileNotFoundError(sender_dir / "rtp_send.csv")
    if not receiver_packet_rows:
        raise FileNotFoundError(receiver_dir / "packet_buffer_inserts.csv")

    warmup_ms = int(args.drop_warmup_sec * 1000.0)
    if warmup_ms > 0:
        first_recv = min(
            ts
            for row in receiver_packet_rows
            for ts in [as_int(row, "timestamp_ms")]
            if ts is not None
        )
        cutoff = first_recv + warmup_ms
        receiver_packet_rows = [
            row
            for row in receiver_packet_rows
            if (ts := as_int(row, "timestamp_ms")) is not None and ts >= cutoff
        ]

    by_ssrc_seq_rtp, by_ssrc_seq, by_seq = build_sender_packet_indexes(send_rows)
    used_packet_ids: set[int] = set()
    matched: list[dict[str, int]] = []
    exact_matches = 0
    ssrc_seq_matches = 0
    seq_only_matches = 0
    rtp_timestamp_mismatches = 0

    for row in receiver_packet_rows:
        seq_num = as_int(row, "seq_num")
        recv_time = as_int(row, "timestamp_ms")
        if seq_num is None or recv_time is None:
            continue
        ssrc = as_int(row, "ssrc")
        recv_rtp_timestamp = as_int(row, "rtp_timestamp")
        candidate = None
        if ssrc is not None and recv_rtp_timestamp is not None:
            candidate = pop_unused_candidate(
                by_ssrc_seq_rtp.get((ssrc, seq_num, recv_rtp_timestamp)),
                used_packet_ids,
            )
            if candidate is not None:
                exact_matches += 1
        if candidate is None and ssrc is not None:
            candidate = pop_unused_candidate(
                by_ssrc_seq.get((ssrc, seq_num)), used_packet_ids
            )
            if candidate is not None:
                ssrc_seq_matches += 1
        if candidate is None:
            candidate = pop_unused_candidate(by_seq.get(seq_num), used_packet_ids)
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
        matched.append(
            {
                "send_time_ms": candidate["send_time_ms"],
                "recv_time_ms": recv_time,
                "ssrc": ssrc or -1,
                "seq_num": seq_num,
                "rtp_timestamp": recv_rtp_timestamp or -1,
            }
        )

    if not matched:
        raise ValueError("no matched video RTP packets")

    raw_delays = [m["recv_time_ms"] - m["send_time_ms"] for m in matched]
    base_delay_ms = percentile([float(d) for d in raw_delays], args.base_delay_percentile)
    send_origin_ms = min(m["send_time_ms"] for m in matched)
    recv_origin_ms = min(m["recv_time_ms"] for m in matched)

    packet_events: list[dict[str, int]] = []
    for m in sorted(
        matched,
        key=lambda item: (
            item["send_time_ms"] if args.schedule_source == "send" else item["recv_time_ms"],
            item["seq_num"],
        ),
    ):
        if args.schedule_source == "send":
            at_ms = m["send_time_ms"] - send_origin_ms
        else:
            at_ms = m["recv_time_ms"] - recv_origin_ms - args.arrival_shift_ms
        at_ms = max(0, at_ms)

        adjusted_delay = (m["recv_time_ms"] - m["send_time_ms"]) - base_delay_ms
        adjusted_delay = adjusted_delay * args.delay_scale + args.delay_offset_ms
        adjusted_delay = min(args.max_delay_ms, max(args.min_delay_ms, adjusted_delay))
        delay_ms = quantize(adjusted_delay, args.delay_quantum_ms)
        packet_events.append(
            {
                "at_ms": int(at_ms),
                "delay_ms": delay_ms,
                "ssrc": m["ssrc"],
                "seq_num": m["seq_num"],
                "rtp_timestamp": m["rtp_timestamp"],
            }
        )

    if args.coalesce_by_frame:
        frames: dict[tuple[int, int], list[dict[str, int]]] = defaultdict(list)
        for event in packet_events:
            frames[(event["ssrc"], event["rtp_timestamp"])].append(event)
        packet_events = []
        for frame_key in sorted(
            frames,
            key=lambda key: min(event["at_ms"] for event in frames[key]),
        ):
            frame = frames[frame_key]
            delays = [event["delay_ms"] for event in frame]
            first = min(frame, key=lambda event: (event["at_ms"], event["seq_num"]))
            packet_events.append(
                {
                    "at_ms": first["at_ms"],
                    "delay_ms": pick_stat(delays, args.coalesce_stat),
                    "ssrc": first["ssrc"],
                    "seq_num": first["seq_num"],
                    "rtp_timestamp": first["rtp_timestamp"],
                    "packet_count": len(frame),
                }
            )

    if args.coalesce_ms > 0:
        buckets: dict[int, list[dict[str, int]]] = defaultdict(list)
        for event in packet_events:
            bucket_at_ms = (event["at_ms"] // args.coalesce_ms) * args.coalesce_ms
            buckets[bucket_at_ms].append(event)
        packet_events = []
        for bucket_at_ms in sorted(buckets):
            bucket = buckets[bucket_at_ms]
            delays = [event["delay_ms"] for event in bucket]
            first = min(bucket, key=lambda event: event["seq_num"])
            packet_events.append(
                {
                    "at_ms": bucket_at_ms,
                    "delay_ms": pick_stat(delays, args.coalesce_stat),
                    "ssrc": first["ssrc"],
                    "seq_num": first["seq_num"],
                    "rtp_timestamp": first["rtp_timestamp"],
                    "packet_count": len(bucket),
                }
            )

    rows: list[dict[str, str]] = []
    previous_delay: int | None = None
    previous_state: tuple[int, str, str, str] | None = None
    for event in packet_events:
        delay_ms = event["delay_ms"]
        state = (delay_ms, "0", "0", "")
        if (
            previous_delay is not None
            and args.delay_deadband_ms > 0
            and abs(delay_ms - previous_delay) <= args.delay_deadband_ms
        ):
            continue
        if not args.emit_duplicates and state == previous_state:
            continue
        packet_count = event.get("packet_count", 1)
        rows.append(
            {
                "at_ms": str(event["at_ms"]),
                "delay_ms": str(delay_ms),
                "jitter_ms": "0",
                "loss_pct": "0",
                "rate_kbit": "",
                "limit_pkts": "",
                "note": (
                    f"pkt:ssrc={event['ssrc']},seq={event['seq_num']},"
                    f"rtp={event['rtp_timestamp']},n={packet_count}"
                ),
            }
        )
        previous_delay = delay_ms
        previous_state = state

    if not rows:
        raise ValueError("packet-level trace generation produced no rows")
    if rows[0]["at_ms"] != "0":
        rows.insert(
            0,
            {
                "at_ms": "0",
                "delay_ms": "0",
                "jitter_ms": "0",
                "loss_pct": "0",
                "rate_kbit": "",
                "limit_pkts": "",
                "note": "baseline",
            },
        )

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
        f"(matched_packets={len(matched)}, schedule_source={args.schedule_source}, "
        f"base_delay_ms={base_delay_ms:.1f}, exact={exact_matches}, "
        f"ssrc_seq={ssrc_seq_matches}, seq_only={seq_only_matches}, "
        f"rtp_ts_mismatch={rtp_timestamp_mismatches}, "
        f"drop_warmup_sec={args.drop_warmup_sec}, "
        f"coalesce_ms={args.coalesce_ms}, "
        f"coalesce_by_frame={args.coalesce_by_frame}, "
        f"delay_scale={args.delay_scale}, "
        f"delay_offset_ms={args.delay_offset_ms}, "
        f"delay_deadband_ms={args.delay_deadband_ms})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
