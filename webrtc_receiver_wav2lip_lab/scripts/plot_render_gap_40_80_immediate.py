#!/usr/bin/env python3
"""Plot presentation figures for the render-gap 40/80 immediate policy."""

from __future__ import annotations

import argparse
import os
from bisect import bisect_right
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/render_gap_40_80_immediate_eval_original_only"
)
RUNS_ROOT = Path("/home/widen/Sync/webrtc-trace-runs")


def savefig(fig: plt.Figure, base: Path) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(base.with_suffix(".png"), dpi=180, bbox_inches="tight")
    fig.savefig(base.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def read_times(path: Path, columns: list[str]) -> np.ndarray:
    if not path.exists():
        return np.array([])
    df = pd.read_csv(path, low_memory=False)
    for col in columns:
        if col in df:
            vals = pd.to_numeric(df[col], errors="coerce").dropna().to_numpy(dtype=float)
            return vals[vals > 0]
    return np.array([])


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def plot_policy_comparison(out_dir: Path) -> None:
    final = pd.read_csv(out_dir / "final_policy_comparison_with_render_gap_40_80.csv")
    methods = [
        "render-gap only",
        "start gate + stable return",
        "continuous gate + next-render return",
        "render_gap_40_80_immediate",
        "reactive_oracle_latency31",
        "perfect_prefetch_oracle",
    ]
    df = final[final["method"].isin(methods)].copy()
    df["method"] = pd.Categorical(df["method"], methods, ordered=True)
    df = df.sort_values("method")
    labels = [
        "render-gap\nonly",
        "start gate\nstable return",
        "continuous\ngate",
        "40/80\nimmediate",
        "reactive\noracle",
        "perfect\noracle",
    ]
    coverage = df["target_coverage"].astype(float).to_numpy() * 100
    precision = pd.to_numeric(df["target_precision"], errors="coerce").fillna(0).to_numpy() * 100
    fp_hour = pd.to_numeric(df["target_fp_seconds_per_hour"], errors="coerce").fillna(0).to_numpy()

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6))
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2", "#9D755D"]
    for ax, values, title, ylabel in [
        (axes[0], coverage, "Target coverage", "%"),
        (axes[1], precision, "Target precision", "%"),
        (axes[2], fp_hour, "Target FP visible time", "seconds/hour"),
    ]:
        ax.bar(range(len(values)), values, color=colors)
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Temporary render-gap 40/80 immediate-return controller")
    savefig(fig, out_dir / "figures/policy_comparison_render_gap_40_80")


def plot_scatter_and_pareto(out_dir: Path) -> None:
    final = pd.read_csv(out_dir / "final_policy_comparison_with_render_gap_40_80.csv")
    latency = pd.read_csv(out_dir / "render_gap_40_80_latency_sensitivity.csv")
    figs = out_dir / "figures"

    fig, ax = plt.subplots(figsize=(7.8, 5.5))
    for _, row in final.iterrows():
        precision = pd.to_numeric(pd.Series([row["target_precision"]]), errors="coerce").fillna(1.0).iloc[0]
        ax.scatter(float(row["target_coverage"]) * 100, precision * 100, s=120, edgecolor="black")
        ax.annotate(row["method"], (float(row["target_coverage"]) * 100, precision * 100), xytext=(6, 4), textcoords="offset points", fontsize=8)
    ax.plot(latency["concealed_freeze_coverage"] * 100, latency["target_precision"] * 100, "o--", color="#E45756", label="40/80 latency sweep")
    for _, row in latency.iterrows():
        ax.annotate(f"L={int(row['generation_latency_ms'])}", (row["concealed_freeze_coverage"] * 100, row["target_precision"] * 100), fontsize=8, xytext=(4, -10), textcoords="offset points")
    ax.set_xlabel("Target coverage (%)")
    ax.set_ylabel("Target precision (%)")
    ax.set_title("Coverage vs precision")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    savefig(fig, figs / "coverage_precision_scatter_render_gap_40_80")

    fig, ax = plt.subplots(figsize=(7.8, 5.5))
    for _, row in final.iterrows():
        fp = pd.to_numeric(pd.Series([row["target_fp_seconds_per_hour"]]), errors="coerce").fillna(0).iloc[0]
        ax.scatter(fp, float(row["target_coverage"]) * 100, s=120, edgecolor="black")
        ax.annotate(row["method"], (fp, float(row["target_coverage"]) * 100), xytext=(6, 4), textcoords="offset points", fontsize=8)
    ax.plot(latency["visible_fp_seconds_per_hour"], latency["concealed_freeze_coverage"] * 100, "o--", color="#E45756", label="40/80 latency sweep")
    for _, row in latency.iterrows():
        ax.annotate(f"L={int(row['generation_latency_ms'])}", (row["visible_fp_seconds_per_hour"], row["concealed_freeze_coverage"] * 100), fontsize=8, xytext=(4, -10), textcoords="offset points")
    ax.set_xlabel("Target FP visible time (seconds/hour)")
    ax.set_ylabel("Target coverage (%)")
    ax.set_title("Coverage vs target false-positive visible time")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    savefig(fig, figs / "coverage_fp_pareto_render_gap_40_80")


