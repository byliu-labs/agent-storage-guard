"""Setup persists references only, using real private files and a fake Keychain."""
import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_storage_guard import delivery_setup, delivery_credentials, monitor, ownership

WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/SYNTHETIC-PRIVATE"
SECRET = "SYNTHETIC-PRIVATE-SIGNATURE"


def inputs(monkeypatch, values=(WEBHOOK, SECRET)):
    # Emulate only setup's OS boundary. Mutating the shared sys module also makes
    # unrelated host probes try macOS commands on Linux.
    monkeypatch.setattr(delivery_setup, "sys", SimpleNamespace(
        platform="darwin", stdin=SimpleNamespace(isatty=lambda: True)))
    iterator = iter(values)
    monkeypatch.setattr(delivery_setup.getpass, "getpass", lambda prompt: next(iterator))
    saved = []
    monkeypatch.setattr(delivery_credentials, "write_keychain", lambda *args: saved.append(args), raising=False)
    return saved


def test_fresh_setup_keeps_credentials_out_of_all_state_files(tmp_path, monkeypatch):
    saved = inputs(monkeypatch)
    directory = tmp_path / "state/monitor"
    root = tmp_path / "synthetic-ci"
    summary = delivery_setup.configure(directory, "feishu", "personal", [root])
    config = monitor.load_config(directory)
    assert config["roots"][0]["path"] == str(root)
    assert config["destinations"] == [
        {"id": "personal", "provider": "feishu", "settings_keychain": "asg-feishu-personal"}]
    assert saved == [("asg-feishu-personal", json.dumps({"webhook_url": WEBHOOK, "signing_secret": SECRET}))]
    public = json.dumps(summary)
    for path in directory.rglob("*"):
        if path.is_file():
            public += path.read_bytes().decode("utf-8", errors="replace")
    assert WEBHOOK not in public and SECRET not in public
    assert str(directory) not in json.dumps(summary)
    assert summary["next_commands"] == ["monitor delivery-check", "monitor install",
        "monitor test-send --destination personal --installed", "monitor install --activate"]


def test_existing_config_and_other_destinations_are_preserved(tmp_path, monkeypatch):
    directory = tmp_path / "monitor"
    monitor.initialize(directory, [tmp_path / "root"])
    config = monitor.load_config(directory)
    config["operator_label"] = "Existing label"
    config["extra_existing_setting"] = 42
    config["destinations"] = [{"id": "other", "provider": "feishu", "settings_env": "OTHER"}]
    ownership.write_json(directory / "config.json", config)
    inputs(monkeypatch)
    delivery_setup.configure(directory, "feishu", "personal")
    actual = monitor.load_config(directory)
    assert actual == {**config, "destinations": [*config["destinations"],
        {"id": "personal", "provider": "feishu", "settings_keychain": "asg-feishu-personal"}]}


