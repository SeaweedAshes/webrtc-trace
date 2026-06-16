# Receiver Wav2Lip WebRTC Lab

This lab is for a fresh WebRTC native checkout dedicated to live receiver-side
audio-driven talking-face concealment.

It is intentionally separate from:

- `/home/widen/webrtc-trace`
- `/home/widen/webrtc-trace-bootstrap`
- `/home/widen/webrtc-wav2lip-lab`
- `/home/widen/webrtc-wav2lip-native`

Default new checkout:

```text
/home/widen/webrtc-receiver-wav2lip-native
```

## Build

```bash
/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/bootstrap_receiver_wav2lip_native.sh
```

By default this syncs WebRTC to:

```text
f172c4f6e0ced37758d85e060e66a3e06166d9f6
```

Override with:

```bash
WEBRTC_REVISION=<commit> \
/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/bootstrap_receiver_wav2lip_native.sh
```

## Receiver Machine

Run the signaling server on the receiver if the receiver is the public/forwarded
host:

```bash
SIGNAL_PORT=8884 \
/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/run_receiver_signal_server.sh
```

Run the receiver client with Wav2Lip enabled:

```bash
SERVER_HOST=localhost \
SIGNAL_PORT=8884 \
WAV2LIP_PORT=19090 \
/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/run_receiver_concealment_client.sh
```

Only this receiver process starts the Wav2Lip persistent server and sets
`WEBRTC_WAV2LIP_*` environment variables.

## Sender Machine

The sender only sends normal WebRTC media:

```bash
SERVER_HOST=<receiver-host-or-ip> \
SIGNAL_PORT=8884 \
/home/widen/webrtc-checkout/webrtc_receiver_wav2lip_lab/scripts/run_sender_plain_client.sh
```

The sender script does not start Wav2Lip and does not set `WEBRTC_WAV2LIP_*`.

## Current Prototype Policy

Temporary live policy:

- generation trigger: `render_gap >= 40ms`
- display switch: `render_gap >= 80ms` and generated frame is ready
- return: immediately at first real rendered frame

Face context:

- push latest real receiver frame every `40ms`
- force face detector every `500ms`
- use OpenCV template-matching tracker between detector refreshes
- if tracker score `< 0.55`, fallback to detector

This policy is for live end-to-end validation. The final switch policy can later
be replaced with the score-based controller.
