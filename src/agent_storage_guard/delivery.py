"""Explicit destinations; private credentials never become monitor evidence."""
from __future__ import annotations

import json
import math
import os
import plistlib
from pathlib import Path
import re
import shutil
import sys
import subprocess
import tempfile
import time

_OPEN = "/usr/bin/open"
PROVIDERS = {"native", "feishu", "imessage"}
_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_GATES = {
    "timeout": "Delivery timed out; receipt is uncertain. For iMessage, open Messages in the logged-in desktop session, finish any Automation approval, then deliberately rerun test-send --installed. Avoid repeated retries until checking for receipt.",
    "imessage_platform_unavailable": "Use a macOS user session with Messages and osascript available.",
    "imessage_permission_denied": "In System Settings > Privacy & Security > Automation, allow the installed Agent Storage Guard sender to control Messages; rerun test-send --installed.",
    "imessage_service_unavailable": "Sign in to Messages with an iMessage account in the monitored Mac's user session; verify a blue-bubble conversation manually.",
    "imessage_failed": "Verify the recipient can receive iMessage in Messages; rerun the installed test and confirm receipt independently.",
    "keychain_unavailable": "Unlock the selected Keychain and authorize the installed runtime for this item; rerun delivery-check.",
}


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
            result["gate"] = _GATES.get(previous["code"], "Review provider authentication and policy; deliberately test-send after correction.")
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
                          gate=_GATES.get(error.code, "Supply valid provider settings using the configured credential reference; rerun delivery-check."))
        results.append(result)
    return {"destinations": results}, int(any(r["configuration"] == "blocked" or r["acceptance"] == "failed" for r in results))


def _number(value: object) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def _disk_change(capacity: dict, history: list[dict], fresh: bool) -> tuple[float | None, float | None]:
    identity = ("volume_device", "volume_path", "total_bytes")
    if not fresh or any(capacity.get(k) is None for k in identity) or _number(capacity.get("used_bytes")) is None:
        return None, None
    candidates = [sample for sample in history
                  if all(sample.get(k) == capacity[k] for k in identity)
                  and _number(sample.get("time")) is not None
                  and 0 < capacity["time"] - sample["time"] <= 86400
                  and _number(sample.get("used_bytes")) is not None]
    if not candidates:
        return None, None
    baseline = min(candidates, key=lambda sample: sample["time"])
    return round((capacity["used_bytes"] - baseline["used_bytes"]) / 1024 ** 3, 1), capacity["time"] - baseline["time"]


def _coverage_counts(scan: dict, config: dict) -> dict:
    groups = scan.get("groups", {})
    roots = [item["path"] for item in config.get("roots", [])]
    if not roots:
        roots = [key for key in groups if not any(str(parent) in groups for parent in Path(key).parents)]
    counts = dict(total=len(roots), complete=0, permission_denied=0, timed_out=0, other=0)
    for root in roots:
        group = groups.get(root, {})
        if group.get("complete"):
            counts["complete"] += 1
            continue
        permission = group.get("error_kind") == "permission_denied"
        legacy_timeout = "timed_out" not in group and group.get("error", "").startswith(("Folder scan exceeded deadline", "Total folder scan deadline reached"))
        timed_out = bool(group.get("timed_out")) or group.get("error_kind") == "deadline" or legacy_timeout
        counts["permission_denied"] += int(permission)
        counts["timed_out"] += int(timed_out)
        counts["other"] += int(not permission and not timed_out)
    return counts


_ACTIONS = {
    "memory": "Open Activity Monitor > Memory, sort by Memory and inspect the largest processes; save work before quitting unused apps normally.",
    "reserve": "Pause large builds; review recent growers in the private local report and preserve unfinished work before moving reviewed finished outputs.",
    "growth": "Open the private local report's recent growers, compare the time window, preserve source and unfinished work, and review finished outputs before moving them.",
    "coverage": "Open the private local report to review scan coverage; preserve previous baselines until incomplete locations can be measured.",
}


