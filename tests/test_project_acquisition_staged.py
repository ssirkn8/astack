"""Staged acquisition adds raw inputs without copying or exposing the existing raw tree."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from project_tool_test_support import _git_baseline, _project, project_tool

# Each test builds a real project and runs subprocesses, so these belong in the integration tier.
pytestmark = pytest.mark.integration


def _acquisition(target: Path, script: str, *, staged: str | None = None) -> None:
    config = target / ".astack/project.toml"
    line = "" if staged is None else f"staged = {staged}\n"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f'\n[acquisition]\ncommand = ["python", "scripts/acquire.py"]\n{line}',
        encoding="utf-8",
    )
    (target / "scripts/acquire.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "stage = Path(os.environ.get('ASTACK_ACQUISITION_DIR', 'missing'))\n" + script,
        encoding="utf-8",
    )


def _existing(target: Path) -> Path:
    existing = target / "data/raw/existing.txt"
    existing.write_text("original\n", encoding="utf-8")
    existing.chmod(0o444)
    return existing


def _baseline(target: Path) -> None:
    """Commit the fixture, then make raw read-only, as staged acquisition requires."""
    _git_baseline(target)
    raw = target / "data/raw"
    for path in sorted([raw, *raw.rglob("*")], key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)


def _raw_tree(target: Path) -> dict[str, tuple[int, bytes | None]]:
    raw = target / "data/raw"
    return {
        path.relative_to(raw).as_posix(): (
            stat.S_IMODE(path.stat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted([raw, *raw.rglob("*")])
    }


def test_staged_inputs_land_read_only_with_digests_and_no_raw_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(
        target,
        "(stage / 'new.txt').write_text('new\\n')\n"
        "(stage / 'tiles' / 'a').mkdir(parents=True)\n"
        "(stage / 'tiles' / 'a' / 'one.bin').write_bytes(b'1')\n",
    )
    _baseline(target)
    copied: list[Path] = []
    original = project_tool._clone_or_copy

    def recording(source: Path, destination: Path) -> str:
        copied.append(source)
        return original(source, destination)  # type: ignore[no-any-return]

    monkeypatch.setattr(project_tool, "_clone_or_copy", recording)

    result = project_tool.run_acquisition(target)

    assert result["mode"] == "staged"
    assert result["added_raw_inputs"] == [
        "data/raw/new.txt",
        "data/raw/tiles",
        "data/raw/tiles/a",
        "data/raw/tiles/a/one.bin",
    ]
    assert set(result["added_raw_sha256"]) == {"data/raw/new.txt", "data/raw/tiles/a/one.bin"}
    assert existing.read_text(encoding="utf-8") == "original\n"
    for relative in result["added_raw_sha256"]:
        assert not (target / relative).stat().st_mode & 0o222
    raw = (target / "data/raw").resolve()
    assert not any(raw in source.resolve().parents for source in copied)


def test_a_failed_staged_command_leaves_raw_byte_identical(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _existing(target)
    _acquisition(target, "(stage / 'new.txt').write_text('new\\n')\nraise SystemExit(3)\n")
    _baseline(target)
    before = _raw_tree(target)

    with pytest.raises(RuntimeError, match="Acquisition command failed"):
        project_tool.run_acquisition(target)

    assert _raw_tree(target) == before


def test_a_write_into_read_only_raw_fails_and_changes_nothing(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _existing(target)
    _acquisition(target, "Path('data/raw/sneaky.txt').write_text('x')\n")
    _baseline(target)
    before = _raw_tree(target)

    with pytest.raises(RuntimeError, match="Permission"):
        project_tool.run_acquisition(target)

    assert _raw_tree(target) == before


@pytest.mark.parametrize("writable", ["file", "directory"])
def test_staged_acquisition_refuses_writable_raw_before_running(
    tmp_path: Path, writable: str
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, "Path('ran.txt').write_text('ran')\n")
    _baseline(target)
    if writable == "file":
        existing.chmod(0o644)
    else:
        existing.parent.chmod(0o755)

    with pytest.raises(RuntimeError, match="must be read-only first"):
        project_tool.run_acquisition(target)

    assert not (target / "ran.txt").exists()


def test_staged_hard_links_are_refused_and_never_alias_raw(tmp_path: Path) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, "os.link('data/raw/existing.txt', stage / 'alias.txt')\n")
    _baseline(target)
    before = _raw_tree(target)

    with pytest.raises(RuntimeError, match="must not be hard links"):
        project_tool.run_acquisition(target)

    assert _raw_tree(target) == before
    assert existing.stat().st_nlink == 1


def test_leftover_background_processes_cannot_add_inputs_later(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _acquisition(
        target,
        "import subprocess, sys\n"
        "(stage / 'now.txt').write_text('now')\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'import os, time; time.sleep(1); "
        'open(os.environ["ASTACK_ACQUISITION_DIR"] + "/late.txt", "w").write("late")\'])\n',
    )
    _baseline(target)

    result = project_tool.run_acquisition(target)

    assert result["added_raw_inputs"] == ["data/raw/now.txt"]
    import time

    time.sleep(1.5)
    assert not (target / "data/raw/late.txt").exists()


def test_failed_promotion_never_deletes_a_path_it_did_not_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(
        target,
        "(stage / 'a.txt').write_text('a')\n(stage / 'b.txt').write_text('b')\n",
    )
    _baseline(target)
    original = project_tool._place_staged_file
    calls: list[Path] = []

    def racing(source: Path, destination: Path, created: list) -> None:
        calls.append(destination)
        if len(calls) == 2:
            # Simulate a concurrent rename of an original raw file over a path astack created.
            existing.replace(calls[0])
            raise OSError("injected promotion failure")
        original(source, destination, created)

    monkeypatch.setattr(project_tool, "_place_staged_file", racing)

    with pytest.raises(OSError, match="injected") as caught:
        project_tool.run_acquisition(target)

    assert calls[0].read_text(encoding="utf-8") == "original\n"
    assert any("could not prove it created" in note for note in caught.value.__notes__)
    assert stat.S_IMODE((target / "data/raw").stat().st_mode) & 0o222 == 0


def test_a_failed_copy_leaves_no_partial_raw_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _acquisition(target, "(stage / 'big.bin').write_bytes(b'x' * 4096)\n")
    _baseline(target)

    def no_link(source: object, destination: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    def partial(reader: object, writer: object, length: int = 0) -> None:
        writer.write(b"partial")  # type: ignore[attr-defined]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(shutil, "copyfileobj", partial)

    with pytest.raises(OSError, match="No space"):
        project_tool.run_acquisition(target)

    assert not (target / "data/raw/big.bin").exists()


def test_every_parent_permission_is_restored_and_failures_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    for name in ("one", "two"):
        (target / "data/raw" / name).mkdir()
    _acquisition(
        target,
        "for name in ('one', 'two'):\n"
        "    (stage / name).mkdir()\n"
        "    (stage / name / 'new.txt').write_text(name)\n",
    )
    _baseline(target)
    one = target / "data/raw/one"
    original_chmod = Path.chmod

    def failing(self: Path, mode: int, *args: object, **kwargs: object) -> None:
        if self == one and not mode & 0o200:
            raise PermissionError("injected restore failure")
        original_chmod(self, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "chmod", failing)

    with pytest.raises(RuntimeError, match="Could not restore raw directory permissions.*one"):
        project_tool.run_acquisition(target)

    monkeypatch.setattr(Path, "chmod", original_chmod)
    assert stat.S_IMODE((target / "data/raw/two").stat().st_mode) & 0o222 == 0
    assert stat.S_IMODE((target / "data/raw").stat().st_mode) & 0o222 == 0


def test_staged_paths_never_replace_existing_raw_inputs(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _existing(target)
    _acquisition(
        target,
        "(stage / 'fresh.txt').write_text('fresh\\n')\n"
        "(stage / 'existing.txt').write_text('replacement\\n')\n",
    )
    _baseline(target)
    before = _raw_tree(target)

    with pytest.raises(RuntimeError, match="would replace existing raw inputs.*existing.txt"):
        project_tool.run_acquisition(target)

    assert _raw_tree(target) == before


def test_staged_symlinks_are_refused(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _existing(target)
    _acquisition(target, "(stage / 'link.txt').symlink_to('/etc/hosts')\n")
    _baseline(target)
    before = _raw_tree(target)

    with pytest.raises(RuntimeError, match="regular files or directories"):
        project_tool.run_acquisition(target)

    assert _raw_tree(target) == before


def test_staged_inputs_join_a_read_only_shared_raw_directory(tmp_path: Path) -> None:
    target = _project(tmp_path)
    shared = tmp_path / "shared-raw"
    shared.mkdir()
    (shared / "existing.txt").write_text("original\n", encoding="utf-8")
    (shared / "existing.txt").chmod(0o444)
    raw = target / "data/raw"
    raw.rmdir()
    raw.symlink_to(shared, target_is_directory=True)
    _acquisition(target, "(stage / 'new.txt').write_text('new\\n')\n")
    _baseline(target)
    shared.chmod(0o555)
    try:
        result = project_tool.run_acquisition(target)

        assert result["added_raw_inputs"] == ["data/raw/new.txt"]
        assert (shared / "new.txt").read_text(encoding="utf-8") == "new\n"
        assert stat.S_IMODE(shared.stat().st_mode) == 0o555
        assert raw.is_symlink()
    finally:
        shared.chmod(0o755)


@pytest.mark.parametrize("staged", ["false", '"yes"'])
def test_removed_in_place_mode_is_rejected_with_a_migration_hint(
    tmp_path: Path, staged: str
) -> None:
    target = _project(tmp_path)
    _acquisition(target, "", staged=staged)
    _baseline(target)

    with pytest.raises(
        RuntimeError, match="In-place acquisition was removed.*ASTACK_ACQUISITION_DIR"
    ):
        project_tool.run_acquisition(target)
    # Like acquisition-command, the audit checks acquisition for material analyses.
    config = target / ".astack/project.toml"
    config.write_text(config.read_text().replace('stage = "exploratory"', 'stage = "material"'))
    findings = project_tool.audit(target)["findings"]
    assert any(item["check"] == "acquisition-staged" for item in findings)


def test_legacy_staged_true_is_still_accepted(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _acquisition(target, "(stage / 'new.txt').write_text('new\\n')\n", staged="true")
    _baseline(target)

    assert project_tool.run_acquisition(target)["added_raw_inputs"] == ["data/raw/new.txt"]


def test_scratch_is_on_the_same_device_as_raw(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _git_baseline(target)

    work = Path(project_tool._acquisition_work_directory(target))

    assert work.stat().st_dev == (target / "data/raw").stat().st_dev
    # Inside the Git metadata or outside the project, never in the tracked worktree.
    relative = (
        work.resolve().relative_to(target.resolve())
        if work.resolve().is_relative_to(target.resolve())
        else None
    )
    assert relative is None or relative.parts[0] == ".git"


def test_clone_or_copy_never_replaces_an_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")
    destination = tmp_path / "destination.bin"
    destination.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        project_tool._clone_or_copy(source, destination)

    assert destination.read_bytes() == b"keep"


def test_clone_or_copy_preserves_bytes_and_mode(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload" * 1000)
    source.chmod(0o444)
    destination = tmp_path / "destination.bin"

    method = project_tool._clone_or_copy(source, destination)

    assert method in {"clone", "copy"}
    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o444


def test_nested_raw_symlinks_are_refused_before_running(tmp_path: Path) -> None:
    target = _project(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("external\n", encoding="utf-8")
    (target / "data/raw/input.txt").symlink_to(outside)
    _acquisition(target, "Path('data/raw/input.txt').write_text('changed\\n')\n")
    _baseline(target)

    with pytest.raises(RuntimeError, match="not symlinks.*input.txt"):
        project_tool.run_acquisition(target)

    assert outside.read_text(encoding="utf-8") == "external\n"


def test_a_failed_promotion_removes_files_it_already_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    (target / "data/raw/tiles").mkdir()
    _acquisition(
        target,
        "(stage / 'tiles').mkdir()\n"
        "(stage / 'tiles' / 'a.bin').write_bytes(b'a')\n"
        "(stage / 'tiles' / 'b.bin').write_bytes(b'b')\n",
    )
    _baseline(target)
    before = _raw_tree(target)
    original = project_tool._place_staged_file

    def failing(source: Path, destination: Path, created: list) -> None:
        if destination.name == "b.bin":
            raise OSError("injected promotion failure")
        original(source, destination, created)

    monkeypatch.setattr(project_tool, "_place_staged_file", failing)

    with pytest.raises(OSError, match="injected"):
        project_tool.run_acquisition(target)

    assert _raw_tree(target) == before


def test_relocating_raw_into_scratch_deletes_nothing_and_keeps_scratch(tmp_path: Path) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, "stage.rmdir()\nPath('data').rename(stage)\n")
    _baseline(target)
    inode = existing.stat().st_ino

    with pytest.raises(
        RuntimeError, match="deleted nothing and kept its scratch directory"
    ) as caught:
        project_tool.run_acquisition(target)

    scratch = Path(str(caught.value).split("scratch directory at ")[1].split(", which")[0])
    relocated = scratch / "staged/raw/existing.txt"
    assert relocated.read_text(encoding="utf-8") == "original\n"
    assert relocated.stat().st_ino == inode
    relocated.parent.chmod(0o755)
    shutil.rmtree(scratch)


@pytest.mark.parametrize(
    "script",
    [
        # Start succeeds, then reading its output fails.
        "stage.rmdir()\nPath('data').rename(stage)\n(stage.parent / 'stdout').unlink()\n",
        # Raw looks identical through a link to the relocated tree.
        "stage.rmdir()\nPath('data').rename(stage)\n"
        "Path('data').symlink_to(stage, target_is_directory=True)\n",
    ],
    ids=["output-error", "ancestor-symlink"],
)
def test_relocated_raw_survives_every_cleanup_path(tmp_path: Path, script: str) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, script)
    _baseline(target)
    inode = existing.stat().st_ino
    work = Path(project_tool._acquisition_work_directory(target))

    with pytest.raises((RuntimeError, OSError)):
        project_tool.run_acquisition(target)

    survivors = [
        path
        for path in work.rglob("existing.txt")
        if path.stat().st_ino == inode and path.read_text(encoding="utf-8") == "original\n"
    ]
    assert survivors, "the original raw input was deleted"
    for scratch in work.glob("astack-acquisition-*"):
        for path in [scratch, *scratch.rglob("*")]:
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        shutil.rmtree(scratch)


def test_raw_moved_into_scratch_after_validation_is_never_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A surviving worker can relocate raw after the checks. Cleanup must still keep it."""
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, "(stage / 'new.txt').write_text('new\\n')\n")
    _baseline(target)
    inode = existing.stat().st_ino
    original = project_tool._promote_staged_inputs

    def late_worker(root: Path, staging: Path, protected: set) -> list[str]:
        added = original(root, staging, protected)
        (root / "data").rename(staging / "relocated")
        return added  # type: ignore[no-any-return]

    monkeypatch.setattr(project_tool, "_promote_staged_inputs", late_worker)

    with pytest.raises(RuntimeError, match="kept in place") as caught:
        project_tool.run_acquisition(target)

    kept = Path(str(caught.value).split("kept in place: ")[1].split(", ")[0])
    survivor = kept / "existing.txt" if kept.is_dir() else kept
    assert survivor.stat().st_ino == inode
    assert survivor.read_text(encoding="utf-8") == "original\n"


