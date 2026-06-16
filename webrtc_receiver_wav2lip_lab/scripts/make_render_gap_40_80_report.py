#!/usr/bin/env python3
"""Create markdown report for render-gap 40/80 immediate-return evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_OUT_DIR = Path(
    "/home/widen/webrtc-checkout/analysis_runs/render_gap_40_80_immediate_eval_original_only"
)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def sec_ms(x: float) -> str:
    return f"{x / 1000:.2f}s"


def row_first(df: pd.DataFrame, key: str, value: object) -> pd.Series:
    return df[df[key] == value].iloc[0]


def md_table(df: pd.DataFrame, cols: list[str], rename: dict[str, str] | None = None) -> str:
    rename = rename or {}
    lines = []
    headers = [rename.get(c, c) for c in cols]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in cols) + "|")
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            value = row[col]
            if isinstance(value, float):
                vals.append(f"{value:.3f}")
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def select_cases(out_dir: Path) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series | None]:
    freezes = pd.read_csv(out_dir / "render_gap_40_80_per_freeze.csv")
    segments = pd.read_csv(out_dir / "render_gap_40_80_segments.csv")
    target = freezes[freezes["is_target"] == 1].copy()
    good = target.sort_values(["concealed_freeze_ms", "concealed_ratio"], ascending=[False, False]).iloc[0]
    missed = target.sort_values(["concealed_ratio", "residual_freeze_ms"], ascending=[True, False]).iloc[0]
    fp = segments.sort_values("duration_ms", ascending=False).iloc[0]
    oscillation = segments[segments["duration_ms"] < 100].sort_values("duration_ms", ascending=False)
    osc = oscillation.iloc[0] if not oscillation.empty else None
    return good, fp, missed, osc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    out = args.out_dir

    overall = pd.read_csv(out / "render_gap_40_80_overall_summary.csv")
    target = pd.read_csv(out / "render_gap_40_80_target_summary.csv")
    latency = pd.read_csv(out / "render_gap_40_80_latency_sensitivity.csv")
    thresh = pd.read_csv(out / "render_gap_40_80_threshold_sensitivity.csv")
    final = pd.read_csv(out / "final_policy_comparison_with_render_gap_40_80.csv")
    oracle = pd.read_csv(out / "oracle_comparison_render_gap_40_80.csv")
    inclusion = pd.read_csv(out / "render_gap_40_80_run_inclusion.csv")

    o = overall[overall["scope"] == "all_freezes"].iloc[0]
    t = target[target["scope"] == "target_freezes"].iloc[0]
    timing = target[target["freezes"].notna()].iloc[0]
    temp = row_first(final, "method", "render_gap_40_80_immediate")
    continuous = row_first(final, "method", "continuous gate + next-render return")
    render_gap = row_first(final, "method", "render-gap only")
    start_gate = row_first(final, "method", "start gate + stable return")
    reactive = row_first(final, "method", "reactive_oracle_latency31")
    perfect = row_first(final, "method", "perfect_prefetch_oracle")
    good, fp, missed, osc = select_cases(out)

    latency_view = latency[
        [
            "generation_latency_ms",
            "concealed_freeze_coverage",
            "target_precision",
            "visible_fp_seconds_per_hour",
            "residual_freeze_time_ms",
            "any_concealment_event_ratio",
            "fully_concealed_event_ratio",
        ]
    ].copy()
    latency_view["concealed_freeze_coverage"] *= 100
    latency_view["target_precision"] *= 100
    latency_view["any_concealment_event_ratio"] *= 100
    latency_view["fully_concealed_event_ratio"] *= 100

    threshold_view = thresh[
        [
            "generation_trigger_ms",
            "display_switch_ms",
            "concealed_freeze_coverage",
            "target_precision",
            "visible_fp_seconds_per_hour",
        ]
    ].copy()
    threshold_view["concealed_freeze_coverage"] *= 100
    threshold_view["target_precision"] *= 100

    report = f"""# Render-Gap 40/80 Immediate-Return Evaluation

## 1. Policy description

이번 평가는 임시 presentation/test controller만 평가한다. Score-based switch, playout headroom, packet starvation, playable-frame starvation은 사용하지 않았다.

정책은 단순하다.

