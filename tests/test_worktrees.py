"""Inventory real Git worktrees without changing their contents or metadata."""
from pathlib import Path
import subprocess

from agent_storage_guard import worktrees


def git(path: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *arguments], check=True,
                            capture_output=True, text=True, timeout=10)
    return result.stdout


def test_inventory_discovers_main_and_linked_worktree_with_spaces(tmp_path: Path) -> None:
    repo = tmp_path / "projects" / "main"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture")
    linked = tmp_path / "projects" / "work tree"
    git(repo, "worktree", "add", "-b", "feature", str(linked))
    (linked / "unfinished.txt").write_text("keep this work")
    result = worktrees.inventory([tmp_path / "projects"])
    assert len(result["repositories"]) == 1
    assert {item["path"] for item in result["worktrees"]} == {str(repo), str(linked)}
    assert (linked / "unfinished.txt").read_text() == "keep this work"
    assert not result["issues"]


def test_inventory_does_not_follow_symlinks_and_reports_bounded_discovery(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    for number in range(8):
        (root / str(number)).mkdir()
    result = worktrees.inventory([root], max_entries=3)
    assert result["truncated"]
    assert not result["worktrees"]


def test_missing_root_is_an_explicit_coverage_gap(tmp_path: Path) -> None:
    result = worktrees.inventory([tmp_path / "absent"])
    assert result["issues"] and result["issues"][0]["path"] == str(tmp_path / "absent")


def test_porcelain_parser_handles_newlines_in_paths() -> None:
    rows = worktrees.parse_porcelain(b"worktree /a/path\nwith newline\0HEAD abc\0branch refs/heads/main\0\0")
    assert rows == [{"path": "/a/path\nwith newline", "head": "abc", "branch": "main"}]
