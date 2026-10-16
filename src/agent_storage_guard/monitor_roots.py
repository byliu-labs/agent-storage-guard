"""Portable monitor defaults and validation of bounded scan policy."""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
import sys

GIB = 1024 ** 3


def defaults(roots: list[Path] | None = None) -> dict:
    home = Path.home()
    paths = roots if roots is not None else [home / name for name in (
        "pyprojects", ".codex", ".claude", ".cache/uv", ".npm", ".Trash")]
    specs = [{"path": str(path.absolute()), "depth": 2 if path.name == "pyprojects" else 1}
             for path in paths if roots is not None or path.is_dir()]
    if roots is None and sys.platform == "darwin":
        user_temp = subprocess.check_output(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"],
                                             text=True, timeout=5).strip()
        specs.extend([{"path": "/private/tmp", "depth": 1},
                      {"path": user_temp.rstrip("/"), "depth": 1}])
    return {"schema_version": 1, "roots": specs, "warn_free_bytes": 50 * GIB,
            "urgent_free_bytes": 25 * GIB, "growth_hour_bytes": 2 * GIB,
            "growth_day_bytes": 5 * GIB, "scan_timeout_seconds": 180,
            "scan_total_timeout_seconds": 1200, "scan_interval_seconds": 3600,
            "tick_interval_seconds": 300}


def validate(raw: object) -> dict:
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("monitor configuration requires schema_version=1")
    config = {**defaults([]), **raw}
    roots = config.get("roots")
    if not isinstance(roots, list) or len(roots) > 32:
        raise ValueError("roots must be a list of at most 32 bounded directories")
    protected = Path.home() / "Library/Application Support/Nest"
    for spec in roots:
        if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
            raise ValueError("each root requires a path")
        path = Path(spec["path"])
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("scan roots must be absolute without parent traversal")
        if path == protected or path in protected.parents or protected in path.parents:
            raise ValueError("choose narrower scan roots outside the protected Nest directory")
        if type(spec.get("depth", 1)) is not int or not 0 <= spec.get("depth", 1) <= 4:
            raise ValueError("root depth must be an integer between 0 and 4")
        groups = spec.get("prefix_groups", [])
        if not isinstance(groups, (list, bool)) or isinstance(groups, list) and (
                len(groups) > 32 or any(not isinstance(x, str) or not x or len(x) > 80
                                      or any(c in x for c in "/\n\r\t*?[]") for x in groups)):
            raise ValueError("prefix_groups must contain at most 32 plain name prefixes")
        if groups and spec.get("depth", 1) != 1:
            raise ValueError("prefix grouping requires root depth=1")
        if isinstance(groups, list) and len(groups) != len(set(groups)):
            raise ValueError("prefix_groups contains duplicate prefixes")
    for name in ("warn_free_bytes", "urgent_free_bytes", "growth_hour_bytes", "growth_day_bytes"):
        if type(config[name]) is not int or config[name] < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if config["urgent_free_bytes"] > config["warn_free_bytes"]:
        raise ValueError("urgent_free_bytes must not exceed warn_free_bytes")
    for name in ("scan_timeout_seconds", "scan_total_timeout_seconds", "scan_interval_seconds",
                 "tick_interval_seconds"):
        value = config[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or value > 86400:
            raise ValueError(f"{name} must be finite and between 0 and 86400 seconds")
    for name, ceiling in (("scan_timeout_seconds", 3600), ("scan_total_timeout_seconds", 7200)):
        if config[name] > ceiling:
            raise ValueError(f"{name} must not exceed {ceiling} seconds")
    for name in ("tick_interval_seconds", "scan_interval_seconds"):
        if type(config[name]) is not int:
            raise ValueError(f"{name} must be a positive integer for scheduling")
    for name in ("recovery_margin_bytes", "swap_growth_bytes"):
        if name in config and (type(config[name]) is not int or config[name] < 0):
            raise ValueError(f"{name} must be a nonnegative integer")
    for name in ("urgent_cooldown_seconds", "alert_cooldown_seconds"):
        value = config.get(name, 3600)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 30 * 86400:
            raise ValueError(f"{name} must be positive and at most 30 days")
    return config
