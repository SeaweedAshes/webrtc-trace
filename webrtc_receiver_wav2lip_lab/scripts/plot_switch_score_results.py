#!/usr/bin/env python3
"""Create presentation figures for switch-score policy evaluation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/switch_score_policy_eval_original_only"
)
RUNS_ROOT = Path("/home/widen/Sync/webrtc-trace-runs")
SAMPLE_INTERVAL_MS = 20.0


def savefig(fig: plt.Figure, path_base: Path, pdf: bool = True) -> None:
    path_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path_base.with_suffix(".png"), dpi=180, bbox_inches="tight")
    if pdf:
        fig.savefig(path_base.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def as_num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def add_interval_and_row_id(samples: pd.DataFrame) -> pd.DataFrame:
    samples = samples.sort_values(["run_id", "timestamp_ms"]).copy()
    samples["row_id"] = np.arange(len(samples))
    ts = as_num(samples, "timestamp_ms")
    next_sample = samples.groupby("run_id")["timestamp_ms"].shift(-1)
    interval = (pd.to_numeric(next_sample, errors="coerce") - ts).fillna(SAMPLE_INTERVAL_MS)
    next_real = as_num(samples, "next_real_render_ms", np.nan)
    real_cap = (next_real - ts).where((next_real - ts) > 0, SAMPLE_INTERVAL_MS)
    samples["interval_ms"] = np.minimum(interval.clip(1.0, SAMPLE_INTERVAL_MS), real_cap).clip(0.0, SAMPLE_INTERVAL_MS)
    return samples


def baseline_points(out_dir: Path) -> pd.DataFrame:
    final = pd.read_csv(out_dir / "final_policy_comparison.csv")
    keep = [
        "render-gap only",
        "continuous gate + next-render return",
        "headroom prefetch h0",
        "reactive_oracle_latency31",
        "perfect_prefetch_oracle",
    ]
    return final[final["method"].isin(keep)].copy()


def plot_pareto(out_dir: Path) -> None:
    figs = out_dir / "figures"
    sweep = pd.read_csv(out_dir / "switch_score_policy_sweep.csv")
    final = baseline_points(out_dir)
    fig, ax = plt.subplots(figsize=(8.8, 5.3))
    for score_name, group in sweep.groupby("score_name"):
        ax.scatter(
            group["target_fp_seconds_per_hour"],
            group["target_coverage"] * 100.0,
            s=28,
            alpha=0.35,
            label=f"{score_name} sweep",
        )
    markers = {
        "render-gap only": "X",
        "continuous gate + next-render return": "D",
        "headroom prefetch h0": "P",
        "reactive_oracle_latency31": "*",
        "perfect_prefetch_oracle": "*",
    }
    for _, row in final.iterrows():
        x = pd.to_numeric(pd.Series([row["target_fp_seconds_per_hour"]]), errors="coerce").fillna(0).iloc[0]
        y = float(row["target_coverage"]) * 100.0
        ax.scatter(x, y, marker=markers.get(row["method"], "o"), s=130, edgecolor="black", linewidth=0.8, label=row["method"])
        ax.annotate(row["method"], (x, y), xytext=(6, 4), textcoords="offset points", fontsize=8)
    ax.set_xlabel("Target false-positive visible time (seconds/hour)")
    ax.set_ylabel("Target duration coverage (%)")
    ax.set_title("Display switch Pareto: coverage vs false-positive visible time")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7, ncols=2, loc="lower right")
    savefig(fig, figs / "switch_score_pareto")


def plot_precision_coverage(out_dir: Path) -> None:
    figs = out_dir / "figures"
    sweep = pd.read_csv(out_dir / "switch_score_policy_sweep.csv")
    final = baseline_points(out_dir)
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    for score_name, group in sweep.groupby("score_name"):
        ax.scatter(group["target_coverage"] * 100.0, group["target_precision"] * 100.0, s=28, alpha=0.35, label=f"{score_name} sweep")
    for _, row in final.iterrows():
        precision = pd.to_numeric(pd.Series([row["target_precision"]]), errors="coerce").fillna(1.0).iloc[0]
        ax.scatter(float(row["target_coverage"]) * 100.0, precision * 100.0, s=130, edgecolor="black", linewidth=0.8)
        ax.annotate(row["method"], (float(row["target_coverage"]) * 100.0, precision * 100.0), xytext=(6, 4), textcoords="offset points", fontsize=8)
    ax.axvline(45.8874, linestyle="--", color="0.3", linewidth=1, label="continuous gate coverage")
    ax.axhline(48.2213, linestyle=":", color="0.3", linewidth=1, label="continuous gate precision")
    ax.set_xlabel("Target duration coverage (%)")
    ax.set_ylabel("Target precision (%)")
    ax.set_title("Display switch precision/coverage trade-off")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7, ncols=2, loc="lower left")
    savefig(fig, figs / "precision_coverage")


def plot_bar(out_dir: Path) -> None:
    figs = out_dir / "figures"
    final = pd.read_csv(out_dir / "final_policy_comparison.csv")
    best = pd.read_csv(out_dir / "switch_score_policy_best_points.csv")
    best_policy = best[best["meets_minimum"] == True]
    if best_policy.empty:
        best_policy = best.sort_values("coverage_precision_f1", ascending=False).head(1)
    best_policy_id = best_policy.iloc[0]["policy_id"]
    methods = [
        "render-gap only",
        "continuous gate + next-render return",
        "headroom prefetch h0",
        f"best score switch\n{best_policy.iloc[0]['score_name']}",
        "reactive_oracle_latency31",
    ]
    rows: list[pd.Series] = []
    for method in ["render-gap only", "continuous gate + next-render return", "headroom prefetch h0", "reactive_oracle_latency31"]:
        rows.append(final[final["method"] == method].iloc[0])
    rows.insert(3, final[final["policy_id"] == best_policy_id].iloc[0])
    coverage = [float(r["target_coverage"]) * 100.0 for r in rows]
    precision = [pd.to_numeric(pd.Series([r["target_precision"]]), errors="coerce").fillna(100.0).iloc[0] * 100.0 for r in rows]
    fp = [pd.to_numeric(pd.Series([r["target_fp_seconds_per_hour"]]), errors="coerce").fillna(0.0).iloc[0] for r in rows]

    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.6))
    colors = ["#4C78A8", "#59A14F", "#F28E2B", "#E15759", "#9C755F"]
    for ax, vals, title, ylabel in [
        (axes[0], coverage, "Target coverage", "%"),
        (axes[1], precision, "Target precision", "%"),
        (axes[2], fp, "FP visible time", "seconds/hour"),
    ]:
        ax.bar(range(len(vals)), vals, color=colors)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.set_xticks(range(len(vals)))
        ax.set_xticklabels(methods, rotation=25, ha="right", fontsize=8)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Policy comparison for receiver-side display switch")
    savefig(fig, figs / "policy_comparison_bar")


def segment_target_overlap(seg: pd.Series, targets: pd.DataFrame) -> tuple[float, str]:
    run_targets = targets[targets["run"] == seg["run_id"]]
    total = 0.0
    best_key = ""
    best_ov = 0.0
    for _, t in run_targets.iterrows():
        ov = max(0.0, min(float(seg["end_ms"]), float(t["end_ms"])) - max(float(seg["start_ms"]), float(t["start_ms"])))
        total += ov
        if ov > best_ov:
            best_ov = ov
            best_key = t["freeze_key"]
    return total, best_key


def choose_cases(out_dir: Path) -> dict[str, dict[str, Any]]:
    best = pd.read_csv(out_dir / "switch_score_policy_best_points.csv")
    best_row = best[best["meets_minimum"] == True]
    if best_row.empty:
        best_row = best.sort_values("coverage_precision_f1", ascending=False).head(1)
    policy_id = best_row.iloc[0]["policy_id"]
    segments = pd.read_csv(out_dir / "switch_score_policy_segments.csv")
    segments = segments[segments["policy_id"] == policy_id].copy()
    targets = pd.read_csv(out_dir / "target_freeze_intervals.csv")
    targets["freeze_key"] = targets["run"].astype(str) + "#" + targets["freeze_id"].astype(str)
    overlaps = []
    for _, seg in segments.iterrows():
        ov, key = segment_target_overlap(seg, targets)
        overlaps.append((ov, key))
    segments["target_overlap_ms"] = [x[0] for x in overlaps]
    segments["target_key"] = [x[1] for x in overlaps]
    good = segments[segments["target_overlap_ms"] > 0].sort_values("target_overlap_ms", ascending=False).iloc[0]
    fp = segments[segments["target_overlap_ms"] <= 0].sort_values("duration_ms", ascending=False).iloc[0]
    by_key_overlap = segments.groupby("target_key")["target_overlap_ms"].sum().to_dict()
    targets["covered_ms"] = targets["freeze_key"].map(by_key_overlap).fillna(0.0)
    targets["residual_ms"] = targets["duration_ms"] - targets["covered_ms"]
    missed = targets.sort_values("residual_ms", ascending=False).iloc[0]
    return {
        "good": {"policy_id": policy_id, "run": good["run_id"], "start": good["start_ms"], "end": good["end_ms"], "target_key": good["target_key"]},
        "false_positive": {"policy_id": policy_id, "run": fp["run_id"], "start": fp["start_ms"], "end": fp["end_ms"], "target_key": ""},
        "missed_freeze": {"policy_id": policy_id, "run": missed["run"], "start": missed["start_ms"], "end": missed["end_ms"], "target_key": missed["freeze_key"]},
    }


def read_times(path: Path, candidates: list[str]) -> np.ndarray:
    if not path.exists():
        return np.array([])
    rows = pd.read_csv(path, low_memory=False)
    for col in candidates:
        if col in rows:
            vals = pd.to_numeric(rows[col], errors="coerce").dropna().to_numpy(dtype=float)
            return vals[vals > 0]
    return np.array([])


def plot_case(out_dir: Path, name: str, case: dict[str, Any], samples: pd.DataFrame, targets: pd.DataFrame, segments: pd.DataFrame) -> None:
    figs = out_dir / "figures"
    run = str(case["run"])
    start = float(case["start"]) - 500.0
    end = float(case["end"]) + 500.0
    run_samples = samples[(samples["run_id"] == run) & (samples["timestamp_ms"] >= start) & (samples["timestamp_ms"] <= end)].copy()
    if run_samples.empty:
        return
    t0 = start
    run_dir = RUNS_ROOT / run / "receiver"
    render_times = read_times(run_dir / "receiver_rendered_frames.csv", ["timestamp_ms"])
    render_times = render_times[(render_times >= start) & (render_times <= end)] - t0
    audio_times = read_times(run_dir / "audio_packet_inserts.csv", ["wall_time_ms", "timestamp_ms"])
    audio_times = audio_times[(audio_times >= start) & (audio_times <= end)] - t0

    fig, axes = plt.subplots(5, 1, figsize=(10.5, 7.8), sharex=True, gridspec_kw={"height_ratios": [0.65, 0.55, 1.2, 0.8, 1.1]})
    x = run_samples["timestamp_ms"] - t0

    axes[0].eventplot(render_times, lineoffsets=0.5, linelengths=0.8, colors="#2F5597")
    axes[0].set_yticks([])
    axes[0].set_ylabel("real\nrender", rotation=0, labelpad=24, va="center")

    target_run = targets[targets["run"] == run]
    for _, row in target_run.iterrows():
        if row["end_ms"] >= start and row["start_ms"] <= end:
            for ax in axes:
                ax.axvspan(max(row["start_ms"], start) - t0, min(row["end_ms"], end) - t0, color="#D62728", alpha=0.16)
    policy_segments = segments[(segments["policy_id"] == case["policy_id"]) & (segments["run_id"] == run)]
    for _, seg in policy_segments.iterrows():
        if seg["end_ms"] >= start and seg["start_ms"] <= end:
            axes[1].axvspan(max(seg["start_ms"], start) - t0, min(seg["end_ms"], end) - t0, color="#2CA02C", alpha=0.65)
    axes[1].set_yticks([])
    axes[1].set_ylabel("generated\nvisible", rotation=0, labelpad=30, va="center")

    axes[2].plot(x, run_samples["score_logistic"], color="#1F77B4", linewidth=1.4)
    axes[2].set_ylabel("SwitchScore")
    axes[2].set_ylim(-0.05, 1.05)
    axes[2].grid(True, alpha=0.25)

    axes[3].step(x, run_samples["audio_condition_B80"], where="post", color="#9467BD", linewidth=1.3)
    if len(audio_times) < 250:
        axes[3].eventplot(audio_times, lineoffsets=0.2, linelengths=0.25, colors="#9467BD", alpha=0.5)
    axes[3].set_ylim(-0.1, 1.1)
    axes[3].set_ylabel("audio\nB80", rotation=0, labelpad=24, va="center")
    axes[3].grid(True, alpha=0.25)

    axes[4].plot(x, as_num(run_samples, "time_since_last_video_packet_ms"), label="video packet age", color="#D55E00")
    axes[4].plot(x, as_num(run_samples, "time_since_last_playable_frame_ms"), label="playable frame age", color="#009E73")
    axes[4].axhline(80, color="0.25", linestyle=":", linewidth=1)
    axes[4].set_ylabel("age (ms)")
    axes[4].set_xlabel("time from window start (ms)")
    axes[4].grid(True, alpha=0.25)
    axes[4].legend(fontsize=8, loc="upper right")
    fig.suptitle(name.replace("_", " "))
    savefig(fig, figs / name)


def plot_cases(out_dir: Path) -> None:
    samples = pd.read_csv(out_dir / "switch_decision_samples.csv", low_memory=False)
    samples = add_interval_and_row_id(samples)
    logistic = pd.read_csv(out_dir / "switch_score_logistic_predictions.csv", usecols=["row_id", "score_logistic"])
    samples = samples.merge(logistic, on="row_id", how="left")
    targets = pd.read_csv(out_dir / "target_freeze_intervals.csv")
    targets["freeze_key"] = targets["run"].astype(str) + "#" + targets["freeze_id"].astype(str)
    segments = pd.read_csv(out_dir / "switch_score_policy_segments.csv")
    cases = choose_cases(out_dir)
    output_names = {
        "good": "case_good_switch",
        "false_positive": "case_false_positive",
        "missed_freeze": "case_missed_freeze",
    }
    for name, case in cases.items():
        plot_case(out_dir, output_names.get(name, f"case_{name}"), case, samples, targets, segments)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    plot_pareto(args.out_dir)
    plot_precision_coverage(args.out_dir)
    plot_bar(args.out_dir)
    plot_cases(args.out_dir)
    print(f"wrote figures to {args.out_dir / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
