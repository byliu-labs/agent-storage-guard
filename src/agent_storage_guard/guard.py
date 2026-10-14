"""Coordinate cooperative commands; polling is a watchdog, not a hard quota."""
from __future__ import annotations

import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time

from . import measurement, ownership, processes, run_registry


def status(state: Path) -> dict:
    return run_registry.read_status(state)


def reconcile(state: Path, retirement: Path) -> list[dict]:
    with run_registry.locked(state, retirement) as registry:
        return registry.reconcile()


def child(root: Path, parent_fd: int, command: list[str]) -> int:
    return processes.child(root, parent_fd, command)


def _nested(state: Path, estimate: int, floor: int, max_runs: int, command: list[str]) -> int | None:
    inherited = os.environ.get("ASG_SCRATCH_ROOT")
    if not inherited:
        return None
    root = Path(inherited).absolute()
    token = ownership.uuid_token(root.name)
    snapshot = run_registry.read_status(state)
    records = [record for record in snapshot["active_runs"] if record["token"] == token]
    if len(records) != 1:
        raise RuntimeError("inherited scratch has no trusted run registry entry")
    record = records[0]
    if root != state.absolute() / "scratch" / token:
        raise RuntimeError("inherited scratch does not belong to this state directory")
    ownership.verify_root(root, record)
    leader = record["leader"]
    if (record["state"] != "running" or not processes.alive(record["owner"])
            or not processes.alive(leader) or os.getpgrp() != leader["pid"]):
        raise RuntimeError("inherited scratch is not a live managed process group")
    if estimate > record["estimate_bytes"]:
        raise RuntimeError("nested estimate exceeds outer scratch budget")
    if floor > record["floor_bytes"]:
        raise RuntimeError("nested free-space reserve exceeds the outer controller's reserve")
    if max_runs < record["max_runs"]:
        raise RuntimeError("nested concurrency limit is stricter than the outer controller's limit")
    result = subprocess.run(command)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def _measure(root: Path, output: queue.Queue) -> None:
    try:
        output.put(measurement.allocated(root, timeout=2.0))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        output.put(error)


def _watch(process: subprocess.Popen, root: Path, estimate: int, floor: int,
           poll_seconds: float, interrupted: list[int]) -> tuple[int, str, threading.Thread | None]:
    measurements: queue.Queue = queue.Queue()
    scanner = None
    next_scan = 0.0
    while process.poll() is None:
        if interrupted:
            return 128 + interrupted[0], "interrupted", scanner
        try:
            free = measurement.free_bytes(root)
        except (RuntimeError, OSError) as error:
            return 74, "free-space measurement failed", scanner
        if free < floor:
            return 75, "free-space reserve crossed", scanner
        try:
            size = measurements.get_nowait()
        except queue.Empty:
            size = None
        if isinstance(size, BaseException):
            return 74, "scratch measurement failed", scanner
        if size is not None and size > estimate:
            return 75, "scratch budget exceeded", scanner
        if time.monotonic() >= next_scan and (scanner is None or not scanner.is_alive()):
            scanner = threading.Thread(target=_measure, args=(root, measurements), daemon=True)
            scanner.start()
            next_scan = time.monotonic() + poll_seconds
        time.sleep(min(poll_seconds, .1))
    code = process.returncode if process.returncode >= 0 else 128 - process.returncode
    if scanner is not None:
        scanner.join(timeout=2.5)
    # A command can finish between polls; its retained bytes still count.
    try:
        if measurement.free_bytes(root) < floor:
            return 75, "free-space reserve crossed", scanner
        if measurement.allocated(root, timeout=2.0) > estimate:
            return 75, "scratch budget exceeded", scanner
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return 74, "scratch measurement failed", scanner
    return code, "success" if code == 0 else "child failed", scanner


def run(state: Path, retirement: Path, estimate_bytes: int, floor_bytes: int,
        max_runs: int, poll_seconds: float, label: str, command: list[str]) -> int:
    if (type(estimate_bytes) is not int or estimate_bytes <= 0 or type(floor_bytes) is not int
            or floor_bytes < 0 or type(max_runs) is not int or max_runs < 1
            or type(poll_seconds) not in (float, int) or not math.isfinite(poll_seconds)
            or poll_seconds <= 0 or not command):
        raise RuntimeError("positive estimate, concurrency and poll interval, nonnegative reserve and command required")
    if not isinstance(label, str):
        raise RuntimeError("label must be text")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("guard run must own signal handling on the main thread")
    nested = _nested(state, estimate_bytes, floor_bytes, max_runs, command)
    if nested is not None:
        return nested
    with run_registry.locked(state, retirement) as registry:
        registry.reconcile()
        record = registry.admit(estimate_bytes, floor_bytes, max_runs, label)
        root = registry.scratch / record["token"]
    env = dict(os.environ, TMPDIR=str(root), TMP=str(root), TEMP=str(root),
               ASG_SCRATCH_ROOT=str(root), ASG_STATE_DIR=str(state.absolute()))
    interrupted: list[int] = []
    handlers = {sig: signal.signal(sig, lambda number, _: interrupted.append(number))
                for sig in (signal.SIGINT, signal.SIGTERM)}
    process, scanner, pipe_read, pipe_write = None, None, None, None
    code, reason, failure = 74, "spawn failure", None
    try:
        pipe_read, pipe_write = os.pipe()
        launcher = [sys.executable, "-m", "agent_storage_guard", "_child", "--root", str(root),
                    "--parent-fd", str(pipe_read), "--", *command]
        process = subprocess.Popen(launcher, env=env, start_new_session=True, pass_fds=(pipe_read,))
        os.close(pipe_read)
        pipe_read = None
        leader = processes.identity(process.pid)
        if leader is None:
            raise RuntimeError("controller exited before ownership handshake")
        with run_registry.locked(state, retirement) as registry:
            record = registry.running(record["token"], leader)
        os.write(pipe_write, b"G")
        code, reason, scanner = _watch(process, root, estimate_bytes, floor_bytes, poll_seconds, interrupted)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        # OS spawn errors may include command argv[0]; metadata must not copy it.
        failure = f"{type(error).__name__}: controller operation failed"
        reason = "controller failure"
    finally:
        for fd in [pipe_read, pipe_write]:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    failure = "controller pipe close failed; scratch preserved"
        if process is not None and record["leader"] is not None:
            try:
                processes.stop(process, record["leader"])
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                failure = f"{type(error).__name__}: process-group stop could not be verified"
        elif process is not None:
            try:
                process.wait(timeout=12)  # Pipe EOF denies launch before registry update.
            except (OSError, subprocess.TimeoutExpired):
                failure = "controller stop could not be verified; scratch preserved"
        if scanner is not None:
            scanner.join(timeout=2.5)
            if scanner.is_alive():
                failure = "allocation scanner still active; preserving scratch"
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        with run_registry.locked(state, retirement) as registry:
            current = registry.records[record["token"]]
            try:
                if failure:
                    raise RuntimeError(failure)
                registry.retire(current, reason, code)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                if code == 0:
                    code = 74
                reason = "cleanup incomplete"
                registry.preserve(current, str(error), code)
                print(f"cleanup incomplete: {error}; {root}", file=sys.stderr)
    if code in {74, 75}:
        print(reason, file=sys.stderr)
    return code
