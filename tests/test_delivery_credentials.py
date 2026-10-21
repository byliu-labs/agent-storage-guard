"""Exercise the native boundary without querying any actual Keychain item."""
import ctypes

import pytest

from agent_storage_guard import delivery_credentials as credentials


class Security:
    def __init__(self, payload=b'{"synthetic":"settings"}', status=0):
        self.payload, self.status = payload, status
        self.buffer = ctypes.create_string_buffer(payload)
        self.calls = []
        self.restore_status = 0
        self.free_status = 0
        self.add_status = 0

    def SecKeychainAddGenericPassword(self, keychain, service_length, service,
                                     account_length, account, length, data, item):
        assert keychain is None and item is None
        assert service[:service_length] == b"example.synthetic"
        assert account[:account_length] == b"agent-storage-guard"
        assert data[:length] == b'{"synthetic":"settings"}'
        self.calls.append("add")
        return self.add_status

    def SecKeychainGetUserInteractionAllowed(self, state):
        state._obj.value = 1
        self.calls.append("get")
        return 0

    def SecKeychainSetUserInteractionAllowed(self, state):
        self.calls.append(("interaction", state))
        return self.restore_status if state else 0

    def SecKeychainFindGenericPassword(self, keychain, service_length, service,
                                      account_length, account, length, data, item):
        assert keychain is None and item is None
        assert service_length == len(service) and service == b"example.synthetic"
        assert account_length == len(account) and account == b"agent-storage-guard"
        self.calls.append("find")
        if self.status == 0:
            length._obj.value = len(self.payload)
            data._obj.value = ctypes.addressof(self.buffer)
        return self.status

    def SecKeychainItemFreeContent(self, attributes, data):
        assert attributes is None and data.value == ctypes.addressof(self.buffer)
        self.calls.append("free")
        return self.free_status


def boundary(monkeypatch, **kwargs):
    native = Security(**kwargs)
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(credentials, "_security", lambda: native)
    return native


def test_reads_exact_service_account_without_prompts_and_frees_content(monkeypatch):
    native = boundary(monkeypatch)
    assert credentials.read_keychain("example.synthetic") == '{"synthetic":"settings"}'
    assert native.calls == ["get", ("interaction", 0), "find", "free", ("interaction", 1)]


@pytest.mark.parametrize("status", [-25300, -25293, -25308, -1])
def test_native_failure_is_redacted_and_restores_interaction(monkeypatch, status):
    native = boundary(monkeypatch, status=status)
    with pytest.raises(RuntimeError, match="^keychain_locked_or_denied$"):
        credentials.read_keychain("example.synthetic")
    assert native.calls[-1] == ("interaction", 1)
    assert "free" not in native.calls


@pytest.mark.parametrize("payload", [b"", b"x" * 16385, b"\xff"], ids=["empty", "oversized", "invalid-utf8"])
def test_invalid_or_oversized_content_is_not_returned_and_is_freed(monkeypatch, payload):
    native = boundary(monkeypatch, payload=payload)
    with pytest.raises(RuntimeError, match="^keychain_invalid_content$"):
        credentials.read_keychain("example.synthetic")
    assert native.calls[-2:] == ["free", ("interaction", 1)]


def test_unsupported_platform_never_loads_native_framework(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    monkeypatch.setattr(credentials, "_security", lambda: pytest.fail("native lookup"))
    with pytest.raises(RuntimeError, match="^keychain_unavailable$"):
        credentials.read_keychain("example.synthetic")


@pytest.mark.parametrize("service", ["", "bad\x00name", "x" * 1025, None], ids=["empty", "nul", "oversized", "not-string"])
def test_invalid_service_never_queries_native_boundary(monkeypatch, service):
    monkeypatch.setattr(credentials, "_security", lambda: pytest.fail("native lookup"))
    with pytest.raises(RuntimeError, match="^keychain_invalid_service$"):
        credentials.read_keychain(service)


@pytest.mark.parametrize("failure", ["restore_status", "free_status"])
def test_cleanup_errors_are_visible_and_redacted(monkeypatch, failure):
    native = boundary(monkeypatch)
    setattr(native, failure, -1)
    with pytest.raises(RuntimeError, match="^keychain_unavailable$"):
        credentials.read_keychain("example.synthetic")
    assert native.calls[-1] == ("interaction", 1)


def test_failed_interaction_query_prevents_lookup(monkeypatch):
    native = boundary(monkeypatch)
    native.SecKeychainGetUserInteractionAllowed = lambda state: -1
    with pytest.raises(RuntimeError, match="^keychain_unavailable$"):
        credentials.read_keychain("example.synthetic")
    assert native.calls == []


def test_failed_prompt_suppression_prevents_lookup_and_restores_state(monkeypatch):
    native = boundary(monkeypatch)
    native.SecKeychainSetUserInteractionAllowed = lambda state: native.calls.append(state) or (-1 if not state else 0)
    with pytest.raises(RuntimeError, match="^keychain_unavailable$"):
        credentials.read_keychain("example.synthetic")
    assert native.calls == ["get", 0, 1]


def test_framework_load_failure_does_not_reveal_exception(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "darwin")

    def unavailable():
        raise OSError("synthetic private loader details")

    monkeypatch.setattr(credentials, "_security", unavailable)
    with pytest.raises(RuntimeError, match="^keychain_unavailable$"):
        credentials.read_keychain("example.synthetic")


def test_previously_disabled_interaction_remains_disabled(monkeypatch):
    native = boundary(monkeypatch)
    native.SecKeychainGetUserInteractionAllowed = lambda state: 0
    credentials.read_keychain("example.synthetic")
    assert native.calls[-1] == ("interaction", 0)


def test_write_saves_exact_item_without_credential_arguments_or_prompts(monkeypatch):
    native = boundary(monkeypatch)
    credentials.write_keychain("example.synthetic", '{"synthetic":"settings"}')
    assert native.calls == ["get", ("interaction", 0), "add", ("interaction", 1)]


@pytest.mark.parametrize("status,code", [(-25299, "keychain_item_exists"), (-25308, "keychain_locked_or_denied")])
def test_write_never_overwrites_existing_item_or_exposes_native_errors(monkeypatch, status, code):
    native = boundary(monkeypatch)
    native.add_status = status
    with pytest.raises(RuntimeError, match="^" + code + "$"):
        credentials.write_keychain("example.synthetic", '{"synthetic":"settings"}')
    assert native.calls[-1] == ("interaction", 1)


@pytest.mark.parametrize("value", [None, "", "\ud800", "x" * 16385], ids=["not-text", "empty", "surrogate", "oversized"])
def test_invalid_write_payload_never_reaches_keychain(monkeypatch, value):
    monkeypatch.setattr(credentials, "_security", lambda: pytest.fail("Keychain accessed"))
    with pytest.raises(RuntimeError, match="^keychain_invalid_content$"):
        credentials.write_keychain("example.synthetic", value)
