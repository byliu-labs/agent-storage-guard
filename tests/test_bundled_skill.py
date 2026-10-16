"""The portable discovery helper must preserve private data as a coverage gap."""
import json
from pathlib import Path
import subprocess
import sys


SKILL = Path(__file__).resolve().parents[1] / "skills/sweep-computer-disk"


def test_discovery_records_excluded_application_data_and_linked_worktree(tmp_path):
    home = tmp_path / "home"
    repo = home / "projects/demo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit",
                    "--allow-empty", "-qm", "fixture"], check=True)
    linked = tmp_path / "outside-home/linked"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-qb",
                    "linked", str(linked)], check=True)
    private = home / "Library/Application Support/ExampleApp"
    (private / ".git").mkdir(parents=True)
    ledger = tmp_path / "ledger.json"
    result = subprocess.run([sys.executable, str(SKILL / "scripts/discover-worktrees.py"),
                             "--root", str(home), "--exclude", str(private.parent),
                             "--output", str(ledger)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(ledger.read_text())
    assert data["errors"] == []
    assert str(private) not in data["markers"]
    assert any(row["path"] == str(private.parent) for row in data["skipped"])
    assert {row["worktree"] for row in data["worktrees"]} == {str(repo), str(linked)}
    original = ledger.read_bytes()
    repeated = subprocess.run([sys.executable, str(SKILL / "scripts/discover-worktrees.py"),
                               "--root", str(repo), "--output", str(ledger)],
                              capture_output=True, text=True)
    assert repeated.returncode != 0
    assert ledger.read_bytes() == original
