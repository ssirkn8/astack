"""Explicit CI scope never manufactures missing scientific evidence."""

import subprocess
import sys

import pytest
from project_tool_test_support import (
    _append_validation,
    _git_baseline,
    _project,
    project_tool,
    yaml,
)

pytestmark = pytest.mark.integration


def local_project(tmp_path):
    root = _project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(
        config.read_text()
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[ci]\nmode = "local-only-sources"\n'
        '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\nrandom_seed = 42\n'
        'outputs = ["data/processed/analysis/result.txt"]\n'
    )
    (root / "scripts/pipeline.py").write_text('print("project-owned pipeline")\n')
    (root / "tests/test_pipeline.py").write_text(
        "def test_meaningful_contract():\n    assert 2+2 == 4\n"
    )
    source = {
        "title": "Private fixture",
        "path": "data/raw/private.csv",
        "origin": "Original supplied fixture",
        "provider": "Fixture provider",
        "access_date": "2026-11-03",
        "version": "snapshot-1",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "private",
        "allowed_outputs": ["aggregate"],
        "redistribution": "prohibited",
        "citation": "Fixture source (2026)",
        "availability": "local_only",
    }
    (root / "data/sources.yml").write_text(yaml.safe_dump({"sources": {"private": source}}))
    (root / "justfile").write_text(
        f'check:\n\t"{sys.executable}" -c "print(\'engineering-check\')"\n\n'
        f'integration:\n\t"{sys.executable}" -c "print(\'engineering-integration\')"\n\n'
        "ship-ready:\n\tuv run --locked python .astack/workflow.py "
        "audit --format text --run-tools --strict\n\n"
        "ci-check:\n\tuv run --locked python .astack/workflow.py "
        "audit --format text --run-tools --strict --ci\n\n"
        "reproduce:\n\tuv run --locked python .astack/workflow.py reproduce\n"
    )
    workflow = root / ".github/workflows/checks.yml"
    workflow.write_text(workflow.read_text().replace("run: just ship-ready", "run: just ci-check"))
    # A material analysis must declare VERIFY, or strict ship-ready blocks on it.
    _append_validation(root, "verify", [[sys.executable, "-c", "print('science-verify')"]])
    # A material analysis is expected to have a report.
    (root / "reports/study.md").write_text("---\ntitle: Study\nstatus: draft\n---\n\nDraft.\n")
    _git_baseline(root)
    return root


def test_limited_ci_passes_engineering_and_keeps_science_unverified(tmp_path):
    root = local_project(tmp_path)
    result = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    blocking = [f for f in result["findings"] if f["priority"] in {"P0", "P1"}]
    assert not blocking, blocking
    assert result["ci_validation"]["status"] == "pass"
    assert result["verdicts"]["computational_reproducibility"] == "not-assessed"
    assert result["verdicts"]["release_readiness"] == "not-assessed"
    assert result["ci_validation"]["source_identity"] == "not-verified-in-ci"
    assert result["ci_validation"]["reproduction"] == "not-verified-in-ci"
    assert not project_tool._strict_ready(result)
    local = project_tool.audit(root, strict=True)
    assert {"source-missing", "run-provenance"} <= {f["check"] for f in local["findings"]}
    assert local["verdicts"]["release_readiness"] == "fail"
    assert not any(f["check"] == "ci-ship-ready" for f in local["findings"])


def test_cli_scoped_pass_is_explicit_and_local_cli_still_fails(tmp_path):
    root = local_project(tmp_path)
    command = [sys.executable, ".astack/workflow.py", "audit", "--strict", "--run-tools"]
    ci = subprocess.run(command + ["--ci"], cwd=root, capture_output=True, text=True)
    assert ci.returncode == 0, ci.stdout + ci.stderr
    assert "NOT VERIFIED" in ci.stdout
    local = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert local.returncode != 0


@pytest.mark.parametrize(
    "change", ["no-policy", "no-availability", "checksum", "software", "no-verify"]
)
def test_ci_does_not_relax_undeclared_or_checkable_failures(tmp_path, change):
    root = local_project(tmp_path)
    if change == "no-policy":
        config = root / ".astack/project.toml"
        config.write_text(
            config.read_text().replace('mode = "local-only-sources"', 'mode = "invalid"')
        )
    elif change == "no-availability":
        sources = root / "data/sources.yml"
        sources.write_text(
            sources.read_text().replace("availability: local_only", "availability: ci_available")
        )
    elif change == "checksum":
        (root / "data/raw/private.csv").write_text("tampered")
    elif change == "no-verify":
        config = root / ".astack/project.toml"
        config.write_text(
            "".join(
                line
                for line in config.read_text().splitlines(keepends=True)
                if not line.startswith("verify = ")
            )
        )
    else:
        justfile = root / "justfile"
        justfile.write_text(
            justfile.read_text().replace("print('engineering-check')", "raise SystemExit(1)")
        )
    result = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert result["ci_validation"]["status"] == "fail"
    assert result["verdicts"]["release_readiness"] == "fail"


def test_ci_without_executed_tools_is_not_a_pass(tmp_path):
    result = project_tool.audit(local_project(tmp_path), strict=True, ci=True)
    assert result["ci_validation"]["status"] == "not-assessed"


def test_ci_recipe_cannot_be_masked_or_only_a_comment(tmp_path):
    root = local_project(tmp_path)
    justfile = root / "justfile"
    justfile.write_text(justfile.read_text().replace("--strict --ci", "--strict --ci || true"))
    result = project_tool.audit(root, strict=True, ci=True)
    assert any(f["check"] == "ci-ship-ready" for f in result["findings"])


@pytest.mark.parametrize("recipe", ["ci-check", "ship-ready"])
def test_script_recipes_cannot_hide_audit_failure(tmp_path, recipe):
    root = local_project(tmp_path)
    justfile = root / "justfile"
    text = justfile.read_text()
    expected = "uv run --locked python .astack/workflow.py audit --format text --run-tools --strict"
    if recipe == "ci-check":
        expected += " --ci"
    masked = f"{recipe}:\n\t#!/bin/sh\n\t{expected}\n\ttrue"
    text = text.replace(f"{recipe}:\n\t{expected}", masked)
    justfile.write_text(text)
    assert not project_tool._ship_ready_runs_strict_audit(text, ci=recipe == "ci-check")
    report = project_tool.audit(root, strict=True, ci=True)
    assert any(
        f["check"] == ("ci-ship-ready" if recipe == "ci-check" else "ship-ready")
        for f in report["findings"]
    )


def test_plain_ci_recipe_propagates_actual_child_failure(tmp_path):
    import os

    root = local_project(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text("#!/bin/sh\nexit 7\n")
    uv.chmod(0o755)
    run = subprocess.run(
        ["just", "ci-check"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
    )
    assert run.returncode != 0
