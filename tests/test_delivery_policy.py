"""Allowed measurements and destination acknowledgements are independent."""
import json
import pytest

from agent_storage_guard import delivery


def test_native_alert_titles_identify_the_host_without_mutating_evidence(monkeypatch, tmp_path):
    from agent_storage_guard import notifications
    sent = []
    monkeypatch.setattr(notifications, "notify", lambda alert, **kwargs: sent.append(alert))
    alert = {"key": "reserve", "title": "Low disk space", "message": "Preserve active work."}
    for label in ("Laptop", "Tokyo mini"):
        delivery._send({"provider": "native"}, alert, {}, {"operator_label": label}, None, tmp_path)
    assert [item["title"] for item in sent] == ["Laptop — Low disk space", "Tokyo mini — Low disk space"]
    assert all(item["key"] == "reserve" and item["message"] == alert["message"] for item in sent)
    assert alert["title"] == "Low disk space"


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
    assert result == {"label": "CI monitor", "reason":"growth", "severity": "warning", "measured_at": 1000,
                      "free_gib": 20.0, "growth_gib": 3.0, "growth_elapsed_seconds":None,
                      "disk_change_gib":None,"disk_change_seconds":None,"memory_pressure": "critical",
                      "coverage": "partial", "coverage_counts":{"total":1,"complete":0,"permission_denied":0,"timed_out":0,"other":1},
                      "next_action": "Open the private local report's recent growers, compare the time window, preserve source and unfinished work, and review finished outputs before moving them."}
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
    assert "waiting for a second comparable measurement" in text
    assert "Memory pressure: Unknown" in text
    assert "Scan coverage: Unknown" in text
    assert "1970-01-01 00:00 UTC" in text
    assert "PRIVATE_FIXTURE" not in text and "private/path" not in text


def capacity(time, used, **extra):
    return {"time": time, "used_bytes": used * 1024 ** 3, "free_bytes": (100-used) * 1024 ** 3,
            "total_bytes": 100 * 1024 ** 3, "volume_device": 42, "volume_path": "/synthetic", **extra}


@pytest.mark.parametrize("used,expected", [(30, 10.0), (10, -10.0)])
def test_memory_alert_explains_actual_signed_disk_change_and_period(used, expected):
    current = capacity(100000, used, memory={"pressure":"critical"})
    history = [capacity(100, 1), capacity(90000,20), capacity(99000,25),
               capacity(80000,99,volume_device=43), capacity(85000,99,total_bytes=200*1024**3)]
    payload = delivery.remote_payload({"key":"memory","level":"urgent","observed_at":100000},
        {"time":100000,"capacity":current,"capacity_history":history}, {})
    assert payload["reason"] == "memory"
    assert payload["disk_change_gib"] == expected
    assert payload["disk_change_seconds"] == 10000
    text = delivery.remote_text(payload)
    assert f"Disk use change: {expected:+.1f} GiB over 2.8 hours" in text
    assert "sort by Memory" in text and "save work" in text


def test_disk_change_requires_fresh_comparable_measurements():
    alert = {"key":"reserve","level":"urgent","observed_at":100000}
    for history in ([], [capacity(100000,20)], [capacity(90000,20,volume_device=7)],
                    [capacity(90000,20,volume_path="/other")], [capacity(100,20)]):
        result = delivery.remote_payload(alert,{"time":100000,"capacity":capacity(100000,30),"capacity_history":history},{})
        assert result["disk_change_gib"] is None
        assert "waiting for a second comparable measurement" in delivery.remote_text(result)
    stale = delivery.remote_payload(alert,{"time":102000,"capacity":capacity(100000,30),"capacity_history":[capacity(90000,20)]},{})
    assert stale["disk_change_gib"] is None


def test_partial_coverage_counts_only_configured_roots_and_forwards_no_paths():
    groups = {"/private-root": {"complete":False,"error_kind":"permission_denied","error":"PRIVATE"},
              "/private-root/child":{"complete":False,"error_kind":"permission_denied"},
              "/slow-root":{"complete":False,"timed_out":True},
              "/good-root":{"complete":True}}
    config={"roots":[{"path":p} for p in ("/private-root","/slow-root","/good-root","/missing-root")]}
    payload=delivery.remote_payload({"key":"coverage","level":"info"},{"time":1000,"scan":{"time":1000,"groups":groups}},config)
    assert payload["coverage_counts"] == {"total":4,"complete":1,"permission_denied":1,"timed_out":1,"other":1}
    text=delivery.remote_text(payload)
    assert "1/4 roots complete" in text and "permission" in text and "deadline" in text
    assert "/private-root" not in text and "PRIVATE" not in text
    assert "access" in payload["next_action"] and "budget" in payload["next_action"]


def test_coverage_without_configured_roots_ignores_nested_groups():
    payload=delivery.remote_payload({"key":"coverage","level":"info"},{"time":1000,"scan":{"time":1000,"groups":{
        "/root":{"complete":False,"timed_out":True},"/root/child":{"complete":True}}}}, {})
    assert payload["coverage_counts"]["total"] == 1


def test_growth_keeps_alert_delta_and_explicit_period():
    payload=delivery.remote_payload({"key":"growth:/PRIVATE","level":"warning","delta_bytes":3*1024**3,
        "elapsed_seconds":7200},{"time":100000,"capacity":capacity(100000,30),"capacity_history":[capacity(90000,20)]},{})
    assert payload["growth_gib"] == 3.0
    assert payload["growth_elapsed_seconds"] == 7200
    assert "Storage growth: +3.0 GiB over 2.0 hours" in delivery.remote_text(payload)
    assert "compare" in payload["next_action"] and "source" in payload["next_action"]


def test_legacy_scan_retains_both_deadline_and_permission_summary_without_raw_errors():
    private="PRIVATE /Users/example/private-folder"
    snapshot={"time":1000,"scan":{"time":1000,"groups":{"/synthetic/root":{"complete":False,"error_kind":"permission_denied","error":"Folder scan exceeded deadline; Permission denied: "+private}}}}
    payload=delivery.remote_payload({"key":"memory","level":"warning"},snapshot,{})
    assert payload["coverage_counts"]["permission_denied"]==1
    assert payload["coverage_counts"]["timed_out"]==1
    assert private not in json.dumps(payload)
