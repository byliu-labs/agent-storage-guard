import json
import os
from pathlib import Path

import pytest

from agent_storage_guard import ownership
from agent_storage_guard import run_registry


def test_private_directory_rejects_symlink_and_shared_permissions(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        ownership.private_directory(alias)
    real.chmod(0o755)
    with pytest.raises(RuntimeError, match="private"):
        ownership.private_directory(real)


def test_record_must_use_canonical_uuid_and_valid_transition(tmp_path):
    state = tmp_path / "state"
    retirement = tmp_path / "retirement"
    with run_registry.locked(state, retirement) as registry:
        record = registry.admit(1024 * 1024, 0, 2, "test")
        assert record["state"] == "admitted"
        with pytest.raises(RuntimeError, match="transition"):
            registry.transition(record, "admitted")
        with pytest.raises(RuntimeError, match="UUID"):
            ownership.validate_record(dict(record, token="../other"))


def test_registry_lock_symlink_and_non_uuid_records_fail_closed(tmp_path):
    state, retirement = tmp_path / "state", tmp_path / "retirement"
    with run_registry.locked(state, retirement) as registry:
        record = registry.admit(1024 * 1024, 0, 2, "test")
    payload = json.loads((state / "runs.json").read_text())
    payload["runs"][0]["token"] = "../unsafe"
    (state / "runs.json").write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="UUID"):
        with run_registry.locked(state, retirement):
            pass
    (state / "runs.json").rename(tmp_path / "corrupt-record-preserved")
    (state / "admission.lock").rename(tmp_path / "real-lock")
    (state / "admission.lock").symlink_to(tmp_path / "real-lock")
    with pytest.raises(RuntimeError, match="symlink"):
        with run_registry.locked(state, retirement):
            pass


def test_retirement_may_be_state_child_but_not_owned_scratch(tmp_path):
    state = tmp_path / "state"
    with run_registry.locked(state, state / "retired"):
        pass
    with pytest.raises(RuntimeError, match="inside scratch"):
        with run_registry.locked(state, state / "scratch" / "retired"):
            pass


def test_parent_traversal_is_rejected_before_creating_state(tmp_path):
    with pytest.raises(RuntimeError, match="parent traversal"):
        ownership.private_directory(tmp_path / "other" / ".." / "state")
    assert not (tmp_path / "other").exists()


def test_independent_free_space_probe_does_not_wait_for_slow_allocation(tmp_path, monkeypatch):
    import subprocess
    import sys
    import threading
    import time
    from agent_storage_guard import guard, measurement

    entered, release = threading.Event(), threading.Event()
    probes = []

    def slow_allocated(root, timeout):
        entered.set()
        assert release.wait(3)
        return 0

    def free(root):
        probes.append(time.monotonic())
        return 1024 if len(probes) == 1 else 0

    monkeypatch.setattr(measurement, "allocated", slow_allocated)
    monkeypatch.setattr(measurement, "free_bytes", free)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    scanner = None
    try:
        started = time.monotonic()
        code, reason, scanner = guard._watch(process, tmp_path, 1024, 1, .05, [])
        assert entered.is_set()
        assert code == 75
        assert reason == "free-space reserve crossed"
        assert time.monotonic() - started < .5
        assert len(probes) >= 2
    finally:
        release.set()
        if scanner:
            scanner.join(timeout=3)
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("source,target", [
    ("admitted", "running"), ("admitted", "preserved"), ("admitted", "retired"),
    ("running", "preserved"), ("running", "retired"), ("preserved", "retired"),
])
def test_valid_transitions_are_persisted(tmp_path, source, target):
    from agent_storage_guard import processes
    state, retirement = tmp_path / "state", tmp_path / "retirement"
    with run_registry.locked(state, retirement) as registry:
        record = registry.admit(1024 * 1024, 0, 2, "test")
        actual = registry.records[record["token"]]
        if source == "running" or target == "running":
            actual["leader"] = processes.identity(os.getpid())
        if source != "admitted":
            registry.transition(actual, source)
        registry.transition(actual, target)
    with run_registry.locked(state, retirement) as registry:
        assert registry.records[record["token"]]["state"] == target


