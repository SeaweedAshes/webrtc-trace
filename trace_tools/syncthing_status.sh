#!/bin/bash

set -euo pipefail

GUI_PORT=8384
ST_HOME="$HOME/.local/state/syncthing-webrtc-trace"
FOLDER_ID=""

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --gui-port N                Local Syncthing GUI/API port (default: $GUI_PORT)
  --home-dir PATH             Syncthing home/config dir (default: $ST_HOME)
  --folder-id ID              Optional folder ID to inspect
EOF
  exit 1
}

die() {
  printf '[syncthing-status] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --gui-port) GUI_PORT="$2"; shift 2 ;;
      --home-dir) ST_HOME="$2"; shift 2 ;;
      --folder-id) FOLDER_ID="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  [[ "$GUI_PORT" =~ ^[0-9]+$ ]] || die "gui-port must be numeric"
}

read_api_key() {
  python3 - "$ST_HOME/config.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
gui = root.find('./gui')
print(gui.findtext('apikey', default='') if gui is not None else '')
PY
}

main() {
  parse_args "$@"
  [[ -f "$ST_HOME/config.xml" ]] || die "missing Syncthing config at $ST_HOME/config.xml"
  local api_key
  api_key="$(read_api_key)"
  [[ -n "$api_key" ]] || die "failed to read API key"

  python3 - "$api_key" "$GUI_PORT" "$FOLDER_ID" <<'PY'
import json
import sys
import urllib.request

api_key, gui_port, folder_id = sys.argv[1:]
base = f"http://127.0.0.1:{gui_port}"
headers = {"X-API-Key": api_key}

def req(path):
    r = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())

status = req("/rest/system/status")
connections = req("/rest/system/connections")
print(f"myID={status.get('myID','')}")
for device_id, info in sorted((connections.get("connections") or {}).items()):
    print(f"device {device_id} connected={info.get('connected')} paused={info.get('paused')}")
if folder_id:
    try:
        folder = req(f"/rest/db/status?folder={folder_id}")
        print(f"folder {folder_id} state={folder.get('state')} needBytes={folder.get('needBytes')}")
    except Exception as exc:
        print(f"folder {folder_id} status_error={exc}")
PY
}

main "$@"
