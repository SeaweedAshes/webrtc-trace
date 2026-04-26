#!/usr/bin/env python3
"""Replay a time-indexed netem schedule on a given interface.

CSV format:
    at_ms,delay_ms,jitter_ms,loss_pct,rate_kbit,limit_pkts,note

Each row defines the full qdisc state that becomes active at `at_ms`
milliseconds after replay start. Empty `rate_kbit`/`limit_pkts` fields mean
"disabled / omitted". Delay, jitter, and loss default to zero.

Example:
    at_ms,delay_ms,jitter_ms,loss_pct,rate_kbit,limit_pkts,note
    0,0,0,0,,,baseline
    3000,140,0,0,,,freeze candidate pulse
    3400,0,0,0,,,baseline
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, type=Path, help="trace CSV path")
    parser.add_argument("--iface", required=True, help="target interface name")
    parser.add_argument(
        "--namespace",
        help="optional network namespace; if set, run tc under `ip netns exec`",
    )
    parser.add_argument(
        "--reset-at-end",
        action="store_true",
        help="restore zero-delay baseline after the last event",
    )
    parser.add_argument(
        "--sudo",
        action="store_true",
        help="prefix tc/ip commands with `sudo -n`",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print commands without executing them",
    )
    return parser.parse_args()


def read_trace(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"trace is empty: {path}")
    required = {
        "at_ms",
        "delay_ms",
        "jitter_ms",
        "loss_pct",
        "rate_kbit",
        "limit_pkts",
        "note",
    }
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"trace missing columns: {sorted(missing)}")
    parsed = sorted(rows, key=lambda row: int(row["at_ms"]))
    if int(parsed[0]["at_ms"]) != 0:
        raise ValueError("first trace row must start at at_ms=0")
    return parsed


def tc_prefix(namespace: str | None, use_sudo: bool) -> list[str]:
    prefix = ["sudo", "-n"] if use_sudo else []
    if namespace:
        return prefix + ["ip", "netns", "exec", namespace, "tc"]
    return prefix + ["tc"]


def build_tc_cmd(
    namespace: str | None,
    iface: str,
    *,
    use_sudo: bool,
    delay_ms: int,
    jitter_ms: int,
    loss_pct: float,
    rate_kbit: str,
    limit_pkts: str,
) -> list[str]:
    cmd = tc_prefix(namespace, use_sudo) + [
        "qdisc",
        "replace",
        "dev",
        iface,
        "root",
        "netem",
    ]
    if limit_pkts:
        cmd += ["limit", limit_pkts]
    if jitter_ms:
        if delay_ms <= 0:
            raise ValueError("jitter_ms requires delay_ms > 0 in this tool")
        cmd += ["delay", f"{delay_ms}ms", f"{jitter_ms}ms"]
    else:
        cmd += ["delay", f"{delay_ms}ms"]
    if loss_pct > 0:
        cmd += ["loss", f"{loss_pct}%"]
    if rate_kbit:
        cmd += ["rate", f"{rate_kbit}kbit"]
    return cmd


def run_cmd(cmd: list[str], dry_run: bool) -> None:
    printable = " ".join(cmd)
    print(printable, flush=True)
    if not dry_run:
        subprocess.run(cmd, check=True)


def apply_row(args: argparse.Namespace, row: dict[str, str]) -> None:
    delay_ms = int(row["delay_ms"] or 0)
    jitter_ms = int(row["jitter_ms"] or 0)
    loss_pct = float(row["loss_pct"] or 0)
    rate_kbit = row["rate_kbit"].strip()
    limit_pkts = row["limit_pkts"].strip()
    cmd = build_tc_cmd(
        args.namespace,
        args.iface,
        use_sudo=args.sudo,
        delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        loss_pct=loss_pct,
        rate_kbit=rate_kbit,
        limit_pkts=limit_pkts,
    )
    note = row["note"].strip()
    if note:
        print(f"# {row['at_ms']} ms: {note}", flush=True)
    run_cmd(cmd, args.dry_run)


def main() -> int:
    args = parse_args()
    rows = read_trace(args.trace)
    start = time.monotonic()

    for row in rows:
        target_s = int(row["at_ms"]) / 1000.0
        now_s = time.monotonic() - start
        sleep_s = target_s - now_s
        if sleep_s > 0:
            time.sleep(sleep_s)
        apply_row(args, row)

    if args.reset_at_end:
        reset_row = {
            "at_ms": "",
            "delay_ms": "0",
            "jitter_ms": "0",
            "loss_pct": "0",
            "rate_kbit": "",
            "limit_pkts": "",
            "note": "reset baseline",
        }
        apply_row(args, reset_row)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
