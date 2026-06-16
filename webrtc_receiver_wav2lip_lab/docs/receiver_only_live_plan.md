# Receiver-Only Live WebRTC + Wav2Lip Plan

This lab is separate from the existing trace/replay checkout and from the older
`webrtc-wav2lip-lab`. Its default checkout is:

```text
/home/widen/webrtc-receiver-wav2lip-native
```

The receiver is the only side that runs audio-driven talking-face generation.
The sender uses the same built WebRTC binary, but no `WEBRTC_WAV2LIP_*`
environment variables are set on the sender.

## Receiver Responsibilities

- Run `peerconnection_server` if the receiver is the signaling host.
- Run the Wav2Lip persistent server on localhost.
- Join the WebRTC call as receiver.
- Cache recent audio from the remote sender.
- Cache the latest real rendered face frame.
- Run detector every 500ms and use OpenCV template tracking between detector
  refreshes.
- Generate and display talking-face frames only when the receiver-side render
  switch policy decides to show generated video.

## Sender Responsibilities

- Join the same signaling server.
- Send normal camera/audio media.
- Do not run Wav2Lip.
- Do not perform freeze concealment.

## Runtime Split

Receiver:

```bash
~/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/run_receiver_signal_server.sh
~/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/run_receiver_concealment_client.sh
```

Sender:

```bash
SERVER_HOST=<receiver-ip-or-public-host> \
~/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/run_sender_plain_client.sh
```

## Current Temporary Policy

- Generation trigger: `render_gap >= 40ms`
- Visible switch: `render_gap >= 80ms` and generated frame ready
- Return: first real rendered frame, via immediate-return runtime knobs

This is not the final score-based policy. It is the simple live prototype
policy for validating end-to-end operation.