def test_rollback_never_deletes_raw_moved_into_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, "Path('stray.txt').write_text('stray')\n")
    _baseline(target)
    inode = existing.stat().st_ino
    original = project_tool._prohibited_acquisition_changes

    def late_worker(root: Path, before: dict) -> list[str]:
        prohibited = original(root, before)
        (root / "data").rename(root / "moved")
        return prohibited  # type: ignore[no-any-return]

    monkeypatch.setattr(project_tool, "_prohibited_acquisition_changes", late_worker)

    with pytest.raises(RuntimeError, match="prohibited") as caught:
        project_tool.run_acquisition(target)

    assert any("original raw inputs" in note for note in caught.value.__notes__)
    survivor = target / "moved/raw/existing.txt"
    assert survivor.stat().st_ino == inode


def test_failed_promotion_keeps_an_original_moved_over_a_staged_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(
        target, "(stage / 'a.bin').write_bytes(b'a')\n(stage / 'b.bin').write_bytes(b'b')\n"
    )
    _baseline(target)
    inode = existing.stat().st_ino
    original = project_tool._place_staged_file

    def racing(source: Path, destination: Path, created: list) -> None:
        if source.name == "b.bin":
            # The worker then removes the staging name, so raw/a.bin is the original's last name.
            (source.parent / "a.bin").unlink()
            raise OSError("injected promotion failure")
        # A surviving worker moves the original over the staged source while raw is unlocked.
        existing.replace(source)
        original(source, destination, created)

    monkeypatch.setattr(project_tool, "_place_staged_file", racing)

    with pytest.raises(OSError, match="injected") as caught:
        project_tool.run_acquisition(target)

    assert any("data/raw/a.bin" in note for note in caught.value.__notes__)
    survivor = target / "data/raw/a.bin"
    assert survivor.stat().st_ino == inode
    assert survivor.read_text(encoding="utf-8") == "original\n"


