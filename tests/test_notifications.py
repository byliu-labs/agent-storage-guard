import plistlib
import shutil
import subprocess
import sys
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
def test_prepare_app_builds_registered_identity_and_supports_read_only_selftest(tmp_path):
    app = notifications.prepare_app(tmp_path / "sender", tmp_path / "report.txt")
    manifest = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert manifest["CFBundleDisplayName"] == "Agent Storage Guard"
    assert manifest["LSUIElement"]
    executable = app / "Contents/MacOS/storage-alert"
    result = subprocess.run([str(executable), "--self-test"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert notifications.BUNDLE_ID in result.stdout
    assert "authorizationStatus" in result.stdout
    diagnostics = notifications.diagnose(app)
    assert diagnostics["authorization"] in {"notDetermined", "denied", "authorized", "provisional", "unknown"}
    assert isinstance(diagnostics["alerts_enabled"], bool)
    assert diagnostics["delivered_count"] >= 0
    assert notifications.command({"key":"memory", "title":"Pressure", "message":"Save work"}, app=app)[-1] == "MEMORY"


def test_linux_without_sender_fails_visibly(monkeypatch):
    monkeypatch.setattr(notifications.sys,"platform","linux")
    monkeypatch.setattr(notifications.shutil,"which",lambda _:None)
    with pytest.raises(RuntimeError,match="no native notification sender"):
        notifications.command({"title":"Reserve","message":"Review recent growers"})


def test_linux_sender_keeps_titles_as_literal_arguments(monkeypatch):
    monkeypatch.setattr(notifications.sys,"platform","linux")
    monkeypatch.setattr(notifications.shutil,"which",lambda _:"/usr/bin/notify-send")
    assert notifications.command({"title":"--not-an-option","message":"Review growers"}) == ["/usr/bin/notify-send","--app-name=Agent Storage Guard","--","--not-an-option","Review growers"]
