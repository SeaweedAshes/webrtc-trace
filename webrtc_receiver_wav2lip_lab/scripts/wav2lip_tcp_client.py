#!/usr/bin/env python3
"""Small newline-JSON TCP client for the Wav2Lip local server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import time


def send_request(host: str, port: int, request: dict, timeout_sec: float) -> dict:
    payload = (json.dumps(request) + "\n").encode("utf-8")
    with socket.create_connection((host, port), timeout=timeout_sec) as sock:
        sock.settimeout(timeout_sec)
        sock.sendall(payload)
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("server returned no response")
    return json.loads(data.decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ping")
    gen = sub.add_parser("generate")
    gen.add_argument("--audio", required=True)
    gen.add_argument("--face", required=True)
    gen.add_argument("--output", required=True)
    gen.add_argument("--request-id", default="")
    gen.add_argument("--fps", type=float, default=25.0)
    ctx = sub.add_parser("set-audio-context")
    ctx.add_argument("--audio", required=True)
    ctx.add_argument("--request-id", default="")
    face_ctx = sub.add_parser("set-face-context")
    face_ctx.add_argument("--face", required=True)
    face_ctx.add_argument("--request-id", default="")
    track = sub.add_parser("track-face-context")
    track.add_argument("--frame", required=True)
    track.add_argument("--request-id", default="")
    track.add_argument("--no-detector-fallback", action="store_true")
    track.add_argument("--force-detect", action="store_true")
    tail = sub.add_parser("generate-tail")
    tail.add_argument("--face", default="")
    tail.add_argument("--output", required=True)
    tail.add_argument("--request-id", default="")
    tail.add_argument("--fps", type=float, default=25.0)
    tail.add_argument("--tail-ms", type=int, default=160)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "ping":
        request = {"type": "ping"}
    elif args.command == "generate":
        request_id = args.request_id or f"manual_{int(time.time() * 1000)}"
        request = {
            "type": "generate",
            "request_id": request_id,
            "audio_path": str(Path(args.audio).expanduser()),
            "face_path": str(Path(args.face).expanduser()),
            "output_path": str(Path(args.output).expanduser()),
            "fps": args.fps,
        }
    elif args.command == "set-audio-context":
        request_id = args.request_id or f"audio_ctx_{int(time.time() * 1000)}"
        request = {
            "type": "set_audio_context",
            "request_id": request_id,
            "audio_path": str(Path(args.audio).expanduser()),
        }
    elif args.command == "set-face-context":
        request_id = args.request_id or f"face_ctx_{int(time.time() * 1000)}"
        request = {
            "type": "set_face_context",
            "request_id": request_id,
            "face_path": str(Path(args.face).expanduser()),
        }
    elif args.command == "track-face-context":
        request_id = args.request_id or f"face_track_{int(time.time() * 1000)}"
        request = {
            "type": "track_face_context",
            "request_id": request_id,
            "frame_path": str(Path(args.frame).expanduser()),
            "allow_detector_fallback": not args.no_detector_fallback,
            "force_detect": args.force_detect,
        }
    else:
        request_id = args.request_id or f"tail_{int(time.time() * 1000)}"
        request = {
            "type": "generate_tail",
            "request_id": request_id,
            "output_path": str(Path(args.output).expanduser()),
            "fps": args.fps,
            "tail_ms": args.tail_ms,
        }
        if args.face:
            request["face_path"] = str(Path(args.face).expanduser())
    response = send_request(args.host, args.port, request, args.timeout_sec)
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0 if response.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
