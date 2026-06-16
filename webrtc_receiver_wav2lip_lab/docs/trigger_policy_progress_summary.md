# Trigger Policy Progress Summary

This document summarizes the current trigger/switch/return design for
audio-driven talking-face concealment in the WebRTC + Wav2Lip prototype.

The goal is not to prove the root cause of every WebRTC freeze.  The goal is to
detect receiver-side opportunities where generated talking-face video can hide a
user-visible video freeze while audio remains usable.

## 1. Core Design Split

The current design separates three decisions:

```text
generation trigger:
  Start Wav2Lip generation early enough that generated frames are ready.

display switch:
  Actually show generated video on the receiver surface.

return policy:
  Decide when to stop generated video and return to the original WebRTC stream.
```

This split is important.  Generation should be relatively aggressive because
late generation means the user has already seen part of the freeze.  Display
switch should be conservative because switching too often creates false
positive generated-video playback.  Return policy controls the tradeoff between
fast recovery and visual stability.

## 2. Current Runtime Policy

The current native receiver implementation primarily uses render-gap based
prefetch plus a conservative display switch.

### 2.1 Generation Start

Default generation starts when the remote rendered-frame gap reaches the risk
threshold:

```text
generation starts if:
  render_gap_ms >= WEBRTC_WAV2LIP_GENERATION_RISK_MS
```

Default:

```bash
WEBRTC_WAV2LIP_GENERATION_RISK_MS=60
```

This is earlier than the old `180ms` freeze-style trigger, but it is still
renderer-local.  It does not yet fully use packet/frame-buffer state.

### 2.2 Display Switch

Generated video is displayed only after the expected render deadline is missed.

```text
switch to generated video if:
  generated frame is ready
  AND render_gap_ms >= max(expected_render_interval + switch_slack_ms,
                           switch_min_gap_ms)
```

Defaults:

```bash
WEBRTC_WAV2LIP_SWITCH_MIN_GAP_MS=80
WEBRTC_WAV2LIP_SWITCH_SLACK_MS=40
```

This means generation can begin at `60ms`, but generated video is not shown
until the real stream has missed the display deadline by a larger margin.

### 2.3 Return Policy

Return is a selectable policy, not an OR of two simultaneous conditions.

Option A: immediate return

```text
return if:
  first real rendered frame arrives
```

Enabled by:

```bash
WEBRTC_WAV2LIP_RETURN_IMMEDIATE=1
```

Option B: stable return

```text
return if:
  consecutive stable real frames >= N
  AND stable real-frame duration >= T
  AND real-frame gaps <= stable_gap_ms
```

Default:

```bash
WEBRTC_WAV2LIP_RETURN_IMMEDIATE=0
WEBRTC_WAV2LIP_RETURN_CONSECUTIVE_REAL_FRAMES=3
WEBRTC_WAV2LIP_RETURN_STABLE_MS=120
WEBRTC_WAV2LIP_RETURN_STABLE_GAP_MS=80
```

The default implementation uses stable return.  Immediate return is useful as
an experimental low-latency option, but it can cause visual oscillation if the
real stream briefly recovers and then stalls again.

## 3. Proposed Target Policy

The policy we are converging toward is:

```text
generation prefetch if:
  audio_age <= 80ms
  AND (
    playout_headroom_ms < H
    OR time_since_last_playable_frame > 80ms
    OR time_since_last_video_packet > 100ms
  )

switch to generated video if:
  no fresh real rendered frame
  AND generated frame is ready
  AND audio still usable
  AND video/frame starvation still holds

return to real video if selected return policy is satisfied:
  Option A: first real rendered frame arrives
  Option B: 2-3 consecutive stable real frames arrive
```

Key point:

```text
generation trigger != display switch
```

The trigger prepares generated frames.  The switch decides whether the user
should actually see them.

## 4. Implemented Pieces

### 4.1 Render-Gap Prefetch

Implemented in:

```text
/home/widen/webrtc-wav2lip-native/src/examples/peerconnection/client/linux/main_wnd.cc
/home/widen/webrtc-wav2lip-native/src/examples/peerconnection/client/linux/main_wnd.h
```

Staging overlay source:

```text
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/scripts/apply_native_overlay.py
```

Behavior:

