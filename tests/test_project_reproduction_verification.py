"""A command exiting 0 is not reproduction. Outputs must be declared, digested and re-verified."""

from __future__ import annotations

import json

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
)

# End-to-end reproduction runs: integration tier, to keep CHECK within its CI budget.
pytestmark = pytest.mark.integration

PIPELINE = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "out = Path('data/processed/analysis')\n"
    "out.mkdir(parents=True, exist_ok=True)\n"
    "(out / 'result.txt').write_text(os.environ.get('RESULT', 'result') + '\\n')\n"
)


def _material(target: Path, extra: str = "", outputs: str | None = None) -> None:
    config = target / ".astack/project.toml"
    declared = outputs if outputs is not None else '["data/processed/analysis/result.txt"]'
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\n'
        f"outputs = {declared}\n" + extra,
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(PIPELINE, encoding="utf-8")
    (target / "tests/test_pipeline.py").write_text(
        "def test_pipeline_contract():\n    assert True\n", encoding="utf-8"
    )


def _commit(target: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-m", message],
        cwd=target,
        check=True,
        capture_output=True,
    )


def _checks(target: Path, *, strict: bool = False) -> set[str]:
    return {item["check"] for item in project_tool.audit(target, strict=strict)["findings"]}


def test_audit_reproduction_noop_command_is_not_reproduced(tmp_path: Path) -> None:
    """Regression for the pre-publication audit reproduction (row R1)."""
    target = _project(tmp_path)
    _material(target, outputs="[]")
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'command = ["python", "scripts/pipeline.py"]', 'command = ["python3", "-c", "pass"]'
        ),
        encoding="utf-8",
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="declare reproduction.outputs"):
        project_tool.run_reproduction(target)
    assert "reproduction-outputs" in _checks(target)


def test_declared_output_must_be_written_by_the_run(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target, outputs='["data/processed/analysis/stale.txt"]')
    stale = target / "data/processed/analysis/stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="not written by this run"):
        project_tool.run_reproduction(target)


def test_first_run_succeeds_but_is_not_verified(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)

    result = project_tool.run_reproduction(target, promote=True)

    assert result["status"] == "command-succeeded"
    record = result["provenance"]
    assert record["schema_version"] == 2
    digest = project_tool._sha256(target / "data/processed/analysis/result.txt")
    assert record["outputs"] == {
        "data/processed/analysis/result.txt": {"kind": "file", "sha256": digest}
    }
    assert record["verification"]["status"] == "unverified"
    _commit(target, "reference")
    checks = _checks(target, strict=True)
    assert "run-provenance" not in checks
    assert "reproduction-unverified" in checks
    verdict = project_tool.audit(target)["verdicts"]["computational_reproducibility"]
    assert verdict != "pass"


def test_rerun_matching_promoted_reference_is_verified(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)
    reference = project_tool.run_reproduction(target, promote=True)
    _commit(target, "reference")

    result = project_tool.run_reproduction(target, promote=True, verify=True)

    assert result["status"] == "verified"
    verification = result["provenance"]["verification"]
    assert verification["status"] == "verified"
    assert (
        verification["reference"]
        == Path(reference["promoted_record"]).relative_to(target).as_posix()
    )
    assert verification["methods"] == {"data/processed/analysis/result.txt": "digest"}
    _commit(target, "verified")
    checks = _checks(target, strict=True)
    assert "run-provenance" not in checks
    assert "reproduction-unverified" not in checks


def test_altered_or_deleted_output_invalidates_the_record(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)
    project_tool.run_reproduction(target, promote=True)
    _commit(target, "reference")
    project_tool.run_reproduction(target, promote=True)
    _commit(target, "verified")
    result = target / "data/processed/analysis/result.txt"

    result.write_text("tampered\n", encoding="utf-8")
    assert "run-provenance" in _checks(target, strict=True)
    result.unlink()
    assert "run-provenance" in _checks(target, strict=True)


