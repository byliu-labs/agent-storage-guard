"""Real HTTP acceptance with real SQLite state, restarts and deliberate CLI sends."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from agent_storage_guard import delivery, delivery_transports, monitor
from agent_storage_guard.cli import parser
from agent_storage_guard.monitor_store import MonitorStore


@contextmanager
def provider(monkeypatch, statuses):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            status = statuses.pop(0)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b'{"code":0}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = delivery_transports._request
    monkeypatch.setattr(delivery_transports, "_request", lambda url, payload, headers:
        original(f"http://127.0.0.1:{server.server_port}/" + url.rsplit("/", 1)[-1], payload, headers))
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def configured(tmp_path, monkeypatch):
    directory = tmp_path / "state/monitor"
    monitor.initialize(directory, [tmp_path / "workspaces"])
    config = monitor.load_config(directory)
    config.update(operator_label="CI monitor", destinations=[
        {"id": name, "provider": "feishu", "settings_env": "ASG_" + name.upper()}
        for name in ("first", "second")])
    for spec in config["destinations"]:
        monkeypatch.setenv(spec["settings_env"], json.dumps({"webhook_url":
            "https://open.feishu.cn/open-apis/bot/v2/hook/" + spec["id"]}))
    return directory, config


def test_failed_destination_does_not_suppress_other_and_restart_retries_only_failure(tmp_path, monkeypatch):
    directory, config = configured(tmp_path, monkeypatch)
    now = time.time()
    alert = {"key": "reserve", "level": "urgent", "observed_at": now,
             "valid_until": now + 750, "fingerprint": "urgent"}
    snapshot = {"time": now, "capacity": {"time": now, "free_bytes": 1024 ** 3}}
    with provider(monkeypatch, [503, 200, 200]) as requests:
        with MonitorStore(directory) as store:
            assert not delivery.dispatch(store, config, [alert], {}, snapshot, now)
        assert [path for path, _ in requests] == ["/first", "/second"]
        with MonitorStore(directory) as store:
            assert not delivery.dispatch(store, config, [alert], {}, snapshot, now + 1)
            state = store.get_value("delivery_state")
            assert state["first"]["health"]["code"] == "provider_unavailable"
            assert state["first"]["delivered"] == {}
            assert state["second"]["delivered"]["reserve"]["level"] == "urgent"
        assert len(requests) == 2
        with MonitorStore(directory) as store:
            assert delivery.dispatch(store, config, [alert], {}, snapshot, now + 31)
        with MonitorStore(directory) as store:
            assert delivery.dispatch(store, config, [alert], {}, snapshot, now + 32)
        assert [path for path, _ in requests] == ["/first", "/second", "/first"]
        for _, body in requests:
            payload = json.loads(body["content"]["text"])
            assert payload["free_gib"] == 1.0
            assert payload["severity"] == "urgent"
            assert str(tmp_path) not in json.dumps(body)


def test_cli_test_send_is_synthetic_and_does_not_acknowledge_real_alerts(tmp_path, monkeypatch, capsys):
    directory, config = configured(tmp_path, monkeypatch)
    (directory / "config.json").write_text(json.dumps(config))
    arguments = parser().parse_args(["--state", str(directory.parent), "monitor", "test-send", "--destination", "first"])
    with provider(monkeypatch, [200]) as requests:
        assert monitor.dispatch(arguments) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["acceptance"] == "accepted"
        assert result["receipt"] == "not_observed"
        assert result["synthetic"] is True
        payload = json.loads(requests[0][1]["content"]["text"])
        assert payload["free_gib"] == 8.0
        assert payload["growth_gib"] == 3.0
        assert payload["memory_pressure"] == "critical"
        assert payload["coverage"] == "partial"
        assert payload["next_action"].startswith("Synthetic test only; no host measurement was taken.")
        with MonitorStore(directory) as store:
            assert store.get_value("delivery_state")["first"]["delivered"] == {}
            assert not store.latest_capacity()
            assert not store.latest_scan()


def test_monitor_notify_uses_configured_destinations_and_exposes_failure_without_secrets(tmp_path, monkeypatch):
    import pytest
    directory, config = configured(tmp_path, monkeypatch)
    now = time.time()
    with MonitorStore(directory) as store:
        store.record_capacity({"time": now, "free_bytes": 1024 ** 3})
    with provider(monkeypatch, [503, 200]) as requests:
        with pytest.raises(RuntimeError, match="delivery incomplete"):
            monitor.refresh(directory, config, probe=False, send=True)
        assert len(requests) == 2
        assert (directory / "report.html").is_file()
        with MonitorStore(directory) as store:
            health, code = delivery.check_configuration(config, store.get_value("delivery_state"))
        assert code != 0
        assert health["destinations"][0]["last_error"] == "provider_unavailable"
        assert health["destinations"][1]["acceptance"] == "accepted"


def test_native_cooldown_survives_upgrade_and_recovery_rearms_alert(tmp_path, monkeypatch):
    from agent_storage_guard import alerts, notifications
    directory, config = configured(tmp_path, monkeypatch)
    config.pop("destinations")
    now = time.time()
    alert = {"key": "reserve", "level": "urgent", "observed_at": now,
             "valid_until": now + 750, "fingerprint": "urgent"}
    legacy = alerts.mark_delivered({}, alert, now)
    sent = []
    monkeypatch.setattr(notifications, "notify", lambda *args, **kwargs: sent.append(args[0]))
    with MonitorStore(directory) as store:
        assert delivery.dispatch(store, config, [alert], legacy, {}, now + 1)
    assert sent == []
    with MonitorStore(directory) as store:
        assert delivery.dispatch(store, config, [], {}, {}, now + 2)
        assert delivery.dispatch(store, config, [alert], {}, {}, now + 3)
    assert len(sent) == 1


def test_escalation_bypasses_failed_warning_backoff(tmp_path, monkeypatch):
    directory, config = configured(tmp_path, monkeypatch)
    config["destinations"] = config["destinations"][:1]
    now = time.time()
    warning = {"key": "reserve", "level": "warning", "observed_at": now,
               "valid_until": now + 750, "fingerprint": "warning"}
    urgent = {**warning, "level": "urgent", "fingerprint": "urgent"}
    snapshot = {"time": now}
    with provider(monkeypatch, [503, 200]) as requests:
        with MonitorStore(directory) as store:
            assert not delivery.dispatch(store, config, [warning], {}, snapshot, now)
        with MonitorStore(directory) as store:
            assert delivery.dispatch(store, config, [urgent], {}, snapshot, now + 1)
        assert len(requests) == 2


def test_expired_scan_is_not_recovery_and_same_gap_does_not_bypass_cooldown(tmp_path, monkeypatch):
    directory, config = configured(tmp_path, monkeypatch)
    config["destinations"] = config["destinations"][:1]
    config["scan_interval_seconds"] = 40
    clock = [1000]
    monkeypatch.setattr(monitor.time, "time", lambda: clock[0])
    gap = {str(tmp_path / "workspaces"): {"complete": False}}
    with MonitorStore(directory) as store:
        store.record_scan(gap, 100 * 1024 ** 3, now=1000)
    with provider(monkeypatch, [200, 200]) as requests:
        monitor.refresh(directory, config, probe=False, send=True)
        clock[0] = 1150
        monitor.refresh(directory, config, probe=False, send=True)
        clock[0] = 1200
        with MonitorStore(directory) as store:
            store.record_scan(gap, 100 * 1024 ** 3, now=1200)
        monitor.refresh(directory, config, probe=False, send=True)
        assert len(requests) == 1


def test_failed_destination_never_prevents_folder_measurement(tmp_path, monkeypatch):
    import pytest
    directory, config = configured(tmp_path, monkeypatch)
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces/result").write_bytes(b"synthetic" * 100)
    config.update(warn_free_bytes=2 ** 60, urgent_free_bytes=2 ** 59)
    (directory / "config.json").write_text(json.dumps(config))
    for spec in config["destinations"]:
        monkeypatch.delenv(spec["settings_env"])
    arguments = parser().parse_args(["--state", str(directory.parent), "monitor", "scan", "--notify"])
    with pytest.raises(RuntimeError, match="delivery incomplete"):
        monitor.dispatch(arguments)
    with MonitorStore(directory) as store:
        assert store.latest_scan()["groups"][str(tmp_path / "workspaces")]["complete"]


def test_deliberate_test_during_native_upgrade_preserves_existing_cooldown(tmp_path, monkeypatch):
    from agent_storage_guard import alerts, notifications
    directory, config = configured(tmp_path, monkeypatch)
    config.pop("destinations")
    now = time.time()
    alert = {"key": "reserve", "level": "urgent", "observed_at": now,
             "valid_until": now + 750, "fingerprint": "urgent"}
    legacy = alerts.mark_delivered({}, alert, now)
    sent = []
    monkeypatch.setattr(notifications, "notify", lambda *args, **kwargs: sent.append(args[0]))
    with MonitorStore(directory) as store:
        store.set_value("alert_policy", legacy)
        receipt, code = delivery.test_send(store, config, "native")
        assert code == 0 and receipt["synthetic"]
    with MonitorStore(directory) as store:
        assert delivery.dispatch(store, config, [alert], legacy, {}, now + 1)
    assert len(sent) == 1  # Only the deliberate synthetic test, no real-alert repeat.