```text
If no new real frame appears for 40ms:
    start generation

If no new real frame appears for 80ms
AND generated frame is ready:
    display generated video

As soon as the next real rendered frame appears:
    immediately return to real video
```

중요한 해석은 `generation trigger != display switch`다. 40ms trigger는 generated frame을 준비하는 단계이고, 80ms switch가 실제 화면 전환 여부를 결정한다.
이 정책은 최종 정책이 아니다. 최종 trigger/switch policy는 이후 score-based controller로 교체할 예정이다.

## 2. Runtime knob check

현재 native smoke에서 같은 정책을 만들려면 다음 knob이 필요하다.

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

Native overlay의 switch threshold는 `max(switch_gap_ms, switch_min_gap_ms, estimated_render_interval_ms)` 형태다. 따라서 `SWITCH_GAP_MS=80`과 `SWITCH_SLACK_MS=0`을 함께 두는 것이 80ms switch를 가장 명시적으로 만든다.

## 3. Dataset

- Original target-label runs in scope: {int(t['runs_in_scope'])}
- Freeze events: {int(o['freeze_events'])}
- Total freeze duration: {sec_ms(float(o['freeze_duration_ms']))}
- Target events: {int(t['freeze_events'])}
- Target duration: {sec_ms(float(t['freeze_duration_ms']))}
- Observed rendered-frame span: {float(t['observed_span_ms']) / 60000:.2f} min
- Ground truth freeze: `video_freeze.csv` receiver-side render gap
- Target definition: `usable_ratio_B80 >= 0.8 AND no_fresh_rendered_frame_during_freeze AND max(video_recv_gap, frame_completion_gap) >= 80ms`

## 4. Main results

Temporary 40/80 immediate policy:

- all-freeze coverage: {pct(float(o['concealed_freeze_coverage']))}
- all-freeze residual user-visible freeze: {sec_ms(float(o['residual_freeze_time_ms']))}
- target coverage: {pct(float(t['concealed_freeze_coverage']))}
- target precision: {pct(float(t['target_precision']))}
- target FP visible time: {float(t['visible_fp_seconds_per_hour']):.2f}s/hour
- target any concealment event ratio: {pct(float(t['any_concealment_event_ratio']))}
- target fully concealed event ratio: {pct(float(t['fully_concealed_event_ratio']))}
- residual target freeze: {sec_ms(float(t['residual_freeze_time_ms']))}
- generated visible segments: {int(t['generated_visible_segments'])}
- oscillation count, segment <100ms: {int(t['oscillation_count'])}

Comparison:

| Policy | Target coverage | Target precision | Target FP |
|---|---:|---:|---:|
| render-gap only | {pct(float(render_gap['target_coverage']))} | {pct(float(render_gap['target_precision']))} | {float(render_gap['target_fp_seconds_per_hour']):.2f}s/hour |
| start gate + stable return | {pct(float(start_gate['target_coverage']))} | {pct(float(start_gate['target_precision']))} | {float(start_gate['target_fp_seconds_per_hour']):.2f}s/hour |
| continuous gate + next-render return | {pct(float(continuous['target_coverage']))} | {pct(float(continuous['target_precision']))} | {float(continuous['target_fp_seconds_per_hour']):.2f}s/hour |
| render_gap_40_80_immediate | {pct(float(temp['target_coverage']))} | {pct(float(temp['target_precision']))} | {float(temp['target_fp_seconds_per_hour']):.2f}s/hour |
| reactive oracle latency31 | {pct(float(reactive['target_coverage']))} | {pct(float(reactive['target_precision']))} | 0.00s/hour |
| perfect prefetch oracle | {pct(float(perfect['target_coverage']))} | {pct(float(perfect['target_precision']))} | 0.00s/hour |

## 5. Timing behavior

- target freezes triggered after freeze start: {pct(float(timing['triggered_after_freeze_start_ratio']))}
- target freezes triggered before freeze start: {pct(float(timing['triggered_before_freeze_start_ratio']))}
- generated ready before switch threshold: {pct(float(timing['ready_before_switch_threshold_ratio']))}
- trigger delay from freeze start p50/p90: {timing['trigger_delay_from_freeze_start_ms_p50']:.1f}ms / {timing['trigger_delay_from_freeze_start_ms_p90']:.1f}ms
- switch delay from freeze start p50/p90: {timing['switch_delay_from_freeze_start_ms_p50']:.1f}ms / {timing['switch_delay_from_freeze_start_ms_p90']:.1f}ms