```text
20ms monitor checks remote render cadence.
Generation starts at render-risk gap.
Display switch waits for missed render deadline.
Return uses stable real-frame recovery by default.
```

Build status:

```text
autoninja -C /home/widen/webrtc-wav2lip-native/src/out/Wav2Lip peerconnection_client
passed
```

The remaining clang warning is the existing local GCC install-dir warning, not
a trigger-policy build failure.

### 4.2 Playout Headroom Prefetch

Implemented as a process-local signal from the WebRTC video pipeline to the GTK
renderer.

New files:

```text
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/native_overlay/examples/peerconnection/client/playout_headroom_signal.h
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/native_overlay/examples/peerconnection/client/playout_headroom_signal.cc
```

Apply script:

```text
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/scripts/apply_playout_headroom_signal.py
```

Patched runtime files:

```text
/home/widen/webrtc-wav2lip-native/src/video/video_stream_buffer_controller.cc
/home/widen/webrtc-wav2lip-native/src/examples/peerconnection/client/linux/main_wnd.cc
/home/widen/webrtc-wav2lip-native/src/examples/BUILD.gn
```

Runtime knobs:

```bash
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_PREFETCH_MS=20
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_STALE_MS=100
```

Important: this is generation prefetch only.  It should not directly switch the
visible renderer to generated video.

## 5. Offline Evaluation Results

All phenomenon claims use original collection logs only.  Replay logs are kept
separate.

Ground truth freeze:

```text
receiver-side video_freeze.csv
freeze_start = timestamp_ms - freeze_duration_ms
freeze_end = timestamp_ms
```

### 5.1 Overall Original Logs

Scope:

```text
original runs with rendered-frame logs: 37
freeze events: 195
freeze time: 79.603s
total observed rendered-frame span: 1057.35min
```

Evaluation scenario:

```text
generation latency = 31ms
```

Results:

| Model | Concealed Freeze Time | Residual User Freeze | Visible FP Time |
|---|---:|---:|---:|
| render-gap only | 59.43s / 74.7% | 20.18s / 25.3% | 386.42s |
| start gate + stable return | 37.76s / 47.4% | 41.84s / 52.6% | 215.83s |
| continuous gate + next-render return | 21.53s / 27.0% | 58.07s / 73.0% | 18.77s |

Interpretation:

```text
render-gap only:
  high coverage, but too much visible generated video outside target.

start gate + stable return:
  moderate coverage, but FP remains high because stable return keeps generated
  video visible after some real frames have returned.

continuous gate:
  much lower FP, but lower coverage because it returns immediately at the next
  real render.
```

### 5.2 Target-Denominator Evaluation

Target definition used for this evaluation:

```text
target freeze if:
  usable_ratio_B80 >= 0.8
  AND no_fresh_rendered_frame_during_freeze
  AND max(video_recv_gap, frame_completion_gap) >= 80ms
```

Target set:

```text
target events: 108 / 195
target duration: 42.349s / 79.603s
```

Target-denominator results:

| Model | Target Concealed Time | Any Concealment Event Ratio | Fully Concealed Event Ratio |
|---|---:|---:|---:|
| render-gap only | 34.89s / 82.4% | 94/108 / 87.0% | 57/108 / 52.8% |
| start gate + stable return | 29.87s / 70.5% | 72/108 / 66.7% | 47/108 / 43.5% |
| continuous gate + next-render return | 19.43s / 45.9% | 55/108 / 50.9% | 0/108 / 0.0% |

Target FP is defined as:

```text
target_fp_time = total_generated_visible_time - generated_visible_time_overlapping_target_freeze
```

This is stricter than all-freeze FP because generated video shown during
non-target freezes is still counted as target FP.

Target FP results:

| Model | Generated Visible Time | Target Concealed Time | Target FP Time | Target Precision |
|---|---:|---:|---:|---:|
| render-gap only | 445.84s | 34.89s | 410.95s | 7.8% |
| start gate + stable return | 253.59s | 29.87s | 223.72s | 11.8% |
| continuous gate + next-render return | 40.30s | 19.43s | 20.87s | 48.2% |

Interpretation:

```text
continuous gate is best for FP/precision.
render-gap only is best for coverage but unusable as a final policy because
most generated playback is outside the target opportunity.
```

### 5.3 Playout Headroom Prefetch Evaluation

Scope:

