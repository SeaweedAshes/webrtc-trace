#!/usr/bin/env python3
"""Apply the Wav2Lip native overlay to a WebRTC src checkout."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


LINUX_SOURCE_LINES = [
    '        "peerconnection/client/concealment_policy.cc",',
    '        "peerconnection/client/concealment_policy.h",',
    '        "peerconnection/client/frame_image_writer.cc",',
    '        "peerconnection/client/frame_image_writer.h",',
    '        "peerconnection/client/generated_frame_reader.cc",',
    '        "peerconnection/client/generated_frame_reader.h",',
    '        "peerconnection/client/recent_audio_buffer.cc",',
    '        "peerconnection/client/recent_audio_buffer.h",',
    '        "peerconnection/client/wav_writer.cc",',
    '        "peerconnection/client/wav_writer.h",',
    '        "peerconnection/client/wav2lip_bridge.cc",',
    '        "peerconnection/client/wav2lip_bridge.h",',
]


def copy_overlay(lab_dir: Path, src_dir: Path) -> None:
    overlay = lab_dir / "native_overlay"
    if not overlay.exists():
        raise FileNotFoundError(f"native overlay not found: {overlay}")
    for item in overlay.rglob("*"):
        rel = item.relative_to(overlay)
        dest = src_dir / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def patch_build_gn(src_dir: Path) -> bool:
    build_gn = src_dir / "examples" / "BUILD.gn"
    text = build_gn.read_text(encoding="utf-8")
    missing = [line for line in LINUX_SOURCE_LINES if line not in text]
    if not missing:
        return False
    marker = '        "peerconnection/client/linux/main_wnd.h",\n'
    if marker not in text:
        raise RuntimeError(f"could not find Linux source marker in {build_gn}")
    insertion = "\n".join(missing) + "\n"
    text = text.replace(marker, marker + insertion)
    build_gn.write_text(text, encoding="utf-8")
    return True


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"could not find patch target in {path}: {old[:80]!r}")
    return text.replace(old, new, 1)


def replace_once_if_missing(
    text: str, old: str, new: str, marker: str, path: Path
) -> str:
    if marker in text:
        return text
    return replace_once(text, old, new, path)


def insert_after_once(text: str, marker: str, insertion: str, path: Path) -> str:
    if insertion.strip() in text:
        return text
    if marker not in text:
        raise RuntimeError(f"could not find insertion marker in {path}: {marker!r}")
    return text.replace(marker, marker + insertion, 1)


def write_if_changed(path: Path, text: str) -> bool:
    old = path.read_text(encoding="utf-8")
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def patch_main_wnd_interface(src_dir: Path) -> bool:
    path = src_dir / "examples" / "peerconnection" / "client" / "main_wnd.h"
    text = path.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        '#include "examples/peerconnection/client/peer_connection_client.h"\n',
        '#include "examples/peerconnection/client/recent_audio_buffer.h"\n',
        path,
    )
    text = replace_once(
        text,
        "  virtual void StopRemoteRenderer() = 0;\n\n"
        "  virtual void QueueUIThreadCallback(int msg_id, void* data) = 0;\n",
        "  virtual void StopRemoteRenderer() = 0;\n"
        "  virtual void SetRemoteAudioBuffer(\n"
        "      std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio) = 0;\n\n"
        "  virtual void QueueUIThreadCallback(int msg_id, void* data) = 0;\n",
        path,
    )
    return write_if_changed(path, text)


def patch_conductor(src_dir: Path) -> bool:
    changed = False
    header = src_dir / "examples" / "peerconnection" / "client" / "conductor.h"
    text = header.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        '#include "examples/peerconnection/client/main_wnd.h"\n',
        '#include "examples/peerconnection/client/recent_audio_buffer.h"\n',
        header,
    )
    text = replace_once(
        text,
        "  PeerConnectionClient* client_;\n"
        "  MainWindow* main_wnd_;\n"
        "  std::deque<std::string*> pending_messages_;\n",
        "  PeerConnectionClient* client_;\n"
        "  MainWindow* main_wnd_;\n"
        "  std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio_buffer_;\n"
        "  webrtc::scoped_refptr<webrtc::AudioTrackInterface> remote_audio_track_;\n"
        "  std::deque<std::string*> pending_messages_;\n",
        header,
    )
    changed |= write_if_changed(header, text)

    source = src_dir / "examples" / "peerconnection" / "client" / "conductor.cc"
    text = source.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "      env_(env),\n"
        "      client_(client),\n"
        "      main_wnd_(main_wnd) {\n"
        "  client_->RegisterObserver(this);\n"
        "  main_wnd->RegisterObserver(this);\n"
        "}\n",
        "      env_(env),\n"
        "      client_(client),\n"
        "      main_wnd_(main_wnd),\n"
        "      remote_audio_buffer_(\n"
        "          std::make_shared<webrtc_wav2lip::RecentAudioBuffer>(5000)) {\n"
        "  client_->RegisterObserver(this);\n"
        "  main_wnd->RegisterObserver(this);\n"
        "  main_wnd_->SetRemoteAudioBuffer(remote_audio_buffer_);\n"
        "}\n",
        source,
    )
    text = replace_once(
        text,
        "  main_wnd_->StopLocalRenderer();\n"
        "  main_wnd_->StopRemoteRenderer();\n"
        "  peer_connection_ = nullptr;\n",
        "  main_wnd_->StopLocalRenderer();\n"
        "  main_wnd_->StopRemoteRenderer();\n"
        "  if (remote_audio_track_ && remote_audio_buffer_) {\n"
        "    remote_audio_track_->RemoveSink(remote_audio_buffer_.get());\n"
        "  }\n"
        "  remote_audio_track_ = nullptr;\n"
        "  main_wnd_->SetRemoteAudioBuffer(nullptr);\n"
        "  peer_connection_ = nullptr;\n",
        source,
    )
    text = replace_once(
        text,
        "    case NEW_TRACK_ADDED: {\n"
        "      auto* track = reinterpret_cast<webrtc::MediaStreamTrackInterface*>(data);\n"
        "      if (track->kind() == webrtc::MediaStreamTrackInterface::kVideoKind) {\n"
        "        auto* video_track = static_cast<webrtc::VideoTrackInterface*>(track);\n"
        "        main_wnd_->StartRemoteRenderer(video_track);\n"
        "      }\n"
        "      track->Release();\n"
        "      break;\n"
        "    }\n",
        "    case NEW_TRACK_ADDED: {\n"
        "      auto* track = reinterpret_cast<webrtc::MediaStreamTrackInterface*>(data);\n"
        "      if (track->kind() == webrtc::MediaStreamTrackInterface::kVideoKind) {\n"
        "        auto* video_track = static_cast<webrtc::VideoTrackInterface*>(track);\n"
        "        main_wnd_->StartRemoteRenderer(video_track);\n"
        "      } else if (track->kind() ==\n"
        "                 webrtc::MediaStreamTrackInterface::kAudioKind) {\n"
        "        auto* audio_track = static_cast<webrtc::AudioTrackInterface*>(track);\n"
        "        if (remote_audio_track_ && remote_audio_buffer_) {\n"
        "          remote_audio_track_->RemoveSink(remote_audio_buffer_.get());\n"
        "        }\n"
        "        remote_audio_track_ = audio_track;\n"
        "        if (remote_audio_buffer_) {\n"
        "          remote_audio_track_->AddSink(remote_audio_buffer_.get());\n"
        "          main_wnd_->SetRemoteAudioBuffer(remote_audio_buffer_);\n"
        "          RTC_LOG(LS_INFO) << \"Wav2Lip remote audio sink attached\";\n"
        "        }\n"
        "      }\n"
        "      track->Release();\n"
        "      break;\n"
        "    }\n",
        source,
    )
    text = replace_once(
        text,
        "    case TRACK_REMOVED: {\n"
        "      // Remote peer stopped sending a track.\n"
        "      auto* track = reinterpret_cast<webrtc::MediaStreamTrackInterface*>(data);\n"
        "      // Ensure we detach our renderer before releasing the track to avoid\n"
        "      // referencing a destroyed track from the renderer.\n"
        "      main_wnd_->StopRemoteRenderer();\n"
        "      track->Release();\n"
        "      break;\n"
        "    }\n",
        "    case TRACK_REMOVED: {\n"
        "      // Remote peer stopped sending a track.\n"
        "      auto* track = reinterpret_cast<webrtc::MediaStreamTrackInterface*>(data);\n"
        "      if (track->kind() == webrtc::MediaStreamTrackInterface::kVideoKind) {\n"
        "        // Ensure we detach our renderer before releasing the track to avoid\n"
        "        // referencing a destroyed track from the renderer.\n"
        "        main_wnd_->StopRemoteRenderer();\n"
        "      } else if (track->kind() ==\n"
        "                 webrtc::MediaStreamTrackInterface::kAudioKind) {\n"
        "        if (remote_audio_track_ && remote_audio_buffer_ &&\n"
        "            remote_audio_track_.get() == track) {\n"
        "          remote_audio_track_->RemoveSink(remote_audio_buffer_.get());\n"
        "          remote_audio_track_ = nullptr;\n"
        "          main_wnd_->SetRemoteAudioBuffer(nullptr);\n"
        "          RTC_LOG(LS_INFO) << \"Wav2Lip remote audio sink detached\";\n"
        "        }\n"
        "      }\n"
        "      track->Release();\n"
        "      break;\n"
        "    }\n",
        source,
    )
    changed |= write_if_changed(source, text)
    return changed


def patch_linux_main_wnd(src_dir: Path) -> bool:
    changed = False
    header = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.h"
    text = header.read_text(encoding="utf-8")
    text = insert_after_once(text, "#include <stdint.h>\n\n", "#include <atomic>\n", header)
    text = insert_after_once(text, "#include <string>\n", "#include <vector>\n", header)
    text = replace_once_if_missing(
        text,
        "  void StartRemoteRenderer(webrtc::VideoTrackInterface* remote_video) override;\n"
        "  void StopRemoteRenderer() override;\n",
        "  void StartRemoteRenderer(webrtc::VideoTrackInterface* remote_video) override;\n"
        "  void StopRemoteRenderer() override;\n"
        "  void SetRemoteAudioBuffer(\n"
        "      std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio) override;\n",
        "SetRemoteAudioBuffer",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    VideoRenderer(GtkMainWnd* main_wnd,\n"
        "                  webrtc::VideoTrackInterface* track_to_render);\n",
        "    VideoRenderer(GtkMainWnd* main_wnd,\n"
        "                  webrtc::VideoTrackInterface* track_to_render,\n"
        "                  bool is_remote,\n"
        "                  std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer>\n"
        "                      remote_audio_buffer);\n",
        "                  bool is_remote,",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    std::span<const uint8_t> image() const { return image_; }\n\n"
        "    int width() const { return width_; }\n",
        "    std::span<const uint8_t> image() const { return image_; }\n\n"
        "    void SetAudioBuffer(\n"
        "        std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio);\n\n"
        "    bool OnRenderGapTimer();\n\n"
        "    static int RenderGapMonitor(void* data);\n\n"
        "    int width() const { return width_; }\n",
        "SetAudioBuffer",
        header,
    )
    text = replace_once_if_missing(
        text,
        "   protected:\n"
        "    void SetSize(int width, int height);\n",
        "   protected:\n"
        "    void SetSize(int width, int height);\n"
        "    void MaybeStartWav2LipGeneration(int64_t render_gap_ms,\n"
        "                                      const char* reason,\n"
        "                                      std::vector<uint8_t> argb_image,\n"
        "                                      int width,\n"
        "                                      int height);\n",
        "MaybeStartWav2LipGeneration",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    GtkMainWnd* main_wnd_;\n"
        "    webrtc::scoped_refptr<webrtc::VideoTrackInterface> rendered_track_;\n",
        "    GtkMainWnd* main_wnd_;\n"
        "    webrtc::scoped_refptr<webrtc::VideoTrackInterface> rendered_track_;\n"
        "    bool is_remote_ = false;\n"
        "    std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio_buffer_;\n"
        "    std::shared_ptr<std::atomic<bool>> generation_inflight_;\n"
        "    std::atomic<int64_t> last_frame_ms_{0};\n"
        "    std::atomic<int64_t> last_generation_trigger_ms_{0};\n"
        "    std::atomic<int> generation_sequence_{0};\n"
        "    unsigned int monitor_source_id_ = 0;\n",
        "generation_inflight_",
        header,
    )
    method_block = (
        "    void MaybeStartWav2LipGeneration(int64_t render_gap_ms,\n"
        "                                      const char* reason,\n"
        "                                      std::vector<uint8_t> argb_image,\n"
        "                                      int width,\n"
        "                                      int height);\n"
    )
    member_block = (
        "    bool is_remote_ = false;\n"
        "    std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio_buffer_;\n"
        "    std::shared_ptr<std::atomic<bool>> generation_inflight_;\n"
        "    std::atomic<int64_t> last_frame_ms_{0};\n"
        "    std::atomic<int64_t> last_generation_trigger_ms_{0};\n"
        "    std::atomic<int> generation_sequence_{0};\n"
        "    unsigned int monitor_source_id_ = 0;\n"
    )
    remote_audio_method = (
        "  void SetRemoteAudioBuffer(\n"
        "      std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio) override;\n"
    )
    while remote_audio_method + remote_audio_method in text:
        text = text.replace(
            remote_audio_method + remote_audio_method, remote_audio_method
        )
    while method_block + method_block in text:
        text = text.replace(method_block + method_block, method_block)
    while member_block + member_block in text:
        text = text.replace(member_block + member_block, member_block)
    main_window_audio_member = (
        "  std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio_buffer_;\n"
    )
    while main_window_audio_member + main_window_audio_member in text:
        text = text.replace(
            main_window_audio_member + main_window_audio_member,
            main_window_audio_member,
        )
    text = replace_once_if_missing(
        text,
        "  std::unique_ptr<VideoRenderer> local_renderer_;\n"
        "  std::unique_ptr<VideoRenderer> remote_renderer_;\n",
        "  std::unique_ptr<VideoRenderer> local_renderer_;\n"
        "  std::unique_ptr<VideoRenderer> remote_renderer_;\n"
        "  std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio_buffer_;\n",
        "  std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio_buffer_;\n"
        "  int width_ = 0;",
        header,
    )
    changed |= write_if_changed(header, text)

    source = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.cc"
    text = source.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        "#include <cstring>\n",
        "#include <chrono>\n",
        source,
    )
    text = insert_after_once(text, "#include <chrono>\n", "#include <filesystem>\n", source)
    text = insert_after_once(text, "#include <filesystem>\n", "#include <system_error>\n", source)
    text = insert_after_once(text, "#include <system_error>\n", "#include <thread>\n", source)
    text = insert_after_once(text, "#include <thread>\n", "#include <utility>\n", source)
    text = insert_after_once(text, "#include <utility>\n", "#include <vector>\n", source)
    text = insert_after_once(
        text,
        '#include "examples/peerconnection/client/peer_connection_client.h"\n',
        '#include "examples/peerconnection/client/frame_image_writer.h"\n'
        '#include "examples/peerconnection/client/recent_audio_buffer.h"\n'
        '#include "examples/peerconnection/client/wav2lip_bridge.h"\n',
        source,
    )
    if "int64_t NowMillis()" not in text:
        text = insert_after_once(
            text,
            "gboolean Draw(GtkWidget* widget, cairo_t* cr, gpointer data) {\n"
            "  GtkMainWnd* wnd = reinterpret_cast<GtkMainWnd*>(data);\n"
            "  wnd->Draw(widget, cr);\n"
            "  return false;\n"
            "}\n\n",
            "constexpr int kWav2LipMinBufferedAudioMs = 250;\n\n"
            "int64_t NowMillis() {\n"
            "  return std::chrono::duration_cast<std::chrono::milliseconds>(\n"
            "             std::chrono::steady_clock::now().time_since_epoch())\n"
            "      .count();\n"
            "}\n\n"
            "std::string EnvString(const char* name, const char* fallback) {\n"
            "  const char* value = std::getenv(name);\n"
            "  if (!value || value[0] == '\\0') {\n"
            "    return fallback;\n"
            "  }\n"
            "  return value;\n"
            "}\n\n"
            "int EnvInt(const char* name, int fallback) {\n"
            "  const char* value = std::getenv(name);\n"
            "  if (!value || value[0] == '\\0') {\n"
            "    return fallback;\n"
            "  }\n"
            "  char* end = nullptr;\n"
            "  long parsed = std::strtol(value, &end, 10);\n"
            "  if (end == value || *end != '\\0') {\n"
            "    return fallback;\n"
            "  }\n"
            "  return static_cast<int>(parsed);\n"
            "}\n\n",
            source,
        )
    text = replace_once(
        text,
        "void GtkMainWnd::StartLocalRenderer(webrtc::VideoTrackInterface* local_video) {\n"
        "  local_renderer_.reset(new VideoRenderer(this, local_video));\n"
        "}\n",
        "void GtkMainWnd::StartLocalRenderer(webrtc::VideoTrackInterface* local_video) {\n"
        "  local_renderer_.reset(new VideoRenderer(this, local_video, false, nullptr));\n"
        "}\n",
        source,
    )
    text = replace_once(
        text,
        "void GtkMainWnd::StartRemoteRenderer(\n"
        "    webrtc::VideoTrackInterface* remote_video) {\n"
        "  remote_renderer_.reset(new VideoRenderer(this, remote_video));\n"
        "}\n\n"
        "void GtkMainWnd::StopRemoteRenderer() {\n"
        "  remote_renderer_.reset();\n"
        "}\n",
        "void GtkMainWnd::StartRemoteRenderer(\n"
        "    webrtc::VideoTrackInterface* remote_video) {\n"
        "  remote_renderer_.reset(\n"
        "      new VideoRenderer(this, remote_video, true, remote_audio_buffer_));\n"
        "}\n\n"
        "void GtkMainWnd::StopRemoteRenderer() {\n"
        "  remote_renderer_.reset();\n"
        "}\n\n"
        "void GtkMainWnd::SetRemoteAudioBuffer(\n"
        "    std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio) {\n"
        "  remote_audio_buffer_ = std::move(remote_audio);\n"
        "  if (remote_renderer_) {\n"
        "    remote_renderer_->SetAudioBuffer(remote_audio_buffer_);\n"
        "  }\n"
        "}\n",
        source,
    )
    text = replace_once_if_missing(
        text,
        "GtkMainWnd::VideoRenderer::VideoRenderer(\n"
        "    GtkMainWnd* main_wnd,\n"
        "    webrtc::VideoTrackInterface* track_to_render)\n"
        "    : width_(0),\n"
        "      height_(0),\n"
        "      main_wnd_(main_wnd),\n"
        "      rendered_track_(track_to_render) {\n"
        "  rendered_track_->AddOrUpdateSink(this, webrtc::VideoSinkWants());\n"
        "}\n\n"
        "GtkMainWnd::VideoRenderer::~VideoRenderer() {\n"
        "  rendered_track_->RemoveSink(this);\n"
        "}\n",
        "GtkMainWnd::VideoRenderer::VideoRenderer(\n"
        "    GtkMainWnd* main_wnd,\n"
        "    webrtc::VideoTrackInterface* track_to_render,\n"
        "    bool is_remote,\n"
        "    std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer>\n"
        "        remote_audio_buffer)\n"
        "    : width_(0),\n"
        "      height_(0),\n"
        "      main_wnd_(main_wnd),\n"
        "      rendered_track_(track_to_render),\n"
        "      is_remote_(is_remote),\n"
        "      remote_audio_buffer_(std::move(remote_audio_buffer)),\n"
        "      generation_inflight_(std::make_shared<std::atomic<bool>>(false)) {\n"
        "  rendered_track_->AddOrUpdateSink(this, webrtc::VideoSinkWants());\n"
        "  if (is_remote_) {\n"
        "    monitor_source_id_ = g_timeout_add(\n"
        "        50, &GtkMainWnd::VideoRenderer::RenderGapMonitor, this);\n"
        "  }\n"
        "}\n\n"
        "GtkMainWnd::VideoRenderer::~VideoRenderer() {\n"
        "  if (monitor_source_id_ != 0) {\n"
        "    g_source_remove(monitor_source_id_);\n"
        "    monitor_source_id_ = 0;\n"
        "  }\n"
        "  rendered_track_->RemoveSink(this);\n"
        "}\n",
        "    bool is_remote,",
        source,
    )
    if "void GtkMainWnd::VideoRenderer::SetAudioBuffer(" not in text:
        text = insert_after_once(
            text,
            "GtkMainWnd::VideoRenderer::~VideoRenderer() {\n"
            "  if (monitor_source_id_ != 0) {\n"
            "    g_source_remove(monitor_source_id_);\n"
            "    monitor_source_id_ = 0;\n"
            "  }\n"
            "  rendered_track_->RemoveSink(this);\n"
            "}\n\n",
            "void GtkMainWnd::VideoRenderer::SetAudioBuffer(\n"
        "    std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio) {\n"
        "  remote_audio_buffer_ = std::move(remote_audio);\n"
        "}\n\n"
        "int GtkMainWnd::VideoRenderer::RenderGapMonitor(void* data) {\n"
        "  return reinterpret_cast<GtkMainWnd::VideoRenderer*>(data)\n"
        "      ->OnRenderGapTimer();\n"
        "}\n\n"
        "bool GtkMainWnd::VideoRenderer::OnRenderGapTimer() {\n"
        "  if (!is_remote_) {\n"
        "    return false;\n"
        "  }\n"
        "  const int64_t last_frame_ms = last_frame_ms_.load(std::memory_order_relaxed);\n"
        "  if (last_frame_ms == 0) {\n"
        "    return true;\n"
        "  }\n"
        "  const int64_t now_ms = NowMillis();\n"
        "  const int64_t render_gap_ms = now_ms - last_frame_ms;\n"
        "  if (render_gap_ms < kWav2LipRenderGapTriggerMs) {\n"
        "    return true;\n"
        "  }\n"
        "  const int64_t last_generation_ms =\n"
        "      last_generation_trigger_ms_.load(std::memory_order_relaxed);\n"
        "  if (now_ms - last_generation_ms < kWav2LipGenerationCooldownMs) {\n"
        "    return true;\n"
        "  }\n\n"
        "  std::vector<uint8_t> argb_image;\n"
        "  int frame_width = 0;\n"
        "  int frame_height = 0;\n"
        "  gdk_threads_enter();\n"
        "  if (!image_.empty() && width_ > 0 && height_ > 0) {\n"
        "    argb_image.resize(image_.size());\n"
        "    std::memcpy(argb_image.data(), image_.data(), image_.size());\n"
        "    frame_width = width_;\n"
        "    frame_height = height_;\n"
        "  }\n"
        "  gdk_threads_leave();\n\n"
        "  MaybeStartWav2LipGeneration(render_gap_ms, \"render_gap_timer\",\n"
        "                              std::move(argb_image), frame_width,\n"
        "                              frame_height);\n"
        "  return true;\n"
        "}\n\n"
        "void GtkMainWnd::VideoRenderer::MaybeStartWav2LipGeneration(\n"
        "    int64_t render_gap_ms,\n"
        "    const char* reason,\n"
        "    std::vector<uint8_t> argb_image,\n"
        "    int frame_width,\n"
        "    int frame_height) {\n"
        "  if (!is_remote_ || argb_image.empty() || frame_width <= 0 ||\n"
        "      frame_height <= 0) {\n"
        "    return;\n"
        "  }\n"
        "  auto audio_buffer = remote_audio_buffer_;\n"
        "  if (!audio_buffer) {\n"
        "    RTC_LOG(LS_WARNING) << \"Wav2Lip trigger skipped: no remote audio buffer\";\n"
        "    return;\n"
        "  }\n"
        "  const int buffered_ms = audio_buffer->buffered_ms();\n"
        "  if (buffered_ms < kWav2LipMinBufferedAudioMs) {\n"
        "    RTC_LOG(LS_WARNING) << \"Wav2Lip trigger skipped: only \" << buffered_ms\n"
        "                        << \" ms audio buffered\";\n"
        "    return;\n"
        "  }\n"
        "  bool expected = false;\n"
        "  if (!generation_inflight_->compare_exchange_strong(expected, true)) {\n"
        "    return;\n"
        "  }\n"
        "  const int64_t trigger_ms = NowMillis();\n"
        "  last_generation_trigger_ms_.store(trigger_ms, std::memory_order_relaxed);\n"
        "  const int sequence = generation_sequence_.fetch_add(1) + 1;\n"
        "  auto inflight = generation_inflight_;\n\n"
        "  std::thread([audio_buffer, argb_image = std::move(argb_image), frame_width,\n"
        "               frame_height, render_gap_ms, reason = std::string(reason),\n"
        "               sequence, trigger_ms, inflight]() mutable {\n"
        "    const std::filesystem::path runtime_dir = EnvString(\n"
        "        \"WEBRTC_WAV2LIP_RUNTIME_DIR\",\n"
        "        \"/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/runtime/native_bridge\");\n"
        "    const std::string request_id =\n"
        "        \"native_\" + std::to_string(trigger_ms) + \"_\" +\n"
        "        std::to_string(sequence);\n"
        "    const std::filesystem::path request_dir = runtime_dir / request_id;\n"
        "    std::error_code fs_error;\n"
        "    std::filesystem::create_directories(request_dir, fs_error);\n"
        "    if (fs_error) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip mkdir failed: \"\n"
        "                          << fs_error.message();\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n\n"
        "    const std::string face_path = (request_dir / \"last_real_frame.ppm\").string();\n"
        "    const std::string audio_path = (request_dir / \"recent_audio.wav\").string();\n"
        "    const std::string output_path = (request_dir / \"generated.mp4\").string();\n"
        "    std::string error;\n"
        "    if (!webrtc_wav2lip::WriteArgbFrameToPpm(\n"
        "            face_path, argb_image.data(), frame_width, frame_height,\n"
        "            frame_width * 4, &error)) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip face write failed: \" << error;\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n"
        "    if (!audio_buffer->WriteRecentMonoWav(audio_path, kWav2LipAudioChunkMs,\n"
        "                                          &error)) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip audio write failed: \" << error;\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n\n"
        "    webrtc_wav2lip::Wav2LipRequest request;\n"
        "    request.request_id = request_id;\n"
        "    request.audio_path = audio_path;\n"
        "    request.face_path = face_path;\n"
        "    request.output_path = output_path;\n"
        "    request.fps = 25.0;\n"
        "    webrtc_wav2lip::Wav2LipBridge bridge(\n"
        "        EnvString(\"WEBRTC_WAV2LIP_HOST\", \"127.0.0.1\"),\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_PORT\", 19090));\n"
        "    RTC_LOG(LS_INFO) << \"Wav2Lip trigger request_id=\" << request_id\n"
        "                     << \" reason=\" << reason\n"
        "                     << \" render_gap_ms=\" << render_gap_ms\n"
        "                     << \" audio_buffered_ms=\" << audio_buffer->buffered_ms();\n"
        "    const webrtc_wav2lip::Wav2LipResponse response =\n"
        "        bridge.Generate(request);\n"
        "    if (response.ok) {\n"
        "      RTC_LOG(LS_INFO) << \"Wav2Lip generated request_id=\" << request_id\n"
        "                       << \" latency_ms=\" << response.latency_ms\n"
        "                       << \" output=\" << response.output_path;\n"
        "    } else {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip generation failed request_id=\"\n"
        "                          << request_id << \" error=\" << response.error;\n"
        "    }\n"
        "    inflight->store(false);\n"
        "  }).detach();\n"
        "}\n\n",
            source,
        )
    if "last_frame_ms_.store(" not in text:
        text = replace_once(
            text,
            "  gdk_threads_leave();\n\n"
            "  g_idle_add(Redraw, main_wnd_);\n"
            "}\n",
            "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n"
            "  gdk_threads_leave();\n\n"
            "  g_idle_add(Redraw, main_wnd_);\n"
            "}\n",
            source,
        )
    while (
        "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n"
        "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n"
    ) in text:
        text = text.replace(
            "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n"
            "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n",
            "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n",
        )
    text = text.replace(
        "    argb_image.assign(image_.data(), image_.data() + image_.size());\n",
        "    argb_image.resize(image_.size());\n"
        "    std::memcpy(argb_image.data(), image_.data(), image_.size());\n",
    )
    text = text.replace(
        '        EnvInt("WEBRTC_WAV2LIP_PORT", 8765));\n',
        '        EnvInt("WEBRTC_WAV2LIP_PORT", 19090));\n',
    )
    text = text.replace(
        "constexpr int kWav2LipAudioChunkMs = 1000;\n",
        "",
    )
    text = text.replace(
        "    if (!audio_buffer->WriteRecentMonoWav(audio_path, kWav2LipAudioChunkMs,\n"
        "                                          &error)) {\n",
        "    const int audio_chunk_ms =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_AUDIO_CHUNK_MS\", 1000);\n"
        "    const int use_audio_context =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_USE_AUDIO_CONTEXT\", 1);\n"
        "    if (use_audio_context == 0 &&\n"
        "        !audio_buffer->WriteRecentMonoWav(audio_path, audio_chunk_ms,\n"
        "                                          &error)) {\n",
    )
    text = text.replace(
        "    const int audio_chunk_ms =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_AUDIO_CHUNK_MS\", 1000);\n"
        "    if (!audio_buffer->WriteRecentMonoWav(audio_path, audio_chunk_ms,\n"
        "                                          &error)) {\n",
        "    const int audio_chunk_ms =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_AUDIO_CHUNK_MS\", 1000);\n"
        "    const int use_audio_context =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_USE_AUDIO_CONTEXT\", 1);\n"
        "    if (use_audio_context == 0 &&\n"
        "        !audio_buffer->WriteRecentMonoWav(audio_path, audio_chunk_ms,\n"
        "                                          &error)) {\n",
    )
    text = text.replace(
        "    request.fps = 25.0;\n",
        "    request.fps = EnvInt(\"WEBRTC_WAV2LIP_FPS\", 25);\n",
    )
    text = text.replace(
        "                     << \" audio_buffered_ms=\" << audio_buffer->buffered_ms();\n",
        "                     << \" audio_buffered_ms=\" << audio_buffer->buffered_ms()\n"
        "                     << \" audio_chunk_ms=\" << audio_chunk_ms;\n",
    )
    duplicate_block_start = "void GtkMainWnd::VideoRenderer::SetAudioBuffer("
    first_block = text.find(duplicate_block_start)
    second_block = text.find(duplicate_block_start, first_block + 1)
    if first_block != -1 and second_block != -1:
        next_set_size = text.find(
            "void GtkMainWnd::VideoRenderer::SetSize", second_block
        )
        if next_set_size == -1:
            raise RuntimeError(f"could not find duplicate block end in {source}")
        text = text[:second_block] + text[next_set_size:]
    first_constants = text.find("constexpr int64_t kWav2LipRenderGapTriggerMs")
    second_constants = text.find(
        "constexpr int64_t kWav2LipRenderGapTriggerMs", first_constants + 1
    )
    if first_constants != -1 and second_constants != -1:
        text = text[:first_constants] + text[second_constants:]
    changed |= write_if_changed(source, text)
    return changed


def patch_generated_playback(src_dir: Path) -> bool:
    changed = False
    header = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.h"
    text = header.read_text(encoding="utf-8")
    text = replace_once_if_missing(
        text,
        "    static int RenderGapMonitor(void* data);\n\n"
        "    int width() const { return width_; }\n",
        "    static int RenderGapMonitor(void* data);\n\n"
        "    struct GeneratedPlaybackState;\n\n"
        "    int width() const { return width_; }\n",
        "GeneratedPlaybackState",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    bool OnRenderGapTimer();\n\n"
        "    static int RenderGapMonitor(void* data);\n",
        "    bool OnRenderGapTimer();\n"
        "    void MaybePushAudioContext(int64_t now_ms);\n\n"
        "    static int RenderGapMonitor(void* data);\n",
        "MaybePushAudioContext",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    void MaybeStartWav2LipGeneration(int64_t render_gap_ms,\n"
        "                                      const char* reason,\n"
        "                                      std::vector<uint8_t> argb_image,\n"
        "                                      int width,\n"
        "                                      int height);\n",
        "    void MaybeStartWav2LipGeneration(int64_t render_gap_ms,\n"
        "                                      const char* reason,\n"
        "                                      std::vector<uint8_t> argb_image,\n"
        "                                      int width,\n"
        "                                      int height);\n"
        "    bool DisplayNextGeneratedFrame(int64_t now_ms);\n"
        "    void StopGeneratedPlayback();\n",
        "DisplayNextGeneratedFrame",
        header,
    )
    text = text.replace(
        "    std::shared_ptr<std::atomic<bool>> generation_inflight_;\n"
        "    std::atomic<int64_t> last_frame_ms_{0};\n",
        "    std::shared_ptr<std::atomic<bool>> generation_inflight_;\n"
        "    std::shared_ptr<std::atomic<bool>> audio_push_inflight_;\n"
        "    std::shared_ptr<GeneratedPlaybackState> generated_state_;\n"
        "    std::atomic<int64_t> last_frame_ms_{0};\n",
    )
    text = text.replace(
        "    std::shared_ptr<std::atomic<bool>> generation_inflight_;\n"
        "    std::shared_ptr<GeneratedPlaybackState> generated_state_;\n",
        "    std::shared_ptr<std::atomic<bool>> generation_inflight_;\n"
        "    std::shared_ptr<std::atomic<bool>> audio_push_inflight_;\n"
        "    std::shared_ptr<GeneratedPlaybackState> generated_state_;\n",
    )
    text = replace_once_if_missing(
        text,
        "    std::atomic<int64_t> last_generation_trigger_ms_{0};\n"
        "    std::atomic<int> generation_sequence_{0};\n",
        "    std::atomic<int64_t> last_generation_trigger_ms_{0};\n"
        "    std::atomic<int64_t> last_audio_push_ms_{0};\n"
        "    std::atomic<int> generation_sequence_{0};\n"
        "    std::atomic<int> audio_push_sequence_{0};\n",
        "audio_push_sequence_",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    std::atomic<int64_t> last_frame_ms_{0};\n"
        "    std::atomic<int64_t> last_generation_trigger_ms_{0};\n",
        "    std::atomic<int64_t> last_frame_ms_{0};\n"
        "    std::atomic<int64_t> first_remote_frame_ms_{0};\n"
        "    std::atomic<int64_t> last_generation_trigger_ms_{0};\n",
        "first_remote_frame_ms_",
        header,
    )
    changed |= write_if_changed(header, text)

    source = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.cc"
    text = source.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        "#include <filesystem>\n",
        "#include <fstream>\n",
        source,
    )
    text = insert_after_once(
        text,
        "#include <fstream>\n",
        "#include <mutex>\n",
        source,
    )
    text = insert_after_once(
        text,
        '#include "examples/peerconnection/client/frame_image_writer.h"\n',
        '#include "examples/peerconnection/client/generated_frame_reader.h"\n',
        source,
    )
    text = insert_after_once(
        text,
        "constexpr int64_t kWav2LipGenerationCooldownMs = 3000;\n",
        "constexpr int64_t kWav2LipGeneratedFrameIntervalMs = 40;\n"
        "constexpr int kMaxGeneratedPlaybackFrames = 150;\n",
        source,
    )
    text = insert_after_once(
        text,
        "}  // namespace\n\n"
        "//\n"
        "// GtkMainWnd implementation.\n"
        "//\n",
        "struct GtkMainWnd::VideoRenderer::GeneratedPlaybackState {\n"
        "  std::mutex mutex;\n"
        "  bool active = false;\n"
        "  std::vector<webrtc_wav2lip::GeneratedArgbFrame> frames;\n"
        "  size_t frame_index = 0;\n"
        "  int64_t next_frame_ms = 0;\n"
        "  std::string request_id;\n"
        "};\n\n",
        source,
    )
    text = text.replace(
        "      remote_audio_buffer_(std::move(remote_audio_buffer)),\n"
        "      generation_inflight_(std::make_shared<std::atomic<bool>>(false)) {\n",
        "      remote_audio_buffer_(std::move(remote_audio_buffer)),\n"
        "      generation_inflight_(std::make_shared<std::atomic<bool>>(false)),\n"
        "      audio_push_inflight_(std::make_shared<std::atomic<bool>>(false)),\n"
        "      generated_state_(std::make_shared<GeneratedPlaybackState>()) {\n",
    )
    text = replace_once_if_missing(
        text,
        "      generation_inflight_(std::make_shared<std::atomic<bool>>(false)),\n"
        "      generated_state_(std::make_shared<GeneratedPlaybackState>()) {\n",
        "      generation_inflight_(std::make_shared<std::atomic<bool>>(false)),\n"
        "      audio_push_inflight_(std::make_shared<std::atomic<bool>>(false)),\n"
        "      generated_state_(std::make_shared<GeneratedPlaybackState>()) {\n",
        "audio_push_inflight_(std::make_shared<std::atomic<bool>>(false))",
        source,
    )
    text = insert_after_once(
        text,
        "void GtkMainWnd::VideoRenderer::SetAudioBuffer(\n"
        "    std::shared_ptr<webrtc_wav2lip::RecentAudioBuffer> remote_audio) {\n"
        "  remote_audio_buffer_ = std::move(remote_audio);\n"
        "}\n\n",
        "void GtkMainWnd::VideoRenderer::MaybePushAudioContext(int64_t now_ms) {\n"
        "  if (EnvInt(\"WEBRTC_WAV2LIP_STREAM_AUDIO_CONTEXT\", 1) == 0) {\n"
        "    return;\n"
        "  }\n"
        "  auto audio_buffer = remote_audio_buffer_;\n"
        "  if (!audio_buffer) {\n"
        "    return;\n"
        "  }\n"
        "  const int context_ms = EnvInt(\"WEBRTC_WAV2LIP_AUDIO_CONTEXT_MS\", 500);\n"
        "  if (audio_buffer->buffered_ms() < context_ms) {\n"
        "    return;\n"
        "  }\n"
        "  const int push_interval_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_AUDIO_PUSH_INTERVAL_MS\", 120);\n"
        "  const int64_t last_push_ms =\n"
        "      last_audio_push_ms_.load(std::memory_order_relaxed);\n"
        "  if (now_ms - last_push_ms < push_interval_ms) {\n"
        "    return;\n"
        "  }\n"
        "  bool expected = false;\n"
        "  if (!audio_push_inflight_->compare_exchange_strong(expected, true)) {\n"
        "    return;\n"
        "  }\n"
        "  last_audio_push_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "  const int sequence = audio_push_sequence_.fetch_add(1) + 1;\n"
        "  auto inflight = audio_push_inflight_;\n"
        "  std::thread([audio_buffer, context_ms, now_ms, sequence,\n"
        "               inflight]() mutable {\n"
        "    const std::filesystem::path runtime_dir = EnvString(\n"
        "        \"WEBRTC_WAV2LIP_RUNTIME_DIR\",\n"
        "        \"/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/runtime/native_bridge\");\n"
        "    const std::filesystem::path context_dir = runtime_dir / \"audio_context\";\n"
        "    std::error_code fs_error;\n"
        "    std::filesystem::create_directories(context_dir, fs_error);\n"
        "    if (fs_error) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip audio context mkdir failed: \"\n"
        "                          << fs_error.message();\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n"
        "    const std::string request_id =\n"
        "        \"audio_ctx_\" + std::to_string(now_ms) + \"_\" +\n"
        "        std::to_string(sequence);\n"
        "    const std::string audio_path =\n"
        "        (context_dir / (request_id + \".wav\")).string();\n"
        "    std::string error;\n"
        "    if (!audio_buffer->WriteRecentMonoWav(audio_path, context_ms,\n"
        "                                          &error)) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip audio context write failed: \"\n"
        "                          << error;\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n"
        "    webrtc_wav2lip::Wav2LipRequest request;\n"
        "    request.request_id = request_id;\n"
        "    request.audio_path = audio_path;\n"
        "    webrtc_wav2lip::Wav2LipBridge bridge(\n"
        "        EnvString(\"WEBRTC_WAV2LIP_HOST\", \"127.0.0.1\"),\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_PORT\", 19090));\n"
        "    const webrtc_wav2lip::Wav2LipResponse response =\n"
        "        bridge.SetAudioContext(request);\n"
        "    if (!response.ok) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip audio context push failed: \"\n"
        "                          << response.error;\n"
        "    }\n"
        "    inflight->store(false);\n"
        "  }).detach();\n"
        "}\n\n"
        "bool GtkMainWnd::VideoRenderer::DisplayNextGeneratedFrame(int64_t now_ms) {\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return false;\n"
        "  }\n"
        "  webrtc_wav2lip::GeneratedArgbFrame frame;\n"
        "  {\n"
        "    std::lock_guard<std::mutex> lock(state->mutex);\n"
        "    if (!state->active || state->frames.empty()) {\n"
        "      return false;\n"
        "    }\n"
        "    if (now_ms < state->next_frame_ms) {\n"
        "      return true;\n"
        "    }\n"
        "    frame = state->frames[state->frame_index];\n"
        "    state->frame_index = (state->frame_index + 1) % state->frames.size();\n"
        "    state->next_frame_ms = now_ms + kWav2LipGeneratedFrameIntervalMs;\n"
        "  }\n\n"
        "  gdk_threads_enter();\n"
        "  width_ = frame.width;\n"
        "  height_ = frame.height;\n"
        "  image_.SetSize(frame.bgra.size());\n"
        "  std::memcpy(image_.data(), frame.bgra.data(), frame.bgra.size());\n"
        "  gdk_threads_leave();\n"
        "  g_idle_add(Redraw, main_wnd_);\n"
        "  return true;\n"
        "}\n\n"
        "void GtkMainWnd::VideoRenderer::StopGeneratedPlayback() {\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return;\n"
        "  }\n"
        "  std::lock_guard<std::mutex> lock(state->mutex);\n"
        "  if (state->active) {\n"
        "    RTC_LOG(LS_INFO) << \"Wav2Lip generated playback stopped request_id=\"\n"
        "                     << state->request_id;\n"
        "  }\n"
        "  state->active = false;\n"
        "}\n\n",
        source,
    )
    legacy_timer_marker = (
        "  if (render_gap_ms < kWav2LipRenderGapTriggerMs) {\n"
        "    return true;\n"
        "  }\n"
    )
    if (
        legacy_timer_marker in text
        and "const bool displayed_generated = DisplayNextGeneratedFrame(now_ms);"
        not in text
    ):
        text = insert_after_once(
            text,
            legacy_timer_marker,
            "  if (DisplayNextGeneratedFrame(now_ms)) {\n"
            "    return true;\n"
            "  }\n",
            source,
        )
    text = replace_once_if_missing(
        text,
        "  const int64_t now_ms = NowMillis();\n"
        "  const int64_t render_gap_ms = now_ms - last_frame_ms;\n",
        "  const int64_t now_ms = NowMillis();\n"
        "  MaybePushAudioContext(now_ms);\n"
        "  const int64_t render_gap_ms = now_ms - last_frame_ms;\n",
        "MaybePushAudioContext(now_ms);",
        source,
    )
    text = replace_once_if_missing(
        text,
        "  auto inflight = generation_inflight_;\n\n"
        "  std::thread([audio_buffer, argb_image = std::move(argb_image), frame_width,\n",
        "  auto inflight = generation_inflight_;\n"
        "  auto generated_state = generated_state_;\n\n"
        "  std::thread([audio_buffer, generated_state,\n"
        "               argb_image = std::move(argb_image), frame_width,\n",
        "generated_state = generated_state_",
        source,
    )
    frame_load_block = (
        "      std::vector<webrtc_wav2lip::GeneratedArgbFrame> frames;\n"
        "      std::string frame_error;\n"
        "      if (webrtc_wav2lip::LoadGeneratedPpmFrames(\n"
        "              response.frames_dir, kMaxGeneratedPlaybackFrames, &frames,\n"
        "              &frame_error)) {\n"
        "        std::lock_guard<std::mutex> lock(generated_state->mutex);\n"
        "        generated_state->frames = std::move(frames);\n"
        "        generated_state->frame_index = 0;\n"
        "        generated_state->next_frame_ms = NowMillis();\n"
        "        generated_state->request_id = request_id;\n"
        "        generated_state->active = true;\n"
        "        RTC_LOG(LS_INFO) << \"Wav2Lip generated playback ready request_id=\"\n"
        "                         << request_id\n"
        "                         << \" frames=\" << generated_state->frames.size()\n"
        "                         << \" frames_dir=\" << response.frames_dir;\n"
        "        std::filesystem::path ready_path =\n"
        "            std::filesystem::path(response.frames_dir).parent_path() /\n"
        "            \"native_playback_ready.txt\";\n"
        "        std::ofstream ready_file(ready_path);\n"
        "        ready_file << \"request_id=\" << request_id << \"\\n\"\n"
        "                   << \"frames=\" << generated_state->frames.size() << \"\\n\"\n"
        "                   << \"frames_dir=\" << response.frames_dir << \"\\n\";\n"
        "      } else {\n"
        "        RTC_LOG(LS_WARNING) << \"Wav2Lip frame load failed request_id=\"\n"
        "                            << request_id << \" error=\" << frame_error;\n"
        "      }\n"
    )
    if "LoadGeneratedPpmFrames" not in text:
        text = insert_after_once(
            text,
            "      RTC_LOG(LS_INFO) << \"Wav2Lip generated request_id=\" << request_id\n"
            "                       << \" latency_ms=\" << response.latency_ms\n"
            "                       << \" output=\" << response.output_path;\n",
            frame_load_block,
            source,
        )
    text = text.replace(
        "    const webrtc_wav2lip::Wav2LipResponse response =\n"
        "        bridge.Generate(request);\n",
        "    request.tail_ms = EnvInt(\"WEBRTC_WAV2LIP_TAIL_MS\", 160);\n"
        "    const webrtc_wav2lip::Wav2LipResponse response =\n"
        "        use_audio_context ? bridge.GenerateTail(request)\n"
        "                          : bridge.Generate(request);\n",
    )
    text = text.replace(
        "                     << \" audio_chunk_ms=\" << audio_chunk_ms;\n",
        "                     << \" audio_chunk_ms=\" << audio_chunk_ms\n"
        "                     << \" use_audio_context=\"\n"
        "                     << EnvInt(\"WEBRTC_WAV2LIP_USE_AUDIO_CONTEXT\", 1);\n",
    )
    frame_block_start = (
        "      std::vector<webrtc_wav2lip::GeneratedArgbFrame> frames;\n"
    )
    generation_failed_else = (
        "    } else {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip generation failed request_id=\""
    )
    first_frame_block = text.find(frame_block_start)
    if first_frame_block != -1:
        frame_block_end = text.find(generation_failed_else, first_frame_block)
        if frame_block_end == -1:
            raise RuntimeError(f"could not find generated frame block end in {source}")
        text = text[:first_frame_block] + frame_load_block + text[frame_block_end:]
    display_block_start = (
        "bool GtkMainWnd::VideoRenderer::DisplayNextGeneratedFrame(int64_t now_ms) {\n"
    )
    first_display_block = text.find(display_block_start)
    second_display_block = text.find(display_block_start, first_display_block + 1)
    if first_display_block != -1 and second_display_block != -1:
        render_monitor_block = text.find(
            "int GtkMainWnd::VideoRenderer::RenderGapMonitor", second_display_block
        )
        if render_monitor_block == -1:
            raise RuntimeError(f"could not find duplicate display block end in {source}")
        text = text[:second_display_block] + text[render_monitor_block:]
    if "render_real_frame" not in text:
        text = replace_once_if_missing(
            text,
            "void GtkMainWnd::VideoRenderer::OnFrame(const webrtc::VideoFrame& video_frame) {\n"
            "  gdk_threads_enter();\n",
            "void GtkMainWnd::VideoRenderer::OnFrame(const webrtc::VideoFrame& video_frame) {\n"
            "  StopGeneratedPlayback();\n"
            "  gdk_threads_enter();\n",
            "StopGeneratedPlayback();\n"
            "  gdk_threads_enter();",
            source,
        )
    changed |= write_if_changed(source, text)
    return changed


def patch_generated_queue_prefetch(src_dir: Path) -> bool:
    """Turn generated playback into a real queue with prefetch-threshold refill."""

    changed = False
    header = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.h"
    text = header.read_text(encoding="utf-8")
    text = insert_after_once(text, "#include <stdint.h>\n\n", "#include <cstddef>\n", header)
    text = replace_once_if_missing(
        text,
        "    bool DisplayNextGeneratedFrame(int64_t now_ms);\n"
        "    void StopGeneratedPlayback();\n"
        "    webrtc::Buffer image_;\n",
        "    bool DisplayNextGeneratedFrame(int64_t now_ms);\n"
        "    size_t GeneratedQueueRemaining() const;\n"
        "    bool CopyLastRealFrame(std::vector<uint8_t>* argb_image,\n"
        "                           int* frame_width,\n"
        "                           int* frame_height);\n"
        "    void StopGeneratedPlayback();\n"
        "    webrtc::Buffer image_;\n",
        "CopyLastRealFrame",
        header,
    )
    text = replace_once_if_missing(
        text,
        "    webrtc::Buffer image_;\n"
        "    int width_;\n"
        "    int height_;\n",
        "    webrtc::Buffer image_;\n"
        "    webrtc::Buffer last_real_image_;\n"
        "    int last_real_width_ = 0;\n"
        "    int last_real_height_ = 0;\n"
        "    int width_;\n"
        "    int height_;\n",
        "last_real_image_",
        header,
    )
    changed |= write_if_changed(header, text)

    source = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.cc"
    text = source.read_text(encoding="utf-8")
    text = text.replace(
        "constexpr int64_t kWav2LipGenerationCooldownMs = 3000;\n",
        "",
    )
    old_display = (
        "bool GtkMainWnd::VideoRenderer::DisplayNextGeneratedFrame(int64_t now_ms) {\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return false;\n"
        "  }\n"
        "  webrtc_wav2lip::GeneratedArgbFrame frame;\n"
        "  {\n"
        "    std::lock_guard<std::mutex> lock(state->mutex);\n"
        "    if (!state->active || state->frames.empty()) {\n"
        "      return false;\n"
        "    }\n"
        "    if (now_ms < state->next_frame_ms) {\n"
        "      return true;\n"
        "    }\n"
        "    frame = state->frames[state->frame_index];\n"
        "    state->frame_index = (state->frame_index + 1) % state->frames.size();\n"
        "    state->next_frame_ms = now_ms + kWav2LipGeneratedFrameIntervalMs;\n"
        "  }\n\n"
        "  gdk_threads_enter();\n"
        "  width_ = frame.width;\n"
        "  height_ = frame.height;\n"
        "  image_.SetSize(frame.bgra.size());\n"
        "  std::memcpy(image_.data(), frame.bgra.data(), frame.bgra.size());\n"
        "  gdk_threads_leave();\n"
        "  g_idle_add(Redraw, main_wnd_);\n"
        "  return true;\n"
        "}\n\n"
    )
    new_display = (
        "bool GtkMainWnd::VideoRenderer::DisplayNextGeneratedFrame(int64_t now_ms) {\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return false;\n"
        "  }\n"
        "  webrtc_wav2lip::GeneratedArgbFrame frame;\n"
        "  {\n"
        "    std::lock_guard<std::mutex> lock(state->mutex);\n"
        "    if (!state->active || state->frames.empty()) {\n"
        "      return false;\n"
        "    }\n"
        "    if (state->frame_index >= state->frames.size()) {\n"
        "      state->active = false;\n"
        "      state->frames.clear();\n"
        "      state->frame_index = 0;\n"
        "      return false;\n"
        "    }\n"
        "    if (now_ms < state->next_frame_ms) {\n"
        "      return true;\n"
        "    }\n"
        "    frame = state->frames[state->frame_index++];\n"
        "    if (state->frame_index >= state->frames.size()) {\n"
        "      state->active = false;\n"
        "    }\n"
        "    state->next_frame_ms = now_ms + kWav2LipGeneratedFrameIntervalMs;\n"
        "  }\n\n"
        "  gdk_threads_enter();\n"
        "  width_ = frame.width;\n"
        "  height_ = frame.height;\n"
        "  image_.SetSize(frame.bgra.size());\n"
        "  std::memcpy(image_.data(), frame.bgra.data(), frame.bgra.size());\n"
        "  gdk_threads_leave();\n"
        "  g_idle_add(Redraw, main_wnd_);\n"
        "  return true;\n"
        "}\n\n"
        "size_t GtkMainWnd::VideoRenderer::GeneratedQueueRemaining() const {\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return 0;\n"
        "  }\n"
        "  std::lock_guard<std::mutex> lock(state->mutex);\n"
        "  if (!state->active || state->frames.empty() ||\n"
        "      state->frame_index >= state->frames.size()) {\n"
        "    return 0;\n"
        "  }\n"
        "  return state->frames.size() - state->frame_index;\n"
        "}\n\n"
        "bool GtkMainWnd::VideoRenderer::CopyLastRealFrame(\n"
        "    std::vector<uint8_t>* argb_image,\n"
        "    int* frame_width,\n"
        "    int* frame_height) {\n"
        "  if (!argb_image || !frame_width || !frame_height) {\n"
        "    return false;\n"
        "  }\n"
        "  argb_image->clear();\n"
        "  *frame_width = 0;\n"
        "  *frame_height = 0;\n"
        "  gdk_threads_enter();\n"
        "  const webrtc::Buffer* source_image = &last_real_image_;\n"
        "  int source_width = last_real_width_;\n"
        "  int source_height = last_real_height_;\n"
        "  if (source_image->empty() || source_width <= 0 || source_height <= 0) {\n"
        "    source_image = &image_;\n"
        "    source_width = width_;\n"
        "    source_height = height_;\n"
        "  }\n"
        "  if (!source_image->empty() && source_width > 0 && source_height > 0) {\n"
        "    argb_image->resize(source_image->size());\n"
        "    std::memcpy(argb_image->data(), source_image->data(),\n"
        "                source_image->size());\n"
        "    *frame_width = source_width;\n"
        "    *frame_height = source_height;\n"
        "  }\n"
        "  gdk_threads_leave();\n"
        "  return !argb_image->empty();\n"
        "}\n\n"
    )
    display_start = text.find(
        "bool GtkMainWnd::VideoRenderer::DisplayNextGeneratedFrame(int64_t now_ms) {\n"
    )
    stop_playback_start = text.find(
        "void GtkMainWnd::VideoRenderer::StopGeneratedPlayback()",
        display_start,
    )
    if display_start == -1 or stop_playback_start == -1:
        raise RuntimeError(f"could not find generated display block in {source}")
    text = text[:display_start] + new_display + text[stop_playback_start:]
    text = text.replace(
        "void GtkMainWnd::VideoRenderer::StopGeneratedPlayback() {\n"
        "  auto state = generated_state_;\n",
        "void GtkMainWnd::VideoRenderer::StopGeneratedPlayback() {\n"
        "  if (EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON\", 0) != 0) {\n"
        "    return;\n"
        "  }\n"
        "  auto state = generated_state_;\n",
    )
    first_audio_context = text.find(
        "void GtkMainWnd::VideoRenderer::MaybePushAudioContext(int64_t now_ms) {\n"
    )
    duplicate_audio_context = text.find(
        "void GtkMainWnd::VideoRenderer::MaybePushAudioContext(int64_t now_ms) {\n",
        first_audio_context + 1,
    )
    while duplicate_audio_context != -1:
        duplicate_end = text.find(
            "int GtkMainWnd::VideoRenderer::RenderGapMonitor",
            duplicate_audio_context,
        )
        if duplicate_end == -1:
            raise RuntimeError(
                f"could not find duplicate audio context block end in {source}"
            )
        text = text[:duplicate_audio_context] + text[duplicate_end:]
        duplicate_audio_context = text.find(
            "void GtkMainWnd::VideoRenderer::MaybePushAudioContext(int64_t now_ms) {\n",
            first_audio_context + 1,
        )

    old_timer = (
        "bool GtkMainWnd::VideoRenderer::OnRenderGapTimer() {\n"
        "  if (!is_remote_) {\n"
        "    return false;\n"
        "  }\n"
        "  const int64_t last_frame_ms = last_frame_ms_.load(std::memory_order_relaxed);\n"
        "  if (last_frame_ms == 0) {\n"
        "    return true;\n"
        "  }\n"
        "  const int64_t now_ms = NowMillis();\n"
        "  MaybePushAudioContext(now_ms);\n"
        "  const int64_t render_gap_ms = now_ms - last_frame_ms;\n"
        "  if (render_gap_ms < kWav2LipRenderGapTriggerMs) {\n"
        "    return true;\n"
        "  }\n"
        "  if (DisplayNextGeneratedFrame(now_ms)) {\n"
        "    return true;\n"
        "  }\n"
        "  const int64_t last_generation_ms =\n"
        "      last_generation_trigger_ms_.load(std::memory_order_relaxed);\n"
        "  if (now_ms - last_generation_ms < kWav2LipGenerationCooldownMs) {\n"
        "    return true;\n"
        "  }\n\n"
        "  std::vector<uint8_t> argb_image;\n"
        "  int frame_width = 0;\n"
        "  int frame_height = 0;\n"
        "  gdk_threads_enter();\n"
        "  if (!image_.empty() && width_ > 0 && height_ > 0) {\n"
        "    argb_image.resize(image_.size());\n"
        "    std::memcpy(argb_image.data(), image_.data(), image_.size());\n"
        "    frame_width = width_;\n"
        "    frame_height = height_;\n"
        "  }\n"
        "  gdk_threads_leave();\n\n"
        "  MaybeStartWav2LipGeneration(render_gap_ms, \"render_gap_timer\",\n"
        "                              std::move(argb_image), frame_width,\n"
        "                              frame_height);\n"
        "  return true;\n"
        "}\n\n"
    )
    new_timer = (
        "bool GtkMainWnd::VideoRenderer::OnRenderGapTimer() {\n"
        "  if (!is_remote_) {\n"
        "    return false;\n"
        "  }\n"
        "  const int64_t last_frame_ms = last_frame_ms_.load(std::memory_order_relaxed);\n"
        "  if (last_frame_ms == 0) {\n"
        "    return true;\n"
        "  }\n"
        "  const int64_t now_ms = NowMillis();\n"
        "  MaybePushAudioContext(now_ms);\n"
        "  const int64_t render_gap_ms = now_ms - last_frame_ms;\n"
        "  int prefetch_threshold_frames =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_PREFETCH_THRESHOLD_FRAMES\",\n"
        "             EnvInt(\"WEBRTC_WAV2LIP_QUEUE_LOW_WATERMARK_FRAMES\", 3));\n"
        "  if (prefetch_threshold_frames < 0) {\n"
        "    prefetch_threshold_frames = 0;\n"
        "  }\n"
        "  const int64_t last_generation_ms =\n"
        "      last_generation_trigger_ms_.load(std::memory_order_relaxed);\n"
        "  if (render_gap_ms < kWav2LipRenderGapTriggerMs) {\n"
        "    if (EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON\", 0) != 0) {\n"
        "      const int64_t first_remote_frame_ms =\n"
        "          first_remote_frame_ms_.load(std::memory_order_relaxed);\n"
        "      const int always_on_startup_delay_ms =\n"
        "          EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON_STARTUP_DELAY_MS\",\n"
        "                 EnvInt(\"WEBRTC_WAV2LIP_AUDIO_CONTEXT_MS\", 500) + 1000);\n"
        "      if (first_remote_frame_ms > 0 &&\n"
        "          now_ms - first_remote_frame_ms < always_on_startup_delay_ms) {\n"
        "        return true;\n"
        "      }\n"
        "      const int64_t last_audio_push_ms =\n"
        "          last_audio_push_ms_.load(std::memory_order_relaxed);\n"
        "      const int audio_context_grace_ms =\n"
        "          EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON_AUDIO_CONTEXT_GRACE_MS\", 80);\n"
        "      if (last_audio_push_ms == 0 ||\n"
        "          now_ms - last_audio_push_ms < audio_context_grace_ms) {\n"
        "        return true;\n"
        "      }\n"
        "      const int always_on_interval_ms =\n"
        "          EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON_INTERVAL_MS\", 400);\n"
        "      if (now_ms - last_generation_ms >= always_on_interval_ms) {\n"
        "        std::vector<uint8_t> argb_image;\n"
        "        int frame_width = 0;\n"
        "        int frame_height = 0;\n"
        "        if (CopyLastRealFrame(&argb_image, &frame_width, &frame_height)) {\n"
        "          MaybeStartWav2LipGeneration(render_gap_ms, \"always_on_prefetch\",\n"
        "                                      std::move(argb_image), frame_width,\n"
        "                                      frame_height);\n"
        "        }\n"
        "      }\n"
        "    }\n"
        "    return true;\n"
        "  }\n"
        "  const bool displayed_generated = DisplayNextGeneratedFrame(now_ms);\n"
        "  if (displayed_generated &&\n"
        "      GeneratedQueueRemaining() >\n"
        "          static_cast<size_t>(prefetch_threshold_frames)) {\n"
        "    return true;\n"
        "  }\n"
        "  const int generation_cooldown_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_GENERATION_COOLDOWN_MS\", 120);\n"
        "  if (now_ms - last_generation_ms < generation_cooldown_ms) {\n"
        "    return true;\n"
        "  }\n\n"
        "  std::vector<uint8_t> argb_image;\n"
        "  int frame_width = 0;\n"
        "  int frame_height = 0;\n"
        "  if (!CopyLastRealFrame(&argb_image, &frame_width, &frame_height)) {\n"
        "    return true;\n"
        "  }\n"
        "  MaybeStartWav2LipGeneration(\n"
        "      render_gap_ms,\n"
        "      displayed_generated ? \"generated_queue_low\" : \"render_gap_timer\",\n"
        "      std::move(argb_image), frame_width, frame_height);\n"
        "  return true;\n"
        "}\n\n"
    )
    timer_start = text.find("bool GtkMainWnd::VideoRenderer::OnRenderGapTimer() {\n")
    generation_start = text.find(
        "void GtkMainWnd::VideoRenderer::MaybeStartWav2LipGeneration(",
        timer_start,
    )
    if timer_start == -1 or generation_start == -1:
        raise RuntimeError(f"could not find render gap timer block in {source}")
    text = text[:timer_start] + new_timer + text[generation_start:]

    old_frame_load = (
        "      std::vector<webrtc_wav2lip::GeneratedArgbFrame> frames;\n"
        "      std::string frame_error;\n"
        "      if (webrtc_wav2lip::LoadGeneratedPpmFrames(\n"
        "              response.frames_dir, kMaxGeneratedPlaybackFrames, &frames,\n"
        "              &frame_error)) {\n"
        "        std::lock_guard<std::mutex> lock(generated_state->mutex);\n"
        "        generated_state->frames = std::move(frames);\n"
        "        generated_state->frame_index = 0;\n"
        "        generated_state->next_frame_ms = NowMillis();\n"
        "        generated_state->request_id = request_id;\n"
        "        generated_state->active = true;\n"
        "        RTC_LOG(LS_INFO) << \"Wav2Lip generated playback ready request_id=\"\n"
        "                         << request_id\n"
        "                         << \" frames=\" << generated_state->frames.size()\n"
        "                         << \" frames_dir=\" << response.frames_dir;\n"
        "        std::filesystem::path ready_path =\n"
        "            std::filesystem::path(response.frames_dir).parent_path() /\n"
        "            \"native_playback_ready.txt\";\n"
        "        std::ofstream ready_file(ready_path);\n"
        "        ready_file << \"request_id=\" << request_id << \"\\n\"\n"
        "                   << \"frames=\" << generated_state->frames.size() << \"\\n\"\n"
        "                   << \"frames_dir=\" << response.frames_dir << \"\\n\";\n"
        "      } else {\n"
    )
    new_frame_load = (
        "      std::vector<webrtc_wav2lip::GeneratedArgbFrame> frames;\n"
        "      std::string frame_error;\n"
        "      if (webrtc_wav2lip::LoadGeneratedPpmFrames(\n"
        "              response.frames_dir, kMaxGeneratedPlaybackFrames, &frames,\n"
        "              &frame_error)) {\n"
        "        const size_t loaded_frames = frames.size();\n"
        "        size_t queued_frames = 0;\n"
        "        {\n"
        "          std::lock_guard<std::mutex> lock(generated_state->mutex);\n"
        "          if (reason == \"always_on_prefetch\" ||\n"
        "              !generated_state->active || generated_state->frames.empty() ||\n"
        "              generated_state->frame_index >= generated_state->frames.size()) {\n"
        "            generated_state->frames = std::move(frames);\n"
        "            generated_state->frame_index = 0;\n"
        "            generated_state->next_frame_ms = NowMillis();\n"
        "          } else {\n"
        "            if (generated_state->frame_index > 0) {\n"
        "              generated_state->frames.erase(\n"
        "                  generated_state->frames.begin(),\n"
        "                  generated_state->frames.begin() +\n"
        "                      generated_state->frame_index);\n"
        "              generated_state->frame_index = 0;\n"
        "            }\n"
        "            for (auto& generated_frame : frames) {\n"
        "              generated_state->frames.push_back(std::move(generated_frame));\n"
        "            }\n"
        "          }\n"
        "          if (generated_state->frames.size() > kMaxGeneratedPlaybackFrames) {\n"
        "            generated_state->frames.resize(kMaxGeneratedPlaybackFrames);\n"
        "          }\n"
        "          generated_state->request_id = request_id;\n"
        "          generated_state->active = !generated_state->frames.empty();\n"
        "          queued_frames = generated_state->frames.size() -\n"
        "                          generated_state->frame_index;\n"
        "        }\n"
        "        RTC_LOG(LS_INFO) << \"Wav2Lip generated playback ready request_id=\"\n"
        "                         << request_id\n"
        "                         << \" frames_loaded=\" << loaded_frames\n"
        "                         << \" queue_remaining=\" << queued_frames\n"
        "                         << \" frames_dir=\" << response.frames_dir;\n"
        "        std::filesystem::path ready_path =\n"
        "            std::filesystem::path(response.frames_dir).parent_path() /\n"
        "            \"native_playback_ready.txt\";\n"
        "        std::ofstream ready_file(ready_path);\n"
        "        ready_file << \"request_id=\" << request_id << \"\\n\"\n"
        "                   << \"reason=\" << reason << \"\\n\"\n"
        "                   << \"frames_loaded=\" << loaded_frames << \"\\n\"\n"
        "                   << \"queue_remaining=\" << queued_frames << \"\\n\"\n"
        "                   << \"frames_dir=\" << response.frames_dir << \"\\n\";\n"
        "      } else {\n"
    )
    response_ok_start = text.find(
        "    if (response.ok) {\n"
        "      RTC_LOG(LS_INFO) << \"Wav2Lip generated request_id=\"",
    )
    frame_load_start = text.find(
        "      std::vector<webrtc_wav2lip::GeneratedArgbFrame> frames;\n",
        response_ok_start,
    )
    frame_load_end = text.find(
        "      } else {\n"
        "        RTC_LOG(LS_WARNING) << \"Wav2Lip frame load failed request_id=\"",
        frame_load_start,
    )
    if frame_load_start == -1 or frame_load_end == -1:
        raise RuntimeError(f"could not find generated frame load block in {source}")
    text = text[:frame_load_start] + new_frame_load + text[frame_load_end:]
    text = text.replace(
        "      } else {\n"
        "      } else {\n"
        "        RTC_LOG(LS_WARNING) << \"Wav2Lip frame load failed request_id=\"",
        "      } else {\n"
        "        RTC_LOG(LS_WARNING) << \"Wav2Lip frame load failed request_id=\"",
    )

    old_onframe = (
        "  libyuv::I420ToARGB(buffer->DataY(), buffer->StrideY(), buffer->DataU(),\n"
        "                     buffer->StrideU(), buffer->DataV(), buffer->StrideV(),\n"
        "                     image_.data(), width_ * 4, buffer->width(),\n"
        "                     buffer->height());\n\n"
        "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n"
    )
    new_onframe = (
        "  libyuv::I420ToARGB(buffer->DataY(), buffer->StrideY(), buffer->DataU(),\n"
        "                     buffer->StrideU(), buffer->DataV(), buffer->StrideV(),\n"
        "                     image_.data(), width_ * 4, buffer->width(),\n"
        "                     buffer->height());\n"
        "  if (is_remote_) {\n"
        "    last_real_width_ = width_;\n"
        "    last_real_height_ = height_;\n"
        "    last_real_image_.SetSize(image_.size());\n"
        "    std::memcpy(last_real_image_.data(), image_.data(), image_.size());\n"
        "  }\n\n"
        "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n"
    )
    if "last_real_image_.SetSize(" not in text:
        text = replace_once(text, old_onframe, new_onframe, source)
    text = replace_once_if_missing(
        text,
        "void GtkMainWnd::VideoRenderer::OnFrame(const webrtc::VideoFrame& video_frame) {\n"
        "  StopGeneratedPlayback();\n"
        "  gdk_threads_enter();\n",
        "void GtkMainWnd::VideoRenderer::OnFrame(const webrtc::VideoFrame& video_frame) {\n"
        "  const int64_t now_ms = NowMillis();\n"
        "  if (is_remote_) {\n"
        "    int64_t expected_first_ms = 0;\n"
        "    first_remote_frame_ms_.compare_exchange_strong(expected_first_ms,\n"
        "                                                    now_ms);\n"
        "    const int test_freeze_after_ms =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_TEST_FREEZE_AFTER_MS\", 0);\n"
        "    const int64_t first_remote_frame_ms =\n"
        "        first_remote_frame_ms_.load(std::memory_order_relaxed);\n"
        "    if (test_freeze_after_ms > 0 && first_remote_frame_ms > 0 &&\n"
        "        now_ms - first_remote_frame_ms >= test_freeze_after_ms) {\n"
        "      return;\n"
        "    }\n"
        "  }\n"
        "  StopGeneratedPlayback();\n"
        "  gdk_threads_enter();\n",
        "WEBRTC_WAV2LIP_TEST_FREEZE_AFTER_MS",
        source,
    )
    text = text.replace(
        "  last_frame_ms_.store(NowMillis(), std::memory_order_relaxed);\n",
        "  last_frame_ms_.store(now_ms, std::memory_order_relaxed);\n",
    )
    text = text.replace(
        "    const std::string face_path = (request_dir / \"last_real_frame.ppm\").string();\n"
        "    const std::string audio_path = (request_dir / \"recent_audio.wav\").string();\n"
        "    const std::string output_path = (request_dir / \"generated.mp4\").string();\n"
        "    std::string error;\n"
        "    if (!webrtc_wav2lip::WriteArgbFrameToPpm(\n"
        "            face_path, argb_image.data(), frame_width, frame_height,\n"
        "            frame_width * 4, &error)) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip face write failed: \" << error;\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n",
        "    std::string face_path = EnvString(\"WEBRTC_WAV2LIP_FACE_IMAGE_PATH\", \"\");\n"
        "    const std::string audio_path = (request_dir / \"recent_audio.wav\").string();\n"
        "    const std::string output_path = (request_dir / \"generated.mp4\").string();\n"
        "    std::string error;\n"
        "    if (face_path.empty()) {\n"
        "      face_path = (request_dir / \"last_real_frame.ppm\").string();\n"
        "      if (!webrtc_wav2lip::WriteArgbFrameToPpm(\n"
        "              face_path, argb_image.data(), frame_width, frame_height,\n"
        "              frame_width * 4, &error)) {\n"
        "        RTC_LOG(LS_WARNING) << \"Wav2Lip face write failed: \" << error;\n"
        "        inflight->store(false);\n"
        "        return;\n"
        "      }\n"
        "    }\n",
    )

    changed |= write_if_changed(source, text)
    return changed


def patch_face_context_streaming(src_dir: Path) -> bool:
    """Stream the latest real remote frame as a cached Wav2Lip face context."""
    changed = False
    header = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.h"
    text = header.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        "    void MaybePushAudioContext(int64_t now_ms);\n",
        "    void MaybePushFaceContext(int64_t now_ms,\n"
        "                              const uint8_t* argb_image,\n"
        "                              int width,\n"
        "                              int height);\n",
        header,
    )
    text = insert_after_once(
        text,
        "    std::shared_ptr<std::atomic<bool>> audio_push_inflight_;\n",
        "    std::shared_ptr<std::atomic<bool>> face_push_inflight_;\n",
        header,
    )
    text = insert_after_once(
        text,
        "    std::atomic<int64_t> last_face_push_ms_{0};\n",
        "    std::atomic<int64_t> last_face_detect_ms_{0};\n",
        header,
    )
    text = text.replace(
        "    std::atomic<int64_t> last_face_push_ms_{0};\n"
        "    std::atomic<int64_t> last_face_detect_ms_{0};\n"
        "    std::atomic<int64_t> last_face_push_ms_{0};\n",
        "    std::atomic<int64_t> last_face_push_ms_{0};\n"
        "    std::atomic<int64_t> last_face_detect_ms_{0};\n",
    )
    text = insert_after_once(
        text,
        "    std::atomic<int> audio_push_sequence_{0};\n",
        "    std::atomic<int> face_push_sequence_{0};\n",
        header,
    )
    changed |= write_if_changed(header, text)

    source = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.cc"
    text = source.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        "      audio_push_inflight_(std::make_shared<std::atomic<bool>>(false)),\n",
        "      face_push_inflight_(std::make_shared<std::atomic<bool>>(false)),\n",
        source,
    )
    face_method = (
        "void GtkMainWnd::VideoRenderer::MaybePushFaceContext(int64_t now_ms,\n"
        "                                                     const uint8_t* argb_image,\n"
        "                                                     int frame_width,\n"
        "                                                     int frame_height) {\n"
        "  if (EnvInt(\"WEBRTC_WAV2LIP_USE_FACE_CONTEXT\", 1) == 0 ||\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_STREAM_FACE_CONTEXT\", 1) == 0 ||\n"
        "      !EnvString(\"WEBRTC_WAV2LIP_FACE_IMAGE_PATH\", \"\").empty() ||\n"
        "      !argb_image || frame_width <= 0 || frame_height <= 0) {\n"
        "    return;\n"
        "  }\n"
        "  const int push_interval_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_FACE_PUSH_INTERVAL_MS\", 40);\n"
        "  const int detect_interval_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_FACE_DETECT_INTERVAL_MS\", 500);\n"
        "  const int64_t last_push_ms =\n"
        "      last_face_push_ms_.load(std::memory_order_relaxed);\n"
        "  if (now_ms - last_push_ms < push_interval_ms) {\n"
        "    return;\n"
        "  }\n"
        "  bool expected = false;\n"
        "  if (!face_push_inflight_->compare_exchange_strong(expected, true)) {\n"
        "    return;\n"
        "  }\n"
        "  last_face_push_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "  bool force_face_detect = false;\n"
        "  if (detect_interval_ms >= 0) {\n"
        "    const int64_t last_detect_ms =\n"
        "        last_face_detect_ms_.load(std::memory_order_relaxed);\n"
        "    if (last_detect_ms == 0 || now_ms - last_detect_ms >= detect_interval_ms) {\n"
        "      force_face_detect = true;\n"
        "      last_face_detect_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "    }\n"
        "  }\n"
        "  const int sequence = face_push_sequence_.fetch_add(1) + 1;\n"
        "  const size_t frame_size =\n"
        "      static_cast<size_t>(frame_width) * frame_height * 4;\n"
        "  std::vector<uint8_t> frame_copy(frame_size);\n"
        "  std::memcpy(frame_copy.data(), argb_image, frame_size);\n"
        "  auto inflight = face_push_inflight_;\n"
        "  std::thread([frame_copy = std::move(frame_copy), frame_width,\n"
        "               frame_height, now_ms, sequence, force_face_detect,\n"
        "               inflight]() mutable {\n"
        "    const std::filesystem::path runtime_dir = EnvString(\n"
        "        \"WEBRTC_WAV2LIP_RUNTIME_DIR\",\n"
        "        \"/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/runtime/native_bridge\");\n"
        "    const std::filesystem::path context_dir = runtime_dir / \"face_context\";\n"
        "    std::error_code fs_error;\n"
        "    std::filesystem::create_directories(context_dir, fs_error);\n"
        "    if (fs_error) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip face context mkdir failed: \"\n"
        "                          << fs_error.message();\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n"
        "    const std::string request_id =\n"
        "        \"face_ctx_\" + std::to_string(now_ms) + \"_\" +\n"
        "        std::to_string(sequence);\n"
        "    const std::string face_path =\n"
        "        (context_dir / (request_id + \".ppm\")).string();\n"
        "    std::string error;\n"
        "    if (!webrtc_wav2lip::WriteArgbFrameToPpm(face_path, frame_copy.data(),\n"
        "                                             frame_width, frame_height,\n"
        "                                             frame_width * 4, &error)) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip face context write failed: \"\n"
        "                          << error;\n"
        "      inflight->store(false);\n"
        "      return;\n"
        "    }\n"
        "    webrtc_wav2lip::Wav2LipRequest request;\n"
        "    request.request_id = request_id;\n"
        "    request.face_path = face_path;\n"
        "    request.force_face_detect = force_face_detect;\n"
        "    webrtc_wav2lip::Wav2LipBridge bridge(\n"
        "        EnvString(\"WEBRTC_WAV2LIP_HOST\", \"127.0.0.1\"),\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_PORT\", 19090));\n"
        "    const int track_face_context =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_TRACK_FACE_CONTEXT\", 1);\n"
        "    const webrtc_wav2lip::Wav2LipResponse response =\n"
        "        track_face_context ? bridge.TrackFaceContext(request)\n"
        "                           : bridge.SetFaceContext(request);\n"
        "    if (!response.ok) {\n"
        "      RTC_LOG(LS_WARNING) << \"Wav2Lip face context push failed: \"\n"
        "                          << response.error;\n"
        "    }\n"
        "    inflight->store(false);\n"
        "  }).detach();\n"
        "}\n\n"
    )
    if face_method.strip() not in text:
        display_marker = (
            "bool GtkMainWnd::VideoRenderer::DisplayNextGeneratedFrame(int64_t now_ms) {\n"
        )
        text = replace_once(
            text,
            display_marker,
            face_method + display_marker,
            source,
        )
    text = text.replace(
        "  const int sequence = face_push_sequence_.fetch_add(1) + 1;\n"
        "  std::vector<uint8_t> frame_copy(\n"
        "      argb_image, argb_image + frame_width * frame_height * 4);\n",
        "  const int sequence = face_push_sequence_.fetch_add(1) + 1;\n"
        "  const size_t frame_size =\n"
        "      static_cast<size_t>(frame_width) * frame_height * 4;\n"
        "  std::vector<uint8_t> frame_copy(frame_size);\n"
        "  std::memcpy(frame_copy.data(), argb_image, frame_size);\n",
    )
    if "MaybePushFaceContext(now_ms," not in text:
        text = insert_after_once(
            text,
            "    std::memcpy(last_real_image_.data(), image_.data(), image_.size());\n",
            "    MaybePushFaceContext(now_ms, image_.data(), width_, height_);\n",
            source,
        )
    text = text.replace(
        "  last_frame_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "  last_frame_ms_.store(now_ms, std::memory_order_relaxed);\n",
        "  last_frame_ms_.store(now_ms, std::memory_order_relaxed);\n",
    )
    text = text.replace(
        "    if (face_path.empty()) {\n"
        "      face_path = (request_dir / \"last_real_frame.ppm\").string();\n",
        "    const int use_face_context =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_USE_FACE_CONTEXT\", 1);\n"
        "    if (face_path.empty() && use_face_context == 0) {\n"
        "      face_path = (request_dir / \"last_real_frame.ppm\").string();\n",
    )
    text = insert_after_once(
        text,
        "                     << \" audio_chunk_ms=\" << audio_chunk_ms\n",
        "                     << \" use_face_context=\" << use_face_context\n",
        source,
    )
    changed |= write_if_changed(source, text)
    return changed


def patch_render_switch_policy(src_dir: Path) -> bool:
    """Split early generation risk from actual render switching/recovery."""
    changed = False
    header = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.h"
    text = header.read_text(encoding="utf-8")
    text = insert_after_once(
        text,
        "    void StopGeneratedPlayback();\n",
        "    bool GeneratedPlaybackVisible() const;\n"
        "    void RecordRealFrameArrival(int64_t now_ms);\n"
        "    bool ShouldReturnToRealStream(int64_t now_ms) const;\n",
        header,
    )
    text = insert_after_once(
        text,
        "    std::shared_ptr<GeneratedPlaybackState> generated_state_;\n",
        "    std::atomic<bool> generated_playback_visible_{false};\n",
        header,
    )
    text = insert_after_once(
        text,
        "    std::atomic<int64_t> first_remote_frame_ms_{0};\n",
        "    std::atomic<int64_t> last_real_arrival_ms_{0};\n"
        "    std::atomic<int64_t> estimated_render_interval_ms_{40};\n"
        "    std::atomic<int64_t> stable_real_start_ms_{0};\n",
        header,
    )
    text = insert_after_once(
        text,
        "    std::atomic<int> generation_sequence_{0};\n",
        "    std::atomic<int> consecutive_real_frames_{0};\n",
        header,
    )
    changed |= write_if_changed(header, text)

    source = src_dir / "examples" / "peerconnection" / "client" / "linux" / "main_wnd.cc"
    text = source.read_text(encoding="utf-8")
    text = text.replace("constexpr int64_t kWav2LipRenderGapTriggerMs = 180;\n", "")
    text = text.replace(
        "  if (is_remote_) {\n"
        "    monitor_source_id_ = g_timeout_add(\n"
        "        50, &GtkMainWnd::VideoRenderer::RenderGapMonitor, this);\n"
        "  }\n",
        "  if (is_remote_) {\n"
        "    int monitor_interval_ms =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_RENDER_MONITOR_INTERVAL_MS\", 20);\n"
        "    if (monitor_interval_ms <= 0) {\n"
        "      monitor_interval_ms = 20;\n"
        "    }\n"
        "    monitor_source_id_ = g_timeout_add(\n"
        "        monitor_interval_ms, &GtkMainWnd::VideoRenderer::RenderGapMonitor,\n"
        "        this);\n"
        "  }\n",
    )
    helper_block = (
        "constexpr int kWav2LipMinBufferedAudioMs = 250;\n\n"
        "int64_t NowMillis() {\n"
        "  return std::chrono::duration_cast<std::chrono::milliseconds>(\n"
        "             std::chrono::steady_clock::now().time_since_epoch())\n"
        "      .count();\n"
        "}\n\n"
        "std::string EnvString(const char* name, const char* fallback) {\n"
        "  const char* value = std::getenv(name);\n"
        "  if (!value || value[0] == '\\0') {\n"
        "    return fallback;\n"
        "  }\n"
        "  return value;\n"
        "}\n\n"
        "int EnvInt(const char* name, int fallback) {\n"
        "  const char* value = std::getenv(name);\n"
        "  if (!value || value[0] == '\\0') {\n"
        "    return fallback;\n"
        "  }\n"
        "  char* end = nullptr;\n"
        "  long parsed = std::strtol(value, &end, 10);\n"
        "  if (end == value || *end != '\\0') {\n"
        "    return fallback;\n"
        "  }\n"
        "  return static_cast<int>(parsed);\n"
        "}\n\n"
    )
    first_helper = text.find(helper_block)
    second_helper = text.find(helper_block, first_helper + len(helper_block))
    if first_helper != -1 and second_helper != -1:
        text = text[:second_helper] + text[second_helper + len(helper_block) :]
    text = insert_after_once(
        text,
        "  std::memcpy(image_.data(), frame.bgra.data(), frame.bgra.size());\n",
        "  generated_playback_visible_.store(true, std::memory_order_relaxed);\n",
        source,
    )

    old_stop = (
        "void GtkMainWnd::VideoRenderer::StopGeneratedPlayback() {\n"
        "  if (EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON\", 0) != 0) {\n"
        "    return;\n"
        "  }\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return;\n"
        "  }\n"
        "  std::lock_guard<std::mutex> lock(state->mutex);\n"
        "  if (state->active) {\n"
        "    RTC_LOG(LS_INFO) << \"Wav2Lip generated playback stopped request_id=\"\n"
        "                     << state->request_id;\n"
        "  }\n"
        "  state->active = false;\n"
        "}\n\n"
    )
    new_stop = (
        "void GtkMainWnd::VideoRenderer::StopGeneratedPlayback() {\n"
        "  generated_playback_visible_.store(false, std::memory_order_relaxed);\n"
        "  if (EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON\", 0) != 0) {\n"
        "    return;\n"
        "  }\n"
        "  auto state = generated_state_;\n"
        "  if (!state) {\n"
        "    return;\n"
        "  }\n"
        "  std::lock_guard<std::mutex> lock(state->mutex);\n"
        "  if (state->active) {\n"
        "    RTC_LOG(LS_INFO) << \"Wav2Lip generated playback stopped request_id=\"\n"
        "                     << state->request_id;\n"
        "  }\n"
        "  state->active = false;\n"
        "}\n\n"
        "bool GtkMainWnd::VideoRenderer::GeneratedPlaybackVisible() const {\n"
        "  return generated_playback_visible_.load(std::memory_order_relaxed);\n"
        "}\n\n"
        "void GtkMainWnd::VideoRenderer::RecordRealFrameArrival(int64_t now_ms) {\n"
        "  const int stable_gap_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_RETURN_STABLE_GAP_MS\", 80);\n"
        "  const int64_t last_real_ms =\n"
        "      last_real_arrival_ms_.exchange(now_ms, std::memory_order_relaxed);\n"
        "  if (last_real_ms <= 0) {\n"
        "    stable_real_start_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "    consecutive_real_frames_.store(1, std::memory_order_relaxed);\n"
        "    return;\n"
        "  }\n"
        "  const int64_t gap_ms = now_ms - last_real_ms;\n"
        "  if (gap_ms > 0 && gap_ms < 1000) {\n"
        "    int64_t estimate_ms =\n"
        "        estimated_render_interval_ms_.load(std::memory_order_relaxed);\n"
        "    if (estimate_ms <= 0) {\n"
        "      estimate_ms = kWav2LipGeneratedFrameIntervalMs;\n"
        "    }\n"
        "    int64_t updated_ms = (estimate_ms * 7 + gap_ms) / 8;\n"
        "    if (updated_ms < 20) {\n"
        "      updated_ms = 20;\n"
        "    } else if (updated_ms > 200) {\n"
        "      updated_ms = 200;\n"
        "    }\n"
        "    estimated_render_interval_ms_.store(updated_ms,\n"
        "                                        std::memory_order_relaxed);\n"
        "  }\n"
        "  if (gap_ms > 0 && gap_ms <= stable_gap_ms) {\n"
        "    consecutive_real_frames_.fetch_add(1, std::memory_order_relaxed);\n"
        "  } else {\n"
        "    stable_real_start_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "    consecutive_real_frames_.store(1, std::memory_order_relaxed);\n"
        "  }\n"
        "}\n\n"
        "bool GtkMainWnd::VideoRenderer::ShouldReturnToRealStream(\n"
        "    int64_t now_ms) const {\n"
        "  if (EnvInt(\"WEBRTC_WAV2LIP_RETURN_IMMEDIATE\", 0) != 0) {\n"
        "    return true;\n"
        "  }\n"
        "  int required_frames =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_RETURN_CONSECUTIVE_REAL_FRAMES\", 3);\n"
        "  if (required_frames < 1) {\n"
        "    required_frames = 1;\n"
        "  }\n"
        "  int stable_ms = EnvInt(\"WEBRTC_WAV2LIP_RETURN_STABLE_MS\", 120);\n"
        "  if (stable_ms < 0) {\n"
        "    stable_ms = 0;\n"
        "  }\n"
        "  const int consecutive_frames =\n"
        "      consecutive_real_frames_.load(std::memory_order_relaxed);\n"
        "  const int64_t stable_start_ms =\n"
        "      stable_real_start_ms_.load(std::memory_order_relaxed);\n"
        "  return consecutive_frames >= required_frames && stable_start_ms > 0 &&\n"
        "         now_ms - stable_start_ms >= stable_ms;\n"
        "}\n\n"
    )
    if "GeneratedPlaybackVisible() const" not in text:
        text = replace_once(text, old_stop, new_stop, source)
    else:
        text = text.replace(old_stop, new_stop)

    timer_start = text.find("bool GtkMainWnd::VideoRenderer::OnRenderGapTimer() {\n")
    generation_start = text.find(
        "void GtkMainWnd::VideoRenderer::MaybeStartWav2LipGeneration(",
        timer_start,
    )
    if timer_start == -1 or generation_start == -1:
        raise RuntimeError(f"could not find render gap timer block in {source}")
    new_timer = (
        "bool GtkMainWnd::VideoRenderer::OnRenderGapTimer() {\n"
        "  if (!is_remote_) {\n"
        "    return false;\n"
        "  }\n"
        "  const int64_t last_frame_ms = last_frame_ms_.load(std::memory_order_relaxed);\n"
        "  if (last_frame_ms == 0) {\n"
        "    return true;\n"
        "  }\n"
        "  const int64_t now_ms = NowMillis();\n"
        "  MaybePushAudioContext(now_ms);\n"
        "  const int64_t render_gap_ms = now_ms - last_frame_ms;\n"
        "  int estimated_interval_ms = static_cast<int>(\n"
        "      estimated_render_interval_ms_.load(std::memory_order_relaxed));\n"
        "  if (estimated_interval_ms <= 0) {\n"
        "    estimated_interval_ms = kWav2LipGeneratedFrameIntervalMs;\n"
        "  }\n"
        "  int switch_slack_ms = EnvInt(\"WEBRTC_WAV2LIP_SWITCH_SLACK_MS\", 40);\n"
        "  if (switch_slack_ms < 0) {\n"
        "    switch_slack_ms = 0;\n"
        "  }\n"
        "  int switch_gap_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_SWITCH_GAP_MS\",\n"
        "             estimated_interval_ms + switch_slack_ms);\n"
        "  if (switch_gap_ms < estimated_interval_ms) {\n"
        "    switch_gap_ms = estimated_interval_ms;\n"
        "  }\n"
        "  int switch_min_gap_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_SWITCH_MIN_GAP_MS\", 80);\n"
        "  if (switch_min_gap_ms < estimated_interval_ms) {\n"
        "    switch_min_gap_ms = estimated_interval_ms;\n"
        "  }\n"
        "  if (switch_gap_ms < switch_min_gap_ms) {\n"
        "    switch_gap_ms = switch_min_gap_ms;\n"
        "  }\n"
        "  const int generation_risk_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_GENERATION_RISK_MS\", 60);\n"
        "  int prefetch_threshold_frames =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_PREFETCH_THRESHOLD_FRAMES\",\n"
        "             EnvInt(\"WEBRTC_WAV2LIP_QUEUE_LOW_WATERMARK_FRAMES\", 3));\n"
        "  if (prefetch_threshold_frames < 0) {\n"
        "    prefetch_threshold_frames = 0;\n"
        "  }\n"
        "  const int64_t last_generation_ms =\n"
        "      last_generation_trigger_ms_.load(std::memory_order_relaxed);\n"
        "  const bool switch_due = render_gap_ms >= switch_gap_ms;\n"
        "  const bool risk_due = render_gap_ms >= generation_risk_ms;\n"
        "  bool displayed_generated = false;\n"
        "  if (switch_due) {\n"
        "    displayed_generated = DisplayNextGeneratedFrame(now_ms);\n"
        "  }\n"
        "  const size_t queue_remaining = GeneratedQueueRemaining();\n"
        "  if (displayed_generated &&\n"
        "      queue_remaining > static_cast<size_t>(prefetch_threshold_frames)) {\n"
        "    return true;\n"
        "  }\n"
        "  if (!risk_due && !switch_due) {\n"
        "    if (EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON\", 0) != 0) {\n"
        "      const int64_t first_remote_frame_ms =\n"
        "          first_remote_frame_ms_.load(std::memory_order_relaxed);\n"
        "      const int always_on_startup_delay_ms =\n"
        "          EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON_STARTUP_DELAY_MS\",\n"
        "                 EnvInt(\"WEBRTC_WAV2LIP_AUDIO_CONTEXT_MS\", 500) + 1000);\n"
        "      if (first_remote_frame_ms > 0 &&\n"
        "          now_ms - first_remote_frame_ms < always_on_startup_delay_ms) {\n"
        "        return true;\n"
        "      }\n"
        "      const int64_t last_audio_push_ms =\n"
        "          last_audio_push_ms_.load(std::memory_order_relaxed);\n"
        "      const int audio_context_grace_ms =\n"
        "          EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON_AUDIO_CONTEXT_GRACE_MS\", 80);\n"
        "      if (last_audio_push_ms == 0 ||\n"
        "          now_ms - last_audio_push_ms < audio_context_grace_ms) {\n"
        "        return true;\n"
        "      }\n"
        "      const int always_on_interval_ms =\n"
        "          EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON_INTERVAL_MS\", 400);\n"
        "      if (now_ms - last_generation_ms < always_on_interval_ms) {\n"
        "        return true;\n"
        "      }\n"
        "    } else {\n"
        "      return true;\n"
        "    }\n"
        "  }\n"
        "  const int generation_cooldown_ms =\n"
        "      EnvInt(\"WEBRTC_WAV2LIP_GENERATION_COOLDOWN_MS\", 120);\n"
        "  if (now_ms - last_generation_ms < generation_cooldown_ms) {\n"
        "    return true;\n"
        "  }\n\n"
        "  std::vector<uint8_t> argb_image;\n"
        "  int frame_width = 0;\n"
        "  int frame_height = 0;\n"
        "  if (!CopyLastRealFrame(&argb_image, &frame_width, &frame_height)) {\n"
        "    return true;\n"
        "  }\n"
        "  const char* reason = \"playout_risk_prefetch\";\n"
        "  if (displayed_generated) {\n"
        "    reason = \"generated_queue_low\";\n"
        "  } else if (switch_due) {\n"
        "    reason = \"render_deadline_miss\";\n"
        "  } else if (EnvInt(\"WEBRTC_WAV2LIP_ALWAYS_ON\", 0) != 0 && !risk_due) {\n"
        "    reason = \"always_on_prefetch\";\n"
        "  }\n"
        "  MaybeStartWav2LipGeneration(render_gap_ms, reason, std::move(argb_image),\n"
        "                              frame_width, frame_height);\n"
        "  return true;\n"
        "}\n\n"
    )
    text = text[:timer_start] + new_timer + text[generation_start:]

    onframe_start = text.find(
        "void GtkMainWnd::VideoRenderer::OnFrame(const webrtc::VideoFrame& video_frame) {\n"
    )
    if onframe_start == -1:
        raise RuntimeError(f"could not find OnFrame in {source}")
    onframe_end = text.find("\n}\n", onframe_start)
    if onframe_end == -1:
        raise RuntimeError(f"could not find OnFrame end in {source}")
    old_onframe = text[onframe_start : onframe_end + 3]
    new_onframe = (
        "void GtkMainWnd::VideoRenderer::OnFrame(const webrtc::VideoFrame& video_frame) {\n"
        "  const int64_t now_ms = NowMillis();\n"
        "  if (is_remote_) {\n"
        "    int64_t expected_first_ms = 0;\n"
        "    first_remote_frame_ms_.compare_exchange_strong(expected_first_ms,\n"
        "                                                    now_ms);\n"
        "    const int test_freeze_after_ms =\n"
        "        EnvInt(\"WEBRTC_WAV2LIP_TEST_FREEZE_AFTER_MS\", 0);\n"
        "    const int64_t first_remote_frame_ms =\n"
        "        first_remote_frame_ms_.load(std::memory_order_relaxed);\n"
        "    if (test_freeze_after_ms > 0 && first_remote_frame_ms > 0 &&\n"
        "        now_ms - first_remote_frame_ms >= test_freeze_after_ms) {\n"
        "      return;\n"
        "    }\n"
        "    RecordRealFrameArrival(now_ms);\n"
        "  }\n\n"
        "  webrtc::scoped_refptr<webrtc::I420BufferInterface> buffer(\n"
        "      video_frame.video_frame_buffer()->ToI420());\n"
        "  if (video_frame.rotation() != webrtc::kVideoRotation_0) {\n"
        "    buffer = webrtc::I420Buffer::Rotate(*buffer, video_frame.rotation());\n"
        "  }\n"
        "  const int real_width = buffer->width();\n"
        "  const int real_height = buffer->height();\n"
        "  webrtc::Buffer real_image;\n"
        "  real_image.SetSize(real_width * real_height * 4);\n"
        "  libyuv::I420ToARGB(buffer->DataY(), buffer->StrideY(), buffer->DataU(),\n"
        "                     buffer->StrideU(), buffer->DataV(), buffer->StrideV(),\n"
        "                     real_image.data(), real_width * 4, real_width,\n"
        "                     real_height);\n\n"
        "  bool render_real_frame = true;\n"
        "  gdk_threads_enter();\n"
        "  if (is_remote_) {\n"
        "    last_real_width_ = real_width;\n"
        "    last_real_height_ = real_height;\n"
        "    last_real_image_.SetSize(real_image.size());\n"
        "    std::memcpy(last_real_image_.data(), real_image.data(), real_image.size());\n"
        "    MaybePushFaceContext(now_ms, real_image.data(), real_width, real_height);\n"
        "    if (GeneratedPlaybackVisible() && !ShouldReturnToRealStream(now_ms)) {\n"
        "      render_real_frame = false;\n"
        "    }\n"
        "  }\n"
        "  if (render_real_frame) {\n"
        "    width_ = real_width;\n"
        "    height_ = real_height;\n"
        "    image_.SetSize(real_image.size());\n"
        "    std::memcpy(image_.data(), real_image.data(), real_image.size());\n"
        "    last_frame_ms_.store(now_ms, std::memory_order_relaxed);\n"
        "  }\n"
        "  gdk_threads_leave();\n\n"
        "  if (!render_real_frame) {\n"
        "    return;\n"
        "  }\n"
        "  StopGeneratedPlayback();\n"
        "  g_idle_add(Redraw, main_wnd_);\n"
        "}\n"
    )
    if "render_real_frame" not in old_onframe:
        text = text[:onframe_start] + new_onframe + text[onframe_end + 3 :]
    changed |= write_if_changed(source, text)
    return changed


def patch_native_integration(src_dir: Path) -> dict[str, bool]:
    return {
        "main_wnd_interface": patch_main_wnd_interface(src_dir),
        "conductor": patch_conductor(src_dir),
        "linux_main_wnd": patch_linux_main_wnd(src_dir),
        "generated_playback": patch_generated_playback(src_dir),
        "generated_queue_prefetch": patch_generated_queue_prefetch(src_dir),
        "face_context_streaming": patch_face_context_streaming(src_dir),
        "render_switch_policy": patch_render_switch_policy(src_dir),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src-dir", default="/home/widen/webrtc-receiver-wav2lip-native/src")
    parser.add_argument("--lab-dir", default="/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    src_dir = Path(args.src_dir).expanduser().resolve()
    lab_dir = Path(args.lab_dir).expanduser().resolve()
    if not (src_dir / "examples" / "BUILD.gn").exists():
        raise FileNotFoundError(f"not a WebRTC src checkout: {src_dir}")
    copy_overlay(lab_dir, src_dir)
    patched = patch_build_gn(src_dir)
    integration_patches = patch_native_integration(src_dir)
    print(f"[apply-native-overlay] src_dir={src_dir}")
    print(f"[apply-native-overlay] lab_dir={lab_dir}")
    print(f"[apply-native-overlay] build_gn_patched={patched}")
    for name, changed in integration_patches.items():
        print(f"[apply-native-overlay] {name}_patched={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
