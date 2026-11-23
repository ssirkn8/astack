from __future__ import annotations

import json

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    _set_capabilities,  # noqa: F401
    cast,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
)

pytestmark = pytest.mark.integration


def test_reproduction_rejects_unsafe_component_path(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["../../../escape"]')
        + (
            '\n[reproduction]\ncomponent = "../../../escape"\n'
            'command = ["python", "scripts/pipeline.py"]\n'
        ),
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text("pass\n", encoding="utf-8")
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="lowercase slug"):
        project_tool.run_reproduction(target)
    assert not (tmp_path / "escape").exists()


def test_legacy_material_pipeline_writes_local_run_records(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text[: text.index("\n[outputs]")].replace('stage = "exploratory"', 'stage = "material"')
        + '\n[reproduction]\ncommand = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/result.parquet"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        "Path('reports/qa').mkdir(parents=True, exist_ok=True)\n"
        "Path('reports/qa/metrics.json').write_text('{}\\n')\n"
        "Path('data/processed').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/processed/result.parquet').write_bytes(b'result')\n",
        encoding="utf-8",
    )
    (target / "tests/test_pipeline.py").write_text(
        "def test_pipeline_contract():\n    assert True\n", encoding="utf-8"
    )
    _git_baseline(target)

    result = project_tool.run_reproduction(target)

    assert Path(result["record"]).is_relative_to(target / ".cache/runs/project")


def test_dvc_reproduction_uses_pipeline_without_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\nengine = "dvc"\ncomponent = "analysis"\n',
        encoding="utf-8",
    )
    (target / ".dvc").mkdir()
    (target / ".dvc/.gitignore").write_text("/config.local\n/cache\n", encoding="utf-8")
    (target / ".dvc/config").write_text("[core]\nanalytics = false\n", encoding="utf-8")
    (target / "dvc.yaml").write_text(
        "stages:\n"
        "  analysis:\n"
        "    cmd: python scripts/pipeline.py\n"
        "    deps:\n"
        "      - scripts/pipeline.py\n"
        "    outs:\n"
        "      - data/processed/analysis/result.txt\n",
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text("print('pipeline')\n", encoding="utf-8")
    (target / "tests/test_pipeline.py").write_text(
        "def test_pipeline_contract():\n    assert True\n", encoding="utf-8"
    )
    _git_baseline(target)
    calls: list[list[str]] = []
    original_run = project_tool._run

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[-1] == project_tool.DVC_PIPELINE_PROBE:
            stage = {
                "name": "analysis",
                "frozen": False,
                "cmd": "python scripts/pipeline.py",
                "deps": [{"path": "scripts/pipeline.py"}],
                "outs": [{"path": "data/processed/analysis/result.txt", "dir": False}],
            }
            payload = {"status": {"analysis": ["changed"]}, "stages": [stage]}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if args[:5] == ["uv", "run", "--locked", "dvc", "repro"]:
            output = target / "data/processed/analysis/result.txt"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("result\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")
        return cast(
            subprocess.CompletedProcess[str],
            original_run(args, cwd=cwd, input_text=input_text, env=env),
        )

    monkeypatch.setattr(project_tool, "_run", fake_run)

    result = project_tool.run_reproduction(target)

    stage_run = ["uv", "run", "--locked", "dvc", "repro", "--single-item", "--force", "analysis"]
    assert stage_run in calls
    assert result["provenance"]["dependency_check"]["stages"]["analysis"]["untraced"]
    assert not any("--pull" in args for args in calls)
    assert result["provenance"]["engine"] == "dvc"
    cache_config = (target / ".dvc/config.local").read_text(encoding="utf-8")
    assert "[cache]" in cache_config
    assert "dir = " in cache_config

    config.write_text(
        config.read_text(encoding="utf-8") + 'targets = ["--pull"]\n', encoding="utf-8"
    )
    audit_result = project_tool.audit(target)
    assert any(item["check"] == "dvc-targets" for item in audit_result["findings"])
    with pytest.raises(RuntimeError, match="valid command"):
        project_tool.run_reproduction(target)


def test_dvc_pipeline_cannot_declare_raw_outputs(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\nengine = "dvc"\ncomponent = "analysis"\n',
        encoding="utf-8",
    )
    (target / ".dvc").mkdir()
    (target / ".dvc/config").write_text("[core]\nanalytics = false\n", encoding="utf-8")
    (target / "dvc.yaml").write_text(
        "stages:\n  acquire:\n    cmd: python scripts/acquire.py\n"
        "    outs:\n      - data/raw/acquired.txt\n",
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "dvc-output-path" for item in result["findings"])


def _read_only_raw(raw: Path) -> None:
    for path in sorted([raw, *raw.rglob("*")], key=lambda item: len(item.parts), reverse=True):
        path.chmod(path.stat().st_mode & ~0o222)


def _declare_acquisition(target: Path, script: str) -> None:
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[acquisition]\ncommand = ["python", "scripts/acquire.py"]\n',
        encoding="utf-8",
    )
    (target / "scripts/acquire.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "stage = Path(os.environ['ASTACK_ACQUISITION_DIR'])\n" + script,
        encoding="utf-8",
    )


def test_acquisition_adds_raw_inputs_without_rewriting_existing_snapshots(tmp_path: Path) -> None:
    target = _project(tmp_path)
    existing = target / "data/raw/existing.txt"
    existing.write_text("original\n", encoding="utf-8")
    _declare_acquisition(target, "(stage / 'new.txt').write_text('new\\n')\n")
    _git_baseline(target)
    _read_only_raw(target / "data/raw")

    result = project_tool.run_acquisition(target)

    assert result["added_raw_inputs"] == ["data/raw/new.txt"]
    assert existing.read_text(encoding="utf-8") == "original\n"


