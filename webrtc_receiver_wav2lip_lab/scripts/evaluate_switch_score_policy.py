#!/usr/bin/env python3
"""Evaluate score-based display-switch policies for freeze concealment.

The score is a receiver-side display decision score. It is not a network-cause
classifier.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/switch_score_policy_eval_original_only"
)

GENERATION_LATENCY_MS = 31.0
SAMPLE_INTERVAL_MS = 20.0
STALE_GENERATED_MS = 1000.0
CONTINUOUS_GATE_TARGET_COVERAGE = 0.458874
CONTINUOUS_GATE_TARGET_PRECISION = 0.482213


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


def read_metric_csv(path: Path) -> dict[str, float]:
    rows = pd.read_csv(path)
    out: dict[str, float] = {}
    for _, row in rows.iterrows():
        try:
            out[str(row["metric"])] = float(row["value"])
        except Exception:
            continue
    return out


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def clamp01(series: pd.Series | np.ndarray) -> pd.Series | np.ndarray:
    return np.clip(series, 0.0, 1.0)


def as_num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def add_interval_columns(samples: pd.DataFrame) -> pd.DataFrame:
    samples = samples.sort_values(["run_id", "timestamp_ms"]).copy()
    ts = as_num(samples, "timestamp_ms")
    next_real = as_num(samples, "next_real_render_ms", np.nan)
    next_sample = samples.groupby("run_id")["timestamp_ms"].shift(-1)
    interval = pd.to_numeric(next_sample, errors="coerce") - ts
    interval = interval.fillna(SAMPLE_INTERVAL_MS)
    interval = interval.clip(lower=1.0, upper=SAMPLE_INTERVAL_MS)
    real_cap = next_real - ts
    real_cap = real_cap.where(real_cap > 0, SAMPLE_INTERVAL_MS)
    samples["interval_ms"] = np.minimum(interval, real_cap).clip(lower=0.0, upper=SAMPLE_INTERVAL_MS)
    samples["row_id"] = np.arange(len(samples))
    return samples


def build_scores(samples: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = samples
    scores = pd.DataFrame(index=s.index)
    scores["render_gap_score"] = clamp01(as_num(s, "render_gap_ms") / 200.0)
    scores["video_packet_age_score"] = clamp01(as_num(s, "time_since_last_video_packet_ms") / 100.0)
    scores["playable_frame_age_score"] = clamp01(as_num(s, "time_since_last_playable_frame_ms") / 80.0)

    headroom = as_num(s, "playout_headroom_ms", np.nan)
    headroom_valid = np.isfinite(headroom)
    scores["headroom_low_score"] = clamp01((80.0 - headroom.fillna(80.0)) / 80.0)
    future_units = as_num(s, "future_render_valid_units", np.nan)
    scores["no_future_renderable_score"] = (
        headroom_valid & np.isfinite(future_units) & (future_units <= 0)
    ).astype(float)

    frame_gap = as_num(s, "max_recent_frame_completion_gap_ms", 0.0)
    scores["frame_completion_gap_score"] = clamp01(frame_gap / 100.0)
    retrans = as_num(s, "recent_rtx_count", 0.0) + as_num(s, "recent_missing_seq_count", 0.0)
    scores["retransmission_or_missing_score"] = clamp01(retrans / 3.0)
    scores["no_fresh_real_frame_score"] = as_num(s, "no_fresh_real_frame", 0.0).clip(0, 1)

    component_cols = [
        "render_gap_score",
        "video_packet_age_score",
        "playable_frame_age_score",
        "headroom_low_score",
        "no_future_renderable_score",
        "frame_completion_gap_score",
        "retransmission_or_missing_score",
        "no_fresh_real_frame_score",
    ]
    scores["score_rule_equal"] = scores[component_cols].mean(axis=1)

    weights = {
        "render_gap_score": 0.15,
        "video_packet_age_score": 0.18,
        "playable_frame_age_score": 0.18,
        "headroom_low_score": 0.12,
        "no_future_renderable_score": 0.10,
        "frame_completion_gap_score": 0.12,
        "retransmission_or_missing_score": 0.05,
        "no_fresh_real_frame_score": 0.10,
    }
    scores["score_rule_weighted"] = sum(scores[col] * w for col, w in weights.items())

    importance_rows = [
        {
            "model": "rule_weighted",
            "feature": feature,
            "importance": weight,
            "note": "fixed hand-designed weight",
        }
        for feature, weight in weights.items()
    ]
    return scores, pd.DataFrame(importance_rows)


LOGISTIC_FEATURES = [
    "render_gap_score",
    "video_packet_age_score",
    "playable_frame_age_score",
    "headroom_low_score",
    "no_future_renderable_score",
    "frame_completion_gap_score",
    "retransmission_or_missing_score",
    "no_fresh_real_frame_score",
    "switch_deadline_missed",
    "audio_condition_B80",
    "generated_frame_ready",
    "target_bitrate_drop",
    "gcc_overuse_count",
]


def train_manual_logistic(samples: pd.DataFrame, scores: pd.DataFrame, out_dir: Path) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    work = pd.concat(
        [
            samples[["run_id", "decision_label", "generated_frame_ready", "audio_condition_B80", "no_fresh_real_frame"]].copy(),
            scores.copy(),
        ],
        axis=1,
    )
    work["switch_deadline_missed"] = as_num(samples, "switch_deadline_missed", 0.0)
    work["target_bitrate_drop"] = as_num(samples, "target_bitrate_drop", 0.0).clip(0.0, 1.0)
    work["gcc_overuse_count"] = clamp01(as_num(samples, "gcc_overuse_count", 0.0) / 3.0)
    work["generated_frame_ready"] = as_num(samples, "generated_frame_ready", 0.0).clip(0, 1)
    work["audio_condition_B80"] = as_num(samples, "audio_condition_B80", 0.0).clip(0, 1)

    hard_focus = (
        (work["generated_frame_ready"] >= 1)
        & (work["audio_condition_B80"] >= 1)
        & (as_num(samples, "no_fresh_real_frame", 0.0) >= 1)
    )
    y_all = work["decision_label"].astype(int).to_numpy()
    pred = np.zeros(len(work), dtype=float)
    runs = np.array(sorted(work["run_id"].unique()))
    folds = [runs[i::5] for i in range(5) if len(runs[i::5]) > 0]
    coef_rows: list[dict[str, Any]] = []
    cv_rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(7)

    for fold_idx, test_runs in enumerate(folds, start=1):
        test_mask = work["run_id"].isin(test_runs).to_numpy()
        train_mask = (~test_mask) & hard_focus.to_numpy()
        test_focus = test_mask & hard_focus.to_numpy()
        if y_all[train_mask].sum() < 10 or y_all[test_focus].sum() < 1:
            continue

        pos_idx = np.flatnonzero(train_mask & (y_all == 1))
        neg_idx = np.flatnonzero(train_mask & (y_all == 0))
        max_neg = min(len(neg_idx), max(1000, len(pos_idx) * 20))
        neg_idx = rng.choice(neg_idx, size=max_neg, replace=False) if len(neg_idx) > max_neg else neg_idx
        train_idx = np.concatenate([pos_idx, neg_idx])
        rng.shuffle(train_idx)

        X_train = work.iloc[train_idx][LOGISTIC_FEATURES].astype(float).to_numpy()
        y_train = y_all[train_idx].astype(float)
        med = np.nanmedian(X_train, axis=0)
        q25 = np.nanpercentile(X_train, 25, axis=0)
        q75 = np.nanpercentile(X_train, 75, axis=0)
        scale = np.where((q75 - q25) > 1e-6, q75 - q25, 1.0)
        X_train = np.nan_to_num((X_train - med) / scale)
        X_train = np.hstack([np.ones((len(X_train), 1)), X_train])

        pos_count = max(1.0, y_train.sum())
        neg_count = max(1.0, len(y_train) - y_train.sum())
        sample_weight = np.where(y_train > 0, len(y_train) / (2 * pos_count), len(y_train) / (2 * neg_count))

        beta = np.zeros(X_train.shape[1], dtype=float)
        lr = 0.08
        l2 = 0.002
        for _ in range(500):
            p = sigmoid(X_train @ beta)
            grad = (X_train.T @ ((p - y_train) * sample_weight)) / len(y_train)
            grad[1:] += l2 * beta[1:]
            beta -= lr * grad

        X_test = work.loc[test_mask, LOGISTIC_FEATURES].astype(float).to_numpy()
        X_test = np.nan_to_num((X_test - med) / scale)
        X_test = np.hstack([np.ones((len(X_test), 1)), X_test])
        pred[test_mask] = sigmoid(X_test @ beta)

        for name, value in zip(["intercept", *LOGISTIC_FEATURES], beta):
            coef_rows.append({"fold": fold_idx, "feature": name, "coefficient": value})

        focus_pred = pred[test_focus]
        focus_y = y_all[test_focus]
        for theta in [0.3, 0.5, 0.7]:
            tp = int(((focus_pred >= theta) & (focus_y == 1)).sum())
            fp = int(((focus_pred >= theta) & (focus_y == 0)).sum())
            fn = int(((focus_pred < theta) & (focus_y == 1)).sum())
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            cv_rows.append(
                {
                    "model": "manual_logistic",
                    "fold": fold_idx,
                    "test_runs": " ".join(map(str, test_runs)),
                    "threshold": theta,
                    "test_focus_samples": int(test_focus.sum()),
                    "test_positive_samples": int(focus_y.sum()),
                    "sample_precision": precision,
                    "sample_recall": recall,
                }
            )

    if not coef_rows:
        pred = scores["score_rule_weighted"].to_numpy()
        coef_df = pd.DataFrame(
            [{"fold": "skipped", "feature": "manual_logistic", "coefficient": "", "note": "insufficient run-level positives"}]
        )
        cv_df = pd.DataFrame(
            [{"model": "manual_logistic", "status": "skipped", "reason": "insufficient run-level positives"}]
        )
    else:
        coef_df = pd.DataFrame(coef_rows)
        cv_df = pd.DataFrame(cv_rows)

    if importlib.util.find_spec("xgboost") is None and importlib.util.find_spec("lightgbm") is None:
        cv_df = pd.concat(
            [
                cv_df,
                pd.DataFrame(
                    [
                        {
                            "model": "gradient_boosted_tree",
                            "status": "skipped",
                            "reason": "xgboost/lightgbm not installed",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    return pd.Series(pred, index=samples.index, name="score_logistic"), coef_df, cv_df


def load_target_intervals(out_dir: Path) -> pd.DataFrame:
    targets = pd.read_csv(out_dir / "target_freeze_intervals.csv")
    targets["freeze_key"] = targets["run"].astype(str) + "#" + targets["freeze_id"].astype(str)
    return targets


def add_target_overlap_columns(samples: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Precompute target-freeze overlap for every display decision sample."""
    samples = samples.copy()
    start = samples["timestamp_ms"].astype(float)
    end = start + samples["interval_ms"].astype(float)
    samples["target_overlap_ms"] = 0.0
    samples["target_overlap_freeze_key"] = ""
    for target in targets.itertuples(index=False):
        run_mask = samples["run_id"].astype(str).to_numpy() == str(target.run)
        if not run_mask.any():
            continue
        ov = np.maximum(
            0.0,
            np.minimum(end.to_numpy(dtype=float), float(target.end_ms))
            - np.maximum(start.to_numpy(dtype=float), float(target.start_ms)),
        )
        mask = run_mask & (ov > 0)
        if not mask.any():
            continue
        samples.loc[mask, "target_overlap_ms"] = samples.loc[mask, "target_overlap_ms"].to_numpy(dtype=float) + ov[mask]
        samples.loc[mask, "target_overlap_freeze_key"] = str(target.freeze_key)
    return samples


