#!/usr/bin/env python3
"""Patch WebRTC receiver-side RTCP feedback logging into a src checkout."""

from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"could not find patch target in {path}: {old[:120]!r}")
    return text.replace(old, new, 1)


def patch_rtp_video_stream_receiver(src_root: Path) -> bool:
    path = src_root / "video" / "rtp_video_stream_receiver2.cc"
    text = path.read_text(encoding="utf-8")
    original = text

    text = replace_once(
        text,
        '#include "rtc_base/checks.h"\n',
        '#include "rtc_base/checks.h"\n'
        '#include "rtc_base/csv_log.h"\n',
        path,
    )

    text = replace_once(
        text,
        "void RtpVideoStreamReceiver2::RequestKeyFrame() {\n"
        "  RTC_DCHECK_RUN_ON(&worker_task_checker_);\n"
        "  // TODO(bugs.webrtc.org/10336): Allow the sender to ignore key frame requests\n"
        "  // issued by anything other than the LossNotificationController if it (the\n"
        "  // sender) is relying on LNTF alone.\n"
        "  if (keyframe_request_method_ == KeyFrameReqMethod::kPliRtcp) {\n"
        "    rtp_rtcp_->SendPictureLossIndication();\n"
        "  } else if (keyframe_request_method_ == KeyFrameReqMethod::kFirRtcp) {\n"
        "    rtp_rtcp_->SendFullIntraRequest();\n"
        "  }\n"
        "}\n",
        "void RtpVideoStreamReceiver2::RequestKeyFrame() {\n"
        "  RTC_DCHECK_RUN_ON(&worker_task_checker_);\n"
        "  // TODO(bugs.webrtc.org/10336): Allow the sender to ignore key frame requests\n"
        "  // issued by anything other than the LossNotificationController if it (the\n"
        "  // sender) is relying on LNTF alone.\n"
        "  const bool use_pli =\n"
        "      keyframe_request_method_ == KeyFrameReqMethod::kPliRtcp;\n"
        "  const bool use_fir =\n"
        "      keyframe_request_method_ == KeyFrameReqMethod::kFirRtcp;\n"
        "  if (use_pli) {\n"
        "    rtp_rtcp_->SendPictureLossIndication();\n"
        "  } else if (use_fir) {\n"
        "    rtp_rtcp_->SendFullIntraRequest();\n"
        "  }\n"
        "\n"
        "  static FILE* rtcp_feedback_log = rtc::OpenCsvLog(\n"
        "      \"rtcp_feedback.csv\",\n"
        "      \"timestamp_ms,event,media_ssrc,packet_count,first_seq,last_seq,buffering_allowed,decodability_flag,last_decoded_seq,last_received_seq\\n\");\n"
        "  if (rtcp_feedback_log && (use_pli || use_fir)) {\n"
        "    fprintf(rtcp_feedback_log, \"%lld,%s,%u,0,-1,-1,-1,-1,-1,-1\\n\",\n"
        "            static_cast<long long>(env_.clock().CurrentTime().ms()),\n"
        "            use_pli ? \"PLI\" : \"FIR\",\n"
        "            static_cast<unsigned>(config_.rtp.remote_ssrc));\n"
        "  }\n"
        "}\n",
        path,
    )

    text = replace_once(
        text,
        "void RtpVideoStreamReceiver2::SendNack(\n"
        "    const std::vector<uint16_t>& sequence_numbers,\n"
        "    bool /*buffering_allowed*/) {\n"
        "  rtp_rtcp_->SendNack(sequence_numbers);\n"
        "}\n",
        "void RtpVideoStreamReceiver2::SendNack(\n"
        "    const std::vector<uint16_t>& sequence_numbers,\n"
        "    bool buffering_allowed) {\n"
        "  rtp_rtcp_->SendNack(sequence_numbers);\n"
        "\n"
        "  static FILE* rtcp_feedback_log = rtc::OpenCsvLog(\n"
        "      \"rtcp_feedback.csv\",\n"
        "      \"timestamp_ms,event,media_ssrc,packet_count,first_seq,last_seq,buffering_allowed,decodability_flag,last_decoded_seq,last_received_seq\\n\");\n"
        "  if (rtcp_feedback_log && !sequence_numbers.empty()) {\n"
        "    fprintf(rtcp_feedback_log, \"%lld,NACK,%u,%zu,%u,%u,%d,-1,-1,-1\\n\",\n"
        "            static_cast<long long>(env_.clock().CurrentTime().ms()),\n"
        "            static_cast<unsigned>(config_.rtp.remote_ssrc),\n"
        "            sequence_numbers.size(),\n"
        "            static_cast<unsigned>(sequence_numbers.front()),\n"
        "            static_cast<unsigned>(sequence_numbers.back()),\n"
        "            buffering_allowed ? 1 : 0);\n"
        "  }\n"
        "}\n",
        path,
    )

    text = replace_once(
        text,
        "void RtpVideoStreamReceiver2::SendLossNotification(\n"
        "    uint16_t last_decoded_seq_num,\n"
        "    uint16_t last_received_seq_num,\n"
        "    bool decodability_flag,\n"
        "    bool buffering_allowed) {\n"
        "  RTC_DCHECK(config_.rtp.lntf.enabled);\n"
        "  rtp_rtcp_->SendLossNotification(last_decoded_seq_num, last_received_seq_num,\n"
        "                                  decodability_flag, buffering_allowed);\n"
        "}\n",
        "void RtpVideoStreamReceiver2::SendLossNotification(\n"
        "    uint16_t last_decoded_seq_num,\n"
        "    uint16_t last_received_seq_num,\n"
        "    bool decodability_flag,\n"
        "    bool buffering_allowed) {\n"
        "  RTC_DCHECK(config_.rtp.lntf.enabled);\n"
        "  rtp_rtcp_->SendLossNotification(last_decoded_seq_num, last_received_seq_num,\n"
        "                                  decodability_flag, buffering_allowed);\n"
        "\n"
        "  static FILE* rtcp_feedback_log = rtc::OpenCsvLog(\n"
        "      \"rtcp_feedback.csv\",\n"
        "      \"timestamp_ms,event,media_ssrc,packet_count,first_seq,last_seq,buffering_allowed,decodability_flag,last_decoded_seq,last_received_seq\\n\");\n"
        "  if (rtcp_feedback_log) {\n"
        "    fprintf(rtcp_feedback_log, \"%lld,LNTF,%u,0,-1,-1,%d,%d,%u,%u\\n\",\n"
        "            static_cast<long long>(env_.clock().CurrentTime().ms()),\n"
        "            static_cast<unsigned>(config_.rtp.remote_ssrc),\n"
        "            buffering_allowed ? 1 : 0,\n"
        "            decodability_flag ? 1 : 0,\n"
        "            static_cast<unsigned>(last_decoded_seq_num),\n"
        "            static_cast<unsigned>(last_received_seq_num));\n"
        "  }\n"
        "}\n",
        path,
    )

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src-root", required=True)
    args = parser.parse_args()
    src_root = Path(args.src_root).expanduser().resolve()
    changed = patch_rtp_video_stream_receiver(src_root)
    print(f"[rtcp-feedback-logging] src_root={src_root}")
    print(f"[rtcp-feedback-logging] patched={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
