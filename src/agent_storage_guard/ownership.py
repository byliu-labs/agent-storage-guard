"""Filesystem ownership checks. Scratch contents are never an authority."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import tempfile
import uuid


def no_symlinks(path: Path) -> Path:
    if ".." in path.parts:
        raise RuntimeError("parent traversal in storage path refused")
    path = path.absolute()
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f"symlink path refused: {part}")
    return path


def private_directory(path: Path, create: bool = True) -> Path:
    path = no_symlinks(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"directory is not owned by this user: {path}")
    if info.st_mode & 0o077:
        raise RuntimeError(f"directory must be private (mode 0700): {path}")
    return path


def checked_file(path: Path, create: bool = False) -> int:
    no_symlinks(path)
    flags = os.O_RDWR if create else os.O_RDONLY
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise RuntimeError(f"state file must be private, regular and singly linked: {path}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_json(path: Path, missing: object = None, max_bytes: int = 1024 * 1024) -> object:
    try:
        fd = checked_file(path)
    except FileNotFoundError:
        return missing
    with os.fdopen(fd) as stream:
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise RuntimeError(f"state JSON exceeds size limit: {path}")
        try:
            # Bound the read too, in case another same-user process grows the file.
            payload = stream.read(max_bytes + 1)
            if len(payload.encode("utf-8")) > max_bytes:
                raise RuntimeError(f"state JSON exceeds size limit: {path}")
            return json.loads(payload)
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError(f"corrupt state JSON: {path}") from error


def write_json(path: Path, value: object) -> None:
    private_directory(path.parent, create=False)
    if path.exists() or path.is_symlink():
        os.close(checked_file(path))
    fd, name = tempfile.mkstemp(prefix=".asg-write-", dir=path.parent)
    # No permanent deletion: an interrupted atomic write stays as recoverable data.
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def uuid_token(token: object) -> str:
    if not isinstance(token, str):
        raise RuntimeError("invalid run UUID")
    try:
        parsed = uuid.UUID(token)
    except ValueError as error:
        raise RuntimeError("invalid run UUID") from error
    if str(parsed) != token or parsed.version != 4:
        raise RuntimeError("invalid canonical run UUID")
    return token


def validate_identity(identity: object) -> None:
    if not isinstance(identity, dict) or set(identity) != {"pid", "uid", "start"}:
        raise RuntimeError("corrupt process identity")
    if (type(identity["pid"]) is not int or not 0 < identity["pid"] <= 2147483647
            or type(identity["uid"]) is not int or identity["uid"] != os.getuid()
            or not isinstance(identity["start"], str) or not 0 < len(identity["start"]) <= 128):
        raise RuntimeError("invalid process identity")


def validate_record(record: object) -> dict:
    required = {"schema", "token", "state", "owner", "leader", "device", "inode", "uid",
                "estimate_bytes", "floor_bytes", "max_runs", "label", "started_at"}
    if not isinstance(record, dict) or required != record.keys():
        raise RuntimeError("corrupt run record")
    uuid_token(record["token"])
    validate_identity(record["owner"])
    if record["leader"] is not None:
        validate_identity(record["leader"])
    if record["schema"] != 1 or record["state"] not in {"admitted", "running", "preserved", "retired"}:
        raise RuntimeError("invalid run schema or state")
    for field in ["device", "inode", "uid", "estimate_bytes", "floor_bytes", "max_runs"]:
        if type(record[field]) is not int or record[field] < 0:
            raise RuntimeError(f"invalid {field} in run record")
    if (record["uid"] != os.getuid() or record["estimate_bytes"] == 0 or record["max_runs"] == 0
            or not isinstance(record["label"], str) or len(record["label"]) > 80
            or type(record["started_at"]) not in (float, int)
            or not math.isfinite(record["started_at"])):
        raise RuntimeError("invalid run owner, estimate, label or time")
    if record["state"] == "running" and record["leader"] is None:
        raise RuntimeError("running record has no controller identity")
    return record


def marker(record: dict) -> dict:
    return {key: record[key] for key in ["schema", "token", "device", "inode", "uid"]}


def verify_root(root: Path, record: dict) -> None:
    private_directory(root, create=False)
    info = root.lstat()
    expected = (record["device"], record["inode"], record["uid"])
    if (info.st_dev, info.st_ino, info.st_uid) != expected:
        raise RuntimeError("scratch inode or ownership changed; preserving it")
    if read_json(root / ".asg-owner.json", max_bytes=64 * 1024) != marker(record):
        raise RuntimeError("scratch ownership marker changed; preserving it")
