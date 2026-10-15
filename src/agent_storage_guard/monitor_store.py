"""Private bounded SQLite monitor history, baselines and runtime decisions."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Iterator

from .ownership import checked_file, private_directory, read_json

_MAX_PAYLOAD = 4 * 1024 * 1024
_MAX_HISTORY_BYTES = 96 * 1024 * 1024
_MAX_GROUPS = 12000


def _encode(value: object) -> str:
    payload = json.dumps(value, allow_nan=False, separators=(",", ":"))
    if len(payload.encode()) > _MAX_PAYLOAD:
        raise RuntimeError("monitor payload exceeds 4 MiB budget")
    return payload


def _timestamp(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("monitor timestamp must be finite")
    return float(value)


def _size(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("monitor byte count must be a nonnegative integer")
    return value


def _groups(groups: object) -> dict:
    if not isinstance(groups, dict) or len(groups) > _MAX_GROUPS:
        raise ValueError("monitor groups must be an object of at most 12000 aggregates")
    for key, item in groups.items():
        if not isinstance(key, str) or len(key) > 8192 or not isinstance(item, dict):
            raise ValueError("invalid monitor aggregate")
        if type(item.get("complete")) is not bool:
            raise ValueError("monitor aggregate requires explicit complete coverage")
        if item["complete"] or "bytes" in item:
            _size(item.get("bytes"))
        if "observed_at" in item:
            _timestamp(item["observed_at"])
    _encode(groups)
    return groups


def _complete_parent(groups: dict, key: str) -> bool:
    parent = groups.get(str(Path(key).parent), {})
    return parent.get("complete", False) and parent.get("children_complete", False)


def _changes(groups: dict, history: list[dict], baseline: dict, now: float) -> dict:
    changes = {}
    for key, group in groups.items():
        if not group["complete"]:
            continue
        observed = group.get("observed_at", now)
        if key not in baseline:
            parent = str(Path(key).parent)
            absent = [s["groups"][parent].get("observed_at", s["time"]) for s in history
                      if key not in s["groups"] and _complete_parent(s["groups"], key)
                      and s["groups"][parent].get("observed_at", s["time"]) < observed]
            baseline[key] = {"time": min(absent) if absent else observed,
                             "bytes": 0 if absent else group["bytes"]}
        origin = baseline[key]
        item = {"bytes": group["bytes"], "observed_at": observed,
                "baseline_delta_bytes": group["bytes"] - origin["bytes"],
                "baseline_started_at": origin["time"]}
        for name, window in (("hour", 3600), ("day", 86400)):
            candidates = []
            for sample in history:
                previous = sample["groups"].get(key)
                if previous and previous.get("complete"):
                    at, size = previous.get("observed_at", sample["time"]), previous["bytes"]
                elif previous is None and _complete_parent(sample["groups"], key):
                    parent = sample["groups"][str(Path(key).parent)]
                    at, size = parent.get("observed_at", sample["time"]), 0
                else:
                    continue
                if observed - window * 1.5 <= at < observed:
                    candidates.append((at, size))
            if not candidates:
                continue
            preceding = [candidate for candidate in candidates if candidate[0] <= observed - window]
            at, size = max(preceding) if preceding else min(candidates)
            item.update({name + "_delta_bytes": group["bytes"] - size,
                         name + "_elapsed_seconds": observed - at,
                         name + "_started_at": at})
        changes[key] = item
    return changes


@contextmanager
def _database_lock(state: Path) -> Iterator[None]:
    fd = checked_file(state / "monitor.lock", create=True)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("monitor database is busy; retry after its writer finishes")
                time.sleep(0.025)
        yield
    finally:
        os.close(fd)


def _legacy_values(source: Path) -> dict:
    with ExitStack() as stack:
        for name in ("scan.lock", "notifications.lock"):
            try:
                fd = checked_file(source / name)
            except FileNotFoundError:
                continue  # A never-started legacy instance has no lock files.
            stack.callback(os.close, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("legacy monitor is busy; wait for its scan before importing") from error
        names = ("history", "baseline", "latest", "capacity", "notifications", "config")
        values = {name: read_json(source / (name + ".json"), {}, max_bytes=64 * 1024 * 1024)
                  for name in names}
        if any(not isinstance(value, dict) for value in values.values()):
            raise ValueError("legacy monitor JSON must contain objects")
        return values


class MonitorStore:
    """One SQLite authority for monitor history and alert state.

    An original baseline never expires. Detailed samples retain 90 days (at most
    2160 samples), daily summaries two years (730 samples), and all history payloads
    together at most 96 MiB. SQLite itself is capped at 256 MiB. Budget exhaustion
    raises rather than silently dropping a baseline or recording a false zero.
    """

    def __init__(self, state: Path, max_samples: int = 2160) -> None:
        if type(max_samples) is not int or not 1 <= max_samples <= 2160:
            raise ValueError("max_samples must be between 1 and 2160")
        self.state = private_directory(Path(state))
        self.path = self.state / "monitor.sqlite3"
        self.max_samples = max_samples
        existed = self.path.exists()
        os.close(checked_file(self.path, create=True))
        with self._connection() as connection:
            if not existed:
                connection.execute("PRAGMA auto_vacuum=FULL")
            connection.execute("PRAGMA max_page_count=65536")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f"unsupported monitor database schema {version}")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    observed REAL NOT NULL, tier TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(observed,tier));
                CREATE TABLE IF NOT EXISTS capacity_samples (
                    observed REAL PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baselines (path TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    def __enter__(self) -> MonitorStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Connections are short-lived; retained for explicit context ownership."""

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        # Serialize the entire SQLite lifecycle, including transient journals.
        # Checking a journal before acquiring this lock races SQLite unlinking it.
        with _database_lock(self.state):
            with self._sqlite_connection() as connection:
                yield connection

    @contextmanager
    def _sqlite_connection(self) -> Iterator[sqlite3.Connection]:
        os.close(checked_file(self.path))
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            try:
                os.close(checked_file(sidecar))
            except FileNotFoundError:
                pass  # SQLite removes a completed rollback journal atomically.
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("PRAGMA max_page_count=65536")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _get(connection: sqlite3.Connection, key: str, default: object = None) -> object:
        row = connection.execute("SELECT payload FROM metadata WHERE name=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _set(connection: sqlite3.Connection, key: str, value: object) -> None:
        connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, _encode(value)))

    def get_value(self, key: str, default: object = None) -> object:
        with self._connection() as connection:
            return self._get(connection, key, default)

    def set_value(self, key: str, value: object) -> None:
        if not isinstance(key, str) or len(key) > 100:
            raise ValueError("invalid monitor metadata key")
        with self._transaction() as connection:
            self._set(connection, key, value)

    @staticmethod
    def _history(connection: sqlite3.Connection) -> list[dict]:
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT payload FROM snapshots ORDER BY observed,tier")]

    def history(self) -> list[dict]:
        with self._connection() as connection:
            return self._history(connection)

    @staticmethod
    def _baselines(connection: sqlite3.Connection) -> dict:
        return {row[0]: json.loads(row[1]) for row in connection.execute("SELECT path,payload FROM baselines")}

    def baselines(self) -> dict:
        with self._connection() as connection:
            return self._baselines(connection)

    def latest_scan(self) -> dict:
        return self.get_value("latest_scan", {})

    def latest_capacity(self) -> dict:
        return self.get_value("latest_capacity", {})

    def _prune(self, connection: sqlite3.Connection, now: float) -> None:
        old = connection.execute("SELECT observed,payload FROM snapshots WHERE tier='hourly' AND observed<? ORDER BY observed",
                                 (now - 90 * 86400,)).fetchall()
        daily = {}
        for observed, payload in old:
            daily[int(observed // 86400)] = (observed, payload)
        for observed, payload in daily.values():
            connection.execute("DELETE FROM snapshots WHERE tier='daily' AND observed>=? AND observed<?",
                               (int(observed // 86400) * 86400, (int(observed // 86400) + 1) * 86400))
            connection.execute("INSERT OR REPLACE INTO snapshots VALUES (?,'daily',?)", (observed, payload))
        connection.execute("DELETE FROM snapshots WHERE tier='hourly' AND observed<?", (now - 90 * 86400,))
        connection.execute("DELETE FROM snapshots WHERE tier='daily' AND observed<?", (now - 730 * 86400,))
        for tier, limit in (("hourly", self.max_samples), ("daily", 730)):
            connection.execute("DELETE FROM snapshots WHERE rowid IN (SELECT rowid FROM snapshots WHERE tier=? ORDER BY observed DESC LIMIT -1 OFFSET ?)", (tier, limit))
        total = connection.execute("SELECT COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM snapshots").fetchone()[0]
        while total > _MAX_HISTORY_BYTES:
            rowid, size = connection.execute("SELECT rowid,LENGTH(CAST(payload AS BLOB)) FROM snapshots ORDER BY observed LIMIT 1").fetchone()
            connection.execute("DELETE FROM snapshots WHERE rowid=?", (rowid,))
            total -= size

    def record_scan(self, groups: dict, free_bytes: int, now: float | None = None,
                    started_at: float | None = None) -> dict:
        now = _timestamp(time.time() if now is None else now)
        _size(free_bytes)
        groups = json.loads(_encode(_groups(groups)))
        with self._transaction() as connection:
            history, baseline = self._history(connection), self._baselines(connection)
            for key in baseline:
                synthetic = Path(key).name.startswith("[") and Path(key).name.endswith("]")
                if not synthetic and key not in groups and _complete_parent(groups, key):
                    observed = groups[str(Path(key).parent)].get("observed_at", now)
                    groups[key] = {"bytes": 0, "complete": True, "observed_at": observed, "inferred_absent": True}
            changes = _changes(groups, history, baseline, now)
            if len(baseline) > _MAX_GROUPS:
                raise RuntimeError("monitor baseline count budget exceeded; narrow scan roots")
            current = {"time": now, "started_at": now if started_at is None else _timestamp(started_at),
                       "free_bytes": free_bytes, "groups": groups, "changes": changes, "baseline": baseline}
            current["duration_seconds"] = max(0, now - current["started_at"])
            sample = {k: current[k] for k in ("time", "started_at", "free_bytes", "groups")}
            self._prune(connection, now)
            connection.execute("INSERT OR REPLACE INTO snapshots VALUES (?,'hourly',?)", (now, _encode(sample)))
            self._prune(connection, now)
            for key, value in baseline.items():
                connection.execute("INSERT OR IGNORE INTO baselines VALUES (?,?)", (key, _encode(value)))
            latest = self._get(connection, "latest_scan", {})
            if now >= latest.get("time", -math.inf):
                self._set(connection, "latest_scan", current)
        return current

    def record_capacity(self, payload: dict) -> None:
        _timestamp(payload["time"])
        _size(payload["free_bytes"])
        with self._transaction() as connection:
            latest = self._get(connection, "latest_capacity", {})
            if payload["time"] >= latest.get("time", -math.inf):
                self._set(connection, "latest_capacity", payload)
            encoded = _encode(payload)
            if len(encoded.encode()) > 64 * 1024:
                raise RuntimeError("capacity sample exceeds 64 KiB budget")
            connection.execute("INSERT OR REPLACE INTO capacity_samples VALUES (?,?)", (payload["time"], encoded))
            newest = connection.execute("SELECT MAX(observed) FROM capacity_samples").fetchone()[0]
            connection.execute("DELETE FROM capacity_samples WHERE observed<?", (newest - 7 * 86400,))
            connection.execute("DELETE FROM capacity_samples WHERE rowid IN (SELECT rowid FROM capacity_samples ORDER BY observed DESC LIMIT -1 OFFSET 2016)")
            total = connection.execute("SELECT COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM capacity_samples").fetchone()[0]
            while total > 8 * 1024 * 1024:
                rowid, size = connection.execute("SELECT rowid,LENGTH(CAST(payload AS BLOB)) FROM capacity_samples ORDER BY observed LIMIT 1").fetchone()
                connection.execute("DELETE FROM capacity_samples WHERE rowid=?", (rowid,))
                total -= size

    def capacity_history(self, limit: int = 288) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 2016:
            raise ValueError("capacity history limit must be between 1 and 2016")
        with self._connection() as connection:
            rows = connection.execute("SELECT payload FROM capacity_samples ORDER BY observed DESC LIMIT ?", (limit,))
            return list(reversed([json.loads(row[0]) for row in rows]))

    def migrate_legacy(self, source: Path) -> dict:
        """Import the old aggregate JSON store once, without changing any source file."""
        source = private_directory(Path(source), create=False)
        previous = self.get_value("legacy_import")
        if previous:
            if previous["source"] != str(source):
                raise RuntimeError("a different legacy source was already imported")
            return {**previous, "imported": False}
        values = _legacy_values(source)
        with self._transaction() as connection:
            previous = self._get(connection, "legacy_import")
            if previous:
                if previous["source"] != str(source):
                    raise RuntimeError("a different legacy source was already imported")
                return {**previous, "imported": False}
            counts = sum(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("snapshots", "baselines", "capacity_samples"))
            if counts:
                raise RuntimeError("legacy import requires an empty monitor history")
            baseline = values["baseline"]
            if not isinstance(baseline, dict) or len(baseline) > _MAX_GROUPS:
                raise ValueError("invalid legacy baseline")
            for key, item in baseline.items():
                if not isinstance(key, str) or len(key) > 8192 or not isinstance(item, dict):
                    raise ValueError("invalid legacy baseline entry")
                _timestamp(item["time"])
                _size(item["bytes"])
                connection.execute("INSERT INTO baselines VALUES (?,?)", (key, _encode(item)))
            count = 0
            for tier, name in (("hourly", "samples"), ("daily", "daily")):
                for sample in values["history"].get(name, []):
                    _timestamp(sample["time"])
                    _groups(sample["groups"])
                    connection.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                                       (sample["time"], tier, _encode(sample)))
                    count += 1
            latest = values["latest"]
            if latest:
                _timestamp(latest["time"])
                _groups(latest["groups"])
                latest["baseline"] = baseline
                latest.setdefault("changes", _changes(latest["groups"], self._history(connection), dict(baseline), latest["time"]))
                self._set(connection, "latest_scan", latest)
            capacity = values["capacity"]
            if capacity:
                _timestamp(capacity["time"])
                _size(capacity["free_bytes"])
                self._set(connection, "latest_capacity", capacity)
                connection.execute("INSERT INTO capacity_samples VALUES (?,?)", (capacity["time"], _encode(capacity)))
            for name in ("notifications", "config"):
                self._set(connection, "legacy_" + name, values[name])
            marker = {"source": str(source), "samples": count, "baselines": len(baseline),
                      "completed_at": time.time(), "imported": True}
            self._prune(connection, latest.get("time", time.time()))
            self._set(connection, "legacy_import", marker)
            return marker
