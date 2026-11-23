"""Passing software checks must never be reported as computational reproducibility."""

from __future__ import annotations

from typing import Any

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _append_validation,
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
)


@pytest.fixture
def passing_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    original_run = project_tool._run

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if args[:1] == ["just"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return original_run(args, cwd=cwd, input_text=input_text)  # type: ignore[no-any-return]

    monkeypatch.setattr(project_tool, "_run", fake_run)


def test_audit_reproduction_fresh_project_is_not_reproducible(
    tmp_path: Path, passing_tools: None
) -> None:
    """Regression for the pre-publication audit reproduction (row R2)."""
    target = _project(tmp_path)
    _git_baseline(target)

    result = project_tool.audit(target, run_tools=True, strict=True)
    verdicts = result["verdicts"]

    assert result["analysis_stage"] == "exploratory"
    assert verdicts["software_checks"] == "pass"
    assert verdicts["computational_reproducibility"] == "not-assessed"
    assert verdicts["release_readiness"] == "not-assessed"
    assert any("no reproduction evidence" in item for item in result["not_assessed"])
    # Exploratory ship-readiness is gated on software checks, honestly labelled.
    assert project_tool._strict_ready(result)


def test_software_checks_not_run_are_not_assessed(tmp_path: Path) -> None:
    target = _project(tmp_path)

    verdicts = project_tool.audit(target)["verdicts"]

    assert verdicts["software_checks"] == "not-assessed"
    assert verdicts["computational_reproducibility"] != "pass"


def test_exploratory_strict_readiness_requires_passing_software_checks() -> None:
    result = {
        "analysis_stage": "exploratory",
        "verdicts": {
            "workflow_conformance": "pass",
            "software_checks": "not-assessed",
            "computational_reproducibility": "not-assessed",
            "release_readiness": "not-assessed",
        },
    }
    assert not project_tool._strict_ready(result)


def test_material_strict_readiness_still_requires_reproducibility_pass() -> None:
    result = {
        "analysis_stage": "material",
        "verdicts": {
            "workflow_conformance": "pass",
            "software_checks": "pass",
            "computational_reproducibility": "not-assessed",
            "release_readiness": "not-assessed",
        },
    }
    assert not project_tool._strict_ready(result)


def _findings(result: dict[str, Any], check: str) -> list[dict[str, Any]]:
    return [item for item in result["findings"] if item["check"] == check]


def test_undeclared_verify_is_disclosed_but_does_not_block_exploratory_ship_ready(
    tmp_path: Path, passing_tools: None
) -> None:
    target = _project(tmp_path)
    _git_baseline(target)

    result = project_tool.audit(target, run_tools=True, strict=True)

    assert [item["priority"] for item in _findings(result, "verify-undeclared")] == ["P2"]
    assert result["verdicts"]["software_checks"] == "pass"
    assert project_tool._strict_ready(result)


def test_undeclared_verify_blocks_material_ship_ready(tmp_path: Path, passing_tools: None) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(config.read_text().replace('stage = "exploratory"', 'stage = "material"', 1))
    _git_baseline(target)

    result = project_tool.audit(target, run_tools=True, strict=True)

    assert [item["priority"] for item in _findings(result, "verify-undeclared")] == ["P1"]
    assert result["verdicts"]["release_readiness"] == "fail"
    assert not project_tool._strict_ready(result)


def test_ship_ready_runs_declared_verify_as_its_own_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _append_validation(target, "verify", [["just", "science"]])
    _git_baseline(target)
    ran: list[list[str]] = []
    original_run = project_tool._run

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if args[:1] == ["just"]:
            ran.append(args)
            return subprocess.CompletedProcess(args, int(args == ["just", "science"]), "", "")
        return original_run(args, cwd=cwd, input_text=input_text)  # type: ignore[no-any-return]

    monkeypatch.setattr(project_tool, "_run", fake_run)

    result = project_tool.audit(target, run_tools=True, strict=True)

    assert ["just", "science"] in ran
    assert not _findings(result, "verify-undeclared")
    assert result["verdicts"]["software_checks"] == "fail"
    stages = {stage["name"]: stage["status"] for stage in result["validation_session"]["stages"]}
    assert stages["verify"] == "failed", stages
