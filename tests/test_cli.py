import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from astack import cli
from astack.scaffold import LockGenerationError


def test_package_name_derives_safe_identifier() -> None:
    assert cli._package_name("Jakarta Flood Exposure") == "jakarta_flood_exposure"
    assert cli._package_name("2026 Study") == "project_2026_study"
    assert cli._package_name("Class") == "project_class"


@pytest.mark.parametrize(
    "argv",
    [
        ["work-unit", "next-action"],
        ["work-unit", "packet", "--phase", "planning"],
        ["work-unit", "preflight", "--packet-file", "packet.json"],
        ["work-unit", "validate-result", "unit", "--result-file", "result.json"],
        ["work-unit", "child-runner", "capability", "--packet-file", "packet.json"],
        ["work-unit", "record-pending-decision", "unit", "--record-file", "record.json"],
        ["work-unit", "resolve-pending-decision", "unit", "--decision-id", "x"],
        ["work-unit", "record-action-failure", "unit", "--record-file", "record.json"],
        ["work-unit", "retry-action", "unit", "--action", "x", "--input-fingerprint", "y"],
        ["work-unit", "clear-action-failure", "unit", "--action", "x", "--input-fingerprint", "y"],
        ["controller-check", "--harness", "pi"],
        ["work-unit", "list"],
        ["work-unit", "resume", "issue:1"],
        ["worktree", "list"],
        ["work-item", "ensure", "--identity", "x"],
        ["control-plane", "status"],
        ["direct", "start"],
        ["verify-merge-readiness", "--pr", "1", "--reviewer", "a", "--check", "b"],
        ["release", "intent", "unit:release-v1-2-0"],
        ["release", "finalize", "unit:release-v1-2-0"],
    ],
)
def test_retired_run_cli_commands_are_not_available(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args(argv)
    assert error.value.code == 2


def test_new_asks_for_missing_details_in_a_terminal(tmp_path: Path, monkeypatch, capsys) -> None:
    target = tmp_path / "coastal-study"
    answers = iter(["", "", "Evaluate coastal exposure.", "Support exploratory planning."])
    captured: dict[str, object] = {}

    def fake_create(path: Path, **kwargs: object) -> list[str]:
        captured.update({"target": path, **kwargs})
        path.mkdir()
        return ["README.md"]

    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "create_project", fake_create)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    argv = ["astack", "new", str(target), "--tool", "dvc", "--tool", "quarto"]
    monkeypatch.setattr(sys, "argv", argv)

    cli.main()

    assert captured == {
        "target": target,
        "project_name": "Coastal Study",
        "package_name": "coastal_study",
        "description": "Evaluate coastal exposure.",
        "intended_use": "Support exploratory planning.",
        "tools": ["dvc", "marimo", "quarto"],
        "lock": True,
    }
    streams = capsys.readouterr()
    assert json.loads(streams.out)["status"] == "created"
    assert f"cd {target}" in streams.err


def test_new_names_missing_flags_without_a_terminal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys, "argv", ["astack", "new", str(tmp_path / "x"), "--name", "X"])
    with pytest.raises(SystemExit, match="missing --package, --description, --intended-use"):
        cli.main()
    assert not (tmp_path / "x").exists()


def _new_with_flags(target: Path, monkeypatch, *extra: str) -> None:
    def fake_create(path: Path, **kwargs: object) -> list[str]:
        path.mkdir(parents=True)
        return ["README.md"]

    monkeypatch.setattr(cli, "create_project", fake_create)
    flags = ["--name", "S", "--package", "s", "--description", "D", "--intended-use", "U"]
    monkeypatch.setattr(sys, "argv", ["astack", "new", str(target), *flags, *extra])
    cli.main()


def _branch(repository: Path) -> str:
    return subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_new_runs_git_init_on_main_without_committing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    target = tmp_path / "study"

    _new_with_flags(target, monkeypatch)

    assert (target / ".git").is_dir()
    assert _branch(target) == "main"
    commits = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=target, capture_output=True
    )
    assert commits.returncode != 0
    streams = capsys.readouterr()
    assert json.loads(streams.out)["git"] == "initialized"
    assert "Nothing is committed yet." in streams.err


