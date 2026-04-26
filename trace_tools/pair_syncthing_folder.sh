#!/bin/bash

set -euo pipefail

REMOTE_DEVICE_ID=""
REMOTE_DEVICE_NAME="peer"
REMOTE_ADDRESS="dynamic"
SHARED_DIR="$HOME/Sync/webrtc-trace-runs"
FOLDER_ID="webrtc-trace-runs"
FOLDER_LABEL="WebRTC Trace Runs"
GUI_PORT=8384
ST_HOME="$HOME/.local/state/syncthing-webrtc-trace"
FOLDER_TYPE="sendreceive"

usage() {
  cat <<EOF
Usage: $0 --remote-device-id ID [options]

Required:
  --remote-device-id ID       Remote Syncthing device ID

Options:
  --remote-device-name NAME   Label for the remote device (default: $REMOTE_DEVICE_NAME)
  --remote-address ADDR       Remote address, or 'dynamic' (default: $REMOTE_ADDRESS)
  --shared-dir PATH           Local folder to sync (default: $SHARED_DIR)
  --folder-id ID              Syncthing folder ID (default: $FOLDER_ID)
  --folder-label LABEL        Syncthing folder label (default: $FOLDER_LABEL)
  --folder-type TYPE          sendreceive|sendonly|receiveonly|receiveencrypted
  --gui-port N                Local Syncthing GUI/API port (default: $GUI_PORT)
  --home-dir PATH             Syncthing home/config dir (default: $ST_HOME)
EOF
  exit 1
}

log() {
  printf '[syncthing-pair] %s\n' "$*"
}

die() {
  printf '[syncthing-pair] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --remote-device-id) REMOTE_DEVICE_ID="$2"; shift 2 ;;
      --remote-device-name) REMOTE_DEVICE_NAME="$2"; shift 2 ;;
      --remote-address) REMOTE_ADDRESS="$2"; shift 2 ;;
      --shared-dir) SHARED_DIR="$2"; shift 2 ;;
      --folder-id) FOLDER_ID="$2"; shift 2 ;;
      --folder-label) FOLDER_LABEL="$2"; shift 2 ;;
      --folder-type) FOLDER_TYPE="$2"; shift 2 ;;
      --gui-port) GUI_PORT="$2"; shift 2 ;;
      --home-dir) ST_HOME="$2"; shift 2 ;;
      *) usage ;;
    esac
  done

  [[ -n "$REMOTE_DEVICE_ID" ]] || usage
  [[ "$GUI_PORT" =~ ^[0-9]+$ ]] || die "gui-port must be numeric"
}

read_api_key() {
  python3 - "$ST_HOME/config.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
path = sys.argv[1]
root = ET.parse(path).getroot()
gui = root.find('./gui')
apikey = gui.findtext('apikey', default='') if gui is not None else ''
print(apikey)
PY
}

wait_for_api() {
  local deadline=$((SECONDS + 20))
  while (( SECONDS < deadline )); do
    if curl -fsS "http://127.0.0.1:$GUI_PORT/rest/noauth/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

local_device_id() {
  syncthing --home="$ST_HOME" --device-id
}

apply_pairing() {
  local api_key="$1"
  local local_id
  local_id="$(local_device_id)"

  python3 - "$api_key" "$GUI_PORT" "$REMOTE_DEVICE_ID" "$REMOTE_DEVICE_NAME" "$REMOTE_ADDRESS" "$SHARED_DIR" "$FOLDER_ID" "$FOLDER_LABEL" "$FOLDER_TYPE" "$local_id" <<'PY'
import json
import sys
import urllib.request

api_key, gui_port, remote_id, remote_name, remote_addr, shared_dir, folder_id, folder_label, folder_type, local_id = sys.argv[1:]
base = f"http://127.0.0.1:{gui_port}"
headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

def req(method, path, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r) as resp:
        body = resp.read()
        if not body:
            return None
        return json.loads(body)

devices = req("GET", "/rest/config/devices") or []
if not any(d.get("deviceID") == remote_id for d in devices):
    device = req("GET", "/rest/config/defaults/device")
    device["deviceID"] = remote_id
    device["name"] = remote_name
    device["addresses"] = [remote_addr]
    req("POST", "/rest/config/devices", device)

folders = req("GET", "/rest/config/folders") or []
folder = next((f for f in folders if f.get("id") == folder_id), None)
if folder is None:
    folder = req("GET", "/rest/config/defaults/folder")
    folder["id"] = folder_id
    folder["label"] = folder_label
    folder["path"] = shared_dir
    folder["type"] = folder_type
    folder["devices"] = [
        {"deviceID": local_id, "introducedBy": "", "encryptionPassword": ""},
        {"deviceID": remote_id, "introducedBy": "", "encryptionPassword": ""},
    ]
    req("POST", "/rest/config/folders", folder)
else:
    folder["path"] = shared_dir
    folder["label"] = folder_label
    folder["type"] = folder_type
    ids = {d.get("deviceID") for d in folder.get("devices", [])}
    if local_id not in ids:
        folder.setdefault("devices", []).append({"deviceID": local_id, "introducedBy": "", "encryptionPassword": ""})
    if remote_id not in ids:
        folder.setdefault("devices", []).append({"deviceID": remote_id, "introducedBy": "", "encryptionPassword": ""})
    req("PUT", f"/rest/config/folders/{folder_id}", folder)

restart_needed = req("GET", "/rest/config/restart-required")
if restart_needed and restart_needed.get("requiresRestart"):
    req("POST", "/rest/system/restart")
PY
}

main() {
  parse_args "$@"
  [[ -f "$ST_HOME/config.xml" ]] || die "missing Syncthing config at $ST_HOME/config.xml; run setup_syncthing_node.sh first"
  mkdir -p "$SHARED_DIR"

  wait_for_api || die "Syncthing API is not reachable on 127.0.0.1:$GUI_PORT"
  local api_key
  api_key="$(read_api_key)"
  [[ -n "$api_key" ]] || die "failed to read Syncthing API key"

  apply_pairing "$api_key"

  log "paired"
  log "  local_device_id=$(local_device_id)"
  log "  remote_device_id=$REMOTE_DEVICE_ID"
  log "  shared_dir=$SHARED_DIR"
  log "  folder_id=$FOLDER_ID"
}

main "$@"
