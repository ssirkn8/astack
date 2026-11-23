"""The research-visuals example stays honest under the existing report check (#296, #300)."""

from __future__ import annotations

import shutil

import pytest
from project_tool_test_support import Path, _project, project_tool

from astack.components import load_components

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "tests/fixtures/research_visuals"
PENDING = (
    "[UNRESOLVED: methodology diagram selection] Choose an option before inserting a diagram.\n"
    "Rebuild the options with `python scripts/lake_visuals.py prototypes` "
    "once the report figures exist."
)
SELECTED = (
    "![Grouped workflow linking simulated inputs to report panels](figures/lake/workflow.png)\n\n"
    "**Figure 1. Simulated inputs feed separate analyses and report panels.**"
)


def _example(root: Path) -> Path:
    for folder in ("evidence", "reports"):
        shutil.copytree(EXAMPLE / "output" / folder, root / folder, dirs_exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    shutil.copyfile(EXAMPLE / "build.py", root / "scripts/lake_visuals.py")
    report = root / "reports/lake.md"
    report.write_text((EXAMPLE / "report.md").read_text(encoding="utf-8"), encoding="utf-8")
    return report


def _outputs(monkeypatch: pytest.MonkeyPatch, *, diagram: str = "current") -> None:
    outputs = [
        {"path": "evidence/lake", "stage": "figures", "label": "current", "reason": "locked"},
        {
            "path": "reports/figures/lake/trends.png",
            "stage": "figures",
            "label": "current",
            "reason": "locked",
        },
        {
            "path": "reports/figures/lake/validation.png",
            "stage": "figures",
            "label": "current",
            "reason": "locked",
        },
        {
            "path": "reports/figures/lake/workflow.png",
            "stage": "diagram",
            "label": diagram,
            "reason": "embedded asset changed",
        },
    ]
    monkeypatch.setattr(
        project_tool, "output_status", lambda root: {"status": "available", "outputs": outputs}
    )


def _kinds(result: dict) -> set[str]:
    return {problem["kind"] for item in result["reports"] for problem in item["problems"]}


def _finalize_with_selection(report: Path) -> None:
    diagram = report.parent / "figures/lake/workflow.png"
    diagram.write_bytes(b"selected diagram")
    text = report.read_text(encoding="utf-8")
    assert PENDING in text
    report.write_text(
        text.replace(PENDING, SELECTED).replace("status: draft", "status: final"),
        encoding="utf-8",
    )


def test_pending_diagram_selection_passes_a_draft_and_blocks_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    report = _example(root)
    _outputs(monkeypatch)
    assert project_tool.report_check(root, [])["status"] == "passed"

    text = report.read_text(encoding="utf-8")
    assert "figures/lake/workflow" not in text
    report.write_text(text.replace("status: draft", "status: final"), encoding="utf-8")

    result = project_tool.report_check(root, [])
    assert result["status"] == "failed"
    assert _kinds(result) == {"unresolved"}


def test_selected_diagram_passes_as_final(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    report = _example(root)
    _outputs(monkeypatch)
    _finalize_with_selection(report)

    assert project_tool.report_check(root, [])["status"] == "passed"


def test_stale_embedded_asset_fails_through_the_selected_diagram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    report = _example(root)
    _outputs(monkeypatch, diagram="outdated")
    _finalize_with_selection(report)

    result = project_tool.report_check(root, [])

    assert result["status"] == "failed"
    assert _kinds(result) == {"outdated-evidence"}


def test_example_keeps_machine_tables_out_of_reports() -> None:
    assert (EXAMPLE / "output/evidence/lake/site_trends.csv").is_file()
    assert (EXAMPLE / "output/reports/tables/lake/site_trends.md").is_file()
    assert not list((EXAMPLE / "output/reports").rglob("*.csv"))


def test_table_value_that_disagrees_with_evidence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    report = _example(root)
    _outputs(monkeypatch)
    text = report.read_text(encoding="utf-8")
    report.write_text(text.replace("[360]", "[361]"), encoding="utf-8")

    result = project_tool.report_check(root, [])

    assert _kinds(result) == {"value-mismatch"}


def test_skill_style_and_template_are_delivered() -> None:
    targets = {component.target for component in load_components(ROOT)}
    for target in (
        ".opencode/skills/research-visuals/SKILL.md",
        ".opencode/skills/research-visuals/journal-serif.mplstyle",
        ".agents/skills/research-visuals/SKILL.md",
        ".claude/skills/research-visuals/SKILL.md",
    ):
        assert target in targets

    template = (ROOT / ".opencode/skills/ds-report-generation/report-template.md").read_text(
        encoding="utf-8"
    )
    assert "| Dataset | Year | Data type / resolution | Source |" in template
    assert "| Source resolution | Analysis or display resolution |" in template
