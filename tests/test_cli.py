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


def _write_fake_workflow(root: Path, body: str) -> Path:
    script = root / ".astack" / "workflow.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body, encoding="utf-8")
    return script


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "astack.cli", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def test_help_includes_run_command(tmp_path: Path) -> None:
    result = _run_cli("--help", cwd=tmp_path)
    assert result.returncode == 0
    for command in ("new", "install", "doctor", "change-policy", "skills", "run"):
        assert command in result.stdout


@pytest.mark.parametrize(
    ("retired", "replacement"),
    [
        ("upgrade-release", "astack run upgrade <tag>"),
        ("validate", "astack run validate <tier>"),
    ],
)
def test_retired_workflow_commands_point_to_astack_run(
    retired: str, replacement: str, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["astack", retired, "x"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert replacement in str(error.value)


@pytest.mark.parametrize(
    "forwarded",
    [
        ["validate", "--help"],
        ["validate", "-h"],
        ["audit", "--json", "--strict"],
        ["upgrade", "v0.1.0", "--apply"],
        ["--help"],
        ["-h"],
    ],
)
def test_run_forwards_arguments_and_flags_verbatim(tmp_path: Path, forwarded: list[str]) -> None:
    _write_fake_workflow(
        tmp_path,
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
    )

    result = _run_cli("run", *forwarded, cwd=tmp_path)

    assert result.returncode == 0
    assert json.loads(result.stdout) == forwarded
    assert result.stderr == ""


def test_run_locates_enclosing_project_from_nested_subdirectory(tmp_path: Path) -> None:
    _write_fake_workflow(
        tmp_path,
        "import json, pathlib, sys\n"
        "print(json.dumps({'cwd': str(pathlib.Path.cwd()), 'argv': sys.argv[1:]}))\n",
    )
    nested = tmp_path / "src" / "pkg" / "deep"
    nested.mkdir(parents=True)

    result = _run_cli("run", "outputs", "--flag", cwd=nested)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == {"cwd": str(tmp_path.resolve()), "argv": ["outputs", "--flag"]}


def test_run_preserves_exit_code_stdout_and_stderr_without_wrapping(tmp_path: Path) -> None:
    _write_fake_workflow(
        tmp_path,
        "import sys\n"
        "print('raw-stdout-line')\n"
        "print('raw-stderr-line', file=sys.stderr)\n"
        "raise SystemExit(42)\n",
    )

    result = _run_cli("run", "audit", cwd=tmp_path)

    assert result.returncode == 42
    assert result.stdout == "raw-stdout-line\n"
    assert result.stderr == "raw-stderr-line\n"
    assert "result_envelope" not in result.stdout
    assert "result_envelope" not in result.stderr


def test_run_outside_project_fails_cleanly_on_stderr(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-project"
    outside.mkdir()

    result = _run_cli("run", "validate", "check", cwd=outside)

    assert result.returncode != 0
    assert result.stdout == ""
    assert ".astack/workflow.py" in result.stderr
    assert "Traceback" not in result.stderr
    assert "result_envelope" not in result.stderr


def test_run_help_outside_project_prints_usage(tmp_path: Path) -> None:
    result = _run_cli("run", "--help", cwd=tmp_path)

    assert result.returncode == 0
    assert "astack run <subcommand> [args...]" in result.stdout
    assert result.stderr == ""


def test_run_without_subcommand_reports_usage_error(tmp_path: Path) -> None:
    _write_fake_workflow(tmp_path, "raise SystemExit(0)\n")

    result = _run_cli("run", cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage: astack run <subcommand> [args...]" in result.stderr
    assert "missing subcommand" in result.stderr


def test_workflow_command_env_prefers_data_science_venv_and_prepends_path(tmp_path: Path) -> None:
    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack" / "project.toml").write_text(
        'schema_version = 1\nprofile = "data-science"\n'
        'capabilities = ["scientific-review", "scientific-validation"]\n',
        encoding="utf-8",
    )
    assert cli._workflow_command_env(tmp_path) == (sys.executable, None)

    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    venv_python.chmod(0o755)
    executable, env = cli._workflow_command_env(tmp_path)
    assert executable == str(venv_python)
    assert env is not None
    assert env["PATH"].split(os.pathsep)[0] == str(venv_python.parent)

    (tmp_path / ".astack" / "project.toml").write_text(
        'schema_version = 1\nprofile = "general"\n',
        encoding="utf-8",
    )
    assert cli._workflow_command_env(tmp_path) == (sys.executable, None)


def test_run_puts_project_venv_on_path_for_named_python_commands(tmp_path: Path) -> None:
    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack" / "project.toml").write_text(
        'schema_version = 1\nprofile = "data-science"\n'
        'capabilities = ["scientific-review", "scientific-validation"]\n',
        encoding="utf-8",
    )
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    _write_fake_workflow(
        tmp_path,
        "import shutil, subprocess, sys\n"
        "resolved = shutil.which('python')\n"
        "child = subprocess.run(['python', '-c', 'print(\"from-named-python\")'], "
        "check=True, capture_output=True, text=True)\n"
        "print(f'{resolved}|{child.stdout.strip()}')\n",
    )

    env = {key: value for key, value in os.environ.items() if key != "PATH"}
    env["PATH"] = ""
    result = subprocess.run(
        [sys.executable, "-m", "astack.cli", "run", "validate", "verify"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    expected_python = tmp_path.resolve() / ".venv" / "bin" / "python"
    assert result.stdout.strip() == f"{expected_python}|from-named-python"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group SIGINT semantics")
def test_run_delivers_process_group_sigint_to_workflow_without_wrapper_traceback(
    tmp_path: Path,
) -> None:
    import signal

    # Use raw fd writes instead of print() so SIGINT arriving before print() releases
    # stdout's lock cannot trigger a reentrant BufferedWriter RuntimeError.
    _write_fake_workflow(
        tmp_path,
        "import os, signal, time\n"
        "def handle(_signum, _frame):\n"
        "    os.write(1, b'workflow-handled-sigint\\n')\n"
        "    raise SystemExit(42)\n"
        "signal.signal(signal.SIGINT, handle)\n"
        "os.write(1, b'ready\\n')\n"
        "time.sleep(10)\n",
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "astack.cli", "run", "validate", "check"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        os.killpg(proc.pid, signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 42, f"stdout={stdout!r} stderr={stderr!r}"
    assert stdout.strip() == "workflow-handled-sigint"
    assert stderr == ""


def test_install_defaults_release_to_installed_version_and_keeps_mutual_exclusion(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    captured: dict[str, object] = {}

    def fake_install(target: Path, **kwargs: object) -> dict[str, object]:
        captured.clear()
        captured.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    monkeypatch.setattr(installation, "install_project", fake_install)
    target = tmp_path / "repo"

    monkeypatch.setattr(sys, "argv", ["astack", "install", str(target)])
    cli.main()
    assert captured["target"] == target
    assert captured["release"] == f"v{cli.workflow_version()}"
    assert captured["candidate"] is False
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"

    # Explicit empty --release must pass through verbatim so `_source_identity` rejects it.
    monkeypatch.setattr(sys, "argv", ["astack", "install", str(target), "--release", ""])
    cli.main()
    assert captured["release"] == ""
    assert captured["candidate"] is False
    capsys.readouterr()

    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args(["install", str(target), "--release", "v1.0.0", "--candidate"])
    assert error.value.code == 2


def test_install_default_release_still_verifies_commit_identity(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    source_root = tmp_path / "source"
    source_root.mkdir()
    target = tmp_path / "empty-target"
    expected_version = cli.workflow_version()
    resolved_tags: list[str] = []

    def fake_resolve(tag: str) -> dict[str, str]:
        resolved_tags.append(tag)
        return {"tag": tag, "commit": "a" * 40, "release_id": "42"}

    monkeypatch.setattr(installation, "repository_root", lambda: source_root)
    monkeypatch.setattr(installation, "workflow_version", lambda _root=None: expected_version)
    monkeypatch.setattr(installation, "resolve_release", fake_resolve)
    monkeypatch.setattr(installation, "_source_commit", lambda _root: "b" * 40)
    monkeypatch.setattr(
        sys,
        "argv",
        ["astack", "install", str(target), "--initialize", "--check-argv", json.dumps(["true"])],
    )

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
    assert resolved_tags == [f"v{expected_version}"]
    assert "exact published commit" in capsys.readouterr().err


def test_install_recover_works_without_release_or_candidate(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    recovered: dict[str, object] = {}

    def fake_recover(target: Path, **kwargs: object) -> dict[str, object]:
        recovered.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    def fail_install(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("install_project should not run during --recover")

    monkeypatch.setattr(installation, "recover_installation", fake_recover)
    monkeypatch.setattr(installation, "install_project", fail_install)
    target = tmp_path / "repo"
    monkeypatch.setattr(sys, "argv", ["astack", "install", str(target), "--recover"])

    cli.main()

    assert recovered == {"target": target, "apply": False, "approve_plan": None}
    assert json.loads(capsys.readouterr().out)["status"] == "dry-run"


def test_change_policy_defaults_release_and_data_science_capabilities(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    captured: dict[str, object] = {}

    def fake_transition(target: Path, **kwargs: object) -> dict[str, object]:
        captured.clear()
        captured.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    monkeypatch.setattr(installation, "transition_project_policy", fake_transition)
    target = tmp_path / "repo"

    monkeypatch.setattr(
        sys, "argv", ["astack", "change-policy", str(target), "--profile", "data-science"]
    )
    cli.main()
    assert captured["target"] == target
    assert captured["profile"] == "data-science"
    assert captured["capabilities"] == ["scientific-review", "scientific-validation"]
    assert captured["release"] == f"v{cli.workflow_version()}"
    assert captured["candidate"] is False
    capsys.readouterr()

    monkeypatch.setattr(
        sys, "argv", ["astack", "change-policy", str(target), "--profile", "general"]
    )
    cli.main()
    assert captured["profile"] == "general"
    assert captured["capabilities"] == []
    assert captured["release"] == f"v{cli.workflow_version()}"
    assert captured["candidate"] is False
    capsys.readouterr()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astack",
            "change-policy",
            str(target),
            "--profile",
            "data-science",
            "--capability",
            "scientific-review",
            "--candidate",
        ],
    )
    cli.main()
    assert captured["profile"] == "data-science"
    assert captured["capabilities"] == ["scientific-review"]
    assert captured["release"] is None
    assert captured["candidate"] is True
    capsys.readouterr()

    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args(
            [
                "change-policy",
                str(target),
                "--profile",
                "data-science",
                "--release",
                "v1.0.0",
                "--candidate",
            ]
        )
    assert error.value.code == 2


def test_doctor_cli_allows_omitting_harness_and_rejects_t3_code(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import environment

    calls: list[tuple[Path, str | None]] = []

    def fake_doctor(root: Path, harness: str | None = None) -> dict[str, object]:
        calls.append((root, harness))
        return {
            "root": str(root),
            "harness": harness,
            "status": "READY",
            "checks": [
                {
                    "id": "harness-pi",
                    "required": False,
                    "status": "NOT INSTALLED",
                    "capability": "harness",
                    "recovery": "Install pi if you use it",
                    "summary": "pi not found on PATH",
                }
            ],
        }

    monkeypatch.setattr(environment, "doctor", fake_doctor)
    monkeypatch.setattr(sys, "argv", ["astack", "doctor", "--root", str(tmp_path)])
    cli.main()
    streams = capsys.readouterr()
    assert calls[-1] == (tmp_path.resolve(), None)
    assert json.loads(streams.out)["status"] == "READY"
    assert "harness-pi: NOT INSTALLED" in streams.err

    monkeypatch.setattr(
        sys, "argv", ["astack", "doctor", "--root", str(tmp_path), "--harness", "claude-code"]
    )
    cli.main()
    capsys.readouterr()
    assert calls[-1] == (tmp_path.resolve(), "claude-code")

    with pytest.raises(SystemExit) as error:
        cli._parser().parse_args(["doctor", "--harness", "t3-code"])
    assert error.value.code == 2


def test_install_check_flag_splits_argv_without_shell_and_combines_with_check_argv(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    captured: dict[str, object] = {}

    def fake_install(target: Path, **kwargs: object) -> dict[str, object]:
        captured.clear()
        captured.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    monkeypatch.setattr(installation, "install_project", fake_install)
    target = tmp_path / "repo"

    monkeypatch.setattr(sys, "argv", ["astack", "install", str(target), "--check", "npm test"])
    cli.main()
    capsys.readouterr()
    assert captured["commands"] == [["npm", "test"]]

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astack",
            "install",
            str(target),
            "--check",
            "python -c 'print(1 + 1)'",
            "--check-argv",
            json.dumps(["ruff", "check", "."]),
            "--check",
            'pytest -k "fast and clean"',
        ],
    )
    cli.main()
    capsys.readouterr()
    assert captured["commands"] == [
        ["python", "-c", "print(1 + 1)"],
        ["ruff", "check", "."],
        ["pytest", "-k", "fast and clean"],
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["astack", "install", str(target), "--check-argv", json.dumps(["cargo", "test"])],
    )
    cli.main()
    capsys.readouterr()
    assert captured["commands"] == [["cargo", "test"]]


@pytest.mark.parametrize("bad_check", ["", "   ", 'npm "test', '"" arg'])
def test_install_check_flag_rejects_empty_and_malformed_values(
    tmp_path: Path, monkeypatch, capsys, bad_check: str
) -> None:
    from astack import installation

    def fail_install(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("install_project should not run for invalid --check")

    monkeypatch.setattr(installation, "install_project", fail_install)
    monkeypatch.setattr(sys, "argv", ["astack", "install", str(tmp_path), "--check", bad_check])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
    assert "Invalid --check command" in capsys.readouterr().err


def test_install_and_change_policy_default_target_to_current_directory(monkeypatch, capsys) -> None:
    from astack import installation

    install_captured: dict[str, object] = {}
    recover_captured: dict[str, object] = {}
    policy_captured: dict[str, object] = {}

    def fake_install(target: Path, **kwargs: object) -> dict[str, object]:
        install_captured.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    def fake_recover(target: Path, **kwargs: object) -> dict[str, object]:
        recover_captured.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    def fake_transition(target: Path, **kwargs: object) -> dict[str, object]:
        policy_captured.update({"target": target, **kwargs})
        return {"status": "dry-run"}

    monkeypatch.setattr(installation, "install_project", fake_install)
    monkeypatch.setattr(installation, "recover_installation", fake_recover)
    monkeypatch.setattr(installation, "transition_project_policy", fake_transition)

    monkeypatch.setattr(sys, "argv", ["astack", "install", "--check", "npm test"])
    cli.main()
    capsys.readouterr()
    assert install_captured["target"] == Path(".")
    assert install_captured["commands"] == [["npm", "test"]]

    monkeypatch.setattr(sys, "argv", ["astack", "install", "--recover"])
    cli.main()
    capsys.readouterr()
    assert recover_captured["target"] == Path(".")

    monkeypatch.setattr(sys, "argv", ["astack", "change-policy", "--profile", "data-science"])
    cli.main()
    capsys.readouterr()
    assert policy_captured["target"] == Path(".")
    assert policy_captured["capabilities"] == ["scientific-review", "scientific-validation"]


def test_approve_plan_implies_apply_and_fails_closed_on_empty_or_missing_digest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    target = tmp_path / "empty-target"
    base_args = [
        "astack",
        "install",
        str(target),
        "--candidate",
        "--initialize",
        "--check-argv",
        json.dumps([sys.executable, "-c", "pass"]),
    ]

    monkeypatch.setattr(sys, "argv", base_args)
    cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "dry-run"
    digest = plan["plan_digest"]
    assert not target.exists()

    # Bare --apply and empty --approve-plan must both fail closed without writing anything.
    for bad_flags in (["--apply"], ["--approve-plan", ""]):
        monkeypatch.setattr(sys, "argv", [*base_args, *bad_flags])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 1
        assert "plan digest" in capsys.readouterr().err
        assert not target.exists()

    # --approve-plan alone implies --apply.
    monkeypatch.setattr(sys, "argv", [*base_args, "--approve-plan", digest])
    cli.main()
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"
    assert (target / ".astack/manifest.json").is_file()

    # --apply --approve-plan remains accepted for backward compatibility.
    target_compat = tmp_path / "compat-target"
    compat_args = [
        "astack",
        "install",
        str(target_compat),
        "--candidate",
        "--initialize",
        "--check-argv",
        json.dumps([sys.executable, "-c", "pass"]),
    ]
    monkeypatch.setattr(sys, "argv", compat_args)
    cli.main()
    compat_digest = json.loads(capsys.readouterr().out)["plan_digest"]
    monkeypatch.setattr(sys, "argv", [*compat_args, "--apply", "--approve-plan", compat_digest])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "applied"

    # Recovery: bare --apply and --approve-plan "" fail closed; --approve-plan alone recovers.
    journal = target / installation.TRANSACTION
    journal.write_text(json.dumps({"schema_version": 1, "files": {}, "created_dirs": []}))
    monkeypatch.setattr(sys, "argv", ["astack", "install", str(target), "--recover"])
    cli.main()
    recover_digest = json.loads(capsys.readouterr().out)["plan_digest"]

    for bad_flags in (["--apply"], ["--approve-plan", ""]):
        monkeypatch.setattr(
            sys, "argv", ["astack", "install", str(target), "--recover", *bad_flags]
        )
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 1
        assert "exact conflict-free" in capsys.readouterr().err
        assert journal.is_file()

    monkeypatch.setattr(
        sys,
        "argv",
        ["astack", "install", str(target), "--recover", "--approve-plan", recover_digest],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "recovered"
    assert not journal.exists()

    # Recovery with explicit --apply --approve-plan also still works.
    journal.write_text(json.dumps({"schema_version": 1, "files": {}, "created_dirs": []}))
    monkeypatch.setattr(sys, "argv", ["astack", "install", str(target), "--recover"])
    cli.main()
    recover_digest_2 = json.loads(capsys.readouterr().out)["plan_digest"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astack",
            "install",
            str(target),
            "--recover",
            "--apply",
            "--approve-plan",
            recover_digest_2,
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "recovered"


def test_change_policy_cli_approve_plan_implies_apply_dispatch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import installation

    captured: dict[str, object] = {}

    def fake_transition(target: Path, **kwargs: object) -> dict[str, object]:
        captured.clear()
        captured.update({"target": target, **kwargs})
        return {"status": "applied" if kwargs.get("apply") else "dry-run"}

    monkeypatch.setattr(installation, "transition_project_policy", fake_transition)
    target = tmp_path / "repo"
    base = ["astack", "change-policy", str(target), "--profile", "data-science", "--candidate"]

    cases = [
        ([], False, None),
        (["--apply"], True, None),
        (["--approve-plan", ""], True, ""),
        (["--approve-plan", "a" * 64], True, "a" * 64),
        (["--apply", "--approve-plan", "b" * 64], True, "b" * 64),
    ]
    for extra, expected_apply, expected_approve in cases:
        monkeypatch.setattr(sys, "argv", [*base, *extra])
        cli.main()
        capsys.readouterr()
        assert captured["apply"] is expected_apply
        assert captured["approve_plan"] == expected_approve


def test_runtime_upgrade_cli_defaults_to_latest_and_implies_apply_on_approve_plan(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from project_tool_test_support import project_tool

    from astack import release_upgrade

    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack/manifest.json").write_text(json.dumps({"profile": "general"}))
    endpoints: list[str] = []

    def fake_github_json(endpoint: str) -> dict[str, object]:
        endpoints.append(endpoint)
        if endpoint in {"releases/latest", "releases/tags/v1.2.1"}:
            return {
                "tag_name": "v1.2.1",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-07T00:00:00Z",
                "id": 42,
            }
        if endpoint == "git/ref/tags/v1.2.1":
            return {"ref": "refs/tags/v1.2.1", "object": {"type": "commit", "sha": "a" * 40}}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def fake_run(command: list[str], target: Path) -> subprocess.CompletedProcess[str]:
        payload: dict[str, object] = {
            "status": "applied" if "--apply" in command else "dry-run",
            "profile": "general",
            "source": {
                "kind": "release",
                "tag": "v1.2.1",
                "commit": "a" * 40,
                "release_id": "42",
            },
            "conflicts": [],
            "plan_digest": "c" * 64,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(release_upgrade, "_github_json", fake_github_json)
    monkeypatch.setattr(release_upgrade, "_run", fake_run)
    monkeypatch.chdir(tmp_path)

    # Omitted tag resolves releases/latest and routes through resolve_release.
    monkeypatch.setattr(sys, "argv", ["workflow.py", "upgrade"])
    project_tool.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "dry-run"
    assert plan["target_version"] == "v1.2.1"
    assert endpoints == ["releases/latest", "releases/tags/v1.2.1", "git/ref/tags/v1.2.1"]

    # Explicit "latest" follows the same path.
    endpoints.clear()
    monkeypatch.setattr(sys, "argv", ["workflow.py", "upgrade", "latest"])
    project_tool.main()
    assert json.loads(capsys.readouterr().out)["target_version"] == "v1.2.1"
    assert endpoints == ["releases/latest", "releases/tags/v1.2.1", "git/ref/tags/v1.2.1"]

    # Bare --apply and --approve-plan "" fail closed.
    for argv in (
        ["workflow.py", "upgrade", "--apply"],
        ["workflow.py", "upgrade", "--approve-plan", ""],
        ["workflow.py", "upgrade", "v1.2.1", "--approve-plan", ""],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as error:
            project_tool.main()
        assert error.value.code == 1
        assert "exact conflict-free" in capsys.readouterr().err

    # --approve-plan alone implies --apply for omitted tag, "latest", and exact tag.
    for argv in (
        ["workflow.py", "upgrade", "--approve-plan", "c" * 64],
        ["workflow.py", "upgrade", "latest", "--approve-plan", "c" * 64],
        ["workflow.py", "upgrade", "v1.2.1", "--approve-plan", "c" * 64],
        ["workflow.py", "upgrade", "v1.2.1", "--apply", "--approve-plan", "c" * 64],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        project_tool.main()
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "applied"
        assert result["target_version"] == "v1.2.1"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "tag_name": "v1.2.1",
            "draft": True,
            "prerelease": False,
            "published_at": "2026-10-07T00:00:00Z",
            "id": 42,
        },
        {
            "tag_name": "v1.2.1",
            "draft": False,
            "prerelease": True,
            "published_at": "2026-10-07T00:00:00Z",
            "id": 42,
        },
        {
            "tag_name": "main",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-10-07T00:00:00Z",
            "id": 42,
        },
        {
            "tag_name": "v1.2.1-rc1",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-10-07T00:00:00Z",
            "id": 42,
        },
        {
            "tag_name": "v1.2.1",
            "draft": False,
            "prerelease": False,
            "published_at": None,
            "id": 42,
        },
        {
            "tag_name": "v1.2.1",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-10-07T00:00:00Z",
            "id": True,
        },
    ],
)
def test_resolve_latest_release_rejects_non_stable_payloads(
    monkeypatch, payload: dict[str, object]
) -> None:
    from astack import release_upgrade

    monkeypatch.setattr(release_upgrade, "_github_json", lambda _endpoint: payload)
    with pytest.raises(ValueError, match="published stable"):
        release_upgrade.resolve_latest_release()


def test_resolve_latest_release_fails_closed_when_github_api_unavailable(monkeypatch) -> None:
    from astack import release_upgrade

    def unavailable(_endpoint: str) -> dict[str, object]:
        raise RuntimeError("Published release evidence is unavailable")

    monkeypatch.setattr(release_upgrade, "_github_json", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        release_upgrade.resolve_latest_release()


def test_tutorial_without_topic_lists_available_tutorials(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial"])

    cli.main()

    streams = capsys.readouterr()
    payload = json.loads(streams.out)
    assert payload["status"] == "available"
    assert [item["name"] for item in payload["tutorials"]] == ["intro"]
    assert "astack tutorial intro" in streams.err


def test_tutorial_rejects_unknown_topic(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "unknown-topic"])
    with pytest.raises(SystemExit, match="Unknown tutorial 'unknown-topic'.*intro"):
        cli.main()


def test_tutorial_intro_defaults_target_and_forwards_to_create_tutorial(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import tutorial

    captured: dict[str, object] = {}

    def fake_create(target: Path, **kwargs: object) -> dict[str, object]:
        captured.update({"target": target, **kwargs})
        return {
            "status": "created",
            "topic": "intro",
            "target": str(target),
            "files": ["TUTORIAL.md"],
            "raw_inputs": ["data/raw/sites.parquet"],
            "git": "initialized and committed on main",
            "next_prompt": "Turn 1 prompt",
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tutorial, "create_tutorial", fake_create)
    monkeypatch.setattr(cli, "_enclosing_repository", lambda _target, **_kw: None)
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "intro"])

    cli.main()

    assert captured["target"] == Path("stargazing-spots")
    assert captured["topic"] == "intro"
    assert captured["init_git"] is True
    streams = capsys.readouterr()
    assert json.loads(streams.out)["topic"] == "intro"
    assert "astack doctor" in streams.err
    assert "Turn 1 prompt" in streams.err


def test_tutorial_refuses_to_initialize_inside_an_existing_git_repo(
    tmp_path: Path, monkeypatch
) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "stargazing-spots"
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "intro", str(target)])
    with pytest.raises(SystemExit, match="refuses to initialize inside existing Git repository"):
        cli.main()
    assert not target.exists()


def test_tutorial_no_git_skips_enclosing_repo_check(tmp_path: Path, monkeypatch) -> None:
    from astack import tutorial

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "stargazing-spots"
    captured: dict[str, object] = {}

    def fake_create(path: Path, **kwargs: object) -> dict[str, object]:
        captured.update({"target": path, **kwargs})
        return {
            "status": "created",
            "topic": "intro",
            "target": str(path),
            "files": ["TUTORIAL.md"],
            "raw_inputs": [],
            "git": "skipped (--no-git)",
            "next_prompt": "Turn 1 prompt",
        }

    monkeypatch.setattr(tutorial, "create_tutorial", fake_create)
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "intro", str(target), "--no-git"])

    cli.main()

    assert captured["target"] == target
    assert captured["init_git"] is False


def test_tutorial_refuses_existing_target_without_modifying_it(tmp_path: Path) -> None:
    from astack.tutorial import create_tutorial

    existing_empty = tmp_path / "existing-empty"
    existing_empty.mkdir()
    with pytest.raises(FileExistsError, match="Target already exists"):
        create_tutorial(existing_empty, topic="intro", init_git=False)
    assert existing_empty.is_dir()

    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("existing file\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Target already exists"):
        create_tutorial(target, topic="intro", init_git=False)
    assert marker.read_text(encoding="utf-8") == "existing file\n"


def test_tutorial_expands_tilde_before_enclosing_repo_preflight(
    tmp_path: Path, monkeypatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=fake_home, check=True, capture_output=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(outside)
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "intro", "~/stargazing-spots"])

    with pytest.raises(SystemExit, match="refuses to initialize inside existing Git repository"):
        cli.main()
    assert not (fake_home / "stargazing-spots").exists()


def test_tutorial_declares_independent_uv_workspace_under_parent_glob(tmp_path: Path) -> None:
    from astack.tutorial import TUTORIALS, _configure_intro_pyproject

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "parent"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n\n'
        '[tool.uv.workspace]\nmembers = ["*"]\n',
        encoding="utf-8",
    )
    child = tmp_path / "stargazing-spots"
    child.mkdir()
    (child / "pyproject.toml").write_text(
        '[project]\nname = "stargazing-spots"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12"\ndependencies = []\n\n'
        "[tool.pytest.ini_options]\nmarkers = [\n"
        '  "integration: tests that exercise integration-heavy behavior",\n]\n',
        encoding="utf-8",
    )
    _configure_intro_pyproject(child, TUTORIALS["intro"])
    configured = (child / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.uv.workspace]" in configured

    # Keep the fast test offline by stripping external dependencies while retaining the workspace.
    (child / "pyproject.toml").write_text(
        configured.replace(
            'dependencies = ["pyarrow>=15", "rasterio>=1.4", "numpy>=2.0"]', "dependencies = []"
        ),
        encoding="utf-8",
    )
    lock = subprocess.run(
        ["uv", "lock", "--offline"],
        cwd=child,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
        capture_output=True,
        text=True,
    )
    assert lock.returncode == 0, lock.stderr
    assert (child / "uv.lock").is_file()
    assert not (tmp_path / "uv.lock").exists()


def test_tutorial_rollback_removes_failed_new_target_for_clean_retry(
    tmp_path: Path, monkeypatch
) -> None:
    from astack import tutorial

    def fail_lock(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("uv lock failed")

    monkeypatch.setattr(tutorial, "_run_step", fail_lock)

    fresh_target = tmp_path / "fresh-target"
    with pytest.raises(RuntimeError, match="uv lock failed"):
        tutorial.create_tutorial(fresh_target, topic="intro", init_git=False)
    assert not fresh_target.exists()


def test_tutorial_cli_prints_git_identity_guidance_when_fallback_used(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import tutorial

    spaced_target = tmp_path / "my tutorial"
    monkeypatch.setattr(
        tutorial,
        "create_tutorial",
        lambda target, **_kw: {
            "status": "created",
            "topic": "intro",
            "target": str(target),
            "files": ["TUTORIAL.md"],
            "raw_inputs": [],
            "git": "initialized and committed on main",
            "git_identity_fallback": True,
            "next_prompt": "Turn 1 prompt",
        },
    )
    monkeypatch.setattr(cli, "_enclosing_repository", lambda _target, **_kw: None)
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "intro", str(spaced_target)])

    cli.main()

    streams = capsys.readouterr()
    resolved = spaced_target.resolve()
    assert f"git -C '{resolved}' config user.name" in streams.err
    assert f"git -C '{resolved}' config user.email" in streams.err
    assert f"cd '{resolved}'" in streams.err


def test_tutorial_cli_uses_absolute_path_for_dash_target(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from astack import tutorial

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        tutorial,
        "create_tutorial",
        lambda target, **_kw: {
            "status": "created",
            "topic": "intro",
            "target": str(target),
            "files": ["TUTORIAL.md"],
            "raw_inputs": [],
            "git": "initialized and committed on main",
            "git_identity_fallback": False,
            "next_prompt": "Turn 1 prompt",
        },
    )
    monkeypatch.setattr(cli, "_enclosing_repository", lambda _target, **_kw: None)
    monkeypatch.setattr(sys, "argv", ["astack", "tutorial", "intro", "--", "-"])

    cli.main()

    streams = capsys.readouterr()
    assert f"cd {(tmp_path / '-').resolve()}" in streams.err
    assert "\n  cd -\n" not in streams.err


def test_tutorial_md_reflects_no_git_mode() -> None:
    from astack.tutorial import _render_tutorial_md

    with_git = _render_tutorial_md(init_git=True)
    without_git = _render_tutorial_md(init_git=False)
    assert "created the baseline commit" in with_git
    assert "Skipped Git setup (`--no-git`)" in without_git
    assert "git init -b main" in without_git
    assert "chmod u+w" in with_git
    assert "docs/walkthrough.md" not in with_git
