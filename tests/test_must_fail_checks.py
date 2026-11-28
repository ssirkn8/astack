"""Check the checker: break a working project on purpose and require the audit to block it.

Each case first confirms the check is quiet on a sound project, so a check that always fires
cannot pass. Covers checks guarding raw data, provenance, reproducibility, and private data.
The known false passes already have their own tests:
- A do-nothing pipeline: test_audit_reproduction_noop_command_is_not_reproduced.
- A `trust me` source: tests/test_project_source_verification.py.
- An empty project: tests/test_project_reproducibility_verdict.py and tests/test_first_run.py.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from project_tool_test_support import _git_baseline, _project, project_tool

pytestmark = pytest.mark.integration


def _edit_config(target: Path, old: str, new: str) -> None:
    config = target / ".astack/project.toml"
    text = config.read_text(encoding="utf-8")
    assert old in text
    config.write_text(text.replace(old, new, 1), encoding="utf-8")


def _append_config(target: Path, text: str) -> None:
    config = target / ".astack/project.toml"
    config.write_text(config.read_text(encoding="utf-8") + text, encoding="utf-8")


def _source_record(target: Path) -> dict[str, Any]:
    raw = target / "data/raw/table.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("a,b\n1,2\n", encoding="utf-8")
    known = lambda value: {"status": "known", "value": value}  # noqa: E731
    return {
        "title": "Table",
        "path": "data/raw/table.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "raw_checksum": "sha256:" + hashlib.sha256(raw.read_bytes()).hexdigest(),
        "license": known("CC0-1.0"),
        "sensitivity": known("public"),
        "redistribution": known("allowed"),
        "allowed_outputs": ["internal-analysis"],
        "citation": known("Provider (2026)"),
    }


def _write_sources(target: Path, sources: dict[str, Any]) -> None:
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources}, sort_keys=False),
        encoding="utf-8",
    )


def _with_source(target: Path) -> None:
    _write_sources(target, {"table": _source_record(target)})


def _with_local_only_source(target: Path) -> None:
    _material(target)
    _write_sources(target, {"table": {**_source_record(target), "availability": "local_only"}})


def _material(target: Path) -> None:
    _edit_config(target, 'stage = "exploratory"', 'stage = "material"')
    _edit_config(target, "components = []", 'components = ["analysis"]')
    _append_config(
        target,
        '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
    )


def _material_dvc(target: Path) -> None:
    _edit_config(target, "tools = []", 'tools = ["dvc"]')
    _material(target)
    _append_config(target, 'engine = "dvc"\n')
    (target / "dvc.yaml").write_text(
        "stages:\n  analyze:\n    cmd: python scripts/pipeline.py\n"
        "    outs:\n      - data/processed/analysis/result.txt\n",
        encoding="utf-8",
    )


def _with_acquisition(target: Path) -> None:
    _material(target)
    _append_config(target, '\n[acquisition]\ncommand = ["python", "scripts/acquire.py"]\n')


def _empty_dvc_pipeline(target: Path) -> None:
    (target / "dvc.yaml").write_text("stages: {}\n", encoding="utf-8")


def _leave_uncommitted_work(target: Path) -> None:
    (target / "uncommitted.txt").write_text("work\n", encoding="utf-8")


def _force_add(target: Path, relative: str) -> None:
    subprocess.run(["git", "add", "-f", relative], cwd=target, check=True, capture_output=True)


def _remove_reproduce_recipe(target: Path) -> None:
    justfile = target / "justfile"
    text = justfile.read_text(encoding="utf-8")
    updated = re.sub(r"(?m)^reproduce\b[^\n]*:\n(?:[ \t]+[^\n]*\n)+", "", text)
    assert updated != text
    justfile.write_text(updated, encoding="utf-8")


def _escape_evidence_directory(target: Path) -> None:
    outside = target.parent / "outside-evidence"
    outside.mkdir()
    evidence = target / "evidence/analysis"
    evidence.parent.mkdir(exist_ok=True)
    evidence.symlink_to(outside, target_is_directory=True)


def _commit_private_env(target: Path) -> None:
    (target / ".env").write_text("SETTING=private\n", encoding="utf-8")
    _force_add(target, ".env")


def _add_oversized_file(target: Path) -> None:
    with (target / "big.parquet").open("wb") as handle:
        handle.truncate(10 * 1024 * 1024 + 1)
    _force_add(target, "big.parquet")


def _add_unscannable_file(target: Path) -> None:
    (target / "notes.txt").write_bytes(b"\xff" * (3 * 1024 * 1024))
    _force_add(target, "notes.txt")


def _break_source_field(field: str, value: object) -> Callable[[Path], None]:
    def apply(target: Path) -> None:
        _write_sources(target, {"table": {**_source_record(target), field: value}})

    return apply


def _stop_declaring_local_only(target: Path) -> None:
    record = {**_source_record(target), "availability": "ci_available"}
    _write_sources(target, {"table": record})


def _no_change(_target: Path) -> None:
    return None


@dataclass(frozen=True)
class Case:
    check: str
    prepare: Callable[[Path], None]
    defect: Callable[[Path], None]
    ci: bool = False


CASES = [
    # Provenance: evidence about where the data came from.
    Case("source-record", _with_source, lambda t: _write_sources(t, {"table": "not a record"})),
    Case(
        "source-evidence-state", _with_source, _break_source_field("license", {"status": "known"})
    ),
    Case("source-availability", _with_source, _break_source_field("availability", "everywhere")),
    Case("ci-source-declaration", _with_local_only_source, _stop_declaring_local_only, ci=True),
    # Raw data and acquisition.
    Case(
        "data-layout", _no_change, lambda t: _edit_config(t, 'layout = "standard"', 'layout = "x"')
    ),
    Case(
        "acquisition-command",
        _with_acquisition,
        lambda t: _edit_config(t, '\ncommand = ["python", "scripts/acquire.py"]', "\ncommand = []"),
    ),
    # Reproducibility: a material result must have a real way to be rerun.
    Case("reproduction-engine", _material, lambda t: _append_config(t, 'engine = "other"\n')),
    Case(
        "reproduction-command",
        _material,
        lambda t: _edit_config(
            t, '\ncommand = ["python", "scripts/pipeline.py"]', "\ncommand = []"
        ),
    ),
    Case(
        "reproduction-component",
        _material,
        # The leading newline skips the commented example in the generated config.
        lambda t: _edit_config(t, '\ncomponent = "analysis"', '\ncomponent = "undeclared"'),
    ),
    Case("reproduction-interface", _material, _remove_reproduce_recipe),
    Case("reproduction-record-path", _material, _escape_evidence_directory),
    Case(
        "dvc-capability",
        _material_dvc,
        lambda t: _edit_config(t, 'tools = ["dvc"]', "tools = []"),
    ),
    Case("dvc-pipeline", _material_dvc, _empty_dvc_pipeline),
    # Private data leaving the project.
    Case("sensitive-file", _no_change, _commit_private_env),
    Case("large-file", _no_change, _add_oversized_file),
    Case("secret-scan-unavailable", _no_change, _add_unscannable_file),
    # Evidence must describe a committed state.
    Case("worktree-cleanliness", _no_change, _leave_uncommitted_work),
]


def _blockers(result: dict[str, Any]) -> set[str]:
    return {item["check"] for item in result["findings"] if item["priority"] in {"P0", "P1"}}


def _commit_defect(target: Path) -> None:
    """A committed defect keeps a dirty worktree from failing the audit on its own."""
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "x"],
        cwd=target,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.check)
def test_a_broken_project_is_blocked(tmp_path: Path, case: Case) -> None:
    target = _project(tmp_path)
    case.prepare(target)
    _git_baseline(target)
    quiet = project_tool.audit(target, strict=True, ci=case.ci)
    assert case.check not in _blockers(quiet), "The check fired before the project was broken"
    # A material project has blockers until its run is recorded, and any later commit invalidates
    # that record. Only an exploratory project can show this one defect flips the verdict.
    exploratory = quiet["analysis_stage"] != "material"
    if exploratory:
        assert not _blockers(quiet)
        assert quiet["verdicts"]["release_readiness"] != "fail"

    case.defect(target)
    if case.check != "worktree-cleanliness":
        _commit_defect(target)

    result = project_tool.audit(target, strict=True, ci=case.ci)
    assert case.check in _blockers(result), f"{case.check} missed a deliberately broken project"
    if exploratory:
        assert result["verdicts"]["release_readiness"] == "fail"


def test_missing_yaml_support_blocks_instead_of_skipping_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _with_source(target)
    _git_baseline(target)
    assert not _blockers(project_tool.audit(target, strict=True))

    from workflow.project_runtime import sources

    monkeypatch.setattr(sources, "yaml", None)

    result = project_tool.audit(target, strict=True)
    assert "sources-parser" in _blockers(result)
    assert result["verdicts"]["release_readiness"] == "fail"


def test_an_unverifiable_worktree_blocks_instead_of_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _git_baseline(target)
    assert not _blockers(project_tool.audit(target, strict=True))
    real_run = project_tool._run

    def failing_status(command: list[str], *args: Any, **kwargs: Any) -> Any:
        if command[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 128, "", "status failed")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(project_tool, "_run", failing_status)

    result = project_tool.audit(target, strict=True)
    assert "worktree-status" in _blockers(result)
    assert result["verdicts"]["release_readiness"] == "fail"
