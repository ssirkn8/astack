"""Profiles select policy without branching the lifecycle state machine."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from astack import project_policy as policy
from astack.output_contract import output_contract_summary


def _general(root: Path, commands: str | None = None) -> Path:
    root.mkdir(exist_ok=True)
    (root / ".astack").mkdir(exist_ok=True)
    text = 'schema_version = 1\nprofile = "general"\n'
    if commands:
        text += "[validation]\ncheck = " + commands + "\n"
    (root / ".astack/project.toml").write_text(text)
    return root


def test_general_validation_runs_exact_argv_in_project(tmp_path: Path) -> None:
    # Spaces and shell metacharacters must remain literal argv, never shell execution.
    script = tmp_path / "own check.py"
    script.write_text(
        'import pathlib, sys\npathlib.Path("observed.json").write_text(sys.argv[1])\n'
    )
    argv = [sys.executable, str(script), "literal $(no-shell); value"]
    root = _general(tmp_path, json.dumps([argv]))
    assert policy.project_profile(root) == "general"
    assert policy.run_project_validation(root, "check")["commands"] == [argv]
    assert (root / "observed.json").read_text() == argv[-1]


@pytest.mark.parametrize(
    "command",
    [[], [["missing-workflow-test-executable"]], [[sys.executable, "-c", "raise SystemExit(3)"]]],
)
def test_general_never_passes_empty_unavailable_or_failed_checks(
    tmp_path: Path, command: list
) -> None:
    root = _general(tmp_path, json.dumps(command))
    with pytest.raises((ValueError, RuntimeError)):
        policy.run_project_validation(root, "check")


def test_general_missing_checks_fail_closed(tmp_path: Path) -> None:
    root = _general(tmp_path)
    with pytest.raises(ValueError, match="not configured"):
        policy.validation_commands(root, "check")


def test_ds_profile_runs_its_declared_check_with_science_capabilities(tmp_path: Path) -> None:
    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack/project.toml").write_text(
        'schema_version = 1\nprofile = "data-science"\n'
        'capabilities = ["scientific-review", "scientific-validation"]\n'
        '[validation]\ncheck = [["just", "check"]]\n'
    )
    assert policy.project_profile(tmp_path) == "data-science"
    assert policy.validation_commands(tmp_path, "check") == [["just", "check"]]
    assert output_contract_summary(tmp_path)["status"] == "not_declared"


def test_general_mixed_science_uses_shared_review_and_contract_policy(tmp_path: Path) -> None:
    root = _general(tmp_path)
    path = root / ".astack/project.toml"
    path.write_text(path.read_text() + '[review]\nscientific_paths = ["exports/**"]\n')
    ordinary = policy.review_policy(root, ["src/ui.js"])
    scientific = policy.review_policy(root, ["exports/measurements.json"])
    assert ordinary["required_axes"] == ["acceptance", "engineering_reproducibility"]
    assert "scientific_credibility" in scientific["required_axes"]
    path.write_text(
        path.read_text()
        + '[scientific_outputs]\npath = "docs/missing.yml"\n'
        + 'evidence_path = "evidence/missing.yml"\n'
    )
    summary = output_contract_summary(root)
    assert summary["declaration"] == "declared"
    assert summary["status"] == "unknown"
    assert summary["blockers"]


def test_validation_timeout_is_not_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _general(tmp_path, json.dumps([["my-check"]]))

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(policy.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        policy.run_project_validation(root, "check", timeout=1)


@pytest.mark.integration
def test_vendored_general_runtime_validates_without_ds_or_application_python(
    tmp_path: Path,
) -> None:
    from astack.scaffold import create_project

    source = tmp_path / "source"
    create_project(
        source,
        project_name="Source",
        package_name="source",
        description="Runtime delivery",
        lock=False,
    )
    argv = [sys.executable, "-c", 'import sys; assert sys.argv[1] == ""', ""]
    root = _general(tmp_path / "application", json.dumps([argv]))
    # Relocate the same delivered core. There is no profile-specific runtime fork.
    shutil.copytree(
        source / ".astack",
        root / ".astack",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("project.toml"),
    )
    runtime = root / ".astack/workflow.py"
    result = subprocess.run(
        [sys.executable, str(runtime), "validate", "check"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["status"] == "passed"
    assert not (root / "pyproject.toml").exists()


def test_validation_preserves_valid_empty_argument_after_executable(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", 'import sys; assert sys.argv[1] == ""', ""]
    root = _general(tmp_path, json.dumps([argv]))
    assert policy.run_project_validation(root, "check")["commands"] == [argv]


@pytest.mark.parametrize("argv", [[""], [sys.executable, None], [sys.executable, "\x00"]])
def test_invalid_validation_executable_types_and_nul_remain_blocked(
    tmp_path: Path, argv: list
) -> None:
    root = _general(tmp_path, json.dumps([argv]))
    with pytest.raises(ValueError):
        policy.validation_commands(root, "check")


def test_policy_based_ds_requires_its_scientific_safeguards(tmp_path: Path) -> None:
    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack/project.toml").write_text('profile = "data-science"\n')
    with pytest.raises(ValueError, match="requires scientific review and validation"):
        policy.project_config(tmp_path)


def _scientific(root: Path, verify: object = None) -> Path:
    (root / ".astack").mkdir(exist_ok=True)
    check = [
        [sys.executable, "-c", "from pathlib import Path; Path('CHECK.RAN').write_text('ran')"]
    ]
    text = (
        'profile = "general"\ncapabilities = ["scientific-validation"]\n[validation]\ncheck = '
        + json.dumps(check)
        + "\n"
    )
    if verify is not None:
        text += "verify = " + json.dumps(verify) + "\n"
    (root / ".astack/project.toml").write_text(text)
    return root


def test_undeclared_verify_is_reported_as_not_run_and_check_still_runs(tmp_path: Path) -> None:
    result = policy.run_project_validation(_scientific(tmp_path), "check")

    assert (tmp_path / "CHECK.RAN").exists()
    assert result["status"] == "passed"
    assert result["verify"]["status"] == "not-run"


def test_declared_verify_runs_with_check_and_its_failure_fails_check(tmp_path: Path) -> None:
    passing = [[sys.executable, "-c", "from pathlib import Path; Path('V.RAN').write_text('1')"]]
    result = policy.run_project_validation(_scientific(tmp_path, passing), "check")
    assert (tmp_path / "V.RAN").exists()
    assert "verify" not in result

    failing = tmp_path / "failing"
    failing.mkdir()
    _scientific(failing, [[sys.executable, "-c", "raise SystemExit(3)"]])
    with pytest.raises(RuntimeError, match="CHECK validation failed"):
        policy.run_project_validation(failing, "check")


@pytest.mark.parametrize("verify", [[], "pytest", [["pytest"], []]])
def test_malformed_declared_verify_is_an_error_not_not_run(tmp_path: Path, verify: object) -> None:
    with pytest.raises(ValueError):
        policy.run_project_validation(_scientific(tmp_path, verify), "check")
    assert not (tmp_path / "CHECK.RAN").exists()


def test_verify_tier_itself_still_fails_closed_when_undeclared(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not configured"):
        policy.run_project_validation(_scientific(tmp_path), "verify")


def test_malformed_capabilities_are_not_interpreted_as_missing_science(tmp_path: Path) -> None:
    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack/project.toml").write_text(
        'profile = "general"\ncapabilities = "scientific-review"\n'
    )
    with pytest.raises(ValueError, match="list of strings"):
        policy.project_config(tmp_path)
