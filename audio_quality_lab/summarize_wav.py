#!/usr/bin/env python3
"""Small WAV sanity summary for generated listening samples."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", nargs="+")
    return parser.parse_args()


def summarize(path: Path) -> None:
    with wave.open(str(path), "rb") as f:
        channels = f.getnchannels()
        sample_width = f.getsampwidth()
        sample_rate = f.getframerate()
        frames = f.getnframes()
        raw = f.readframes(frames)
    duration = frames / float(sample_rate) if sample_rate else 0.0
    rms = None
    peak = None
    if sample_width == 2 and raw:
        count = len(raw) // 2
        samples = struct.unpack("<" + "h" * count, raw[: count * 2])
        peak = max(abs(x) for x in samples) if samples else 0
        mean_square = sum(float(x) * float(x) for x in samples) / len(samples)
        rms = 20.0 * math.log10(math.sqrt(mean_square) / 32768.0) if mean_square > 0 else -math.inf
    print(
        f"{path}: duration={duration:.3f}s sample_rate={sample_rate} "
        f"channels={channels} sample_width={sample_width} peak={peak} rms_dbfs={rms}"
    )


def main() -> int:
    args = parse_args()
    for item in args.wav:
        summarize(Path(item).expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
