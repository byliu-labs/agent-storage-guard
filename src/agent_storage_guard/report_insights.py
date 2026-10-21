"""Evidence-driven presentation model; measurements never grant cleanup authority."""
from __future__ import annotations

from pathlib import Path

from .investigation import plan, refresh_commands

GIB = 1024 ** 3
_WINDOWS = (("Past hours", 3600), ("Past day", 86400), ("Past week", 7 * 86400))


def _under(path: str, root: str) -> bool:
    return path == root or Path(root) in Path(path).parents


def _category(path: str) -> str:
    parts = Path(path).parts
    name = Path(path).name
    if name in {".Trash", "Trash", ".local/share/Trash"}:
        return "Trash"
    if name in {".npm", "uv", "pip", "pnpm", "yarn"} and any(p in parts for p in (".cache", ".npm", "Caches", "uv")):
        return "Package caches"
    if name in {".codex", ".claude", ".cursor", ".asg"}:
        return "Agent data"
    if path in {"/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp"} or "var" in parts and "folders" in parts or "scratch" in parts:
        return "Temporary work"
    if name in {"projects", "pyprojects", "src", "worktrees", "repos", "code", "dev"}:
        return "Projects and worktrees"
    return "Other monitored folders"


def _reference(samples: list[dict], end: float, window: float) -> dict | None:
    candidates = [sample for sample in samples if window * .5 <= end - sample.get("time", end) <= window * 1.5]
    return min(candidates, key=lambda s: abs(end - s["time"] - window)) if candidates else None


def _window_choice(history: list[dict], end: float, free: int | None = None) -> tuple | None:
    choices = [(label, seconds, _reference(history, end, seconds)) for label, seconds in _WINDOWS]
    choices = [choice for choice in choices if choice[2] is not None]
    chosen = next((choice for choice in choices if choice[0] == "Past day"), choices[0] if choices else None)
    week = next((choice for choice in choices if choice[0] == "Past week"), None)
    if week and free is not None and chosen and "free_bytes" in chosen[2] and "free_bytes" in week[2]:
        if abs(free - week[2]["free_bytes"]) >= max(2 * GIB, 2 * abs(free - chosen[2]["free_bytes"])):
            chosen = week
    return chosen


def _same_volume(current: dict, previous: dict) -> bool:
    if current.get("volume_device") is not None and previous.get("volume_device") is not None:
        return current["volume_device"] == previous["volume_device"]
    return not (current.get("volume_path") and previous.get("volume_path")
                and current["volume_path"] != previous["volume_path"])


def _period(payload: dict) -> dict:
    capacity, scan = payload.get("capacity", {}), payload.get("scan", {})
    capacity_end = capacity.get("time", payload.get("time", 0))
    folder_end = scan.get("time", capacity_end)
    capacity_history = [s for s in payload.get("capacity_history", [])
                        if "free_bytes" in s and _same_volume(capacity, s)]
    folder_history = payload.get("folder_history", [])
    capacity_choice = _window_choice(capacity_history, capacity_end, capacity.get("free_bytes"))
    folder_choice = _window_choice(folder_history, folder_end)
    # A newly installed capacity probe must not hide imported folder history.
    choose_folders = folder_choice is not None and (capacity_choice is None or folder_choice[1] > capacity_choice[1])
    chosen = folder_choice if choose_folders else capacity_choice
    end = folder_end if choose_folders else capacity_end
    if chosen is None:
        return {"label": "Building a baseline", "started_at": None, "ended_at": end,
                "elapsed_seconds": None, "free_delta_bytes": None, "window_seconds": None,
                "explanation": "A comparable earlier sample is not available yet. Changes are unknown, not zero."}
    label, window, previous = chosen
    capacity_before = _reference(capacity_history, capacity_end, window)
    free_delta = capacity["free_bytes"] - capacity_before["free_bytes"] if capacity_before and "free_bytes" in capacity else None
    return {"label": label, "started_at": previous["time"], "ended_at": end,
            "elapsed_seconds": end - previous["time"], "free_delta_bytes": free_delta,
            "capacity_started_at": capacity_before["time"] if capacity_before else None,
            "capacity_ended_at": capacity_end if capacity_before else None,
            "capacity_elapsed_seconds": capacity_end - capacity_before["time"] if capacity_before else None,
            "window_seconds": window,
            "explanation": "Actual sample times are shown. Folder measurements have their own intervals; they do not prove what caused the disk change."}


