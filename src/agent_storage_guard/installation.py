"""Reversible user launchd installation, preserving prior plists and loaded state."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import uuid

from .ownership import checked_file, no_symlinks, private_directory, read_json, write_json

LABELS = ("local.agent-storage-guard.tick", "local.agent-storage-guard.scan")
LEGACY_LABELS = ("local.pc-backup.disk-tick", "local.pc-backup.disk-scan")


def _run(controller: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([str(controller), *arguments], capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise RuntimeError(f"launchctl {arguments[0]} failed ({result.returncode}): {result.stderr.strip()[:300]}")
    return result


def _safe_directory(path: Path) -> Path:
    no_symlinks(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError(f"installation directory must be owned and not writable by others: {path}")
    return path


def _plist_source(path: Path) -> bool:
    no_symlinks(path)
    if not path.exists():
        return False
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise RuntimeError(f"installation plist must be an owned regular file: {path}")
    return True


def _copy(source: Path, destination: Path) -> None:
    no_symlinks(destination)
    temporary = destination.with_name(destination.name + ".installing-" + uuid.uuid4().hex)
    with temporary.open("xb") as output, source.open("rb") as content:
        os.chmod(temporary, 0o600)
        shutil.copyfileobj(content, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)


def service_plist(executable: Path, state: Path, command: str, interval: int | None = None) -> dict:
    if command not in {"tick", "scan"}:
        raise ValueError("unknown monitor service")
    interval = (300 if command == "tick" else 3600) if interval is None else interval
    if type(interval) is not int or not 1 <= interval <= 86400:
        raise ValueError("launchd service interval must be an integer between 1 and 86400 seconds")
    logs = state / "monitor" / "logs"
    return {"Label": f"local.agent-storage-guard.{command}",
            "ProgramArguments": [str(executable), "--state", str(state), "monitor", command, "--notify"],
            "StartInterval": interval, "RunAtLoad": True,
            "ProcessType": "Background", "Nice": 10, "LowPriorityIO": True, "Umask": 0o077,
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": str(logs / f"{command}.stderr.log")}


def _restore(manifest: dict, archive: Path, controller: Path) -> None:
    domain = manifest["domain"]
    if manifest["activated"]:
        for label in (*LABELS, *LEGACY_LABELS):
            if _run(controller, "print", f"{domain}/{label}", check=False).returncode == 0:
                _run(controller, "bootout", f"{domain}/{label}")
    agents = Path(manifest["agents"])
    for label in (*LABELS, *LEGACY_LABELS):
        destination = agents / f"{label}.plist"
        if _plist_source(destination):
            destination.rename(archive / f"retired-{uuid.uuid4().hex}-{destination.name}")
        original = archive / f"before-{label}.plist"
        if original.exists():
            _copy(original, destination)
    if manifest["activated"]:
        for label in manifest["loaded_before"]:
            source = archive / f"before-{label}.plist"
            if not source.exists():
                raise RuntimeError(f"cannot restore loaded service without its original plist: {label}")
            _run(controller, "bootstrap", domain, str(agents / f"{label}.plist"))


def install(home: Path, executable: Path, state: Path, activate: bool = False,
            launchctl: Path = Path("/bin/launchctl"), intervals: dict | None = None) -> dict:
    if not executable.is_absolute():
        raise RuntimeError("installer requires an absolute executable CLI path")
    executable, state, home = executable.absolute(), state.absolute(), home.absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError("installer requires an absolute executable CLI path")
    if activate and sys.platform != "darwin" and launchctl == Path("/bin/launchctl"):
        raise RuntimeError("launchd activation is available on macOS only")
    intervals = {"tick": 300, "scan": 3600} if intervals is None else intervals
    if not isinstance(intervals, dict) or intervals.keys() != {"tick", "scan"}:
        raise ValueError("service intervals must specify exactly tick and scan")
    for command, interval in intervals.items():
        service_plist(executable, state, command, interval)
    private_directory(state)
    monitor = private_directory(state / "monitor")
    private_directory(monitor / "logs")
    private_directory(monitor / "installations")
    agents = _safe_directory(home / "Library" / "LaunchAgents")
    with os.fdopen(checked_file(monitor / "installation.lock", create=True), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _install_locked(agents, executable, state, activate, launchctl, intervals)


def _install_locked(agents: Path, executable: Path, state: Path, activate: bool,
                    controller: Path, intervals: dict) -> dict:
    archive = private_directory(state / "monitor" / "installations" / uuid.uuid4().hex)
    domain = f"gui/{os.getuid()}"
    manifest = {"schema": 1, "agents": str(agents), "executable": str(executable),
                "state": str(state), "domain": domain, "activated": activate,
                "loaded_before": [], "phase": "prepared"}
    for label in (*LABELS, *LEGACY_LABELS):
        path = agents / f"{label}.plist"
        exists = _plist_source(path)
        if exists:
            _copy(path, archive / f"before-{label}.plist")
        if activate and _run(controller, "print", f"{domain}/{label}", check=False).returncode == 0:
            if not exists:
                raise RuntimeError(f"loaded service has no original plist; cannot guarantee rollback: {label}")
            if plistlib.loads(path.read_bytes()).get("Label") != label:
                raise RuntimeError(f"loaded service plist label does not match; cannot guarantee rollback: {label}")
            manifest["loaded_before"].append(label)
    for command in ("tick", "scan"):
        staged = archive / f"local.agent-storage-guard.{command}.plist"
        with staged.open("xb") as output:
            os.chmod(staged, 0o600)
            plistlib.dump(service_plist(executable, state, command, intervals[command]), output)
        plistlib.loads(staged.read_bytes())
        if sys.platform == "darwin":
            subprocess.run(["/usr/bin/plutil", "-lint", str(staged)], check=True, capture_output=True)
    record = archive / "manifest.json"
    write_json(record, manifest)
    if not activate:
        manifest["phase"] = "staged"
        write_json(record, manifest)
        return {**manifest, "manifest": str(record)}
    try:
        if activate:
            for label in manifest["loaded_before"]:
                _run(controller, "bootout", f"{domain}/{label}")
        for label in LABELS:
            _copy(archive / f"{label}.plist", agents / f"{label}.plist")
        if activate:
            for label in LABELS:
                _run(controller, "bootstrap", domain, str(agents / f"{label}.plist"))
            for label in LEGACY_LABELS:
                prior = agents / f"{label}.plist"
                if prior.exists():
                    prior.rename(archive / f"disabled-{label}.plist")
        manifest["phase"] = "installed"
        write_json(record, manifest)
    except (OSError, RuntimeError, subprocess.SubprocessError) as installation_error:
        try:
            _restore(manifest, archive, controller)
        except (OSError, RuntimeError, subprocess.SubprocessError) as rollback_error:
            raise RuntimeError(f"installation failed ({installation_error}); rollback also failed ({rollback_error}); preserve recovery manifest {record}") from rollback_error
        manifest["phase"] = "rolled_back"
        write_json(record, manifest)
        raise RuntimeError(f"monitor installation failed and was rolled back; details: {record}")
    return {**manifest, "manifest": str(record)}


def rollback(manifest_path: Path, launchctl: Path = Path("/bin/launchctl")) -> dict:
    no_symlinks(manifest_path)
    manifest = read_json(manifest_path)
    required = {"schema", "agents", "executable", "state", "domain", "activated", "loaded_before", "phase"}
    if (not isinstance(manifest, dict) or manifest.keys() != required or manifest.get("schema") != 1
            or type(manifest.get("activated")) is not bool
            or manifest.get("phase") not in {"prepared", "staged", "installed", "rolled_back"}
            or not isinstance(manifest.get("loaded_before"), list)
            or any(label not in (*LABELS, *LEGACY_LABELS) for label in manifest["loaded_before"])
            or any(not isinstance(manifest.get(key), str) for key in ("agents", "state", "domain", "executable"))):
        raise RuntimeError("invalid installation manifest")
    if manifest.get("phase") == "rolled_back":
        return manifest
    monitor = private_directory(Path(manifest["state"]) / "monitor", create=False)
    expected = monitor / "installations" / manifest_path.parent.name / "manifest.json"
    if manifest_path.absolute() != expected or manifest["domain"] != f"gui/{os.getuid()}":
        raise RuntimeError("installation manifest location or user does not match")
    _safe_directory(Path(manifest["agents"]))
    with os.fdopen(checked_file(monitor / "installation.lock", create=True), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if manifest.get("phase") != "staged":
            _restore(manifest, manifest_path.parent, launchctl)
        manifest["phase"] = "rolled_back"
        write_json(manifest_path, manifest)
    return manifest
