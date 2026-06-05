#!/usr/bin/env python3
"""Replay separate netem schedules for RTP audio/video SSRCs.

This tool installs a classful qdisc on the sender egress interface and routes
RTP packets by SSRC:

  audio SSRC(s) -> audio netem child qdisc
  video SSRC(s) -> video netem child qdisc
  everything else -> default no-delay child qdisc

The SSRC match assumes IPv4/UDP/RTP with no IP options. This is valid for the
lab's direct WebRTC replay path, but it is intentionally not a generic SRTP
parser.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path


TRACE_COLUMNS = {
    "at_ms",
    "delay_ms",
    "jitter_ms",
    "loss_pct",
    "rate_kbit",
    "limit_pkts",
    "note",
}


def parse_ssrc_list(value: str) -> list[int]:
    ssrcs: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        ssrc = int(item, 0)
        if ssrc < 0 or ssrc > 0xFFFFFFFF:
            raise argparse.ArgumentTypeError(f"invalid SSRC: {item}")
        ssrcs.append(ssrc)
    if not ssrcs:
        raise argparse.ArgumentTypeError("empty SSRC list")
    return ssrcs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iface", required=True, help="target interface name")
    parser.add_argument("--audio-ssrc", required=True, type=parse_ssrc_list)
    parser.add_argument("--video-ssrc", required=True, type=parse_ssrc_list)
    parser.add_argument("--audio-trace", type=Path, help="audio netem trace CSV")
    parser.add_argument("--video-trace", type=Path, help="video netem trace CSV")
    parser.add_argument("--audio-delay-ms", type=int, default=0)
    parser.add_argument("--video-delay-ms", type=int, default=0)
    parser.add_argument("--namespace", help="optional network namespace")
    parser.add_argument("--sudo", action="store_true", help="prefix tc/ip commands with sudo -n")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset-at-end", action="store_true")
    parser.add_argument(
        "--update-mode",
        choices=("change", "replace"),
        default="change",
        help="child netem qdisc update mode after initial install",
    )
    args = parser.parse_args()
    if args.audio_delay_ms < 0 or args.video_delay_ms < 0:
        parser.error("fixed delay must be >= 0")
    return args


def read_trace(path: Path | None, *, fixed_delay_ms: int) -> list[dict[str, str]]:
    if path is None:
        return [
            {
                "at_ms": "0",
                "delay_ms": str(fixed_delay_ms),
                "jitter_ms": "0",
                "loss_pct": "0",
                "rate_kbit": "",
                "limit_pkts": "",
                "note": f"fixed {fixed_delay_ms}ms delay",
            }
        ]
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"trace is empty: {path}")
    missing = TRACE_COLUMNS - set(rows[0].keys())
    if missing:
        raise ValueError(f"trace missing columns: {sorted(missing)}")
    parsed = sorted(rows, key=lambda row: int(row["at_ms"]))
    if int(parsed[0]["at_ms"]) != 0:
        raise ValueError(f"first trace row must start at at_ms=0: {path}")
    return parsed


def tc_prefix(namespace: str | None, use_sudo: bool) -> list[str]:
    prefix = ["sudo", "-n"] if use_sudo else []
    if namespace:
        return prefix + ["ip", "netns", "exec", namespace, "tc"]
    return prefix + ["tc"]


def run_cmd(cmd: list[str], dry_run: bool, *, check: bool = True) -> subprocess.CompletedProcess[str] | None:
    print(" ".join(cmd), flush=True)
    if dry_run:
        return None
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def build_netem_cmd(
    args: argparse.Namespace,
    *,
    parent: str,
    handle: str,
    operation: str,
    row: dict[str, str],
) -> list[str]:
    delay_ms = int(row["delay_ms"] or 0)
    jitter_ms = int(row["jitter_ms"] or 0)
    loss_pct = float(row["loss_pct"] or 0)
    rate_kbit = row["rate_kbit"].strip()
    limit_pkts = row["limit_pkts"].strip()

    cmd = tc_prefix(args.namespace, args.sudo) + [
        "qdisc",
        operation,
        "dev",
        args.iface,
        "parent",
        parent,
        "handle",
        handle,
        "netem",
    ]
    if limit_pkts:
        cmd += ["limit", limit_pkts]
    if jitter_ms:
        if delay_ms <= 0:
            raise ValueError("jitter_ms requires delay_ms > 0")
        cmd += ["delay", f"{delay_ms}ms", f"{jitter_ms}ms"]
    else:
        cmd += ["delay", f"{delay_ms}ms"]
    if loss_pct > 0:
        cmd += ["loss", f"{loss_pct}%"]
    if rate_kbit:
        cmd += ["rate", f"{rate_kbit}kbit"]
    return cmd


def apply_netem_row(
    args: argparse.Namespace,
    *,
    media: str,
    parent: str,
    handle: str,
    row: dict[str, str],
    operation: str,
) -> None:
    note = row["note"].strip()
    if note:
        print(f"# {media} {row['at_ms']} ms: {note}", flush=True)
    cmd = build_netem_cmd(args, parent=parent, handle=handle, operation=operation, row=row)
    result = run_cmd(cmd, args.dry_run, check=False)
    if result is not None and result.returncode != 0:
        if operation == "change":
            fallback = build_netem_cmd(
                args, parent=parent, handle=handle, operation="replace", row=row
            )
            print("# change failed; falling back to replace", flush=True)
            run_cmd(fallback, args.dry_run)
        else:
            sys.stderr.write(result.stderr)
            result.check_returncode()


def install_root(args: argparse.Namespace, audio_row: dict[str, str], video_row: dict[str, str]) -> None:
    prefix = tc_prefix(args.namespace, args.sudo)
    # HTB is used only for deterministic classification. The rate is set high
    # enough not to be the replay bottleneck.
    run_cmd(
        prefix + ["qdisc", "replace", "dev", args.iface, "root", "handle", "1:", "htb", "default", "30"],
        args.dry_run,
    )
    for classid in ("1:10", "1:20", "1:30"):
        run_cmd(
            prefix
            + [
                "class",
                "replace",
                "dev",
                args.iface,
                "parent",
                "1:",
                "classid",
                classid,
                "htb",
                "rate",
                "10000mbit",
                "ceil",
                "10000mbit",
            ],
            args.dry_run,
        )

    apply_netem_row(
        args, media="audio", parent="1:10", handle="10:", row=audio_row, operation="replace"
    )
    apply_netem_row(
        args, media="video", parent="1:20", handle="20:", row=video_row, operation="replace"
    )
    apply_netem_row(
        args,
        media="default",
        parent="1:30",
        handle="30:",
        row={
            "at_ms": "0",
            "delay_ms": "0",
            "jitter_ms": "0",
            "loss_pct": "0",
            "rate_kbit": "",
            "limit_pkts": "",
            "note": "default passthrough",
        },
        operation="replace",
    )

    add_ssrc_filters(args, args.audio_ssrc, flowid="1:10", priority="1")
    add_ssrc_filters(args, args.video_ssrc, flowid="1:20", priority="2")


def add_ssrc_filters(
    args: argparse.Namespace, ssrcs: list[int], *, flowid: str, priority: str
) -> None:
    prefix = tc_prefix(args.namespace, args.sudo)
    for ssrc in ssrcs:
        run_cmd(
            prefix
            + [
                "filter",
                "add",
                "dev",
                args.iface,
                "protocol",
                "ip",
                "parent",
                "1:",
                "prio",
                priority,
                "u32",
                "match",
                "ip",
                "protocol",
                "17",
                "0xff",
                "match",
                "u32",
                f"0x{ssrc:08x}",
                "0xffffffff",
                "at",
                "36",
                "flowid",
                flowid,
            ],
            args.dry_run,
        )


def replay(args: argparse.Namespace, audio_rows: list[dict[str, str]], video_rows: list[dict[str, str]]) -> None:
    install_root(args, audio_rows[0], video_rows[0])
    events: list[tuple[int, str, int, dict[str, str]]] = []
    events.extend((int(row["at_ms"]), "audio", idx, row) for idx, row in enumerate(audio_rows[1:], 1))
    events.extend((int(row["at_ms"]), "video", idx, row) for idx, row in enumerate(video_rows[1:], 1))
    events.sort(key=lambda event: (event[0], 0 if event[1] == "audio" else 1, event[2]))

    start = time.monotonic()
    for at_ms, media, _idx, row in events:
        target_s = at_ms / 1000.0
        now_s = time.monotonic() - start
        sleep_s = target_s - now_s
        if sleep_s > 0:
            time.sleep(sleep_s)
        if media == "audio":
            apply_netem_row(
                args,
                media="audio",
                parent="1:10",
                handle="10:",
                row=row,
                operation=args.update_mode,
            )
        else:
            apply_netem_row(
                args,
                media="video",
                parent="1:20",
                handle="20:",
                row=row,
                operation=args.update_mode,
            )


def reset_root(args: argparse.Namespace) -> None:
    row = {
        "at_ms": "0",
        "delay_ms": "0",
        "jitter_ms": "0",
        "loss_pct": "0",
        "rate_kbit": "",
        "limit_pkts": "",
        "note": "reset baseline",
    }
    cmd = tc_prefix(args.namespace, args.sudo) + [
        "qdisc",
        "replace",
        "dev",
        args.iface,
        "root",
        "netem",
        "delay",
        "0ms",
    ]
    print(f"# {row['note']}", flush=True)
    run_cmd(cmd, args.dry_run)


def main() -> int:
    args = parse_args()
    audio_rows = read_trace(args.audio_trace, fixed_delay_ms=args.audio_delay_ms)
    video_rows = read_trace(args.video_trace, fixed_delay_ms=args.video_delay_ms)
    try:
        replay(args, audio_rows, video_rows)
    finally:
        if args.reset_at_end:
            reset_root(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
