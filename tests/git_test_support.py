"""Plain Git project fixtures. Worktrees use native ``git worktree``, never a registry."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from astack.git_state import share_raw_inputs
from astack.scaffold import create_project


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _project(tmp_path: Path) -> Path:
    target = tmp_path / "project"
    create_project(
        target,
        project_name="Worktree Project",
        package_name="worktree_project",
        description="Exercise linked worktrees.",
        intended_use="Test worktree behavior.",
        lock=False,
    )
    raw_file = target / "data/raw/source.bin"
    raw_file.write_bytes(b"x" * (2 * 1024 * 1024))
    raw_file.chmod(0o444)
    raw_file.parent.chmod(0o555)
    _run(["git", "init", "-b", "main"], target)
    _run(["git", "add", "."], target)
    _run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        target,
    )
    _run(["git", "config", "user.name", "Test"], target)
    _run(["git", "config", "user.email", "test@example.com"], target)
    return target


def _project_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    project = _project(tmp_path)
    remote = tmp_path / "remote.git"
    _run(["git", "init", "--bare", str(remote)], tmp_path)
    _run(["git", "remote", "add", "origin", str(remote)], project)
    _run(["git", "push", "-u", "origin", "main"], project)
    _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], remote)
    return project, remote


def create_linked_worktree(project: Path, name: str, root: Path) -> dict[str, Any]:
    """Create a native linked worktree and link only immutable shared raw inputs."""
    path = root / name
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "worktree", "add", "-b", name, str(path)], project)
    shared = share_raw_inputs(path, apply=True)
    return {"worktree": str(path), "branch": name, "shared": shared}


def remove_linked_worktree(project: Path, path: Path) -> None:
    _run(["git", "worktree", "remove", str(path)], project)
