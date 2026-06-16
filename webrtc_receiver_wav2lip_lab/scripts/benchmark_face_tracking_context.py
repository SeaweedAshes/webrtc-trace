#!/usr/bin/env python3
"""Benchmark face-context detector refresh vs cheap tracking refresh."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import socket
import statistics
import time


def request(host: str, port: int, payload: dict, timeout_sec: float) -> dict:
    with socket.create_connection((host, port), timeout=timeout_sec) as sock:
        sock.settimeout(timeout_sec)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("server returned no response")
    return json.loads(data.decode("utf-8"))


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, round((pct / 100.0) * (len(vals) - 1))))
    return vals[idx]


def stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "count": 0,
            "mean": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "max": 0.0,
        }
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "max": max(values),
    }


def parse_canvas(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    if "x" not in value:
        raise ValueError("--canvas must be formatted as WIDTHxHEIGHT")
    width_s, height_s = value.lower().split("x", 1)
    return int(width_s), int(height_s)


def make_shifted_frames(
    face_path: Path, out_dir: Path, count: int, canvas: tuple[int, int] | None
) -> list[Path]:
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    frame = cv2.imread(str(face_path))
    if frame is None:
        raise ValueError(f"cv2 could not read face image: {face_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    face_h, face_w = frame.shape[:2]
    if canvas is None:
        h, w = face_h, face_w
        base = frame
        center_x = 0
        center_y = 0
    else:
        w, h = canvas
        if face_w > w or face_h > h:
            scale = min(w / face_w, h / face_h) * 0.75
            frame = cv2.resize(
                frame,
                (
                    max(16, int(round(face_w * scale))),
                    max(16, int(round(face_h * scale))),
                ),
            )
            face_h, face_w = frame.shape[:2]
        base = np.full((h, w, 3), 24, dtype=np.uint8)
        center_x = (w - face_w) // 2
        center_y = (h - face_h) // 2
    frames = []
    for i in range(count):
        # Simulate slow head/camera movement while preserving frame size.
        dx = int(round(10.0 * np.sin(i * 0.41)))
        dy = int(round(6.0 * np.cos(i * 0.37)))
        if canvas is None:
            matrix = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
            shifted = cv2.warpAffine(
                base,
                matrix,
                (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )
        else:
            shifted = base.copy()
            x = min(max(0, center_x + dx), w - face_w)
            y = min(max(0, center_y + dy), h - face_h)
            shifted[y : y + face_h, x : x + face_w] = frame
        path = out_dir / f"frame_{i:03d}.jpg"
        cv2.imwrite(str(path), shifted)
        frames.append(path)
    return frames


def run_mode(
    *,
    host: str,
    port: int,
    frames: list[Path],
    mode: str,
    timeout_sec: float,
) -> list[dict]:
    rows = []
    if mode == "tracker":
        init = request(
            host,
            port,
            {
                "type": "set_face_context",
                "request_id": f"tracker_init_{int(time.time() * 1000)}",
                "face_path": str(frames[0]),
            },
            timeout_sec,
        )
        rows.append({"mode": "tracker_init", "frame_index": 0, **init})
        iterable = enumerate(frames[1:], start=1)
    else:
        iterable = enumerate(frames, start=0)

    for idx, frame in iterable:
        payload = {
            "type": "track_face_context",
            "request_id": f"{mode}_{idx}_{int(time.time() * 1000)}",
            "frame_path": str(frame),
            "allow_detector_fallback": mode != "tracker_no_fallback",
            "force_detect": mode == "detector",
        }
        response = request(host, port, payload, timeout_sec)
        rows.append({"mode": mode, "frame_index": idx, **response})
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--face", default="/home/widen/Wav2Lip/me.jpg")
    parser.add_argument(
        "--out-dir",
        default="/home/widen/webrtc-wav2lip-lab/runtime/bench/face_tracking_context",
    )
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument(
        "--canvas",
        default="",
        help="Optional synthetic full-frame size, e.g. 1920x1080.",
    )
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir).expanduser()
    frames_dir = out_dir / "synthetic_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = make_shifted_frames(
        Path(args.face).expanduser(), frames_dir, args.frames, parse_canvas(args.canvas)
    )

    ping = request(args.host, args.port, {"type": "ping"}, args.timeout_sec)
    (out_dir / "ping.json").write_text(json.dumps(ping, indent=2), encoding="utf-8")

    rows = []
    rows.extend(
        run_mode(
            host=args.host,
            port=args.port,
            frames=frames,
            mode="detector",
            timeout_sec=args.timeout_sec,
        )
    )
    rows.extend(
        run_mode(
            host=args.host,
            port=args.port,
            frames=frames,
            mode="tracker",
            timeout_sec=args.timeout_sec,
        )
    )

    fields = sorted({key for row in rows for key in row.keys()})
    csv_path = out_dir / "face_tracking_context_benchmark.csv"
    with csv_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary_rows = []
    for mode in ["detector", "tracker"]:
        subset = [r for r in rows if r.get("mode") == mode and r.get("status") == "ok"]
        lat = [float(r.get("latency_ms") or 0) for r in subset]
        track = [float(r.get("face_track_ms") or 0) for r in subset]
        detect = [float(r.get("face_detect_ms") or 0) for r in subset]
        fallbacks = sum(1 for r in subset if r.get("face_detector_fallback"))
        score_vals = [
            float(r.get("face_track_score") or 0)
            for r in subset
            if float(r.get("face_track_score") or 0) > 0
        ]
        summary_rows.append(
            {
                "mode": mode,
                **{f"latency_ms_{k}": round(v, 3) for k, v in stats(lat).items()},
                **{f"track_ms_{k}": round(v, 3) for k, v in stats(track).items()},
                **{f"detect_ms_{k}": round(v, 3) for k, v in stats(detect).items()},
                "detector_fallbacks": fallbacks,
                "track_score_p50": round(statistics.median(score_vals), 4)
                if score_vals
                else 0,
                "track_score_min": round(min(score_vals), 4) if score_vals else 0,
            }
        )

    summary_path = out_dir / "face_tracking_context_summary.csv"
    with summary_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"csv={csv_path}")
    print(f"summary={summary_path}")
    for row in summary_rows:
        print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
