#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL=""
BRANCH="main"
WORKDIR="$HOME/webrtc-trace-lab-repo"
DEPOT_TOOLS_DIR="${DEPOT_TOOLS_DIR:-$HOME/depot_tools}"
BUILD_DIR="out/Trace"
TARGETS=("peerconnection_client" "peerconnection_server" "rtc_unittests")
SYNC_ARGS=("--nohooks" "--jobs" "1")
DEFAULT_GN_ARGS=$'rtc_build_examples = true\nrtc_include_tests = true\nis_debug = false\nuse_sysroot = false'
GN_ARGS_TEXT=""
SKIP_BUILD=0
FORCE_GCLIENT_SYNC=0
FORCE_GN_GEN=0

usage() {
  cat <<EOF
Usage: $0 --repo-url URL [options]

Required:
  --repo-url URL              Git repository containing the trace-lab checkout

Options:
  --branch NAME               Git branch to checkout/update (default: main)
  --workdir PATH              Local clone/update path (default: $WORKDIR)
  --depot-tools-dir PATH      depot_tools location (default: $DEPOT_TOOLS_DIR)
  --build-dir PATH            GN output dir under src/ (default: out/Trace)
  --target NAME               Additional/autorepeated build target
  --gn-args-file PATH         Use this file as args.gn template
  --gn-arg 'key = value'      Append one GN arg line
  --skip-build                Stop after clone/update + gclient sync
  --force-gclient-sync        Always run gclient sync even if src/ exists
  --force-gn-gen              Always rerun gn gen
  --sync-arg ARG              Extra arg forwarded to gclient sync

Repository layouts supported:
  1. repo root contains .gclient and src/
  2. repo root contains webrtc-trace-lab/.gclient and webrtc-trace-lab/src/
EOF
  exit 1
}

log() {
  printf '[bootstrap] %s\n' "$*"
}

die() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

append_unique_path() {
  case ":$PATH:" in
    *":$1:"*) ;;
    *) export PATH="$1:$PATH" ;;
  esac
}

append_path_tail() {
  case ":$PATH:" in
    *":$1:"*) ;;
    *) export PATH="$PATH:$1" ;;
  esac
}

