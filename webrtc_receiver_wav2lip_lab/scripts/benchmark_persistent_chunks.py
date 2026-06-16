#!/usr/bin/env python3
"""Benchmark persistent Wav2Lip latency across audio chunk sizes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import socket
import subprocess
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
    return json.loads(data.decode("utf-8"))


def run_ffmpeg(audio: Path, output: Path, chunk_ms: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.02, chunk_ms / 1000.0)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio),
        "-t",
        f"{duration:.3f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        str(output),
    ]
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--audio", default="/home/widen/audio.wav")
    parser.add_argument("--face", default="/home/widen/Wav2Lip/me.jpg")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--fps", type=float, default=25.0)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument(
        "--chunk-ms",
        nargs="+",
        type=int,
        default=[1000, 700, 500, 400, 300, 250, 200, 150, 100],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = Path(args.audio).expanduser()
    face = Path(args.face).expanduser()
    rows = []

    ping = request(args.host, args.port, {"type": "ping"}, args.timeout_sec)
    (out_dir / "ping.json").write_text(json.dumps(ping, indent=2), encoding="utf-8")

    for chunk_ms in args.chunk_ms:
        case_dir = out_dir / f"chunk_{chunk_ms}ms"
        case_dir.mkdir(parents=True, exist_ok=True)
        chunk_audio = case_dir / "audio.wav"
        run_ffmpeg(audio, chunk_audio, chunk_ms)
        payload = {
            "type": "generate",
            "request_id": f"chunk_{chunk_ms}ms_{int(time.time() * 1000)}",
            "audio_path": str(chunk_audio),
            "face_path": str(face),
            "output_path": str(case_dir / "generated.mp4"),
            "fps": args.fps,
        }
        response = request(args.host, args.port, payload, args.timeout_sec)
        frame_count = int(response.get("frame_count", 0) or 0)
        output_video_ms = 1000.0 * frame_count / args.fps if args.fps > 0 else 0.0
        latency_ms = float(response.get("latency_ms", 0) or 0)
        row = {
            "chunk_ms": chunk_ms,
            "latency_ms": latency_ms,
            "preprocess_ms": response.get("preprocess_ms", ""),
            "inference_ms": response.get("inference_ms", ""),
            "postprocess_ms": response.get("postprocess_ms", ""),
            "face_detect_ms": response.get("face_detect_ms", ""),
            "face_cache_hit": response.get("face_cache_hit", ""),
            "frame_count": frame_count,
            "output_video_ms": output_video_ms,
            "latency_to_output_ratio": latency_ms / output_video_ms
            if output_video_ms > 0
            else "",
            "status": response.get("status", ""),
            "error": response.get("error", ""),
            "frames_dir": response.get("frames_dir", ""),
        }
        rows.append(row)
        (case_dir / "response.json").write_text(
            json.dumps(response, indent=2), encoding="utf-8"
        )

    summary_path = out_dir / "summary.csv"
    with summary_path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"summary={summary_path}")
    for row in rows:
        print(
            "chunk={chunk_ms}ms latency={latency_ms}ms infer={inference_ms}ms "
            "frames={frame_count} out={output_video_ms:.1f}ms ratio={latency_to_output_ratio}".format(
                **row
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
