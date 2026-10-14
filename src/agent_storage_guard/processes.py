"""Process identity, group stopping and conservative open-file coverage."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class _BSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in [
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid",
        "svuid", "svgid", "rfu"]] + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in ["nfiles", "pgid", "pjobc", "e_tdev", "e_tpgid", "nice"]] + [
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


def identity(pid: int) -> dict | None:
    if sys.platform == "darwin":
        info = _BSDInfo()
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        ctypes.set_errno(0)
        count = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if count == 0:
            if ctypes.get_errno() not in {0, errno.ESRCH}:
                raise RuntimeError("process identity coverage denied")
            return None
        if count != ctypes.sizeof(info):
            raise RuntimeError("cannot obtain complete process start identity")
        if info.status == 5:  # SZOMB is not an active writer.
            return None
        return {"pid": pid, "uid": int(info.uid), "start": f"{info.start_sec}.{info.start_usec:06d}"}
    if sys.platform.startswith("linux"):
        try:
            path = Path(f"/proc/{pid}/stat")
            fields = path.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            return {"pid": pid, "uid": path.stat().st_uid, "start": fields[19]}
        except FileNotFoundError:
            return None
    raise RuntimeError("process identity supports macOS and Linux only")


def alive(expected: dict | None) -> bool:
    return expected is not None and identity(expected["pid"]) == expected


def group_members(pgid: int) -> list[int]:
    result = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,stat="],
                            capture_output=True, text=True, timeout=2, start_new_session=True)
    if result.returncode or result.stderr:
        raise RuntimeError("cannot verify process-group coverage")
    members = []
    for line in result.stdout.splitlines():
        pid, group, status = line.split()
        if int(group) == pgid and not status.startswith("Z"):
            members.append(int(pid))
    return members


def stop(process: subprocess.Popen, expected: dict) -> None:
    if not alive(expected):
        if group_members(expected["pid"]):
            raise RuntimeError("group leader identity lost; cannot safely signal remaining group")
        process.wait(timeout=2)
        return
    for sig, duration in [(signal.SIGTERM, 2), (signal.SIGKILL, 2)]:
        if not alive(expected):
            break
        os.killpg(expected["pid"], sig)
        deadline = time.monotonic() + duration
        while alive(expected) and time.monotonic() < deadline:
            time.sleep(.02)
    process.wait(timeout=2)
    if group_members(expected["pid"]):
        raise RuntimeError("managed group still has live processes; scratch preserved")


def open_paths(root: Path) -> bool:
    lsof = "/usr/sbin/lsof" if sys.platform == "darwin" else "/usr/bin/lsof"
    result = subprocess.run([lsof, "-nP", "-F", "n"], capture_output=True,
                            text=True, timeout=3)
    if result.returncode not in (0, 1) or result.stderr:
        raise RuntimeError("open-file coverage incomplete; scratch preserved")
    prefix = "n" + str(root) + "/"
    return any(line == "n" + str(root) or line.startswith(prefix)
               for line in result.stdout.splitlines())


def child(root: Path, parent_fd: int, command: list[str]) -> int:
    # EOF is a kernel-backed owner-death signal, independent of mutable scratch.
    import select

    ready, _, _ = select.select([parent_fd], [], [], 10)
    if not ready or os.read(parent_fd, 1) != b"G":
        return 75
    if select.select([parent_fd], [], [], 0)[0]:
        return 75  # Owner closed its end before the launch handshake was consumed.
    signal.signal(signal.SIGTERM, lambda *_: None)
    signal.signal(signal.SIGINT, lambda *_: None)
    process = subprocess.Popen(command)
    try:
        while process.poll() is None:
            ready, _, _ = select.select([parent_fd], [], [], .05)
            if ready and not os.read(parent_fd, 1):
                return 75
        return process.returncode if process.returncode >= 0 else 128 - process.returncode
    finally:
        # Keep the group leader alive until ordinary descendants have received TERM.
        os.close(parent_fd)
        os.killpg(os.getpgrp(), signal.SIGTERM)
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline and any(pid != os.getpid() for pid in group_members(os.getpgrp())):
            time.sleep(.02)
        if any(pid != os.getpid() for pid in group_members(os.getpgrp())):
            os.killpg(os.getpgrp(), signal.SIGKILL)
        process.wait(timeout=2)
