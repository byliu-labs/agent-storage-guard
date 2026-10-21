"""Bounded native folder accounting. Unknown coverage never becomes zero."""
from __future__ import annotations

import math
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

from .measurement import MeasurementError, _checked_path, _stop_scanner
from .scan_totals import ScanTotals

_LINE_LIMIT = 16 * 1024
_DU = "/usr/bin/du"
_MAX_GROUPS = 12000


def _unknown(message: str, observed: float, kind: str = "io_error") -> dict:
    return {"complete": False, "error": message, "observed_at": observed,
            "error_kind": kind, "timed_out": kind == "deadline"}


def _prefixes(spec: dict) -> list[str]:
    prefixes = spec.get("prefix_groups", [])
    if prefixes is True:  # Legacy migration compatibility, never a default.
        prefixes = ["video-pi-test-", "truly-"]
    if not prefixes:
        return []
    if not isinstance(prefixes, list) or len(prefixes) > 32:
        raise ValueError("prefix_groups must be a list of at most 32 prefixes")
    if any(not isinstance(p, str) or not p or len(p) > 128 or any(c in p for c in "/[]*\n\r") for p in prefixes):
        raise ValueError("prefix_groups must contain literal filename prefixes")
    if len(set(prefixes)) != len(prefixes):
        raise ValueError("prefix_groups contains duplicate prefixes")
    return prefixes


def _native_scan(root: Path, depth: int, deadline: float, totals: ScanTotals) -> tuple[int | None, str | None]:
    process = subprocess.Popen(
        [_DU, "-P", "-x", "-k", "-d", str(depth), str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    selector = selectors.DefaultSelector()
    pending = {"stdout": bytearray(), "stderr": bytearray()}
    handlers = {"stdout": totals.output, "stderr": totals.error}
    completed = False
    try:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "Folder scan exceeded deadline; completed subtrees retained"
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), 16384)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                buffer = pending[key.data]
                buffer.extend(data)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    if len(line) > _LINE_LIMIT:
                        raise MeasurementError("native scan record exceeds filename/diagnostic limit")
                    handlers[key.data](bytes(line))
                    buffer[:] = rest
                if len(buffer) > _LINE_LIMIT:
                    raise MeasurementError("native scan record exceeds filename/diagnostic limit")
        for name, buffer in pending.items():
            if buffer:
                handlers[name](bytes(buffer))
        try:
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return None, "Folder scan exceeded deadline; completed subtrees retained"
        completed = True
        return code, None
    finally:
        selector.close()
        try:
            if not completed or process.poll() is None:
                _stop_scanner(process)
        finally:
            process.stdout.close()
            process.stderr.close()


def scan_root(spec: dict, timeout: float = 900, now: float | None = None) -> dict:
    """Return aggregate groups, retaining unknown measurements explicitly.

    Prefix grouping is restricted to depth one to avoid counting nested totals
    twice. Directory totals include contained files, allocated blocks and hardlink
    accounting; APFS shared blocks are not exclusively reclaimable storage.
    """
    root = Path(spec["path"]).expanduser().absolute()
    prefixes = _prefixes(spec)
    depth = spec.get("depth", 1)
    if type(depth) is not int or not 0 <= depth <= 4 or (prefixes and depth != 1):
        raise ValueError("scan depth must be 0..4, or exactly 1 for prefix groups")
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError("scan timeout must be positive and at most 3600 seconds")
    observed = time.time() if now is None else now
    deadline = time.monotonic() + timeout
    try:
        logical_root = root
        # /var is a system-managed alias on macOS, including getconf's temp
        # directory. Preserve configured keys while checking its canonical path.
        if sys.platform == "darwin" and root.parts[:2] == ("/", "var"):
            if Path("/var").resolve() == Path("/private/var"):
                root = Path("/private/var").joinpath(*root.parts[2:])
        root, identity = _checked_path(root, directory=True)
        totals = ScanTotals(root, depth, prefixes, _MAX_GROUPS)
        code, stopped = _native_scan(root, depth, deadline, totals)
        if _checked_path(root, directory=True)[1] != identity:
            raise MeasurementError("folder root identity changed during scan")
        groups = totals.finish(code, stopped, time.time() if now is None else now)
        return {str(logical_root / Path(key).relative_to(root)): value for key, value in groups.items()}
    except (OSError, MeasurementError, ValueError) as error:
        return {str(Path(spec["path"]).expanduser().absolute()): _unknown(str(error), time.time() if now is None else now,
            "permission_denied" if isinstance(error, PermissionError) else "io_error")}


def _merge(groups: dict, measured: dict, root: str, configured: set[str]) -> None:
    if measured[root].get("complete"):
        for key in list(groups):
            if key not in configured and Path(root) in Path(key).parents:
                del groups[key]
    available = _MAX_GROUPS - len(groups) + 1
    retained = {root: measured[root]}
    for key, item in measured.items():
        if key != root and (key in groups or len(retained) < available):
            retained[key] = item
    if len(retained) < len(measured):
        for item in retained.values():
            item["children_complete"] = False
            item["detail_note"] = "Folder detail limit reached; root accounting retained"
    groups.update(retained)


def scan_roots(specs: list[dict], timeout: float = 900, total_timeout: float = 1200) -> dict:
    """Give every root a first turn, then retry slow roots with the spare budget."""
    if not math.isfinite(total_timeout) or not 0 < total_timeout <= 7200:
        raise ValueError("total scan timeout must be positive and at most 7200 seconds")
    deadline = time.monotonic() + total_timeout
    ordered = [str(Path(spec["path"]).expanduser().absolute()) for spec in specs]
    configured = set(ordered)
    groups = {root: _unknown("Waiting for this scan pass", time.time(), "pending") for root in ordered}
    first_budget = min(timeout, total_timeout / max(1, len(specs)))
    retry = []
    for spec in specs:
        root = str(Path(spec["path"]).expanduser().absolute())
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            groups[root] = _unknown("Total folder scan deadline reached", time.time(), "deadline")
            continue
        measured = scan_root(spec, min(first_budget, remaining))
        _merge(groups, measured, root, configured)
        if "deadline" in measured[root].get("error", "").lower():
            retry.append(spec)
    for spec in retry:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        root = str(Path(spec["path"]).expanduser().absolute())
        # Every root already got a first turn. Finish one slow root with the
        # spare budget instead of repeatedly giving all of them too little.
        measured = scan_root(spec, min(timeout, remaining))
        _merge(groups, measured, root, configured)
    return groups
