#!/usr/bin/env python3
"""Build display-switch decision samples from original WebRTC collection logs.

The dataset is for the receiver visible switch decision:
  "Should generated video be visible at this receiver timestamp?"

It intentionally uses receiver-side render/freeze/audio/video/frame signals and
does not use root-cause labels as the primary label.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_RUNS_ROOT = Path("/home/widen/Sync/webrtc-trace-runs")
DEFAULT_INPUT_ROOT = Path("/home/widen/webrtc-checkout/analysis_runs")
DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/switch_score_policy_eval_original_only"
)


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def inum(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as fp:
        return list(csv.DictReader(fp))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prev_value(times: list[float], values: list[Any], t: float, default: Any = "") -> tuple[float, Any]:
    idx = bisect_right(times, t) - 1
    if idx < 0:
        return math.nan, default
    return times[idx], values[idx]


def age_at(times: list[float], t: float) -> float:
    idx = bisect_right(times, t) - 1
    if idx < 0:
        return math.inf
    return t - times[idx]


def count_between(times: list[float], start: float, end: float) -> int:
    return max(0, bisect_right(times, end) - bisect_left(times, start))


def sum_between(times: list[float], values: list[float], start: float, end: float) -> float:
    left = bisect_left(times, start)
    right = bisect_right(times, end)
    return sum(values[left:right])


def max_recent_gap(times: list[float], t: float, window_ms: float) -> float:
    if not times:
        return math.inf
    start = t - window_ms
    left = max(0, bisect_left(times, start) - 1)
    right = bisect_right(times, t)
    if right - left <= 1:
        return age_at(times, t)
    gaps = [times[idx] - times[idx - 1] for idx in range(left + 1, right)]
    gaps.append(t - times[right - 1])
    return max(gaps)


def overlap_ms(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def find_original_runs(root: Path) -> list[Path]:
    runs: list[Path] = []
    for run_dir in sorted(root.glob("*/*")):
        sender = run_dir / "sender"
        receiver = run_dir / "receiver"
        if not sender.is_dir() or not receiver.is_dir():
            continue
        if not (sender / "COLLECT_INFO.txt").exists():
            continue
        if (sender / "REPLAY_INFO.txt").exists():
            continue
        if not (receiver / "receiver_rendered_frames.csv").exists():
            continue
        if not (receiver / "video_freeze.csv").exists():
            continue
        runs.append(run_dir)
    return runs


def load_freeze_rows(path: Path) -> list[dict[str, Any]]:
    freezes: list[dict[str, Any]] = []
    for row in read_rows(path):
        run = row.get("run", "")
        freeze_id = inum(row.get("freeze_id"), -1)
        if not run or freeze_id < 1:
            continue
        start = fnum(row.get("freeze_start_ms"), math.nan)
        end = fnum(row.get("freeze_end_ms"), math.nan)
        dur = fnum(row.get("freeze_duration_ms"), math.nan)
        if not math.isfinite(start) or not math.isfinite(end):
            end = fnum(row.get("freeze_end_ms") or row.get("timestamp_ms"), math.nan)
            dur = fnum(row.get("freeze_duration_ms"), math.nan)
            start = end - dur
        if math.isfinite(start) and math.isfinite(end) and end > start:
            freezes.append(
                {
                    "run": run,
                    "freeze_id": freeze_id,
                    "start_ms": start,
                    "end_ms": end,
                    "duration_ms": end - start,
                    "is_target": int(inum(row.get("is_broad_concealment_target"), 0) == 1),
                    "strict_audio_good_80": inum(row.get("strict_audio_good_80"), 0),
                    "cause_label": row.get("cause_label", ""),
                    "usable_ratio_B80": fnum(row.get("usable_ratio_B80"), 0.0),
                    "no_fresh_rendered_frame_during_freeze": inum(row.get("no_fresh_rendered_frame_during_freeze"), 0),
                    "max_video_recv_gap_ms": fnum(row.get("max_video_recv_gap_ms"), 0.0),
                    "max_frame_completion_gap_ms": fnum(row.get("max_frame_completion_gap_ms"), 0.0),
                }
            )
    return freezes


def read_freezes_from_video_freeze(path: Path, run_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(read_rows(path), start=1):
        end = fnum(row.get("timestamp_ms"), -1.0)
        dur = fnum(row.get("freeze_duration_ms"), -1.0)
        if end > 0 and dur > 0:
            rows.append(
                {
                    "run": run_key,
                    "freeze_id": idx,
                    "start_ms": end - dur,
                    "end_ms": end,
                    "duration_ms": dur,
                    "is_target": 0,
                    "strict_audio_good_80": 0,
                    "cause_label": "",
                }
            )
    return rows


def interval_lookup(freezes: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    for freeze in freezes:
        if freeze["start_ms"] <= t < freeze["end_ms"]:
            return freeze
    return None


def read_render_times(path: Path) -> tuple[list[float], list[dict[str, str]]]:
    rows = [r for r in read_rows(path) if fnum(r.get("timestamp_ms"), -1) > 0]
    rows.sort(key=lambda r: fnum(r.get("timestamp_ms"), 0))
    return [fnum(r.get("timestamp_ms"), 0) for r in rows], rows


def read_audio_times(receiver: Path) -> list[float]:
    path = receiver / "audio_packet_inserts.csv"
    if path.exists():
        return sorted(fnum(r.get("wall_time_ms"), -1) for r in read_rows(path) if fnum(r.get("wall_time_ms"), -1) > 0)
    return []


def read_video_packet_rows(receiver: Path) -> tuple[list[float], list[float]]:
    times: list[float] = []
    missing: list[float] = []
    for row in read_rows(receiver / "packet_buffer_inserts.csv"):
        t = fnum(row.get("timestamp_ms"), -1)
        if t <= 0:
            continue
        times.append(t)
        missing.append(max(0, fnum(row.get("missing_count"), 0)))
    pairs = sorted(zip(times, missing))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def read_frame_rows(receiver: Path) -> tuple[list[float], list[float]]:
    times: list[float] = []
    rtx: list[float] = []
    for row in read_rows(receiver / "frame_buffer.csv"):
        if inum(row.get("inserted"), 1) == 0:
            continue
        t = fnum(row.get("receive_time_ms") or row.get("timestamp_ms"), -1)
        if t <= 0:
            continue
        times.append(t)
        rtx.append(max(0, fnum(row.get("delayed_by_retransmission"), 0)))
    pairs = sorted(zip(times, rtx))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def read_budget_rows(receiver: Path) -> tuple[list[float], list[dict[str, str]]]:
    rows = [r for r in read_rows(receiver / "effective_playout_budget.csv") if fnum(r.get("timestamp_ms"), -1) > 0]
    rows.sort(key=lambda r: fnum(r.get("timestamp_ms"), 0))
    return [fnum(r.get("timestamp_ms"), 0) for r in rows], rows


def read_delay_bwe(receiver: Path) -> tuple[list[float], list[float], list[float]]:
    rows = [r for r in read_rows(receiver / "delay_bwe_state.csv") if fnum(r.get("at_time_ms"), -1) > 0]
    rows.sort(key=lambda r: fnum(r.get("at_time_ms"), 0))
    times = [fnum(r.get("at_time_ms"), 0) for r in rows]
    overuse = [1.0 if inum(r.get("detector_state"), 0) == 2 else 0.0 for r in rows]
    target = [fnum(r.get("target_bps"), 0) for r in rows]
    return times, overuse, target


def target_bitrate_drop(times: list[float], targets: list[float], t: float, window_ms: float) -> float:
    if not times:
        return 0.0
    left = bisect_left(times, t - window_ms)
    right = bisect_right(times, t)
    vals = [v for v in targets[left:right] if v > 0]
    if len(vals) < 2:
        return 0.0
    first = vals[0]
    low = min(vals)
    return max(0.0, (first - low) / first)


def copy_existing_baselines(input_root: Path, out_dir: Path) -> None:
    target_dir = input_root / "render_switch_policy_target_denominator_summary"
    render_dir = input_root / "render_switch_policy_eval_original_only"
    gated_dir = input_root / "gated_render_switch_policy_original_only_summary_v3"
    headroom_dir = input_root / "headroom_prefetch_policy_original_only_tight"

    target_den = read_rows(target_dir / "target_denominator_summary.csv")
    target_fp = read_rows(target_dir / "target_fp_summary.csv")
    write_csv(out_dir / "target_denominator_baseline_reproduced.csv", target_den)
    write_csv(out_dir / "target_fp_baseline_reproduced.csv", target_fp)

    rows: list[dict[str, Any]] = []
    for row in read_rows(render_dir / "policy_summary.csv"):
        if row.get("scenario") == "latency_31ms":
            rows.append(
                {
                    "scope": "all_original_freezes",
                    "model": "render_gap_only",
                    "generation_latency_ms": fnum(row.get("generation_latency_ms")),
                    "runs": inum(row.get("runs")),
                    "freeze_events": inum(row.get("freeze_events")),
                    "freeze_time_ms": fnum(row.get("freeze_time_ms")),
                    "concealed_freeze_time_ms": fnum(row.get("concealed_freeze_time_ms")),
                    "coverage": fnum(row.get("concealed_freeze_time_ratio")),
                    "residual_user_freeze_time_ms": fnum(row.get("residual_user_freeze_time_ms")),
                    "visible_generated_time_ms": fnum(row.get("visible_generated_time_ms")),
                    "visible_fp_time_ms": fnum(row.get("visible_switch_fp_time_ms")),
                }
            )
    for row in read_rows(gated_dir / "policy_summary.csv"):
        if row.get("scenario") == "latency_31ms":
            rows.append(
                {
                    "scope": "all_original_freezes",
                    "model": row.get("model"),
                    "generation_latency_ms": fnum(row.get("generation_latency_ms")),
                    "runs": inum(row.get("runs")),
                    "freeze_events": inum(row.get("freeze_events")),
                    "freeze_time_ms": fnum(row.get("freeze_time_ms")),
                    "concealed_freeze_time_ms": fnum(row.get("concealed_freeze_time_ms")),
                    "coverage": fnum(row.get("concealed_freeze_time_ratio")),
                    "residual_user_freeze_time_ms": fnum(row.get("residual_user_freeze_time_ms")),
                    "visible_generated_time_ms": fnum(row.get("visible_generated_time_ms")),
                    "visible_fp_time_ms": fnum(row.get("visible_switch_fp_time_ms")),
                }
            )
    for row in read_rows(headroom_dir / "summary.csv"):
        if row.get("target") == "ratio80" and fnum(row.get("headroom_threshold_ms"), -1) == 0:
            rows.append(
                {
                    "scope": "headroom_subset_targets",
                    "model": "headroom_prefetch_h0",
                    "generation_latency_ms": fnum(row.get("generation_latency_ms")),
                    "runs": inum(row.get("runs_with_headroom")),
                    "freeze_events": inum(row.get("target_freezes_in_scope")),
                    "freeze_time_ms": fnum(row.get("target_duration_ms")),
                    "concealed_freeze_time_ms": fnum(row.get("target_concealed_ms")),
                    "coverage": fnum(row.get("target_concealed_ratio")),
                    "residual_user_freeze_time_ms": fnum(row.get("target_duration_ms")) - fnum(row.get("target_concealed_ms")),
                    "visible_generated_time_ms": fnum(row.get("visible_generated_time_ms")),
                    "visible_fp_time_ms": fnum(row.get("target_fp_time_ms")),
                    "target_precision": fnum(row.get("target_precision_time")),
                }
            )
    write_csv(out_dir / "evaluation_baseline_reproduced.csv", rows)

    (out_dir / "baseline_tradeoff.md").write_text(
        "\n".join(
            [
                "## Existing Baseline Trade-off",
                "",
                "- `render_gap_only` covers many target freezes but keeps generated video visible for a long time outside target freezes, so precision is low.",
                "- `start_gate_current_return` reduces visible false positives but still has substantial non-target generated time.",
                "- `continuous_gate_to_next_render` is the precision-oriented rule baseline: it sharply reduces false positives, but misses many target freezes.",
                "- Headroom prefetch improves coverage on the subset with `effective_playout_budget.csv`, but if used alone it is too broad and reduces precision.",
                "- The next question is therefore a display-switch problem: can a score improve the coverage/precision Pareto frontier without turning prefetch into visible output too early?",
                "",
            ]
        ),
        encoding="utf-8",
    )

    for name in ["target_denominator_summary.csv", "target_fp_summary.csv"]:
        src = target_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / f"source_{name}")


def build_samples(args: argparse.Namespace) -> None:
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    copy_existing_baselines(args.input_root, out_dir)

    freeze_rows = load_freeze_rows(
        args.input_root / "concealment_opportunity_evidence_original_only" / "cause_labels_per_freeze.csv"
    )
    by_run: dict[str, list[dict[str, Any]]] = {}
    for row in freeze_rows:
        by_run.setdefault(row["run"], []).append(row)

    runs = find_original_runs(args.runs_root)
    samples: list[dict[str, Any]] = []
    label_counter = Counter()
    run_span_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []

    for run_dir in runs:
        run_key = str(run_dir.relative_to(args.runs_root))
        receiver = run_dir / "receiver"
        render_times, render_rows = read_render_times(receiver / "receiver_rendered_frames.csv")
        if len(render_times) < 2:
            continue
        freezes = by_run.get(run_key)
        if not freezes:
            freezes = read_freezes_from_video_freeze(receiver / "video_freeze.csv", run_key)
        freezes = sorted(freezes, key=lambda r: r["start_ms"])
        for freeze in freezes:
            if freeze.get("is_target", 0):
                target_rows.append(
                    {
                        "run": run_key,
                        "freeze_id": freeze["freeze_id"],
                        "start_ms": freeze["start_ms"],
                        "end_ms": freeze["end_ms"],
                        "duration_ms": freeze["duration_ms"],
                    }
                )

        audio_times = read_audio_times(receiver)
        video_times, missing_vals = read_video_packet_rows(receiver)
        frame_times, rtx_vals = read_frame_rows(receiver)
        budget_times, budget_rows = read_budget_rows(receiver)
        bwe_times, overuse_vals, target_bps_vals = read_delay_bwe(receiver)
        observed_start = render_times[0]
        observed_end = render_times[-1]
        observed_span = observed_end - observed_start
        run_span_rows.append(
            {
                "run": run_key,
                "observed_start_ms": observed_start,
                "observed_end_ms": observed_end,
                "observed_span_ms": observed_span,
                "rendered_frames": len(render_times),
                "freeze_events": len(freezes),
                "target_freezes": sum(1 for x in freezes if x.get("is_target", 0)),
            }
        )

        estimated_interval = 40.0
        stable_next_sample_ms = observed_start
        for idx in range(1, len(render_times)):
            prev_t = render_times[idx - 1]
            next_t = render_times[idx]
            gap = next_t - prev_t
            if gap <= 0:
                continue

            # Stable non-freeze negatives, at low rate, keep "fresh real frame
            # available" examples in the dataset without dominating it.
            if prev_t >= stable_next_sample_ms and not interval_lookup(freezes, prev_t):
                samples.append(
                    {
                        "run_id": run_key,
                        "timestamp_ms": round(prev_t, 3),
                        "sample_type": "stable_real_render",
                        "is_inside_any_freeze": 0,
                        "is_inside_target_freeze": 0,
                        "freeze_id": "",
                        "generated_frame_ready": 0,
                        "generated_frame_age_ms": "",
                        "time_since_last_real_rendered_frame_ms": 0.0,
                        "no_fresh_real_frame": 0,
                        "fresh_real_frame_available": 1,
                        "render_gap_ms": 0.0,
                        "expected_render_interval_ms": round(estimated_interval, 3),
                        "switch_deadline_missed": 0,
                        "audio_age_ms": round(age_at(audio_times, prev_t), 3) if audio_times else "",
                        "audio_condition_B80": int(age_at(audio_times, prev_t) <= args.audio_budget_ms) if audio_times else 0,
                        "time_since_last_video_packet_ms": round(age_at(video_times, prev_t), 3) if video_times else "",
                        "time_since_last_playable_frame_ms": round(age_at(frame_times, prev_t), 3) if frame_times else "",
                        "playout_headroom_ms": "",
                        "headroom_sample_age_ms": "",
                        "future_render_valid_units": "",
                        "frame_completion_gap_ms": "",
                        "max_recent_video_recv_gap_ms": round(max_recent_gap(video_times, prev_t, args.recent_window_ms), 3) if video_times else "",
                        "max_recent_frame_completion_gap_ms": round(max_recent_gap(frame_times, prev_t, args.recent_window_ms), 3) if frame_times else "",
                        "recent_nack_count": "",
                        "recent_rtx_count": round(sum_between(frame_times, rtx_vals, prev_t - args.recent_window_ms, prev_t), 3) if frame_times else 0,
                        "recent_missing_seq_count": round(sum_between(video_times, missing_vals, prev_t - args.recent_window_ms, prev_t), 3) if video_times else 0,
                        "rtt_ms": "",
                        "loss_rate": "",
                        "gcc_overuse_count": round(sum_between(bwe_times, overuse_vals, prev_t - args.recent_window_ms, prev_t), 3) if bwe_times else 0,
                        "target_bitrate_drop": round(target_bitrate_drop(bwe_times, target_bps_vals, prev_t, args.recent_window_ms), 6) if bwe_times else 0,
                        "next_real_render_ms": round(next_t, 3),
                        "decision_label": 0,
                    }
                )
                stable_next_sample_ms = prev_t + args.stable_negative_interval_ms

            switch_deadline = max(estimated_interval + args.render_deadline_slack_ms, args.switch_min_gap_ms)
            start_sample = prev_t + args.sample_start_gap_ms
            generation_time = prev_t + args.generation_trigger_gap_ms
            ready_time = generation_time + args.generation_latency_ms
            t = start_sample
            while t < next_t:
                freeze = interval_lookup(freezes, t)
                is_any = int(freeze is not None)
                is_target = int(bool(freeze and freeze.get("is_target", 0)))
                audio_age = age_at(audio_times, t) if audio_times else math.inf
                audio_ok = int(audio_age <= args.audio_budget_ms)
                generated_ready = int(t >= ready_time)
                budget_t, budget = prev_value(budget_times, budget_rows, t, {})
                if budget:
                    headroom = fnum(budget.get("effective_budget_ms"), math.inf)
                    headroom_age = t - budget_t
                    future_units = inum(budget.get("future_render_valid_units"), 0)
                else:
                    headroom = math.inf
                    headroom_age = math.inf
                    future_units = -1
                y = int(is_target and audio_ok and generated_ready)
                label_counter[y] += 1
                samples.append(
                    {
                        "run_id": run_key,
                        "timestamp_ms": round(t, 3),
                        "sample_type": "render_gap_candidate",
                        "is_inside_any_freeze": is_any,
                        "is_inside_target_freeze": is_target,
                        "freeze_id": freeze["freeze_id"] if freeze else "",
                        "generated_frame_ready": generated_ready,
                        "generated_frame_age_ms": round(t - ready_time, 3) if generated_ready else "",
                        "time_since_last_real_rendered_frame_ms": round(t - prev_t, 3),
                        "no_fresh_real_frame": 1,
                        "fresh_real_frame_available": 0,
                        "render_gap_ms": round(t - prev_t, 3),
                        "expected_render_interval_ms": round(estimated_interval, 3),
                        "switch_deadline_missed": int(t - prev_t >= switch_deadline),
                        "audio_age_ms": round(audio_age, 3) if math.isfinite(audio_age) else "",
                        "audio_condition_B80": audio_ok,
                        "time_since_last_video_packet_ms": round(age_at(video_times, t), 3) if video_times else "",
                        "time_since_last_playable_frame_ms": round(age_at(frame_times, t), 3) if frame_times else "",
                        "playout_headroom_ms": round(headroom, 3) if math.isfinite(headroom) else "",
                        "headroom_sample_age_ms": round(headroom_age, 3) if math.isfinite(headroom_age) else "",
                        "future_render_valid_units": future_units if future_units >= 0 else "",
                        "frame_completion_gap_ms": round(age_at(frame_times, t), 3) if frame_times else "",
                        "max_recent_video_recv_gap_ms": round(max_recent_gap(video_times, t, args.recent_window_ms), 3) if video_times else "",
                        "max_recent_frame_completion_gap_ms": round(max_recent_gap(frame_times, t, args.recent_window_ms), 3) if frame_times else "",
                        "recent_nack_count": "",
                        "recent_rtx_count": round(sum_between(frame_times, rtx_vals, t - args.recent_window_ms, t), 3) if frame_times else 0,
                        "recent_missing_seq_count": round(sum_between(video_times, missing_vals, t - args.recent_window_ms, t), 3) if video_times else 0,
                        "rtt_ms": "",
                        "loss_rate": "",
                        "gcc_overuse_count": round(sum_between(bwe_times, overuse_vals, t - args.recent_window_ms, t), 3) if bwe_times else 0,
                        "target_bitrate_drop": round(target_bitrate_drop(bwe_times, target_bps_vals, t, args.recent_window_ms), 6) if bwe_times else 0,
                        "next_real_render_ms": round(next_t, 3),
                        "decision_label": y,
                    }
                )
                t += args.sample_interval_ms

            if 0 < gap < 1000:
                estimated_interval = min(200.0, max(20.0, (estimated_interval * 7.0 + gap) / 8.0))

    write_csv(out_dir / "switch_decision_samples.csv", samples)
    write_csv(out_dir / "run_observed_spans.csv", run_span_rows)
    write_csv(out_dir / "target_freeze_intervals.csv", target_rows)

    # Recompute from all rows so low-rate stable negatives are included.
    pos = sum(1 for row in samples if inum(row.get("decision_label"), 0) == 1)
    neg = sum(1 for row in samples if inum(row.get("decision_label"), 0) == 0)
    label_summary = [
        {"metric": "samples", "value": len(samples)},
        {"metric": "positive_samples", "value": pos},
        {"metric": "negative_samples", "value": neg},
        {"metric": "positive_ratio", "value": pos / (pos + neg) if pos + neg else 0},
        {"metric": "runs", "value": len(run_span_rows)},
        {"metric": "target_freezes", "value": len(target_rows)},
        {"metric": "target_duration_ms", "value": sum(fnum(r["duration_ms"]) for r in target_rows)},
        {"metric": "observed_span_ms", "value": sum(fnum(r["observed_span_ms"]) for r in run_span_rows)},
    ]
    write_csv(out_dir / "switch_decision_label_summary.csv", label_summary)

    (out_dir / "switch_decision_schema.md").write_text(
        """# Switch Decision Sample Schema

