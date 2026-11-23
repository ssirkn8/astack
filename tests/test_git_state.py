"""Plain Git facts replace the retired work-unit registry without inventing evidence."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from git_test_support import _project, _run

from astack import git_state


def _linked(project: Path, tmp_path: Path, name: str = "stage") -> Path:
    path = tmp_path / "trees" / name
    _run(["git", "worktree", "add", "-b", name, str(path)], project)
    return path


def test_list_worktrees_reports_native_linked_worktrees(tmp_path: Path) -> None:
    project = _project(tmp_path)
    linked = _linked(project, tmp_path)
    (linked / "notes.md").write_text("work in progress\n")
    records = {Path(item["path"]).resolve(): item for item in git_state.list_worktrees(linked)}
    assert records[project.resolve()]["canonical"] is True
    assert records[project.resolve()]["dirty"] is False
    assert records[linked.resolve()]["canonical"] is False
    assert records[linked.resolve()]["branch"] == "stage"
    assert records[linked.resolve()]["dirty"] is True
    assert git_state.canonical_checkout(linked) == project.resolve()


def test_missing_linked_worktree_state_is_unknown_not_clean(tmp_path: Path) -> None:
    project = _project(tmp_path)
    linked = _linked(project, tmp_path)
    for path in sorted(linked.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink():
            path.chmod(stat.S_IRWXU)
    import shutil

    shutil.rmtree(linked)
    record = next(
        item
        for item in git_state.list_worktrees(project)
        if Path(item["path"]).resolve() == linked.resolve()
    )
    assert record["exists"] is False
    assert record["dirty"] is None


def test_worktree_inventory_failure_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        git_state.list_worktrees(tmp_path)


def test_share_raw_is_dry_run_first_and_links_only_immutable_raw(tmp_path: Path) -> None:
    project = _project(tmp_path)
    linked = _linked(project, tmp_path)
    assert not (linked / "data/raw/source.bin").exists()
    plan = git_state.share_raw_inputs(linked)
    assert plan["status"] == "planned"
    assert plan["links"] == [{"path": "data/raw", "source": str((project / "data/raw").resolve())}]
    assert not (linked / "data/raw").is_symlink()
    applied = git_state.share_raw_inputs(linked, apply=True)
    assert applied["status"] == "applied"
    assert (linked / "data/raw").is_symlink()
    assert (linked / "data/raw/source.bin").read_bytes() == (
        project / "data/raw/source.bin"
    ).read_bytes()
    assert git_state.share_raw_inputs(linked)["links"] == []  # Idempotent.
    for relative in ("data/interim", "data/processed", "reports"):
        assert not (linked / relative).is_symlink()


def test_share_raw_refuses_writable_sources_and_occupied_destinations(tmp_path: Path) -> None:
    project = _project(tmp_path)
    linked = _linked(project, tmp_path)
    raw = project / "data/raw"
    raw.chmod(0o755)
    try:
        with pytest.raises(ValueError, match="read-only"):
            git_state.share_raw_inputs(linked, apply=True)
    finally:
        raw.chmod(0o555)
    assert not (linked / "data/raw").is_symlink()
    (linked / "data/raw").mkdir(parents=True, exist_ok=True)
    (linked / "data/raw/local.csv").write_text("worktree-local\n")
    with pytest.raises(FileExistsError):
        git_state.share_raw_inputs(linked, apply=True)
    assert (linked / "data/raw/local.csv").read_text() == "worktree-local\n"


def test_share_raw_is_not_applicable_in_the_canonical_checkout(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert git_state.share_raw_inputs(project, apply=True)["status"] == "not_applicable"
    assert not (project / "data/raw").is_symlink()
    assert os.access(project / "data/raw/source.bin", os.R_OK)


@pytest.mark.parametrize("occupied", [False, True])
@pytest.mark.parametrize("apply", [False, True])
def test_raw_sharing_rejects_symlinked_destination_parent(
    tmp_path: Path, occupied: bool, apply: bool
) -> None:
    project = _project(tmp_path)
    linked = _linked(project, tmp_path)
    outside = tmp_path / "outside"
    (outside / "raw").mkdir(parents=True)
    if occupied:
        (outside / "raw/owner.txt").write_text("preserve")
    (linked / "data").rename(linked / "data-local")
    (linked / "data").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="ancestor"):
        git_state.share_raw_inputs(linked, apply=apply)
    assert not (outside / "raw").is_symlink()
    assert (outside / "raw").is_dir()
    if occupied:
        assert (outside / "raw/owner.txt").read_text() == "preserve"


def test_raw_sharing_rechecks_parents_between_preview_and_mutation(tmp_path, monkeypatch):
    project = _project(tmp_path)
    linked = _linked(project, tmp_path)
    outside = tmp_path / "outside"
    (outside / "raw").mkdir(parents=True)
    original = git_state.shared_inputs

    def mappings(root):
        yield from original(root)
        (linked / "data").rename(linked / "data-local")
        (linked / "data").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(git_state, "shared_inputs", mappings)
    with pytest.raises(ValueError, match="ancestor"):
        git_state.share_raw_inputs(linked, apply=True)
    assert (outside / "raw").is_dir() and not (outside / "raw").is_symlink()
