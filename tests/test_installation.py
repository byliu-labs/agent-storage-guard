"""Install real launchd plists; simulate only the unavailable launchd lifecycle."""
import os
from pathlib import Path
import plistlib
import subprocess

import pytest

from agent_storage_guard import installation


def executable(tmp_path: Path) -> Path:
    target = tmp_path / "guard"
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o700)
    return target


def launchctl(tmp_path: Path, fail_scan: bool = False) -> Path:
    target = tmp_path / "launchctl"
    target.write_text("#!/usr/bin/env python3\n" +
        "import json, pathlib, sys, plistlib\n" +
        f"p=pathlib.Path({str(tmp_path / 'jobs.json')!r})\n" +
        "jobs=json.loads(p.read_text()) if p.exists() else []\n" +
        "action=sys.argv[1]; label=sys.argv[2].split('/')[-1]\n" +
        "if action == 'print': sys.exit(0 if label in jobs else 113)\n" +
        "if action == 'bootout':\n jobs.remove(label)\n" +
        "if action == 'bootstrap':\n" +
        " label=plistlib.loads(pathlib.Path(sys.argv[3]).read_bytes())['Label']\n" +
        f" if {fail_scan!r} and label == 'local.agent-storage-guard.scan': sys.exit(5)\n" +
        " if label in jobs: sys.exit(5)\n jobs.append(label)\n" +
        "p.write_text(json.dumps(jobs))\n")
    target.chmod(0o700)
    return target


def test_stage_installs_private_valid_plists_without_stopping_old_jobs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = installation.install(home, executable(tmp_path), home / ".asg")
    for command, seconds in (("tick", 300), ("scan", 3600)):
        path = Path(result["manifest"]).parent / f"local.agent-storage-guard.{command}.plist"
        content = plistlib.loads(path.read_bytes())
        assert content["ProgramArguments"][-3:] == ["monitor", command, "--notify"]
        assert content["StartInterval"] == seconds
        assert content["LowPriorityIO"] and content["Nice"] == 10
        assert path.stat().st_mode & 0o777 == 0o600
        if Path("/usr/bin/plutil").exists() and os.uname().sysname == "Darwin":
            subprocess.run(["/usr/bin/plutil", "-lint", str(path)], check=True, capture_output=True)
    assert result["activated"] is False
    assert result["phase"] == "staged"
    assert not list((home / "Library/LaunchAgents").glob("*.plist"))
    assert Path(result["manifest"]).is_file()


def test_failed_activation_restores_old_loaded_monitor_and_plist(tmp_path: Path) -> None:
    import json

    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    old = agents / "local.pc-backup.disk-tick.plist"
    old.write_bytes(plistlib.dumps({"Label": "local.pc-backup.disk-tick"}))
    old.chmod(0o600)
    original = old.read_bytes()
    (tmp_path / "jobs.json").write_text(json.dumps(["local.pc-backup.disk-tick"]))
    with pytest.raises(RuntimeError, match="rolled back"):
        installation.install(home, executable(tmp_path), home / ".asg", True, launchctl(tmp_path, True))
    assert old.read_bytes() == original
    assert json.loads((tmp_path / "jobs.json").read_text()) == ["local.pc-backup.disk-tick"]
    assert not (agents / "local.agent-storage-guard.tick.plist").exists()
    assert not (agents / "local.agent-storage-guard.scan.plist").exists()


def test_successful_activation_and_explicit_rollback_restore_prior_jobs(tmp_path: Path) -> None:
    import json

    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    old = agents / "local.pc-backup.disk-scan.plist"
    old.write_bytes(plistlib.dumps({"Label": "local.pc-backup.disk-scan"}))
    old.chmod(0o600)
    (tmp_path / "jobs.json").write_text(json.dumps(["local.pc-backup.disk-scan"]))
    controller = launchctl(tmp_path)
    result = installation.install(home, executable(tmp_path), home / ".asg", True, controller)
    assert not old.exists()
    assert set(json.loads((tmp_path / "jobs.json").read_text())) == set(installation.LABELS)
    installation.rollback(Path(result["manifest"]), controller)
    assert old.exists()
    assert json.loads((tmp_path / "jobs.json").read_text()) == ["local.pc-backup.disk-scan"]
    installation.rollback(Path(result["manifest"]), controller)
    assert json.loads((tmp_path / "jobs.json").read_text()) == ["local.pc-backup.disk-scan"]


def test_refuses_symlinked_agent_file_without_touching_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    target = tmp_path / "unrelated"
    target.write_text("preserve")
    (agents / "local.agent-storage-guard.tick.plist").symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        installation.install(home, executable(tmp_path), home / ".asg")
    assert target.read_text() == "preserve"


def test_concurrent_install_is_refused_by_same_real_lock(tmp_path: Path) -> None:
    import fcntl
    import threading
    from concurrent.futures import ThreadPoolExecutor

    home = tmp_path / "home"
    cli = executable(tmp_path)
    installation.install(home, cli, home / ".asg")
    entered, release = threading.Event(), threading.Event()

    def hold_installation_lock() -> None:
        with (home / ".asg/monitor/installation.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(hold_installation_lock)
        assert entered.wait(5)
        try:
            with pytest.raises(BlockingIOError):
                installation.install(home, cli, home / ".asg")
        finally:
            release.set()
            held.result()


def test_invalid_executable_never_changes_existing_plist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with pytest.raises(RuntimeError, match="executable"):
        installation.install(home, tmp_path / "missing", home / ".asg")
    assert not home.exists()


def test_corrupt_rollback_manifest_is_refused_before_changes(tmp_path: Path) -> None:
    import json

    home = tmp_path / "home"
    result = installation.install(home, executable(tmp_path), home / ".asg")
    manifest = Path(result["manifest"])
    content = json.loads(manifest.read_text())
    content["loaded_before"] = ["unrelated.backup.job"]
    manifest.write_text(json.dumps(content))
    with pytest.raises(RuntimeError, match="manifest"):
        installation.rollback(manifest)
    assert not list((home / "Library/LaunchAgents").glob("*.plist"))


def test_configured_intervals_reach_actual_valid_launchd_plists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    result = installation.install(home, executable(tmp_path), home / ".asg",
                                  intervals={"tick": 60, "scan": 900})
    archive = Path(result["manifest"]).parent
    for command, seconds in (("tick", 60), ("scan", 900)):
        path = archive / f"local.agent-storage-guard.{command}.plist"
        assert plistlib.loads(path.read_bytes())["StartInterval"] == seconds
        if os.uname().sysname == "Darwin":
            subprocess.run(["/usr/bin/plutil", "-lint", str(path)], check=True, capture_output=True)


@pytest.mark.parametrize("intervals", [{"tick": 60}, {"tick": 0, "scan": 900},
                                      {"tick": 0.5, "scan": 900}, {"tick": True, "scan": 900}])
def test_invalid_intervals_never_prepare_scheduling(tmp_path: Path, intervals: dict) -> None:
    home = tmp_path / "home"
    with pytest.raises(ValueError, match="interval"):
        installation.install(home, executable(tmp_path), home / ".asg", intervals=intervals)
    assert not home.exists()