def remote_payload(alert: dict, snapshot: dict, config: dict, *, synthetic: bool = False) -> dict:
    """Construct an allowlist, never reuse private alert prose, keys or paths."""
    capacity, scan = snapshot.get("capacity", {}), snapshot.get("scan", {})
    now = snapshot["time"]
    fresh = 0 <= now - capacity.get("time", -math.inf) <= config.get("tick_interval_seconds", 300) * 2.5
    key = alert.get("key", "")
    reason = "growth" if key.startswith("growth:") else key if key in _ACTIONS else "coverage"
    counts = _coverage_counts(scan, config)
    coverage = "unknown"
    if scan:
        coverage = "complete" if counts["total"] and counts["complete"] == counts["total"] else "partial"
        if not 0 <= now - scan.get("time", -math.inf) <= config.get("scan_interval_seconds", 3600) * 2.5:
            coverage = "stale"
    free = _number(capacity.get("free_bytes")) if fresh else None
    growth = _number(alert.get("delta_bytes"))
    pressure = capacity.get("memory", {}).get("pressure") if fresh else None
    change, elapsed = _disk_change(capacity, snapshot.get("capacity_history", []), fresh)
    action = _ACTIONS[reason]
    if counts["permission_denied"]:
        action += "\nScan access: review access permissions for Agent Storage Guard in System Settings > Privacy & Security > Full Disk Access, then rerun the scan."
    if counts["timed_out"]:
        action += "\nScan timeout: rerun the scan; if deadlines persist, narrow configured roots or review scan budgets."
    if synthetic:
        action = "Synthetic test only; no host measurement was taken. Confirm this test message arrived; review the private report for real conditions."
    return {"label": config.get("operator_label", "Storage monitor"), "reason": reason,
            "severity": alert["level"] if alert.get("level") in {"info", "warning", "urgent"} else "info",
            "measured_at": _number(alert.get("observed_at")),
            "free_gib": round(free / 1024 ** 3, 1) if free is not None else None,
            "growth_gib": round(growth / 1024 ** 3, 1) if growth is not None else None,
            "growth_elapsed_seconds": _number(alert.get("elapsed_seconds")),
            "disk_change_gib": change, "disk_change_seconds": elapsed,
            "memory_pressure": pressure if pressure in {"normal", "warning", "critical"} else "unknown",
            "coverage": coverage, "coverage_counts": counts, "next_action": action}


def _period(seconds: float | None) -> str:
    return f" over {seconds / 3600:.1f} hours" if seconds is not None and seconds > 0 else " (measurement period unavailable)"


def remote_text(payload: dict, *, synthetic: bool = False) -> str:
    """Present only the reconstructed allowlist, never the private alert itself."""
    heading = "TEST ALERT" if synthetic else payload["severity"].upper()
    reason = {"memory":"Memory pressure", "reserve":"Low free space", "growth":"Folder growth", "coverage":"Incomplete scan"}[payload["reason"]]
    lines = [f"{payload['label']} — {heading}", "Reason: " + reason]
    if synthetic:
        lines.append("Sample values • no host measurement")
    measured = payload["measured_at"]
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(measured)) if measured is not None else "Unavailable"
    except (OverflowError, ValueError, OSError):
        stamp = "Unavailable"
    free, growth, change = payload["free_gib"], payload["growth_gib"], payload["disk_change_gib"]
    counts = payload["coverage_counts"]
    lines.extend(["", f"Free space: {free:.1f} GiB" if free is not None else "Free space: Unavailable",
                  f"Disk use change: {change:+.1f} GiB" + _period(payload["disk_change_seconds"]) if change is not None else
                  "Disk use change: Unavailable; waiting for a second comparable measurement (up to 24 hours).",
                  *([f"Storage growth: +{growth:.1f} GiB" + _period(payload["growth_elapsed_seconds"]) if growth is not None else "Storage growth: Unavailable"] if payload["reason"] == "growth" or synthetic else []),
                  f"Memory pressure: {payload['memory_pressure'].capitalize()}",
                  f"Scan coverage: {payload['coverage'].capitalize()} — {counts['complete']}/{counts['total']} roots complete; "
                  f"{counts['permission_denied']} permission gaps, {counts['timed_out']} deadline gaps, {counts['other']} other/unknown gaps",
                  f"Measured: {stamp}", "", "Next step: " + payload["next_action"]])
    return "\n".join(lines)


