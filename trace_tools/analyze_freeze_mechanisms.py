#!/usr/bin/env python3
"""Explain why video freezes can exceed receiver-side packet gaps.

The script correlates each freeze with frame-level observables:

- intra-frame packet span: last packet arrival - first packet arrival
- pre-freeze render gap: gap between rendered frames around freeze start
- RTP timestamp delta between rendered frames, as a source cadence proxy
- post-packet recovery: rendered frame time - last packet arrival
- frame buffer state around the freeze

It is intentionally read-only and works on existing run directories.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", help="Run directory containing receiver/")
    parser.add_argument("--side", default="receiver", choices=["receiver", "sender"])
    parser.add_argument("--window-ms", type=float, default=2000.0)
    parser.add_argument("--output", default=None)
    parser.add_argument("--top", type=int, default=20)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def percentile(values: list[float], pct: float) -> float | None:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return None
    k = (len(vals) - 1) * pct / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def load_packet_frame_stats(path: Path) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    packet_counts: dict[str, int] = defaultdict(int)
    for row in read_csv(path):
        rtp = row.get("rtp_timestamp")
        t = to_float(row.get("timestamp_ms"))
        if not rtp or t is None:
            continue
        grouped[rtp].append(t)
        packet_counts[rtp] += 1
    stats: dict[str, dict[str, float]] = {}
    for rtp, times in grouped.items():
        first = min(times)
        last = max(times)
        stats[rtp] = {
            "first_packet_ms": first,
            "last_packet_ms": last,
            "packet_span_ms": last - first,
            "packet_count": packet_counts[rtp],
        }
    return stats


def load_rendered(path: Path) -> list[dict[str, float | str]]:
    rows = []
    for row in read_csv(path):
        t = to_float(row.get("timestamp_ms"))
        render_time = to_float(row.get("render_time_ms"))
        if t is None:
            continue
        rows.append(
            {
                "timestamp_ms": t,
                "render_time_ms": render_time if render_time is not None else t,
                "rtp_timestamp": row.get("rtp_timestamp", ""),
                "packet_count": to_float(row.get("packet_count")) or 0.0,
                "width": row.get("width", ""),
                "height": row.get("height", ""),
            }
        )
    return sorted(rows, key=lambda r: float(r["timestamp_ms"]))


def rtp_delta_ms(prev_rtp: str, next_rtp: str) -> float | None:
    try:
        prev = int(prev_rtp)
        nxt = int(next_rtp)
    except ValueError:
        return None
    return ((nxt - prev) & 0xFFFFFFFF) / 90.0


def frame_buffer_window(path: Path, start_ms: float, end_ms: float) -> dict[str, float | str]:
    playable = []
    continuous = []
    buffer_size = []
    drops = 0
    inserted = 0
    delayed_retx = 0
    for row in read_csv(path):
        t = to_float(row.get("timestamp_ms"))
        if t is None or t < start_ms or t > end_ms:
            continue
        for target, key in [
            (playable, "playable_units"),
            (continuous, "continuous_units"),
            (buffer_size, "buffer_size"),
        ]:
            value = to_float(row.get(key))
            if value is not None:
                target.append(value)
        if row.get("drop_reason"):
            drops += 1
        if row.get("inserted") == "1":
            inserted += 1
        if row.get("delayed_by_retransmission") == "1":
            delayed_retx += 1
    return {
        "playable_min": min(playable) if playable else "",
        "playable_max": max(playable) if playable else "",
        "continuous_min": min(continuous) if continuous else "",
        "continuous_max": max(continuous) if continuous else "",
        "buffer_size_min": min(buffer_size) if buffer_size else "",
        "buffer_size_max": max(buffer_size) if buffer_size else "",
        "frame_buffer_drops": drops,
        "frame_buffer_inserted": inserted,
        "delayed_by_retransmission": delayed_retx,
    }


def classify(row: dict[str, object]) -> str:
    freeze = float(row["freeze_duration_ms"])
    video_gap = float(row["render_gap_ms"])
    rtp_delta = row.get("rtp_delta_ms")
    packet_span = row.get("next_frame_packet_span_ms")
    post_recovery = row.get("post_packet_recovery_ms")
    playable_min = row.get("playable_min")

    labels = []
    if isinstance(rtp_delta, (int, float)) and rtp_delta > video_gap * 0.7 and rtp_delta > 150:
        labels.append("source_cadence_or_frame_drop")
    if isinstance(packet_span, (int, float)) and packet_span > 100:
        labels.append("intra_frame_packet_spread")
    if isinstance(post_recovery, (int, float)) and post_recovery > 80:
        labels.append("decode_render_recovery")
    if isinstance(playable_min, (int, float)) and playable_min <= 1:
        labels.append("buffer_depleted")
    if freeze > video_gap + 100:
        labels.append("freeze_amplified_after_gap")
    return "+".join(labels) if labels else "small_or_ambiguous"


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    side_dir = run_dir / args.side
    packet_stats = load_packet_frame_stats(side_dir / "packet_buffer_inserts.csv")
    rendered = load_rendered(side_dir / "receiver_rendered_frames.csv")
    freezes = []
    for row in read_csv(side_dir / "video_freeze.csv"):
        end = to_float(row.get("timestamp_ms"))
        duration = to_float(row.get("freeze_duration_ms"))
        if end is None or duration is None:
            continue
        freezes.append({"start_ms": end - duration, "end_ms": end, "duration_ms": duration})

    if not rendered:
        raise SystemExit("no rendered frames")
    render_times = [float(r["timestamp_ms"]) for r in rendered]
    run_start = render_times[0]
    rows: list[dict[str, object]] = []
    for idx, freeze in enumerate(freezes, start=1):
        fs = float(freeze["start_ms"])
        pos = bisect.bisect_left(render_times, fs)
        prev_idx = max(0, pos - 1)
        next_idx = min(len(rendered) - 1, pos)
        prev_frame = rendered[prev_idx]
        next_frame = rendered[next_idx]
        render_gap = float(next_frame["timestamp_ms"]) - float(prev_frame["timestamp_ms"])
        next_rtp = str(next_frame["rtp_timestamp"])
        prev_rtp = str(prev_frame["rtp_timestamp"])
        next_packet = packet_stats.get(next_rtp, {})
        prev_packet = packet_stats.get(prev_rtp, {})
        next_last = next_packet.get("last_packet_ms")
        next_first = next_packet.get("first_packet_ms")
        next_packet_span = next_packet.get("packet_span_ms")
        post_recovery = (
            float(next_frame["timestamp_ms"]) - float(next_last)
            if isinstance(next_last, (int, float))
            else ""
        )
        window_stats = frame_buffer_window(
            side_dir / "frame_buffer.csv", fs - args.window_ms, float(freeze["end_ms"]) + args.window_ms
        )
        row: dict[str, object] = {
            "run": run_dir.name,
            "side": args.side,
            "freeze_id": idx,
            "freeze_start_rel_s": round((fs - run_start) / 1000.0, 3),
            "freeze_duration_ms": round(float(freeze["duration_ms"]), 3),
            "prev_render_rel_s": round((float(prev_frame["timestamp_ms"]) - run_start) / 1000.0, 3),
            "next_render_rel_s": round((float(next_frame["timestamp_ms"]) - run_start) / 1000.0, 3),
            "render_gap_ms": round(render_gap, 3),
            "freeze_minus_render_gap_ms": round(float(freeze["duration_ms"]) - render_gap, 3),
            "prev_rtp_timestamp": prev_rtp,
            "next_rtp_timestamp": next_rtp,
            "rtp_delta_ms": round(rtp_delta_ms(prev_rtp, next_rtp) or 0.0, 3),
            "prev_frame_packet_span_ms": round(prev_packet.get("packet_span_ms", 0.0), 3)
            if prev_packet
            else "",
            "next_frame_packet_span_ms": round(next_packet_span, 3)
            if isinstance(next_packet_span, (int, float))
            else "",
            "next_frame_packet_count": int(next_packet.get("packet_count", 0)) if next_packet else "",
            "next_frame_first_packet_rel_s": round((float(next_first) - run_start) / 1000.0, 3)
            if isinstance(next_first, (int, float))
            else "",
            "next_frame_last_packet_rel_s": round((float(next_last) - run_start) / 1000.0, 3)
            if isinstance(next_last, (int, float))
            else "",
            "post_packet_recovery_ms": round(post_recovery, 3)
            if isinstance(post_recovery, (int, float))
            else "",
            **window_stats,
        }
        row["mechanism_guess"] = classify(row)
        rows.append(row)

    out_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else run_dir / "analysis" / f"freeze_mechanisms_{args.side}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["run"])
        writer.writeheader()
        writer.writerows(rows)

    amplified = [r for r in rows if float(r["freeze_minus_render_gap_ms"]) > 0]
    packet_spans = [
        float(r["next_frame_packet_span_ms"])
        for r in rows
        if isinstance(r.get("next_frame_packet_span_ms"), (int, float))
        or str(r.get("next_frame_packet_span_ms", "")).replace(".", "", 1).isdigit()
    ]
    print(f"run={run_dir.name} freezes={len(rows)} output={out_path}")
    print(f"freeze_gt_render_gap={len(amplified)}")
    if packet_spans:
        print(
            "next_frame_packet_span_ms "
            f"p50={percentile(packet_spans, 50):.1f} "
            f"p95={percentile(packet_spans, 95):.1f} max={max(packet_spans):.1f}"
        )
    print("top_freeze_amplification:")
    for row in sorted(rows, key=lambda r: float(r["freeze_minus_render_gap_ms"]), reverse=True)[: args.top]:
        print(
            f"  id={row['freeze_id']} rel={row['freeze_start_rel_s']}s "
            f"freeze={row['freeze_duration_ms']}ms render_gap={row['render_gap_ms']}ms "
            f"rtp_delta={row['rtp_delta_ms']}ms packet_span={row['next_frame_packet_span_ms']}ms "
            f"post_recovery={row['post_packet_recovery_ms']}ms "
            f"playable_min={row['playable_min']} cause={row['mechanism_guess']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