```text
original runs with effective_playout_budget.csv: 19
target freezes in this subset: 45
target duration in this subset: 13.227s
```

Baseline for the same subset:

```text
continuous gate:
  target coverage = 47.2%
  target FP = 9.0s
  target precision = 40.9%
```

Headroom prefetch result:

```text
headroom prefetch:
  target coverage = 58.7%
  target FP = 16.6s
  target precision = 31.9%
```

Threshold sweep:

| Headroom Threshold | Target Coverage | Target FP | Precision |
|---:|---:|---:|---:|
| 0ms | 58.7% | 16.6s | 31.8% |
| 10ms | 58.7% | 16.6s | 31.9% |
| 20ms | 58.7% | 16.6s | 31.9% |
| 30ms | 58.7% | 16.6s | 31.9% |
| 40ms | 58.7% | 16.6s | 31.9% |
| 60ms | 58.7% | 16.6s | 31.9% |
| 80ms | 58.7% | 16.6s | 31.9% |
| 120ms | 58.7% | 16.6s | 31.9% |

The threshold sweep being almost identical means the current
`effective_budget_ms` signal often stays low enough that the threshold does not
strongly separate true opportunities from false opportunities.

Observed distribution:

```text
effective_budget_ms p50 ~= 34ms
effective_budget_ms p75 ~= 38ms
effective_budget_ms p90 ~= 64ms
effective_budget_ms p99 ~= 73ms
zero budget rows ~= 4.1%
```

Interpretation:

```text
headroom prefetch improves coverage, but it is too broad if used alone.
It should be combined with audio age, video packet age, frame/playable age, and
freshness checks.
```

## 6. Current Issues

### 6.1 Render-Gap Trigger Is Late

Render-gap based generation waits until the user-visible render stream is
already delayed.  Even with a 60ms risk threshold, part of the freeze may have
already been observed by the user.

### 6.2 Headroom Signal Is Broad

The current `effective_budget_ms` signal is often low.  This makes it useful as
an early warning, but weak as a standalone trigger.

Potential reason:

```text
WebRTC low-latency rendering often has very small render headroom even under
normal operation.  Therefore "headroom is small" does not always mean "freeze is
about to happen".
```

### 6.3 Stable Return Increases FP

Stable return avoids flicker, but it can keep generated video visible after a
real frame has already arrived.  This increases target FP.

Immediate return reduces FP, but can cause oscillation:

```text
generated -> real -> generated
```

if the real stream briefly recovers and stalls again.

### 6.4 Missing Runtime Signals

The runtime does not yet fully connect all intended trigger inputs:

```text
audio_age
time_since_last_video_packet
time_since_last_playable_frame
playout_headroom_ms
fresh rendered frame availability
```

Current implementation has render-gap and playout-headroom prefetch.  The next
step is connecting packet/frame starvation signals in the same process-local
signal style.

## 7. Recommended Next Policy

The next practical policy should be:

```text
generation prefetch if:
  audio_age <= 80ms
  AND (
    playout_headroom_ms <= H
    AND headroom_sample_age <= 100ms
    AND (
      time_since_last_video_packet > 100ms
      OR time_since_last_playable_frame > 80ms
      OR future_render_valid_units == 0
    )
  )

display generated video if:
  generated frame ready
  AND no fresh real rendered frame
  AND audio_age <= 80ms
  AND (
    time_since_last_video_packet > 100ms
    OR time_since_last_playable_frame > 80ms
    OR no future renderable unit
  )

return policy:
  default stable return:
    3 consecutive real frames
    AND stable duration >= 120ms
    AND real frame gaps <= 80ms

  experimental immediate return:
    first real rendered frame arrives
```

This keeps the key principle:

```text
Aggressive prefetch, conservative display.
```

## 8. Files And Outputs

Implementation files:

```text
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/scripts/apply_native_overlay.py
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/scripts/apply_playout_headroom_signal.py
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/native_overlay/examples/peerconnection/client/playout_headroom_signal.h
/home/widen/webrtc-checkout/webrtc_wav2lip_lab_staging/native_overlay/examples/peerconnection/client/playout_headroom_signal.cc
```

Applied native files:

