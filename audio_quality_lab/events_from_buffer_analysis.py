#!/usr/bin/env python3
"""Create audio audition gap events from buffer-exhausting analysis CSV."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gap-column", default="audio_peak_gap_max_ms")
    parser.add_argument("--time-column", default="peak_rel_s")
    parser.add_argument("--label-column", default="run")
    parser.add_argument("--only-freeze-associated", action="store_true")
    parser.add_argument("--exclude-audio-stall", action="store_true")
    parser.add_argument("--min-gap-ms", type=float, default=0.0)
    parser.add_argument("--top", type=int, default=0, help="Keep largest N gaps after filtering.")
    return parser.parse_args()


def to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def main() -> int:
    args = parse_args()
    src = Path(args.analysis_csv).expanduser().resolve()
    dst = Path(args.output).expanduser().resolve()
    events: list[dict[str, object]] = []
    with src.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if args.only_freeze_associated and row.get("associated_freeze") != "1":
                continue
            if args.exclude_audio_stall and row.get("excluded_by_audio_stall") == "1":
                continue
            time_sec = to_float(row.get(args.time_column))
            gap_ms = to_float(row.get(args.gap_column))
            if time_sec is None or gap_ms is None or gap_ms < args.min_gap_ms:
                continue
            label_parts = []
            label = row.get(args.label_column, "")
            if label:
                label_parts.append(label)
            if row.get("episode_id"):
                label_parts.append(f"ep{row['episode_id']}")
            if row.get("freeze_duration_ms"):
                label_parts.append(f"freeze{row['freeze_duration_ms']}ms")
            events.append(
                {
                    "time_sec": time_sec,
                    "gap_ms": gap_ms,
                    "label": "_".join(label_parts),
                }
            )

    events.sort(key=lambda e: float(e["gap_ms"]), reverse=True)
    if args.top > 0:
        events = events[: args.top]
    events.sort(key=lambda e: float(e["time_sec"]))

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time_sec", "gap_ms", "label"])
        writer.writeheader()
        writer.writerows(events)
    print(f"wrote {dst} events={len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
