"""Bounded native accounting measurements; no file contents are collected."""

import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import time


_DU = "/usr/bin/du"
_OUTPUT_LIMIT = 64 * 1024


class MeasurementError(RuntimeError):
    """A complete, trustworthy measurement could not be obtained."""


def _checked_path(path: Path, directory: bool = False) -> tuple[Path, tuple[int, int]]:
    if ".." in path.parts:
        raise MeasurementError("cannot inspect a path containing parent traversal")
    path = path.absolute()
    try:
        for ancestor in (*reversed(path.parents), path):
            observed = ancestor.lstat()
            if stat.S_ISLNK(observed.st_mode):
                raise MeasurementError("symlink root or ancestor refused")
        if directory and not stat.S_ISDIR(observed.st_mode):
            raise MeasurementError("allocated measurement requires a directory")
        return path, (observed.st_dev, observed.st_ino)
    except OSError as error:
        raise MeasurementError("cannot inspect measurement path; missing or unreadable") from error


def free_bytes(path: Path) -> int:
    """Return the physical free-space signal for this exact local path."""
    path, identity = _checked_path(path)
    try:
        free = shutil.disk_usage(path).free
    except OSError as error:
        raise MeasurementError("cannot measure volume free space") from error
    if _checked_path(path)[1] != identity:
        raise MeasurementError("measurement path identity changed")
    return free


def _stop_scanner(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired as error:
        raise MeasurementError("native scanner did not stop after kill") from error


def _scan(path: Path, deadline: float) -> tuple[int, bytes, bytes]:
    """Drain bounded pipes without waiting behind an unbounded stderr stream."""
    try:
        process = subprocess.Popen(
            [_DU, "-skxP", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"}, start_new_session=True,
        )
    except OSError as error:
        raise MeasurementError("native du could not start") from error
    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    completed = False
    try:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MeasurementError("native measurement exceeded deadline")
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), 4096)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                output[key.data].extend(data)
                if sum(map(len, output.values())) > _OUTPUT_LIMIT:
                    raise MeasurementError("native measurement output exceeded limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MeasurementError("native measurement exceeded deadline")
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise MeasurementError("native measurement exceeded deadline") from error
        completed = True
        return code, bytes(output["stdout"]), bytes(output["stderr"])
    finally:
        selector.close()
        # Also stop descendants retaining pipes after the scanner leader exits.
        try:
            if not completed or process.poll() is None:
                _stop_scanner(process)
        finally:
            process.stdout.close()
            process.stderr.close()


def allocated(root: Path, timeout: float = 2.0) -> int:
    """Return native du accounting bytes, confined to the root filesystem.

    Sparse holes are excluded, hardlinks count once, and symlinks are not followed.
    APFS clone accounting is not exclusively owned physical storage. Timeouts,
    unreadable coverage, and root replacement fail closed rather than return zero.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 2:
        raise MeasurementError("timeout must be finite, positive, and at most two seconds")
    deadline = time.monotonic() + timeout
    root, identity = _checked_path(root, directory=True)
    for attempt in range(2):
        if time.monotonic() >= deadline:
            raise MeasurementError("native measurement exceeded deadline")
        code, output, errors = _scan(root, deadline)
        if _checked_path(root, directory=True)[1] != identity:
            raise MeasurementError("measurement root identity changed")
        if code == 0 and not errors:
            size, separator, measured_path = output.partition(b"\t")
            if separator and size.strip().isdigit() and measured_path == os.fsencode(root) + b"\n":
                return int(size) * 1024
            raise MeasurementError("native measurement returned an invalid root total")
        lines = errors.splitlines()
        transient = code != 0 and lines and all(b"No such file or directory" in line for line in lines)
        if not transient or attempt:
            raise MeasurementError("native measurement coverage incomplete")
    raise MeasurementError("native measurement coverage incomplete")
