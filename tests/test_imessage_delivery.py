"""Native delivery contract without launching Messages or reading its account data."""
import pytest
from agent_storage_guard import imessage_delivery as imessage
from agent_storage_guard.delivery_transports import DeliveryError


def test_check_is_offline_and_rejects_invalid_recipient(monkeypatch):
    monkeypatch.setattr(imessage.sys, "platform", "darwin")
    monkeypatch.setattr(imessage.os, "access", lambda *args: True)
    with pytest.raises(DeliveryError, match="^invalid_configuration$"):
        imessage.check({"recipient": "private\n@example.invalid"})


@pytest.fixture
def native(monkeypatch, tmp_path):
    import sys
    helper = tmp_path / "controlled-native"
    helper.write_text("#!" + sys.executable + "\n" + """import sys
script = sys.stdin.read()
assert sys.argv[1:] == ['-']
assert 'operator@example.invalid' in script
assert 'Synthetic alert' in script
print('accepted')
""")
    helper.chmod(0o700)
    monkeypatch.setattr(imessage.sys, "platform", "darwin")
    monkeypatch.setattr(imessage, "_OSASCRIPT", str(helper))
    return helper


def test_send_uses_private_stdin_and_accepts_native_acknowledgment(native):
    imessage.send({"recipient": "operator@example.invalid"}, "Synthetic alert")


@pytest.mark.parametrize("outcome,code", [("permission_denied","imessage_permission_denied"),("service_unavailable","imessage_service_unavailable"),("failed","imessage_failed"),("private raw failure","imessage_failed")])
def test_native_failure_is_redacted(native, outcome, code):
    native.write_text(native.read_text().replace("print('accepted')", "print(" + repr(outcome) + ")"))
    with pytest.raises(DeliveryError, match="^" + code + "$"):
        imessage.send({"recipient":"operator@example.invalid"}, "Synthetic alert")


def test_timeout_reaps_native_child(monkeypatch, native):
    import subprocess
    native.write_text(native.read_text().replace("print('accepted')", "import time; time.sleep(30)"))
    monkeypatch.setattr(imessage, "_TIMEOUT_SECONDS", .1)
    children = []
    original = subprocess.Popen
    class Child(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            children.append(self)
    monkeypatch.setattr(subprocess, "Popen", Child)
    with pytest.raises(DeliveryError, match="^timeout$") as error:
        imessage.send({"recipient":"operator@example.invalid"}, "Synthetic alert")
    assert error.value.retryable
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("recipient", [None, 12, "", "a", "a\n@example.invalid", "a@example.invalid;do shell script", "\ud800", "1"*255])
def test_malformed_recipient_fails_without_process(monkeypatch, recipient):
    monkeypatch.setattr(imessage.subprocess, "run", lambda *a,**k: pytest.fail("process launched"))
    with pytest.raises(DeliveryError, match="^invalid_configuration$"):
        imessage.check({"recipient":recipient})


def test_check_does_not_launch_messages_or_request_permissions(native, monkeypatch):
    monkeypatch.setattr(imessage.subprocess, "run", lambda *a,**k: pytest.fail("process launched"))
    imessage.check({"recipient":"+15555550100"})


def test_nonmac_is_an_explicit_gate(monkeypatch):
    monkeypatch.setattr(imessage.sys, "platform", "linux")
    with pytest.raises(DeliveryError, match="^imessage_platform_unavailable$"):
        imessage.check({"recipient":"operator@example.invalid"})


@pytest.mark.parametrize("text", ["", "\ud800", "x"*16001, None])
def test_invalid_message_is_redacted(native, text):
    with pytest.raises(DeliveryError, match="^invalid_payload$"):
        imessage.send({"recipient":"operator@example.invalid"}, text)


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='AppleScript compiler requires macOS')
def test_native_script_compiles_against_real_messages_dictionary(tmp_path):
    import subprocess
    script = imessage._SCRIPT.replace('deliver(INPUT)', 'deliver("operator@example.invalid", "Synthetic alert")')
    result = subprocess.run(['/usr/bin/osacompile', '-o', str(tmp_path / 'sender.scpt'), '-'], input=script, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='AppleScript boundary requires macOS')
def test_message_code_is_data_not_executable():
    import subprocess
    text = '\" & error \"injected\" & \"\n\r\t\\ Unicode: 雪'
    # Evaluate only a literal, never the Messages bridge or a send command.
    script = 'return ' + imessage._literal(text)
    result = subprocess.run(['/usr/bin/osascript', '-'], input=script.encode(), capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().rstrip('\n') == text


def test_nonzero_native_process_never_accepts_or_exposes_diagnostics(native):
    native.write_text(native.read_text().replace("print('accepted')", "print('accepted'); print('private diagnostic', file=sys.stderr); sys.exit(1)"))
    with pytest.raises(DeliveryError, match="^imessage_failed$"):
        imessage.send({"recipient":"operator@example.invalid"}, "Synthetic alert")
