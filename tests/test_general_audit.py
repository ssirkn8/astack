"""A General project gets the checks that apply to any repository, not the data science layout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from git_tree_cache_test_support import copy_cached_git_tree
from project_tool_test_support import _project, project_tool

from astack import installation as install


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def _commit(root: Path) -> None:
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Fixture state",
    )


def _general(tmp_path: Path, check: list[list[str]] | None = None) -> Path:
    commands = check or [[sys.executable, "-c", "pass"]]

    def build(parent: Path) -> Path:
        root = parent / "application"
        shutil.copytree(Path("tests/fixtures/compatibility/existing-node"), root)
        _git(root, "init", "-b", "main")
        _commit(root)
        plan = install.install_project(root, candidate=True, commands=commands)
        assert not plan["conflicts"]
        install.install_project(
            root, candidate=True, commands=commands, apply=True, approve_plan=plan["plan_digest"]
        )
        _commit(root)
        return root

    return copy_cached_git_tree(f"general:{json.dumps(commands)}", tmp_path, build)


def _blocking(result: dict[str, Any]) -> set[str]:
    return {item["check"] for item in result["findings"] if item["priority"] in {"P0", "P1"}}


def test_a_general_project_is_not_asked_for_data_science_files(tmp_path: Path) -> None:
    root = _general(tmp_path)

    project_tool.audit(root, run_tools=True, strict=True)
    # The first run writes run records. They must not dirty the worktree for the next run.
    result = project_tool.audit(root, run_tools=True, strict=True)

    assert _blocking(result) == set()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    )
    assert status.stdout == ""
    assert result["verdicts"]["software_checks"] == "pass"
    assert result["verdicts"]["computational_reproducibility"] == "not-assessed"
    assert result["verdicts"]["release_readiness"] == "not-assessed"
    assert any("science checks" in note for note in result["not_assessed"])


def test_a_general_project_runs_its_own_check_command(tmp_path: Path) -> None:
    root = _general(tmp_path, [[sys.executable, "-c", "raise SystemExit(3)"]])

    result = project_tool.audit(root, run_tools=True, strict=True)

    assert result["verdicts"]["software_checks"] == "fail"
    assert not any("just" in str(item["evidence"]) for item in result["findings"])


@pytest.mark.parametrize("opt_in", ["capability", "sources"])
def test_a_general_project_that_opts_into_science_gets_the_science_checks(
    tmp_path: Path, opt_in: str
) -> None:
    root = _general(tmp_path)
    config = root / ".astack/project.toml"
    if opt_in == "capability":
        text = config.read_text(encoding="utf-8")
        config.write_text(
            text.replace(
                'profile = "general"\n',
                'profile = "general"\ncapabilities = ["scientific-review"]\n',
            ),
            encoding="utf-8",
        )
    else:
        (root / "data").mkdir(exist_ok=True)
        (root / "data/sources.yml").write_text("schema_version: 1\nsources: []\n", encoding="utf-8")
    _commit(root)

    blocking = _blocking(project_tool.audit(root))

    assert "analysis-stage" in blocking
    # Science does not bring the Python layout with it.
    assert not {"dependency-lock", "package-install", "ship-ready"} & blocking


def test_a_project_without_a_profile_keeps_every_data_science_check(tmp_path: Path) -> None:
    root = _project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace('profile = "data-science"\n', ""),
        encoding="utf-8",
    )
    (root / "uv.lock").unlink()

    assert "dependency-lock" in _blocking(project_tool.audit(root))


def _set_config(root: Path, old: str, new: str) -> None:
    config = root / ".astack/project.toml"
    text = config.read_text(encoding="utf-8")
    assert old in text
    config.write_text(text.replace(old, new, 1), encoding="utf-8")
    _commit(root)


def test_a_scientific_review_declaration_opts_into_science(tmp_path: Path) -> None:
    root = _general(tmp_path)
    _set_config(
        root, 'profile = "general"\n', 'profile = "general"\n\n[review]\nscientific = true\n'
    )

    assert "analysis-stage" in _blocking(project_tool.audit(root, run_tools=True, strict=True))


def test_audit_accepts_exactly_the_commands_validate_accepts(tmp_path: Path) -> None:
    # An empty argument after the executable is valid for `validate check`.
    root = _general(
        tmp_path, [[sys.executable, "-c", "import sys; sys.exit(sys.argv[1:] != [''])", ""]]
    )

    result = project_tool.audit(root, run_tools=True, strict=True)

    assert result["verdicts"]["software_checks"] == "pass"


def test_a_nul_byte_in_a_command_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    root = _general(tmp_path)
    _set_config(root, '"-c", "pass"', '"-c", "pass\\u0000"')

    result = project_tool.audit(root, run_tools=True, strict=True)

    assert "software-config" in _blocking(result)
    assert result["verdicts"]["software_checks"] == "fail"


def test_the_session_plans_only_the_commands_a_general_project_runs(tmp_path: Path) -> None:
    root = _general(tmp_path)

    session = project_tool.audit(root, run_tools=True, strict=True)["validation_session"]

    assert session["status"] == "passed"
    assert all(stage["status"] == "passed" for stage in session["stages"]), session["stages"]


def test_ci_mode_needs_no_source_declaration_without_science(tmp_path: Path) -> None:
    root = _general(tmp_path)

    result = project_tool.audit(root, run_tools=True, strict=True, ci=True)

    assert _blocking(result) == set()


def test_the_cache_ignore_leaves_owner_files_visible(tmp_path: Path) -> None:
    root = _general(tmp_path)
    (root / ".cache").mkdir()
    (root / ".cache/owner-file.txt").write_text("mine\n", encoding="utf-8")

    project_tool.audit(root, run_tools=True)

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.splitlines() == ["?? .cache/owner-file.txt"]


def test_reproduce_keeps_its_first_record_out_of_git(tmp_path: Path, monkeypatch) -> None:
    root = _general(tmp_path)

    def fake(cwd: Path, **_: object) -> dict[str, str]:
        # Like run_reproduction: the worktree must be clean before the record is written.
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, check=True, capture_output=True, text=True
        )
        assert status.stdout == ""
        record = cwd / ".cache/runs/project/record.json"
        record.parent.mkdir(parents=True)
        record.write_text("{}\n", encoding="utf-8")
        return {"status": "command-succeeded"}

    monkeypatch.setattr(project_tool, "run_reproduction", fake)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["workflow.py", "reproduce"])
    project_tool.main()

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def _status(root: Path) -> list[str]:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--ignored"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def test_the_audit_keeps_its_reports_out_of_git_without_editing_gitignore(tmp_path: Path) -> None:
    root = _general(tmp_path)
    ignore = root / ".gitignore"
    before = ignore.read_bytes() if ignore.exists() else None

    result = project_tool.audit(root, run_tools=True, strict=True)
    (root / "reports/audits/pending.md").write_text("# Audit\n", encoding="utf-8")

    assert "audit-report-ignore" not in _blocking(result)
    assert (ignore.read_bytes() if ignore.exists() else None) == before
    status = _status(root)
    assert "!! reports/audits/pending.md" in status
    assert not [line for line in status if not line.startswith("!! ")]


def test_tracked_audit_files_keep_the_finding(tmp_path: Path) -> None:
    root = _general(tmp_path)
    (root / "reports/audits").mkdir(parents=True)
    (root / "reports/audits/kept.md").write_text("mine\n", encoding="utf-8")
    _commit(root)

    result = project_tool.audit(root)

    assert "audit-report-ignore" in _blocking(result)
    assert not (root / "reports/audits/.gitignore").exists()


def test_a_project_that_already_ignores_reports_gets_no_extra_file(tmp_path: Path) -> None:
    root = _project(tmp_path)

    project_tool.audit(root)

    assert not (root / "reports/audits/.gitignore").exists()


def test_untracked_owner_files_in_the_audit_folder_stay_visible(tmp_path: Path) -> None:
    root = _general(tmp_path)
    (root / "reports/audits").mkdir(parents=True)
    (root / "reports/audits/owner-notes.md").write_text("mine\n", encoding="utf-8")

    result = project_tool.audit(root)

    assert "audit-report-ignore" in _blocking(result)
    assert not (root / "reports/audits/.gitignore").exists()
    assert "?? reports/audits/owner-notes.md" in _status(root)


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_the_ignore_file_is_never_written_through_a_link(tmp_path: Path) -> None:
    root = _general(tmp_path)
    owner_ignore = root / ".gitignore"
    owner_ignore.write_text("/owner-rule/\n", encoding="utf-8")
    (root / "reports/audits").mkdir(parents=True)
    # A link that appears after the helper's checks must not redirect the write.
    (root / "reports/audits/.gitignore").symlink_to(owner_ignore)

    with pytest.raises(OSError):
        project_tool._create_new_file(root, ("reports", "audits", ".gitignore"), "*\n")

    assert owner_ignore.read_text(encoding="utf-8") == "/owner-rule/\n"


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_a_linked_reports_folder_is_never_written_into(tmp_path: Path) -> None:
    root = _general(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "reports").symlink_to(outside, target_is_directory=True)

    project_tool.audit(root)
    with pytest.raises(OSError):
        project_tool._create_new_file(root, ("reports", "audits", ".gitignore"), "*\n")

    assert list(outside.iterdir()) == []


def test_an_owner_file_saved_during_the_checks_stays_visible(tmp_path: Path, monkeypatch) -> None:
    root = _general(tmp_path)
    real = project_tool._create_new_file

    def save_then_create(base: Path, parts: tuple[str, ...], *args: Any, **kwargs: Any) -> None:
        if parts[0] == "reports":
            # The owner saves a file after the folder was checked and found empty.
            (root / "reports/audits").mkdir(parents=True, exist_ok=True)
            (root / "reports/audits/owner-notes.md").write_text("mine\n", encoding="utf-8")
        real(base, parts, *args, **kwargs)

    # Patch where the helper looks the writer up, not the re-export.
    monkeypatch.setattr(sys.modules[real.__module__], "_create_new_file", save_then_create)
    result = project_tool.audit(root)

    assert "audit-report-ignore" in _blocking(result)
    assert not (root / "reports/audits/.gitignore").exists()
    assert "?? reports/audits/owner-notes.md" in _status(root)


def test_audit_reports_are_not_ignored_where_links_cannot_be_refused(
    tmp_path: Path, monkeypatch
) -> None:
    root = _general(tmp_path)
    monkeypatch.setattr(os, "supports_dir_fd", set())

    result = project_tool.audit(root)

    assert "audit-report-ignore" in _blocking(result)
    assert not (root / "reports/audits/.gitignore").exists()
    # The run records are not sensitive, so they still get their ignore file.
    assert (root / ".cache/.gitignore").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_giving_the_folder_back_never_follows_a_swapped_in_link(
    tmp_path: Path, monkeypatch
) -> None:
    root = _general(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".gitignore").write_text("/owner-rule/\n", encoding="utf-8")
    (outside / "other.txt").write_text("other\n", encoding="utf-8")
    real_listdir = os.listdir
    swapped: list[bool] = []

    def swap_then_list(path: Any = ".") -> list[str]:
        # The first listing of the audit folder, by handle or by path, comes after the write.
        if not swapped and (isinstance(path, int) or str(path).endswith("reports/audits")):
            swapped.append(True)
            # Right after the write, the folder is moved away, gains an owner file, and a link
            # to an outside folder takes its place.
            moved = root / "reports/moved"
            (root / "reports/audits").rename(moved)
            (moved / "owner-notes.md").write_text("mine\n", encoding="utf-8")
            (root / "reports/audits").symlink_to(outside, target_is_directory=True)
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", swap_then_list)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {swap_then_list})
    project_tool._ensure_audit_reports_ignored(root)
    monkeypatch.setattr(os, "listdir", real_listdir)

    assert swapped
    assert (outside / ".gitignore").read_text(encoding="utf-8") == "/owner-rule/\n"
    assert not (root / "reports/moved/.gitignore").exists()
