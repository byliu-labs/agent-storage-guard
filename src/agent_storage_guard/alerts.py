"""Evidence, next action and delivery policy, independent of persistence/senders."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

GIB = 1024 ** 3
RANK = {"info": 0, "warning": 1, "urgent": 2}


def _alert(key: str, level: str, title: str, evidence: str, action: str,
           now: float, lifetime: float, fingerprint: str = "", **details: object) -> dict:
    return {"key": key, "level": level, "title": title, "message": f"{evidence} {action}",
            "evidence": evidence, "action": action, "observed_at": now,
            "valid_until": now + lifetime, "fingerprint": fingerprint or level, **details}


def _consecutive(current: dict, previous: dict | None, interval: float) -> bool:
    return bool(previous and interval / 2 <= current["time"] - previous["time"] <= interval * 2.5)


def _reserve(current: dict, previous: dict | None, active: dict, config: dict, now: float) -> None:
    reserve = config.get("warn_free_bytes", 50 * GIB)
    urgent = config.get("urgent_free_bytes", 25 * GIB)
    free = current["free_bytes"]
    two = _consecutive(current, previous, config.get("tick_interval_seconds", 300))
    if free >= reserve + config.get("recovery_margin_bytes", 2 * GIB):
        if two and previous["free_bytes"] >= reserve + config.get("recovery_margin_bytes", 2 * GIB):
            active.pop("reserve", None)
        return
    if free >= reserve and "reserve" not in active:
        return
    if free < urgent or (two and previous["free_bytes"] < reserve) or "reserve" in active:
        level = "urgent" if free < urgent else "warning"
        title = f"Free space is below your {reserve / GIB:.1f} GiB reserve"
        evidence = f"{free / GIB:.1f} GiB free; {max(0, reserve - free) / GIB:.1f} GiB short of your reserve."
        if free >= reserve:
            title = "Disk reserve recovery is being confirmed"
            evidence = f"{free / GIB:.1f} GiB free; waiting for two checks above the recovery margin."
        action = "Open the report; review recent growers and move finished work to another disk."
        if level == "urgent":
            title = f"Low disk space: {free / GIB:.1f} GiB left"
            action = "Pause large builds; open the report and move finished work to another disk."
        active["reserve"] = _alert("reserve", level, title,
            evidence,
            action, now, config.get("tick_interval_seconds", 300) * 2.5)


def _memory(current: dict, previous: dict | None, active: dict, config: dict, now: float) -> None:
    memory = current.get("memory", {})
    pressure = memory.get("pressure", "unknown")
    old = (previous or {}).get("memory", {})
    two = (_consecutive(current, previous, config.get("tick_interval_seconds", 300))
           and memory.get("boot_time") is not None and memory.get("boot_time") == old.get("boot_time"))
    if pressure == "normal":
        if two and old.get("pressure") == "normal":
            active.pop("memory", None)
        return
    if pressure == "unknown" or not memory.get("supported"):
        return
    if pressure != "critical" and not (two and old.get("pressure") in {"warning", "critical"}):
        return
    swap = memory.get("swap_used_bytes")
    evidence = f"Memory pressure is {pressure}."
    if swap is not None:
        evidence += f" Swap uses {swap / GIB:.1f} GiB of disk."
        growth = swap - old.get("swap_used_bytes", swap)
        if two and growth >= config.get("swap_growth_bytes", GIB / 8):
            evidence += f" Up {growth / GIB:.1f} GiB since the previous check."
    action = "Save work; open Activity Monitor > Memory and quit unused apps normally. Restart only if pressure persists."
    active["memory"] = _alert("memory", "urgent" if pressure == "critical" else "warning",
        "Memory pressure: reduce active work", evidence, action, now,
        config.get("tick_interval_seconds", 300) * 2.5)


def _folder_name(path: str) -> str:
    name = Path(path).name
    return name if len(name) <= 60 else name[:57] + "…"


def _scan_alerts(scan: dict, config: dict, now: float) -> dict:
    lifetime = config.get("scan_interval_seconds", 3600) * 2.5
    observed = scan.get("time", now)
    if not 0 <= now - observed <= lifetime:
        return {}
    active = {}
    gaps = sorted(key for key, group in scan.get("groups", {}).items() if not group.get("complete"))
    roots = [key for key in gaps if not any(Path(parent) in Path(key).parents for parent in gaps)]
    if roots:
        fingerprint = hashlib.sha256("\n".join(roots).encode()).hexdigest()
        count = len(roots)
        title = f"{count} location{'s' if count != 1 else ''} could not be scanned"
        active["coverage"] = _alert("coverage", "info", title, "Previous complete baselines kept.",
            "Open the report for locations; exclude unneeded folders or review access permissions.",
            observed, lifetime, fingerprint, locations=roots)
    candidates = []
    for path, change in scan.get("changes", {}).items():
        for window, default in [("hour", 2 * GIB), ("day", 5 * GIB)]:
            delta = change.get(window + "_delta_bytes", 0)
            elapsed = change.get(window + "_elapsed_seconds", 0)
            if delta >= config.get("growth_" + window + "_bytes", default) and elapsed > 0:
                candidates.append((path, delta, elapsed, window, change.get("bytes", 0)))
                break
    selected = []
    for candidate in sorted(candidates, key=lambda item: (-len(Path(item[0]).parts), -item[1])):
        path = Path(candidate[0])
        if not any(path in Path(item[0]).parents or Path(item[0]) in path.parents or path == Path(item[0]) for item in selected):
            selected.append(candidate)
    for path, delta, elapsed, window, size in sorted(selected, key=lambda item: -item[1])[:3]:
        key = "growth:" + path
        active[key] = _alert(key, "warning", f"{_folder_name(path)} grew {delta / GIB:.1f} GiB",
            f"Measured over {elapsed / 3600:.1f} hours.",
            "Open the report; keep unfinished work and move disposable outputs to Trash after review.",
            observed, lifetime, path, path=path, delta_bytes=delta, bytes=size, window=window)
    return active


def evaluate(capacity: dict, previous_capacity: dict | None, scan: dict, config: dict,
             policy_state: dict, now: float) -> tuple[list[dict], dict]:
    """Observe each sample once; return active alerts and caller-persisted policy state.

    Delivery is separate: only mark_delivered after a sender acknowledges success.
    Unknown memory evidence never proves recovery. Stale alerts cannot be delivered.
    """
    state = deepcopy(policy_state)
    active = state.setdefault("active", {})
    if capacity and capacity.get("time") != state.get("capacity_time"):
        observed = capacity["time"]
        if 0 <= now - observed <= config.get("tick_interval_seconds", 300) * 2.5:
            _reserve(capacity, previous_capacity, active, config, observed)
            _memory(capacity, previous_capacity, active, config, observed)
        state["capacity_time"] = observed
    if scan and scan.get("time") != state.get("scan_time"):
        for key in list(active):
            if key == "coverage" or key.startswith("growth:"):
                active.pop(key)
        active.update(_scan_alerts(scan, config, now))
        state["scan_time"] = scan.get("time")
    delivered = state.setdefault("delivered", {})
    for key in list(delivered):
        if key not in active:
            delivered.pop(key)
    current_alerts = [item for item in active.values() if now <= item["valid_until"]]
    return sorted(current_alerts, key=lambda item: (-RANK[item["level"]], item["key"])), state


def delivery_candidates(alerts: list[dict], policy_state: dict, now: float, config: dict | None = None) -> list[dict]:
    config = config or {}
    result = []
    for alert in alerts:
        previous = policy_state.get("delivered", {}).get(alert["key"])
        if now > alert["valid_until"]:
            continue
        if previous and RANK[alert["level"]] <= RANK[previous["level"]]:
            if alert["key"] == "coverage":
                if not set(alert.get("locations", [])) - set(previous.get("locations", [])):
                    continue
                result.append(alert)
                continue
            cooldown = config.get("urgent_cooldown_seconds", 3600) if alert["level"] == "urgent" else config.get("alert_cooldown_seconds", 21600)
            if now - previous["time"] < cooldown:
                continue
            if alert["key"].startswith("growth:") and alert.get("bytes", 0) - previous.get("bytes", 0) < config.get("growth_hour_bytes", 2 * GIB):
                continue
        result.append(alert)
    return result[:3]


def mark_delivered(policy_state: dict, alert: dict, now: float) -> dict:
    state = deepcopy(policy_state)
    state.setdefault("delivered", {})[alert["key"]] = {"time": now, "level": alert["level"],
        "fingerprint": alert["fingerprint"], "bytes": alert.get("bytes", 0),
        "locations": alert.get("locations", [])}
    return state
