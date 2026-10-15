"""Human-readable evidence and next actions; never a deletion manifest."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

GIB = 1024 ** 3


def timestamp(value: float | None) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "not measured"


def render(payload: dict) -> str:
    capacity, scan = payload.get("capacity", {}), payload.get("scan", {})
    memory, config = capacity.get("memory", {}), payload.get("config", {})
    lines = ["Agent Storage Guard — workstation report", "",
             f"Capacity measured: {timestamp(capacity.get('time'))}"]
    if "free_bytes" in capacity:
        lines.append(f"Disk: {capacity['free_bytes'] / GIB:.1f} GiB free; reserve {config.get('warn_free_bytes', 0) / GIB:.1f} GiB.")
    lines.append(f"Memory pressure: {memory.get('pressure', 'unavailable')}.")
    if "swap_used_bytes" in memory:
        lines.append(f"Swap: {memory['swap_used_bytes'] / GIB:.2f} GiB; uptime {memory.get('uptime_seconds', 0) / 86400:.1f} days.")
        trend = payload.get("memory_trend", {})
        if trend:
            lines.append(f"Swap change: {trend['swap_delta_bytes'] / GIB:+.2f} GiB over {trend['elapsed_seconds'] / 3600:.1f}h (same boot).")
    else:
        lines.append(f"Memory measurement unavailable: {memory.get('error', memory.get('reason', 'not measured'))}.")
    lines.extend(["", "Alerts and next actions"])
    for alert in payload.get("alerts", []):
        lines.extend([f"- {alert['title']}", f"  {alert['message']}"])
        if alert.get("action") and alert["action"] not in alert["message"]:
            lines.append(f"  Next: {alert['action']}")
    stale_capacity = payload.get("time", 0) - capacity.get("time", 0) > config.get("tick_interval_seconds", 300) * 2.5
    if not payload.get("alerts"):
        lines.append("No current assessment: capacity data is missing or stale. Run 'monitor tick'."
                     if not capacity or stale_capacity else "No current disk alert. Large stable swap or uptime alone is not an incident.")
        if memory.get("pressure") in {None, "unknown"}:
            lines.append("Memory pressure could not be assessed; no healthy state is inferred.")
    lines.extend(["", f"Folder scan: {timestamp(scan.get('time'))}",
                  "GiB now | change since baseline | recent day change | folder"])
    if not scan or payload.get("time", 0) - scan.get("time", 0) > config.get("scan_interval_seconds", 3600) * 2.5:
        lines.append("Folder evidence is missing or stale; run 'monitor scan' before making cleanup decisions.")
    changes = scan.get("changes", {})
    items = sorted(changes.items(), key=lambda x: x[1].get("day_delta_bytes", x[1].get("baseline_delta_bytes", 0)), reverse=True)
    for name, item in items[:30]:
        day = f"{item['day_delta_bytes'] / GIB:+.2f}" if "day_delta_bytes" in item else "unknown"
        elapsed = f" ({item['day_elapsed_seconds'] / 3600:.1f}h)" if "day_elapsed_seconds" in item else ""
        lines.append(f"{item['bytes'] / GIB:7.2f} | {item.get('baseline_delta_bytes', 0) / GIB:+.2f} | {day}{elapsed} | {name}")
    gaps = [(name, group) for name, group in scan.get("groups", {}).items() if not group.get("complete")]
    if gaps:
        lines.extend(["", "Scan coverage gaps — previous baselines preserved; missing bytes are unknown."])
        lines.extend(f"- {name}: {group.get('error', 'incomplete measurement')}" for name, group in gaps[:32])
        lines.append("Next: choose narrower readable roots or review macOS Privacy access if you want these locations included.")
    inventory = payload.get("worktrees", {})
    if inventory:
        lines.extend(["", "Git worktrees (read-only inventory; not cleanup approval)"])
        rows = inventory.get("worktrees", [])
        stale = [item for item in rows if item.get("prunable")]
        current = [item for item in rows if not item.get("prunable")]
        lines.append(f"{len(current)} current Git registrations; {len(stale)} stale registrations (not evidence of disk usage).")
        for item in current[:60]:
            lines.append(f"- {item.get('path', item.get('worktree', '?'))} ({item.get('branch', 'detached')})")
        if len(current) > 60:
            lines.append("More registrations are available in 'monitor status --json'.")
        if inventory.get("truncated") or inventory.get("issues"):
            lines.append("Inventory coverage is incomplete; inspect monitor status --json for details.")
    lines.extend(["", "Review large recent growers before cleanup; preserve source, active worktrees and unfinished outputs.",
                  "Folder totals overlap. APFS clones/hardlinks can share bytes; sums are not guaranteed reclaim.",
                  "Swap is managed by macOS; leave system swap files alone. Save work before quitting apps or restarting.",
                  "This monitor only observes. Commands need 'agent-storage-guard run' for scratch budgeting."])
    return "\n".join(lines) + "\n"
