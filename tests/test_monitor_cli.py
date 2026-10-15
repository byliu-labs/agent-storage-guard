"""Real CLI flows: initialize, measure, retain history and explain next actions."""
import json
from pathlib import Path
import subprocess
import sys


def invoke(state, *args):
    return subprocess.run([sys.executable, "-m", "agent_storage_guard", "--state", str(state),
                           "monitor", *args], capture_output=True, text=True, timeout=30)


def test_status_before_install_is_read_only(tmp_path):
    state = tmp_path / "absent"
    result = invoke(state, "status")
    assert result.returncode == 0, result.stderr
    assert "monitor init" in result.stdout
    assert not state.exists()


def test_monitor_real_scan_and_report(tmp_path):
    state, root = tmp_path / "state", tmp_path / "project"
    root.mkdir()
    (root / "fixture").write_bytes(b"a" * 8192)
    assert invoke(state, "init", "--root", str(root)).returncode == 0
    config_path = state / "monitor" / "config.json"
    config = json.loads(config_path.read_text())
    config["warn_free_bytes"] = 2 ** 60
    config["urgent_free_bytes"] = 0
    config_path.write_text(json.dumps(config))
    first = invoke(state, "scan", "--json")
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)
    assert payload["scan"]["groups"][str(root)]["complete"]
    # The second CLI sample must be separated by a real policy interval, not
    # merely another process invocation a fraction of a second later.
    from agent_storage_guard.monitor_store import MonitorStore
    with MonitorStore(state / "monitor") as store:
        previous = store.latest_capacity()
        previous["time"] -= 300
        store.set_value("latest_capacity", previous)
    (root / "fixture").write_bytes(b"b" * 16384)
    assert invoke(state, "scan").returncode == 0
    result = invoke(state, "status")
    assert result.returncode == 0, result.stderr
    assert "below" in result.stdout and "reserve" in result.stdout
    assert "Review" in result.stdout
    assert str(root) in result.stdout
    assert "Memory" in result.stdout and "UTC" in result.stdout
    assert (state / "monitor" / "report.txt").read_text() == result.stdout
    assert config_path.stat().st_mode & 0o077 == 0


def test_missing_root_is_visible_without_replacing_baseline(tmp_path):
    state, root = tmp_path / "state", tmp_path / "project"
    root.mkdir()
    assert invoke(state, "init", "--root", str(root)).returncode == 0
    assert invoke(state, "scan").returncode == 0
    root.rename(tmp_path / "preserved")
    assert invoke(state, "scan").returncode == 0
    result = invoke(state, "status")
    assert "coverage" in result.stdout.lower()
    assert "preserved" in result.stdout.lower()


def test_bad_monitor_configuration_fails_before_scan(tmp_path):
    state = tmp_path / "state"
    assert invoke(state, "init", "--root", str(tmp_path)).returncode == 0
    path = state / "monitor" / "config.json"
    config = json.loads(path.read_text())
    config["scan_timeout_seconds"] = -1
    path.write_text(json.dumps(config))
    result = invoke(state, "scan")
    assert result.returncode != 0
    assert "scan_timeout_seconds" in result.stderr and "Traceback" not in result.stderr


def test_second_init_preserves_original_configuration(tmp_path):
    state = tmp_path / "state"
    assert invoke(state, "init", "--root", str(tmp_path)).returncode == 0
    path = state / "monitor" / "config.json"
    before = path.read_bytes()
    assert invoke(state, "init").returncode != 0
    assert path.read_bytes() == before


def test_log_retention_keeps_launchd_descriptor_and_bounded_backup(tmp_path):
    import os
    from agent_storage_guard.monitor import rotate_logs
    directory = tmp_path / "monitor"
    logs = directory / "logs"
    logs.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    path = logs / "tick.stderr.log"
    with path.open("ab") as launchd:
        path.chmod(0o600)
        launchd.write(b"x" * (2 * 1024 ** 2))
        launchd.flush()
        inode = path.stat().st_ino
        rotate_logs(directory)
        launchd.write(b"new error\n")
        launchd.flush()
    assert path.stat().st_ino == inode
    assert path.read_bytes() == b"new error\n"
    assert path.with_suffix(".previous").stat().st_size == 1024 ** 2


def test_constrained_native_passes_give_every_root_first_priority(tmp_path):
    from agent_storage_guard import monitor
    from agent_storage_guard.monitor_store import MonitorStore
    roots = [tmp_path / f"root-{index}" for index in range(4)]
    for root in roots:
        root.mkdir()
    directory = tmp_path / "monitor"
    monitor.initialize(directory, roots)
    config = monitor.load_config(directory)
    config["scan_total_timeout_seconds"] = .000001
    observed = []
    for _ in range(4):
        assert monitor.scan(directory, config)
        with MonitorStore(directory) as store:
            groups = store.latest_scan()["groups"]
            observed.append(next(iter(groups)))
            assert set(groups) == {str(root) for root in roots}
            assert all(not item["complete"] for item in groups.values())
    assert observed == [str(root) for root in roots]
