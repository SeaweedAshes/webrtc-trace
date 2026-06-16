#!/usr/bin/env python3
"""Apply playout-headroom prefetch signal patches to a WebRTC checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


SOURCE_LINES = [
    '        "peerconnection/client/playout_headroom_signal.cc",',
    '        "peerconnection/client/playout_headroom_signal.h",',
]


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"patch target not found in {path}: {old[:100]!r}")
    return text.replace(old, new, 1)


def insert_after_once(text: str, marker: str, insertion: str, path: Path) -> str:
    if insertion.strip() in text:
        return text
    if marker not in text:
        raise RuntimeError(f"insertion marker not found in {path}: {marker!r}")
    return text.replace(marker, marker + insertion, 1)


def write_if_changed(path: Path, text: str) -> bool:
    old = path.read_text(encoding="utf-8")
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def copy_signal_files(lab_dir: Path, src_dir: Path) -> None:
    rels = [
        Path("examples/peerconnection/client/playout_headroom_signal.cc"),
        Path("examples/peerconnection/client/playout_headroom_signal.h"),
    ]
    for rel in rels:
        src = lab_dir / "native_overlay" / rel
        dst = src_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def patch_build_gn(src_dir: Path) -> bool:
    path = src_dir / "examples" / "BUILD.gn"
    text = path.read_text(encoding="utf-8")
    missing = [line for line in SOURCE_LINES if line not in text]
    if not missing:
        return False
    marker = '        "peerconnection/client/wav2lip_bridge.h",\n'
    insertion = "\n".join(missing) + "\n"
    text = insert_after_once(text, marker, insertion, path)
    return write_if_changed(path, text)


def patch_video_stream_buffer_controller(src_dir: Path) -> bool:
    path = src_dir / "video" / "video_stream_buffer_controller.cc"
    text = path.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        '#include "video/frame_decode_timing.h"\n',
        '#include "examples/peerconnection/client/playout_headroom_signal.h"\n',
        path,
    )
    text = replace_once(
        text,
        "  auto decodable_tu_info = buffer_->DecodableTemporalUnitsInfo();\n"
        "  if (!decoder_ready_for_new_frame_ || !decodable_tu_info) {\n"
        "    return;\n"
        "  }\n",
        "  auto decodable_tu_info = buffer_->DecodableTemporalUnitsInfo();\n"
        "  if (!decoder_ready_for_new_frame_ || !decodable_tu_info) {\n"
        "    if (decoder_ready_for_new_frame_) {\n"
        "      webrtc_wav2lip::UpdateReceiverPlayoutHeadroom(\n"
        "          clock_->CurrentTime().ms(), 0, buffer_->CurrentSize(), -1);\n"
        "    }\n"
        "    return;\n"
        "  }\n",
        path,
    )
    text = replace_once(
        text,
        "    if (schedule) {\n"
        "      // Don't schedule if already waiting for the same frame.\n",
        "    if (schedule) {\n"
        "      const auto timings = timing_->GetTimings();\n"
        "      const int64_t render_time_ms = schedule->render_time.ms_or(-1);\n"
        "      const int64_t release_deadline_ms =\n"
        "          render_time_ms >= 0 ? render_time_ms - timings.render_delay.ms()\n"
        "                              : clock_->CurrentTime().ms();\n"
        "      const int64_t headroom_ms = std::max<int64_t>(\n"
        "          0, release_deadline_ms - clock_->CurrentTime().ms());\n"
        "      webrtc_wav2lip::UpdateReceiverPlayoutHeadroom(\n"
        "          clock_->CurrentTime().ms(), headroom_ms, buffer_->CurrentSize(),\n"
        "          decodable_tu_info->next_rtp_timestamp);\n"
        "      // Don't schedule if already waiting for the same frame.\n",
        path,
    )
    return write_if_changed(path, text)


def patch_main_wnd(src_dir: Path) -> bool:
    path = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.cc"
    text = path.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        '#include "examples/peerconnection/client/wav2lip_bridge.h"\n',
        '#include "examples/peerconnection/client/playout_headroom_signal.h"\n',
        path,
    )
    text = replace_once(
        text,
        "  const bool switch_due = render_gap_ms >= switch_gap_ms;\n"
        "  const bool risk_due = render_gap_ms >= generation_risk_ms;\n"
        "  bool displayed_generated = false;\n",
        "  const bool switch_due = render_gap_ms >= switch_gap_ms;\n"
        "  const bool risk_due = render_gap_ms >= generation_risk_ms;\n"
        "  const int headroom_threshold_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_PREFETCH_MS\", -1);\n"
        "  const int headroom_stale_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_PLAYOUT_HEADROOM_STALE_MS\", 100);\n"
        "  const auto headroom = webrtc_wav2lip::GetReceiverPlayoutHeadroom();\n"
        "  const bool headroom_fresh =\n"
        "      headroom.updated_ms > 0 && now_ms >= headroom.updated_ms &&\n"
        "      now_ms - headroom.updated_ms <= headroom_stale_ms;\n"
        "  const bool headroom_due =\n"
        "      headroom_threshold_ms >= 0 && headroom_fresh &&\n"
        "      headroom.headroom_ms <= headroom_threshold_ms;\n"
        "  bool displayed_generated = false;\n",
        path,
    )
    text = replace_once(
        text,
        "  if (!risk_due && !switch_due) {\n",
        "  if (!risk_due && !switch_due && !headroom_due) {\n",
        path,
    )
    text = replace_once(
        text,
        "  const char* reason = \"playout_risk_prefetch\";\n"
        "  if (displayed_generated) {\n",
        "  const char* reason = \"playout_risk_prefetch\";\n"
        "  if (headroom_due && !risk_due && !switch_due) {\n"
        "    reason = \"playout_headroom_prefetch\";\n"
        "  }\n"
        "  if (displayed_generated) {\n",
        path,
    )
    return write_if_changed(path, text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab-dir", default="/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab")
    parser.add_argument("--src-dir", default="/home/widen/webrtc-receiver-wav2lip-native/src")
    args = parser.parse_args()

    lab_dir = Path(args.lab_dir).expanduser().resolve()
    src_dir = Path(args.src_dir).expanduser().resolve()
    copy_signal_files(lab_dir, src_dir)
    changed = {
        "BUILD.gn": patch_build_gn(src_dir),
        "video_stream_buffer_controller.cc": patch_video_stream_buffer_controller(src_dir),
        "main_wnd.cc": patch_main_wnd(src_dir),
    }
    for name, did_change in changed.items():
        print(f"{name}: {'changed' if did_change else 'already up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
