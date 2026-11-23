from pathlib import Path

import pytest

from astack.components import load_components
from astack.result_contract import (
    make_result_envelope,
    render_command_result,
    validate_result_envelope,
)
from astack.scaffold import create_project

ROOT = Path(__file__).resolve().parents[1]


def _envelope(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "command": "workflow-runtime",
        "outcome": "completed",
        "summary": "Audited the project.",
        "result": {"operation": "audit", "payload": {}},
        "blockers": [],
        "evidence": [],
        "decisions": [],
        "approvals": [],
        "readiness": None,
        "next_action": None,
        "required_input": None,
        "notes": [],
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("outcome", "extra"),
    [
        ("completed", {}),
        ("no_op", {}),
        (
            "blocked",
            {"blockers": [{"id": "missing", "summary": "Required evidence is missing."}]},
        ),
        (
            "failed",
            {"blockers": [{"id": "failed-check", "summary": "A required check failed."}]},
        ),
        ("awaiting_input", {"required_input": "Approve the proposed plan."}),
    ],
)
def test_result_envelope_validates_execution_outcomes(
    outcome: str, extra: dict[str, object]
) -> None:
    assert validate_result_envelope(_envelope(outcome=outcome, **extra))["outcome"] == outcome


@pytest.mark.parametrize("status", ["passed", "failed", "unknown", "skipped", "unavailable"])
def test_evidence_statuses_remain_distinct(status: str) -> None:
    evidence = [
        {"id": "required-check", "summary": "Required check", "required": True, "status": status}
    ]
    result = validate_result_envelope(_envelope(evidence=evidence))
    assert result["evidence"][0]["status"] == status


def test_ready_result_requires_passed_referenced_evidence() -> None:
    evidence = [
        {"id": "integration", "summary": "Integration checks", "required": True, "status": "passed"}
    ]
    ready = {
        "status": "ready",
        "summary": "All declared required checks passed.",
        "required_evidence": ["integration"],
    }
    assert (
        validate_result_envelope(_envelope(evidence=evidence, readiness=ready))["readiness"]
        == ready
    )

    no_evidence = {**ready, "required_evidence": []}
    with pytest.raises(ValueError, match="must identify required evidence"):
        validate_result_envelope(_envelope(readiness=no_evidence))

    for status in ("failed", "unknown", "skipped", "unavailable"):
        not_passed = [{**evidence[0], "status": status}]
        with pytest.raises(ValueError, match="require every referenced evidence item to pass"):
            validate_result_envelope(_envelope(evidence=not_passed, readiness=ready))


def test_notes_cannot_replace_typed_readiness_evidence() -> None:
    readiness = {
        "status": "ready",
        "summary": "Looks good according to a note.",
        "required_evidence": ["integration"],
    }
    evidence = [{"id": "integration", "summary": "Checks", "required": True, "status": "unknown"}]
    with pytest.raises(ValueError, match="require every referenced evidence item to pass"):
        validate_result_envelope(
            _envelope(evidence=evidence, readiness=readiness, notes=["All checks passed"])
        )


@pytest.mark.parametrize("status", ["passed", "failed", "skipped", "unavailable"])
def test_ready_result_cannot_omit_required_evidence(status: str) -> None:
    evidence = [
        {"id": "integration", "summary": "Integration", "required": True, "status": "passed"},
        {"id": "other", "summary": "Other check", "required": True, "status": status},
    ]
    readiness = {"status": "ready", "summary": "Ready", "required_evidence": ["integration"]}
    with pytest.raises(ValueError, match="every required evidence item"):
        validate_result_envelope(_envelope(evidence=evidence, readiness=readiness))


@pytest.mark.parametrize("outcome", ["blocked", "failed", "awaiting_input"])
def test_ready_result_rejects_unsuccessful_execution(outcome: str) -> None:
    evidence = [{"id": "check", "summary": "Check", "required": True, "status": "passed"}]
    readiness = {"status": "ready", "summary": "Ready", "required_evidence": ["check"]}
    with pytest.raises(ValueError, match="unsuccessful execution outcome"):
        validate_result_envelope(_envelope(outcome=outcome, evidence=evidence, readiness=readiness))


def test_retired_workflow_context_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported fields: workflow_context"):
        validate_result_envelope(_envelope(workflow_context=None))


def test_markdown_renderer_does_not_dump_command_payload() -> None:
    envelope = make_result_envelope(
        command="workflow-runtime",
        outcome="completed",
        summary="Audit verified.",
        result={
            "operation": "audit",
            "payload": {"private_detail": {"nested": "do-not-render"}},
        },
        next_action="Review the audit findings.",
    )

    rendered = render_command_result(envelope)

    assert "Audit verified." in rendered
    assert "Review the audit findings." in rendered
    assert "do-not-render" not in rendered
    assert '{"operation"' not in rendered


def test_commands_end_with_their_skill_output_not_a_shared_envelope() -> None:
    for path in sorted((ROOT / ".opencode" / "commands").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert "End with the skill's Output section" in text, path
        assert "command-results" not in text, path
        assert "result-contract" not in text, path


def test_retired_command_results_skill_is_not_scaffolded(tmp_path: Path) -> None:
    targets = {component.target for component in load_components(ROOT)}
    assert ".opencode/skills/command-results/SKILL.md" not in targets
    assert ".astack/result_contract.py" not in targets  # No vendored consumer remains.

    target = tmp_path / "generated-project"
    create_project(
        target,
        project_name="Result Contract Test",
        package_name="result_contract_test",
        description="Verify generated result contracts.",
        intended_use="Exercise scaffolded result validation.",
        lock=False,
    )
    for folder in (".opencode", ".agents", ".claude"):
        assert not (target / folder / "skills/command-results").exists(), folder
    assert not (target / ".astack/result_contract.py").exists()
    assert not (target / ".opencode/commands/workflow-run.md").exists()
    assert not (target / ".opencode/skills/workflow-maintenance/SKILL.md").exists()
