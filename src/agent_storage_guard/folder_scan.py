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

_OUTPUT_LIMIT = 4 * 1024 * 1024
_MAX_GROUPS = 12000


def _unknown(message: str, observed: float) -> dict:
    return {"complete": False, "error": message, "observed_at": observed}


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


def _group(path: Path, root: Path, prefixes: list[str]) -> str:
    name = path.relative_to(root).parts[0]
    prefix = next((p for p in prefixes if name.startswith(p)), None)
    return str(root / (f"[{prefix}*]" if prefix else "[other]"))


def _native_scan(root: Path, depth: int, deadline: float) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        ["/usr/bin/du", "-P", "-x", "-k", "-d", str(depth), str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    completed = False
    try:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MeasurementError("folder scan exceeded deadline")
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), 16384)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                output[key.data].extend(data)
                if sum(map(len, output.values())) > _OUTPUT_LIMIT:
                    raise MeasurementError("folder scan exceeded output budget")
        try:
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            raise MeasurementError("folder scan exceeded deadline") from error
        completed = True
        return code, bytes(output["stdout"]), bytes(output["stderr"])
    finally:
        selector.close()
        try:
            if not completed or process.poll() is None:
                _stop_scanner(process)
        finally:
            process.stdout.close()
            process.stderr.close()


def _parse(output: bytes, root: Path, depth: int, prefixes: list[str]) -> dict:
    groups = {}
    if prefixes:
        groups = {str(root / label): {"bytes": 0, "entries": 0, "complete": True}
                  for label in [*(f"[{p}*]" for p in prefixes), "[other]"]}
    for line in output.splitlines():
        size, separator, raw = line.partition(b"\t")
        if not separator or not size.isdigit():
            raise MeasurementError("native folder scan returned ambiguous filenames or invalid output")
        path = Path(os.fsdecode(raw))
        relative = path.relative_to(root)
        key = _group(path, root, prefixes) if prefixes and relative.parts else str(path)
        item = groups.setdefault(key, {"bytes": 0, "entries": 0, "complete": True,
                                       "children_complete": len(relative.parts) < depth})
        item["bytes"] += int(size) * 1024
        item["entries"] += 1
        if len(groups) > _MAX_GROUPS:
            raise MeasurementError("folder scan exceeded aggregate count budget")
    if str(root) not in groups:
        raise MeasurementError("native folder scan returned no root total")
    return groups


def _mark_gaps(groups: dict, errors: bytes, root: Path, prefixes: list[str]) -> None:
    bad = {str(root)}
    for line in os.fsdecode(errors).splitlines():
        if not line.startswith("du: ") or ": " not in line[4:]:
            bad = set(groups)
            break
        path = Path(line[4:].rsplit(": ", 1)[0])
        try:
            relative = path.relative_to(root)
        except ValueError:
            bad = set(groups)
            break
        if not relative.parts:
            bad = set(groups)
            break
        if prefixes:
            bad.add(_group(path, root, prefixes))
        else:
            bad.update(str(parent) for parent in [path, *path.parents] if str(parent) in groups)
    if not errors:
        bad = set(groups)
    for key in bad:
        if key in groups:
            groups[key]["complete"] = False
            groups[key]["error"] = "Some entries could not be measured; previous baseline preserved"
            groups[key]["children_complete"] = False


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
        code, output, errors = _native_scan(root, depth, deadline)
        if _checked_path(root, directory=True)[1] != identity:
            raise MeasurementError("folder root identity changed during scan")
        groups = _parse(output, root, depth, prefixes)
        if code or errors:
            _mark_gaps(groups, errors, root, prefixes)
        elif prefixes:
            # Native du lists directories; files directly under the root belong
            # to the other aggregate too. No per-file database is needed.
            children = sum(v["bytes"] for k, v in groups.items() if k != str(root))
            groups[str(root / "[other]")]["bytes"] += max(0, groups[str(root)]["bytes"] - children)
        observed = time.time() if now is None else now
        for item in groups.values():
            item["observed_at"] = observed
        return {str(logical_root / Path(key).relative_to(root)): value for key, value in groups.items()}
    except (OSError, MeasurementError, ValueError) as error:
        return {str(Path(spec["path"]).expanduser().absolute()): _unknown(str(error), time.time() if now is None else now)}


def scan_roots(specs: list[dict], timeout: float = 900, total_timeout: float = 1200) -> dict:
    """Scan sequentially under a whole-pass deadline; unvisited roots stay unknown."""
    if not math.isfinite(total_timeout) or not 0 < total_timeout <= 7200:
        raise ValueError("total scan timeout must be positive and at most 7200 seconds")
    deadline = time.monotonic() + total_timeout
    groups = {}
    for spec in specs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            root = str(Path(spec["path"]).expanduser().absolute())
            groups[root] = _unknown("Total folder scan deadline reached", time.time())
        else:
            measured = scan_root(spec, min(timeout, remaining))
            if len(groups) + len(measured) > _MAX_GROUPS:
                root = str(Path(spec["path"]).expanduser().absolute())
                groups[root] = _unknown("Total folder aggregate count budget reached", time.time())
            else:
                groups.update(measured)
    return groups
