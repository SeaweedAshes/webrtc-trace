#!/usr/bin/env python3
"""Create presentation-focused render-gap 40/80 evaluation outputs.

This keeps the full evaluation files intact and writes reduced comparison tables
and figures that only include the policy under discussion plus essential
reference points.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


BASE_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/"
    "render_gap_40_80_immediate_eval_original_only"
)
FIG_DIR = BASE_DIR / "figures"

KEEP_METHODS = [
    "render-gap only",
    "continuous gate + next-render return",
    "render_gap_40_80_immediate",
    "reactive_oracle_latency31",
]

LABELS = {
    "render-gap only": "Render-gap only",
    "continuous gate + next-render return": "Continuous gate",
    "render_gap_40_80_immediate": "Proposed 40/80 immediate",
    "reactive_oracle_latency31": "Reactive oracle",
}

COLORS = {
    "render-gap only": "#c76e53",
    "continuous gate + next-render return": "#4f81bd",
    "render_gap_40_80_immediate": "#1f9e89",
    "reactive_oracle_latency31": "#6a5acd",
}


def load_subset() -> pd.DataFrame:
    path = BASE_DIR / "final_policy_comparison_with_render_gap_40_80.csv"
    df = pd.read_csv(path)
    subset = df[df["method"].isin(KEEP_METHODS)].copy()
    subset["method_label"] = subset["method"].map(LABELS)
    subset["target_coverage_pct"] = subset["target_coverage"] * 100.0
    subset["target_precision_pct"] = subset["target_precision"] * 100.0
    subset.to_csv(
        BASE_DIR / "presentation_policy_comparison_render_gap_40_80.csv",
        index=False,
    )
    return subset


def savefig(name: str) -> None:
    for suffix in ("png", "pdf"):
        plt.savefig(FIG_DIR / f"{name}.{suffix}", bbox_inches="tight", dpi=200)
    plt.close()


def plot_bar(df: pd.DataFrame) -> None:
    metrics = [
        ("target_coverage_pct", "Target coverage (%)"),
        ("target_precision_pct", "Target precision (%)"),
        ("target_fp_seconds_per_hour", "Target FP (s/hour)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 3.8))
    for ax, (col, title) in zip(axes, metrics, strict=True):
        colors = [COLORS[m] for m in df["method"]]
        ax.bar(df["method_label"], df[col], color=colors)
        ax.set_title(title)
        ax.tick_params(axis="x", labelrotation=25)
        ax.grid(axis="y", alpha=0.25)
        if col.endswith("_pct"):
            ax.set_ylim(0, 105)
    fig.suptitle("Essential Policy Comparison", y=1.05, fontsize=13)
    savefig("presentation_policy_comparison_render_gap_40_80")


def plot_precision_coverage(df: pd.DataFrame) -> None:
    plt.figure(figsize=(6.4, 4.6))
    for _, row in df.iterrows():
        method = row["method"]
        plt.scatter(
            row["target_coverage_pct"],
            row["target_precision_pct"],
            s=90,
            color=COLORS[method],
            label=LABELS[method],
        )
        plt.text(
            row["target_coverage_pct"] + 1.0,
            row["target_precision_pct"] + 1.0,
            LABELS[method],
            fontsize=8,
        )
    plt.xlabel("Target coverage (%)")
    plt.ylabel("Target precision (%)")
    plt.xlim(0, 100)
    plt.ylim(0, 105)
    plt.grid(alpha=0.25)
    plt.title("Precision vs. Coverage")
    savefig("presentation_coverage_precision_render_gap_40_80")


def plot_fp_pareto(df: pd.DataFrame) -> None:
    plt.figure(figsize=(6.4, 4.6))
    for _, row in df.iterrows():
        method = row["method"]
        plt.scatter(
            row["target_fp_seconds_per_hour"],
            row["target_coverage_pct"],
            s=90,
            color=COLORS[method],
            label=LABELS[method],
        )
        plt.text(
            row["target_fp_seconds_per_hour"] + 0.4,
            row["target_coverage_pct"] + 1.0,
            LABELS[method],
            fontsize=8,
        )
    plt.xlabel("Target FP visible time (s/hour)")
    plt.ylabel("Target coverage (%)")
    plt.xlim(left=0)
    plt.ylim(0, 105)
    plt.grid(alpha=0.25)
    plt.title("Coverage vs. False Positive Cost")
    savefig("presentation_coverage_fp_pareto_render_gap_40_80")


def write_report(df: pd.DataFrame) -> None:
    lines = [
        "# Render-gap 40/80 Immediate: Presentation Subset",
        "",
        "이 파일은 발표용 비교군만 남긴 축약 결과입니다. 전체 재현용 CSV와 그림은 원본 파일에 그대로 유지했습니다.",
        "",
        "남긴 비교군:",
        "- `render-gap only`: render gap만으로 켜는 공격적 기준입니다.",
        "- `continuous gate + next-render return`: FP를 줄이는 보수적 기준입니다.",
        "- `render_gap_40_80_immediate`: 현재 발표용 임시 제안안입니다.",
        "- `reactive_oracle_latency31`: generation latency 31ms를 가정한 현실적 상한입니다.",
        "",
        "제거한 비교군:",
        "- `hold-last-frame`: concealment가 없어서 수치 비교 그래프에서 정보량이 낮습니다.",
        "- `start gate + stable return`: continuous gate와 proposed 사이의 중간 기준이라 발표 핵심성이 낮습니다.",
        "- `perfect_prefetch_oracle`: 완전 prefetch 상한이라 현재 임시 controller 설명에는 과하게 이상적입니다.",
        "",
        "## 축약 비교표",
        "",
        "| method | target coverage | target precision | target FP s/hour |",
        "|---|---:|---:|---:|",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['method_label']} | "
            f"{row['target_coverage_pct']:.1f}% | "
            f"{row['target_precision_pct']:.1f}% | "
            f"{row['target_fp_seconds_per_hour']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 발표용 해석",
            "",
            "`render_gap_40_80_immediate`는 `render-gap only`보다 FP를 크게 줄이면서도 target coverage를 높게 유지합니다. "
            "다만 `continuous gate`보다 precision은 낮으므로, 이 정책은 최종 정책이 아니라 단순하고 설명 가능한 임시 prototype controller로 제시하는 것이 안전합니다.",
        ]
    )
    (BASE_DIR / "presentation_render_gap_40_80_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    df = load_subset()
    plot_bar(df)
    plot_precision_coverage(df)
    plot_fp_pareto(df)
    write_report(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
