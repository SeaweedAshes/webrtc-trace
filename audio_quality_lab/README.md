# Audio Quality Lab

Standalone tools for listening-oriented audio gap experiments.

This directory is intentionally separate from the WebRTC trace/replay code. It
does not modify the WebRTC checkout, overlay, trace generator, replay scripts,
or collection scripts.

## Purpose

The receiver-side audio packet interval is not the same as perceived audio
silence. Jitter buffer and PLC can hide part of a packet arrival gap. These
tools generate audible WAV samples from a fixed reference WAV under controlled
gap assumptions so that RED/FEC/PLC hypotheses can be checked by listening.

The simulation is not a replacement for WebRTC NetEq. It is a lightweight
audition harness:

- `red_frames=0`: hold-and-fade PLC over the full uncovered gap.
- `red_frames=1`: optimistic model where one 20 ms frame is recovered.
- `red_frames=2`: optimistic model where two 20 ms frames are recovered.
- `jitter_buffer_ms`: amount of the arrival gap assumed to be absorbed before
  PLC becomes audible.

## Input WAV

Use a deterministic speech WAV. Recommended format:

- mono or stereo
- 48 kHz
- 16-bit PCM
- at least 30 seconds

If your file is not already 48 kHz PCM, convert it with:

```bash
ffmpeg -y -i input.wav -ar 48000 -ac 1 -sample_fmt s16 reference_48k_mono.wav
```

## Quick Start

Create a simple gap list:

```bash
cat > /tmp/gaps.csv <<'CSV'
time_sec,gap_ms,label
5.0,200,small
10.0,400,medium
15.0,1000,large
CSV
```

Generate listening samples:

```bash
python3 ~/webrtc-trace/audio_quality_lab/simulate_audio_gaps.py \
  --reference ~/reference_48k_mono.wav \
  --events /tmp/gaps.csv \
  --output-dir ~/audio-gap-test \
  --jitter-buffer-ms 80 \
  --red-frames 0 1 2
```

Outputs:

- `playout_red0.wav`
- `playout_red1.wav`
- `playout_red2.wav`
- `events_applied.csv`

## Extract Events From Existing Analysis CSV

You can turn the existing buffer-exhausting gap analysis into audio audition
events using the associated audio peak gap:

```bash
python3 ~/webrtc-trace/audio_quality_lab/events_from_buffer_analysis.py \
  --analysis-csv ~/Sync/webrtc-trace-runs/20260427/run11/analysis/buffer_exhausting_gap_episodes_receiver_200ms_nominal.csv \
  --output /tmp/run11_audio_events.csv \
  --only-freeze-associated \
  --exclude-audio-stall
```

Then feed `/tmp/run11_audio_events.csv` into `simulate_audio_gaps.py`.
