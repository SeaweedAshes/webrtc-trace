# Wav2Lip Native Overlay

This overlay contains the first C++ building blocks for the dedicated
WebRTC Native receiver that will connect to the Wav2Lip localhost TCP server.

It is not wired into `peerconnection_client` yet. The current goal is to keep the
trace/replay WebRTC tree untouched while staging the integration pieces.

## Files

- `wav2lip_bridge.h/.cc`: synchronous localhost TCP client for newline-JSON
  requests to the Python Wav2Lip server.
- `concealment_policy.h/.cc`: state-machine skeleton with independently
  toggleable trigger and recovery modules.

## Next Native Integration Step

The next patch should connect these pieces to the receiver render path:

1. Collect receiver signals:
   - video packet gap
   - frame decode/render gap
   - render gap
   - optional effective playout headroom
2. Save last good rendered frame as a face image.
3. Save recent audio PCM as a 1 second WAV chunk.
4. Call `Wav2LipBridge::Generate`.
5. Route generated frames to the research renderer.