def _older_complete(path: str, history: list[dict], before: float) -> tuple[dict, float] | None:
    choices = []
    for sample in history:
        group = sample.get("groups", {}).get(path, {})
        observed = group.get("observed_at", sample["time"])
        if group.get("complete") and observed < before:
            choices.append((group, observed))
    return max(choices, key=lambda pair: pair[1]) if choices else None


def _folder_delta(path: str, observed: float, size: int, history: list[dict], window: float | None) -> dict:
    empty = {"delta_bytes": None, "period_started_at": None, "period_elapsed_seconds": None}
    if window is None:
        return empty
    choices = []
    for sample in history:
        groups = sample.get("groups", {})
        group = groups.get(path)
        if group is None:
            parent = groups.get(str(Path(path).parent), {})
            if not (parent.get("complete") and parent.get("children_complete")):
                continue
            group = {"bytes": 0, "complete": True,
                     "observed_at": parent.get("observed_at", sample["time"])}
        at = group.get("observed_at", sample["time"])
        if group.get("complete") and window * .5 <= observed - at <= window * 1.5:
            choices.append((at, group["bytes"]))
    if not choices:
        return empty
    at, before = min(choices, key=lambda pair: abs(observed - pair[0] - window))
    return {"delta_bytes": size - before, "period_started_at": at,
            "period_elapsed_seconds": observed - at}


def _folders(payload: dict, period: dict) -> list[dict]:
    scan = payload.get("scan", {})
    history = payload.get("folder_history", [])
    roots = [item["path"] for item in payload.get("config", {}).get("roots", [])]
    rows = []
    groups = dict(scan.get("groups", {}))
    historical_paths = {path for sample in history for path in sample.get("groups", {})}
    for path in historical_paths - groups.keys():
        if not any(_under(path, root) for root in roots):
            continue
        parent = groups.get(str(Path(path).parent), {})
        missing_ancestor = any(groups.get(str(ancestor), {}).get("inferred_absent")
                               for ancestor in Path(path).parents)
        if missing_ancestor or parent.get("complete") and parent.get("children_complete"):
            continue  # A current complete listing proves this branch absent.
        groups[path] = {"complete": False, "observed_at": scan.get("time", 0)}
    for path, current in groups.items():
        observed = current.get("observed_at", scan.get("time", 0))
        measured = bool(current.get("complete"))
        fresh = 0 <= payload.get("time", 0) - observed <= payload.get("config", {}).get("scan_interval_seconds", 3600) * 2.5
        complete = measured and fresh
        older = (current, observed) if measured and not fresh else _older_complete(path, history, observed) if not measured else None
        group, at = (current, observed) if complete else older or ({}, None)
        root = max((root for root in roots if _under(path, root)), key=len, default=path)
        origin = scan.get("baseline", {}).get(path)
        spec = next((item for item in payload.get("config", {}).get("roots", []) if item["path"] == root), {})
        prefixes = spec.get("prefix_groups", [])
        if prefixes is True:
            prefixes = ["video-pi-test-", "truly-"]
        grouped = bool(prefixes and Path(path).parent == Path(root) and
                       Path(path).name in ["[other]", *(f"[{prefix}*]" for prefix in prefixes)])
        label = (Path(path).name[1:-1] + " folders") if grouped else Path(path).name or path
        if grouped and Path(path).name == "[other]":
            label = "Other grouped folders"
        row = {"path": path, "label": label, "bytes": group.get("bytes"),
               "coverage": "complete" if complete else "older" if older else "unknown",
               "observed_at": at, "category": _category(root), "root": root,
               "error": current.get("error"), "error_kind": current.get("error_kind"),
               "grouped": grouped, "inspect_path": root if grouped else path,
               "baseline_delta_bytes": group["bytes"] - origin["bytes"] if complete and origin else None}
        row.update(_folder_delta(path, observed, group["bytes"], history, period["window_seconds"])
                   if complete else {"delta_bytes": None, "period_started_at": None, "period_elapsed_seconds": None})
        rows.append(row)
    return sorted(rows, key=lambda row: (-(row["bytes"] or 0), row["path"]))