def test_staged_acquisition_refuses_raw_it_cannot_fully_list(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "data/raw/locked").mkdir()
    (target / "data/raw/locked/inside.txt").write_text("hidden\n", encoding="utf-8")
    _acquisition(target, "Path('ran.txt').write_text('ran')\n")
    _baseline(target)
    (target / "data/raw/locked").chmod(0)
    try:
        with pytest.raises(RuntimeError, match="cannot list"):
            project_tool.run_acquisition(target)
        assert not (target / "ran.txt").exists()
    finally:
        (target / "data/raw/locked").chmod(0o555)


def test_cleanup_errors_never_hide_where_an_original_was_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "scratch"
    tree.mkdir()
    original_file = tree / "a-original.txt"
    original_file.write_text("original\n", encoding="utf-8")
    (tree / "b-stuck.txt").write_text("x", encoding="utf-8")
    identity = original_file.stat()
    real_unlink = Path.unlink

    def failing(self: Path, missing_ok: bool = False) -> None:
        if self.name == "b-stuck.txt":
            raise PermissionError("injected cleanup failure")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing)
    kept: list[str] = []
    errors: list[str] = []

    project_tool._remove_unprotected(tree, {(identity.st_dev, identity.st_ino)}, kept, errors)

    assert kept == [str(original_file)]
    assert any("injected cleanup failure" in error for error in errors)
    assert original_file.read_text(encoding="utf-8") == "original\n"


