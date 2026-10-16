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
assert sys.argv[1:] == ['-l', 'JavaScript', '-']
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


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='JXA syntax boundary requires macOS')
@pytest.mark.parametrize("service,permission,enabled,connection,expected", [("iMessage",False,True,"connected","accepted"),("SMS",False,True,"connected","service_unavailable"),("RCS",False,True,"connected","service_unavailable"),("iMessage",True,True,"connected","permission_denied"),("iMessage",False,False,"connected","service_unavailable"),("iMessage",False,True,"disconnected","service_unavailable")])
def test_jxa_contract_against_controlled_service_without_accessing_messages(service, permission, enabled, connection, expected):
    import json
    import subprocess
    # Replace the application bridge before executing the script: no real app access.
    mock = """
var controlledApplication = function(name) {
    if (name !== 'Messages') throw Error('Unexpected app');
    return {
        accounts: {whose: function(query) {
            if (query.serviceType !== 'iMessage') throw Error('Unrestricted service');
            return function() {
                if (SERVICE !== 'iMessage') return [];
                return [{enabled:function(){return ENABLED;}, connectionStatus:function(){return CONNECTION;},
                         participants:{byName:function(recipient){return recipient;}}}];
            };
        }},
        send: function(text, options) {
            if (DENIED) throw {errorNumber:-1743};
            if (text !== 'Synthetic alert' || options.to !== 'operator@example.invalid') throw Error('Unsafe data');
        }
    };
};
""".replace('SERVICE',json.dumps(service)).replace('DENIED',json.dumps(permission)).replace('ENABLED',json.dumps(enabled)).replace('CONNECTION',json.dumps(connection))
    script = imessage._SCRIPT.replace("Application('Messages')", "controlledApplication('Messages')").replace('deliver(INPUT);','deliver({recipient:"operator@example.invalid",text:"Synthetic alert"});')
    result = subprocess.run(['/usr/bin/osascript','-l','JavaScript','-'],input=mock+script,capture_output=True,text=True,timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='JXA syntax boundary requires macOS')
def test_message_code_is_data_not_executable(native, monkeypatch):
    import json
    import subprocess
    real_run = subprocess.run
    text = '\"}); throw Error(\"injected\"); // \n \u2028'
    expected = json.dumps(text)
    bridge = """var controlledApplication = function(name) {return {
      accounts:{whose:function(query){return function(){return [{
        enabled:function(){return true;},connectionStatus:function(){return 'connected';},
        participants:{byName:function(recipient){return recipient;}}
      }];};}},send:function(text,options){
        if(text !== EXPECTED) throw Error('mismatch');
      }};};
""".replace('EXPECTED', expected)
    def controlled(args, **kwargs):
        assert args[1:] == ['-l','JavaScript','-']
        kwargs['input'] = bridge + kwargs['input'].replace("Application('Messages')", "controlledApplication('Messages')")
        return real_run(['/usr/bin/osascript','-l','JavaScript','-'], **kwargs)
    monkeypatch.setattr(imessage.subprocess,'run',controlled)
    imessage.send({'recipient':'operator@example.invalid'},text)


def test_nonzero_native_process_never_accepts_or_exposes_diagnostics(native):
    native.write_text(native.read_text().replace("print('accepted')", "print('accepted'); print('private diagnostic', file=sys.stderr); sys.exit(1)"))
    with pytest.raises(DeliveryError, match="^imessage_failed$"):
        imessage.send({"recipient":"operator@example.invalid"}, "Synthetic alert")
