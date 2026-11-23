from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml
from project_tool_test_support import _declare_scientific_outputs, _set_progress

from astack import output_contract as runtime
from astack.output_contract import output_contract_summary


def _project(root: Path) -> tuple[Path, Path]:
    (root / ".astack").mkdir(exist_ok=True)
    (root / ".astack/project.toml").write_text("", encoding="utf-8")
    return _declare_scientific_outputs(root)


def test_undeclared_and_declared_repeat_reads_do_not_mutate(tmp_path: Path) -> None:
    (tmp_path / ".astack").mkdir()
    assert output_contract_summary(tmp_path)["status"] == "not_declared"
    _project(tmp_path)
    files = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    first = output_contract_summary(tmp_path)
    second = output_contract_summary(tmp_path)
    assert first == second
    assert first["confirmation"] == "confirmed" and first["status"] == "complete"
    assert {p: p.read_bytes() for p in files} == files


def test_confirmation_digest_is_unchanged_by_the_analyses_rename(tmp_path: Path) -> None:
    """Pinned from the release that still called analyses `work_units`."""
    (tmp_path / ".astack").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "evidence").mkdir()
    (tmp_path / ".astack/project.toml").write_text(
        '[scientific_outputs]\npath = "docs/scientific-outputs.yml"\n'
        'evidence_path = "evidence/scientific-outputs.yml"\n'
    )
    (tmp_path / "docs/scientific-outputs.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "research_question": "Q?",
                "outputs": {
                    "fig": {
                        "type": "figure",
                        "role": "headline",
                        "description": "D",
                        "validation": ["qa"],
                    }
                },
                "validation": {"qa": {"description": "V"}},
                "analyses": {"plot": {"outputs": ["fig"]}},
            }
        )
    )
    (tmp_path / "evidence/scientific-outputs.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "outputs": {}, "validation": {}, "confirmations": {}})
    )
    assert (
        output_contract_summary(tmp_path, "plot")["definition_digest"]
        == "14ab09952cf42d21633fca92a63f72479ddbed3cb72b0082bc324c94fdffe3d9"
    )


def test_malformed_and_unsafe_evidence_are_explicit(tmp_path: Path) -> None:
    definitions, progress = _project(tmp_path)
    _set_progress(
        progress,
        "outputs",
        "clean-depths",
        {"status": "complete", "evidence": [".astack/project.toml"]},
    )
    summary = output_contract_summary(tmp_path)
    assert summary["status"] == "incomplete"
    entry = next(item for item in summary["required_outputs"] if item["id"] == "clean-depths")
    assert entry["evidence_status"] == "invalid"
    definitions.write_text("not: [valid")
    assert output_contract_summary(tmp_path)["blockers"]


def test_symlinked_registry_is_rejected(tmp_path: Path) -> None:
    definitions, _ = _project(tmp_path)
    outside = tmp_path.parent / f"outside-{tmp_path.name}.yml"
    outside.write_bytes(definitions.read_bytes())
    definitions.unlink()
    definitions.symlink_to(outside)
    summary = output_contract_summary(tmp_path)
    assert summary["status"] == "unknown"
    assert summary["blockers"]
    outside.unlink()


@pytest.mark.parametrize("status", ["planned", "blocked", "deferred", "complete"])
def test_missing_required_evidence_never_passes(tmp_path: Path, status: str) -> None:
    _, progress = _project(tmp_path)
    _set_progress(progress, "validation", "depth-qa", {"status": status, "evidence": []})
    summary = output_contract_summary(tmp_path)
    assert summary["status"] == "incomplete"
    assert summary["confirmation"] == "confirmed"
    assert summary["required_validation"][0]["evidence_status"] != "valid"
    assert any("depth-qa" in blocker for blocker in summary["blockers"])


@pytest.mark.parametrize("link_kind", ["file", "directory"])
@pytest.mark.parametrize("ignored", [False, True])
def test_evidence_symlinks_never_pass(tmp_path: Path, link_kind: str, ignored: bool) -> None:
    _, progress = _project(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("scratch/\n" if ignored else "")
    target = tmp_path / "scratch/result.txt"
    target.parent.mkdir()
    target.write_text("evidence")
    link = tmp_path / "reports/linked"
    link.symlink_to(target if link_kind == "file" else target.parent)
    evidence_path = "reports/linked" if link_kind == "file" else "reports/linked/result.txt"
    _set_progress(
        progress, "validation", "depth-qa", {"status": "complete", "evidence": [evidence_path]}
    )
    summary = output_contract_summary(tmp_path)
    assert summary["confirmation"] == "confirmed"
    assert summary["status"] == "incomplete"
    assert summary["required_validation"][0]["evidence_status"] == "invalid"


def test_missing_confirmation_and_unavailable_yaml_are_not_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, progress = _project(tmp_path)
    data = yaml.safe_load(progress.read_text())
    data["confirmations"] = {}
    progress.write_text(yaml.safe_dump(data))
    assert output_contract_summary(tmp_path)["confirmation"] == "unconfirmed"
    monkeypatch.setattr(runtime, "yaml", None)
    summary = output_contract_summary(tmp_path)
    assert summary["status"] == "unknown"
    assert summary["blockers"]


def test_invalid_yaml_scalars_remain_json_serializable(tmp_path: Path) -> None:
    _, progress = _project(tmp_path)
    _set_progress(
        progress,
        "validation",
        "depth-qa",
        {"status": date(2026, 10, 3), "evidence": [date(2026, 10, 3)]},
    )
    summary = output_contract_summary(tmp_path)
    assert summary["status"] == "incomplete"
    assert json.loads(json.dumps(summary)) == summary
