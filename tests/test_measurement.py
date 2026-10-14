import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_storage_guard import measurement
from agent_storage_guard.measurement import MeasurementError, allocated, free_bytes


def test_real_allocated_bytes_not_sparse_apparent_size(tmp_path):
    data = tmp_path / "fixture"
    data.mkdir()
    (data / "normal").write_bytes(b"x" * 131072)
    with (data / "sparse").open("wb") as stream:
        stream.seek(64 * 1024 * 1024)
        stream.write(b"x")
    result = allocated(data)
    assert result >= 131072
    assert result < 1024 * 1024
    native = subprocess.check_output(["/usr/bin/du", "-skxP", str(data)])
    assert result == int(native.split(b"\t", 1)[0]) * 1024


def test_hardlinks_not_counted_twice_and_symlinks_not_followed(tmp_path):
    data = tmp_path / "fixture"
    data.mkdir()
    (data / "original").write_bytes(b"x" * 131072)
    before = allocated(data)
    os.link(data / "original", data / "hardlink")
    assert allocated(data) < before + 32768
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "large").write_bytes(b"x" * 1048576)
    (data / "shortcut").symlink_to(outside, target_is_directory=True)
    assert allocated(data) < before + 32768


def test_symlink_root_and_ancestor_refused(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "child").mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    for path in (link, link / "child"):
        with pytest.raises(MeasurementError, match="symlink"):
            allocated(path)
        with pytest.raises(MeasurementError, match="symlink"):
            free_bytes(path)


def test_missing_path_is_not_reported_as_zero(tmp_path):
    for function in (allocated, free_bytes):
        with pytest.raises(MeasurementError, match="missing|inspect"):
            function(tmp_path / "missing")


def test_unreadable_subdirectory_fails_closed(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read mode-000 directories")
    root = tmp_path / "fixture"
    root.mkdir()
    denied = root / "denied"
    denied.mkdir()
    (denied / "hidden").write_bytes(b"x" * 4096)
    denied.chmod(0)
    try:
        with pytest.raises(MeasurementError, match="incomplete"):
            allocated(root)
    finally:
        denied.chmod(0o700)


def test_free_space_matches_physical_volume_signal(tmp_path):
    assert free_bytes(tmp_path) == shutil.disk_usage(tmp_path).free


def helper(tmp_path, monkeypatch, source):
    path = tmp_path / "fake-du"
    # Helpers use only stdlib: avoid unrelated site initialization under CPU load.
    path.write_text("#!" + sys.executable + " -S\n" + source)
    path.chmod(0o700)
    monkeypatch.setattr(measurement, "_DU", str(path))
    return path


def test_timeout_kills_and_reaps_owned_scanner_group(tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    root.mkdir()
    executable = helper(tmp_path, monkeypatch, """import json,os,pathlib,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(60)'])
def stop(signum,frame):
    child.terminate()
    child.wait(timeout=1)
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
pathlib.Path(sys.argv[-1],'pids.json').write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(60)
""")
    scanner = subprocess.Popen([str(executable), "-skxP", str(root)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        # Handshake before starting the measurement clock. This test must exercise
        # timeout cleanup of an actual child, not race Python startup on a busy CI host.
        ready_deadline = time.monotonic() + 8
        while time.monotonic() < ready_deadline:
            try:
                pids = json.loads((root / "pids.json").read_text())
                break
            except (FileNotFoundError, json.JSONDecodeError):
                assert scanner.poll() is None, "helper exited before readiness"
                time.sleep(.01)
        else:
            pytest.fail("helper did not finish its startup handshake")
        with monkeypatch.context() as patches:
            patches.setattr(measurement.subprocess, "Popen", lambda *args, **kwargs: scanner)
            started = time.monotonic()
            with pytest.raises(MeasurementError, match="deadline"):
                allocated(root, timeout=.3)
            assert time.monotonic() - started < 1.5
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if scanner.poll() is None:
            os.killpg(scanner.pid, signal.SIGTERM)
            try:
                scanner.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(scanner.pid, signal.SIGKILL)
                scanner.wait(timeout=2)
        scanner.stdout.close()
        scanner.stderr.close()


def test_transient_rename_retries_with_one_total_deadline(tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    root.mkdir()
    attempts = []

    def scan(path, deadline):
        attempts.append(deadline)
        if len(attempts) == 1:
            return 1, b"", b"du: transient: No such file or directory\n"
        return 0, b"7\t" + os.fsencode(path) + b"\n", b""

    monkeypatch.setattr(measurement, "_scan", scan)
    assert allocated(root) == 7 * 1024
    assert len(attempts) == 2 and attempts[0] == attempts[1]


def test_nontransient_failure_never_returns_partial_total(tmp_path, monkeypatch):
    helper(tmp_path, monkeypatch, "import sys; print('7\\t'+sys.argv[-1]); sys.stderr.write('Permission denied\\n'); raise SystemExit(1)\n")
    with pytest.raises(MeasurementError, match="incomplete"):
        allocated(tmp_path)


def test_output_limit_stops_scanner_and_does_not_echo_details(tmp_path, monkeypatch):
    helper(tmp_path, monkeypatch, "import sys; sys.stderr.write('sensitive-fixture-name'*100000); sys.stderr.flush()\n")
    with pytest.raises(MeasurementError, match="output") as error:
        allocated(tmp_path)
    assert "sensitive-fixture-name" not in str(error.value)


def test_replaced_root_cannot_supply_a_trusted_measurement(tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    root.mkdir()
    helper(tmp_path, monkeypatch, """import pathlib,sys
root=pathlib.Path(sys.argv[-1]); root.rename(root.with_name('preserved-original'))
root.mkdir(); print('7\\t'+str(root))
""")
    with pytest.raises(MeasurementError, match="identity changed"):
        allocated(root)
    assert (tmp_path / "preserved-original").is_dir()


def test_transient_retry_does_not_reset_total_deadline(tmp_path, monkeypatch):
    root = tmp_path / "fixture"
    root.mkdir()
    clock, attempts = [0.0], []

    def scan(path, deadline):
        attempts.append(deadline)
        clock[0] += .7
        if clock[0] > deadline:
            raise MeasurementError("native measurement exceeded deadline")
        return 1, b"", b"du: transient: No such file or directory\n"

    monkeypatch.setattr(measurement, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(measurement, "_scan", scan)
    with pytest.raises(MeasurementError, match="deadline"):
        allocated(root, timeout=1)
    assert attempts == [1.0, 1.0]


def test_incomplete_success_and_malformed_totals_fail_closed(tmp_path, monkeypatch):
    for script in ("import sys; print('7\\t'+sys.argv[-1]); sys.stderr.write('warning\\n')\n",
                   "print('not-a-total')\n"):
        helper(tmp_path, monkeypatch, script)
        with pytest.raises(MeasurementError, match="incomplete|invalid"):
            allocated(tmp_path)


@pytest.mark.parametrize("timeout", [0, -1, 3, float("nan"), float("inf")])
def test_timeout_must_be_finite_positive_and_at_most_two_seconds(tmp_path, timeout):
    with pytest.raises(MeasurementError, match="timeout"):
        allocated(tmp_path, timeout)
