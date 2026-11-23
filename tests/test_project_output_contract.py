from __future__ import annotations

from project_tool_test_support import (
    Path,  # noqa: F401
    _declare_scientific_outputs,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    _set_progress,  # noqa: F401
    json,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    sys,  # noqa: F401
    yaml,  # noqa: F401
)


def _registry_findings(target: Path) -> list[dict[str, object]]:
    return [
        item
        for item in project_tool.audit(target)["findings"]
        if item["check"] == "scientific-output-registry"
    ]


def test_vendored_output_contract_summary_and_audit_share_confirmation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    # The generated runtime must load its own helper, not an installed astack package.
    command = [sys.executable, ".astack/workflow.py", "output-contract"]
    undeclared = subprocess.run(command, cwd=target, check=True, capture_output=True, text=True)
    assert json.loads(undeclared.stdout)["status"] == "not_declared"
    definitions, _ = _declare_scientific_outputs(target)
    before = definitions.read_bytes()
    shown = subprocess.run(command, cwd=target, check=True, capture_output=True, text=True)
    summary = json.loads(shown.stdout)
    assert summary["confirmation"] == "confirmed"
    assert summary["status"] == "complete"
    assert summary["contributing_analyses"] == ["main"]
    assert project_tool.audit(target)["output_contract"] == summary
    assert definitions.read_bytes() == before
    scoped = subprocess.run(
        [*command, "--analysis", "main"], cwd=target, check=True, capture_output=True, text=True
    )
    assert json.loads(scoped.stdout)["analysis"] == "main"
    # `--work-unit` is the option's old name and keeps working.
    legacy = subprocess.run(
        [*command, "--work-unit", "main"], cwd=target, check=True, capture_output=True, text=True
    )
    assert json.loads(legacy.stdout) == json.loads(scoped.stdout)


def test_audit_accepts_complete_declared_scientific_outputs(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare_scientific_outputs(target)

    result = project_tool.audit(target)

    assert not any(item["area"] == "scientific-output" for item in result["findings"])
    assert result["verdicts"]["scientific_credibility"] == "needs-interpretive-review"


def test_audit_rejects_unsafe_ignored_or_self_referencing_evidence(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _, progress = _declare_scientific_outputs(target)
    _git_baseline(target)
    for evidence in (
        "../private.csv",
        "reports/audits/private-evidence.md",
        "docs/scientific-outputs.yml",
    ):
        if not evidence.startswith(".."):
            (target / evidence).parent.mkdir(parents=True, exist_ok=True)
            (target / evidence).touch(exist_ok=True)
            if evidence.startswith("reports/audits"):
                (target / evidence).write_text("private\n", encoding="utf-8")
        _set_progress(
            progress, "outputs", "clean-depths", {"status": "complete", "evidence": [evidence]}
        )
        findings = _registry_findings(target)
        assert any("clean-depths" in str(item["message"]) for item in findings), evidence


def test_audit_fails_incomplete_required_validation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _, progress = _declare_scientific_outputs(target)
    _set_progress(progress, "validation", "depth-qa", {"status": "deferred", "evidence": []})

    findings = _registry_findings(target)

    assert any("depth-qa" in str(item["message"]) for item in findings)
    assert all(item["priority"] == "P1" for item in findings)


def test_audit_reports_unhashable_output_status_instead_of_crashing(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _, progress = _declare_scientific_outputs(target)
    _set_progress(progress, "validation", "depth-qa", {"status": ["complete"], "evidence": []})

    assert any("invalid status" in str(item["message"]) for item in _registry_findings(target))


def test_audit_reports_non_utf8_registry_instead_of_crashing(tmp_path: Path) -> None:
    target = _project(tmp_path)
    definitions, _ = _declare_scientific_outputs(target)
    definitions.write_bytes(b"\xff\xfe")

    assert any("malformed" in str(item["message"]) for item in _registry_findings(target))


def test_retired_single_file_contract_names_its_replacement(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[output_contract]\npath = "docs/output-contracts/current.yml"\n',
        encoding="utf-8",
    )

    findings = _registry_findings(target)

    assert len(findings) == 1
    assert "[output_contract] single-file contracts were removed" in str(findings[0]["message"])
    assert "[scientific_outputs]" in str(findings[0]["message"])


def test_registry_using_work_units_is_told_to_rename_it(tmp_path: Path) -> None:
    target = _project(tmp_path)
    definitions, progress = _declare_scientific_outputs(target)
    confirmed = project_tool.output_contract_summary(target, "main")
    registry = yaml.safe_load(definitions.read_text(encoding="utf-8"))
    registry["work_units"] = registry.pop("analyses")
    definitions.write_text(yaml.safe_dump(registry), encoding="utf-8")

    findings = _registry_findings(target)
    assert any(
        "Rename `work_units` to `analyses` in docs/scientific-outputs.yml" in str(item["message"])
        for item in findings
    )
    # After the rename, the recorded confirmation still matches: the digest is unchanged.
    registry["analyses"] = registry.pop("work_units")
    definitions.write_text(yaml.safe_dump(registry), encoding="utf-8")
    assert project_tool.output_contract_summary(target, "main") == confirmed
    assert progress.is_file()


def _scoped_findings(target: Path, analysis: str) -> list[str]:
    result = project_tool.audit(target, output_scope=analysis)
    return [item["message"] for item in result["findings"] if item["area"] == "scientific-output"]


def test_named_analysis_that_exists_and_is_confirmed_passes(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare_scientific_outputs(target)

    assert _scoped_findings(target, "main") == []


def test_mistyped_analysis_fails_and_lists_the_declared_ones(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare_scientific_outputs(target)

    findings = _scoped_findings(target, "mian")

    assert len(findings) == 1  # Reported once, not once per audit pass.
    assert all("'mian' is not in the registry's analyses (declared: main)" in f for f in findings)


def test_named_analysis_without_a_registry_fails(tmp_path: Path) -> None:
    target = _project(tmp_path)

    assert project_tool.audit(target)["output_contract"]["status"] == "not_declared"
    assert any("declares no [scientific_outputs]" in f for f in _scoped_findings(target, "main"))


def test_named_analysis_whose_definition_changed_needs_reconfirmation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    definitions, _ = _declare_scientific_outputs(target)
    registry = yaml.safe_load(definitions.read_text(encoding="utf-8"))
    registry["outputs"]["depth-figure"]["description"] = "A different figure."
    definitions.write_text(yaml.safe_dump(registry), encoding="utf-8")

    summary = project_tool.audit(target, output_scope="main")["output_contract"]

    assert summary["confirmation"] == "reconfirmation_required"
    assert _scoped_findings(target, "main")


def test_ship_ready_passes_the_analysis_to_the_audit(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare_scientific_outputs(target)
    dry = subprocess.run(
        ["just", "--dry-run", "ship-ready", "mian"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--strict --analysis 'mian'" in dry.stderr
    unnamed = subprocess.run(
        ["just", "--dry-run", "ship-ready"], cwd=target, check=True, capture_output=True, text=True
    )
    assert "--analysis ''" in unnamed.stderr
