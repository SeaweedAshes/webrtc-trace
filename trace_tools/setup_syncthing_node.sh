#!/bin/bash

set -euo pipefail

SHARED_DIR="$HOME/Sync/webrtc-trace-runs"
FOLDER_ID="webrtc-trace-runs"
FOLDER_LABEL="WebRTC Trace Runs"
GUI_PORT=8384
ST_HOME="$HOME/.local/state/syncthing-webrtc-trace"
SERVICE_NAME="syncthing-webrtc-trace"
DEVICE_NAME="${HOSTNAME:-$(hostname)}"
API_KEY=""
SKIP_INSTALL=0

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --shared-dir PATH           Local folder to sync (default: $SHARED_DIR)
  --folder-id ID              Syncthing folder ID (default: $FOLDER_ID)
  --folder-label LABEL        Syncthing folder label (default: $FOLDER_LABEL)
  --gui-port N                Syncthing GUI/API port on localhost (default: $GUI_PORT)
  --home-dir PATH             Syncthing home/config dir (default: $ST_HOME)
  --service-name NAME         User systemd service name (default: $SERVICE_NAME)
  --device-name NAME          Local Syncthing device name (default: hostname)
  --api-key KEY               Explicit API key for the local GUI/API
  --skip-install              Do not install Syncthing even if missing
EOF
  exit 1
}

log() {
  printf '[syncthing-setup] %s\n' "$*"
}

die() {
  printf '[syncthing-setup] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --shared-dir) SHARED_DIR="$2"; shift 2 ;;
      --folder-id) FOLDER_ID="$2"; shift 2 ;;
      --folder-label) FOLDER_LABEL="$2"; shift 2 ;;
      --gui-port) GUI_PORT="$2"; shift 2 ;;
      --home-dir) ST_HOME="$2"; shift 2 ;;
      --service-name) SERVICE_NAME="$2"; shift 2 ;;
      --device-name) DEVICE_NAME="$2"; shift 2 ;;
      --api-key) API_KEY="$2"; shift 2 ;;
      --skip-install) SKIP_INSTALL=1; shift ;;
      *) usage ;;
    esac
  done

  [[ "$GUI_PORT" =~ ^[0-9]+$ ]] || die "gui-port must be numeric"
}

install_syncthing() {
  if command -v syncthing >/dev/null 2>&1; then
    log "syncthing already installed: $(command -v syncthing)"
    return
  fi

  (( SKIP_INSTALL == 0 )) || die "syncthing not installed and --skip-install was set"

  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y syncthing
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y syncthing
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y syncthing
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm syncthing
  else
    die "unsupported package manager; install Syncthing manually"
  fi

  command -v syncthing >/dev/null 2>&1 || die "syncthing still missing after install"
}

random_api_key() {
  python3 - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
}

read_api_key() {
  python3 - "$ST_HOME/config.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
path = sys.argv[1]
root = ET.parse(path).getroot()
gui = root.find('./gui')
if gui is None:
    raise SystemExit(1)
apikey = gui.findtext('apikey', default='')
print(apikey)
PY
}

write_user_service() {
  local service_dir="$HOME/.config/systemd/user"
  mkdir -p "$service_dir"
  cat > "$service_dir/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Syncthing for WebRTC trace sync
After=network-online.target

[Service]
ExecStart=$(command -v syncthing) --home="$ST_HOME" --no-browser --no-restart --gui-address="127.0.0.1:$GUI_PORT"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
}

start_service() {
  if command -v systemctl >/dev/null 2>&1; then
    write_user_service
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME.service"
    systemctl --user restart "$SERVICE_NAME.service"
  else
    nohup "$(command -v syncthing)" --home="$ST_HOME" --no-browser --no-restart --gui-address="127.0.0.1:$GUI_PORT" >/tmp/"$SERVICE_NAME".log 2>&1 &
  fi
}

wait_for_api() {
  local deadline=$((SECONDS + 30))
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

main() {
  parse_args "$@"
  install_syncthing

  mkdir -p "$SHARED_DIR" "$ST_HOME"

  if [[ ! -f "$ST_HOME/config.xml" ]]; then
    if [[ -z "$API_KEY" ]]; then
      API_KEY="$(random_api_key)"
    fi
    log "generating fresh Syncthing config in $ST_HOME"
    syncthing --generate="$ST_HOME" --gui-address="127.0.0.1:$GUI_PORT" --gui-apikey="$API_KEY" >/dev/null
  fi

  API_KEY="$(read_api_key)"
  [[ -n "$API_KEY" ]] || die "failed to read Syncthing API key from $ST_HOME/config.xml"

  start_service
  wait_for_api || die "Syncthing API did not become ready on 127.0.0.1:$GUI_PORT"

  log "ready"
  log "  device_id=$(local_device_id)"
  log "  shared_dir=$SHARED_DIR"
  log "  folder_id=$FOLDER_ID"
  log "  folder_label=$FOLDER_LABEL"
  log "  home_dir=$ST_HOME"
  log "  gui_url=http://127.0.0.1:$GUI_PORT"
  log "  service_name=$SERVICE_NAME"
}

main "$@"
