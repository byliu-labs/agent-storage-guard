"""Bounded, read-only Git worktree discovery; names never grant cleanup authority."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

from .ownership import no_symlinks


def parse_porcelain(payload: bytes) -> list[dict]:
    rows, current = [], {}
    for field in payload.split(b"\0"):
        if not field:
            if current:
                rows.append(current)
                current = {}
            continue
        name, _, value = field.partition(b" ")
        text = os.fsdecode(value)
        if name == b"worktree":
            current["path"] = text
        elif name in {b"HEAD", b"branch"}:
            current[name.decode().lower()] = text.removeprefix("refs/heads/")
        elif name in {b"bare", b"detached", b"locked", b"prunable"}:
            current[name.decode()] = text or True
    if current:
        rows.append(current)
    return rows


def _git(path: Path, arguments: list[str], timeout: float) -> bytes:
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(path), *arguments],
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"git exited {result.returncode}; repository may be inaccessible")
    return result.stdout


def inventory(roots: list[Path], max_depth: int = 4, max_entries: int = 10000,
              timeout: float = 5, max_repositories: int = 64,
              total_seconds: float = 30) -> dict:
    if max_depth < 0 or min(max_entries, max_repositories, timeout, total_seconds) <= 0:
        raise ValueError("inventory bounds must be positive; depth must be nonnegative")
    result = {"repositories": [], "worktrees": [], "issues": [], "truncated": False}
    deadline, examined = time.monotonic() + total_seconds, 0
    seen_directories, seen_repositories, seen_worktrees = set(), set(), set()
    stack = [(Path(root).absolute(), 0) for root in roots]
    excluded = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cache"}
    while stack:
        if examined >= max_entries or time.monotonic() >= deadline:
            result["truncated"] = True
            break
        path, depth = stack.pop()
        try:
            if path.is_symlink():
                continue
            no_symlinks(path)
            identity = path.stat()
            key = (identity.st_dev, identity.st_ino)
            if key in seen_directories:
                continue
            seen_directories.add(key)
            marker = path / ".git"
            is_repository = (not marker.is_symlink() and marker.exists())
            if is_repository:
                remaining = min(timeout, max(.01, deadline - time.monotonic()))
                common = os.fsdecode(_git(path, ["rev-parse", "--path-format=absolute", "--git-common-dir"], remaining)).strip()
                if common not in seen_repositories:
                    if len(seen_repositories) >= max_repositories:
                        result["truncated"] = True
                        break
                    seen_repositories.add(common)
                    rows = parse_porcelain(_git(path, ["worktree", "list", "--porcelain", "-z"], remaining))
                    result["repositories"].append({"path": str(path), "git_common_dir": common})
                    for row in rows:
                        if row.get("path") not in seen_worktrees:
                            seen_worktrees.add(row.get("path"))
                            result["worktrees"].append(row)
            if depth < max_depth:
                with os.scandir(path) as entries:
                    for entry in entries:
                        examined += 1
                        if examined >= max_entries or time.monotonic() >= deadline:
                            result["truncated"] = True
                            break
                        if entry.name not in excluded and entry.is_dir(follow_symlinks=False):
                            stack.append((Path(entry.path), depth + 1))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            result["issues"].append({"path": str(path), "reason": str(error)})
    result["worktrees"].sort(key=lambda row: row.get("path", ""))
    result["repositories"].sort(key=lambda row: row["path"])
    return result
