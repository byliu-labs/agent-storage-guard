from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from agent_storage_guard.monitor_store import MonitorStore


def sample(size, now=10000):
    return {"/projects": {"bytes": size, "complete": True, "children_complete": True, "observed_at": now}}


def test_original_baseline_and_hourly_jitter_survive_restart(tmp_path):
    MonitorStore(tmp_path).record_scan(sample(100), 1000, now=10000)
    current = MonitorStore(tmp_path).record_scan(sample(500, 13601), 900, now=13601)
    change = current["changes"]["/projects"]
    assert change["baseline_delta_bytes"] == change["hour_delta_bytes"] == 400
    assert change["hour_elapsed_seconds"] == 3601
    assert current["baseline"]["/projects"] == {"bytes": 100, "time": 10000}


def test_partial_measurement_preserves_baseline_and_unknown_not_zero(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_scan(sample(100), 1000, now=10000)
    current = store.record_scan({"/projects": {"complete": False, "error": "denied"}}, 900, now=13600)
    assert current["changes"] == {}
    assert current["baseline"]["/projects"]["bytes"] == 100


def test_new_and_removed_children_use_only_complete_parent_evidence(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_scan(sample(100), 1000, now=10000)
    grown = sample(300, 13600)
    grown["/projects/new"] = {"bytes": 200, "complete": True}
    current = store.record_scan(grown, 1000, now=13600)
    assert current["changes"]["/projects/new"]["baseline_delta_bytes"] == 200
    assert current["changes"]["/projects/new"]["hour_delta_bytes"] == 200
    gone = store.record_scan(sample(100, 17200), 1000, now=17200)
    assert gone["groups"]["/projects/new"]["bytes"] == 0
    assert gone["changes"]["/projects/new"]["hour_delta_bytes"] == -200


def test_migration_is_atomic_idempotent_and_preserves_unknown(tmp_path):
    legacy = tmp_path / "old"
    legacy.mkdir(mode=0o700)
    values = {
        "history.json": {"samples": [{"time": 10000, "free_bytes": 500, "groups": sample(100)}], "daily": []},
        "baseline.json": {"/projects": {"time": 9000, "bytes": 50}},
        "latest.json": {"time": 13600, "free_bytes": 450, "groups": {"/projects": {"complete": False, "error": "denied"}}},
        "capacity.json": {"time": 13601, "free_bytes": 440},
        "notifications.json": {"space-warning": 13601},
        "config.json": {"roots": [{"path": "/projects", "depth": 1}]},
    }
    for name, value in values.items():
        (legacy / name).write_text(json.dumps(value))
        (legacy / name).chmod(0o600)
    store = MonitorStore(tmp_path / "new")
    assert store.migrate_legacy(legacy)["imported"] is True
    assert store.migrate_legacy(legacy)["imported"] is False
    assert store.baselines()["/projects"] == {"time": 9000, "bytes": 50}
    assert not store.latest_scan()["groups"]["/projects"]["complete"]
    assert store.latest_capacity()["free_bytes"] == 440
    assert store.get_value("legacy_notifications")["space-warning"] == 13601
    assert json.loads((legacy / "baseline.json").read_text()) == values["baseline.json"]


def test_invalid_migration_rolls_back_without_partial_history(tmp_path):
    legacy = tmp_path / "old"
    legacy.mkdir(mode=0o700)
    (legacy / "history.json").write_text(json.dumps({"samples": [{"time": 10, "groups": sample(1)}]}))
    (legacy / "history.json").chmod(0o600)
    (legacy / "baseline.json").write_text(json.dumps({"/bad": {"time": 1, "bytes": -1}}))
    (legacy / "baseline.json").chmod(0o600)
    store = MonitorStore(tmp_path / "new")
    with pytest.raises((ValueError, RuntimeError)):
        store.migrate_legacy(legacy)
    assert store.history() == []
    assert store.baselines() == {}


def test_parallel_capacity_updates_do_not_lose_scan_history(tmp_path):
    store = MonitorStore(tmp_path)
    def write(index):
        worker = MonitorStore(tmp_path)
        worker.record_capacity({"time": index + 100, "free_bytes": index})
        worker.record_scan(sample(index, index + 100), 1000, now=index + 100)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    assert len(store.history()) == 12
    assert len(store.baselines()) == 1


def test_history_retention_is_bounded_and_baseline_is_durable(tmp_path):
    store = MonitorStore(tmp_path, max_samples=3)
    for index in range(6):
        store.record_scan(sample(index, index * 3600), 1000, now=index * 3600)
    assert len(store.history()) == 3
    assert store.baselines()["/projects"]["bytes"] == 0


def test_state_symlink_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError):
        MonitorStore(link)


def test_partial_parent_does_not_infer_new_child_zero(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_scan({"/projects": {"complete": False}}, 1000, now=10000)
    groups = sample(200, 13600)
    groups["/projects/new"] = {"bytes": 200, "complete": True}
    current = store.record_scan(groups, 1000, now=13600)
    assert current["changes"]["/projects/new"]["baseline_delta_bytes"] == 0
    assert "hour_delta_bytes" not in current["changes"]["/projects/new"]


def test_removed_child_stays_unknown_when_parent_listing_is_partial(tmp_path):
    store = MonitorStore(tmp_path)
    groups = sample(200)
    groups["/projects/child"] = {"bytes": 200, "complete": True}
    store.record_scan(groups, 1000, now=10000)
    current = store.record_scan({"/projects": {"complete": False}}, 1000, now=13600)
    assert "/projects/child" not in current["changes"]
    assert current["baseline"]["/projects/child"]["bytes"] == 200


def test_out_of_order_capacity_does_not_replace_fresher_observation(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_capacity({"time": 200, "free_bytes": 100})
    store.record_capacity({"time": 100, "free_bytes": 200})
    assert store.latest_capacity() == {"time": 200, "free_bytes": 100}


def test_daily_history_retention_keeps_baseline_and_two_year_limit(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_scan(sample(1, 0), 1000, now=0)
    store.record_scan(sample(2, 100 * 86400), 1000, now=100 * 86400)
    assert len(store.history()) == 2
    store.record_scan(sample(3, 800 * 86400), 1000, now=800 * 86400)
    assert len(store.history()) == 2
    assert store.baselines()["/projects"]["bytes"] == 1


def test_database_has_private_mode_and_fixed_page_budget(tmp_path):
    import sqlite3
    import stat
    store = MonitorStore(tmp_path)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with store._connection() as connection:
        assert connection.execute("PRAGMA max_page_count").fetchone()[0] == 65536
        assert connection.execute("PRAGMA page_size").fetchone()[0] == 4096


def test_bad_scan_transaction_does_not_partially_create_baselines(tmp_path):
    store = MonitorStore(tmp_path)
    groups = {"/one": {"complete": True, "bytes": 1}, "/two": {"complete": True, "bytes": -1}}
    with pytest.raises(ValueError):
        store.record_scan(groups, 1000, now=100)
    assert store.history() == []
    assert store.baselines() == {}


def test_capacity_history_retains_memory_and_boot_identity_with_bounded_age(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_capacity({"time": 100, "free_bytes": 1, "memory": {"swap_used_bytes": 100, "boot_time": 1}})
    store.record_capacity({"time": 200, "free_bytes": 1, "memory": {"swap_used_bytes": 500, "boot_time": 1}})
    assert [item["memory"]["swap_used_bytes"] for item in store.capacity_history()] == [100, 500]
    store.record_capacity({"time": 8 * 86400, "free_bytes": 1, "memory": {"boot_time": 2}})
    assert len(store.capacity_history()) == 1


def test_future_schema_is_rejected_without_reset(tmp_path):
    import sqlite3
    store = MonitorStore(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="unsupported.*999"):
        MonitorStore(tmp_path)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_changing_prefix_group_config_does_not_infer_false_zero(tmp_path):
    store = MonitorStore(tmp_path)
    first = sample(200)
    first["/projects/[test-*]"] = {"bytes": 200, "complete": True}
    store.record_scan(first, 1000, now=10000)
    current = store.record_scan(sample(200, 13600), 1000, now=13600)
    assert "/projects/[test-*]" not in current["changes"]


def test_import_waits_for_legacy_scan_instead_of_reading_mixed_state(tmp_path):
    import fcntl
    legacy = tmp_path / "old"
    legacy.mkdir(mode=0o700)
    path = legacy / "scan.lock"
    path.touch(mode=0o600)
    store = MonitorStore(tmp_path / "new")
    with path.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="legacy monitor is busy"):
            store.migrate_legacy(legacy)
    assert store.history() == []
    assert store.get_value("legacy_import") is None


def test_late_import_validation_failure_rolls_back_all_inserted_rows(tmp_path):
    legacy = tmp_path / "old"
    legacy.mkdir(mode=0o700)
    history = {"samples": [
        {"time": 100, "groups": sample(1)},
        {"time": 200, "groups": {"/broken": {"bytes": -1, "complete": True}}},
    ]}
    for name, value in {"history.json": history, "baseline.json": {"/projects": {"time": 1, "bytes": 1}}}.items():
        path = legacy / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    store = MonitorStore(tmp_path / "new")
    with pytest.raises(ValueError):
        store.migrate_legacy(legacy)
    assert store.history() == []
    assert store.baselines() == {}


def test_oversize_capacity_sample_rolls_back_latest_and_history(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_capacity({"time": 100, "free_bytes": 1})
    with pytest.raises(RuntimeError, match="64 KiB"):
        store.record_capacity({"time": 200, "free_bytes": 1, "oversized": "x" * 65536})
    assert store.latest_capacity()["time"] == 100
    assert len(store.capacity_history()) == 1


def test_absence_interval_uses_parent_measurement_time_not_scan_completion(tmp_path):
    store = MonitorStore(tmp_path)
    store.record_scan(sample(100, 10000), 1000, now=11000)
    groups = sample(200, 13600)
    groups["/projects/new"] = {"bytes": 100, "complete": True, "observed_at": 13600}
    current = store.record_scan(groups, 1000, now=14600)
    change = current["changes"]["/projects/new"]
    assert change["hour_elapsed_seconds"] == 3600
    assert change["baseline_started_at"] == 10000
    gone = store.record_scan(sample(100, 17200), 1000, now=18200)
    assert gone["groups"]["/projects/new"]["observed_at"] == 17200
    assert gone["changes"]["/projects/new"]["hour_elapsed_seconds"] == 3600


def test_report_history_is_bounded_and_preserves_hour_day_week_anchors(tmp_path):
    latest = 2_000_000
    anchors = {latest - 3600, latest - 86400, latest - 7 * 86400}
    observations = sorted({latest - hour * 3600 for hour in range(0, 240, 6)} | anchors | {latest})
    with MonitorStore(tmp_path / "state") as store:
        for observed in observations:
            store.record_scan({"/dev": {"complete": True, "bytes": observed}}, 100, now=observed)
        selected = store.report_history()
    assert len(selected) <= 14
    assert anchors <= {sample["time"] for sample in selected}
    assert selected[-1]["time"] == latest
