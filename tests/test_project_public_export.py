"""A project's public export is deterministic, allowlisted, history-free, and source-gated."""

from __future__ import annotations

import subprocess

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    yaml,  # noqa: F401
)

pytestmark = pytest.mark.integration


def _known(value: object) -> dict[str, object]:
    return {"status": "known", "value": value}


def _configure(target: Path, include: list[str], extra: str = "") -> None:
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[public_export]\ninclude = "
        + repr(include).replace("'", '"')
        + "\n"
        + extra,
        encoding="utf-8",
    )


def _sources(target: Path, allowed: list[str]) -> None:
    record = {
        "title": "Survey",
        "path": "data/manual/survey.csv",
        "redistribution": _known("CC-BY-4.0 permits redistribution"),
        "allowed_outputs": allowed,
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"survey": record}}), encoding="utf-8"
    )


def _commit(target: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "x"],
        cwd=target,
        check=True,
        capture_output=True,
    )


def _files(directory: Path) -> list[str]:
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file())


def test_export_copies_only_allowlisted_tracked_files_without_history(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(target, ["README.md", "src/**"])
    _git_baseline(target)
    out = tmp_path / "export"

    result = project_tool.export_public(target, out)

    assert result["status"] == "exported"
    assert not (out / ".git").exists()
    assert "README.md" in _files(out)
    assert all(path == "README.md" or path.startswith("src/") for path in _files(out))
    assert result["files"] == _files(out)


def test_export_is_deterministic(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(target, ["README.md", "src/**"])
    _git_baseline(target)

    first = project_tool.export_public(target, tmp_path / "one")
    second = project_tool.export_public(target, tmp_path / "two")

    assert first["export_sha256"] == second["export_sha256"]


def test_export_requires_an_allowlist(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="public_export"):
        project_tool.export_public(target, tmp_path / "export")


def test_export_refuses_a_dirty_tree(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(target, ["README.md"])
    _git_baseline(target)
    (target / "notes.txt").write_text("untracked\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="clean"):
        project_tool.export_public(target, tmp_path / "export")


@pytest.mark.parametrize("where", ["inside", "nonempty"])
def test_export_destination_must_be_new_and_outside(tmp_path: Path, where: str) -> None:
    target = _project(tmp_path)
    _configure(target, ["README.md"])
    _git_baseline(target)
    out = target / "public" if where == "inside" else tmp_path / "export"
    if where == "nonempty":
        out.mkdir()
        (out / "keep.txt").write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="outside the project|empty"):
        project_tool.export_public(target, out)


def test_private_paths_are_never_exported_even_if_allowlisted(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "docs/work-items/plan.md").write_text("internal\n", encoding="utf-8")
    _configure(target, ["docs/**"])
    _git_baseline(target)

    result = project_tool.export_public(target, tmp_path / "export")

    assert not any(path.startswith("docs/work-items/") for path in result["files"])
    assert "docs/work-items/plan.md" in result["refused_private"]


def test_data_requires_source_redistribution_and_public_export_permission(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    (target / "data/manual/survey.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    _sources(target, ["internal-analysis"])
    _configure(target, ["data/manual/**"])
    (target / ".gitignore").write_text("", encoding="utf-8")
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="public-export"):
        project_tool.export_public(target, tmp_path / "export")
    assert not (tmp_path / "export").exists()

    _sources(target, ["internal-analysis", "public-export"])
    _commit(target)
    result = project_tool.export_public(target, tmp_path / "export")
    assert "data/manual/survey.csv" in result["files"]


def test_derived_outputs_need_every_source_to_allow_public_export(tmp_path: Path) -> None:
    target = _project(tmp_path)
    figure = target / "reports/figures/analysis/figure.svg"
    figure.parent.mkdir(parents=True, exist_ok=True)
    figure.write_text("<svg/>\n", encoding="utf-8")
    _sources(target, ["derived-figures"])
    _configure(target, ["reports/figures/**"])
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="survey"):
        project_tool.export_public(target, tmp_path / "export")


def test_secret_in_an_allowlisted_file_blocks_the_export(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "src/config.py").write_text('api_key = "abcdefghijklmnopqrstuvwxyz"\n', "utf-8")
    _configure(target, ["src/**"])
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="src/config.py") as error:
        project_tool.export_public(target, tmp_path / "export")

    assert "abcdefghijklmnop" not in str(error.value)
    assert not (tmp_path / "export").exists()


def test_project_secret_scan_hook_must_pass(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(
        target,
        ["README.md"],
        'scan = [["python", "-c", "import sys; sys.exit(0 if sys.argv[1] else 1)", "{out}"]]\n',
    )
    _git_baseline(target)
    assert project_tool.export_public(target, tmp_path / "ok")["scans"] == 1

    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("sys.exit(0 if", "sys.exit(3 if"),
        encoding="utf-8",
    )
    _commit(target)
    with pytest.raises(RuntimeError, match="scan"):
        project_tool.export_public(target, tmp_path / "blocked")
    assert not (tmp_path / "blocked").exists()


def test_symlinks_are_refused(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "src/link.py").symlink_to("../README.md")
    _configure(target, ["src/**"])
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="symlink"):
        project_tool.export_public(target, tmp_path / "export")


@pytest.mark.parametrize(
    "redistribution",
    [
        "unknown",
        "Redistribution prohibited",
        "CC-BY-4.0 permits redistribution",  # a plain string is not structured known evidence
        {"status": "known"},
        {"status": "unknown"},
        {"status": "known", "value": "Redistribution permission denied"},
        {"status": "known", "value": "not_yet_verified"},
    ],
)
def test_unresolved_or_prohibited_redistribution_never_authorizes_export(
    tmp_path: Path, redistribution: object
) -> None:
    target = _project(tmp_path)
    (target / "data/manual/survey.csv").write_text("a\n", encoding="utf-8")
    record = {
        "title": "Survey",
        "path": "data/manual/survey.csv",
        "redistribution": redistribution,
        "allowed_outputs": ["public-export"],
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"survey": record}}), encoding="utf-8"
    )
    _configure(target, ["data/manual/**"])
    (target / ".gitignore").write_text("", encoding="utf-8")
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="public-export"):
        project_tool.export_public(target, tmp_path / "export")