def build_visible_segments(policy_id: str, visible_rows: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if visible_rows.empty:
        return pd.DataFrame(columns=["policy_id", "run_id", "start_ms", "end_ms", "duration_ms"])
    visible_rows = visible_rows.sort_values(["run_id", "timestamp_ms"])
    for run, group in visible_rows.groupby("run_id", sort=False):
        start = None
        end = None
        for row in group.itertuples(index=False):
            t = float(row.timestamp_ms)
            row_end = t + float(row.interval_ms)
            if start is None:
                start, end = t, row_end
                continue
            if t <= end + 1e-6:
                end = max(end, row_end)
            else:
                rows.append({"policy_id": policy_id, "run_id": run, "start_ms": start, "end_ms": end, "duration_ms": end - start})
                start, end = t, row_end
        if start is not None:
            rows.append({"policy_id": policy_id, "run_id": run, "start_ms": start, "end_ms": end, "duration_ms": end - start})
    return pd.DataFrame(rows)


def visible_mask(samples: pd.DataFrame, score: pd.Series, policy: str, theta_on: float, theta_off: float | None = None) -> pd.Series:
    generated_ready = as_num(samples, "generated_frame_ready", 0.0) >= 1
    audio_ok = as_num(samples, "audio_condition_B80", 0.0) >= 1
    no_fresh = as_num(samples, "no_fresh_real_frame", 0.0) >= 1
    stale = as_num(samples, "generated_frame_age_ms", 0.0) > STALE_GENERATED_MS
    hard_gate = generated_ready & audio_ok & no_fresh & (~stale)

    if policy == "score_replace":
        return hard_gate & (score >= theta_on)

    if policy == "score_continuous_gate":
        deadline = as_num(samples, "switch_deadline_missed", 0.0) >= 1
        video_age = as_num(samples, "time_since_last_video_packet_ms", 0.0) >= 100.0
        playable_age = as_num(samples, "time_since_last_playable_frame_ms", 0.0) >= 80.0
        frame_gap = as_num(samples, "max_recent_frame_completion_gap_ms", 0.0) >= 80.0
        continuous_gate = deadline & (video_age | playable_age | frame_gap)
        return hard_gate & continuous_gate & (score >= theta_on)

    if policy != "score_hysteresis":
        raise ValueError(f"unknown policy {policy}")

    assert theta_off is not None
    n = len(samples)
    visible_arr = np.zeros(n, dtype=bool)
    hard_arr = hard_gate.to_numpy(dtype=bool)
    score_arr = score.to_numpy(dtype=float)
    t_arr = samples["timestamp_ms"].to_numpy(dtype=float)
    next_real_arr = as_num(samples, "next_real_render_ms", np.nan).to_numpy(dtype=float)
    run_arr = samples["run_id"].astype(str).to_numpy()
    state = False
    active_until = -math.inf
    last_run = None
    for i in range(n):
        run = run_arr[i]
        if run != last_run:
            state = False
            active_until = -math.inf
            last_run = run
        t = t_arr[i]
        if t >= active_until:
            state = False
        if not state:
            if hard_arr[i] and score_arr[i] >= theta_on:
                state = True
        else:
            if (not hard_arr[i]) or score_arr[i] <= theta_off:
                state = False
        visible_arr[i] = state and hard_arr[i]
        if visible_arr[i] and np.isfinite(next_real_arr[i]):
            active_until = max(active_until, next_real_arr[i])
    return pd.Series(visible_arr, index=samples.index)


def evaluate_policy(
    samples: pd.DataFrame,
    targets: pd.DataFrame,
    score: pd.Series,
    score_name: str,
    policy: str,
    theta_on: float,
    theta_off: float | None,
    observed_span_ms: float,
) -> tuple[dict[str, Any], pd.DataFrame]:
    policy_id = f"{score_name}:{policy}:on{theta_on:.2f}" + (f":off{theta_off:.2f}" if theta_off is not None else "")
    mask = visible_mask(samples, score, policy, theta_on, theta_off)
    visible_rows = samples.loc[mask, ["run_id", "timestamp_ms", "interval_ms"]].copy()
    segments = build_visible_segments(policy_id, visible_rows)

    target_overlap_ms = 0.0
    event_overlap: dict[str, float] = {str(row.freeze_key): 0.0 for row in targets.itertuples(index=False)}

    if not visible_rows.empty:
        visible_full = samples.loc[mask, ["target_overlap_ms", "target_overlap_freeze_key"]]
        target_overlap_ms = float(visible_full["target_overlap_ms"].sum())
        overlap_by_key = (
            visible_full[visible_full["target_overlap_ms"] > 0]
            .groupby("target_overlap_freeze_key")["target_overlap_ms"]
            .sum()
        )
        for key, val in overlap_by_key.items():
            event_overlap[str(key)] = float(val)

    visible_ms = float(visible_rows["interval_ms"].sum()) if not visible_rows.empty else 0.0
    target_duration_ms = float(targets["duration_ms"].sum())
    fp_ms = max(0.0, visible_ms - target_overlap_ms)
    any_events = sum(1 for val in event_overlap.values() if val > 0)
    full_events = 0
    duration_by_key = {str(row.freeze_key): float(row.duration_ms) for row in targets.itertuples(index=False)}
    for key, val in event_overlap.items():
        if val >= duration_by_key.get(key, math.inf) - 1e-6:
            full_events += 1

    switch_count = len(segments)
    avg_segment = float(segments["duration_ms"].mean()) if len(segments) else 0.0
    oscillations = int((segments["duration_ms"] < 100.0).sum()) if len(segments) else 0
    row = {
        "policy_id": policy_id,
        "score_name": score_name,
        "policy": policy,
        "theta_on": theta_on,
        "theta_off": theta_off if theta_off is not None else "",
        "target_events": len(targets),
        "target_duration_ms": target_duration_ms,
        "target_concealed_ms": target_overlap_ms,
        "target_coverage": target_overlap_ms / target_duration_ms if target_duration_ms else 0.0,
        "residual_target_freeze_ms": max(0.0, target_duration_ms - target_overlap_ms),
        "generated_visible_ms": visible_ms,
        "target_fp_ms": fp_ms,
        "target_precision": target_overlap_ms / visible_ms if visible_ms else 0.0,
        "target_fp_seconds_per_hour": (fp_ms / 1000.0) / (observed_span_ms / 3600000.0) if observed_span_ms else 0.0,
        "any_concealment_events": any_events,
        "any_concealment_event_ratio": any_events / len(targets) if len(targets) else 0.0,
        "fully_concealed_events": full_events,
        "fully_concealed_event_ratio": full_events / len(targets) if len(targets) else 0.0,
        "switch_count": switch_count,
        "oscillation_count": oscillations,
        "average_generated_segment_duration_ms": avg_segment,
        "stale_generated_frame_display_ms": 0.0,
        "generated_frames_produced_but_not_displayed": "",
        "compute_overhead_proxy": "",
    }
    if len(segments):
        segments = segments.copy()
        segments["score_name"] = score_name
        segments["policy"] = policy
        segments["theta_on"] = theta_on
        segments["theta_off"] = theta_off if theta_off is not None else ""
    return row, segments


def oracle_rows(targets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    duration = float(targets["duration_ms"].sum())
    reactive = float(np.maximum(targets["duration_ms"].to_numpy(dtype=float) - GENERATION_LATENCY_MS, 0.0).sum())
    rows = [
        {
            "oracle": "reactive_oracle_latency31",
            "target_duration_ms": duration,
            "target_concealed_ms": reactive,
            "target_coverage": reactive / duration if duration else 0.0,
            "target_fp_ms": 0.0,
            "target_precision": 1.0,
        },
        {
            "oracle": "perfect_prefetch_oracle",
            "target_duration_ms": duration,
            "target_concealed_ms": duration,
            "target_coverage": 1.0,
            "target_fp_ms": 0.0,
            "target_precision": 1.0,
        },
    ]
    frac = [
        {
            "metric": "reactive_oracle_fraction_of_perfect",
            "value": reactive / duration if duration else 0.0,
        },
        {
            "metric": "generation_latency_ms",
            "value": GENERATION_LATENCY_MS,
        },
    ]
    return pd.DataFrame(rows), pd.DataFrame(frac)


def baseline_comparison(out_dir: Path, best_rows: pd.DataFrame, oracle: pd.DataFrame, observed_span_ms: float) -> pd.DataFrame:
    target_den = pd.read_csv(out_dir / "target_denominator_baseline_reproduced.csv")
    target_fp = pd.read_csv(out_dir / "target_fp_baseline_reproduced.csv")
    models = {
        "render_gap_only_latency31": "render-gap only",
        "start_gate_current_return_latency31": "start gate + stable return",
        "continuous_gate_to_next_render_latency31": "continuous gate + next-render return",
    }
    rows: list[dict[str, Any]] = [
        {
            "method": "hold-last-frame",
            "scope": "target_B80",
            "target_coverage": 0.0,
            "target_precision": "",
            "target_fp_seconds_per_hour": 0.0,
            "target_concealed_ms": 0.0,
            "target_fp_ms": 0.0,
        }
    ]
    for model, label in models.items():
        den = target_den[(target_den["target"] == "target_B80_usable_ratio_ge_0.8") & (target_den["model"] == model)]
        fp = target_fp[(target_fp["target"] == "target_B80_usable_ratio_ge_0.8") & (target_fp["model"] == model)]
        if den.empty or fp.empty:
            continue
        den_row = den.iloc[0]
        fp_row = fp.iloc[0]
        rows.append(
            {
                "method": label,
                "scope": "target_B80_existing_baseline",
                "target_coverage": float(den_row["concealed_time_ratio"]),
                "target_precision": float(fp_row["target_precision_time"]),
                "target_fp_seconds_per_hour": float(fp_row["target_fp_ms_per_min_observed"]) * 60.0 / 1000.0,
                "target_concealed_ms": float(den_row["concealed_ms"]),
                "target_fp_ms": float(fp_row["target_fp_time_ms"]),
                "any_concealment_event_ratio": float(den_row["any_concealed_event_ratio"]),
                "fully_concealed_event_ratio": float(den_row["fully_concealed_event_ratio"]),
            }
        )

    head = pd.read_csv(out_dir / "evaluation_baseline_reproduced.csv")
    h = head[head["model"] == "headroom_prefetch_h0"]
    if not h.empty:
        h = h.iloc[0]
        rows.append(
            {
                "method": "headroom prefetch h0",
                "scope": "headroom_subset",
                "target_coverage": float(h["coverage"]),
                "target_precision": float(h["target_precision"]),
                "target_fp_seconds_per_hour": "",
                "target_concealed_ms": float(h["concealed_freeze_time_ms"]),
                "target_fp_ms": float(h["visible_fp_time_ms"]),
            }
        )

    for _, row in best_rows.iterrows():
        rows.append(
            {
                "method": f"{row['score_name']} {row['policy']}",
                "scope": "switch_score_dataset_original_only",
                "policy_id": row["policy_id"],
                "target_coverage": row["target_coverage"],
                "target_precision": row["target_precision"],
                "target_fp_seconds_per_hour": row["target_fp_seconds_per_hour"],
                "target_concealed_ms": row["target_concealed_ms"],
                "target_fp_ms": row["target_fp_ms"],
                "any_concealment_event_ratio": row["any_concealment_event_ratio"],
                "fully_concealed_event_ratio": row["fully_concealed_event_ratio"],
            }
        )

    for _, row in oracle.iterrows():
        rows.append(
            {
                "method": row["oracle"],
                "scope": "oracle_target_B80",
                "target_coverage": row["target_coverage"],
                "target_precision": row["target_precision"],
                "target_fp_seconds_per_hour": 0.0,
                "target_concealed_ms": row["target_concealed_ms"],
                "target_fp_ms": 0.0,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--reuse-predictions", action="store_true")
    args = parser.parse_args()
    out_dir = args.out_dir

    samples = pd.read_csv(out_dir / "switch_decision_samples.csv", low_memory=False)
    samples = add_interval_columns(samples)
    targets = load_target_intervals(out_dir)
    samples = add_target_overlap_columns(samples, targets)
    label_summary = read_metric_csv(out_dir / "switch_decision_label_summary.csv")
    observed_span_ms = label_summary.get("observed_span_ms", float(samples["interval_ms"].sum()))

    scores, rule_importance = build_scores(samples)
    logistic_pred_path = out_dir / "switch_score_logistic_predictions.csv"
    if args.reuse_predictions and logistic_pred_path.exists():
        logistic_pred = pd.read_csv(logistic_pred_path, usecols=["row_id", "score_logistic"])
        logistic_pred = logistic_pred.sort_values("row_id")
        logistic_score = pd.Series(logistic_pred["score_logistic"].to_numpy(), index=samples.index, name="score_logistic")
        logistic_coef = pd.read_csv(out_dir / "switch_score_model_coefficients.csv") if (out_dir / "switch_score_model_coefficients.csv").exists() else pd.DataFrame()
        cv_summary = pd.read_csv(out_dir / "switch_score_cv_summary.csv") if (out_dir / "switch_score_cv_summary.csv").exists() else pd.DataFrame()
    else:
        logistic_score, logistic_coef, cv_summary = train_manual_logistic(samples, scores, out_dir)
    scores["score_logistic"] = logistic_score

    pred_cols = ["row_id", "run_id", "timestamp_ms", "decision_label"]
    pd.concat([samples[pred_cols], scores[["score_rule_equal", "score_rule_weighted"]]], axis=1).to_csv(
        out_dir / "switch_score_rule_predictions.csv", index=False
    )
    pd.concat([samples[pred_cols], scores[["score_logistic"]]], axis=1).to_csv(
        out_dir / "switch_score_logistic_predictions.csv", index=False
    )
    logistic_coef.to_csv(out_dir / "switch_score_model_coefficients.csv", index=False)
    cv_summary.to_csv(out_dir / "switch_score_cv_summary.csv", index=False)

    if not logistic_coef.empty and "coefficient" in logistic_coef.columns:
        coef_numeric = pd.to_numeric(logistic_coef["coefficient"], errors="coerce")
        imp = (
            logistic_coef.assign(abs_coefficient=coef_numeric.abs())
            .dropna(subset=["abs_coefficient"])
            .groupby("feature", as_index=False)["abs_coefficient"]
            .mean()
            .rename(columns={"abs_coefficient": "importance"})
        )
        imp["model"] = "manual_logistic"
        imp["note"] = "mean absolute coefficient across run-level folds"
        feat_imp = pd.concat([rule_importance, imp], ignore_index=True, sort=False)
    else:
        feat_imp = rule_importance
    feat_imp.to_csv(out_dir / "switch_score_feature_importance.csv", index=False)

    theta_on_values = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    theta_off_values = [0.1, 0.2, 0.3, 0.4, 0.5]
    score_map = {
        "rule_equal": scores["score_rule_equal"],
        "rule_weighted": scores["score_rule_weighted"],
        "manual_logistic": scores["score_logistic"],
    }
    sweep_rows: list[dict[str, Any]] = []
    all_segments: list[pd.DataFrame] = []
    for score_name, score in score_map.items():
        for theta_on in theta_on_values:
            for policy in ["score_replace", "score_continuous_gate"]:
                row, segments = evaluate_policy(
                    samples, targets, score, score_name, policy, theta_on, None, observed_span_ms
                )
                sweep_rows.append(row)
                if not segments.empty:
                    all_segments.append(segments)
            for theta_off in theta_off_values:
                if theta_on <= theta_off:
                    continue
                row, segments = evaluate_policy(
                    samples, targets, score, score_name, "score_hysteresis", theta_on, theta_off, observed_span_ms
                )
                sweep_rows.append(row)
                if not segments.empty:
                    all_segments.append(segments)

    sweep = pd.DataFrame(sweep_rows)
    sweep["coverage_precision_f1"] = np.where(
        (sweep["target_coverage"] + sweep["target_precision"]) > 0,
        2 * sweep["target_coverage"] * sweep["target_precision"] / (sweep["target_coverage"] + sweep["target_precision"]),
        0.0,
    )
    sweep["meets_minimum"] = (
        (sweep["target_coverage"] >= CONTINUOUS_GATE_TARGET_COVERAGE)
        & (sweep["target_precision"] >= CONTINUOUS_GATE_TARGET_PRECISION)
    )
    sweep.to_csv(out_dir / "switch_score_policy_sweep.csv", index=False)
    segments_df = pd.concat(all_segments, ignore_index=True) if all_segments else pd.DataFrame()
    segments_df.to_csv(out_dir / "switch_score_policy_segments.csv", index=False)

    best_rows: list[pd.Series] = []
    for score_name, score_group in sweep.groupby("score_name"):
        candidates = score_group[score_group["meets_minimum"]]
        if candidates.empty:
            candidates = score_group
        best_rows.append(
            candidates.sort_values(
                ["coverage_precision_f1", "target_precision", "target_coverage"],
                ascending=[False, False, False],
            ).iloc[0]
        )
    best = pd.DataFrame(best_rows)
    best["selection_rule"] = "best_f1_after_minimum_filter_or_best_available"
    best.to_csv(out_dir / "switch_score_policy_best_points.csv", index=False)

    oracle, oracle_fraction = oracle_rows(targets)
    oracle.to_csv(out_dir / "oracle_comparison.csv", index=False)
    oracle_fraction.to_csv(out_dir / "oracle_fraction_summary.csv", index=False)

    final = baseline_comparison(out_dir, best, oracle, observed_span_ms)
    final.to_csv(out_dir / "final_policy_comparison.csv", index=False)

    print(f"wrote policy evaluation to {out_dir}")
    print(best[["score_name", "policy", "theta_on", "theta_off", "target_coverage", "target_precision", "target_fp_seconds_per_hour"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
