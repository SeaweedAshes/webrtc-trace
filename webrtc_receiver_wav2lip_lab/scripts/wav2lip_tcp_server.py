#!/usr/bin/env python3
"""Localhost TCP control server for chunk-based Wav2Lip generation.

Protocol: newline-delimited JSON.

Request examples:
  {"type":"ping"}
  {
    "type":"generate",
    "request_id":"smoke_001",
    "audio_path":"/path/audio.wav",
    "face_path":"/path/face.png",
    "output_path":"/path/out.mp4",
    "fps":25
  }
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import time
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class RequestLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        ensure_parent(path)
        self.fields = [
            "wall_time_ms",
            "request_id",
            "status",
            "latency_ms",
            "audio_path",
            "face_path",
            "output_path",
            "frames_dir",
            "frame_count",
            "error",
        ]
        if not path.exists():
            with path.open("w", newline="") as fp:
                csv.DictWriter(fp, fieldnames=self.fields).writeheader()

    def write(self, row: dict[str, Any]) -> None:
        clean = {field: row.get(field, "") for field in self.fields}
        with self.path.open("a", newline="") as fp:
            csv.DictWriter(fp, fieldnames=self.fields).writerow(clean)


class Wav2LipService:
    def __init__(
        self,
        wav2lip_dir: Path,
        checkpoint_path: Path,
        python_bin: str,
        default_fps: float,
        logger: RequestLogger,
        dry_run: bool,
        cuda_visible_devices: str | None,
    ) -> None:
        self.wav2lip_dir = wav2lip_dir
        self.checkpoint_path = checkpoint_path
        self.python_bin = python_bin
        self.default_fps = default_fps
        self.logger = logger
        self.dry_run = dry_run
        self.cuda_visible_devices = cuda_visible_devices
        self.gpu_info = self._detect_gpu()

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        start = now_ms()
        request_id = str(request.get("request_id", f"req_{start}"))
        audio_path = Path(str(request["audio_path"])).expanduser()
        face_path = Path(str(request["face_path"])).expanduser()
        output_path = Path(str(request["output_path"])).expanduser()
        fps = float(request.get("fps", self.default_fps))
        ensure_parent(output_path)

        error = ""
        status = "ok"
        frames_dir = output_path.parent / "generated_frames"
        frame_count = 0
        try:
            if not audio_path.exists():
                raise FileNotFoundError(f"audio_path does not exist: {audio_path}")
            if not face_path.exists():
                raise FileNotFoundError(f"face_path does not exist: {face_path}")
            if not self.checkpoint_path.exists() or self.checkpoint_path.stat().st_size <= 0:
                raise FileNotFoundError(f"valid checkpoint not found: {self.checkpoint_path}")
            face_path = self._normalize_face_image(face_path, output_path)
            if self.dry_run:
                output_path.write_text("dry-run placeholder\n", encoding="utf-8")
                frame_count = self._write_dry_run_frames(face_path, frames_dir, fps)
            else:
                cmd = [
                    self.python_bin,
                    "inference.py",
                    "--checkpoint_path",
                    str(self.checkpoint_path),
                    "--face",
                    str(face_path),
                    "--audio",
                    str(audio_path),
                    "--outfile",
                    str(output_path),
                    "--fps",
                    str(fps),
                    "--static",
                    "True",
                ]
                subprocess.run(
                    cmd,
                    cwd=str(self.wav2lip_dir),
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=self._subprocess_env(),
                )
                frame_count = self._extract_ppm_frames(output_path, frames_dir)
        except Exception as exc:  # Keep the TCP server alive after bad requests.
            status = "error"
            error = str(exc)

        latency = now_ms() - start
        self.logger.write(
            {
                "wall_time_ms": start,
                "request_id": request_id,
                "status": status,
                "latency_ms": latency,
                "audio_path": str(audio_path),
                "face_path": str(face_path),
                "output_path": str(output_path),
                "frames_dir": str(frames_dir),
                "frame_count": frame_count,
                "error": error,
            }
        )
        return {
            "type": "generate_result",
            "request_id": request_id,
            "status": status,
            "latency_ms": latency,
            "output_path": str(output_path),
            "frames_dir": str(frames_dir),
            "frame_count": frame_count,
            "error": error,
        }

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.cuda_visible_devices is not None:
            env["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        return env

    def _detect_gpu(self) -> dict[str, Any]:
        code = (
            "import json, torch; "
            "print(json.dumps({"
            "'torch_version': torch.__version__, "
            "'cuda_available': torch.cuda.is_available(), "
            "'cuda_version': torch.version.cuda, "
            "'device_count': torch.cuda.device_count(), "
            "'device0': torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''"
            "}))"
        )
        try:
            result = subprocess.run(
                [self.python_bin, "-c", code],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self._subprocess_env(),
            )
            return json.loads(result.stdout.strip().splitlines()[-1])
        except Exception as exc:
            return {
                "torch_version": "",
                "cuda_available": False,
                "cuda_version": "",
                "device_count": 0,
                "device0": "",
                "error": str(exc),
            }

    def _normalize_face_image(self, face_path: Path, output_path: Path) -> Path:
        """Return a Wav2Lip-compatible face image path.

        Wav2Lip's inference script treats only jpg/png/jpeg as static images.
        The native receiver can write easy-to-debug PPM/BMP frames, so this
        server converts non-supported image extensions to PNG before calling
        Wav2Lip.
        """
        if face_path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            return face_path
        import cv2  # Imported lazily so ping/dry-run startup stays lightweight.

        image = cv2.imread(str(face_path))
        if image is None:
            raise ValueError(f"cv2 could not read face image: {face_path}")
        normalized_path = output_path.with_suffix(".face.png")
        ensure_parent(normalized_path)
        if not cv2.imwrite(str(normalized_path), image):
            raise RuntimeError(f"failed to write normalized face image: {normalized_path}")
        return normalized_path

    def _prepare_frames_dir(self, frames_dir: Path) -> None:
        frames_dir.mkdir(parents=True, exist_ok=True)
        for old_frame in frames_dir.glob("frame_*.ppm"):
            old_frame.unlink()

    def _write_ppm(self, path: Path, image_bgr: Any) -> None:
        ensure_parent(path)
        import cv2

        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        h, w = image_rgb.shape[:2]
        with path.open("wb") as fp:
            fp.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
            fp.write(image_rgb.tobytes())

    def _add_generated_overlay(self, image_bgr: Any, label: str) -> Any:
        import cv2

        image = image_bgr.copy()
        h, w = image.shape[:2]
        stripe_h = max(28, h // 16)
        image[:stripe_h, :] = (42, 150, 46)
        cv2.putText(
            image,
            label,
            (12, max(20, stripe_h - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(image, (0, 0), (w - 1, h - 1), (42, 150, 46), 3)
        return image

    def _write_dry_run_frames(self, face_path: Path, frames_dir: Path, fps: float) -> int:
        import cv2

        self._prepare_frames_dir(frames_dir)
        image = cv2.imread(str(face_path))
        if image is None:
            raise ValueError(f"cv2 could not read face image: {face_path}")
        frame_count = max(1, int(round(fps)))
        for idx in range(frame_count):
            frame = self._add_generated_overlay(
                image, f"Wav2Lip generated dry-run {idx + 1:02d}/{frame_count:02d}"
            )
            self._write_ppm(frames_dir / f"frame_{idx:04d}.ppm", frame)
        return frame_count

    def _extract_ppm_frames(self, video_path: Path, frames_dir: Path) -> int:
        import cv2

        self._prepare_frames_dir(frames_dir)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"cv2 could not open generated video: {video_path}")
        count = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frame = self._add_generated_overlay(frame, "Wav2Lip generated")
                self._write_ppm(frames_dir / f"frame_{count:04d}.ppm", frame)
                count += 1
        finally:
            cap.release()
        if count == 0:
            raise RuntimeError(f"no frames extracted from generated video: {video_path}")
        return count


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        service: Wav2LipService = self.server.service  # type: ignore[attr-defined]
        for raw in self.rfile:
            try:
                request = json.loads(raw.decode("utf-8"))
                request_type = request.get("type", "generate")
                if request_type == "ping":
                    response = {
                        "type": "pong",
                        "status": "ok",
                        "wall_time_ms": now_ms(),
                    }
                elif request_type == "generate":
                    response = service.generate(request)
                else:
                    response = {
                        "type": "error",
                        "status": "error",
                        "error": f"unknown request type: {request_type}",
                    }
            except Exception as exc:
                response = {"type": "error", "status": "error", "error": str(exc)}
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
            self.wfile.flush()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--wav2lip-dir", default="/home/widen/Wav2Lip")
    parser.add_argument(
        "--checkpoint-path",
        default="/home/widen/Wav2Lip/checkpoints/wav2lip_gan.pth",
    )
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--fps", type=float, default=25.0)
    parser.add_argument(
        "--log",
        default="/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/runtime/wav2lip_requests.csv",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the TCP path without running Wav2Lip inference.",
    )
    parser.add_argument(
        "--cuda-visible-devices",
        default=os.environ.get("CUDA_VISIBLE_DEVICES"),
        help="CUDA_VISIBLE_DEVICES value for Wav2Lip subprocesses. Example: 0",
    )
    parser.add_argument(
        "--require-gpu",
        action="store_true",
        help="Fail at startup if the selected Python environment cannot use CUDA.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = RequestLogger(Path(args.log).expanduser())
    service = Wav2LipService(
        wav2lip_dir=Path(args.wav2lip_dir).expanduser(),
        checkpoint_path=Path(args.checkpoint_path).expanduser(),
        python_bin=args.python_bin,
        default_fps=args.fps,
        logger=logger,
        dry_run=args.dry_run,
        cuda_visible_devices=args.cuda_visible_devices,
    )
    if args.require_gpu and not service.gpu_info.get("cuda_available", False):
        print(
            "[wav2lip-server] ERROR: --require-gpu was set but CUDA is not available "
            f"for python={service.python_bin}; gpu_info={service.gpu_info}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    with ThreadedTCPServer((args.host, args.port), Handler) as server:
        server.service = service  # type: ignore[attr-defined]
        print(f"[wav2lip-server] listening on {args.host}:{args.port}", flush=True)
        print(f"[wav2lip-server] wav2lip_dir={service.wav2lip_dir}", flush=True)
        print(f"[wav2lip-server] checkpoint={service.checkpoint_path}", flush=True)
        print(
            "[wav2lip-server] gpu_info="
            f"{json.dumps(service.gpu_info, sort_keys=True)}",
            flush=True,
        )
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