def _send(spec: dict, alert: dict, snapshot: dict, config: dict, app: str | None, state: Path, *, synthetic: bool = False) -> None:
    from . import notifications
    from .delivery_transports import DeliveryError, send
    if spec["provider"] == "native":
        try:
            labeled = {**alert, "title": f"{config.get('operator_label', 'Storage monitor')} — {alert['title']}"}
            notifications.notify(labeled, app=Path(app) if app else None, report_path=state / "report.html")
        except (RuntimeError, OSError, subprocess.SubprocessError):
            raise DeliveryError("native_unavailable", True) from None
    else:
        send(spec["provider"], settings(spec), remote_text(remote_payload(alert, snapshot, config, synthetic=synthetic), synthetic=synthetic))


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
             "delta_bytes": 3 * 1024 ** 3, "elapsed_seconds": 7200,
             "title": "Storage monitor test", "message": "Synthetic test; review the private local report."}
    capacity = {"time": now, "free_bytes": 8 * 1024 ** 3, "used_bytes": 92 * 1024 ** 3,
                "total_bytes": 100 * 1024 ** 3, "volume_device": 0, "volume_path": "/synthetic-test",
                "memory": {"pressure": "critical"}}
    snapshot = {"time": now, "capacity": capacity,
                "capacity_history": [{**capacity, "time": now - 7200, "used_bytes": 89 * 1024 ** 3}],
                "scan": {"time": now, "groups": {"synthetic": {"complete": False}}}}
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


def installed_test(app: Path, destination: str, expected_state: Path) -> tuple[dict, int]:
    """Deliberate send under the same responsible app as scheduled jobs."""
    from .ownership import no_symlinks
    no_symlinks(app)
    expected = str(expected_state.absolute())
    try:
        manifest = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise RuntimeError("installed_helper_invalid; rerun monitor install for this state") from None
    if not isinstance(manifest, dict) or manifest.get("ASGMonitorState") != expected:
        raise RuntimeError("installed_helper_state_mismatch; rerun monitor install for this state")
    try:
        # A raw child can inherit its caller's TCC responsibility (e.g. uv),
        # despite being a signed app. Launch Services starts the app independently.
        # open does not propagate the app's exit status; validate its own receipt.
        with tempfile.TemporaryDirectory(prefix="asg-installed-test-") as temporary:
            stdout, stderr = (Path(temporary) / name for name in ("stdout", "stderr"))
            for path in (stdout, stderr):
                path.touch(mode=0o600)
            result = subprocess.run([_OPEN, "-n", "-W", "-g", "-a", str(app),
                                     "--stdout", str(stdout), "--stderr", str(stderr),
                                     "--args", "--test-send", destination, expected],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError("Launch Services failed")
            receipt = json.loads(stdout.read_text())
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
        raise RuntimeError("installed_helper_failed; check the installed identity and retry after approving required access") from None
    if (not isinstance(receipt, dict) or receipt.get("destination") != destination
            or receipt.get("synthetic") is not True or receipt.get("receipt") != "not_observed"
            or receipt.get("acceptance") not in ("accepted", "failed")):
        raise RuntimeError("installed_helper_failed; no valid acceptance receipt")
    # Do not forward arbitrary helper stdout, stderr, raw errors or extra fields.
    safe = {"destination": destination, "synthetic": True,
            "acceptance": receipt["acceptance"], "receipt": "not_observed",
            "next_action": "Confirm receipt independently on the recipient device."}
    if receipt["acceptance"] == "failed":
        error = receipt.get("code")
        safe["code"] = error if isinstance(error, str) and error in _GATES else "delivery_failed"
        safe["next_action"] = _GATES.get(safe["code"], "Run monitor delivery-check for redacted provider details; correct the gate before retrying.")
    return safe, int(receipt["acceptance"] != "accepted")