def _root_contribution(root: str, by_path: dict[str, dict]) -> dict:
    unknown = {"path": root, "bytes": None, "delta_bytes": None, "coverage": "unknown",
               "observed_at": None, "period_started_at": None, "period_elapsed_seconds": None,
               "error": by_path.get(root, {}).get("error"), "error_kind": by_path.get(root, {}).get("error_kind")}
    row = by_path.get(root, unknown)
    if row["coverage"] == "complete":
        return {**row, "known_bytes": row["bytes"], "known_delta_bytes": row["delta_bytes"], "older_known_bytes": 0}
    current = [r for r in by_path.values() if r["path"] != root and _under(r["path"], root) and r["coverage"] == "complete"]
    frontier = [r for r in current if not any(_under(r["path"], other["path"]) for other in current if other != r)]
    if not frontier:
        older = [r for r in by_path.values() if r["path"] != root and _under(r["path"], root) and r["coverage"] == "older"]
        older_frontier = [r for r in older if not any(_under(r["path"], other["path"]) for other in older if other != r)]
        if row["coverage"] != "older" and older_frontier:
            return {**unknown, "coverage": "older_partial", "known_bytes": 0,
                    "older_known_bytes": sum(r["bytes"] for r in older_frontier),
                    "known_delta_bytes": None,
                    "observed_at": min(r["observed_at"] for r in older_frontier)}
        return {**row, "known_bytes": 0, "known_delta_bytes": None, "older_known_bytes": 0}
    deltas = [r["delta_bytes"] for r in frontier if r["delta_bytes"] is not None]
    times = [r["observed_at"] for r in frontier if r["observed_at"] is not None]
    starts = [r["period_started_at"] for r in frontier if r["period_started_at"] is not None]
    intervals = [r["period_elapsed_seconds"] for r in frontier if r["period_elapsed_seconds"] is not None]
    return {**unknown, "coverage": "partial", "known_bytes": sum(r["bytes"] for r in frontier), "older_known_bytes": 0,
            "known_delta_bytes": sum(deltas) if deltas else None,
            "observed_at": min(times) if times else None,
            "period_started_at": min(starts) if starts else None,
            "period_elapsed_seconds": max(intervals) if intervals else None}


def _locations(payload: dict, rows: list[dict]) -> list[dict]:
    configured = sorted({item["path"] for item in payload.get("config", {}).get("roots", [])}, key=len)
    frontier = [root for root in configured if not any(_under(root, parent) for parent in configured if parent != root)]
    by_path = {row["path"]: row for row in rows}
    categories = {}
    for root in frontier:
        categories.setdefault(_category(root), []).append(_root_contribution(root, by_path))
    result = []
    for label, members in categories.items():
        known = [r["bytes"] for r in members if r["bytes"] is not None]
        deltas = [r["delta_bytes"] for r in members if r["delta_bytes"] is not None]
        measured_deltas = [r["known_delta_bytes"] for r in members if r["known_delta_bytes"] is not None]
        times = [r["observed_at"] for r in members if r["observed_at"] is not None]
        starts = [r["period_started_at"] for r in members if r["period_started_at"] is not None]
        intervals = [r["period_elapsed_seconds"] for r in members if r["period_elapsed_seconds"] is not None]
        complete = all(r["coverage"] == "complete" for r in members)
        partial = any(r["coverage"] == "partial" for r in members)
        older_partial = any(r["coverage"] == "older_partial" for r in members)
        coverage = "complete" if complete else "partial" if partial else "older_partial" if older_partial else "older" if len(known) == len(members) else "unknown"
        result.append({"label": label, "path": members[0]["path"] if len(members) == 1 else None,
            "bytes": sum(known) if len(known) == len(members) else None,
            "known_bytes": sum(r["known_bytes"] for r in members),
            "older_known_bytes": sum(r["older_known_bytes"] for r in members),
            "delta_bytes": sum(deltas) if len(deltas) == len(members) and complete else None,
            "known_delta_bytes": sum(measured_deltas) if measured_deltas else None,
            "coverage": coverage, "observed_at": min(times) if times else None,
            "issues": [{"path": r["path"], "error": r["error"], "kind": r.get("error_kind")}
                       for r in members if r.get("error")],
            "period_started_at": min(starts) if starts else None,
            "period_elapsed_seconds": max(intervals) if intervals else None,
            "roots": [r["path"] for r in members], "root_count": len(members)})
    return sorted(result, key=lambda row: -(row["bytes"] or row["known_bytes"] or row["older_known_bytes"]))


