#!/usr/bin/env python3
"""Evaluate a temporary render-gap 40/80ms immediate-return controller.

This intentionally does not use playout headroom, packet starvation, frame
starvation, or learned SwitchScore. It is a simple presentation/test controller:
generate at render_gap >= 40 ms, display at render_gap >= 80 ms when ready,
return immediately at the first real rendered frame.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import statistics
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_RUNS_ROOT = Path("/home/widen/Sync/webrtc-trace-runs")
DEFAULT_ANALYSIS_ROOT = Path("/home/widen/webrtc-checkout/analysis_runs")
DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/render_gap_40_80_immediate_eval_original_only"
)
DEFAULT_LABEL_FILE = (
    DEFAULT_ANALYSIS_ROOT
    / "concealment_opportunity_evidence_original_only"
    / "cause_labels_per_freeze.csv"
)

MAIN_LATENCY_MS = 31.0
LATENCY_SWEEP_MS = [0.0, 16.0, 31.0, 50.0, 80.0]
TRIGGER_SWEEP_MS = [20.0, 40.0, 60.0]
SWITCH_SWEEP_MS = [60.0, 80.0, 100.0]
MONITOR_INTERVAL_MS = 20.0


def fnum(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def inum(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
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


def read_times(path: Path, columns: list[str]) -> list[float]:
    rows = read_rows(path)
    times: list[float] = []
    for row in rows:
        for col in columns:
            if col in row:
                t = fnum(row.get(col), -1)
                if t > 0:
                    times.append(t)
                break
    return sorted(times)


def q(values: list[float], percentile: float) -> float:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, max(0, round((percentile / 100.0) * (len(vals) - 1))))
    return vals[idx]


def overlap_ms(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    clean = sorted((a, b) for a, b in intervals if b > a)
    if not clean:
        return []
    merged = [clean[0]]
    for start, end in clean[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def interval_total(intervals: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in merge_intervals(intervals))


def overlap_total(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    aa = merge_intervals(a)
    bb = merge_intervals(b)
    i = j = 0
    total = 0.0
    while i < len(aa) and j < len(bb):
        total += overlap_ms(aa[i][0], aa[i][1], bb[j][0], bb[j][1])
        if aa[i][1] <= bb[j][1]:
            i += 1
        else:
            j += 1
    return total


def ceil_to_monitor(t: float, anchor: float, step_ms: float) -> float:
    if step_ms <= 0:
        return t
    return anchor + math.ceil((t - anchor - 1e-9) / step_ms) * step_ms


def load_freeze_labels(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = ["run", "freeze_id", "freeze_start_ms", "freeze_end_ms", "freeze_duration_ms"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"missing columns in {path}: {missing}")
    df = df.copy()
    df["run"] = df["run"].astype(str)
    df["freeze_id"] = pd.to_numeric(df["freeze_id"], errors="coerce").fillna(-1).astype(int)
    df["freeze_start_ms"] = pd.to_numeric(df["freeze_start_ms"], errors="coerce")
    df["freeze_end_ms"] = pd.to_numeric(df["freeze_end_ms"], errors="coerce")
    df["freeze_duration_ms"] = pd.to_numeric(df["freeze_duration_ms"], errors="coerce")
    if "is_broad_concealment_target" not in df.columns:
        df["is_broad_concealment_target"] = 0
    df["is_target"] = pd.to_numeric(df["is_broad_concealment_target"], errors="coerce").fillna(0).astype(int)
    df["freeze_key"] = df["run"].astype(str) + "#" + df["freeze_id"].astype(str)
    return df


def observed_spans(runs_root: Path, runs: list[str]) -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    rows: list[dict[str, Any]] = []
    render_by_run: dict[str, list[float]] = {}
    for run in runs:
        receiver = runs_root / run / "receiver"
        render_times = read_times(receiver / "receiver_rendered_frames.csv", ["timestamp_ms"])
        render_by_run[run] = render_times
        if len(render_times) >= 2:
            rows.append(
                {
                    "run": run,
                    "included": 1,
                    "rendered_frames": len(render_times),
                    "observed_start_ms": render_times[0],
                    "observed_end_ms": render_times[-1],
                    "observed_span_ms": render_times[-1] - render_times[0],
                    "note": "",
                }
            )
        else:
            rows.append(
                {
                    "run": run,
                    "included": 0,
                    "rendered_frames": len(render_times),
                    "observed_start_ms": "",
                    "observed_end_ms": "",
                    "observed_span_ms": 0.0,
                    "note": "missing or too few receiver rendered frame timestamps",
                }
            )
    return rows, render_by_run


def simulate_run(
    run: str,
    render_times: list[float],
    trigger_ms: float,
    switch_ms: float,
    latency_ms: float,
    monitor_interval_ms: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    if len(render_times) < 2:
        return events, segments
    anchor = render_times[0]
    for idx in range(1, len(render_times)):
        gap_start = render_times[idx - 1]
        gap_end = render_times[idx]
        gap_ms = gap_end - gap_start
        if gap_ms <= 0:
            continue
        trigger_due = gap_start + trigger_ms
        trigger_time = ceil_to_monitor(trigger_due, anchor, monitor_interval_ms)
        triggered = trigger_time < gap_end
        ready_time = trigger_time + latency_ms if triggered else math.nan
        switch_due = gap_start + switch_ms
        switch_threshold_time = ceil_to_monitor(switch_due, anchor, monitor_interval_ms)
        switch_time = max(switch_threshold_time, ready_time) if triggered else math.nan
        switched = bool(triggered and switch_time < gap_end)
        reason = ""
        if not triggered:
            reason = "real_frame_returned_before_generation_trigger"
        elif gap_end <= switch_threshold_time:
            reason = "real_frame_returned_before_80ms_switch_threshold"
        elif ready_time >= gap_end:
            reason = "generated_not_ready_before_real_frame_return"
        elif switched:
            reason = "visible_generated_until_immediate_real_return"
        else:
            reason = "unknown_no_switch"
        event = {
            "run": run,
            "gap_index": idx,
            "gap_start_ms": round(gap_start, 3),
            "gap_end_ms": round(gap_end, 3),
            "gap_ms": round(gap_ms, 3),
            "trigger_ms": trigger_ms,
            "switch_ms": switch_ms,
            "generation_latency_ms": latency_ms,
            "monitor_interval_ms": monitor_interval_ms,
            "generation_triggered": int(triggered),
            "generation_trigger_time_ms": round(trigger_time, 3) if triggered else "",
            "generated_ready_time_ms": round(ready_time, 3) if triggered else "",
            "switch_threshold_time_ms": round(switch_threshold_time, 3),
            "display_switched": int(switched),
            "display_switch_time_ms": round(switch_time, 3) if switched else "",
            "return_time_ms": round(gap_end, 3) if switched else "",
            "visible_duration_ms": round(gap_end - switch_time, 3) if switched else 0.0,
            "ready_before_switch_threshold": int(triggered and ready_time <= switch_threshold_time),
            "event_reason": reason,
        }
        events.append(event)
        if switched:
            segments.append(
                {
                    "run": run,
                    "segment_id": f"{run}#g{idx}",
                    "gap_index": idx,
                    "start_ms": round(switch_time, 3),
                    "end_ms": round(gap_end, 3),
                    "duration_ms": round(gap_end - switch_time, 3),
                    "generation_trigger_time_ms": round(trigger_time, 3),
                    "generated_ready_time_ms": round(ready_time, 3),
                    "switch_threshold_time_ms": round(switch_threshold_time, 3),
                    "return_time_ms": round(gap_end, 3),
                    "generation_latency_ms": latency_ms,
                    "trigger_ms": trigger_ms,
                    "switch_ms": switch_ms,
                }
            )
    return events, segments


def assign_freeze_metrics(
    freezes: pd.DataFrame,
    events: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    latency_ms: float,
    trigger_ms: float,
    switch_ms: float,
) -> list[dict[str, Any]]:
    events_by_run: dict[str, list[dict[str, Any]]] = {}
    segments_by_run: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_run.setdefault(str(event["run"]), []).append(event)
    for seg in segments:
        segments_by_run.setdefault(str(seg["run"]), []).append(seg)

    rows: list[dict[str, Any]] = []
    for row in freezes.itertuples(index=False):
        run = str(row.run)
        start = float(row.freeze_start_ms)
        end = float(row.freeze_end_ms)
        duration = float(row.freeze_duration_ms)
        run_segments = segments_by_run.get(run, [])
        overlaps = [
            overlap_ms(float(seg["start_ms"]), float(seg["end_ms"]), start, end)
            for seg in run_segments
        ]
        concealed = sum(overlaps)
        first_visible = math.nan
        for seg, ov in zip(run_segments, overlaps):
            if ov > 0:
                first_visible = max(float(seg["start_ms"]), start)
                break
        related = [
            ev
            for ev in events_by_run.get(run, [])
            if overlap_ms(float(ev["gap_start_ms"]), float(ev["gap_end_ms"]), start, end) > 0
            or (float(ev["gap_start_ms"]) <= start <= float(ev["gap_end_ms"]))
        ]
        if related:
            related.sort(
                key=lambda ev: overlap_ms(float(ev["gap_start_ms"]), float(ev["gap_end_ms"]), start, end),
                reverse=True,
            )
            ev = related[0]
        else:
            ev = None
        trigger_time = fnum(ev.get("generation_trigger_time_ms"), math.nan) if ev else math.nan
        ready_time = fnum(ev.get("generated_ready_time_ms"), math.nan) if ev else math.nan
        switch_time = fnum(ev.get("display_switch_time_ms"), math.nan) if ev else math.nan
        switch_threshold_time = fnum(ev.get("switch_threshold_time_ms"), math.nan) if ev else math.nan
        triggered = int(ev.get("generation_triggered", 0)) if ev else 0
        switched = int(ev.get("display_switched", 0)) if ev else 0

        miss_reason = ""
        if concealed > 0:
            miss_reason = "concealed"
        elif duration < switch_ms:
            miss_reason = "freeze_shorter_than_display_switch_threshold"
        elif not ev:
            miss_reason = "missing_render_gap_event"
        elif not triggered:
            miss_reason = "real_frame_returned_before_generation_trigger"
        elif not switched and fnum(ev.get("gap_end_ms")) <= switch_threshold_time:
            miss_reason = "real_frame_returned_before_80ms_switch_threshold"
        elif not switched and ready_time >= fnum(ev.get("gap_end_ms")):
            miss_reason = "generated_not_ready_before_real_frame_return"
        elif not switched:
            miss_reason = ev.get("event_reason", "unknown_no_switch")
        else:
            miss_reason = "unknown"

        rows.append(
            {
                "run": run,
                "freeze_id": int(row.freeze_id),
                "freeze_key": row.freeze_key,
                "freeze_start_ms": round(start, 3),
                "freeze_end_ms": round(end, 3),
                "freeze_duration_ms": round(duration, 3),
                "is_target": int(row.is_target),
                "usable_ratio_B80": round(fnum(getattr(row, "usable_ratio_B80", 0.0)), 6),
                "cause_label": getattr(row, "cause_label", ""),
                "generation_latency_ms": latency_ms,
                "trigger_ms": trigger_ms,
                "switch_ms": switch_ms,
                "related_gap_start_ms": ev.get("gap_start_ms", "") if ev else "",
                "related_gap_end_ms": ev.get("gap_end_ms", "") if ev else "",
                "related_render_gap_ms": ev.get("gap_ms", "") if ev else "",
                "generation_triggered": triggered,
                "generation_trigger_time_ms": "" if math.isnan(trigger_time) else round(trigger_time, 3),
                "generated_ready_time_ms": "" if math.isnan(ready_time) else round(ready_time, 3),
                "switch_threshold_time_ms": "" if math.isnan(switch_threshold_time) else round(switch_threshold_time, 3),
                "display_switched": switched,
                "display_switch_time_ms": "" if math.isnan(switch_time) else round(switch_time, 3),
                "first_generated_visible_ms": "" if math.isnan(first_visible) else round(first_visible, 3),
                "return_time_ms": ev.get("return_time_ms", "") if ev else "",
                "concealed_freeze_ms": round(concealed, 3),
                "concealed_ratio": round(concealed / duration, 6) if duration > 0 else 0.0,
                "residual_freeze_ms": round(max(0.0, duration - concealed), 3),
                "trigger_delay_from_freeze_start_ms": "" if math.isnan(trigger_time) else round(trigger_time - start, 3),
                "switch_delay_from_freeze_start_ms": "" if math.isnan(switch_time) else round(switch_time - start, 3),
                "first_generated_visible_delay_ms": "" if math.isnan(first_visible) else round(first_visible - start, 3),
                "triggered_before_freeze_start": int(triggered and trigger_time < start),
                "triggered_after_freeze_start": int(triggered and trigger_time >= start),
                "generated_ready_before_switch_threshold": int(triggered and ready_time <= switch_threshold_time),
                "miss_reason": miss_reason,
            }
        )
    return rows


def summarize_policy(
    freezes: pd.DataFrame,
    per_freeze: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    observed_span_ms: float,
    target_only: bool,
) -> dict[str, Any]:
    seg_intervals = [(float(s["start_ms"]), float(s["end_ms"])) for s in segments]
    visible_ms = interval_total(seg_intervals)
    rows = [r for r in per_freeze if (not target_only or int(r["is_target"]) == 1)]
    freeze_intervals = [(float(r["freeze_start_ms"]), float(r["freeze_end_ms"])) for r in rows]
    freeze_duration = sum(float(r["freeze_duration_ms"]) for r in rows)
    concealed = sum(float(r["concealed_freeze_ms"]) for r in rows)
    fp = max(0.0, visible_ms - concealed)
    durations = [float(s["duration_ms"]) for s in segments]
    any_events = sum(1 for r in rows if float(r["concealed_freeze_ms"]) > 0)
    fully_events = sum(
        1
        for r in rows
        if float(r["concealed_freeze_ms"]) >= float(r["freeze_duration_ms"]) - 1e-6
    )
    oscillation_count = sum(1 for d in durations if d < 100.0)
    out = {
        "scope": "target_freezes" if target_only else "all_freezes",
        "freeze_events": len(rows),
        "freeze_duration_ms": round(freeze_duration, 3),
        "concealed_freeze_time_ms": round(concealed, 3),
        "concealed_freeze_coverage": round(concealed / freeze_duration, 6) if freeze_duration else 0.0,
        "residual_freeze_time_ms": round(max(0.0, freeze_duration - concealed), 3),
        "residual_freeze_ratio": round(max(0.0, freeze_duration - concealed) / freeze_duration, 6) if freeze_duration else 0.0,
        "generated_visible_time_ms": round(visible_ms, 3),
        "visible_fp_time_ms": round(fp, 3),
        "visible_fp_seconds_per_hour": round((fp / 1000.0) / (observed_span_ms / 3600000.0), 6)
        if observed_span_ms
        else 0.0,
        "generated_visible_segments": len(segments),
        "average_generated_segment_duration_ms": round(statistics.mean(durations), 3) if durations else 0.0,
        "median_generated_segment_duration_ms": round(statistics.median(durations), 3) if durations else 0.0,
        "p90_generated_segment_duration_ms": round(q(durations, 90), 3) if durations else 0.0,
        "return_count": len(segments),
        "oscillation_count": oscillation_count,
        "any_concealment_events": any_events,
        "any_concealment_event_ratio": round(any_events / len(rows), 6) if rows else 0.0,
        "fully_concealed_events": fully_events,
        "fully_concealed_event_ratio": round(fully_events / len(rows), 6) if rows else 0.0,
        "target_precision": round(concealed / visible_ms, 6) if visible_ms else 0.0,
        "target_fp_time_ms": round(fp, 3),
    }
    return out


def summarize_timing(per_freeze: list[dict[str, Any]], target_only: bool = True) -> dict[str, Any]:
    rows = [r for r in per_freeze if (not target_only or int(r["is_target"]) == 1)]
    triggered = [r for r in rows if int(r["generation_triggered"]) == 1]
    missed = [r for r in rows if float(r["concealed_freeze_ms"]) <= 0]
    reason_counts: dict[str, int] = {}
    for row in missed:
        reason_counts[row["miss_reason"]] = reason_counts.get(row["miss_reason"], 0) + 1
    out = {
        "scope": "target_freezes" if target_only else "all_freezes",
        "freezes": len(rows),
        "triggered_freezes": len(triggered),
        "triggered_before_freeze_start_events": sum(int(r["triggered_before_freeze_start"]) for r in rows),
        "triggered_before_freeze_start_ratio": round(sum(int(r["triggered_before_freeze_start"]) for r in rows) / len(rows), 6) if rows else 0.0,
        "triggered_after_freeze_start_events": sum(int(r["triggered_after_freeze_start"]) for r in rows),
        "triggered_after_freeze_start_ratio": round(sum(int(r["triggered_after_freeze_start"]) for r in rows) / len(rows), 6) if rows else 0.0,
        "ready_before_switch_threshold_events": sum(int(r["generated_ready_before_switch_threshold"]) for r in rows),
        "ready_before_switch_threshold_ratio": round(sum(int(r["generated_ready_before_switch_threshold"]) for r in rows) / len(rows), 6) if rows else 0.0,
        "missed_freezes": len(missed),
    }
    for reason, count in sorted(reason_counts.items()):
        out[f"miss_reason_{reason}"] = count
        out[f"miss_reason_{reason}_ratio"] = round(count / len(rows), 6) if rows else 0.0
    for col in [
        "trigger_delay_from_freeze_start_ms",
        "switch_delay_from_freeze_start_ms",
        "first_generated_visible_delay_ms",
    ]:
        vals = [fnum(r.get(col), math.nan) for r in rows if r.get(col, "") != ""]
        out[f"{col}_p50"] = round(q(vals, 50), 3) if vals else ""
        out[f"{col}_p90"] = round(q(vals, 90), 3) if vals else ""
    return out


def copy_existing_baselines(analysis_root: Path, out_dir: Path) -> None:
    src_dir = analysis_root / "render_switch_policy_target_denominator_summary"
    for name in ["target_denominator_summary.csv", "target_fp_summary.csv"]:
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, out_dir / f"source_{name}")


def make_oracles(freezes: pd.DataFrame, latency_ms: float) -> list[dict[str, Any]]:
    targets = freezes[freezes["is_target"] == 1]
    target_duration = float(targets["freeze_duration_ms"].sum())
    reactive = float(np.maximum(targets["freeze_duration_ms"].to_numpy(dtype=float) - latency_ms, 0.0).sum())
    return [
        {
            "oracle": f"reactive_oracle_latency{int(latency_ms)}",
            "target_events": len(targets),
            "target_duration_ms": round(target_duration, 3),
            "target_concealed_ms": round(reactive, 3),
            "target_coverage": round(reactive / target_duration, 6) if target_duration else 0.0,
            "target_precision": 1.0,
            "target_fp_seconds_per_hour": 0.0,
        },
        {
            "oracle": "perfect_prefetch_oracle",
            "target_events": len(targets),
            "target_duration_ms": round(target_duration, 3),
            "target_concealed_ms": round(target_duration, 3),
            "target_coverage": 1.0,
            "target_precision": 1.0,
            "target_fp_seconds_per_hour": 0.0,
        },
    ]


def final_comparison(out_dir: Path, temp_target: dict[str, Any], oracles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "method": "hold-last-frame",
            "target_coverage": 0.0,
            "target_precision": "",
            "target_fp_seconds_per_hour": 0.0,
            "target_concealed_ms": 0.0,
            "target_fp_ms": 0.0,
        }
    ]
    den_path = out_dir / "source_target_denominator_summary.csv"
    fp_path = out_dir / "source_target_fp_summary.csv"
    if den_path.exists() and fp_path.exists():
        den = pd.read_csv(den_path)
        fp = pd.read_csv(fp_path)
        label_map = {
            "render_gap_only_latency31": "render-gap only",
            "start_gate_current_return_latency31": "start gate + stable return",
            "continuous_gate_to_next_render_latency31": "continuous gate + next-render return",
        }
        for model, label in label_map.items():
            d = den[(den["target"] == "target_B80_usable_ratio_ge_0.8") & (den["model"] == model)]
            f = fp[(fp["target"] == "target_B80_usable_ratio_ge_0.8") & (fp["model"] == model)]
            if d.empty or f.empty:
                continue
            d0 = d.iloc[0]
            f0 = f.iloc[0]
            rows.append(
                {
                    "method": label,
                    "target_coverage": float(d0["concealed_time_ratio"]),
                    "target_precision": float(f0["target_precision_time"]),
                    "target_fp_seconds_per_hour": float(f0["target_fp_ms_per_min_observed"]) * 60.0 / 1000.0,
                    "target_concealed_ms": float(d0["concealed_ms"]),
                    "target_fp_ms": float(f0["target_fp_time_ms"]),
                    "scope_note": "existing baseline summary",
                }
            )
    rows.append(
        {
            "method": "render_gap_40_80_immediate",
            "target_coverage": temp_target["concealed_freeze_coverage"],
            "target_precision": temp_target["target_precision"],
            "target_fp_seconds_per_hour": temp_target["visible_fp_seconds_per_hour"],
            "target_concealed_ms": temp_target["concealed_freeze_time_ms"],
            "target_fp_ms": temp_target["target_fp_time_ms"],
            "scope_note": "current original target-label runs",
        }
    )
    for row in oracles:
        rows.append(
            {
                "method": row["oracle"],
                "target_coverage": row["target_coverage"],
                "target_precision": row["target_precision"],
                "target_fp_seconds_per_hour": row["target_fp_seconds_per_hour"],
                "target_concealed_ms": row["target_concealed_ms"],
                "target_fp_ms": 0.0,
                "scope_note": "target oracle",
            }
        )
    return rows


def run_policy(
    freezes: pd.DataFrame,
    render_by_run: dict[str, list[float]],
    trigger_ms: float,
    switch_ms: float,
    latency_ms: float,
    monitor_interval_ms: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    all_events: list[dict[str, Any]] = []
    all_segments: list[dict[str, Any]] = []
    for run in sorted(freezes["run"].unique()):
        events, segments = simulate_run(
            run,
            render_by_run.get(run, []),
            trigger_ms=trigger_ms,
            switch_ms=switch_ms,
            latency_ms=latency_ms,
            monitor_interval_ms=monitor_interval_ms,
        )
        all_events.extend(events)
        all_segments.extend(segments)
    per_freeze = assign_freeze_metrics(
        freezes, all_events, all_segments, latency_ms=latency_ms, trigger_ms=trigger_ms, switch_ms=switch_ms
    )
    return all_events, all_segments, per_freeze


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--analysis-root", type=Path, default=DEFAULT_ANALYSIS_ROOT)
    parser.add_argument("--label-file", type=Path, default=DEFAULT_LABEL_FILE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--trigger-ms", type=float, default=40.0)
    parser.add_argument("--switch-ms", type=float, default=80.0)
    parser.add_argument("--generation-latency-ms", type=float, default=MAIN_LATENCY_MS)
    parser.add_argument("--monitor-interval-ms", type=float, default=MONITOR_INTERVAL_MS)
    args = parser.parse_args()

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    copy_existing_baselines(args.analysis_root, out)

    freezes = load_freeze_labels(args.label_file)
    runs = sorted(freezes["run"].unique())
    run_rows, render_by_run = observed_spans(args.runs_root, runs)
    write_csv(out / "render_gap_40_80_run_inclusion.csv", run_rows)
    included_runs = [r["run"] for r in run_rows if int(r["included"]) == 1]
    freezes_in_scope = freezes[freezes["run"].isin(included_runs)].copy()
    observed_span_ms = sum(fnum(r["observed_span_ms"]) for r in run_rows if int(r["included"]) == 1)

    events, segments, per_freeze = run_policy(
        freezes_in_scope,
        render_by_run,
        trigger_ms=args.trigger_ms,
        switch_ms=args.switch_ms,
        latency_ms=args.generation_latency_ms,
        monitor_interval_ms=args.monitor_interval_ms,
    )
    for seg in segments:
        seg["policy"] = "render_gap_40_80_immediate"
    write_csv(out / "render_gap_40_80_gap_events.csv", events)
    write_csv(out / "render_gap_40_80_segments.csv", segments)
    write_csv(out / "render_gap_40_80_per_freeze.csv", per_freeze)

    overall = summarize_policy(freezes_in_scope, per_freeze, segments, observed_span_ms, target_only=False)
    target = summarize_policy(freezes_in_scope, per_freeze, segments, observed_span_ms, target_only=True)
    overall.update(
        {
            "policy": "render_gap_40_80_immediate",
            "generation_trigger_ms": args.trigger_ms,
            "display_switch_ms": args.switch_ms,
            "generation_latency_ms": args.generation_latency_ms,
            "monitor_interval_ms": args.monitor_interval_ms,
            "runs_in_scope": len(included_runs),
            "observed_span_ms": round(observed_span_ms, 3),
        }
    )
    target.update(
        {
            "policy": "render_gap_40_80_immediate",
            "generation_trigger_ms": args.trigger_ms,
            "display_switch_ms": args.switch_ms,
            "generation_latency_ms": args.generation_latency_ms,
            "monitor_interval_ms": args.monitor_interval_ms,
            "runs_in_scope": len(included_runs),
            "observed_span_ms": round(observed_span_ms, 3),
        }
    )
    timing_target = summarize_timing(per_freeze, target_only=True)
    timing_all = summarize_timing(per_freeze, target_only=False)
    write_csv(out / "render_gap_40_80_overall_summary.csv", [overall, timing_all])
    write_csv(out / "render_gap_40_80_target_summary.csv", [target, timing_target])
    write_csv(
        out / "render_gap_40_80_target_fp_summary.csv",
        [
            {
                "policy": "render_gap_40_80_immediate",
                "target_events": target["freeze_events"],
                "target_duration_ms": target["freeze_duration_ms"],
                "visible_generated_time_ms": target["generated_visible_time_ms"],
                "target_concealed_ms": target["concealed_freeze_time_ms"],
                "target_coverage": target["concealed_freeze_coverage"],
                "target_fp_time_ms": target["target_fp_time_ms"],
                "target_fp_seconds_per_hour": target["visible_fp_seconds_per_hour"],
                "target_precision": target["target_precision"],
            }
        ],
    )

    latency_rows: list[dict[str, Any]] = []
    for latency in LATENCY_SWEEP_MS:
        _, segs_l, freeze_l = run_policy(
            freezes_in_scope,
            render_by_run,
            trigger_ms=args.trigger_ms,
            switch_ms=args.switch_ms,
            latency_ms=latency,
            monitor_interval_ms=args.monitor_interval_ms,
        )
        row = summarize_policy(freezes_in_scope, freeze_l, segs_l, observed_span_ms, target_only=True)
        row.update(
            {
                "policy": "render_gap_40_80_immediate",
                "generation_latency_ms": latency,
                "generation_trigger_ms": args.trigger_ms,
                "display_switch_ms": args.switch_ms,
            }
        )
        latency_rows.append(row)
    write_csv(out / "render_gap_40_80_latency_sensitivity.csv", latency_rows)

    threshold_rows: list[dict[str, Any]] = []
    for trig in TRIGGER_SWEEP_MS:
        for sw in SWITCH_SWEEP_MS:
            _, segs_t, freeze_t = run_policy(
                freezes_in_scope,
                render_by_run,
                trigger_ms=trig,
                switch_ms=sw,
                latency_ms=args.generation_latency_ms,
                monitor_interval_ms=args.monitor_interval_ms,
            )
            row = summarize_policy(freezes_in_scope, freeze_t, segs_t, observed_span_ms, target_only=True)
            row.update(
                {
                    "generation_trigger_ms": trig,
                    "display_switch_ms": sw,
                    "generation_latency_ms": args.generation_latency_ms,
                }
            )
            threshold_rows.append(row)
    write_csv(out / "render_gap_40_80_threshold_sensitivity.csv", threshold_rows)

    oracles = make_oracles(freezes_in_scope, args.generation_latency_ms)
    write_csv(out / "oracle_comparison_render_gap_40_80.csv", oracles)
    comparison = final_comparison(out, target, oracles)
    write_csv(out / "final_policy_comparison_with_render_gap_40_80.csv", comparison)

    runtime_note = """# Runtime knob check