def test_new_inside_an_existing_repository_does_not_nest_one(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    outer = tmp_path / "monorepo"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    target = outer / "packages" / "study"

    _new_with_flags(target, monkeypatch)

    assert not (target / ".git").exists()
    git_status = json.loads(capsys.readouterr().out)["git"]
    assert git_status.startswith("skipped (already inside the Git repository at ")
    assert git_status.endswith(f"{outer.resolve().name})")


def test_new_with_no_git_skips_git_init(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    target = tmp_path / "study"

    _new_with_flags(target, monkeypatch, "--no-git")

    assert not (target / ".git").exists()
    assert json.loads(capsys.readouterr().out)["git"] == "skipped (--no-git)"


def test_new_ignores_git_variables_that_point_elsewhere(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=elsewhere, check=True)
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(elsewhere))
    target = tmp_path / "study"

    _new_with_flags(target, monkeypatch)

    assert (target / ".git").is_dir()
    assert json.loads(capsys.readouterr().out)["git"] == "initialized"


def test_ceiling_variable_cannot_hide_an_enclosing_repository(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    outer = tmp_path / "monorepo"
    (outer / "packages" / "existing").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(outer / "packages"))
    target = outer / "packages" / "existing" / "study"

    _new_with_flags(target, monkeypatch)

    assert not (target / ".git").exists()
    assert json.loads(capsys.readouterr().out)["git"].startswith("skipped (already inside")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_unreadable_enclosing_repository_stops_before_writing(tmp_path: Path, monkeypatch) -> None:
    outer = tmp_path / "monorepo"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    metadata = outer / ".git"
    metadata.chmod(0)
    target = outer / "study"
    try:
        with pytest.raises(SystemExit, match="could not read .*\\.git.*pass --no-git"):
            _new_with_flags(target, monkeypatch)
    finally:
        metadata.chmod(0o755)

    assert not target.exists()


def test_new_stops_before_writing_when_repository_detection_fails(
    tmp_path: Path, monkeypatch
) -> None:
    def failing_git(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 128, "", "fatal: detected dubious ownership in repository"
        )

    monkeypatch.setattr(cli.subprocess, "run", failing_git)
    target = tmp_path / "study"

    with pytest.raises(SystemExit, match="dubious ownership.*pass --no-git"):
        _new_with_flags(target, monkeypatch)

    assert not target.exists()


def test_lock_failure_says_git_init_is_still_pending(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    def fail_create(*args: object, **kwargs: object) -> list[str]:
        raise LockGenerationError("resolution failed")

    monkeypatch.setattr(cli, "create_project", fail_create)
    flags = ["--name", "S", "--package", "s", "--description", "D", "--intended-use", "U"]
    monkeypatch.setattr(sys, "argv", ["astack", "new", str(tmp_path / "study"), *flags])

    with pytest.raises(SystemExit):
        cli.main()

    assert "git init -b main" in capsys.readouterr().err


def test_new_without_git_installed_stops_before_writing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    target = tmp_path / "study"

    with pytest.raises(SystemExit, match="git was not found. Install git, or pass --no-git."):
        _new_with_flags(target, monkeypatch)

    assert not target.exists()


@pytest.mark.parametrize("name", ["init", "bootstrap", "setup", "upgrade-release", "validate"])
def test_removed_commands_point_to_their_replacement(name: str, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["astack", name, "x"])
    with pytest.raises(SystemExit, match=f"astack {name} was removed. "):
        cli.main()


def test_new_reports_preserved_project_when_locking_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "study"
    answers = iter(["", "", "Description", "Intended use"])

    def fail_create(*args: object, **kwargs: object) -> list[str]:
        raise LockGenerationError("resolution failed")

    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "create_project", fail_create)
    monkeypatch.setattr(sys, "argv", ["astack", "new", str(target)])

    with pytest.raises(SystemExit, match="1"):
        cli.main()

    error = capsys.readouterr().err
    assert f"Project generated at {target}" in error
    assert f"cd {target}" in error
    assert "uv lock" in error


@pytest.mark.parametrize("status,outcome", [("blocked", "blocked"), ("failed", "failed")])
def test_runtime_envelope_preserves_operation_failure(
    monkeypatch, capsys, tmp_path: Path, status: str, outcome: str
) -> None:
    cli._emit_result(
        {"status": status, "blockers": ["Required check unavailable"]}, tmp_path, command="test"
    )
    envelope = json.loads(capsys.readouterr().out)["result_envelope"]
    assert envelope["outcome"] == outcome
    assert envelope["blockers"][0]["summary"] == "Required check unavailable"


def test_runtime_envelope_distinguishes_unavailable_readiness(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    cli._emit_result(
        {
            "readiness": {
                "status": "not_assessed",
                "summary": "Check unavailable",
                "required_evidence": ["check"],
            },
            "evidence": [
                {"id": "check", "summary": "Check", "required": True, "status": "unavailable"}
            ],
        },
        tmp_path,
        command="test",
    )
    envelope = json.loads(capsys.readouterr().out)["result_envelope"]
    assert envelope["outcome"] == "completed"
    assert envelope["readiness"]["status"] == "not_assessed"
    assert envelope["evidence"][0]["status"] == "unavailable"


def test_command_failure_reports_a_failed_envelope(monkeypatch, capsys, tmp_path) -> None:
    from astack import environment

    def fail(root: Path, harness: str) -> dict[str, object]:
        raise RuntimeError("validation crashed")

    monkeypatch.setattr(environment, "doctor", fail)
    argv = ["astack", "doctor", "--root", str(tmp_path), "--harness", "pi"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="1"):
        cli.main()

    payload = json.loads(capsys.readouterr().err.split("## Result", 1)[0])
    assert payload["error"]["message"] == "validation crashed"
    assert payload["result_envelope"]["outcome"] == "failed"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], ["dvc", "marimo"]),
        (["--no-dvc"], ["marimo"]),
        (["--tool", "quarto"], ["dvc", "marimo", "quarto"]),
    ],
)
def test_new_adds_dvc_unless_declined(
    tmp_path: Path, monkeypatch, flags: list[str], expected: list[str]
) -> None:
    captured: dict[str, object] = {}

    def fake_create(path: Path, **kwargs: object) -> list[str]:
        captured.update(kwargs)
        path.mkdir()
        return ["README.md"]

    monkeypatch.setattr(cli, "create_project", fake_create)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    argv = ["astack", "new", str(tmp_path / "study"), "--no-git", "--name", "Study"]
    argv += ["--package", "study", "--description", "D", "--intended-use", "U", *flags]
    monkeypatch.setattr(sys, "argv", argv)

    cli.main()

    assert captured["tools"] == expected
