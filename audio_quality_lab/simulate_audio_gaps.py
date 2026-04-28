#!/usr/bin/env python3
"""Generate audible WAV samples for controlled audio gap experiments.

This is a standalone audition harness. It does not use or modify WebRTC.
It approximates a PLC-like response by repeating the previous short audio frame
with a gradual fade. RED is modeled optimistically as N recovered audio frames.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import struct
import wave
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, help="Input 16-bit PCM WAV")
    parser.add_argument("--events", required=True, help="CSV with time_sec,gap_ms[,label]")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--frame-ms", type=float, default=20.0)
    parser.add_argument("--jitter-buffer-ms", type=float, default=80.0)
    parser.add_argument("--red-frames", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument(
        "--plc-mode",
        choices=["hold_fade", "silence"],
        default="hold_fade",
        help="Audition model for uncovered audio gap.",
    )
    parser.add_argument(
        "--copy-reference",
        action="store_true",
        help="Also copy the reference WAV to output-dir/reference.wav.",
    )
    return parser.parse_args()


def read_wav(path: Path) -> tuple[list[int], int, int]:
    with wave.open(str(path), "rb") as f:
        channels = f.getnchannels()
        sample_width = f.getsampwidth()
        sample_rate = f.getframerate()
        frames = f.getnframes()
        if sample_width != 2:
            raise SystemExit(f"{path} must be 16-bit PCM WAV; got sample width {sample_width}")
        raw = f.readframes(frames)
    samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    return samples, sample_rate, channels


def write_wav(path: Path, samples: list[int], sample_rate: int, channels: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = [max(-32768, min(32767, int(x))) for x in samples]
    raw = struct.pack("<" + "h" * len(clipped), *clipped)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(raw)


def read_events(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        required = {"time_sec", "gap_ms"}
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit(f"{path} must have columns: time_sec,gap_ms[,label]")
        for idx, row in enumerate(reader, start=1):
            try:
                time_sec = float(row["time_sec"])
                gap_ms = float(row["gap_ms"])
            except ValueError as exc:
                raise SystemExit(f"bad event row {idx}: {row}") from exc
            if time_sec < 0 or gap_ms <= 0:
                continue
            events.append(
                {
                    "event_id": idx,
                    "time_sec": time_sec,
                    "gap_ms": gap_ms,
                    "label": row.get("label", ""),
                }
            )
    return sorted(events, key=lambda e: float(e["time_sec"]))


def make_plc_segment(
    samples: list[int],
    start_sample: int,
    length_samples: int,
    frame_samples: int,
    channels: int,
    mode: str,
) -> list[int]:
    if length_samples <= 0:
        return []
    if mode == "silence" or start_sample <= 0:
        return [0] * length_samples

    history_len = min(frame_samples * channels, start_sample)
    history_start = start_sample - history_len
    history = samples[history_start:start_sample]
    if not history:
        return [0] * length_samples

    out: list[int] = []
    while len(out) < length_samples:
        out.extend(history)
    out = out[:length_samples]

    # A simple approximation of PLC becoming less reliable over time.
    for i in range(len(out)):
        sample_idx = i // channels
        fade = max(0.15, 1.0 - sample_idx / max(1.0, length_samples / channels))
        out[i] = int(out[i] * fade)
    return out


def apply_events(
    original: list[int],
    events: list[dict[str, object]],
    sample_rate: int,
    channels: int,
    frame_ms: float,
    jitter_buffer_ms: float,
    red_frames: int,
    plc_mode: str,
) -> tuple[list[int], list[dict[str, object]]]:
    out = list(original)
    frame_samples_per_channel = max(1, int(round(sample_rate * frame_ms / 1000.0)))
    applied: list[dict[str, object]] = []
    for event in events:
        time_sec = float(event["time_sec"])
        gap_ms = float(event["gap_ms"])
        recovered_ms = red_frames * frame_ms
        audible_gap_ms = max(0.0, gap_ms - jitter_buffer_ms - recovered_ms)
        start_sample = int(round(time_sec * sample_rate)) * channels
        length_samples = int(round(audible_gap_ms * sample_rate / 1000.0)) * channels
        if start_sample >= len(out) or length_samples <= 0:
            applied.append({**event, "red_frames": red_frames, "audible_gap_ms": audible_gap_ms, "applied": 0})
            continue
        end_sample = min(len(out), start_sample + length_samples)
        replacement = make_plc_segment(
            out,
            start_sample,
            end_sample - start_sample,
            frame_samples_per_channel,
            channels,
            plc_mode,
        )
        out[start_sample:end_sample] = replacement
        applied.append(
            {
                **event,
                "red_frames": red_frames,
                "jitter_buffer_ms": jitter_buffer_ms,
                "recovered_ms": recovered_ms,
                "audible_gap_ms": audible_gap_ms,
                "applied": 1,
            }
        )
    return out, applied


def write_applied(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "event_id",
        "time_sec",
        "gap_ms",
        "label",
        "red_frames",
        "jitter_buffer_ms",
        "recovered_ms",
        "audible_gap_ms",
        "applied",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    reference = Path(args.reference).expanduser().resolve()
    events_path = Path(args.events).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    samples, sample_rate, channels = read_wav(reference)
    events = read_events(events_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.copy_reference:
        shutil.copy2(reference, output_dir / "reference.wav")

    all_applied: list[dict[str, object]] = []
    for red_frames in args.red_frames:
        processed, applied = apply_events(
            samples,
            events,
            sample_rate,
            channels,
            args.frame_ms,
            args.jitter_buffer_ms,
            red_frames,
            args.plc_mode,
        )
        out_path = output_dir / f"playout_red{red_frames}.wav"
        write_wav(out_path, processed, sample_rate, channels)
        all_applied.extend(applied)
        audible = [float(row["audible_gap_ms"]) for row in applied]
        mean_audible = sum(audible) / len(audible) if audible else 0.0
        print(
            f"red_frames={red_frames} output={out_path} "
            f"events={len(applied)} mean_audible_gap_ms={mean_audible:.1f} "
            f"max_audible_gap_ms={max(audible) if audible else 0:.1f}"
        )

    write_applied(output_dir / "events_applied.csv", all_applied)
    print(f"wrote {output_dir / 'events_applied.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