def test_acquisition_cannot_change_existing_raw_inputs(tmp_path: Path) -> None:
    target = _project(tmp_path)
    existing = target / "data/raw/existing.txt"
    existing.write_text("original\n", encoding="utf-8")
    _declare_acquisition(target, "Path('data/raw/existing.txt').write_text('changed\\n')\n")
    _git_baseline(target)
    _read_only_raw(target / "data/raw")

    with pytest.raises(RuntimeError, match="Permission"):
        project_tool.run_acquisition(target)

    assert existing.read_text(encoding="utf-8") == "original\n"


def test_acquisition_preserves_a_shared_raw_symlink(tmp_path: Path) -> None:
    target = _project(tmp_path)
    shared = tmp_path / "shared-raw"
    shared.mkdir()
    existing = shared / "existing.txt"
    existing.write_text("original\n", encoding="utf-8")
    raw = target / "data/raw"
    raw.rmdir()
    raw.symlink_to(shared, target_is_directory=True)
    _declare_acquisition(target, "Path('data/raw/existing.txt').write_text('changed\\n')\n")
    _git_baseline(target)
    _read_only_raw(shared)

    try:
        with pytest.raises(RuntimeError, match="Permission"):
            project_tool.run_acquisition(target)

        assert raw.is_symlink()
        assert raw.resolve() == shared.resolve()
        assert existing.read_text(encoding="utf-8") == "original\n"
    finally:
        shared.chmod(0o755)


def test_acquisition_rejection_restores_prohibited_worktree_changes(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[acquisition]\ncommand = ["python", "scripts/acquire.py"]\n',
        encoding="utf-8",
    )
    original_readme = (target / "README.md").read_text(encoding="utf-8")
    (target / "scripts/acquire.py").write_text(
        "from pathlib import Path\n"
        "Path('README.md').write_text('changed\\n')\n"
        "Path('unexpected.txt').write_text('unexpected\\n')\n",
        encoding="utf-8",
    )
    _git_baseline(target)
    _read_only_raw(target / "data/raw")

    with pytest.raises(RuntimeError, match="prohibited worktree changes"):
        project_tool.run_acquisition(target)

    assert (target / "README.md").read_text(encoding="utf-8") == original_readme
    assert not (target / "unexpected.txt").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not status.stdout.strip()


def test_reproduction_refuses_uncommitted_code_state(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\ncommand = ["python", "-c", "print(1)"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    _git_baseline(target)
    (target / "uncommitted.py").write_text("value = 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="clean Git worktree"):
        project_tool.run_reproduction(target)


def test_reproduction_rejects_ignored_raw_mutation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    raw = target / "data/raw/source.txt"
    raw.write_text("original\n", encoding="utf-8")
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\ncommand = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\nPath('data/raw/source.txt').write_text('changed\\n')\n",
        encoding="utf-8",
    )
    _git_baseline(target)
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--", "data/raw/source.txt"],
            cwd=target,
            check=False,
        ).returncode
        == 0
    )

    with pytest.raises(RuntimeError, match="raw input"):
        project_tool.run_reproduction(target)
    assert not list((target / "evidence/analysis/runs").glob("*.json"))


@pytest.mark.parametrize(
    "relative",
    ["data/interim/result.parquet", "data/processed/result.parquet"],
)
def test_reproduction_rejects_ignored_unscoped_derived_output(
    tmp_path: Path, relative: str
) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\ncommand = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        f"output = Path({relative!r})\n"
        "output.parent.mkdir(parents=True, exist_ok=True)\n"
        "output.write_bytes(b'result')\n",
        encoding="utf-8",
    )
    (target / "tests/test_pipeline.py").write_text(
        "def test_pipeline_contract():\n    assert True\n", encoding="utf-8"
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="prohibited worktree changes"):
        project_tool.run_reproduction(target)
    assert not list((target / "evidence/analysis/runs").glob("*.json"))


@pytest.mark.parametrize(
    "relative",
    [
        "reports/figures/analysis/diagnostic.npy",
        "reports/tables/analysis/results.parquet",
    ],
)
def test_reproduction_rejects_machine_outputs_in_component_reports(
    tmp_path: Path, relative: str
) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\ncommand = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        f"output = Path({relative!r})\n"
        "output.parent.mkdir(parents=True, exist_ok=True)\n"
        "output.write_bytes(b'machine output')\n",
        encoding="utf-8",
    )
    (target / "tests/test_pipeline.py").write_text(
        "def test_pipeline_contract():\n    assert True\n", encoding="utf-8"
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="prohibited worktree changes"):
        project_tool.run_reproduction(target)
    assert not list((target / "evidence/analysis/runs").glob("*.json"))


def test_reproduction_allows_derived_output_mutation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\ncommand = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["reports/figures/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        "Path('reports/figures/analysis').mkdir(parents=True, exist_ok=True)\n"
        "Path('reports/figures/analysis/result.txt').write_text('result\\n')\n",
        encoding="utf-8",
    )
    _git_baseline(target)

    result = project_tool.run_reproduction(target)

    result_path = target / "reports/figures/analysis/result.txt"
    assert result_path.read_text(encoding="utf-8") == "result\n"
    assert Path(result["record"]).exists()