def test_rollback_never_replaces_a_tracked_path_that_reappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(target, "Path('README.md').write_text('changed')\n")
    _baseline(target)
    inode = existing.stat().st_ino
    original = project_tool._remove_unprotected

    def late_worker(path: Path, protected: set, kept: list, errors: list) -> None:
        original(path, protected, kept, errors)
        if path.name == "README.md":
            # A surviving worker moves raw onto the path rollback just cleared.
            (target / "data").rename(path)

    monkeypatch.setattr(project_tool, "_remove_unprotected", late_worker)

    with pytest.raises(RuntimeError, match="prohibited") as caught:
        project_tool.run_acquisition(target)

    assert any("README.md" in note and "left in place" in note for note in caught.value.__notes__)
    survivor = target / "README.md/raw/existing.txt"
    assert survivor.stat().st_ino == inode


def test_a_kept_original_on_success_still_reports_cleanup_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _existing(target)
    _acquisition(target, "(stage / 'new.txt').write_text('new')\n")
    _baseline(target)
    original = project_tool._promote_staged_inputs
    real_unlink = Path.unlink

    def late_worker(root: Path, staging: Path, protected: set) -> list[str]:
        added = original(root, staging, protected)
        (root / "data").rename(staging / "relocated")
        (staging / "stuck.txt").write_text("x")
        return added  # type: ignore[no-any-return]

    def failing(self: Path, missing_ok: bool = False) -> None:
        if self.name == "stuck.txt":
            raise PermissionError("injected cleanup failure")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(project_tool, "_promote_staged_inputs", late_worker)
    monkeypatch.setattr(Path, "unlink", failing)

    with pytest.raises(RuntimeError, match="kept in place") as caught:
        project_tool.run_acquisition(target)

    assert any("injected cleanup failure" in note for note in caught.value.__notes__)


