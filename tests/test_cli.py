"""Exercise the installed command interface in a fresh process."""
import json
import subprocess
import sys
from pathlib import Path


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "agent_storage_guard", *args],
                          text=True, capture_output=True, timeout=15)


def test_help_explains_managed_commands() -> None:
    result = invoke("--help")
    assert result.returncode == 0
    assert "run" in result.stdout and "reconcile" in result.stdout
    assert "_child" not in result.stdout


def test_status_is_read_only_before_first_run(tmp_path: Path) -> None:
    state = tmp_path / "absent"
    result = invoke("--state", str(state), "status")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["active_runs"] == []
    assert not state.exists()


def test_invalid_budget_never_starts_command(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ran = tmp_path / "must-not-exist"
    for estimate in ("nan", "inf", "0", "-1"):
        result = invoke("--state", str(state), "run", "--estimate-gib", estimate,
                        "--", sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).touch()", str(ran))
        assert result.returncode != 0
        assert not ran.exists()
        assert "Traceback" not in result.stderr


def test_default_state_allows_local_unix_socket_commands(tmp_path: Path, monkeypatch) -> None:
    import os
    import tempfile
    import uuid
    from agent_storage_guard import cli

    # A short synthetic home makes the default layout, rather than pytest's
    # deeply nested fixture path, the subject of this real socket regression.
    base = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
    synthetic_home = Path(tempfile.mkdtemp(prefix="asg-", dir=base))
    retirement = tmp_path / "retired"
    monkeypatch.delenv("ASG_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: synthetic_home)
    code = ("import os,pathlib,socket; "
            "p=pathlib.Path(os.environ['TMPDIR'])/'ipc'; p.mkdir(); "
            "s=socket.socket(socket.AF_UNIX); s.bind(str(p/'listener.sock')); s.close()")
    monkeypatch.setattr(sys, "argv", ["agent-storage-guard", "--retirement", str(retirement),
                                     "run", "--estimate-gib", ".01", "--floor-gib", "0",
                                     "--", sys.executable, "-c", code])
    try:
        assert cli.main() == 0
        receipts = list(cli.default_state().joinpath("receipts").glob("*.json"))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text())["state"] == "retired"
    finally:
        synthetic_home.rename(tmp_path / ("home-evidence-" + uuid.uuid4().hex))
