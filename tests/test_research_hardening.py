"""Offline regressions for scientific definitions and incomplete landing recovery."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from astack import scaffold
from astack.output_contract import output_contract_summary


def _registry(root: Path) -> tuple[Path, Path]:
    (root / ".astack").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "evidence").mkdir(exist_ok=True)
    (root / ".astack/project.toml").write_text(
        '[scientific_outputs]\npath = "docs/scientific-outputs.yml"\n'
        'evidence_path = "evidence/scientific-outputs.yml"\n'
    )
    definitions = root / "docs/scientific-outputs.yml"
    evidence = root / "evidence/scientific-outputs.yml"
    definitions.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "research_question": "How well do simulated depths match observations?",
                "outputs": {
                    "depth-validation": {
                        "type": "figure",
                        "role": "headline",
                        "description": "Compare modeled and observed depths in meters",
                        "validation": ["depth-qa"],
                        "communication": {
                            "key_message": "Agreement and uncertainty",
                            "references": ["missing.png"],
                        },
                    },
                    "sensitivity": {
                        "type": "table",
                        "role": "supplementary",
                        "description": "Sensitivity analysis",
                    },
                },
                "validation": {"depth-qa": {"description": "Check units and independent samples"}},
                "analyses": {
                    "prepare-depths": {"outputs": ["depth-validation"]},
                    "plot-depths": {"outputs": ["depth-validation"]},
                },
            }
        )
    )
    evidence.write_text(
        yaml.safe_dump({"schema_version": 2, "outputs": {}, "validation": {}, "confirmations": {}})
    )
    return definitions, evidence


def test_registry_derives_shared_output_without_duplicate_definitions(tmp_path: Path) -> None:
    _registry(tmp_path)
    for unit in ("prepare-depths", "plot-depths"):
        result = output_contract_summary(tmp_path, unit)
        assert result["representation"] == "project-registry"
        assert [item["id"] for item in result["required_outputs"]] == ["depth-validation"]
        assert result["required_outputs"][0]["role"] == "headline"
        assert [item["id"] for item in result["required_validation"]] == ["depth-qa"]
        assert result["confirmation"] == "unconfirmed"
        assert not any("missing.png" in blocker for blocker in result["blockers"])
    aggregate = output_contract_summary(tmp_path)
    assert {item["role"] for item in aggregate["required_outputs"]} == {"headline", "supplementary"}


def test_registry_aggregate_never_confirms_unbound_outputs(tmp_path: Path) -> None:
    _, evidence = _registry(tmp_path)
    progress = yaml.safe_load(evidence.read_text())
    for unit in ("prepare-depths", "plot-depths"):
        progress["confirmations"][unit] = {
            "definition_digest": output_contract_summary(tmp_path, unit)["definition_digest"],
            "evidence": "Human confirmed this analysis definition",
        }
    evidence.write_text(yaml.safe_dump(progress))
    aggregate = output_contract_summary(tmp_path)
    assert aggregate["confirmation"] == "unconfirmed"
    assert any("human confirmation" in blocker for blocker in aggregate["blockers"])


def test_registry_confirmation_survives_progress_but_not_definition_changes(tmp_path: Path) -> None:
    definitions, evidence = _registry(tmp_path)
    digest = output_contract_summary(tmp_path, "plot-depths")["definition_digest"]
    progress = yaml.safe_load(evidence.read_text())
    progress["confirmations"]["plot-depths"] = {
        "definition_digest": digest,
        "evidence": "Human explicitly confirmed scientific outputs and validation",
    }
    evidence.write_text(yaml.safe_dump(progress))
    assert output_contract_summary(tmp_path, "plot-depths")["confirmation"] == "confirmed"
    progress["outputs"]["depth-validation"] = {"status": "blocked", "evidence": []}
    evidence.write_text(yaml.safe_dump(progress))
    summary = output_contract_summary(tmp_path, "plot-depths")
    assert summary["definition_digest"] == digest
    assert summary["confirmation"] == "confirmed"
    definition = yaml.safe_load(definitions.read_text())
    definition["outputs"]["sensitivity"]["description"] = "Unrelated change"
    definitions.write_text(yaml.safe_dump(definition))
    assert output_contract_summary(tmp_path, "plot-depths")["confirmation"] == "confirmed"
    definition["outputs"]["depth-validation"]["description"] = "A different scientific comparison"
    definitions.write_text(yaml.safe_dump(definition))
    assert (
        output_contract_summary(tmp_path, "plot-depths")["confirmation"]
        == "reconfirmation_required"
    )


@pytest.mark.parametrize(
    "mutation", ["unknown-output", "progress-in-definition", "unsafe-path", "malformed-evidence"]
)
def test_registry_fails_closed_on_unsafe_or_unknown_definition(
    tmp_path: Path, mutation: str
) -> None:
    definitions, evidence = _registry(tmp_path)
    definition = yaml.safe_load(definitions.read_text())
    if mutation == "unknown-output":
        definition["analyses"]["plot-depths"]["outputs"] = ["unknown"]
    elif mutation == "progress-in-definition":
        definition["outputs"]["depth-validation"]["status"] = "complete"
    elif mutation == "unsafe-path":
        (tmp_path / ".astack/project.toml").write_text(
            '[scientific_outputs]\npath = "../secrets.yml"\n'
            'evidence_path = "evidence/scientific-outputs.yml"\n'
        )
    else:
        evidence.write_text("[invalid yaml")
    definitions.write_text(yaml.safe_dump(definition))
    result = output_contract_summary(tmp_path, "plot-depths")
    assert result["status"] == "unknown"
    assert result["blockers"]


def test_named_analysis_must_exist_in_the_registry(tmp_path: Path) -> None:
    _registry(tmp_path)
    # Maintenance work names no analysis. A named one must exist, or a typo would check nothing.
    result = output_contract_summary(tmp_path, "fix-spelling")
    assert result["status"] == "unknown"
    assert "'fix-spelling' is not in the registry's analyses" in result["blockers"][0]


def test_explicit_output_scope_does_not_gate_unrelated_future_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "hardening_project_tool", "workflow/project_tool.py"
    )
    assert spec is not None and spec.loader is not None
    project_tool = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, project_tool)
    spec.loader.exec_module(project_tool)

    _, evidence = _registry(tmp_path)
    summary = output_contract_summary(tmp_path, "plot-depths")
    (tmp_path / "evidence/result.txt").write_text("Reproducible output and validation evidence\n")
    progress = yaml.safe_load(evidence.read_text())
    progress["outputs"]["depth-validation"] = {
        "status": "complete",
        "evidence": ["evidence/result.txt"],
    }
    progress["validation"]["depth-qa"] = {"status": "complete", "evidence": ["evidence/result.txt"]}
    progress["confirmations"]["plot-depths"] = {
        "definition_digest": summary["definition_digest"],
        "evidence": "Human confirmed the definition",
    }
    evidence.write_text(yaml.safe_dump(progress))
    scoped = project_tool._audit_output_contract_summary(tmp_path, "plot-depths")
    assert scoped["analysis"] == "plot-depths"
    assert scoped["status"] == "complete"
    assert scoped["blockers"] == []

    # Without an explicit scope, nothing is excluded: unrelated incomplete outputs still block.
    aggregate = project_tool._audit_output_contract_summary(tmp_path)
    assert aggregate["analysis"] == "project"
    assert aggregate["status"] == "incomplete"
    assert any("sensitivity" in blocker for blocker in aggregate["blockers"])


def test_normal_ci_keeps_correctness_without_routine_benchmarks() -> None:
    checks = Path(".github/workflows/checks.yml").read_text()
    assert "bubblewrap" not in checks and "bwrap" not in checks
    assert "validation-benchmark" not in checks
    assert "just ship-ready" in checks and "fetch-depth: 0" in checks
    assert "validation-benchmark:" in Path("justfile").read_text()


def test_generated_project_derives_registry_contract_offline(tmp_path: Path) -> None:
    target = tmp_path / "study"
    scaffold.create_project(
        target,
        project_name="Study",
        package_name="study",
        description="Study",
        intended_use="Research",
        lock=False,
    )
    _registry(target)
    result = subprocess.run(
        [sys.executable, ".astack/workflow.py", "output-contract", "--analysis", "plot-depths"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["representation"] == "project-registry"
    assert summary["analysis"] == "plot-depths"
    assert summary["required_outputs"][0]["id"] == "depth-validation"
    assert (target / "docs/scientific-outputs.md").exists()


def test_fresh_ds_project_check_reports_undeclared_verify_as_not_run(tmp_path: Path) -> None:
    target = tmp_path / "study"
    scaffold.create_project(
        target,
        project_name="Study",
        package_name="study",
        description="Study",
        intended_use="Research",
        lock=False,
    )
    config = target / ".astack/project.toml"
    text = config.read_text()
    assert "\n# verify = " in text and "\nverify = " not in text  # VERIFY ships undeclared
    # Swap only the CHECK command for a cheap one, so the test needs no environment sync.
    cheap = json.dumps([[sys.executable, "-c", "pass"]])
    config.write_text(text.replace('check = [["just", "check"]]', f"check = {cheap}"))

    def validate(tier: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, ".astack/workflow.py", "validate", tier],
            cwd=target,
            capture_output=True,
            text=True,
            check=False,
        )

    check = validate("check")
    assert check.returncode == 0, check.stdout + check.stderr
    result = json.loads(check.stdout)
    assert result["status"] == "passed"
    assert result["verify"]["status"] == "not-run"
    # VERIFY itself still fails closed until it is declared.
    verify = validate("verify")
    assert verify.returncode != 0 and "not configured" in verify.stdout + verify.stderr
