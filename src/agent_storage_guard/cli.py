"""Command interface; runtime state contains no command arguments or environment."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
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
    result = argparse.ArgumentParser(description="Budget and own scratch for managed commands; retirement preserves bytes.")
    result.add_argument("--state", type=Path, default=default_state())
    result.add_argument("--retirement", type=Path, help="existing same-volume private directory; defaults to macOS Trash or state/retired")
    commands = result.add_subparsers(dest="action", required=True, metavar="{run,status,reconcile}")
    run = commands.add_parser("run", help="admit and supervise a command with owned temporary storage")
    run.add_argument("--estimate-gib", type=finite_positive, required=True)
    run.add_argument("--floor-gib", type=finite_nonnegative, default=20)
    run.add_argument("--max-runs", type=positive_integer, default=2)
    run.add_argument("--poll-seconds", type=finite_positive, default=1)
    run.add_argument("--label", default="managed-command")
    run.add_argument("command", nargs=argparse.REMAINDER)
    commands.add_parser("status", help="read current registry without changing it")
    commands.add_parser("reconcile", help="recover only provably owned inactive scratch")
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
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"storage guard refused: {error}", file=sys.stderr)
        return 75
