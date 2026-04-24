# webrtc-trace seed repo

This repository is meant to stay small.
It does not vendor the full WebRTC checkout.

## What is stored here

- `.gclient`: upstream WebRTC checkout definition
- `src_base_commit.txt`: pinned upstream `src` revision for reproducible sync
- `overlay/src/...`: instrumented source files copied on top of upstream WebRTC
- `trace_tools/`: bootstrap, record, trace-build, and replay scripts

## First bootstrap

```bash
chmod +x trace_tools/*.sh

./trace_tools/bootstrap_trace_lab.sh \
  --repo-url <this-repo-url> \
  --branch main \
  --workdir /tmp/webrtc-trace-bootstrap
```

The bootstrap script will:

1. Clone or update this repo.
2. Reuse `gclient`, `gn`, and `autoninja` if already installed.
3. Install or update `depot_tools` if they are missing.
4. Run `gclient sync` for the pinned WebRTC revision.
5. Copy `overlay/src/...` files into the synced `src/`.
6. Run `gn gen` and `autoninja`.

## After bootstrap

Record one live run and build a replay trace:

```bash
./trace_tools/run_record_and_build_trace.sh \
  --run-dir /tmp/trace-record
```

Replay a generated trace:

```bash
./trace_tools/run_replay_from_trace.sh \
  --run-dir /tmp/trace-replay \
  --trace /tmp/trace-record/generated_trace.csv
```

## Updating instrumentation

When you change local instrumented files, copy the updated files back into
`overlay/src/...`, commit, and push.
