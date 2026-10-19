"""Installed helper -> CLI -> native boundary, without calling real Messages."""
import json
from pathlib import Path
import shutil
import sys

import pytest

from agent_storage_guard import monitor, notifications
from agent_storage_guard.cli import parser
from agent_storage_guard.monitor_store import MonitorStore


@pytest.fixture
def launchservices_stub(tmp_path, monkeypatch):
    """Real subprocess/file boundary; emulate open's lack of app exit status."""
    from agent_storage_guard import delivery
    opener = tmp_path / "open"
    marker = tmp_path / "launched-through-services"
    opener.write_text(f'''#!{sys.executable}
import subprocess,sys
from pathlib import Path
args=sys.argv[1:]
assert args[:3] == ['-n','-W','-g']
Path({str(marker)!r}).write_text('launched')
app=Path(args[args.index('-a')+1])
with open(args[args.index('--stdout')+1],'w') as out, open(args[args.index('--stderr')+1],'w') as err:
    subprocess.run([str(app/'Contents/MacOS/Agent Storage Guard'),*args[args.index('--args')+1:]],stdout=out,stderr=err)
''')
    opener.chmod(0o700)
    monkeypatch.setattr(delivery, "_OPEN", str(opener), raising=False)
    return marker


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS helper compiler")
def test_installed_identity_test_send_reaches_controlled_native_boundary(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "state/monitor"
    monitor.initialize(directory, [tmp_path / "synthetic-workspaces"])
    config = monitor.load_config(directory)
    config["destinations"] = [{"id": "imessage", "provider": "imessage", "settings_env": "ASG_SYNTHETIC_IMESSAGE"}]
    (directory / "config.json").write_text(json.dumps(config))
    monkeypatch.setenv("ASG_SYNTHETIC_IMESSAGE", '{"recipient":"synthetic@example.invalid"}')
    boundary = tmp_path / "native-boundary"
    boundary.write_text(f'''#!{sys.executable}
import sys
assert sys.argv[1:] == ['-']
source=sys.stdin.read()
assert 'synthetic@example.invalid' in source
assert 'Synthetic test only' in source
print('accepted')
''')
    boundary.chmod(0o700)
    cli = tmp_path / "synthetic-cli"
    cli.write_text(f'''#!{sys.executable}
import os
os.environ['ASG_SYNTHETIC_IMESSAGE']='{{"recipient":"synthetic@example.invalid"}}'
from agent_storage_guard import imessage_delivery
from agent_storage_guard.cli import main
imessage_delivery._OSASCRIPT={str(boundary)!r}
raise SystemExit(main())
''')
    cli.chmod(0o700)
    monkeypatch.setattr(notifications, "BUNDLE_ID", notifications.BUNDLE_ID + ".imessage-tests")
    app = notifications.prepare_app(tmp_path / "sender", directory / "report.html", cli, directory.parent)
    with MonitorStore(directory) as store:
        store.set_value("notification_app", str(app))
    args = parser().parse_args(["--state", str(directory.parent), "monitor", "test-send", "--destination", "imessage", "--installed"])
    assert monitor.dispatch(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["acceptance"] == "accepted" and receipt["receipt"] == "not_observed"
    assert "synthetic@example.invalid" not in json.dumps(receipt)
    with MonitorStore(directory) as store:
        entry = store.get_value("delivery_state")["imessage"]
        assert entry["health"]["acceptance"] == "accepted"
        assert entry["delivered"] == {}
        assert not store.latest_capacity()


def test_installed_sender_never_forwards_raw_helper_output(tmp_path, launchservices_stub):
    import plistlib
    from agent_storage_guard import delivery
    app = tmp_path / "sender.app"
    binary = app / "Contents/MacOS/Agent Storage Guard"
    binary.parent.mkdir(parents=True)
    binary.write_text(f'''#!{sys.executable}
import json
print(json.dumps({{"destination":"imessage","synthetic":True,"acceptance":"accepted","receipt":"not_observed", "private":"PRIVATE_FIXTURE"}}))
''')
    binary.chmod(0o700)
    state = tmp_path / "state"
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"ASGMonitorState": str(state)}))
    receipt, code = delivery.installed_test(app, "imessage", state)
    assert launchservices_stub.exists()
    assert code == 0
    assert "PRIVATE_FIXTURE" not in json.dumps(receipt)


def test_installed_sender_rejects_helper_rebound_to_another_state_before_execution(tmp_path):
    import plistlib
    from agent_storage_guard import delivery
    app = tmp_path / "sender.app"
    binary = app / "Contents/MacOS/Agent Storage Guard"
    binary.parent.mkdir(parents=True)
    marker = tmp_path / "unexpected-execution"
    binary.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n')
    binary.chmod(0o700)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"ASGMonitorState": str(tmp_path / "other-state")}))
    with pytest.raises(RuntimeError, match="installed_helper_state_mismatch"):
        delivery.installed_test(app, "imessage", tmp_path / "expected-state")
    assert not marker.exists()


@pytest.mark.parametrize("error,expected", [("timeout", "timeout"), ("imessage_permission_denied", "imessage_permission_denied"), ("PRIVATE_FIXTURE", "delivery_failed")])
def test_installed_failure_has_safe_error_and_action(tmp_path, error, expected, launchservices_stub):
    import plistlib
    from agent_storage_guard import delivery
    app = tmp_path / "sender.app"
    binary = app / "Contents/MacOS/Agent Storage Guard"
    binary.parent.mkdir(parents=True)
    result = {"destination":"imessage", "synthetic":True, "acceptance":"failed", "receipt":"not_observed", "code":error}
    binary.write_text(f"#!{sys.executable}\nimport sys\nprint({json.dumps(result)!r})\nsys.exit(1)\n")
    binary.chmod(0o700)
    state = tmp_path / "state"
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"ASGMonitorState":str(state)}))
    receipt, status = delivery.installed_test(app, "imessage", state)
    assert launchservices_stub.exists()
    assert status == 1
    assert receipt["code"] == expected
    assert "PRIVATE_FIXTURE" not in json.dumps(receipt)
    assert receipt["next_action"] != "Confirm receipt independently; run monitor delivery-check for redacted gate details if delivery failed."
