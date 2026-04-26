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
  --gui-port N                Ignored in offline-config mode; kept for compatibility
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
}

local_device_id() {
  syncthing --home="$ST_HOME" --device-id
}

apply_pairing() {
  local local_id
  local config_path="$ST_HOME/config.xml"
  local_id="$(local_device_id)"

  python3 - "$config_path" "$REMOTE_DEVICE_ID" "$REMOTE_DEVICE_NAME" "$REMOTE_ADDRESS" "$SHARED_DIR" "$FOLDER_ID" "$FOLDER_LABEL" "$FOLDER_TYPE" "$local_id" <<'PY'
import copy
import os
import sys
import xml.etree.ElementTree as ET

config_path, remote_id, remote_name, remote_addr, shared_dir, folder_id, folder_label, folder_type, local_id = sys.argv[1:]

tree = ET.parse(config_path)
root = tree.getroot()

devices = root.findall('./device')
remote_dev = None
local_dev = None
for dev in devices:
    if dev.attrib.get('id') == remote_id:
        remote_dev = dev
    if dev.attrib.get('id') == local_id:
        local_dev = dev

if local_dev is None:
    local_dev = ET.Element('device', {
        'id': local_id,
        'name': os.uname().nodename,
        'compression': 'metadata',
        'introducer': 'false',
        'skipIntroductionRemovals': 'false',
        'introducedBy': '',
    })
    for tag, text in [
        ('address', 'dynamic'),
        ('paused', 'false'),
        ('autoAcceptFolders', 'false'),
        ('maxSendKbps', '0'),
        ('maxRecvKbps', '0'),
        ('maxRequestKiB', '0'),
        ('untrusted', 'false'),
        ('remoteGUIPort', '0'),
    ]:
        child = ET.SubElement(local_dev, tag)
        child.text = text
    root.append(local_dev)

if remote_dev is None:
    template = copy.deepcopy(local_dev)
    template.attrib['id'] = remote_id
    template.attrib['name'] = remote_name
    template.attrib['introducedBy'] = ''
    addr = template.find('./address')
    if addr is None:
        addr = ET.SubElement(template, 'address')
    addr.text = remote_addr
    root.append(template)
else:
    remote_dev.attrib['name'] = remote_name
    addr = remote_dev.find('./address')
    if addr is None:
        addr = ET.SubElement(remote_dev, 'address')
    addr.text = remote_addr

folder = None
for f in root.findall('./folder'):
    if f.attrib.get('id') == folder_id:
        folder = f
        break

if folder is None:
    default_folder = root.find('./folder')
    if default_folder is not None:
        folder = copy.deepcopy(default_folder)
        for child in list(folder.findall('./device')):
            folder.remove(child)
    else:
        folder = ET.Element('folder')
        ET.SubElement(folder, 'filesystemType').text = 'basic'
        ET.SubElement(folder, 'minDiskFree', {'unit': '%'}).text = '1'
        versioning = ET.SubElement(folder, 'versioning')
        ET.SubElement(versioning, 'cleanupIntervalS').text = '3600'
        ET.SubElement(versioning, 'fsPath').text = ''
        ET.SubElement(versioning, 'fsType').text = 'basic'
        for tag, text in [
            ('copiers', '0'),
            ('pullerMaxPendingKiB', '0'),
            ('hashers', '0'),
            ('order', 'random'),
            ('ignoreDelete', 'false'),
            ('scanProgressIntervalS', '0'),
            ('pullerPauseS', '0'),
            ('maxConflicts', '10'),
            ('disableSparseFiles', 'false'),
            ('disableTempIndexes', 'false'),
            ('paused', 'false'),
            ('weakHashThresholdPct', '25'),
            ('markerName', '.stfolder'),
            ('copyOwnershipFromParent', 'false'),
            ('modTimeWindowS', '0'),
            ('maxConcurrentWrites', '2'),
            ('disableFsync', 'false'),
            ('blockPullOrder', 'standard'),
            ('copyRangeMethod', 'standard'),
            ('caseSensitiveFS', 'false'),
            ('junctionsAsDirs', 'false'),
        ]:
            ET.SubElement(folder, tag).text = text
    root.insert(0, folder)

folder.attrib.update({
    'id': folder_id,
    'label': folder_label,
    'path': shared_dir,
    'type': folder_type,
    'rescanIntervalS': folder.attrib.get('rescanIntervalS', '3600'),
    'fsWatcherEnabled': folder.attrib.get('fsWatcherEnabled', 'true'),
    'fsWatcherDelayS': folder.attrib.get('fsWatcherDelayS', '10'),
    'ignorePerms': folder.attrib.get('ignorePerms', 'false'),
    'autoNormalize': folder.attrib.get('autoNormalize', 'true'),
})

folder_device_ids = {d.attrib.get('id') for d in folder.findall('./device')}
for device_id in (local_id, remote_id):
    if device_id not in folder_device_ids:
        dev_el = ET.Element('device', {'id': device_id, 'introducedBy': ''})
        ET.SubElement(dev_el, 'encryptionPassword')
        fs = folder.find('./filesystemType')
        insert_at = list(folder).index(fs) + 1 if fs is not None else 0
        folder.insert(insert_at, dev_el)

backup_path = config_path + '.bak'
tree.write(backup_path, encoding='utf-8', xml_declaration=True)
tree.write(config_path, encoding='utf-8', xml_declaration=True)
print(backup_path)
PY
}

main() {
  parse_args "$@"
  [[ -f "$ST_HOME/config.xml" ]] || die "missing Syncthing config at $ST_HOME/config.xml; run setup_syncthing_node.sh first"
  mkdir -p "$SHARED_DIR"

  local backup_path
  backup_path="$(apply_pairing)"

  log "paired"
  log "  local_device_id=$(local_device_id)"
  log "  remote_device_id=$REMOTE_DEVICE_ID"
  log "  shared_dir=$SHARED_DIR"
  log "  folder_id=$FOLDER_ID"
  log "  backup=$backup_path"
  log "  restart Syncthing on this machine to apply the updated config"
}

main "$@"
