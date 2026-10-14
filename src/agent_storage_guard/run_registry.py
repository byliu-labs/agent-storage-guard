"""Serialized admission, recovery and reversible retirement of owned scratch."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess
import time
import uuid

from . import measurement, ownership, processes

TRANSITIONS = {
    "admitted": {"running", "retired", "preserved"},
    "running": {"retired", "preserved"},
    "preserved": {"retired"},
    "retired": set(),
}
ORPHAN_GRACE_SECONDS = 1.0


class Registry:
    def __init__(self, state: Path, retirement: Path):
        self.state, self.retirement = state, retirement
        self.scratch = ownership.private_directory(state / "scratch")
        self.receipts = ownership.private_directory(state / "receipts")
        self.path = state / "runs.json"
        payload = ownership.read_json(self.path, {"schema": 1, "runs": []})
        if not isinstance(payload, dict) or payload.get("schema") != 1 or not isinstance(payload.get("runs"), list):
            raise RuntimeError("corrupt registry schema")
        self.records = {}
        for raw in payload["runs"]:
            record = ownership.validate_record(raw)
            if record["token"] in self.records:
                raise RuntimeError("duplicate run UUID in registry")
            self.records[record["token"]] = record

    def save(self) -> None:
        for record in self.records.values():
            ownership.validate_record(record)
        ownership.write_json(self.path, {"schema": 1, "runs": list(self.records.values())})

    def transition(self, record: dict, target: str) -> None:
        if target not in TRANSITIONS[record["state"]]:
            raise RuntimeError(f"invalid run transition: {record['state']} -> {target}")
        record["state"] = target
        self.save()

    def admit(self, estimate: int, floor: int, max_runs: int, label: str) -> dict:
        effective_cap = min([max_runs, *(record["max_runs"] for record in self.records.values())])
        if len(self.records) >= effective_cap:
            raise RuntimeError("concurrent-run limit reached (preserved reservations count too)")
        reserved = sum(record["estimate_bytes"] for record in self.records.values())
        effective_floor = max([floor, *(record["floor_bytes"] for record in self.records.values())])
        if measurement.free_bytes(self.scratch) - reserved - estimate < effective_floor:
            raise RuntimeError("projected free space would fall below the configured reserve")
        token = str(uuid.uuid4())
        root = self.scratch / token
        root.mkdir(mode=0o700)
        info = root.lstat()
        record = {
            "schema": 1, "token": token, "state": "admitted",
            "owner": processes.identity(os.getpid()), "leader": None,
            "device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
            "estimate_bytes": estimate, "floor_bytes": floor, "max_runs": max_runs,
            "label": label[:80], "started_at": time.time(),
        }
        ownership.validate_record(record)
        ownership.write_json(root / ".asg-owner.json", ownership.marker(record))
        self.records[token] = record
        self.save()
        return dict(record)

    def running(self, token: str, leader: dict) -> dict:
        record = self.records[token]
        record["leader"] = leader
        self.transition(record, "running")
        return dict(record)

    def _receipt(self, record: dict, reason: str, exit_code: int | None, destination: Path | None) -> None:
        value = dict(record, reason=reason, completed_at=time.time(), exit_code=exit_code,
                     original_path=str(self.scratch / record["token"]),
                     retired_path=str(destination) if destination else None)
        ownership.write_json(self.receipts / (record["token"] + ".json"), value)

    def preserve(self, record: dict, reason: str, exit_code: int | None) -> dict:
        destination = None
        if record["state"] not in {"preserved", "retired"}:
            self.transition(record, "preserved")
        if record["state"] == "retired":
            # Release may have failed after the rename and valid receipt were durable.
            destination = self.retirement / ("asg-" + record["token"])
            ownership.verify_root(destination, record)
            self.records[record["token"]] = record
            self.save()
        self._receipt(record, reason, exit_code, destination)
        return {"token": record["token"], "result": "preserved", "reason": reason}

    def retire(self, record: dict, reason: str, exit_code: int | None = None) -> dict:
        token = record["token"]
        root = self.scratch / token
        destination = self.retirement / ("asg-" + token)
        leader = record["leader"]
        if leader and (processes.alive(leader) or processes.group_members(leader["pid"])):
            raise RuntimeError("managed processes still active; scratch preserved")
        # A completed rename with an interrupted receipt/registry commit is recoverable.
        source_exists = root.exists() or root.is_symlink()
        if destination.exists() or destination.is_symlink():
            if source_exists:
                raise RuntimeError("retirement destination collision; scratch preserved")
            ownership.verify_root(destination, record)
            current = destination
        else:
            ownership.verify_root(root, record)
            current = root
        if processes.open_paths(current):
            raise RuntimeError("open files in owned scratch; preserving it")
        if current == root:
            ownership.verify_root(root, record)
            os.rename(root, destination)
        if record["state"] != "retired":
            self.transition(record, "retired")
        self._receipt(record, reason, exit_code, destination)
        del self.records[token]
        try:
            self.save()
        except BaseException:
            self.records[token] = record
            raise
        return {"token": token, "result": "retired", "destination": str(destination)}

    def reconcile(self) -> list[dict]:
        outcomes = []
        for record in list(self.records.values()):
            if processes.alive(record["owner"]):
                outcomes.append({"token": record["token"], "result": "preserved-live"})
                continue
            if time.time() - record["started_at"] < ORPHAN_GRACE_SECONDS:
                outcomes.append({"token": record["token"], "result": "preserved-grace"})
                continue
            try:
                outcomes.append(self.retire(record, "recovered after owner exit"))
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                outcomes.append(self.preserve(record, str(error), None))
        # Unknown scratch never becomes owned merely because it contains a marker.
        for root in self.scratch.iterdir():
            if root.name not in self.records:
                outcomes.append({"path": str(root), "result": "preserved-unowned"})
        return outcomes


@contextmanager
def locked(state: Path, retirement: Path):
    state = ownership.private_directory(state)
    retirement = ownership.private_directory(retirement)
    if state.stat().st_dev != retirement.stat().st_dev:
        raise RuntimeError("retirement must be on the scratch filesystem (no implicit copies)")
    if state == retirement or retirement in state.parents:
        raise RuntimeError("state must not be inside the retirement directory")
    fd = ownership.checked_file(state / "admission.lock", create=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        registry = Registry(state, retirement)
        if registry.scratch.stat().st_dev != retirement.stat().st_dev:
            raise RuntimeError("retirement must be on the scratch filesystem (no implicit copies)")
        for owned in [registry.scratch, registry.receipts]:
            if retirement == owned or owned in retirement.parents:
                raise RuntimeError("retirement must not be inside scratch or receipts")
        yield registry
    finally:
        os.close(fd)


def read_status(state: Path) -> dict:
    ownership.no_symlinks(state)
    if not state.exists():
        return {"schema": 1, "active_runs": [], "reserved_bytes": 0}
    state = ownership.private_directory(state, create=False)
    if not (state / "runs.json").exists() and not (state / "admission.lock").exists():
        return {"schema": 1, "active_runs": [], "reserved_bytes": 0}
    fd = ownership.checked_file(state / "admission.lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        payload = ownership.read_json(state / "runs.json", {"schema": 1, "runs": []})
        if not isinstance(payload, dict) or payload.get("schema") != 1 or not isinstance(payload.get("runs"), list):
            raise RuntimeError("corrupt registry schema")
        records = [ownership.validate_record(record) for record in payload["runs"]]
        if len({record["token"] for record in records}) != len(records):
            raise RuntimeError("duplicate run UUID in registry")
        return {"schema": 1, "active_runs": records,
                "reserved_bytes": sum(record["estimate_bytes"] for record in records)}
    finally:
        os.close(fd)