def test_stop_refuses_stale_identity_without_signalling_live_group():
    import subprocess
    import sys
    from agent_storage_guard import processes
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        real = processes.identity(process.pid)
        stale = dict(real, start="wrong-start")
        with pytest.raises(RuntimeError, match="identity lost"):
            processes.stop(process, stale)
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_oversized_registry_and_marker_fail_closed(tmp_path):
    state, retirement = tmp_path / "state", tmp_path / "retirement"
    with run_registry.locked(state, retirement) as registry:
        record = registry.admit(1024 * 1024, 0, 2, "test")
    path = state / "runs.json"
    with path.open("r+b") as stream:
        stream.truncate(1024 * 1024 + 1)
    with pytest.raises(RuntimeError, match="size limit"):
        run_registry.read_status(state)
    marker = state / "scratch" / record["token"] / ".asg-owner.json"
    with marker.open("r+b") as stream:
        stream.truncate(64 * 1024 + 1)
    with pytest.raises(RuntimeError, match="size limit"):
        ownership.verify_root(marker.parent, record)


def test_cleanup_failure_receipt_matches_nonzero_return_and_retains_reservation(tmp_path, monkeypatch):
    import sys
    from agent_storage_guard import guard, processes

    def uncertain_stop(process, expected):
        raise RuntimeError("synthetic inability to verify stop")

    monkeypatch.setattr(processes, "stop", uncertain_stop)
    state, retirement = tmp_path / "state", tmp_path / "retirement"
    code = guard.run(state, retirement, 1024 * 1024, 0, 2, .05, "test", [sys.executable, "-c", "pass"])
    assert code == 74
    assert guard.status(state)["reserved_bytes"] == 1024 * 1024
    receipt = json.loads(next((state / "receipts").glob("*.json")).read_text())
    assert receipt["exit_code"] == 74
    assert receipt["state"] == "preserved"
    assert receipt["retired_path"] is None


def test_admission_preserves_strongest_live_reserve(tmp_path, monkeypatch):
    from agent_storage_guard import measurement
    state, retirement = tmp_path / "state", tmp_path / "retirement"
    monkeypatch.setattr(measurement, "free_bytes", lambda _: 100000)
    with run_registry.locked(state, retirement) as registry:
        registry.admit(10000, 80000, 2, "strong reserve")
        with pytest.raises(RuntimeError, match="projected"):
            registry.admit(20000, 0, 2, "weaker caller")
        assert len(registry.records) == 1


def test_release_commit_failure_preserves_destination_and_recoverable_record(tmp_path, monkeypatch):
    from agent_storage_guard import guard, processes
    state, retirement = tmp_path / "state", tmp_path / "retirement"
    with run_registry.locked(state, retirement) as registry:
        record = registry.admit(1024 * 1024, 0, 2, "test")
        actual = registry.records[record["token"]]
        actual["owner"] = dict(actual["owner"], start="old-owner")
        actual["started_at"] -= 10
        registry.save()
        original_save = registry.save
        failed = []

        def fail_release_once():
            if record["token"] not in registry.records and not failed:
                failed.append(True)
                raise OSError("synthetic registry release write failure")
            original_save()

        monkeypatch.setattr(registry, "save", fail_release_once)
        with pytest.raises(OSError, match="release"):
            registry.retire(actual, "finished", 0)
        assert record["token"] in registry.records
        destination = retirement / ("asg-" + record["token"])
        assert destination.exists()
        registry.preserve(actual, "registry release failed", 74)
        receipt = ownership.read_json(state / "receipts" / (record["token"] + ".json"))
        assert receipt["retired_path"] == str(destination)
        assert receipt["exit_code"] == 74
    assert guard.status(state)["reserved_bytes"] == 1024 * 1024
    assert guard.reconcile(state, retirement)[0]["result"] == "retired"
    assert guard.status(state)["reserved_bytes"] == 0
    assert guard.reconcile(state, retirement) == []