def _status(payload: dict) -> dict:
    config, capacity = payload.get("config", {}), payload.get("capacity", {})
    now = payload.get("time", 0)
    fresh = bool(capacity and 0 <= now - capacity.get("time", 0) <= config.get("tick_interval_seconds", 300) * 2.5)
    free = capacity.get("free_bytes")
    reserve = config.get("warn_free_bytes", 50 * GIB)
    shortfall = max(0, reserve - free) if free is not None else None
    severity = "unknown" if not fresh else "urgent" if free < config.get("urgent_free_bytes", 25 * GIB) else "warning" if shortfall else "normal"
    memory = dict(capacity.get("memory", {}))
    memory.update(payload.get("memory_trend", {}))
    if fresh and memory.get("pressure") == "critical":
        severity = "urgent"
    elif fresh and memory.get("pressure") == "warning" and severity == "normal":
        severity = "warning"
    if not fresh:
        headline = "Refresh measurements before deciding what to do"
    elif shortfall and memory.get("pressure") in {"warning", "critical"}:
        headline = "Disk space is low and memory pressure is high"
    elif shortfall:
        headline = "Disk space is critically low" if free < config.get("urgent_free_bytes", 25 * GIB) else "Disk space is below your reserve"
    elif memory.get("pressure") in {"warning", "critical"}:
        headline = "Memory pressure is critical" if memory["pressure"] == "critical" else "Memory pressure needs attention"
    else:
        headline = "Disk space is within your reserve"
    scan_at = payload.get("scan", {}).get("time")
    return {"headline": headline, "severity": severity, "measured_at": capacity.get("time"),
            "capacity_fresh": fresh, "free_bytes": free, "reserve_bytes": reserve, "shortfall_bytes": shortfall,
            "memory": memory, "folder_measured_at": scan_at,
            "folder_fresh": bool(scan_at and 0 <= now - scan_at <= config.get("scan_interval_seconds", 3600) * 2.5)}


def _growers(rows: list[dict]) -> list[dict]:
    candidates = [row for row in rows if row["coverage"] == "complete" and (row["delta_bytes"] or 0) > 0]
    selected = []
    for row in sorted(candidates, key=lambda r: (-r["delta_bytes"], -len(Path(r["path"]).parts))):
        if not any(_under(row["path"], previous["path"]) or _under(previous["path"], row["path"]) for previous in selected):
            selected.append(row)
    return sorted(selected, key=lambda row: -row["delta_bytes"])


def _actions(status: dict, growers: list[dict], gaps: list[dict], older: list[dict]) -> list[dict]:
    actions = []
    if not status["capacity_fresh"] or not status["folder_fresh"]:
        actions.append({"priority": 0, "title": "Refresh the measurements", "body": "Refresh monitoring with the installed tool, then reopen this report. Check measurement times before deciding what to remove; exact commands are in the appendix."})
    pressure = status["memory"].get("pressure", "unknown")
    if status["capacity_fresh"] and pressure in {"warning", "critical"}:
        actions.append({"priority": 1, "title": "Save work and reduce active memory use", "body": "Open Activity Monitor > Memory, sort by Memory, and quit unused apps normally. If pressure stays high after closing apps, save everything and restart. Never delete system swap files."})
    if status["capacity_fresh"] and (status["shortfall_bytes"] or 0) > 0:
        actions.append({"priority": 2, "title": f"Restore {status['shortfall_bytes'] / GIB:.1f} GiB of disk headroom", "body": "Pause large builds and test runs while reviewing the measured locations below. Move verified finished outputs to another disk; verify the copy before retiring originals. Source and unfinished work must stay protected."})
    if status["folder_fresh"]:
        for row in growers[:3]:
            body = f"{row['label']} uses {row['bytes'] / GIB:.1f} GiB and grew {row['delta_bytes'] / GIB:.1f} GiB over {row['period_elapsed_seconds'] / 3600:.1f} hours."
            body += " Review its generated outputs and running jobs; keep unfinished work. A temporary-looking name does not establish that it is disposable."
            actions.append({"priority": 3, "title": f"Review {row['label']}", "body": body, "path": row["path"], "inspect_path": row["inspect_path"]})
    for row in older[:2]:
        actions.append({"priority": 4, "title": f"Check last-measured {row['label']}",
            "body": f"The last complete measurement was {row['bytes'] / GIB:.1f} GiB. Current size is unknown. Refresh this location before any cleanup decision; inspect generated outputs and keep unfinished work.",
            "path": row["path"], "inspect_path": row["inspect_path"], "observed_at": row["observed_at"]})
    if gaps:
        actions.append({"priority": 4, "title": f"Resolve {len(gaps)} scan coverage gap{'s' if len(gaps) != 1 else ''}", "body": "Open the coverage details below. Narrow inaccessible roots or review macOS Privacy access only for folders you want monitored. Unmeasured bytes remain unknown; earlier complete values are labeled older."})
    if not actions:
        actions.append({"priority": 5, "title": "Keep the monitor running", "body": "No measured condition requires cleanup now. Stable swap use or long uptime alone does not require a restart; watch memory pressure and disk trends."})
    return actions