@pytest.mark.parametrize("git_marker", ["directory", "file"])
def test_git_checkout_refused_before_prompt_or_storage(tmp_path, monkeypatch, git_marker):
    marker = tmp_path / ".git"
    marker.mkdir() if git_marker == "directory" else marker.write_text("gitdir: synthetic")
    inputs(monkeypatch, ())
    directory = tmp_path / "state/monitor"
    with pytest.raises(RuntimeError, match="outside_git"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert not directory.exists()


@pytest.mark.parametrize("change", ["duplicate", "roots"])
def test_existing_destination_or_new_roots_fail_without_prompt(tmp_path, monkeypatch, change):
    directory = tmp_path / "monitor"
    monitor.initialize(directory, [])
    config = monitor.load_config(directory)
    config["destinations"] = [{"id": "personal", "provider": "native"}]
    ownership.write_json(directory / "config.json", config)
    before = (directory / "config.json").read_bytes()
    saved = inputs(monkeypatch, ())
    with pytest.raises(RuntimeError):
        delivery_setup.configure(directory, "feishu", "personal", [] if change == "roots" else None)
    assert (directory / "config.json").read_bytes() == before and saved == []


@pytest.mark.parametrize("values", [("invalid-private-url", SECRET), (WEBHOOK, "")])
def test_invalid_private_values_fail_redacted_before_storage(tmp_path, monkeypatch, values):
    saved = inputs(monkeypatch, values)
    directory = tmp_path / "monitor"
    with pytest.raises(RuntimeError, match="^setup_invalid_settings$"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert not directory.exists() and saved == []


def test_noninteractive_or_unsupported_platform_fails_before_initialization(tmp_path, monkeypatch):
    inputs(monkeypatch, ())
    directory = tmp_path / "monitor"
    monkeypatch.setattr(delivery_setup.sys.stdin, "isatty", lambda: False)
    with pytest.raises(RuntimeError, match="terminal_required"):
        delivery_setup.configure(directory, "feishu", "personal")
    monkeypatch.setattr(delivery_setup.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macos_required"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert not directory.exists()


def test_keychain_failure_does_not_publish_config_or_private_error(tmp_path, monkeypatch):
    inputs(monkeypatch)

    def denied(*args):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(delivery_credentials, "write_keychain", denied)
    directory = tmp_path / "monitor"
    with pytest.raises(RuntimeError, match="^setup_keychain_unavailable; unlock or authorize the existing Keychain manually$"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert not (directory / "config.json").exists()


def test_getpass_fallback_is_rejected_before_echo_or_storage(tmp_path, monkeypatch):
    saved = inputs(monkeypatch)

    def fallback(prompt):
        warnings.warn("Cannot control echo", delivery_setup.getpass.GetPassWarning)
        pytest.fail("must not continue into echoing fallback")

    monkeypatch.setattr(delivery_setup.getpass, "getpass", fallback)
    directory = tmp_path / "monitor"
    with pytest.raises(RuntimeError, match="^setup_terminal_required$"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert not directory.exists() and saved == []


def test_destination_added_during_prompt_is_not_overwritten(tmp_path, monkeypatch):
    directory = tmp_path / "monitor"
    monitor.initialize(directory, [])
    saved = inputs(monkeypatch)
    calls = iter([WEBHOOK, SECRET])

    def prompt(message):
        config = monitor.load_config(directory)
        config["destinations"] = [{"id": "personal", "provider": "native"}]
        ownership.write_json(directory / "config.json", config)
        return next(calls)

    monkeypatch.setattr(delivery_setup.getpass, "getpass", prompt)
    with pytest.raises(RuntimeError, match="setup_destination_exists"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert saved == []
    assert monitor.load_config(directory)["destinations"] == [{"id": "personal", "provider": "native"}]


def test_existing_legacy_config_preserves_native_delivery(tmp_path, monkeypatch):
    directory = tmp_path / "monitor"
    monitor.initialize(directory, [])
    inputs(monkeypatch)
    delivery_setup.configure(directory, "feishu", "personal")
    assert monitor.load_config(directory)["destinations"][0] == {"id": "native", "provider": "native"}


def test_keychain_collision_requests_new_destination_without_config_write(tmp_path, monkeypatch):
    inputs(monkeypatch)

    def duplicate(*args):
        raise RuntimeError("keychain_item_exists")

    monkeypatch.setattr(delivery_credentials, "write_keychain", duplicate)
    directory = tmp_path / "monitor"
    with pytest.raises(RuntimeError, match="choose a new --destination"):
        delivery_setup.configure(directory, "feishu", "personal")
    assert not (directory / "config.json").exists()


def test_imessage_setup_stores_recipient_only_in_keychain(tmp_path, monkeypatch):
    from agent_storage_guard import imessage_delivery
    saved = inputs(monkeypatch, ("synthetic@example.invalid",))
    monkeypatch.setattr(imessage_delivery, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(imessage_delivery.os, "access", lambda *args: True)
    directory = tmp_path / "monitor"
    result = delivery_setup.configure(directory, "imessage", "personal")
    assert saved == [("asg-imessage-personal", json.dumps({"recipient": "synthetic@example.invalid"}))]
    config = monitor.load_config(directory)
    assert config["destinations"] == [{"id": "personal", "provider": "imessage",
                                       "settings_keychain": "asg-imessage-personal"}]
    assert "synthetic@example.invalid" not in (directory / "config.json").read_text()
    assert "synthetic@example.invalid" not in json.dumps(result)
