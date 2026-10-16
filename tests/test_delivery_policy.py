"""Allowed measurements and destination acknowledgements are independent."""
import json
import pytest

from agent_storage_guard import delivery


def test_remote_payload_reconstructs_only_allowed_measurements():
    private = "PRIVATE /Users/example/repository feature-name token=credential"
    alert = {"key": "growth:" + private, "level": "warning", "observed_at": 1000,
             "delta_bytes": 3 * 1024 ** 3, "message": private, "title": private,
             "path": private, "fingerprint": private, "action": private}
    snapshot = {"time": 1000, "capacity": {"time": 1000, "free_bytes": 20 * 1024 ** 3,
                "memory": {"pressure": "critical", "raw": private}},
                "scan": {"time": 1000, "groups": {private: {"complete": False}}},
                "config": {"roots": [private]}, "exception": private}
    result = delivery.remote_payload(alert, snapshot, {"operator_label": "CI monitor"})
    assert result == {"label": "CI monitor", "severity": "warning", "measured_at": 1000,
                      "free_gib": 20.0, "growth_gib": 3.0, "memory_pressure": "critical",
                      "coverage": "partial", "next_action":
                      "Review the private local report and all material storage areas; preserve active work before moving reviewed outputs to another volume."}
    assert "PRIVATE" not in json.dumps(result)


def test_keychain_settings_are_resolved_without_persisting_credentials(monkeypatch):
    from agent_storage_guard import delivery_credentials
    spec = {"id": "operations", "provider": "feishu", "settings_keychain": "asg-feishu-test"}
    config = {"destinations": [spec]}
    delivery.validate_config(config)
    called = []
    def lookup(service):
        called.append(service)
        return '{"webhook_url":"https://open.feishu.cn/open-apis/bot/v2/hook/synthetic"}'
    monkeypatch.setattr(delivery_credentials, "read_keychain", lookup)
    assert delivery.settings(spec)["webhook_url"].endswith("/synthetic")
    assert called == ["asg-feishu-test"]
    assert "synthetic" not in json.dumps(config)


@pytest.mark.parametrize("spec", [
    {"id": "operations", "provider": []},
    {"id": "operations", "provider": "feishu", "settings_env": "invalid lowercase"},
    {"id": "operations", "provider": "feishu", "webhook_url": "PRIVATE"},
    {"id": "operations", "provider": "feishu", "settings_keychain": "PRIVATE\n"},
    {"id": "operations", "provider": "feishu", "settings_env": "ASG_TEST", "settings_keychain": "asg-test"},
])
def test_malformed_destination_configuration_is_rejected_without_echoing_values(spec):
    with pytest.raises(ValueError) as result:
        delivery.validate_config({"destinations": [spec]})
    assert "PRIVATE" not in str(result.value)


def test_imessage_health_has_actionable_automation_gate_without_account_data(monkeypatch):
    from agent_storage_guard import imessage_delivery
    monkeypatch.setattr(imessage_delivery.sys, "platform", "darwin")
    monkeypatch.setattr(imessage_delivery.os, "access", lambda *args: True)
    spec = {"id": "personal", "provider": "imessage", "settings_env": "ASG_SYNTHETIC_IMESSAGE"}
    monkeypatch.setenv(spec["settings_env"], '{"recipient":"private-fixture@example.invalid"}')
    ledger = {"personal": {"binding": ["imessage", spec["settings_env"], None], "delivered": {},
        "health": {"acceptance": "failed", "code": "imessage_permission_denied"}}}
    result, code = delivery.check_configuration({"destinations": [spec]}, ledger)
    assert code != 0
    assert "Automation" in result["destinations"][0]["gate"]
    assert "Messages" in result["destinations"][0]["gate"]
    assert "private-fixture" not in json.dumps(result)


def test_readable_remote_text_preserves_unknown_measurements_and_privacy():
    alert = {"level":"warning", "key":"private/path", "message":"PRIVATE_FIXTURE", "observed_at":0}
    payload = delivery.remote_payload(alert, {"time":1000}, {})
    text = delivery.remote_text(payload)
    assert text.startswith("Storage monitor — WARNING")
    assert "Free space: Unavailable" in text
    assert "Storage growth: Unavailable" in text
    assert "Memory pressure: Unknown" in text
    assert "Scan coverage: Unknown" in text
    assert "1970-01-01 00:00 UTC" in text
    assert "PRIVATE_FIXTURE" not in text and "private/path" not in text
