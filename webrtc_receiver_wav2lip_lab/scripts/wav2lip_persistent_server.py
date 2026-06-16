#!/usr/bin/env python3
"""Persistent localhost TCP Wav2Lip server.

This server keeps the Wav2Lip model and face detector in memory so each request
does not pay subprocess startup and checkpoint load cost.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import socketserver
import sys
import threading
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
            "request_type",
            "request_id",
            "status",
            "latency_ms",
            "preprocess_ms",
            "inference_ms",
            "tensor_ms",
            "model_forward_ms",
            "gpu_to_cpu_ms",
            "blend_ms",
            "postprocess_ms",
            "face_detect_ms",
            "face_cache_hit",
            "face_track_ms",
            "face_track_status",
            "face_track_score",
            "face_detector_fallback",
            "context_ms",
            "tail_ms",
            "device",
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


class PersistentWav2LipService:
    def __init__(
        self,
        wav2lip_dir: Path,
        checkpoint_path: Path,
        default_fps: float,
        logger: RequestLogger,
        require_gpu: bool,
        debug_overlay: bool,
        write_mp4: bool,
        warmup: bool,
        warmup_tail_ms: int,
        warmup_face_path: Path | None,
        warmup_iters: int,
    ) -> None:
        self.wav2lip_dir = wav2lip_dir
        self.checkpoint_path = checkpoint_path
        self.default_fps = default_fps
        self.logger = logger
        self.debug_overlay = debug_overlay
        self.write_mp4 = write_mp4
        self.warmup_tail_ms = warmup_tail_ms
        self.warmup_face_path = warmup_face_path
        self.warmup_iters = max(1, warmup_iters)
        self.lock = threading.Lock()

        sys.path.insert(0, str(wav2lip_dir))
        import audio  # type: ignore
        import cv2  # type: ignore
        import face_detection  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from models import Wav2Lip  # type: ignore

        self.audio = audio
        self.cv2 = cv2
        self.face_detection = face_detection
        self.np = np
        self.torch = torch
        self.Wav2Lip = Wav2Lip

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if require_gpu and self.device != "cuda":
            raise RuntimeError(
                "--require-gpu was set but torch.cuda.is_available() is false"
            )
        if self.device == "cuda":
            torch.backends.cudnn.benchmark = True

        self.img_size = 96
        self.mel_step_size = 16
        self.wav2lip_batch_size = 128
        self.pads = [0, 10, 0, 0]
        self.cached_face_coords: tuple[int, int, int, int] | None = None
        self.cached_frame_shape: tuple[int, int] | None = None
        self.track_template_gray: Any | None = None
        self.track_template_shape: tuple[int, int] | None = None
        self.track_search_margin_ratio = 0.65
        self.track_min_score = 0.55
        self.latest_audio_context: Any | None = None
        self.latest_audio_context_ms = 0
        self.audio_context_warmup_done = False
        self.latest_face_context: dict[str, Any] | None = None
        self.face_context_warmup_done = False

        self.model = self._load_model(checkpoint_path)
        self.detector = face_detection.FaceAlignment(
            face_detection.LandmarksType._2D,
            flip_input=False,
            device=self.device,
        )
        self.warmup_ms = 0
        if warmup:
            self.warmup_ms = self._warmup()

    def gpu_info(self) -> dict[str, Any]:
        torch = self.torch
        return {
            "torch_version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "cuda_version": torch.version.cuda,
            "device_count": torch.cuda.device_count(),
            "device0": torch.cuda.get_device_name(0)
            if torch.cuda.is_available()
            else "",
            "device": self.device,
            "warmup_ms": self.warmup_ms,
        }

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._generate_from_request(request, request_type="generate")

    def set_audio_context(self, request: dict[str, Any]) -> dict[str, Any]:
        start = now_ms()
        request_id = str(request.get("request_id", f"audio_ctx_{start}"))
        audio_path = Path(str(request["audio_path"])).expanduser()
        status = "ok"
        error = ""
        context_ms = 0
        try:
            if not audio_path.exists():
                raise FileNotFoundError(f"audio_path does not exist: {audio_path}")
            wav = self.audio.load_wav(str(audio_path), 16000)
            context_ms = int(round(1000.0 * len(wav) / 16000.0))
            with self.lock:
                self.latest_audio_context = wav
                self.latest_audio_context_ms = context_ms
                if (
                    not self.audio_context_warmup_done
                    and self.warmup_face_path
                    and self.warmup_face_path.exists()
                ):
                    self._warmup_with_audio_context(wav)
                    self.audio_context_warmup_done = True
                if (
                    not self.face_context_warmup_done
                    and self.latest_face_context is not None
                ):
                    self._warmup_with_current_context_locked()
                    self.face_context_warmup_done = True
        except Exception as exc:
            status = "error"
            error = str(exc)
        latency_ms = now_ms() - start
        self.logger.write(
            {
                "wall_time_ms": start,
                "request_type": "set_audio_context",
                "request_id": request_id,
                "status": status,
                "latency_ms": latency_ms,
                "preprocess_ms": latency_ms if status == "ok" else 0,
                "context_ms": context_ms,
                "audio_path": str(audio_path),
                "error": error,
            }
        )
        return {
            "type": "set_audio_context_result",
            "request_id": request_id,
            "status": status,
            "latency_ms": latency_ms,
            "context_ms": context_ms,
            "error": error,
        }

    def set_face_context(self, request: dict[str, Any]) -> dict[str, Any]:
        start = now_ms()
        request_id = str(request.get("request_id", f"face_ctx_{start}"))
        face_path = Path(str(request["face_path"])).expanduser()
        status = "ok"
        error = ""
        face_detect_ms = 0
        warmed = False
        try:
            if not face_path.exists():
                raise FileNotFoundError(f"face_path does not exist: {face_path}")
            frame = self.cv2.imread(str(face_path))
            if frame is None:
                raise ValueError(f"cv2 could not read face image: {face_path}")
            with self.lock:
                face_tensor, coords, face_info = self._prepare_static_face(
                    frame, force_detect=True
                )
                self._update_tracking_template(frame, coords)
                face_detect_ms = face_info["face_detect_ms"]
                self.latest_face_context = {
                    "base_frame": frame,
                    "face_tensor": face_tensor,
                    "coords": coords,
                    "face_path": str(face_path),
                    "updated_ms": start,
                }
                if self.latest_audio_context is not None:
                    self._warmup_with_current_context_locked()
                    self.face_context_warmup_done = True
                    warmed = True
        except Exception as exc:
            status = "error"
            error = str(exc)
        latency_ms = now_ms() - start
        self.logger.write(
            {
                "wall_time_ms": start,
                "request_type": "set_face_context",
                "request_id": request_id,
                "status": status,
                "latency_ms": latency_ms,
                "preprocess_ms": latency_ms if status == "ok" else 0,
                "face_detect_ms": face_detect_ms,
                "face_cache_hit": 0,
                "face_path": str(face_path),
                "frame_count": 1 if status == "ok" else 0,
                "error": error,
            }
        )
        return {
            "type": "set_face_context_result",
            "request_id": request_id,
            "status": status,
            "latency_ms": latency_ms,
            "face_detect_ms": face_detect_ms,
            "warmed": warmed,
            "error": error,
        }

    def track_face_context(self, request: dict[str, Any]) -> dict[str, Any]:
        start = now_ms()
        request_id = str(request.get("request_id", f"face_track_{start}"))
        frame_path = Path(str(request["frame_path"])).expanduser()
        allow_detector_fallback = bool(request.get("allow_detector_fallback", True))
        force_detect = bool(request.get("force_detect", False))
        status = "ok"
        error = ""
        face_detect_ms = 0
        face_track_ms = 0
        face_track_status = ""
        face_track_score = 0.0
        face_detector_fallback = False
        try:
            if not frame_path.exists():
                raise FileNotFoundError(f"frame_path does not exist: {frame_path}")
            frame = self.cv2.imread(str(frame_path))
            if frame is None:
                raise ValueError(f"cv2 could not read frame image: {frame_path}")
            with self.lock:
                t_track = now_ms()
                if (
                    force_detect
                    or self.latest_face_context is None
                    or self.cached_face_coords is None
                    or self.track_template_gray is None
                ):
                    face_track_status = "detector_init"
                    face_detector_fallback = True
                    face_tensor, coords, face_info = self._prepare_static_face(
                        frame, force_detect=True
                    )
                    face_detect_ms = face_info["face_detect_ms"]
                    self._update_tracking_template(frame, coords)
                else:
                    tracked = self._track_face_on_frame(frame)
                    face_track_ms = now_ms() - t_track
                    if tracked is None:
                        face_track_status = "track_fail"
                        if not allow_detector_fallback:
                            raise RuntimeError("face tracking failed")
                        face_detector_fallback = True
                        face_tensor, coords, face_info = self._prepare_static_face(
                            frame, force_detect=True
                        )
                        face_detect_ms = face_info["face_detect_ms"]
                        self._update_tracking_template(frame, coords)
                    else:
                        coords, face_track_score = tracked
                        face_track_status = "tracked"
                        face_tensor = self._face_tensor_from_coords(frame, coords)
                        self.cached_face_coords = coords
                        self.cached_frame_shape = frame.shape[:2]
                        self._update_tracking_template(frame, coords)

                self.latest_face_context = {
                    "base_frame": frame,
                    "face_tensor": face_tensor,
                    "coords": coords,
                    "face_path": str(frame_path),
                    "updated_ms": start,
                    "track_score": face_track_score,
                    "track_status": face_track_status,
                }
        except Exception as exc:
            status = "error"
            error = str(exc)
        latency_ms = now_ms() - start
        self.logger.write(
            {
                "wall_time_ms": start,
                "request_type": "track_face_context",
                "request_id": request_id,
                "status": status,
                "latency_ms": latency_ms,
                "preprocess_ms": latency_ms if status == "ok" else 0,
                "face_detect_ms": face_detect_ms,
                "face_cache_hit": 0,
                "face_track_ms": face_track_ms,
                "face_track_status": face_track_status,
                "face_track_score": round(face_track_score, 4),
                "face_detector_fallback": int(face_detector_fallback),
                "face_path": str(frame_path),
                "frame_count": 1 if status == "ok" else 0,
                "error": error,
            }
        )
        return {
            "type": "track_face_context_result",
            "request_id": request_id,
            "status": status,
            "latency_ms": latency_ms,
            "face_track_ms": face_track_ms,
            "face_track_status": face_track_status,
            "face_track_score": round(face_track_score, 4),
            "face_detect_ms": face_detect_ms,
            "face_detector_fallback": face_detector_fallback,
            "error": error,
        }

    def generate_tail(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._generate_from_request(request, request_type="generate_tail")

    def _generate_from_request(
        self, request: dict[str, Any], request_type: str
    ) -> dict[str, Any]:
        start = now_ms()
        request_id = str(request.get("request_id", f"req_{start}"))
        audio_path = Path(str(request.get("audio_path", ""))).expanduser()
        face_path_raw = str(request.get("face_path", "")).strip()
        face_path = Path(face_path_raw).expanduser() if face_path_raw else None
        output_path = Path(str(request["output_path"])).expanduser()
        fps = float(request.get("fps", self.default_fps))
        tail_ms = int(request.get("tail_ms", 0) or 0)
        frames_dir = output_path.parent / "generated_frames"
        frame_count = 0
        context_ms = 0
        status = "ok"
        error = ""
        preprocess_ms = 0
        inference_ms = 0
        postprocess_ms = 0
        tensor_ms = 0.0
        model_forward_ms = 0.0
        gpu_to_cpu_ms = 0.0
        blend_ms = 0.0
        face_detect_ms = 0
        face_cache_hit = False
        ensure_parent(output_path)

        try:
            if request_type != "generate_tail" and face_path is None:
                raise RuntimeError("face_path is required for non-tail generation")
            if face_path is not None and not face_path.exists():
                raise FileNotFoundError(f"face_path does not exist: {face_path}")
            with self.lock:
                wav_override = None
                if request_type == "generate_tail":
                    if self.latest_audio_context is None:
                        raise RuntimeError("no rolling audio context available")
                    wav_override = self.latest_audio_context.copy()
                    context_ms = self.latest_audio_context_ms
                else:
                    assert face_path is not None
                    if not audio_path.exists():
                        raise FileNotFoundError(
                            f"audio_path does not exist: {audio_path}"
                        )
                t0 = now_ms()
                if request_type == "generate_tail" and face_path is None:
                    if self.latest_face_context is None:
                        raise RuntimeError("no rolling face context available")
                    base_frame = self.latest_face_context["base_frame"]
                    face_tensor = self.latest_face_context["face_tensor"]
                    coords = self.latest_face_context["coords"]
                    face_info = {"face_detect_ms": 0, "face_cache_hit": True}
                    mel_chunks = self._mel_chunks_from_wav(wav_override, fps)
                else:
                    assert face_path is not None
                    base_frame, face_tensor, coords, mel_chunks, face_info = self._prepare_inputs(
                        face_path, audio_path, fps, wav_override=wav_override
                    )
                if context_ms == 0:
                    context_ms = int(
                        round(1000.0 * len(wav_override) / 16000.0)
                    ) if wav_override is not None else 0
                face_detect_ms = face_info["face_detect_ms"]
                face_cache_hit = face_info["face_cache_hit"]
                preprocess_ms = now_ms() - t0

                if request_type == "generate_tail" and tail_ms > 0 and mel_chunks:
                    tail_frames = max(1, int(round(fps * tail_ms / 1000.0)))
                    mel_chunks = mel_chunks[-tail_frames:]

                t1 = now_ms()
                frames, infer_info = self._infer_frames(
                    base_frame, face_tensor, coords, mel_chunks
                )
                inference_ms = now_ms() - t1
                tensor_ms = infer_info["tensor_ms"]
                model_forward_ms = infer_info["model_forward_ms"]
                gpu_to_cpu_ms = infer_info["gpu_to_cpu_ms"]
                blend_ms = infer_info["blend_ms"]

                t2 = now_ms()
                self._write_frames(frames, frames_dir)
                if self.write_mp4:
                    self._write_mp4(frames, output_path, fps)
                else:
                    output_path.write_text(
                        "persistent-server frames-only output\n", encoding="utf-8"
                    )
                postprocess_ms = now_ms() - t2
                frame_count = len(frames)
        except Exception as exc:
            status = "error"
            error = str(exc)

        latency_ms = now_ms() - start
        row = {
            "wall_time_ms": start,
            "request_type": request_type,
            "request_id": request_id,
            "status": status,
            "latency_ms": latency_ms,
            "preprocess_ms": preprocess_ms,
            "inference_ms": inference_ms,
            "tensor_ms": round(tensor_ms, 3),
            "model_forward_ms": round(model_forward_ms, 3),
            "gpu_to_cpu_ms": round(gpu_to_cpu_ms, 3),
            "blend_ms": round(blend_ms, 3),
            "postprocess_ms": postprocess_ms,
            "face_detect_ms": face_detect_ms,
            "face_cache_hit": int(face_cache_hit),
            "context_ms": context_ms,
            "tail_ms": tail_ms,
            "device": self.device,
            "audio_path": str(audio_path),
            "face_path": str(face_path) if face_path is not None else "<face_context>",
            "output_path": str(output_path),
            "frames_dir": str(frames_dir),
            "frame_count": frame_count,
            "error": error,
        }
        self.logger.write(row)
        return {
            "type": "generate_result",
            "request_id": request_id,
            "status": status,
            "latency_ms": latency_ms,
            "preprocess_ms": preprocess_ms,
            "inference_ms": inference_ms,
            "tensor_ms": round(tensor_ms, 3),
            "model_forward_ms": round(model_forward_ms, 3),
            "gpu_to_cpu_ms": round(gpu_to_cpu_ms, 3),
            "blend_ms": round(blend_ms, 3),
            "postprocess_ms": postprocess_ms,
            "face_detect_ms": face_detect_ms,
            "face_cache_hit": face_cache_hit,
            "context_ms": context_ms,
            "tail_ms": tail_ms,
            "device": self.device,
            "output_path": str(output_path),
            "frames_dir": str(frames_dir),
            "frame_count": frame_count,
            "error": error,
        }

    def _load_model(self, checkpoint_path: Path) -> Any:
        if not checkpoint_path.exists() or checkpoint_path.stat().st_size <= 0:
            raise FileNotFoundError(f"valid checkpoint not found: {checkpoint_path}")
        model = self.Wav2Lip()
        if self.device == "cuda":
            checkpoint = self.torch.load(str(checkpoint_path))
        else:
            checkpoint = self.torch.load(
                str(checkpoint_path), map_location=lambda storage, loc: storage
            )
        state = {
            key.replace("module.", ""): value
            for key, value in checkpoint["state_dict"].items()
        }
        model.load_state_dict(state)
        return model.to(self.device).eval()

    def _warmup(self) -> int:
        start = now_ms()
        np = self.np
        torch = self.torch
        try:
            # Warm the audio/mel path as well as CUDA kernels. The real first
            # set_audio_context still loads a file, but this avoids first-use
            # FFT/mel-basis setup during the first generate_tail request.
            self.audio.melspectrogram(np.zeros(16000, dtype=np.float32))

            dummy_image = np.zeros((1, 256, 256, 3), dtype=np.uint8)
            self.detector.get_detections_for_batch(dummy_image)
            warmup_frames = max(
                1, int(round(self.default_fps * self.warmup_tail_ms / 1000.0))
            )
            dummy_mel = torch.randn(
                (warmup_frames, 1, 80, self.mel_step_size), device=self.device
            )
            dummy_face = torch.randn(
                (warmup_frames, 6, self.img_size, self.img_size), device=self.device
            )
            with torch.no_grad():
                for _ in range(self.warmup_iters):
                    self.model(dummy_mel, dummy_face)
            if self.device == "cuda":
                torch.cuda.synchronize()

            if self.warmup_face_path and self.warmup_face_path.exists():
                real_frame = self.cv2.imread(str(self.warmup_face_path))
                if real_frame is not None:
                    face_tensor, _, _ = self._prepare_static_face(real_frame)
                    real_face_batch = face_tensor.repeat(warmup_frames, 1, 1, 1)
                    with torch.no_grad():
                        for _ in range(self.warmup_iters):
                            self.model(dummy_mel, real_face_batch)
                    if self.device == "cuda":
                        torch.cuda.synchronize()
        except Exception:
            pass
        return now_ms() - start

    def _warmup_with_audio_context(self, wav: Any) -> None:
        if not self.warmup_face_path:
            return
        base_frame, face_tensor, coords, mel_chunks, _ = self._prepare_inputs(
            self.warmup_face_path, Path("."), self.default_fps, wav_override=wav
        )
        if not mel_chunks:
            return
        warmup_frames = max(
            1, int(round(self.default_fps * self.warmup_tail_ms / 1000.0))
        )
        self._infer_frames(base_frame, face_tensor, coords, mel_chunks[-warmup_frames:])

    def _warmup_with_current_context_locked(self) -> None:
        if self.latest_face_context is None or self.latest_audio_context is None:
            return
        mel_chunks = self._mel_chunks_from_wav(
            self.latest_audio_context, self.default_fps
        )
        if not mel_chunks:
            return
        warmup_frames = max(
            1, int(round(self.default_fps * self.warmup_tail_ms / 1000.0))
        )
        self._infer_frames(
            self.latest_face_context["base_frame"],
            self.latest_face_context["face_tensor"],
            self.latest_face_context["coords"],
            mel_chunks[-warmup_frames:],
        )

    def _prepare_inputs(
        self,
        face_path: Path,
        audio_path: Path,
        fps: float,
        wav_override: Any | None = None,
    ) -> tuple[Any, Any, tuple[int, int, int, int], list[Any], dict[str, Any]]:
        cv2 = self.cv2
        base_frame = cv2.imread(str(face_path))
        if base_frame is None:
            raise ValueError(f"cv2 could not read face image: {face_path}")
        face_tensor, coords, face_info = self._prepare_static_face(base_frame)
        wav = wav_override if wav_override is not None else self.audio.load_wav(str(audio_path), 16000)
        mel_chunks = self._mel_chunks_from_wav(wav, fps)
        return base_frame, face_tensor, coords, mel_chunks, face_info

    def _mel_chunks_from_wav(self, wav: Any, fps: float) -> list[Any]:
        mel = self.audio.melspectrogram(wav)
        if self.np.isnan(mel.reshape(-1)).sum() > 0:
            raise ValueError("mel contains NaN")
        mel_chunks = []
        mel_idx_multiplier = 80.0 / fps
        idx = 0
        while True:
            start_idx = int(idx * mel_idx_multiplier)
            if start_idx + self.mel_step_size > len(mel[0]):
                mel_chunks.append(mel[:, len(mel[0]) - self.mel_step_size :])
                break
            mel_chunks.append(mel[:, start_idx : start_idx + self.mel_step_size])
            idx += 1
        return mel_chunks

    def _prepare_static_face(
        self, base_frame: Any, force_detect: bool = False
    ) -> tuple[Any, tuple[int, int, int, int], dict[str, Any]]:
        cv2 = self.cv2
        np = self.np
        frame_shape = base_frame.shape[:2]
        face_cache_hit = (
            not force_detect
            and
            self.cached_face_coords is not None
            and self.cached_frame_shape == frame_shape
        )
        face_detect_ms = 0
        if face_cache_hit:
            y1, y2, x1, x2 = self.cached_face_coords
        else:
            detect_start = now_ms()
            predictions = self.detector.get_detections_for_batch(np.array([base_frame]))
            face_detect_ms = now_ms() - detect_start
            rect = predictions[0]
            if rect is None:
                raise ValueError("face not detected")
            pady1, pady2, padx1, padx2 = self.pads
            y1 = max(0, rect[1] - pady1)
            y2 = min(base_frame.shape[0], rect[3] + pady2)
            x1 = max(0, rect[0] - padx1)
            x2 = min(base_frame.shape[1], rect[2] + padx2)
            self.cached_face_coords = (y1, y2, x1, x2)
            self.cached_frame_shape = frame_shape
        face = base_frame[y1:y2, x1:x2]
        face = cv2.resize(face, (self.img_size, self.img_size))

        img_tensor = self._face_tensor_from_face_image(face)
        return img_tensor, (y1, y2, x1, x2), {
            "face_detect_ms": face_detect_ms,
            "face_cache_hit": face_cache_hit,
        }

    def _face_tensor_from_coords(
        self, base_frame: Any, coords: tuple[int, int, int, int]
    ) -> Any:
        y1, y2, x1, x2 = coords
        face = base_frame[y1:y2, x1:x2]
        if face.size == 0:
            raise ValueError(f"empty face crop: {coords}")
        face = self.cv2.resize(face, (self.img_size, self.img_size))
        return self._face_tensor_from_face_image(face)

    def _face_tensor_from_face_image(self, face: Any) -> Any:
        np = self.np
        img_batch = np.asarray([face])
        img_masked = img_batch.copy()
        img_masked[:, self.img_size // 2 :] = 0
        img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.0
        img_tensor = self.torch.FloatTensor(
            np.transpose(img_batch, (0, 3, 1, 2))
        ).to(self.device)
        return img_tensor

    def _update_tracking_template(
        self, frame: Any, coords: tuple[int, int, int, int]
    ) -> None:
        y1, y2, x1, x2 = coords
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            self.track_template_gray = None
            self.track_template_shape = None
            return
        gray = self.cv2.cvtColor(crop, self.cv2.COLOR_BGR2GRAY)
        # Downscale large crops before template matching. The tracker is used
        # only to keep the Wav2Lip mouth crop fresh; pixel-perfect tracking is
        # less important than keeping this off the generation critical path.
        h, w = gray.shape[:2]
        max_side = max(h, w)
        if max_side > 160:
            scale = 160.0 / max_side
            gray = self.cv2.resize(
                gray,
                (max(16, int(round(w * scale))), max(16, int(round(h * scale)))),
            )
        self.track_template_gray = gray
        self.track_template_shape = gray.shape[:2]

    def _track_face_on_frame(
        self, frame: Any
    ) -> tuple[tuple[int, int, int, int], float] | None:
        if (
            self.cached_face_coords is None
            or self.cached_frame_shape is None
            or self.track_template_gray is None
            or self.track_template_shape is None
        ):
            return None
        frame_h, frame_w = frame.shape[:2]
        old_h, old_w = self.cached_frame_shape
        if (frame_h, frame_w) != (old_h, old_w):
            return None

        y1, y2, x1, x2 = self.cached_face_coords
        crop_h = max(1, y2 - y1)
        crop_w = max(1, x2 - x1)
        margin = int(round(max(crop_h, crop_w) * self.track_search_margin_ratio))
        sy1 = max(0, y1 - margin)
        sy2 = min(frame_h, y2 + margin)
        sx1 = max(0, x1 - margin)
        sx2 = min(frame_w, x2 + margin)
        search = frame[sy1:sy2, sx1:sx2]
        if search.size == 0:
            return None

        search_gray = self.cv2.cvtColor(search, self.cv2.COLOR_BGR2GRAY)
        tmpl_h, tmpl_w = self.track_template_shape
        scale = tmpl_w / float(crop_w)
        search_scaled = self.cv2.resize(
            search_gray,
            (
                max(tmpl_w, int(round(search_gray.shape[1] * scale))),
                max(tmpl_h, int(round(search_gray.shape[0] * scale))),
            ),
        )
        if (
            search_scaled.shape[0] < tmpl_h
            or search_scaled.shape[1] < tmpl_w
        ):
            return None

        result = self.cv2.matchTemplate(
            search_scaled, self.track_template_gray, self.cv2.TM_CCOEFF_NORMED
        )
        _, max_val, _, max_loc = self.cv2.minMaxLoc(result)
        if max_val < self.track_min_score:
            return None

        new_x1 = sx1 + int(round(max_loc[0] / scale))
        new_y1 = sy1 + int(round(max_loc[1] / scale))
        new_x2 = min(frame_w, new_x1 + crop_w)
        new_y2 = min(frame_h, new_y1 + crop_h)
        new_x1 = max(0, new_x2 - crop_w)
        new_y1 = max(0, new_y2 - crop_h)
        coords = (new_y1, new_y2, new_x1, new_x2)
        return coords, float(max_val)

    def _infer_frames(
        self,
        base_frame: Any,
        face_tensor: Any,
        coords: tuple[int, int, int, int],
        mel_chunks: list[Any],
    ) -> tuple[list[Any], dict[str, float]]:
        cv2 = self.cv2
        np = self.np
        torch = self.torch
        frames = []
        tensor_ms = 0.0
        model_forward_ms = 0.0
        gpu_to_cpu_ms = 0.0
        blend_ms = 0.0
        y1, y2, x1, x2 = coords
        for start_idx in range(0, len(mel_chunks), self.wav2lip_batch_size):
            batch = mel_chunks[start_idx : start_idx + self.wav2lip_batch_size]
            t_tensor = time.perf_counter()
            mel_batch = np.asarray(batch)
            mel_batch = np.reshape(
                mel_batch, [len(mel_batch), mel_batch.shape[1], mel_batch.shape[2], 1]
            )
            mel_tensor = torch.FloatTensor(
                np.transpose(mel_batch, (0, 3, 1, 2))
            ).to(self.device)
            face_batch = face_tensor.repeat(len(batch), 1, 1, 1)
            if self.device == "cuda":
                torch.cuda.synchronize()
            tensor_ms += (time.perf_counter() - t_tensor) * 1000.0

            t_model = time.perf_counter()
            with torch.no_grad():
                pred = self.model(mel_tensor, face_batch)
            if self.device == "cuda":
                torch.cuda.synchronize()
            model_forward_ms += (time.perf_counter() - t_model) * 1000.0

            t_copy = time.perf_counter()
            pred = pred.detach().cpu().numpy().transpose(0, 2, 3, 1) * 255.0
            gpu_to_cpu_ms += (time.perf_counter() - t_copy) * 1000.0

            t_blend = time.perf_counter()
            for p in pred:
                p = cv2.resize(p.astype(np.uint8), (x2 - x1, y2 - y1))
                frame = base_frame.copy()
                frame[y1:y2, x1:x2] = p
                if self.debug_overlay:
                    frame = self._add_generated_overlay(frame, "Wav2Lip persistent")
                frames.append(frame)
            blend_ms += (time.perf_counter() - t_blend) * 1000.0
        return frames, {
            "tensor_ms": tensor_ms,
            "model_forward_ms": model_forward_ms,
            "gpu_to_cpu_ms": gpu_to_cpu_ms,
            "blend_ms": blend_ms,
        }

    def _prepare_frames_dir(self, frames_dir: Path) -> None:
        frames_dir.mkdir(parents=True, exist_ok=True)
        for old_frame in frames_dir.glob("frame_*.ppm"):
            old_frame.unlink()

    def _write_ppm(self, path: Path, image_bgr: Any) -> None:
        cv2 = self.cv2
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        h, w = image_rgb.shape[:2]
        with path.open("wb") as fp:
            fp.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
            fp.write(image_rgb.tobytes())

    def _write_frames(self, frames: list[Any], frames_dir: Path) -> None:
        self._prepare_frames_dir(frames_dir)
        for idx, frame in enumerate(frames):
            self._write_ppm(frames_dir / f"frame_{idx:04d}.ppm", frame)

    def _write_mp4(self, frames: list[Any], output_path: Path, fps: float) -> None:
        if not frames:
            raise RuntimeError("no generated frames to write")
        h, w = frames[0].shape[:2]
        writer = self.cv2.VideoWriter(
            str(output_path), self.cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
        )
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()

    def _add_generated_overlay(self, image_bgr: Any, label: str) -> Any:
        cv2 = self.cv2
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


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        service: PersistentWav2LipService = self.server.service  # type: ignore[attr-defined]
        for raw in self.rfile:
            try:
                request = json.loads(raw.decode("utf-8"))
                request_type = request.get("type", "generate")
                if request_type == "ping":
                    response = {
                        "type": "pong",
                        "status": "ok",
                        "wall_time_ms": now_ms(),
                        "server_mode": "persistent",
                        "gpu_info": service.gpu_info(),
                    }
                elif request_type == "generate":
                    response = service.generate(request)
                elif request_type == "set_audio_context":
                    response = service.set_audio_context(request)
                elif request_type == "set_face_context":
                    response = service.set_face_context(request)
                elif request_type == "track_face_context":
                    response = service.track_face_context(request)
                elif request_type == "generate_tail":
                    response = service.generate_tail(request)
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


class PersistentTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


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
    parser.add_argument("--fps", type=float, default=25.0)
    parser.add_argument(
        "--log",
        default="/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/runtime/wav2lip_requests.csv",
    )
    parser.add_argument(
        "--cuda-visible-devices",
        default=os.environ.get("CUDA_VISIBLE_DEVICES"),
        help="CUDA_VISIBLE_DEVICES value before torch import. Example: 0",
    )
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument(
        "--write-mp4",
        action="store_true",
        help="Also write a silent MP4. Native playback only needs PPM frames.",
    )
    parser.add_argument(
        "--no-debug-overlay",
        action="store_true",
        help="Do not add the green generated-frame debug marker.",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip startup dummy detector/model warm-up.",
    )
    parser.add_argument(
        "--warmup-tail-ms",
        type=int,
        default=400,
        help=(
            "Tail duration used to choose the dummy Wav2Lip batch size for "
            "startup CUDA/cuDNN warm-up."
        ),
    )
    parser.add_argument(
        "--warmup-face-path",
        default="",
        help=(
            "Optional static face image to run through face detection and "
            "real-face Wav2Lip warm-up at server startup."
        ),
    )
    parser.add_argument(
        "--warmup-iters",
        type=int,
        default=3,
        help="Number of dummy Wav2Lip forwards to run for each startup warm-up shape.",
    )
    parser.add_argument(
        "--threaded",
        action="store_true",
        help=(
            "Handle each TCP connection in a new Python thread. This is slower "
            "for CUDA inference because every new thread pays CUDA warm-up."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cuda_visible_devices is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices
    logger = RequestLogger(Path(args.log).expanduser())
    try:
        service = PersistentWav2LipService(
            wav2lip_dir=Path(args.wav2lip_dir).expanduser(),
            checkpoint_path=Path(args.checkpoint_path).expanduser(),
            default_fps=args.fps,
            logger=logger,
            require_gpu=args.require_gpu,
            debug_overlay=not args.no_debug_overlay,
            write_mp4=args.write_mp4,
            warmup=not args.no_warmup,
            warmup_tail_ms=args.warmup_tail_ms,
            warmup_face_path=Path(args.warmup_face_path).expanduser()
            if args.warmup_face_path
            else None,
            warmup_iters=args.warmup_iters,
        )
    except Exception as exc:
        print(f"[wav2lip-persistent] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    server_cls = ThreadedTCPServer if args.threaded else PersistentTCPServer
    with server_cls((args.host, args.port), Handler) as server:
        server.service = service  # type: ignore[attr-defined]
        print(f"[wav2lip-persistent] listening on {args.host}:{args.port}", flush=True)
        print(f"[wav2lip-persistent] wav2lip_dir={service.wav2lip_dir}", flush=True)
        print(f"[wav2lip-persistent] checkpoint={service.checkpoint_path}", flush=True)
        print(
            "[wav2lip-persistent] gpu_info="
            f"{json.dumps(service.gpu_info(), sort_keys=True)}",
            flush=True,
        )
        print(f"[wav2lip-persistent] warmup_ms={service.warmup_ms}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