def choose_cases(out_dir: Path) -> dict[str, Any]:
    freezes = pd.read_csv(out_dir / "render_gap_40_80_per_freeze.csv")
    segments = pd.read_csv(out_dir / "render_gap_40_80_segments.csv")
    target = freezes[freezes["is_target"] == 1].copy()
    good = target.sort_values(["concealed_freeze_ms", "concealed_ratio"], ascending=[False, False]).iloc[0]
    missed = target.sort_values(["concealed_ratio", "residual_freeze_ms"], ascending=[True, False]).iloc[0]

    def seg_target_overlap(seg: pd.Series) -> float:
        run_freezes = target[target["run"] == seg["run"]]
        return sum(overlap(seg["start_ms"], seg["end_ms"], f["freeze_start_ms"], f["freeze_end_ms"]) for _, f in run_freezes.iterrows())

    segments = segments.copy()
    segments["target_overlap_ms"] = segments.apply(seg_target_overlap, axis=1)
    fp_candidates = segments[segments["target_overlap_ms"] <= 0].copy()
    false_positive = fp_candidates.sort_values("duration_ms", ascending=False).iloc[0]
    return {"good": good, "missed": missed, "false_positive": false_positive}


def render_gap_series(render_times: np.ndarray, xs: np.ndarray) -> np.ndarray:
    out = np.zeros_like(xs)
    for i, t in enumerate(xs):
        idx = bisect_right(render_times, t) - 1
        out[i] = t - render_times[idx] if idx >= 0 else np.nan
    return out


def audio_ok_series(audio_times: np.ndarray, xs: np.ndarray, budget_ms: float = 80.0) -> np.ndarray:
    out = np.zeros_like(xs)
    for i, t in enumerate(xs):
        idx = bisect_right(audio_times, t) - 1
        out[i] = 1.0 if idx >= 0 and t - audio_times[idx] <= budget_ms else 0.0
    return out


def case_event_for_freeze(events: pd.DataFrame, freeze: pd.Series) -> pd.Series | None:
    run_events = events[events["run"] == freeze["run"]].copy()
    if run_events.empty:
        return None
    run_events["overlap"] = run_events.apply(
        lambda ev: overlap(ev["gap_start_ms"], ev["gap_end_ms"], freeze["freeze_start_ms"], freeze["freeze_end_ms"]),
        axis=1,
    )
    candidates = run_events[run_events["overlap"] > 0]
    if candidates.empty:
        return None
    return candidates.sort_values("overlap", ascending=False).iloc[0]


