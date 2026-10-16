"""Operator workflows never send implicitly or expose credential values."""
import json
import os
import subprocess
import sys


def invoke(state, *arguments, settings=None):
    environment = {**os.environ, "ASG_SYNTHETIC_FEISHU": settings or ""}
    return subprocess.run([sys.executable, "-m", "agent_storage_guard", "--state", str(state),
                           "monitor", *arguments], env=environment, capture_output=True,
                          text=True, timeout=30)


def configure(state, root):
    assert invoke(state, "init", "--root", str(root)).returncode == 0
    path = state / "monitor/config.json"
    config = json.loads(path.read_text())
    config.update(operator_label="CI monitor", destinations=[
        {"id": "operations", "provider": "feishu", "settings_env": "ASG_SYNTHETIC_FEISHU"}])
    path.write_text(json.dumps(config))


def test_check_validates_without_contacting_provider_or_revealing_endpoint(tmp_path):
    state = tmp_path / "state"
    configure(state, tmp_path / "workspaces")
    settings = json.dumps({"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/synthetic-secret",
                           "signing_secret": "synthetic-signing-secret"})
    result = invoke(state, "delivery-check", settings=settings)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["destinations"][0]["configuration"] == "ready"
    assert payload["destinations"][0]["acceptance"] == "not_tested"
    assert payload["destinations"][0]["receipt"] == "not_observed"
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert "synthetic-signing-secret" not in result.stdout + result.stderr


def test_bad_settings_fail_with_actionable_redacted_gate(tmp_path):
    state = tmp_path / "state"
    configure(state, tmp_path / "workspaces")
    for settings in (None, "PRIVATE malformed credential", '{"webhook_url":"PRIVATE endpoint"}'):
        result = invoke(state, "delivery-check", settings=settings)
        assert result.returncode != 0
        assert "PRIVATE" not in result.stdout + result.stderr
        assert "operations" in result.stdout
        assert "gate" in result.stdout


def test_test_send_requires_explicit_configured_destination(tmp_path):
    state = tmp_path / "state"
    configure(state, tmp_path / "workspaces")
    result = invoke(state, "test-send", "--destination", "absent")
    assert result.returncode != 0
    assert "unknown destination" in result.stderr


def test_setup_refuses_noninteractive_input_without_creating_state(tmp_path):
    state = tmp_path / "absent"
    result = invoke(state, "setup", "--provider", "feishu")
    assert result.returncode != 0
    gate = "setup_terminal_required" if sys.platform == "darwin" else "setup_macos_required"
    assert gate in result.stderr
    assert not state.exists()


def test_installed_test_requires_prepared_helper_and_never_falls_back(tmp_path):
    state = tmp_path / "state"
    configure(state, tmp_path / "workspaces")
    result = invoke(state, "test-send", "--destination", "operations", "--installed")
    assert result.returncode != 0
    assert "installed_helper_missing" in result.stderr
