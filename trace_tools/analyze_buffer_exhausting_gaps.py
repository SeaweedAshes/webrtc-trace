#!/usr/bin/env python3
"""Analyze receiver-side buffer-exhausting video packet gap episodes."""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Iterable


OVERUSING_STATE = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find receiver-side video packet interval episodes where the packet "
            "gap exhausts the currently playable video buffer."
        )
    )
    parser.add_argument("run_dir", help="Run directory containing receiver/ and sender/")
    parser.add_argument("--side", default="receiver", choices=["receiver", "sender"])
    parser.add_argument("--window-ms", type=float, default=200.0)
    parser.add_argument("--hold-ms", type=float, default=200.0)
    parser.add_argument("--association-ms", type=float, default=500.0)
    parser.add_argument("--warmup-sec", type=float, default=5.0)
    parser.add_argument(
        "--frame-interval-ms",
        type=float,
        default=40.0,
        help="Nominal frame interval. Default is 40ms for 25fps.",
    )
    parser.add_argument(
        "--frame-interval-mode",
        default="nominal",
        choices=["nominal", "observed"],
        help="Use fixed --frame-interval-ms or observed median render interval.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Default: <run_dir>/analysis",
    )
    parser.add_argument("--top", type=int, default=30)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def to_float(value: str | None, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def percentile(values: Iterable[float], pct: float) -> float | None:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    k = (len(vals) - 1) * pct / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def median_or_none(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return median(vals) if vals else None


@dataclass
class Gap:
    idx: int
    start_ms: float
    end_ms: float
    gap_ms: float
    baseline_p75_ms: float | None
    playable_units: int | None
    continuous_units: int | None
    buffer_size: int | None
    budget_ms: float | None
    buffer_exhausting: bool
    elevated: bool


@dataclass
class Episode:
    episode_id: int
    start_ms: float
    end_ms: float
    peak_gap: Gap
    gaps: list[Gap]


def load_packet_times(path: Path, time_col: str) -> list[float]:
    times: list[float] = []
    for row in read_csv(path):
        t = to_float(row.get(time_col))
        if t is not None:
            times.append(t)
    return sorted(times)


def load_video_gaps(path: Path, warmup_start_ms: float) -> list[tuple[float, float, float]]:
    rows = read_csv(path)
    times: list[float] = []
    order_offset = 0.0
    last_t: float | None = None
    # Preserve CSV order for packets with identical timestamps. The sub-ms offset is
    # only for stable ordering; reported intervals are still based on logged ms.
    ordered: list[tuple[float, float]] = []
    for row in rows:
        t = to_float(row.get("timestamp_ms"))
        if t is None:
            continue
        if last_t is None or t != last_t:
            order_offset = 0.0
            last_t = t
        else:
            order_offset += 1e-6
        ordered.append((t + order_offset, t))
    ordered.sort(key=lambda x: x[0])
    times = [raw_t for _, raw_t in ordered if raw_t >= warmup_start_ms]
    gaps: list[tuple[float, float, float]] = []
    for i in range(len(times) - 1):
        start = times[i]
        end = times[i + 1]
        gaps.append((start, end, max(0.0, end - start)))
    return gaps


def load_frame_states(path: Path) -> tuple[list[float], list[dict[str, int]]]:
    rows = read_csv(path)
    times: list[float] = []
    states: list[dict[str, int]] = []
    for row in rows:
        t = to_float(row.get("timestamp_ms"))
        if t is None:
            continue
        times.append(t)
        states.append(
            {
                "playable_units": int(to_float(row.get("playable_units"), 0) or 0),
                "continuous_units": int(to_float(row.get("continuous_units"), 0) or 0),
                "buffer_size": int(to_float(row.get("buffer_size"), 0) or 0),
            }
        )
    return times, states


def latest_state(
    state_times: list[float], states: list[dict[str, int]], t: float
) -> dict[str, int] | None:
    idx = bisect.bisect_right(state_times, t) - 1
    if idx < 0:
        return None
    return states[idx]


def compute_baselines(raw_gaps: list[tuple[float, float, float]], window_ms: float) -> list[float | None]:
    baselines: list[float | None] = []
    window: deque[tuple[float, float]] = deque()
    for start, _end, gap in raw_gaps:
        cutoff = start - window_ms
        while window and window[0][0] < cutoff:
            window.popleft()
        baselines.append(percentile((g for _t, g in window), 75.0))
        window.append((start, gap))
    return baselines


def build_gaps(
    raw_gaps: list[tuple[float, float, float]],
    state_times: list[float],
    states: list[dict[str, int]],
    frame_interval_ms: float,
    window_ms: float,
) -> list[Gap]:
    baselines = compute_baselines(raw_gaps, window_ms)
    gaps: list[Gap] = []
    for idx, ((start, end, gap_ms), baseline) in enumerate(zip(raw_gaps, baselines)):
        state = latest_state(state_times, states, start)
        playable = state["playable_units"] if state else None
        continuous = state["continuous_units"] if state else None
        buffer_size = state["buffer_size"] if state else None
        budget = playable * frame_interval_ms if playable is not None else None
        buffer_exhausting = budget is not None and gap_ms > budget
        elevated_floor = max(
            baseline if baseline is not None else 0.0,
            budget if budget is not None else 0.0,
        )
        elevated = gap_ms > elevated_floor
        gaps.append(
            Gap(
                idx=idx,
                start_ms=start,
                end_ms=end,
                gap_ms=gap_ms,
                baseline_p75_ms=baseline,
                playable_units=playable,
                continuous_units=continuous,
                buffer_size=buffer_size,
                budget_ms=budget,
                buffer_exhausting=buffer_exhausting,
                elevated=elevated,
            )
        )
    return gaps


def build_episodes(gaps: list[Gap], hold_ms: float) -> list[Episode]:
    episodes: list[Episode] = []
    active: list[Gap] = []
    below_since: float | None = None
    episode_id = 1

    def flush() -> None:
        nonlocal active, below_since, episode_id
        if not active:
            return
        if any(g.buffer_exhausting for g in active):
            exhausting = [g for g in active if g.buffer_exhausting]
            peak = max(exhausting or active, key=lambda g: g.gap_ms)
            episodes.append(
                Episode(
                    episode_id=episode_id,
                    start_ms=active[0].start_ms,
                    end_ms=active[-1].end_ms,
                    peak_gap=peak,
                    gaps=list(active),
                )
            )
            episode_id += 1
        active = []
        below_since = None

    for gap in gaps:
        starts_episode = gap.elevated and gap.buffer_exhausting
        if not active:
            if starts_episode:
                active = [gap]
                below_since = None
            continue

        active.append(gap)
        if gap.elevated:
            below_since = None
        else:
            if below_since is None:
                below_since = gap.start_ms
            if gap.end_ms - below_since >= hold_ms:
                flush()

    flush()
    return episodes


def load_freezes(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        return []
    freezes: list[dict[str, float]] = []
    for row in read_csv(path):
        end = to_float(row.get("timestamp_ms"))
        duration = to_float(row.get("freeze_duration_ms"))
        if end is None or duration is None:
            continue
        freezes.append(
            {
                "freeze_start_ms": end - duration,
                "freeze_end_ms": end,
                "freeze_duration_ms": duration,
            }
        )
    return freezes


def associated_freeze(
    episode: Episode, freezes: list[dict[str, float]], association_ms: float
) -> dict[str, float] | None:
    candidates: list[tuple[float, dict[str, float]]] = []
    ep_start = episode.start_ms
    ep_end = episode.end_ms
    for freeze in freezes:
        fs = freeze["freeze_start_ms"]
        fe = freeze["freeze_end_ms"]
        overlaps = ep_end >= fs - association_ms and ep_start <= fe + association_ms
        if not overlaps:
            continue
        dist = min(abs(episode.peak_gap.start_ms - fs), abs(episode.peak_gap.end_ms - fs))
        candidates.append((dist, freeze))
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]


def intervals_in_window(times: list[float], start_ms: float, end_ms: float) -> list[float]:
    left = bisect.bisect_left(times, start_ms)
    right = bisect.bisect_right(times, end_ms)
    window = times[left:right]
    return [max(0.0, b - a) for a, b in zip(window, window[1:])]


def load_bwe_states(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        return []
    states: list[dict[str, float]] = []
    for row in read_csv(path):
        t = to_float(row.get("at_time_ms"))
        target = to_float(row.get("target_bps"))
        state = to_float(row.get("detector_state"))
        if t is None:
            continue
        states.append(
            {
                "time_ms": t,
                "target_bps": target if target is not None else float("nan"),
                "detector_state": state if state is not None else float("nan"),
            }
        )
    return sorted(states, key=lambda x: x["time_ms"])


def bwe_window(states: list[dict[str, float]], start_ms: float, end_ms: float) -> list[dict[str, float]]:
    times = [s["time_ms"] for s in states]
    left = bisect.bisect_left(times, start_ms)
    right = bisect.bisect_right(times, end_ms)
    return states[left:right]


def observed_frame_interval(rendered_frames_path: Path, fallback_ms: float) -> float:
    if not rendered_frames_path.exists():
        return fallback_ms
    rows = read_csv(rendered_frames_path)
    times = [to_float(r.get("timestamp_ms")) for r in rows]
    vals = [t for t in times if t is not None]
    gaps = [b - a for a, b in zip(vals, vals[1:]) if b >= a and 0 < b - a < 250]
    return median(gaps) if gaps else fallback_ms


def write_episode_csv(
    episodes: list[Episode],
    out_path: Path,
    run_name: str,
    side: str,
    run_start_ms: float,
    freezes: list[dict[str, float]],
    audio_times: list[float],
    audio_baseline_p95_ms: float | None,
    bwe_states: list[dict[str, float]],
    association_ms: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for episode in episodes:
        peak = episode.peak_gap
        freeze = associated_freeze(episode, freezes, association_ms)
        audio_episode = intervals_in_window(audio_times, episode.start_ms, episode.end_ms)
        audio_peak = intervals_in_window(audio_times, peak.start_ms - 1000.0, peak.end_ms + 1000.0)
        before = bwe_window(bwe_states, peak.start_ms - 2000.0, peak.start_ms)
        after = bwe_window(bwe_states, peak.end_ms, peak.end_ms + 10000.0)
        overuse = bwe_window(bwe_states, episode.start_ms, episode.end_ms + 5000.0)
        before_targets = [s["target_bps"] for s in before if math.isfinite(s["target_bps"])]
        after_targets = [s["target_bps"] for s in after if math.isfinite(s["target_bps"])]
        target_before = median_or_none(before_targets)
        target_after_min = min(after_targets) if after_targets else None
        target_delta = (
            target_after_min - target_before
            if target_before is not None and target_after_min is not None
            else None
        )
        overusing_count = sum(1 for s in overuse if s["detector_state"] == OVERUSING_STATE)
        row: dict[str, object] = {
            "run": run_name,
            "side": side,
            "episode_id": episode.episode_id,
            "episode_start_ms": round(episode.start_ms, 3),
            "episode_end_ms": round(episode.end_ms, 3),
            "episode_start_rel_s": round((episode.start_ms - run_start_ms) / 1000.0, 3),
            "episode_duration_ms": round(episode.end_ms - episode.start_ms, 3),
            "num_gaps": len(episode.gaps),
            "num_buffer_exhausting_gaps": sum(g.buffer_exhausting for g in episode.gaps),
            "peak_start_ms": round(peak.start_ms, 3),
            "peak_end_ms": round(peak.end_ms, 3),
            "peak_rel_s": round((peak.start_ms - run_start_ms) / 1000.0, 3),
            "peak_gap_ms": round(peak.gap_ms, 3),
            "peak_baseline_p75_ms": round(peak.baseline_p75_ms, 3)
            if peak.baseline_p75_ms is not None
            else "",
            "peak_playable_units": peak.playable_units if peak.playable_units is not None else "",
            "peak_continuous_units": peak.continuous_units if peak.continuous_units is not None else "",
            "peak_buffer_size": peak.buffer_size if peak.buffer_size is not None else "",
            "peak_playout_budget_ms": round(peak.budget_ms, 3) if peak.budget_ms is not None else "",
            "associated_freeze": 1 if freeze else 0,
            "freeze_start_rel_s": round((freeze["freeze_start_ms"] - run_start_ms) / 1000.0, 3)
            if freeze
            else "",
            "freeze_duration_ms": round(freeze["freeze_duration_ms"], 3) if freeze else "",
            "audio_episode_gap_p95_ms": round(percentile(audio_episode, 95.0), 3)
            if audio_episode
            else "",
            "audio_episode_gap_max_ms": round(max(audio_episode), 3) if audio_episode else "",
            "audio_peak_gap_p95_ms": round(percentile(audio_peak, 95.0), 3) if audio_peak else "",
            "audio_peak_gap_max_ms": round(max(audio_peak), 3) if audio_peak else "",
            "audio_baseline_gap_p95_ms": round(audio_baseline_p95_ms, 3)
            if audio_baseline_p95_ms is not None
            else "",
            "gcc_overusing_count": overusing_count,
            "gcc_target_before_bps": round(target_before, 3) if target_before is not None else "",
            "gcc_target_after_min_bps": round(target_after_min, 3)
            if target_after_min is not None
            else "",
            "gcc_target_delta_min_after_bps": round(target_delta, 3) if target_delta is not None else "",
            "gcc_target_decreased": 1 if target_delta is not None and target_delta < 0 else 0,
        }
        rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with out_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        with out_path.open("w", newline="") as f:
            f.write("run,side,episode_id\n")
    return rows


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    side_dir = run_dir / args.side
    packet_path = side_dir / "packet_buffer_inserts.csv"
    frame_path = side_dir / "frame_buffer.csv"
    audio_path = side_dir / "audio_packet_inserts.csv"
    freeze_path = side_dir / "video_freeze.csv"
    bwe_path = side_dir / "delay_bwe_state.csv"
    rendered_path = side_dir / "receiver_rendered_frames.csv"

    for required in [packet_path, frame_path]:
        if not required.exists():
            raise SystemExit(f"missing required file: {required}")

    first_packet = to_float(read_csv(packet_path)[0].get("timestamp_ms"))
    if first_packet is None:
        raise SystemExit(f"no packet timestamps in {packet_path}")
    warmup_start_ms = first_packet + args.warmup_sec * 1000.0

    frame_interval_ms = args.frame_interval_ms
    if args.frame_interval_mode == "observed":
        frame_interval_ms = observed_frame_interval(rendered_path, args.frame_interval_ms)

    raw_gaps = load_video_gaps(packet_path, warmup_start_ms)
    state_times, states = load_frame_states(frame_path)
    gaps = build_gaps(raw_gaps, state_times, states, frame_interval_ms, args.window_ms)
    episodes = build_episodes(gaps, args.hold_ms)

    audio_times = load_packet_times(audio_path, "wall_time_ms") if audio_path.exists() else []
    audio_intervals = [b - a for a, b in zip(audio_times, audio_times[1:]) if b >= a]
    audio_baseline_p95 = percentile(audio_intervals, 95.0)
    freezes = load_freezes(freeze_path)
    bwe_states = load_bwe_states(bwe_path)

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else run_dir / "analysis"
    suffix = f"{args.side}_{int(args.window_ms)}ms_{args.frame_interval_mode}"
    csv_path = output_dir / f"buffer_exhausting_gap_episodes_{suffix}.csv"
    rows = write_episode_csv(
        episodes,
        csv_path,
        run_dir.name,
        args.side,
        first_packet,
        freezes,
        audio_times,
        audio_baseline_p95,
        bwe_states,
        args.association_ms,
    )

    associated = [r for r in rows if r.get("associated_freeze") == 1]
    decreased = [r for r in rows if r.get("gcc_target_decreased") == 1]
    print(f"run={run_dir.name} side={args.side}")
    print(f"frame_interval_ms={frame_interval_ms:.3f} mode={args.frame_interval_mode}")
    print(f"video_gaps={len(gaps)} episodes={len(rows)} associated_freeze={len(associated)}")
    print(f"gcc_target_decreased_episodes={len(decreased)} output={csv_path}")
    print("top_episodes_by_peak_gap:")
    top_rows = sorted(rows, key=lambda r: float(r["peak_gap_ms"]), reverse=True)[: args.top]
    for row in top_rows:
        print(
            "  "
            f"id={row['episode_id']} rel={row['peak_rel_s']}s "
            f"gap={row['peak_gap_ms']}ms budget={row['peak_playout_budget_ms']}ms "
            f"playable={row['peak_playable_units']} freeze={row['associated_freeze']} "
            f"freeze_dur={row['freeze_duration_ms']}ms "
            f"audio_peak_max={row['audio_peak_gap_max_ms']}ms "
            f"overuse={row['gcc_overusing_count']} "
            f"target_delta={row['gcc_target_delta_min_after_bps']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
