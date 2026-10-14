import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from agent_storage_guard import guard, ownership, processes, run_registry


GIB = 1024 ** 3


def command(tmp_path, code, estimate=4 * 1024 ** 2, floor=0, max_runs=2):
    config = repr((str(tmp_path / "state"), str(tmp_path / "retirement"), estimate, floor, max_runs))
    launcher = ("from pathlib import Path; import sys; from agent_storage_guard.guard import run; "
                f"s,t,e,f,m={config}; "
                f"sys.exit(run(Path(s),Path(t),e,f,m,.05,'test',[sys.executable,'-c',{code!r}]))")
    return [sys.executable, "-c", launcher]


def execute(tmp_path, code, **kwargs):
    return subprocess.run(command(tmp_path, code, **kwargs), capture_output=True, text=True, timeout=15)


def wait_ready(tmp_path, count=1):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        ready = list((tmp_path / "state" / "scratch").glob("*/ready"))
        if len(ready) == count:
            return ready
        time.sleep(.025)
    raise AssertionError("managed command never became ready")


def teardown(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_success_failure_reversible_receipts_and_no_secrets(tmp_path, monkeypatch):
    environment_secret = "do-not-persist-this-environment-value"
    monkeypatch.setenv("ASG_TEST_SECRET", environment_secret)
    secret = "do-not-persist-this-sensitive-value"
    code = ("import os,pathlib,sys; "
            "pathlib.Path(os.environ['TMPDIR'],'fixture').write_bytes(b'preserved'); "
            f"sys.exit(3 if sys.argv[1]=='{secret}' else 9)")
    # The user command's source is itself argv and must never be copied to metadata.
    result = execute(tmp_path, "import os,pathlib; pathlib.Path(os.environ['TMPDIR'],'fixture').write_bytes(b'preserved')")
    assert result.returncode == 0, result.stderr
    result = execute(tmp_path, code.replace("sys.argv[1]", repr(secret)))
    assert result.returncode == 3, result.stderr
    bundles = list((tmp_path / "retirement").glob("asg-*"))
    assert len(bundles) == 2
    assert all((root / "fixture").read_bytes() == b"preserved" for root in bundles)
    assert guard.status(tmp_path / "state")["reserved_bytes"] == 0
    for receipt in (tmp_path / "state" / "receipts").glob("*.json"):
        assert secret not in receipt.read_text()
        assert environment_secret not in receipt.read_text()
        metadata = json.loads(receipt.read_text())
        assert metadata["retired_path"]
        assert metadata["state"] == "retired"


def test_low_space_refuses_before_user_command(tmp_path):
    marker = tmp_path / "should-not-exist"
    result = execute(tmp_path, f"open({str(marker)!r},'w').close()", floor=10 ** 18)
    assert result.returncode != 0
    assert "projected" in result.stderr
    assert not marker.exists()


def test_actual_allocated_budget_stops_writer(tmp_path):
    code = ("import os,pathlib,time; "
            "pathlib.Path(os.environ['TMPDIR'],'big').write_bytes(b'x' * (2*1024*1024)); time.sleep(30)")
    result = execute(tmp_path, code, estimate=512 * 1024)
    assert result.returncode == 75, result.stderr
    assert "budget" in result.stderr
    assert list((tmp_path / "retirement").glob("asg-*/big"))


READY = "import os,pathlib,time; pathlib.Path(os.environ['TMPDIR'],'ready').write_text(str(os.getpid())); time.sleep(30)"


def test_sigterm_stops_writer_then_retires(tmp_path):
    process = subprocess.Popen(command(tmp_path, READY), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pid = int(wait_ready(tmp_path)[0].read_text())
        process.terminate()
        assert process.wait(timeout=12) == 143
        assert processes.identity(pid) is None
        assert list((tmp_path / "retirement").glob("asg-*/ready"))
    finally:
        teardown(process)


def test_concurrent_admission_limits_real_runs(tmp_path):
    running = [subprocess.Popen(command(tmp_path, READY), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        wait_ready(tmp_path, 2)
        result = execute(tmp_path, "raise AssertionError('must not start')")
        assert result.returncode != 0
        assert "concurrent" in result.stderr
        assert len(guard.status(tmp_path / "state")["active_runs"]) == 2
    finally:
        for process in running:
            teardown(process)


def test_sigkill_owner_pipe_stops_writer_and_reconciliation_is_idempotent(tmp_path):
    process = subprocess.Popen(command(tmp_path, READY), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pid = int(wait_ready(tmp_path)[0].read_text())
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while processes.identity(pid) is not None and time.monotonic() < deadline:
            time.sleep(.025)
        assert processes.identity(pid) is None
        time.sleep(1.05)
        outcomes = guard.reconcile(tmp_path / "state", tmp_path / "retirement")
        assert any(outcome["result"] == "retired" for outcome in outcomes), outcomes
        assert guard.status(tmp_path / "state")["reserved_bytes"] == 0
        assert guard.reconcile(tmp_path / "state", tmp_path / "retirement") == []
    finally:
        teardown(process)


def test_registry_corruption_fails_closed_before_user_command(tmp_path):
    with run_registry.locked(tmp_path / "state", tmp_path / "retirement") as registry:
        registry.save()
    (tmp_path / "state" / "runs.json").write_text('{corrupt')
    result = execute(tmp_path, "raise AssertionError('must not start')")
    assert result.returncode != 0
    assert "corrupt" in result.stderr
    assert not list((tmp_path / "state" / "scratch").iterdir())


def orphan(tmp_path):
    with run_registry.locked(tmp_path / "state", tmp_path / "retirement") as registry:
        record = registry.admit(1024 * 1024, 0, 2, "orphan")
        raw = registry.records[record["token"]]
        raw["owner"] = dict(raw["owner"], start="definitely-not-the-owner")
        raw["started_at"] -= 10
        registry.save()
        return registry.scratch / record["token"], record


def test_admitted_without_group_orphan_is_recovered(tmp_path):
    root, record = orphan(tmp_path)
    (root / "unfinished").write_text("keep")
    outcomes = guard.reconcile(tmp_path / "state", tmp_path / "retirement")
    assert outcomes[0]["result"] == "retired", outcomes
    assert (tmp_path / "retirement" / ("asg-" + record["token"]) / "unfinished").read_text() == "keep"


def test_replaced_inode_is_preserved_and_keeps_reservation(tmp_path):
    root, record = orphan(tmp_path)
    root.rename(tmp_path / "original-owned")
    root.mkdir(mode=0o700)
    ownership.write_json(root / ".asg-owner.json", ownership.marker(record))
    outcomes = guard.reconcile(tmp_path / "state", tmp_path / "retirement")
    assert outcomes[0]["result"] == "preserved", outcomes
    assert "inode" in outcomes[0]["reason"]
    assert guard.status(tmp_path / "state")["reserved_bytes"] == record["estimate_bytes"]
    assert root.exists()


def test_symlink_marker_is_preserved_not_followed(tmp_path):
    root, record = orphan(tmp_path)
    marker = root / ".asg-owner.json"
    marker.rename(tmp_path / "original-marker")
    marker.symlink_to(tmp_path / "original-marker")
    outcomes = guard.reconcile(tmp_path / "state", tmp_path / "retirement")
    assert outcomes[0]["result"] == "preserved", outcomes
    assert "symlink" in outcomes[0]["reason"]


def test_pid_identity_mismatch_never_matches_live_process():
    current = processes.identity(os.getpid())
    assert processes.alive(current)
    assert not processes.alive(dict(current, start="wrong-start"))


def test_partial_retirement_commit_recovers_without_duplicate_move(tmp_path):
    root, record = orphan(tmp_path)
    destination = tmp_path / "retirement" / ("asg-" + record["token"])
    root.rename(destination)
    outcomes = guard.reconcile(tmp_path / "state", tmp_path / "retirement")
    assert outcomes[0]["result"] == "retired", outcomes
    assert destination.exists()
    assert guard.reconcile(tmp_path / "state", tmp_path / "retirement") == []


def test_unowned_scratch_marker_does_not_authorize_retirement(tmp_path):
    with run_registry.locked(tmp_path / "state", tmp_path / "retirement") as registry:
        registry.save()
        root = registry.scratch / "not-a-valid-owned-run"
        root.mkdir(mode=0o700)
        ownership.write_json(root / ".asg-owner.json", {"schema": 1, "token": root.name})
    outcomes = guard.reconcile(tmp_path / "state", tmp_path / "retirement")
    assert outcomes == [{"path": str(root), "result": "preserved-unowned"}]
    assert root.exists()


def test_direct_child_exit_stops_ordinary_descendant_before_retirement(tmp_path):
    code = ("import os,pathlib,subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{READY!r}]); "
            "ready=pathlib.Path(os.environ['TMPDIR'],'ready'); "
            "deadline=time.monotonic()+5; "
            "exec('while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)'); "
            "assert ready.exists()")
    result = execute(tmp_path, code)
    assert result.returncode == 0, result.stderr
    ready = list((tmp_path / "retirement").glob("asg-*/ready"))
    assert len(ready) == 1
    assert processes.identity(int(ready[0].read_text())) is None
    assert guard.status(tmp_path / "state")["reserved_bytes"] == 0


def test_killed_controller_preserves_live_writer_and_reservation(tmp_path):
    owner = subprocess.Popen(command(tmp_path, READY), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    writer = None
    try:
        writer_pid = int(wait_ready(tmp_path)[0].read_text())
        writer = processes.identity(writer_pid)
        record = guard.status(tmp_path / "state")["active_runs"][0]
        leader = record["leader"]
        assert processes.alive(leader)
        os.kill(leader["pid"], signal.SIGKILL)
        assert owner.wait(timeout=10) == 137
        assert processes.alive(writer)
        remaining = guard.status(tmp_path / "state")["active_runs"]
        assert remaining[0]["state"] == "preserved"
        assert guard.status(tmp_path / "state")["reserved_bytes"] > 0
        assert list((tmp_path / "state" / "receipts").glob("*.json"))
        assert not list((tmp_path / "retirement").glob("asg-*"))
    finally:
        if writer is not None and processes.alive(writer):
            os.kill(writer["pid"], signal.SIGTERM)
        teardown(owner)


def test_nested_guard_reuses_validated_outer_registry_and_refuses_larger_estimate(tmp_path):
    nested = command(tmp_path, "pass", estimate=8 * 1024 ** 2)
    code = ("import subprocess,pathlib,os; "
            f"result=subprocess.run({nested!r},capture_output=True); "
            "assert result.returncode != 0; assert b'nested estimate' in result.stderr; "
            "pathlib.Path(os.environ['TMPDIR'],'ready').write_text('same outer run')")
    result = execute(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "state" / "receipts").glob("*.json"))) == 1
    assert len(list((tmp_path / "retirement").glob("asg-*/ready"))) == 1


def test_fast_command_over_budget_is_detected_after_exit(tmp_path):
    result = execute(tmp_path, "import os,pathlib; pathlib.Path(os.environ['TMPDIR'],'big').write_bytes(b'x'*1048576)",
                     estimate=128 * 1024)
    assert result.returncode == 75, result.stderr
    assert list((tmp_path / "retirement").glob("asg-*/big"))


def test_status_missing_state_does_not_create_it(tmp_path):
    state = tmp_path / "absent"
    assert guard.status(state) == {"schema": 1, "active_runs": [], "reserved_bytes": 0}
    assert not state.exists()


def test_forged_inherited_environment_cannot_admit_untrusted_scratch(tmp_path):
    root = tmp_path / "625138fd-ce3e-4d19-b842-45397d506078"
    root.mkdir(mode=0o700)
    env = dict(os.environ, ASG_SCRATCH_ROOT=str(root))
    result = subprocess.run(command(tmp_path, "raise AssertionError('must not start')"),
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "trusted run registry" in result.stderr
    assert not (tmp_path / "state").exists()



def test_nested_guard_rejects_stricter_unenforced_reserve(tmp_path):
    nested = command(tmp_path, "raise AssertionError('must not start')", floor=10 ** 18)
    code = ("import subprocess; "
            f"result=subprocess.run({nested!r},capture_output=True); "
            "assert result.returncode != 0; assert b'nested free-space reserve' in result.stderr")
    result = execute(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "state" / "receipts").glob("*.json"))) == 1


@pytest.mark.parametrize("permit", [False, True])
def test_closed_owner_pipe_never_starts_user_work(tmp_path, permit):
    marker = tmp_path / "must-not-run"
    read_fd, write_fd = os.pipe()
    process = None
    try:
        if permit:
            os.write(write_fd, b"G")
        os.close(write_fd)
        write_fd = None
        process = subprocess.Popen([sys.executable, "-m", "agent_storage_guard", "_child", "--root", str(tmp_path),
                                    "--parent-fd", str(read_fd), "--", sys.executable, "-c",
                                    f"open({str(marker)!r},'w').close()"], pass_fds=(read_fd,),
                                   start_new_session=True)
        assert process.wait(timeout=5) == 75
        assert not marker.exists()
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if process is not None:
            teardown(process)


def cli_command(tmp_path, code, max_runs):
    return [sys.executable, "-m", "agent_storage_guard", "--state", str(tmp_path / "state"),
            "--retirement", str(tmp_path / "retirement"), "run", "--estimate-gib", "0.00390625",
            "--floor-gib", "0", "--max-runs", str(max_runs), "--poll-seconds", ".05",
            "--", sys.executable, "-c", code]


def test_real_cli_cannot_weaken_an_active_concurrency_cap(tmp_path):
    process = subprocess.Popen(cli_command(tmp_path, READY, 1), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    marker = tmp_path / "must-not-start"
    try:
        wait_ready(tmp_path)
        result = subprocess.run(cli_command(tmp_path, f"open({str(marker)!r},'w').close()", 2),
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 75, result.stderr
        assert "concurrent" in result.stderr
        assert not marker.exists()
        assert len(guard.status(tmp_path / "state")["active_runs"]) == 1
    finally:
        teardown(process)


def test_real_cli_nested_run_refuses_stricter_unenforced_concurrency(tmp_path):
    marker = tmp_path / "must-not-start"
    nested = cli_command(tmp_path, f"open({str(marker)!r},'w').close()", 1)
    code = ("import subprocess; "
            f"result=subprocess.run({nested!r},capture_output=True); "
            "assert result.returncode == 75; assert b'nested concurrency' in result.stderr")
    result = subprocess.run(cli_command(tmp_path, code, 2), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert len(list((tmp_path / "state" / "receipts").glob("*.json"))) == 1


def test_real_cli_nested_weaker_cap_does_not_weaken_outer_record(tmp_path):
    nested = cli_command(tmp_path, "pass", 2)
    code = ("import json,os,pathlib,subprocess; "
            f"result=subprocess.run({nested!r},capture_output=True); "
            "assert result.returncode == 0, result.stderr; "
            "record=json.loads(pathlib.Path(os.environ['ASG_STATE_DIR'],'runs.json').read_text())['runs'][0]; "
            "assert record['max_runs'] == 1")
    result = subprocess.run(cli_command(tmp_path, code, 1), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "state" / "receipts").glob("*.json"))) == 1