def plot_case(out_dir: Path, name: str, case: pd.Series, mode: str) -> None:
    events = pd.read_csv(out_dir / "render_gap_40_80_gap_events.csv")
    segments = pd.read_csv(out_dir / "render_gap_40_80_segments.csv")
    freezes = pd.read_csv(out_dir / "render_gap_40_80_per_freeze.csv")

    if mode == "false_positive":
        run = case["run"]
        center_start = float(case["start_ms"])
        center_end = float(case["end_ms"])
        event = events[(events["run"] == run) & (events["gap_index"] == int(case["gap_index"]))].iloc[0]
        freeze_rows = freezes[(freezes["run"] == run) & (freezes["freeze_end_ms"] >= center_start - 500) & (freezes["freeze_start_ms"] <= center_end + 500)]
    else:
        run = case["run"]
        center_start = float(case["freeze_start_ms"])
        center_end = float(case["freeze_end_ms"])
        event = case_event_for_freeze(events, case)
        freeze_rows = freezes[(freezes["run"] == run) & (freezes["freeze_id"] == int(case["freeze_id"]))]
    start = center_start - 500.0
    end = center_end + 500.0

    receiver = RUNS_ROOT / run / "receiver"
    render_times = read_times(receiver / "receiver_rendered_frames.csv", ["timestamp_ms"])
    audio_times = read_times(receiver / "audio_packet_inserts.csv", ["wall_time_ms", "timestamp_ms"])
    xs = np.arange(start, end + 1, 10.0)
    gap = render_gap_series(render_times, xs)
    audio_ok = audio_ok_series(audio_times, xs) if len(audio_times) else np.full_like(xs, np.nan)
    segs = segments[(segments["run"] == run) & (segments["end_ms"] >= start) & (segments["start_ms"] <= end)]
    render_window = render_times[(render_times >= start) & (render_times <= end)]

    fig, axes = plt.subplots(5, 1, figsize=(10.5, 7.5), sharex=True, gridspec_kw={"height_ratios": [0.65, 0.55, 1.1, 0.55, 1.1]})
    t0 = start
    for _, fr in freeze_rows.iterrows():
        if int(fr.get("is_target", 0)) == 1:
            color = "#D62728"
        else:
            color = "#999999"
        for ax in axes:
            ax.axvspan(max(fr["freeze_start_ms"], start) - t0, min(fr["freeze_end_ms"], end) - t0, color=color, alpha=0.16)

    axes[0].eventplot(render_window - t0, lineoffsets=0.5, linelengths=0.8, colors="#2F5597")
    axes[0].set_yticks([])
    axes[0].set_ylabel("real\nrender", rotation=0, labelpad=24, va="center")

    for _, seg in segs.iterrows():
        axes[1].axvspan(max(seg["start_ms"], start) - t0, min(seg["end_ms"], end) - t0, color="#2CA02C", alpha=0.65)
    axes[1].set_yticks([])
    axes[1].set_ylabel("generated\nvisible", rotation=0, labelpad=30, va="center")

    axes[2].plot(xs - t0, gap, color="#D55E00", linewidth=1.5)
    axes[2].axhline(40, color="#555555", linestyle="--", linewidth=1, label="trigger 40ms")
    axes[2].axhline(80, color="#111111", linestyle=":", linewidth=1, label="switch 80ms")
    axes[2].set_ylabel("render gap\n(ms)", rotation=0, labelpad=34, va="center")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(fontsize=8, loc="upper right")

    if event is not None:
        markers = [
            ("trigger", event.get("generation_trigger_time_ms"), "#1F77B4"),
            ("ready", event.get("generated_ready_time_ms"), "#9467BD"),
            ("switch", event.get("display_switch_time_ms"), "#2CA02C"),
            ("return", event.get("return_time_ms"), "#111111"),
        ]
        for label, value, color in markers:
            try:
                val = float(value)
            except Exception:
                continue
            if start <= val <= end:
                for ax in axes:
                    ax.axvline(val - t0, color=color, linewidth=1.2, alpha=0.8)
                axes[3].text(val - t0, 0.6, label, rotation=90, color=color, fontsize=8, ha="center", va="center")
    axes[3].set_yticks([])
    axes[3].set_ylabel("events", rotation=0, labelpad=23, va="center")

    axes[4].step(xs - t0, audio_ok, where="post", color="#9467BD", linewidth=1.3)
    axes[4].set_ylim(-0.1, 1.1)
    axes[4].set_ylabel("audio\nB80", rotation=0, labelpad=24, va="center")
    axes[4].set_xlabel("time from window start (ms)")
    axes[4].grid(True, alpha=0.25)
    fig.suptitle(name.replace("_", " "))
    savefig(fig, out_dir / f"figures/{name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    plot_policy_comparison(args.out_dir)
    plot_scatter_and_pareto(args.out_dir)
    cases = choose_cases(args.out_dir)
    plot_case(args.out_dir, "case_good_render_gap_40_80", cases["good"], "freeze")
    plot_case(args.out_dir, "case_false_positive_render_gap_40_80", cases["false_positive"], "false_positive")
    plot_case(args.out_dir, "case_missed_freeze_render_gap_40_80", cases["missed"], "freeze")
    print(f"wrote figures to {args.out_dir / 'figures'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
