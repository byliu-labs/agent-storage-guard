"""Explicit destinations; private credentials never become monitor evidence."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import subprocess
import time

PROVIDERS = {"native", "feishu"}
_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")


def destinations(config: dict) -> list[dict]:
    return config.get("destinations", [{"id": "native", "provider": "native"}])


def validate_config(config: dict) -> None:
    label = config.get("operator_label", "Storage monitor")
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}", label):
        raise ValueError("operator_label requires 1-40 plain characters; use a non-identifying label")
    specs = destinations(config)
    if not isinstance(specs, list) or len(specs) > 8:
        raise ValueError("destinations must be a list of at most eight entries")
    identifiers = set()
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not _NAME.fullmatch(spec["id"]):
            raise ValueError("each destination requires a plain lowercase id")
        if spec["id"] in identifiers:
            raise ValueError("destination ids must be unique")
        identifiers.add(spec["id"])
        if not isinstance(spec.get("provider"), str) or spec["provider"] not in PROVIDERS:
            raise ValueError("unsupported delivery provider")
        source = "settings_keychain" if "settings_keychain" in spec else "settings_env"
        expected = {"id", "provider"} if spec["provider"] == "native" else {"id", "provider", source}
        if spec.keys() != expected:
            raise ValueError("destination requires id, provider and exactly one environment or Keychain reference")
        if spec["provider"] != "native":
            pattern = _ENV if source == "settings_env" else _NAME
            if not isinstance(spec[source], str) or not pattern.fullmatch(spec[source]):
                raise ValueError("invalid credential reference; use an uppercase env name or lowercase Keychain service")


def settings(spec: dict) -> dict:
    from .delivery_transports import DeliveryError, check
    if "settings_keychain" in spec:
        from .delivery_credentials import read_keychain
        try:
            raw = read_keychain(spec["settings_keychain"])
        except RuntimeError:
            raise DeliveryError("keychain_unavailable") from None
    else:
        raw = os.environ.get(spec["settings_env"], "")
    if not raw:
        raise DeliveryError("settings_missing")
    if len(raw) > 16384:
        raise DeliveryError("settings_invalid")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise DeliveryError("settings_invalid") from None
    if not isinstance(value, dict):
        raise DeliveryError("settings_invalid")
    check(spec["provider"], value)
    return value


def check_configuration(config: dict, health: dict) -> tuple[dict, int]:
    from .delivery_transports import DeliveryError
    results = []
    for spec in destinations(config):
        entry = _entry(health, spec, {})
        previous = entry.get("health", {})
        result = {"destination": spec["id"], "provider": spec["provider"],
                  "configuration": "ready", "acceptance": previous.get("acceptance", "not_tested"),
                  "receipt": "not_observed", "gate": "Run explicit test-send; independently confirm receipt."}
        if "code" in previous:
            result["last_error"] = previous["code"]
            result["gate"] = "Review provider authentication and policy; deliberately test-send after correction."
        if "attempted_at" in previous:
            result["attempted_at"] = previous["attempted_at"]
        if entry.get("retry_at"):
            result["retry_at"] = entry["retry_at"]
        try:
            if spec["provider"] != "native":
                settings(spec)
            elif sys.platform != "darwin" and not shutil.which("notify-send"):
                raise DeliveryError("native_unavailable")
        except DeliveryError as error:
            result.update(configuration="blocked", code=error.code,
                          gate="Supply valid provider settings using the configured credential reference; unlock or authorize Keychain manually if needed; rerun delivery-check.")
        results.append(result)
    return {"destinations": results}, int(any(r["configuration"] == "blocked" or r["acceptance"] == "failed" for r in results))


def _number(value: object) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def remote_payload(alert: dict, snapshot: dict, config: dict, *, synthetic: bool = False) -> dict:
    """Construct an allowlist, never reuse private alert prose, keys or paths."""
    capacity, scan = snapshot.get("capacity", {}), snapshot.get("scan", {})
    now = snapshot["time"]
    capacity_fresh = 0 <= now - capacity.get("time", -math.inf) <= config.get("tick_interval_seconds", 300) * 2.5
    observed = _number(alert.get("observed_at"))
    groups = scan.get("groups", {})
    coverage = "unknown"
    if scan:
        coverage = "complete" if groups and all(g.get("complete") for g in groups.values()) else "partial"
        if not 0 <= now - scan.get("time", -math.inf) <= config.get("scan_interval_seconds", 3600) * 2.5:
            coverage = "stale"
    free = _number(capacity.get("free_bytes")) if capacity_fresh else None
    growth = _number(alert.get("delta_bytes"))
    pressure = capacity.get("memory", {}).get("pressure") if capacity_fresh else None
    action = ("Review the private local report and all material storage areas; preserve active work "
              "before moving reviewed outputs to another volume.")
    if alert.get("key") == "memory":
        action = "Save active work; review Activity Monitor Memory on the monitored Mac and quit unused apps normally."
    if synthetic:
        action = "Synthetic test only; no host measurement was taken. Confirm receipt in this test group; review the private report for real conditions."
    return {"label": config.get("operator_label", "Storage monitor"),
            "severity": alert["level"] if alert.get("level") in {"info", "warning", "urgent"} else "info",
            "measured_at": observed, "free_gib": round(free / 1024 ** 3, 1) if free is not None else None,
            "growth_gib": round(growth / 1024 ** 3, 1) if growth is not None else None,
            "memory_pressure": pressure if pressure in {"normal", "warning", "critical"} else "unknown",
            "coverage": coverage, "next_action": action}


def _send(spec: dict, alert: dict, snapshot: dict, config: dict, app: str | None, state: Path, *, synthetic: bool = False) -> None:
    from . import notifications
    from .delivery_transports import DeliveryError, send
    if spec["provider"] == "native":
        try:
            notifications.notify(alert, app=Path(app) if app else None, report_path=state / "report.html")
        except (RuntimeError, OSError, subprocess.SubprocessError):
            raise DeliveryError("native_unavailable", True) from None
    else:
        send(spec["provider"], settings(spec), json.dumps(remote_payload(alert, snapshot, config, synthetic=synthetic), allow_nan=False))


def _failed(entry: dict, error, now: float, level: str) -> None:
    attempts = min(entry.get("attempts", 0) + 1, 8)
    delay = max(30 * 2 ** (attempts - 1), error.retry_after) if error.retryable else 3600
    entry.update(attempts=attempts, retry_at=now + min(3600, delay), retry_level=level,
                 health={"acceptance": "failed", "code": error.code, "attempted_at": now,
                         "receipt": "not_observed"})


def _entry(ledger: dict, spec: dict, legacy: dict) -> dict:
    binding = [spec["provider"], spec.get("settings_env"), spec.get("settings_keychain")]
    entry = ledger.get(spec["id"], {})
    if entry.get("binding") != binding:
        entry = {"binding": binding, "delivered": legacy.get("delivered", {})
                 if spec == {"id": "native", "provider": "native"} else {}}
    return entry


def dispatch(store, config: dict, active: list[dict], legacy: dict, snapshot: dict, now: float) -> bool:
    """Caller holds policy.lock. Commit each acceptance before trying another destination."""
    from . import alerts
    from .delivery_transports import DeliveryError
    previous = store.get_value("delivery_state", {})
    ledger = {spec["id"]: _entry(previous, spec, legacy) for spec in destinations(config)}
    success = True
    # Expired measurements are not recovery. The policy retains stale incidents
    # until a new observation clears them; preserve their delivery acknowledgements.
    ongoing = legacy.get("active", {a["key"]: a for a in active})
    for spec in destinations(config):
        entry = ledger[spec["id"]]
        entry["delivered"] = {k: v for k, v in entry["delivered"].items() if k in ongoing}
        candidates = alerts.delivery_candidates(active, entry, now, config)
        for alert in candidates:
            if now < entry.get("retry_at", 0) and alerts.RANK[alert["level"]] <= alerts.RANK.get(entry.get("retry_level"), 0):
                success = False
                break
            try:
                _send(spec, alert, snapshot, config, store.get_value("notification_app"), store.state)
            except DeliveryError as error:
                _failed(entry, error, now, alert["level"])
                success = False
                break
            entry.update(delivered=alerts.mark_delivered(entry, alert, now)["delivered"], attempts=0,
                         retry_at=0, health={"acceptance": "accepted", "attempted_at": now,
                                            "receipt": "not_observed"})
            store.set_value("delivery_state", ledger)
        store.set_value("delivery_state", ledger)
    return success


def test_send(store, config: dict, destination: str) -> tuple[dict, int]:
    """Deliberate synthetic send; never acknowledge a real policy alert."""
    from .delivery_transports import DeliveryError
    spec = next(s for s in destinations(config) if s["id"] == destination)
    now = time.time()
    alert = {"key": "reserve", "level": "urgent", "observed_at": now,
             "delta_bytes": 3 * 1024 ** 3,
             "title": "Storage monitor test", "message": "Synthetic test; review the private local report."}
    snapshot = {"time": now, "capacity": {"time": now, "free_bytes": 8 * 1024 ** 3,
                "memory": {"pressure": "critical"}}, "scan": {"time": now, "groups": {"synthetic": {"complete": False}}}}
    ledger = store.get_value("delivery_state", {})
    legacy = store.get_value("alert_policy", {})
    entry = _entry(ledger, spec, legacy)
    code = 0
    try:
        _send(spec, alert, snapshot, config, store.get_value("notification_app"), store.state, synthetic=True)
        entry["health"] = {"acceptance": "accepted", "attempted_at": now, "receipt": "not_observed"}
    except DeliveryError as error:
        entry["health"] = {"acceptance": "failed", "code": error.code, "attempted_at": now, "receipt": "not_observed"}
        code = 1
    # Keep state bounded even after an operator replaces destination configuration.
    ledger = {s["id"]: _entry(ledger, s, legacy) for s in destinations(config)}
    ledger[destination] = entry
    store.set_value("delivery_state", ledger)
    return {"destination": destination, "synthetic": True, **entry["health"]}, code
