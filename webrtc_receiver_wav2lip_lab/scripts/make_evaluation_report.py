#!/usr/bin/env python3
"""Generate a slide-oriented markdown report for switch-score evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/switch_score_policy_eval_original_only"
)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def sec(ms: float) -> str:
    return f"{ms / 1000:.2f}s"


def get_metric(path: Path, key: str) -> float:
    df = pd.read_csv(path)
    row = df[df["metric"] == key]
    if row.empty:
        return 0.0
    return float(row.iloc[0]["value"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    out = args.out_dir

    label_runs = int(get_metric(out / "switch_decision_label_summary.csv", "runs"))
    samples = int(get_metric(out / "switch_decision_label_summary.csv", "samples"))
    target_freezes = int(get_metric(out / "switch_decision_label_summary.csv", "target_freezes"))
    target_duration_ms = get_metric(out / "switch_decision_label_summary.csv", "target_duration_ms")
    observed_span_ms = get_metric(out / "switch_decision_label_summary.csv", "observed_span_ms")

    target_den = pd.read_csv(out / "target_denominator_baseline_reproduced.csv")
    target_fp = pd.read_csv(out / "target_fp_baseline_reproduced.csv")
    final = pd.read_csv(out / "final_policy_comparison.csv")
    best = pd.read_csv(out / "switch_score_policy_best_points.csv")
    oracle = pd.read_csv(out / "oracle_comparison.csv")
    cv = pd.read_csv(out / "switch_score_cv_summary.csv")

    best_candidates = best[best["meets_minimum"] == True]
    if best_candidates.empty:
        best_row = best.sort_values("coverage_precision_f1", ascending=False).iloc[0]
        best_note = "continuous gate 대비 coverage/precision 동시 개선 조건을 만족하지 못해 F1 기준 best point를 선택했다."
    else:
        best_row = best_candidates.sort_values("coverage_precision_f1", ascending=False).iloc[0]
        best_note = "continuous gate 대비 coverage와 precision을 모두 넘는 최소 조건을 만족한 point다."

    continuous = final[final["method"] == "continuous gate + next-render return"].iloc[0]
    render_gap = final[final["method"] == "render-gap only"].iloc[0]
    headroom = final[final["method"] == "headroom prefetch h0"].iloc[0]
    best_final = final[final["policy_id"] == best_row["policy_id"]].iloc[0]
    reactive = oracle[oracle["oracle"] == "reactive_oracle_latency31"].iloc[0]
    perfect = oracle[oracle["oracle"] == "perfect_prefetch_oracle"].iloc[0]

    reactive_fraction = float(best_row["target_concealed_ms"]) / float(reactive["target_concealed_ms"])
    perfect_fraction = float(best_row["target_concealed_ms"]) / float(perfect["target_concealed_ms"])

    cv_clean = cv[cv["model"] == "manual_logistic"].copy()
    cv_summary = ""
    if not cv_clean.empty:
        c05 = cv_clean[cv_clean["threshold"] == 0.5]
        if not c05.empty:
            cv_summary = (
                f"Run-level CV sample 기준 threshold 0.5에서 평균 precision "
                f"{c05['sample_precision'].mean():.3f}, 평균 recall {c05['sample_recall'].mean():.3f}다. "
                "이는 sample classification 수치이며, 최종 평가는 visible-time overlap으로 따로 계산했다."
            )

    report = f"""# SwitchScore Display Policy Evaluation

## 1. Evaluation goal

이번 평가는 freeze concealment 시스템에서 **visible display switch**가 병목인지 확인하기 위한 것이다.
generation trigger/prefetch는 공격적으로 켤 수 있지만, 실제 화면을 generated video로 바꾸는 display switch는 false positive를 줄이기 위해 보수적이어야 한다.

SwitchScore는 "지금 generated video를 보여주는 것이 held real frame을 계속 보여주는 것보다 나은가"를 추정한다.
따라서 이 score는 network/root-cause classifier가 아니며, sender departure gap이나 network delay를 분류하기 위한 score가 아니다.

## 2. Dataset and target definition

- 기존 baseline 재현은 original collection logs 기반 기존 summary를 그대로 사용했다.
- 기존 전체 original baseline: rendered-frame log가 있는 run 37개, freeze event 195개, freeze time 79.603s.
- 새 score-decision dataset은 현재 로컬에서 필요한 display-decision feature를 만들 수 있는 original run {label_runs}개로 구성했다.
- decision samples: {samples:,}개.
- target freeze: {target_freezes}개, target duration {sec(target_duration_ms)}.
- observed rendered-frame span in score dataset: {observed_span_ms / 60000:.2f} min.

Target freeze 정의는 기존 정의를 유지했다.

```text
usable_ratio_B80 >= 0.8
AND no_fresh_rendered_frame_during_freeze
AND max(video_recv_gap, frame_completion_gap) >= 80ms
```

