"""Incremental native-du accounting; output volume is not a storage budget."""
from pathlib import Path
import os

from .measurement import MeasurementError


class ScanTotals:
    def __init__(self, root: Path, depth: int, prefixes: list[str], limit: int = 12000):
        self.root, self.depth, self.prefixes, self.limit = root, depth, prefixes, limit
        self.groups = {str(root): {"complete": False}}
        for label in [*(f"[{p}*]" for p in prefixes), "[other]"] if prefixes else []:
            self.groups[str(root / label)] = {"bytes": 0, "entries": 0, "complete": True}
        self.root_seen = self.details_limited = self.all_bad = False
        self.bad = set()
        self.error_count = 0
        self.error_kind = ""
        self.error_samples = []

    def _key(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        if not relative.parts or not self.prefixes:
            return str(path)
        prefix = next((p for p in self.prefixes if relative.parts[0].startswith(p)), None)
        return str(self.root / (f"[{prefix}*]" if prefix else "[other]"))

    def output(self, line: bytes) -> None:
        size, separator, raw = line.partition(b"\t")
        if not separator or not size.isdigit():
            raise MeasurementError("native folder scan returned ambiguous filenames or invalid output")
        path = Path(os.fsdecode(raw))
        relative = path.relative_to(self.root)
        if len(relative.parts) > self.depth:
            raise MeasurementError("native folder scan returned a path outside its requested depth")
        key = self._key(path)
        if path == self.root:
            if self.root_seen:
                raise MeasurementError("native folder scan returned duplicate root totals")
            self.root_seen = True
            self.groups[key] = {"bytes": int(size) * 1024, "entries": 1, "complete": True,
                                "children_complete": self.depth > 0}
            return
        if key not in self.groups and len(self.groups) >= self.limit:
            self.details_limited = True
            return  # Keep draining: the final root total is still valuable.
        item = self.groups.setdefault(key, {"bytes": 0, "entries": 0, "complete": True,
                                           "children_complete": len(relative.parts) < self.depth})
        item["bytes"] += int(size) * 1024
        item["entries"] += 1

    def error(self, line: bytes) -> None:
        self.error_count += 1
        text = os.fsdecode(line)
        permission = "Operation not permitted" in text or "Permission denied" in text
        if permission:
            self.error_kind = "permission_denied"
        elif not self.error_kind:
            self.error_kind = "changed_during_scan" if "No such file or directory" in text else "io_error"
        if len(self.error_samples) < 3:
            self.error_samples.append(text[:500])
        if not text.startswith("du: ") or ": " not in text[4:]:
            self.all_bad = True
            return
        path = Path(text[4:].rsplit(": ", 1)[0])
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            self.all_bad = True
            return
        if not relative.parts:
            self.all_bad = True
        elif self.prefixes:
            self.bad.add(self._key(path))
        else:
            self.bad.update(str(self.root.joinpath(*relative.parts[:i]))
                            for i in range(1, min(self.depth, len(relative.parts)) + 1))
        if len(self.bad) > self.limit:
            self.bad.clear()
            self.all_bad = True

    def finish(self, code: int | None, stopped: str | None, observed: float) -> dict:
        failure = stopped
        if self.error_count:
            prefix = "Permission denied; grant the monitor reader access" if self.error_kind == "permission_denied" else "Some entries could not be measured"
            failure = "; ".join(filter(None, [stopped, f"{prefix} ({self.error_count} scan errors): {self.error_samples[0]}"]))
        elif not stopped and code:
            failure = f"Native folder scanner exited with status {code} without diagnostics"
            self.all_bad = True
        elif not stopped and not self.root_seen:
            failure = "Native folder scanner returned no root total"
            self.all_bad = True
        bad = set(self.groups) if self.all_bad or stopped and self.prefixes else self.bad
        if failure:
            bad.add(str(self.root))
        for key, item in self.groups.items():
            item["observed_at"] = observed
            if key in bad:
                item.update(complete=False, children_complete=False, error=failure,
                            error_kind=self.error_kind or ("deadline" if stopped else "io_error"), timed_out=bool(stopped))
            if self.details_limited:
                item["children_complete"] = False
                item["detail_note"] = "Folder detail limit reached; complete root accounting retained"
        if self.prefixes and not failure:
            children = sum(v["bytes"] for k, v in self.groups.items() if k != str(self.root))
            self.groups[str(self.root / "[other]")]["bytes"] += max(0, self.groups[str(self.root)]["bytes"] - children)
        return self.groups
