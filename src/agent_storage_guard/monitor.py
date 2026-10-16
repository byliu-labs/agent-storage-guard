"""Independent capacity probes, bounded folder scans and local alert delivery."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

from . import ownership, monitor_roots, workstation_report, storage_overview


@contextmanager
def locked(directory: Path, name: str, wait: bool = True):
    fd = ownership.checked_file(directory / name, create=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def write_report(directory: Path, text: str, name: str = "report.txt") -> None:
    target = directory / name
    if target.exists() or target.is_symlink():
        os.close(ownership.checked_file(target))
    fd, temporary = tempfile.mkstemp(prefix=".report-", dir=directory)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


def rotate_logs(directory: Path) -> None:
    logs = directory / "logs"
    if not logs.exists():
        return
    ownership.private_directory(logs, create=False)
    with locked(directory, "logs.lock"):
        for name in ("tick", "scan"):
            path = logs / f"{name}.stderr.log"
            if path.exists() or path.is_symlink():
                fd = ownership.checked_file(path, create=True)
                try:
                    if os.fstat(fd).st_size <= 1024 ** 2:
                        continue
                    previous = path.with_suffix(".previous")
                    if previous.exists() or previous.is_symlink():
                        os.close(ownership.checked_file(previous))
                    # Preserve a bounded tail before shrinking our own log. Keep
                    # the inode: launchd may retain its open stderr descriptor.
                    os.lseek(fd, -1024 ** 2, os.SEEK_END)
                    tail = os.read(fd, 1024 ** 2)
                    backup_fd, temporary = tempfile.mkstemp(prefix=".log-", dir=logs)
                    with os.fdopen(backup_fd, "wb") as backup:
                        backup.write(tail)
                        backup.flush()
                        os.fsync(backup.fileno())
                    os.replace(temporary, previous)
                    os.ftruncate(fd, 0)
                    os.fsync(fd)
                finally:
                    os.close(fd)


def load_config(directory: Path) -> dict:
    raw = ownership.read_json(directory / "config.json")
    if raw is None:
        raise RuntimeError("monitor not initialized; run 'agent-storage-guard monitor init'")
    return monitor_roots.validate(raw)


def initialize(directory: Path, roots: list[Path] | None) -> dict:
    from .monitor_store import MonitorStore
    directory = ownership.private_directory(directory)
    with locked(directory, "policy.lock"):
        path = directory / "config.json"
        if path.exists() or path.is_symlink():
            raise RuntimeError("monitor config already exists; refusing overwrite")
        config = monitor_roots.validate(monitor_roots.defaults(roots))
        with MonitorStore(directory):
            ownership.write_json(path, config)
    return {"initialized": str(directory), "roots": len(config["roots"])}


def migrate(directory: Path, source: Path) -> dict:
    from .monitor_store import MonitorStore
    config = monitor_roots.validate(ownership.read_json(source / "config.json"))
    directory = ownership.private_directory(directory)
    with locked(directory, "policy.lock"):
        current = ownership.read_json(directory / "config.json")
        if current is not None and monitor_roots.validate(current) != config:
            raise RuntimeError("existing monitor configuration differs; refusing migration overwrite")
        with MonitorStore(directory) as store:
            result = store.migrate_legacy(source)
            if current is None:
                ownership.write_json(directory / "config.json", config)
    return result


def snapshot(store, config: dict) -> dict:
    capacity = store.latest_capacity() or {}
    memory = capacity.get("memory", {})
    capacity_history = store.capacity_history(limit=2016)
    history = [item for item in capacity_history
               if item.get("memory", {}).get("boot_time") == memory.get("boot_time")
               and "swap_used_bytes" in item.get("memory", {})]
    trend = {}
    if history and "swap_used_bytes" in memory:
        baseline = history[0]
        trend = {"started_at": baseline["time"],
                 "elapsed_seconds": capacity["time"] - baseline["time"],
                 "swap_delta_bytes": memory["swap_used_bytes"] - baseline["memory"]["swap_used_bytes"]}
    return {"time": time.time(), "report_path": str(store.state / "report.html"),
            "reader_path": store.get_value("notification_app"), "platform": sys.platform,
            "config": config, "capacity": capacity, "memory_trend": trend,
            "scan": store.latest_scan() or {}, "alerts": store.get_value("active_alerts", []),
            "folder_history": store.report_history(), "capacity_history": capacity_history,
            "worktrees": store.get_value("worktrees", {})}


def refresh(directory: Path, config: dict, *, probe: bool, send: bool) -> dict:
    from . import alerts, memory_pressure, delivery
    from .monitor_store import MonitorStore
    with locked(directory, "policy.lock"), MonitorStore(directory) as store:
        previous = store.latest_capacity() or {}
        capacity = previous
        if probe:
            usage = shutil.disk_usage(directory)
            capacity = {"time": time.time(), "free_bytes": usage.free,
                        "total_bytes": usage.total, "used_bytes": usage.used,
                        "volume_path": str(directory), "volume_device": directory.stat().st_dev,
                        "memory": memory_pressure.sample()}
        if probe:
            store.record_capacity(capacity)
        now = time.time()
        active, policy = alerts.evaluate(capacity, previous, store.latest_scan() or {}, config,
                                          store.get_value("alert_policy", {}), now)
        store.set_value("active_alerts", active)
        store.set_value("alert_policy", policy)
        payload = snapshot(store, config)
        write_report(directory, workstation_report.render(payload))
        write_report(directory, storage_overview.render(payload), "report.html")
        if send:
            if not delivery.dispatch(store, config, active, policy, payload, now):
                raise RuntimeError("alert delivery incomplete; run 'monitor delivery-check' for redacted destination health")
        return payload


def scan(directory: Path, config: dict) -> bool:
    from . import folder_scan, worktrees
    from .monitor_store import MonitorStore
    try:
        with locked(directory, "scan.lock", wait=False):
            started = time.time()
            started_clock = time.monotonic()
            with MonitorStore(directory) as store:
                offset = store.get_value("scan_offset", 0) % max(1, len(config["roots"]))
            roots = config["roots"][offset:] + config["roots"][:offset]
            groups = folder_scan.scan_roots(roots, timeout=config["scan_timeout_seconds"],
                                            total_timeout=config["scan_total_timeout_seconds"])
            with MonitorStore(directory) as store:
                store.record_scan(groups, shutil.disk_usage(directory).free, now=time.time(), started_at=started)
                # Advance by one, not by the number visited: fixed budget-sized
                # strides can permanently starve alternate roots.
                if time.monotonic() - started_clock >= config["scan_total_timeout_seconds"]:
                    store.set_value("scan_offset", (offset + 1) % max(1, len(roots)))
            roots = [Path(item["path"]) for item in config["roots"]]
            inventory = worktrees.inventory(roots)
            with MonitorStore(directory) as store:
                store.set_value("worktrees", inventory)
        return True
    except BlockingIOError:
        # launchd can ask for a scan while a manual scan owns it; capacity probes continue.
        return False


def read_status(directory: Path, config: dict) -> dict:
    return refresh(directory, config, probe=False, send=False)


def dispatch(args) -> int:
    directory = args.state / "monitor"
    action = args.monitor_action
    if action == "setup":
        from .delivery_setup import configure
        payload = configure(directory, args.provider, args.destination or args.provider, args.root)
        print(json.dumps(payload, indent=2))
        return 0
    if directory.exists():
        ownership.private_directory(directory, create=False)
        rotate_logs(directory)
    if action in {"status", "report"} and not directory.exists():
        print("Monitor not installed. Run 'agent-storage-guard monitor init'.")
        return 0
    if action == "init":
        payload = initialize(directory, args.root)
    elif action == "migrate":
        payload = migrate(directory, args.from_state)
    elif action == "rollback":
        from .installation import rollback
        payload = rollback(args.manifest)
    else:
        directory = ownership.private_directory(directory, create=False)
        config = load_config(directory)
        if action in {"delivery-check", "test-send"}:
            from . import delivery
            from .monitor_store import MonitorStore
            with locked(directory, "policy.lock"), MonitorStore(directory) as store:
                if action == "delivery-check":
                    payload, code = delivery.check_configuration(config, store.get_value("delivery_state", {}))
                else:
                    if args.destination not in {s["id"] for s in delivery.destinations(config)}:
                        raise ValueError("unknown destination; choose a configured destination id")
                    if args.installed:
                        app = store.get_value("notification_app")
                        if not app:
                            raise RuntimeError("installed_helper_missing; run 'monitor install' first")
                    else:
                        payload, code = delivery.test_send(store, config, args.destination)
            if action == "test-send" and args.installed:
                # The helper child needs policy.lock; never wait for it while holding that lock.
                payload, code = delivery.installed_test(Path(app), args.destination, args.state)
            print(json.dumps(payload, indent=2))
            return code
        if action == "install":
            from . import installation, notifications
            from .monitor_store import MonitorStore
            executable = Path(shutil.which("agent-storage-guard") or sys.argv[0]).absolute()
            if not executable.is_file():
                raise RuntimeError("install the CLI with 'uv tool install .' before scheduling")
            refresh(directory, config, probe=True, send=False)
            app = notifications.prepare_app(Path.home() / "Applications", directory / "report.html", executable, args.state) if sys.platform == "darwin" else None
            with MonitorStore(directory) as store:
                store.set_value("notification_app", str(app) if app else None)
            payload = installation.install(Path.home(), executable, args.state, activate=args.activate,
                intervals={"tick": config["tick_interval_seconds"], "scan": config["scan_interval_seconds"]}, app=app)
            refresh(directory, config, probe=False, send=False)
        elif action == "status":
            payload = read_status(directory, config)
        elif action == "report":
            refresh(directory, config, probe=True, send=False)
            target = directory / "report.html"
            if args.open:
                import webbrowser
                if not webbrowser.open(target.absolute().as_uri()):
                    raise RuntimeError(f"browser could not open {target}; open it manually")
            print(target.absolute())
            return 0
        else:
            payload = refresh(directory, config, probe=True, send=args.notify and action != "scan")
            if action == "scan":
                scan(directory, config)
                payload = refresh(directory, config, probe=False, send=args.notify)
        if action in {"status", "tick", "scan"} and not args.json:
            print(workstation_report.render(payload), end="")
            return 0
    print(json.dumps(payload, indent=2))
    return 0