여기서 audio가 perfect하다는 뜻은 아니다. B=80ms jitter-buffer/PLC budget 아래에서 generation input으로 사용할 수 있는 상태를 의미한다.
Freeze는 RTP gap이 아니라 `video_freeze.csv`의 receiver-side render gap으로 해석한다.

## 3. Baseline trade-off

| Policy | Target coverage | Target precision | Target FP |
|---|---:|---:|---:|
| render-gap only | {pct(float(render_gap['target_coverage']))} | {pct(float(render_gap['target_precision']))} | {float(render_gap['target_fp_seconds_per_hour']):.2f}s/hour |
| continuous gate + next-render return | {pct(float(continuous['target_coverage']))} | {pct(float(continuous['target_precision']))} | {float(continuous['target_fp_seconds_per_hour']):.2f}s/hour |
| headroom prefetch h0 | {pct(float(headroom['target_coverage']))} | {pct(float(headroom['target_precision']))} | subset only |

해석은 명확하다. render-gap only는 많은 target freeze를 덮지만 generated-video FP가 너무 크다.
continuous gate는 precision/FP가 가장 좋지만 target freeze를 많이 놓친다.
headroom prefetch는 coverage를 올릴 수 있지만 단독 display switch로 쓰기에는 너무 넓다.

## 4. SwitchScore definition

Rule score는 다음 normalized component를 결합했다.

- render gap score
- video packet age score
- playable frame age score
- low playout headroom score
- no future renderable score
- frame completion gap score
- retransmission/missing score
- no fresh real frame score

Manual logistic score는 같은 display-side signal을 사용하되, run-level split을 지켜 학습했다.
scikit-learn/xgboost/lightgbm이 현재 환경에 없어서 logistic은 numpy로 직접 학습했고, gradient boosted tree는 skip했다.
{cv_summary}

## 5. Best policy result

Best score-based display policy:

```text
policy_id = {best_row['policy_id']}
score = {best_row['score_name']}
policy = {best_row['policy']}
theta_on = {best_row['theta_on']}
theta_off = {best_row['theta_off']}
```

결과:

- target coverage: {pct(float(best_row['target_coverage']))}
- target precision: {pct(float(best_row['target_precision']))}
- target FP: {float(best_row['target_fp_seconds_per_hour']):.2f}s/hour
- any concealment event ratio: {pct(float(best_row['any_concealment_event_ratio']))}
- fully concealed event ratio: {pct(float(best_row['fully_concealed_event_ratio']))}
- residual target freeze time: {sec(float(best_row['residual_target_freeze_ms']))}
- reactive oracle 대비 concealment fraction: {pct(reactive_fraction)}
- perfect prefetch oracle 대비 concealment fraction: {pct(perfect_fraction)}

{best_note}

중요한 점은 score-based switch가 continuous gate보다 coverage와 precision을 동시에 조금 개선했지만, FP seconds/hour는 continuous gate보다 증가했다는 것이다.
따라서 현재 결과는 "promising but not strong"이다. Strong target인 coverage >= 65%, precision >= 65%에는 아직 도달하지 못했다.

## 6. Failure analysis

대표 그림:

- Good score switch: `figures/case_good_switch.png`
- False positive: `figures/case_false_positive.png`
- Missed/partial-missed target freeze: `figures/case_missed_freeze.png`

False positive의 주된 이유는 score가 "fresh real frame이 곧 복구될 가능성"을 충분히 강하게 보지 못하는 구간이 있기 때문이다.
Missed/partial-missed freeze의 주된 이유는 conservative gate와 31ms generation latency 때문에 freeze 시작부를 완전히 덮지 못하는 것이다.

다음 개선에 필요한 signal은 다음이다.

- explicit audio_age and audio condition confidence
- time_since_last_video_packet
- time_since_last_playable_frame
- fresh real frame availability
- frame buffer/playout state
- future renderable units

## 7. Figures

- `figures/switch_score_pareto.png`
- `figures/precision_coverage.png`
- `figures/policy_comparison_bar.png`
- `figures/case_good_switch.png`
- `figures/case_false_positive.png`
- `figures/case_missed_freeze.png`

## 8. Slide-ready takeaway

- Rule-based display switch는 coverage와 false-positive 사이의 trade-off가 크다.
- SwitchScore는 여러 video-side receiver signal을 하나의 display confidence로 결합한다.
- 현재 score-based switch는 continuous gate보다 coverage/precision을 동시에 소폭 개선하지만, FP cost가 증가하므로 추가 signal과 threshold tuning이 필요하다.
"""

    (out / "evaluation_report_for_presentation.md").write_text(report, encoding="utf-8")
    print(f"wrote {out / 'evaluation_report_for_presentation.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
