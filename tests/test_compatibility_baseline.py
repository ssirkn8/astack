"""Executable extraction boundaries, with release evidence captured outside the test run."""

import shutil
from pathlib import Path

import pytest

from astack.scaffold import create_project

FIXTURES = Path(__file__).parent / "fixtures" / "compatibility"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_existing_node_repository_reproduces_scaffold_refusal_without_changes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "application"
    shutil.copytree(FIXTURES / "existing-node", target)
    before = _snapshot(target)
    with pytest.raises(FileExistsError, match="Target is not empty"):
        create_project(
            target,
            project_name="Application",
            package_name="application",
            description="Existing software",
            lock=False,
        )
    assert _snapshot(target) == before