```text
/home/widen/webrtc-wav2lip-native/src/examples/peerconnection/client/linux/main_wnd.cc
/home/widen/webrtc-wav2lip-native/src/video/video_stream_buffer_controller.cc
/home/widen/webrtc-wav2lip-native/src/examples/peerconnection/client/playout_headroom_signal.h
/home/widen/webrtc-wav2lip-native/src/examples/peerconnection/client/playout_headroom_signal.cc
```

Evaluation outputs:

```text
/home/widen/webrtc-checkout/analysis_runs/render_switch_policy_eval_original_only/
/home/widen/webrtc-checkout/analysis_runs/gated_render_switch_policy_original_only_summary_v3/
/home/widen/webrtc-checkout/analysis_runs/render_switch_policy_target_denominator_summary/
/home/widen/webrtc-checkout/analysis_runs/headroom_prefetch_policy_original_only/
/home/widen/webrtc-checkout/analysis_runs/headroom_prefetch_policy_original_only_tight/
/home/widen/webrtc-checkout/analysis_runs/staged_trigger_return_policy_target_only_switch_sweep/
```

Most relevant summary files:

```text
analysis_runs/render_switch_policy_target_denominator_summary/target_denominator_summary.csv
analysis_runs/render_switch_policy_target_denominator_summary/target_fp_summary.csv
analysis_runs/headroom_prefetch_policy_original_only_tight/summary.csv
analysis_runs/staged_trigger_return_policy_target_only_switch_sweep/switch_min_gap_sweep_summary.csv
```

## 9. Staged Policy Evaluation Update

The latest offline evaluator implements the proposed split:

```text
packet/frame/playout risk -> hidden generation prefetch
render deadline miss      -> visible switch confirmation
first fresh real frame    -> visible return candidate
stable real stream        -> generator stop
```

Script:

```text
/home/widen/webrtc-checkout/analysis_tools/evaluate_staged_trigger_return_policy.py
```

Scope:

```text
original collection logs only
target-freeze runs only
target = is_broad_concealment_target
generation latency = 31 ms
headroom threshold = 80 ms
audio budget = 80 ms
```

`switch_min_gap_ms` was swept because 60 ms was too sensitive and produced many
visible generated intervals outside target freezes.

| switch_min_gap_ms | target coverage | target FP | target precision | note |
|---:|---:|---:|---:|---|
| 60 | 58.1% | 665.779 s | 3.6% | too sensitive |
| 80 | 57.8% | 56.270 s | 30.3% | high coverage, still many switches |
| 100 | 53.8% | 38.051 s | 37.4% | balanced candidate |
| 120 | 52.8% | 29.256 s | 43.3% | more conservative |
| 160 | 46.6% | 17.008 s | 53.7% | lowest FP, lower coverage |

Current interpretation:

```text
switch_min_gap_ms = 100-120 ms is the practical operating region.
60 ms is not usable because it reacts to normal render jitter.
160 ms is safer but starts to lose too much target-freeze coverage.
```

Important correction:

```text
playout headroom is useful for prefetch/risk detection,
but it should not be required to clear before visible return.
```

Reason: WebRTC low-latency playout often operates with low headroom even when
real frames are arriving normally. Requiring headroom recovery delayed return
and created excessive FP. The current visible-return gate uses packet/frame
starvation recovery instead.

## 10. Immediate Next Checks

1. Connect runtime `audio_age` explicitly instead of relying only on buffered
   audio amount.
2. Add runtime signal for `time_since_last_video_packet`.
3. Add runtime signal for `time_since_last_playable_frame` or equivalent frame
   buffer/playout state.
4. Re-run offline evaluation with a combined trigger:

```text
headroom low AND packet/frame starvation AND audio usable
```

5. Run a two-machine smoke test with:

```bash
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_PREFETCH_MS=20
WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_STALE_MS=100
WEBRTC_WAV2LIP_RETURN_IMMEDIATE=0
WEBRTC_WAV2LIP_RETURN_CONSECUTIVE_REAL_FRAMES=3
WEBRTC_WAV2LIP_RETURN_STABLE_MS=120
WEBRTC_WAV2LIP_RETURN_STABLE_GAP_MS=80
```

6. Compare request reasons in the Wav2Lip server log:

```text
playout_headroom_prefetch
playout_risk_prefetch
render_deadline_miss
generated_queue_low
```

The expected successful behavior is that `playout_headroom_prefetch` appears
before `render_deadline_miss`, while visible generated playback remains limited
to periods with no fresh real rendered frame.