Unit: receiver-side display decision sample.

Sampling:
- Candidate samples are emitted every 20 ms inside render gaps once `time_since_last_real_rendered_frame_ms >= 40`.
- Low-rate stable render samples are also emitted as negatives, so the dataset contains examples where a fresh real frame is available.
- Original collection logs only are used. Replay runs are excluded by `COLLECT_INFO.txt` / `REPLAY_INFO.txt` checks.

Label:
- `decision_label = 1` iff the sample is inside a broad target freeze, generated video is ready under 31 ms assumed latency, and `audio_condition_B80` holds.
- `decision_label = 0` outside target freezes, inside non-target freezes, when audio budget B=80 ms fails, or when fresh real video should be shown.

Important:
- This is a display-switch label, not a root-cause/network-delay label.
- Root-cause labels are intentionally not used as the supervised target.
- Missing features are left blank where a run does not contain that log.
""",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--sample-interval-ms", type=float, default=20.0)
    parser.add_argument("--sample-start-gap-ms", type=float, default=40.0)
    parser.add_argument("--generation-trigger-gap-ms", type=float, default=40.0)
    parser.add_argument("--generation-latency-ms", type=float, default=31.0)
    parser.add_argument("--audio-budget-ms", type=float, default=80.0)
    parser.add_argument("--render-deadline-slack-ms", type=float, default=40.0)
    parser.add_argument("--switch-min-gap-ms", type=float, default=80.0)
    parser.add_argument("--recent-window-ms", type=float, default=500.0)
    parser.add_argument("--stable-negative-interval-ms", type=float, default=1000.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_samples(args)
    print(f"wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
