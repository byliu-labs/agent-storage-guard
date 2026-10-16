import plistlib
import shutil
import subprocess
import sys
import json
import os
import signal
import time
from pathlib import Path

import pytest

from agent_storage_guard import notifications


def test_applescript_payload_is_passed_as_arguments_not_interpolated(monkeypatch):
    monkeypatch.setattr(notifications.sys, "platform", "darwin")
    alert = {"title": 'quotes " and shell $(touch /tmp/unwanted)', "message": "line1\nline2"}
    command = notifications.command(alert)
    assert command[-2:] == [alert["title"], alert["message"]]
    assert alert["title"] not in command[2]


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS compiler")
def test_prepare_app_builds_registered_identity_and_supports_read_only_selftest(tmp_path, monkeypatch):
    # A temporary registered helper must never replace the installed app's
    # Launch Services association or inspect its notification permissions.
    monkeypatch.setattr(notifications, "BUNDLE_ID", notifications.BUNDLE_ID + ".tests")
    cli = tmp_path / "test-cli"
    child_pid = tmp_path / "child.pid"
    scanner_pid = tmp_path / "scanner.pid"
    scanner = tmp_path / "slow-du"
    scanner.write_text(f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(scanner_pid)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
    scanner.chmod(0o700)
    cli.write_text(f'''#!{sys.executable}
import json, os, sys, time
from pathlib import Path
if "scan" in sys.argv:
    Path({str(child_pid)!r}).write_text(str(os.getpid()))
    try:
        from agent_storage_guard import folder_scan
        folder_scan._DU = {str(scanner)!r}
        folder_scan.scan_root({{"path": {str(tmp_path)!r}, "depth": 1}}, timeout=30)
    except KeyboardInterrupt:
        sys.exit(130)
else:
    print(json.dumps(sys.argv[1:]))
    sys.exit(7)
''')
    cli.chmod(0o700)
    app = notifications.prepare_app(tmp_path / "sender", tmp_path / "report.html", cli, tmp_path / "state")
    manifest = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert manifest["CFBundleDisplayName"] == "Agent Storage Guard"
    assert manifest["LSUIElement"]
    assert manifest["CFBundleIdentifier"].endswith(".tests")
    assert manifest["ASGReportPath"] == str(tmp_path / "report.html")
    assert manifest["ASGMonitorCLI"] == str(cli)
    assert manifest["NSAppleEventsUsageDescription"]
    assert len(manifest["ASGBuildDigest"]) == 64
    icon = app / "Contents/Resources" / manifest["CFBundleIconFile"]
    assert icon.read_bytes().startswith(b"icns")
    executable = app / "Contents/MacOS/Agent Storage Guard"
    inode = executable.stat().st_ino
    assert notifications.prepare_app(tmp_path / "sender", tmp_path / "report.html", cli, tmp_path / "state") == app
    assert executable.stat().st_ino == inode
    assert not list(app.parent.glob("*.previous-*"))
    result = subprocess.run([str(executable), "--self-test"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert notifications.BUNDLE_ID in result.stdout
    assert "authorizationStatus" in result.stdout
    diagnostics = notifications.diagnose(app)
    assert diagnostics["authorization"] in {"notDetermined", "denied", "authorized", "provisional", "unknown"}
    assert isinstance(diagnostics["alerts_enabled"], bool)
    assert diagnostics["delivered_count"] >= 0
    assert notifications.command({"key":"memory", "title":"Pressure", "message":"Save work"}, app=app)[-1] == "MEMORY"
    result = subprocess.run([str(executable), "--monitor", "tick"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == ["--state", str(tmp_path / "state"), "monitor", "tick", "--notify"]
    result = subprocess.run([str(executable), "--test-send", "messages-test", str(tmp_path / "state")], capture_output=True, text=True, timeout=15)
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == ["--state", str(tmp_path / "state"), "monitor", "test-send", "--destination", "messages-test"]
    for destination in ("UPPER", "../other", "", "a" * 41, "test\n", "test\r", "test\u2028"):
        result = subprocess.run([str(executable), "--test-send", destination, str(tmp_path / "state")], capture_output=True, text=True, timeout=10)
        assert result.returncode == 64
    for extra in ([], [str(tmp_path / "wrong-state")]):
        result = subprocess.run([str(executable), "--test-send", "messages-test", *extra], capture_output=True, text=True, timeout=10)
        assert result.returncode == 64
        assert not result.stdout
    for args in (["--monitor", "run"], ["--monitor", "tick", "--anything"]):
        result = subprocess.run([str(executable), *args], capture_output=True, text=True, timeout=10)
        assert result.returncode == 64
    process = subprocess.Popen([str(executable), "--monitor", "scan"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = None
    try:
        deadline = time.monotonic() + 10
        while not scanner_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pid = int(child_pid.read_text())
        native_pid = int(scanner_pid.read_text())
        process.terminate()
        assert process.wait(timeout=10) == 130
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        with pytest.raises(ProcessLookupError):
            os.kill(native_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if scanner_pid.exists():
            try:
                os.kill(int(scanner_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


    changed = notifications.prepare_app(tmp_path / "sender", tmp_path / "other-report.html", cli, tmp_path / "state")
    changed_manifest = plistlib.loads((changed / "Contents/Info.plist").read_bytes())
    assert changed_manifest["ASGBuildDigest"] != manifest["ASGBuildDigest"]
    assert len(list(app.parent.glob("*.previous-*"))) == 1


@pytest.mark.parametrize("change", ["none", "manifest", "signature", "symlink", "missing", "not-executable"])
def test_reuse_requires_matching_owned_bundle_and_verified_signature(tmp_path, monkeypatch, change):
    app = tmp_path / "Example.app"
    binary = app / "Contents/MacOS/Agent Storage Guard"
    binary.parent.mkdir(parents=True)
    binary.write_text("synthetic executable")
    binary.chmod(0o700)
    manifest = {"ASGBuildDigest": "synthetic", "ASGReportPath": "synthetic-report"}
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(manifest))
    if change == "manifest":
        manifest = {**manifest, "ASGReportPath": "changed-report"}
    elif change == "symlink":
        binary.rename(binary.with_suffix(".original"))
        binary.symlink_to(binary.with_suffix(".original"))
    elif change == "missing":
        binary.rename(binary.with_suffix(".original"))
    elif change == "not-executable":
        binary.chmod(0o600)
    calls = []
    def verify(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, int(change == "signature"))
    monkeypatch.setattr(notifications.subprocess, "run", verify)
    assert notifications._reusable_app(app, manifest) is (change == "none")
    assert bool(calls) is (change in {"none", "signature"})
    if calls:
        assert calls == [["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]]


def test_linux_without_sender_fails_visibly(monkeypatch):
    monkeypatch.setattr(notifications.sys,"platform","linux")
    monkeypatch.setattr(notifications.shutil,"which",lambda _:None)
    with pytest.raises(RuntimeError,match="no native notification sender"):
        notifications.command({"title":"Reserve","message":"Review recent growers"})


def test_linux_sender_keeps_titles_as_literal_arguments(monkeypatch):
    monkeypatch.setattr(notifications.sys,"platform","linux")
    monkeypatch.setattr(notifications.shutil,"which",lambda _:"/usr/bin/notify-send")
    assert notifications.command({"title":"--not-an-option","message":"Review growers"}) == ["/usr/bin/notify-send","--app-name=Agent Storage Guard","--","--not-an-option","Review growers"]