Temporary 40/80 immediate-return smoke policy corresponds to:

```bash
WEBRTC_WAV2LIP_GENERATION_RISK_MS=40
WEBRTC_WAV2LIP_SWITCH_MIN_GAP_MS=80
WEBRTC_WAV2LIP_SWITCH_GAP_MS=80
WEBRTC_WAV2LIP_SWITCH_SLACK_MS=0
WEBRTC_WAV2LIP_RETURN_IMMEDIATE=1
WEBRTC_WAV2LIP_RETURN_CONSECUTIVE_REAL_FRAMES=1
WEBRTC_WAV2LIP_RETURN_STABLE_MS=0
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_PREFETCH_MS=-1
```

The native overlay computes visible switch threshold as:

```text
switch_gap_ms = WEBRTC_WAV2LIP_SWITCH_GAP_MS if set
                else estimated_render_interval_ms + WEBRTC_WAV2LIP_SWITCH_SLACK_MS
switch_gap_ms = max(switch_gap_ms, WEBRTC_WAV2LIP_SWITCH_MIN_GAP_MS, estimated_render_interval_ms)
```

Therefore setting both `WEBRTC_WAV2LIP_SWITCH_GAP_MS=80` and
`WEBRTC_WAV2LIP_SWITCH_SLACK_MS=0` makes the intended 80 ms threshold explicit
as long as the estimated render interval remains below 80 ms, which is the
expected case for 25 fps rendering.
"""
    (out / "runtime_knob_check_render_gap_40_80.md").write_text(runtime_note, encoding="utf-8")
    print(f"wrote {out}")
    print(
        f"main target coverage={target['concealed_freeze_coverage']:.3f} "
        f"precision={target['target_precision']:.3f} "
        f"fp_s_per_hour={target['visible_fp_seconds_per_hour']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