def _capacity_recent(payload: dict) -> dict | None:
    capacity = payload.get("capacity", {})
    if "time" not in capacity or "free_bytes" not in capacity:
        return None
    candidates = [sample for sample in payload.get("capacity_history", [])
                  if "free_bytes" in sample and 0 < capacity["time"] - sample.get("time", capacity["time"]) <= 36 * 3600
                  and _same_volume(capacity, sample)]
    if not candidates:
        return None
    first = min(candidates, key=lambda sample: sample["time"])
    return {"delta_bytes": capacity["free_bytes"] - first["free_bytes"],
            "elapsed_seconds": capacity["time"] - first["time"],
            "started_at": first["time"], "ended_at": capacity["time"]}


def build(payload: dict) -> dict:
    """Build five presentation sections without modifying evidence or reading files."""
    period, status = _period(payload), _status(payload)
    rows = _folders(payload, period)
    growers = _growers(rows) if status["folder_fresh"] else []
    gaps = [{"path": path, "error": group.get("error", "incomplete measurement"), "kind": group.get("error_kind")}
            for path, group in payload.get("scan", {}).get("groups", {}).items() if not group.get("complete")]
    gaps = [gap for gap in gaps if not any(_under(gap["path"], other["path"]) for other in gaps if other != gap)]
    older = [row for row in rows if row["coverage"] == "older" and (row["bytes"] or 0) >= GIB]
    older = [row for row in older if not any(_under(row["path"], other["path"]) for other in older if other != row)]
    delta = period["free_delta_bytes"]
    comparable_folders = any(row["coverage"] == "complete" and row["delta_bytes"] is not None for row in rows)
    summary = "Not enough comparable history yet."
    if delta is not None:
        summary = f"Free space {'fell' if delta < 0 else 'rose'} by {abs(delta) / GIB:.1f} GiB over {period['capacity_elapsed_seconds'] / 3600:.1f} hours."
    elif period["elapsed_seconds"]:
        summary = (f"Folder changes are available over {period['elapsed_seconds'] / 3600:.1f} hours; comparable disk-capacity history is not available."
                   if comparable_folders else "Earlier folder history is available, but current folder changes are unknown. Refresh incomplete or older measurements.")
    return {"status": status, "period": period, "locations": _locations(payload, rows),
            "investigation": plan(rows, payload.get("config", {}).get("roots", [])),
            "changes": {"summary": summary, "period_label": period["label"], "rows": growers[:5],
                        "capacity_recent": _capacity_recent(payload)},
            "actions": _actions(status, growers, gaps, older),
            "appendix": {"folders": rows, "coverage_gaps": gaps, "worktrees": payload.get("worktrees", {}),
                "reader_path": payload.get("reader_path"), "platform": payload.get("platform"),
                "refresh": refresh_commands(payload),
                "caveats": ["Location categories use disjoint configured root totals. For partial roots, only nonoverlapping currently measured subfolders contribute to known bytes; the remainder stays unknown.",
                            refresh_commands(payload)["note"],
                            "Coverage gaps and absent history are unknown, never zero. Older complete values are explicitly labeled.",
                            "Folder sizes are allocated-block observations. APFS shared blocks and hardlinks mean they are not guaranteed reclaimable space.",
                            "Disk capacity, memory, and folder evidence can have different measurement times; no process or folder is blamed without evidence.",
                            "This report does not authorize deleting active work, source, Git state, or system swap files."]}}
