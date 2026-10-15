"""Read-only macOS memory evidence; swap size and uptime alone are not pressure.

Apple XNU's sysctl handler exports dispatch flags (1/2/4), *not* the
internal pressure enumeration (0/1/2/3). Unknown values remain unknown.
See docs/monitoring.md for primary sources and measurement limits.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time


def parse_macos(sysctl: str, vm_stat: str, now: float) -> dict:
    patterns = {
        "swap_total_bytes": r"total\s*=\s*([0-9.]+)M",
        "swap_used_bytes": r"used\s*=\s*([0-9.]+)M",
        "pressure_flag": r"kern\.memorystatus_vm_pressure_level:\s*(\d+)",
        "boot_time": r"kern\.boottime:\s*\{\s*sec\s*=\s*(\d+)",
        "physical_bytes": r"hw\.memsize:\s*(\d+)",
    }
    matches = {name: re.search(pattern, sysctl) for name, pattern in patterns.items()}
    missing = [name for name, match in matches.items() if match is None]
    if missing:
        raise ValueError(f"incomplete macOS memory sample: {', '.join(missing)}")
    values = {name: float(match.group(1)) for name, match in matches.items()}
    result = {"supported": True, "time": now,
              "pressure": {1: "normal", 2: "warning", 4: "critical"}.get(int(values["pressure_flag"]), "unknown"),
              "pressure_flag": int(values["pressure_flag"]), "boot_time": int(values["boot_time"]),
              "physical_bytes": int(values["physical_bytes"]),
              "uptime_seconds": max(0, now - values["boot_time"]),
              "swap_total_bytes": int(values["swap_total_bytes"] * 1024 ** 2),
              "swap_used_bytes": int(values["swap_used_bytes"] * 1024 ** 2)}
    page_size = re.search(r"page size of (\d+) bytes", vm_stat)
    compressor = re.search(r"Pages occupied by compressor:\s*(\d+)", vm_stat)
    if page_size and compressor:
        result["compressed_bytes"] = int(page_size.group(1)) * int(compressor.group(1))
    return result


def sample(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if sys.platform != "darwin":
        return {"supported": False, "time": now, "pressure": "unknown",
                "reason": "memory pressure collection currently supports macOS"}
    try:
        raw = subprocess.run(["/usr/sbin/sysctl", "vm.swapusage", "kern.memorystatus_vm_pressure_level",
                              "kern.boottime", "hw.memsize"], check=True, capture_output=True, text=True, timeout=5)
        vm = subprocess.run(["/usr/bin/vm_stat"], check=True, capture_output=True, text=True, timeout=5)
        return parse_macos(raw.stdout, vm.stdout, now)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"supported": True, "time": now, "pressure": "unknown",
                "error": f"memory measurement unavailable: {type(error).__name__}: {error}"}