def test_governed_outputs_need_at_least_one_permitting_source(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "reports/result.csv").write_text("a\n", encoding="utf-8")
    _configure(target, ["reports/**"])
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="no source"):
        project_tool.export_public(target, tmp_path / "export")


@pytest.mark.parametrize("path", ["Data/Raw/private.csv", "SCRATCH/notes.txt", "Docs/Work-Items/x"])
def test_private_checks_ignore_case(path: str) -> None:
    assert project_tool._export_private(path)


@pytest.mark.parametrize(
    "path", ["Data/Manual/survey.csv", "REPORTS/result.csv", "Evidence/x.json"]
)
def test_governed_classification_ignores_case(path: str) -> None:
    assert project_tool._derived_export_path(path)


def test_env_variants_are_never_exported(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / ".env.production").write_text("DATABASE_URL=postgres://u:p@h/db\n", "utf-8")
    (target / ".env.example").write_text("DATABASE_URL=\n", "utf-8")
    _configure(target, [".env.production", ".env.example", "README.md"])
    (target / ".gitignore").write_text("", encoding="utf-8")
    _git_baseline(target)

    result = project_tool.export_public(target, tmp_path / "export")

    assert ".env.production" in result["refused_private"]
    assert ".env.example" in result["files"]


def test_unscannable_text_fails_closed(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "src/blob.txt").write_bytes(b'api_key = "abcdefghijklmnopqrstuvwxyz"\n\xff\n')
    _configure(target, ["src/**"])
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="src/blob.txt"):
        project_tool.export_public(target, tmp_path / "export")


def test_scan_hook_cannot_add_or_change_files(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(
        target,
        ["README.md"],
        'scan = [["python", "-c", "import pathlib, sys; '
        "(pathlib.Path(sys.argv[1]) / '.env').write_text('x')\", \"{out}\"]]\n",
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="scan hook"):
        project_tool.export_public(target, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_executable_bit_and_digest_follow_the_git_mode(tmp_path: Path) -> None:
    target = _project(tmp_path)
    script = target / "scripts/run.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _configure(target, ["scripts/**"])
    _git_baseline(target)
    plain = project_tool.export_public(target, tmp_path / "plain")
    assert not (tmp_path / "plain/scripts/run.sh").stat().st_mode & 0o111

    subprocess.run(["git", "update-index", "--chmod=+x", "scripts/run.sh"], cwd=target, check=True)
    script.chmod(0o755)
    _commit(target)
    executable = project_tool.export_public(target, tmp_path / "exec")

    assert (tmp_path / "exec/scripts/run.sh").stat().st_mode & 0o111
    assert executable["export_sha256"] != plain["export_sha256"]


def test_export_reads_the_pinned_commit_not_the_worktree(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(target, ["README.md"])
    _git_baseline(target)
    committed = (target / "README.md").read_bytes()
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "README.md"], cwd=target, check=True
    )
    (target / "README.md").write_text("private customer information\n", encoding="utf-8")

    project_tool.export_public(target, tmp_path / "export")

    assert (tmp_path / "export/README.md").read_bytes() == committed


@pytest.mark.parametrize(
    "hook",
    [
        "import os, sys; p = os.path.join(sys.argv[1], 'README.md'); "
        "data = open(p, 'rb').read(); os.remove(p); "
        "q = os.path.join(os.path.dirname(sys.argv[1]), 'copy'); open(q, 'wb').write(data); "
        "os.symlink(q, p)",
        "import os, sys; os.chmod(os.path.join(sys.argv[1], 'README.md'), 0o777)",
    ],
)
def test_scan_hook_cannot_swap_types_or_modes(tmp_path: Path, hook: str) -> None:
    target = _project(tmp_path)
    _configure(
        target,
        ["README.md"],
        f'scan = [["python", "-c", {hook!r}, "{{out}}"]]\n'.replace("'", "\\'").replace("\\'", "'"),
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="scan hook|non-regular"):
        project_tool.export_public(target, tmp_path / "export")


def test_data_readme_is_source_metadata_not_governed(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _configure(target, ["data/README.md"])
    _git_baseline(target)

    result = project_tool.export_public(target, tmp_path / "export")

    assert result["files"] == ["data/README.md"]


def test_scan_hook_cannot_replace_the_staging_directory(tmp_path: Path) -> None:
    target = _project(tmp_path)
    hook = (
        'import os, sys; s = sys.argv[1]; moved = s + "-moved"; '
        "os.rename(s, moved); os.symlink(moved, s)"
    )
    _configure(target, ["README.md"], f'scan = [["python", "-c", \'{hook}\', "{{out}}"]]\n')
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="replaced the staging directory"):
        project_tool.export_public(target, tmp_path / "export")
    assert not (tmp_path / "export").exists()