With 31ms generation latency, the 40ms lead time is enough for the generated tail to be ready by the 80ms switch point in this offline simulation.

## 6. Latency sensitivity

{md_table(latency_view, ['generation_latency_ms', 'concealed_freeze_coverage', 'target_precision', 'visible_fp_seconds_per_hour', 'residual_freeze_time_ms', 'any_concealment_event_ratio', 'fully_concealed_event_ratio'], {'generation_latency_ms': 'L_gen ms', 'concealed_freeze_coverage': 'coverage %', 'target_precision': 'precision %', 'visible_fp_seconds_per_hour': 'FP s/hour', 'residual_freeze_time_ms': 'residual ms', 'any_concealment_event_ratio': 'any %', 'fully_concealed_event_ratio': 'fully %'})}

## 7. Threshold sanity sensitivity

{md_table(threshold_view, ['generation_trigger_ms', 'display_switch_ms', 'concealed_freeze_coverage', 'target_precision', 'visible_fp_seconds_per_hour'], {'generation_trigger_ms': 'trigger ms', 'display_switch_ms': 'switch ms', 'concealed_freeze_coverage': 'coverage %', 'target_precision': 'precision %', 'visible_fp_seconds_per_hour': 'FP s/hour'})}

## 8. Interpretation

This temporary policy is simple and easy to explain. It tests whether render-gap-only control can provide visible concealment without packet/frame-buffer signals.

The result is expected: coverage is high because the policy switches on many render gaps, but target precision is low because it also shows generated video outside the target denominator. Immediate return reduces post-recovery generated display, but it creates many short generated segments and can oscillate when real rendering briefly recovers and stalls again.

Compared with continuous gate, 40/80 immediate gives much higher target coverage ({pct(float(temp['target_coverage']))} vs {pct(float(continuous['target_coverage']))}) but much lower target precision ({pct(float(temp['target_precision']))} vs {pct(float(continuous['target_precision']))}) and higher target FP ({float(temp['target_fp_seconds_per_hour']):.2f}s/hour vs {float(continuous['target_fp_seconds_per_hour']):.2f}s/hour).

## 9. Failure analysis

Figures:

- Good case: `figures/case_good_render_gap_40_80.png`
- False positive case: `figures/case_false_positive_render_gap_40_80.png`
- Mostly missed target freeze: `figures/case_missed_freeze_render_gap_40_80.png`

Good case:

- run: `{good['run']}`, freeze_id: {int(good['freeze_id'])}
- freeze duration: {good['freeze_duration_ms']:.1f}ms
- concealed: {good['concealed_freeze_ms']:.1f}ms
- trigger delay: {good['trigger_delay_from_freeze_start_ms']}ms
- switch delay: {good['switch_delay_from_freeze_start_ms']}ms

False positive case:

- run: `{fp['run']}`, segment duration: {fp['duration_ms']:.1f}ms
- interpretation: generated video was shown outside target freeze, so it is counted as target FP. This may be a non-target visual stall or a true false positive depending on the shaded interval in the figure.

Mostly missed case:

- run: `{missed['run']}`, freeze_id: {int(missed['freeze_id'])}
- freeze duration: {missed['freeze_duration_ms']:.1f}ms
- concealed: {missed['concealed_freeze_ms']:.1f}ms
- residual: {missed['residual_freeze_ms']:.1f}ms
- reason: the policy cannot hide the first ~80-100ms because it waits for render gap threshold and generation readiness.
"""

    if osc is not None:
        report += f"""
Oscillation example:

- run: `{osc['run']}`, segment duration: {osc['duration_ms']:.1f}ms
- generated segment shorter than 100ms, counted as an oscillation-like immediate-return event.
"""

    report += """
## 10. Slide-ready takeaway

- A simple 40/80ms render-gap policy is easy to explain and runs without packet/frame-buffer signals.
- It can hide a large portion of longer render freezes when generation latency is below the 40ms lead time.
- Its limitations are delayed response, false positives on non-target render stalls, and possible oscillation under immediate return.
"""

    (out / "render_gap_40_80_immediate_report.md").write_text(report, encoding="utf-8")
    print(f"wrote {out / 'render_gap_40_80_immediate_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