find_non_depot_ninja() {
  local ninja_path
  while IFS= read -r ninja_path; do
    [[ -n "$ninja_path" ]] || continue
    case "$ninja_path" in
      "$DEPOT_TOOLS_DIR"/*) continue ;;
      *) printf '%s\n' "$ninja_path"; return 0 ;;
    esac
  done < <(type -a -p ninja 2>/dev/null || true)
  for ninja_path in /usr/bin/ninja /bin/ninja /usr/local/bin/ninja; do
    if [[ -x "$ninja_path" ]]; then
      printf '%s\n' "$ninja_path"
      return 0
    fi
  done
  return 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-url) REPO_URL="$2"; shift 2 ;;
      --branch) BRANCH="$2"; shift 2 ;;
      --workdir) WORKDIR="$2"; shift 2 ;;
      --depot-tools-dir) DEPOT_TOOLS_DIR="$2"; shift 2 ;;
      --build-dir) BUILD_DIR="$2"; shift 2 ;;
      --target) TARGETS+=("$2"); shift 2 ;;
      --gn-args-file) GN_ARGS_TEXT="$(cat "$2")"; shift 2 ;;
      --gn-arg)
        if [[ -n "$GN_ARGS_TEXT" ]]; then
          GN_ARGS_TEXT+=$'\n'
        fi
        GN_ARGS_TEXT+="$2"
        shift 2
        ;;
      --skip-build) SKIP_BUILD=1; shift ;;
      --force-gclient-sync) FORCE_GCLIENT_SYNC=1; shift ;;
      --force-gn-gen) FORCE_GN_GEN=1; shift ;;
      --sync-arg) SYNC_ARGS+=("$2"); shift 2 ;;
      *) usage ;;
    esac
  done

  [[ -n "$REPO_URL" ]] || usage
  [[ -n "$GN_ARGS_TEXT" ]] || GN_ARGS_TEXT="$DEFAULT_GN_ARGS"
}

ensure_depot_tools() {
  if command -v gclient >/dev/null 2>&1 && command -v gn >/dev/null 2>&1 && command -v autoninja >/dev/null 2>&1; then
    log "using existing depot_tools from PATH"
    return
  fi

  if [[ -x "$DEPOT_TOOLS_DIR/gclient" && -x "$DEPOT_TOOLS_DIR/gn" && -x "$DEPOT_TOOLS_DIR/autoninja" ]]; then
    append_unique_path "$DEPOT_TOOLS_DIR"
    log "using existing depot_tools at $DEPOT_TOOLS_DIR"
    return
  fi

  need_cmd git
  if [[ -d "$DEPOT_TOOLS_DIR/.git" ]]; then
    log "updating depot_tools in $DEPOT_TOOLS_DIR"
    git -C "$DEPOT_TOOLS_DIR" fetch --all --prune
    git -C "$DEPOT_TOOLS_DIR" pull --ff-only
  else
    log "cloning depot_tools into $DEPOT_TOOLS_DIR"
    git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git "$DEPOT_TOOLS_DIR"
  fi
  append_unique_path "$DEPOT_TOOLS_DIR"

  command -v gclient >/dev/null 2>&1 || die "gclient still missing after depot_tools setup"
  command -v gn >/dev/null 2>&1 || die "gn still missing after depot_tools setup"
  command -v autoninja >/dev/null 2>&1 || die "autoninja still missing after depot_tools setup"
}

ensure_ninja_available() {
  local ninja_path
  if ninja_path="$(find_non_depot_ninja)"; then
    append_path_tail "$(dirname "$ninja_path")"
    log "using ninja at $ninja_path"
    return
  fi

  if [[ -x "$WORKDIR/src/third_party/ninja/ninja" ]]; then
    append_path_tail "$WORKDIR/src/third_party/ninja"
    log "using project ninja at $WORKDIR/src/third_party/ninja/ninja"
    return
  fi

  if [[ -x "$WORKDIR/src/buildtools/linux64/ninja" ]]; then
    append_path_tail "$WORKDIR/src/buildtools/linux64"
    log "using project ninja at $WORKDIR/src/buildtools/linux64/ninja"
    return
  fi

  die "ninja not found outside depot_tools; install ninja-build or add a real ninja binary to PATH after depot_tools"
}

clone_or_update_repo() {
  need_cmd git
  if [[ -d "$WORKDIR/.git" ]]; then
    log "updating repo in $WORKDIR"
    git -C "$WORKDIR" fetch --all --prune
    if git -C "$WORKDIR" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
      git -C "$WORKDIR" checkout "$BRANCH"
      git -C "$WORKDIR" pull --ff-only
    else
      log "branch '$BRANCH' not found in origin; keeping current checkout"
    fi
    return
  fi

  mkdir -p "$(dirname "$WORKDIR")"
  if git ls-remote --exit-code --heads "$REPO_URL" "$BRANCH" >/dev/null 2>&1; then
    log "cloning repo branch '$BRANCH' into $WORKDIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$WORKDIR"
  else
    log "branch '$BRANCH' not found or repo is empty; cloning default repo state into $WORKDIR"
    git clone "$REPO_URL" "$WORKDIR"
  fi
}

discover_lab_root() {
  local candidate
  for candidate in "$WORKDIR" "$WORKDIR/webrtc-trace-lab"; do
    if [[ -f "$candidate/.gclient" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

read_pinned_revision() {
  local lab_root="$1"
  local revision_file="$lab_root/src_base_commit.txt"
  if [[ -f "$revision_file" ]]; then
    tr -d '[:space:]' < "$revision_file"
  fi
}

cleanup_failed_sync_artifacts() {
  local lab_root="$1"
  if [[ -d "$lab_root/_bad_scm" ]]; then
    log "removing failed checkout artifacts under $lab_root/_bad_scm"
    rm -rf "$lab_root/_bad_scm"
  fi
  if [[ -d "$lab_root/src" ]]; then
    find "$lab_root/src" -type d -name '_gclient_src_*' -prune -exec rm -rf {} + 2>/dev/null || true
  fi
}

gclient_sync_once() {
  local lab_root="$1"
  local pinned_revision="$2"
  (
    cd "$lab_root"
    if [[ -n "$pinned_revision" ]]; then
      gclient sync "${SYNC_ARGS[@]}" --revision "src@$pinned_revision"
    else
      gclient sync "${SYNC_ARGS[@]}"
    fi
  )
}

run_gclient_sync() {
  local lab_root="$1"
  local pinned_revision="$2"
  local attempt
  local max_attempts=4

  cleanup_failed_sync_artifacts "$lab_root"
  for attempt in $(seq 1 "$max_attempts"); do
    log "gclient sync attempt $attempt/$max_attempts"
    if gclient_sync_once "$lab_root" "$pinned_revision"; then
      return 0
    fi
    cleanup_failed_sync_artifacts "$lab_root"
    if [[ -d "$lab_root/src/third_party/.git" ]]; then
      (
        cd "$lab_root/src/third_party"
        git reset --hard HEAD >/dev/null 2>&1 || true
        git clean -ffd >/dev/null 2>&1 || true
      )
    fi
    if (( attempt < max_attempts )); then
      local sleep_sec=$((attempt * 20))
      log "gclient sync failed; sleeping ${sleep_sec}s before retry"
      sleep "$sleep_sec"
    fi
  done
  die "gclient sync failed after $max_attempts attempts; likely upstream rate limiting (HTTP 429)"
}

ensure_checkout() {
  local lab_root="$1"
  local pinned_revision
  pinned_revision="$(read_pinned_revision "$lab_root")"
  if [[ ! -d "$lab_root/src" || "$FORCE_GCLIENT_SYNC" -eq 1 ]]; then
    log "running gclient sync in $lab_root"
    run_gclient_sync "$lab_root" "$pinned_revision"
    return
  fi

  if [[ ! -f "$lab_root/src/.gn" || ! -d "$lab_root/src/build" ]]; then
    log "src checkout looks incomplete; running gclient sync"
    run_gclient_sync "$lab_root" "$pinned_revision"
  elif [[ -n "$pinned_revision" ]]; then
    local current_head
    current_head="$(git -C "$lab_root/src" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$current_head" != "$pinned_revision" ]]; then
      log "existing src checkout at $current_head; syncing pinned revision $pinned_revision"
      run_gclient_sync "$lab_root" "$pinned_revision"
    else
      log "existing src checkout already matches pinned revision $pinned_revision"
    fi
  else
    log "existing src checkout found; skipping gclient sync"
  fi
}

apply_overlay() {
  local lab_root="$1"
  local src_root="$2"
  local overlay_root="$lab_root/overlay/src"
  if [[ ! -d "$overlay_root" ]]; then
    log "no overlay directory found; skipping local source overlay"
    return
  fi

  log "applying overlay files from $overlay_root"
  while IFS= read -r -d '' file; do
    local rel="${file#$overlay_root/}"
    mkdir -p "$src_root/$(dirname "$rel")"
    cp "$file" "$src_root/$rel"
  done < <(find "$overlay_root" -type f -print0)
}

ensure_gn_gen() {
  local src_root="$1"
  local out_dir="$src_root/$BUILD_DIR"
  local args_file="$out_dir/args.gn"
  local build_ninja="$out_dir/build.ninja"
  local gn_args_tmp

  mkdir -p "$out_dir"
  gn_args_tmp="$(mktemp)"
  printf '%s\n' "$GN_ARGS_TEXT" > "$gn_args_tmp"

  if [[ "$FORCE_GN_GEN" -eq 1 || ! -f "$args_file" || ! -f "$build_ninja" ]] || ! cmp -s "$gn_args_tmp" "$args_file"; then
    cp "$gn_args_tmp" "$args_file"
    log "running gn gen $BUILD_DIR"
    (
      cd "$src_root"
      gn gen "$BUILD_DIR"
    )
  else
    log "existing gn args match; skipping gn gen"
  fi

  rm -f "$gn_args_tmp"
}

run_build() {
  local src_root="$1"
  log "building targets: ${TARGETS[*]}"
  (
    cd "$src_root"
    autoninja -C "$BUILD_DIR" "${TARGETS[@]}"
  )
}

write_bootstrap_report() {
  local lab_root="$1"
  local src_root="$lab_root/src"
  local report="$lab_root/BOOTSTRAP_REPORT.txt"
  local head

  head="$(git -C "$src_root" rev-parse HEAD 2>/dev/null || echo 'unknown')"
  cat > "$report" <<EOF
generated_at=$(date -Iseconds)
repo_url=$REPO_URL
branch=$BRANCH
workdir=$WORKDIR
lab_root=$lab_root
src_root=$src_root
git_head=$head
build_dir=$BUILD_DIR
targets=${TARGETS[*]}
EOF
  log "wrote bootstrap report to $report"
}

main() {
  parse_args "$@"
  ensure_depot_tools
  clone_or_update_repo

  local lab_root
  lab_root="$(discover_lab_root)" || die "could not find .gclient in $WORKDIR or $WORKDIR/webrtc-trace-lab; push the WebRTC trace-lab checkout structure first"
  local src_root="$lab_root/src"

  ensure_checkout "$lab_root"
  [[ -d "$src_root" ]] || die "src checkout missing after gclient sync: $src_root"
  apply_overlay "$lab_root" "$src_root"

  if [[ "$SKIP_BUILD" -eq 0 ]]; then
    ensure_ninja_available
    ensure_gn_gen "$src_root"
    run_build "$src_root"
  else
    log "skip-build enabled; stopping after checkout sync"
  fi

  write_bootstrap_report "$lab_root"
  log "ready:"
  log "  lab_root=$lab_root"
  log "  binaries=$src_root/$BUILD_DIR"
}

main "$@"