def test_the_digest_cache_never_replaces_a_relocated_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    existing = _existing(target)
    _acquisition(
        target,
        "cache = os.environ['CACHE_TARGET']\n"
        "os.makedirs(os.path.dirname(cache), exist_ok=True)\n"
        "os.chmod('data/raw', 0o755)\n"
        "os.replace('data/raw/existing.txt', cache)\n",
    )
    _baseline(target)
    cache = Path(project_tool._digest_cache_path(target))
    monkeypatch.setenv("CACHE_TARGET", str(cache))
    inode = existing.stat().st_ino

    with pytest.raises(RuntimeError, match="deleted nothing"):
        project_tool.run_acquisition(target)

    assert cache.stat().st_ino == inode
    assert cache.read_text(encoding="utf-8") == "original\n"


def _external_raw(tmp_path: Path, target: Path) -> Path:
    """Point data/raw at a directory outside the project, as a shared raw store does."""
    external = tmp_path / "external" / "raw"
    external.mkdir(parents=True)
    raw = target / "data/raw"
    raw.rmdir()
    raw.symlink_to(external, target_is_directory=True)
    return external


def test_a_scratch_symlink_into_the_worktree_is_never_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    external = _external_raw(tmp_path, target)
    (external.parent / ".astack-acquisition").symlink_to(target / "reports")
    _acquisition(target, "(stage / 'new.txt').write_text('new\\n')\n")
    _baseline(target)

    def no_state(*_: object) -> Path:
        raise RuntimeError("no Git state directory")

    # Without the state directory, the beside-raw candidate is tried first.
    monkeypatch.setattr(project_tool, "state_path", no_state)

    candidates = project_tool._acquisition_scratch_candidates(target)

    assert all(not c.resolve().is_relative_to((target / "reports").resolve()) for c in candidates)
    result = project_tool.run_acquisition(target)
    assert result["added_raw_inputs"] == ["data/raw/new.txt"]


def test_an_unwritable_scratch_candidate_falls_through_to_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    external = _external_raw(tmp_path, target)
    blocked = external.parent / ".astack-acquisition"
    blocked.mkdir()
    blocked.chmod(0o555)
    _acquisition(target, "(stage / 'new.txt').write_text('new\\n')\n")
    _baseline(target)

    def no_state(*_: object) -> Path:
        raise RuntimeError("no Git state directory")

    # Without the state directory, the beside-raw candidate is tried first.
    monkeypatch.setattr(project_tool, "state_path", no_state)
    try:
        assert project_tool._acquisition_scratch_candidates(target)[0] == blocked.resolve()
        result = project_tool.run_acquisition(target)
        assert result["added_raw_inputs"] == ["data/raw/new.txt"]
        assert not any(blocked.iterdir())
    finally:
        blocked.chmod(0o755)