def test_rerun_with_different_bytes_is_a_mismatch_and_never_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)
    project_tool.run_reproduction(target, promote=True)
    _commit(target, "reference")
    durable = sorted((target / "evidence/analysis/runs").glob("*.json"))

    monkeypatch.setenv("RESULT", "different")
    result = project_tool.run_reproduction(target, promote=True)

    assert result["status"] == "mismatch"
    assert "promoted_record" not in result
    assert sorted((target / "evidence/analysis/runs").glob("*.json")) == durable
    mismatch = result["provenance"]["verification"]["mismatches"][0]
    assert mismatch["path"] == "data/processed/analysis/result.txt"


def test_verify_mode_requires_a_reference(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)

    result = project_tool.run_reproduction(target, verify=True)

    assert result["status"] == "command-succeeded"
    assert "no promoted reference" in result["provenance"]["verification"]["reason"]


def test_project_tolerance_check_can_accept_nondeterministic_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _material(
        target,
        extra=(
            "\n[reproduction.tolerance]\n"
            '"data/processed/analysis/result.txt" = ["python", "scripts/compare.py"]\n'
        ),
    )
    (target / "scripts/compare.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "record = json.loads(Path(os.environ['ASTACK_REFERENCE_RECORD']).read_text())\n"
        "assert os.environ['ASTACK_OUTPUT'] in record['outputs']\n"
        "assert len(os.environ['ASTACK_REFERENCE_SHA256']) == 64\n"
        "sys.exit(0 if Path(os.environ['ASTACK_OUTPUT']).read_text().strip() else 1)\n",
        encoding="utf-8",
    )
    _git_baseline(target)
    project_tool.run_reproduction(target, promote=True)
    _commit(target, "reference")

    monkeypatch.setenv("RESULT", "within tolerance")
    result = project_tool.run_reproduction(target)

    assert result["status"] == "verified"
    methods = result["provenance"]["verification"]["methods"]
    assert methods == {"data/processed/analysis/result.txt": "project-tolerance"}


def test_tolerance_check_that_fails_is_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _material(
        target,
        extra=(
            "\n[reproduction.tolerance]\n"
            '"data/processed/analysis/result.txt" = ["python", "-c", "raise SystemExit(3)"]\n'
        ),
    )
    _git_baseline(target)
    project_tool.run_reproduction(target, promote=True)
    _commit(target, "reference")

    monkeypatch.setenv("RESULT", "outside tolerance")
    assert project_tool.run_reproduction(target)["status"] == "mismatch"


def test_schema_one_records_no_longer_count(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)
    result = project_tool.run_reproduction(target)
    path = Path(result["record"])
    record = json.loads(path.read_text(encoding="utf-8"))
    record["schema_version"] = 1
    path.write_text(json.dumps(record), encoding="utf-8")

    assert "run-provenance" in _checks(target)


def test_touching_only_a_declared_directory_does_not_refresh_its_files(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target, outputs='["data/processed/analysis"]')
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        "out = Path('data/processed/analysis')\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'touch.tmp').write_text('x')\n"
        "(out / 'touch.tmp').unlink()\n",
        encoding="utf-8",
    )
    stale = target / "data/processed/analysis/result.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old\n", encoding="utf-8")
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="not written by this run"):
        project_tool.run_reproduction(target)


def test_output_tree_digest_frames_entries_unambiguously(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "a").write_bytes(b"\0b\0Y")
    (two / "a").write_bytes(b"")
    (two / "b").write_bytes(b"Y")

    assert project_tool._output_tree_sha256(one) != project_tool._output_tree_sha256(two)


def test_verified_record_requires_its_reference_to_stay_intact(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)
    reference = Path(project_tool.run_reproduction(target, promote=True)["promoted_record"])
    _commit(target, "reference")
    project_tool.run_reproduction(target, promote=True)
    _commit(target, "verified")
    assert "reproduction-unverified" not in _checks(target, strict=True)

    reference.write_text("{}\n", encoding="utf-8")
    _commit(target, "tamper with reference")

    assert "reproduction-unverified" in _checks(target, strict=True)


def test_output_tree_rejects_directory_symlinks(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (tree / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    with pytest.raises(RuntimeError, match="non-regular"):
        project_tool._output_tree_sha256(tree)
