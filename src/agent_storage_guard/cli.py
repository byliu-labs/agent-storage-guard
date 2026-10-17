"""Command interface; runtime state contains no command arguments or environment."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

GIB = 1024 ** 3


def finite_positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def finite_nonnegative(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return number


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def default_state() -> Path:
    if os.environ.get("ASG_STATE_DIR"):
        return Path(os.environ["ASG_STATE_DIR"])
    # Keep TMPDIR short enough for Unix sockets used by common test runners.
    return Path.home() / ".asg"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Monitor workstation storage and memory; budget scratch for managed commands.")
    result.add_argument("--state", type=Path, default=default_state())
    result.add_argument("--retirement", type=Path, help="existing same-volume private directory; defaults to macOS Trash or state/retired")
    commands = result.add_subparsers(dest="action", required=True, metavar="{run,status,reconcile,monitor}")
    run = commands.add_parser("run", help="admit and supervise a command with owned temporary storage")
    run.add_argument("--estimate-gib", type=finite_positive, required=True)
    run.add_argument("--floor-gib", type=finite_nonnegative, default=20)
    run.add_argument("--max-runs", type=positive_integer, default=2)
    run.add_argument("--poll-seconds", type=finite_positive, default=1)
    run.add_argument("--label", default="managed-command")
    run.add_argument("command", nargs=argparse.REMAINDER)
    commands.add_parser("status", help="read current registry without changing it")
    commands.add_parser("reconcile", help="recover only provably owned inactive scratch")
    monitor = commands.add_parser("monitor", help="local history, actionable alerts and workstation reports")
    monitoring = monitor.add_subparsers(dest="monitor_action", required=True)
    init = monitoring.add_parser("init", help="create a private monitor with portable defaults")
    init.add_argument("--root", type=Path, action="append", help="measure this root instead of default locations; repeatable")
    setup = monitoring.add_parser("setup", help="privately save a destination in macOS Keychain and local config")
    setup.add_argument("--provider", choices=("feishu", "imessage"), required=True)
    setup.add_argument("--destination", help="new destination id; defaults to provider name")
    setup.add_argument("--root", type=Path, action="append", help="custom root for a new monitor; repeatable")
    configure = monitoring.add_parser("configure", help="set a non-identifying alert source label without sending")
    configure.add_argument("--label", required=True, help="short source label, for example Laptop or Build Mac")
    migration = monitoring.add_parser("migrate", help="import legacy pc-backup JSON history without changing its source")
    migration.add_argument("--from-state", type=Path, required=True)
    for name in ("tick", "scan", "status"):
        probe = monitoring.add_parser(name)
        probe.add_argument("--json", action="store_true")
        if name != "status":
            probe.add_argument("--notify", action="store_true")
    report = monitoring.add_parser("report", help="refresh current status and generate the local HTML overview")
    report.add_argument("--open", action="store_true", help="open the overview in your default browser")
    install = monitoring.add_parser("install", help="prepare macOS launchd jobs and notification identity")
    install.add_argument("--activate", action="store_true", help="activate jobs and replace legacy monitor jobs with rollback")
    rollback = monitoring.add_parser("rollback", help="restore jobs from an installation manifest")
    rollback.add_argument("manifest", type=Path)
    monitoring.add_parser("delivery-check", help="validate destination settings offline; never send")
    test_send = monitoring.add_parser("test-send", help="deliberately send synthetic measurements to one destination")
    test_send.add_argument("--destination", required=True, help="configured destination id")
    test_send.add_argument("--installed", action="store_true", help="send through the prepared app, using the scheduled identity")
    child = commands.add_parser("_child")
    child.add_argument("--root", type=Path, required=True)
    child.add_argument("--parent-fd", type=int, required=True)
    child.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main() -> int:
    args = parser().parse_args()
    os.umask(0o077)
    from . import guard
    retirement = args.retirement or (Path.home() / ".Trash" if sys.platform == "darwin" else args.state / "retired")
    try:
        if args.action == "monitor":
            from .monitor import dispatch
            return dispatch(args)
        if args.action == "status":
            print(json.dumps(guard.status(args.state), indent=2))
            return 0
        if args.action == "reconcile":
            outcomes = guard.reconcile(args.state, retirement)
            print(json.dumps(outcomes, indent=2))
            return 74 if any(item.get("result") == "preserved" for item in outcomes) else 0
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser().error("a child command is required after --")
        if args.action == "_child":
            return guard.child(args.root, args.parent_fd, command)
        return guard.run(args.state, retirement, int(args.estimate_gib * GIB),
                         int(args.floor_gib * GIB), args.max_runs,
                         args.poll_seconds, args.label, command)
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"storage guard refused: {error}", file=sys.stderr)
        return 75
