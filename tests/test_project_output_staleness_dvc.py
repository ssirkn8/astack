"""Output labels from the real DVC pipeline probe. Skipped where DVC is not installed."""

from __future__ import annotations

import os
import shutil
from typing import Any, cast

import pytest
from project_tool_test_support import (
    Path,
    _git_baseline,
    _project,
    _set_capabilities,
    project_tool,
    subprocess,
)

pytestmark = pytest.mark.integration


def _dvc_python() -> str | None:
    """The interpreter of the `dvc` command on PATH, which can import DVC."""
    command = shutil.which("dvc")
    if command is None:
        return None
    try:
        first = Path(command).read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    interpreter = first.removeprefix("#!").strip().split()[0] if first.startswith("#!") else ""
    probe = subprocess.run([interpreter, "-c", "import dvc.repo"], capture_output=True, check=False)
    return interpreter if interpreter and probe.returncode == 0 else None


PYTHON = _dvc_python()
skip_without_dvc = pytest.mark.skipif(PYTHON is None, reason="DVC is not installed")


def _pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dvc_yaml: str) -> Path:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    # No network: DVC analytics and update checks stay off in tests.
    monkeypatch.setenv("DVC_NO_ANALYTICS", "1")
    monkeypatch.setenv("DVC_NO_UPDATER", "1")
    subprocess.run(["dvc", "init", "-q"], cwd=target, check=True)
    subprocess.run(["dvc", "config", "core.analytics", "false"], cwd=target, check=True)
    (target / "dvc.yaml").write_text(dvc_yaml, encoding="utf-8")
    original = project_tool._run

    def run(
        args: list[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if args[:4] == ["uv", "run", "--locked", "python"]:
            args = [cast(str, PYTHON), *args[4:]]
        elif args[:4] == ["uv", "run", "--locked", "dvc"]:
            args = ["dvc", *args[4:]]
        return cast(
            subprocess.CompletedProcess[str],
            original(args, cwd=cwd, input_text=input_text, env=env, timeout=timeout),
        )

    monkeypatch.setattr(project_tool, "_run", run)
    return target


def _repro(target: Path, *stages: str) -> None:
    for stage in stages or ("",):
        command = ["dvc", "repro", "-f", "-q", *(["-s", stage] if stage else [])]
        subprocess.run(command, cwd=target, check=True, capture_output=True)
    _git_baseline(target)


def _labels(target: Path) -> dict[str, str]:
    result: dict[str, Any] = project_tool.output_status(target)
    assert result["status"] == "available", result
    return {entry["path"]: entry["label"] for entry in result["outputs"]}


def _final_report_passes(target: Path, body: str) -> bool:
    (target / "reports/study.md").write_text(
        f"---\ntitle: S\nstatus: final\n---\n\n{body}\n", encoding="utf-8"
    )
    return bool(project_tool.report_check(target, [])["status"] == "passed")


@skip_without_dvc
def test_dvc_review_keeps_healthy_units_when_status_cannot_hash_a_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  bad:\n"
        "    cmd: python scripts/bad.py\n"
        "    deps: [scripts/bad.py]\n"
        "  healthy:\n"
        "    cmd: python scripts/healthy.py\n"
        "    deps: [scripts/healthy.py]\n",
    )
    bad = target / "scripts/bad.py"
    bad.write_text("print('original')\n", encoding="utf-8")
    (target / "scripts/healthy.py").write_text("print('healthy')\n", encoding="utf-8")
    _repro(target)
    bad.write_text("print('changed and unreadable')\n", encoding="utf-8")
    bad.chmod(0)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("the test requires permission checks for the current user")
        assert project_tool.output_status(target)["status"] == "unknown"
        status = project_tool.review_status(target)
        rows = {row["unit"]: row for row in status["units"]}
        assert rows["bad"]["status"] == "unknown"
        assert rows["bad"]["checksum"] is None
        assert rows["healthy"]["status"] == "unreviewed"
        assert status["problem"] is None
    finally:
        bad.chmod(0o644)


@skip_without_dvc
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
@pytest.mark.parametrize("parameter_path", ["settings.yaml", "params.yaml"])
def test_dvc_review_rejects_a_parameter_pipe_without_losing_other_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parameter_path: str
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  bad:\n"
        "    cmd: echo bad\n"
        f"    params: [{{{parameter_path}: [rate]}}]\n"
        "  healthy:\n"
        "    cmd: echo healthy\n",
    )
    _git_baseline(target)
    os.mkfifo(target / parameter_path)
    run_probe = project_tool._run

    def bounded_run(args: list[str], **kwargs: Any) -> Any:
        if project_tool.DVC_PIPELINE_PROBE in args:
            kwargs["timeout"] = 10
        return run_probe(args, **kwargs)

    monkeypatch.setattr(project_tool, "_run", bounded_run)
    labels = project_tool.output_status(target)
    assert labels["status"] == "unknown"
    assert "not a regular file" in labels["problem"]
    status = project_tool.review_status(target)
    rows = {row["unit"]: row for row in status["units"]}
    if parameter_path == "params.yaml":
        # DVC uses this file to resolve the whole pipeline before listing any stages.
        assert not rows
        assert "not a regular file" in status["problem"]
    else:
        assert rows["bad"]["status"] == "unknown"
        assert parameter_path in rows["bad"]["why"]
        assert rows["healthy"]["status"] == "unreviewed"


@skip_without_dvc
def test_dvc_probe_allows_parameters_generated_by_an_upstream_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: >-\n"
        "      printf 'rate: 1\\n' > settings.yaml\n"
        "    outs: [settings.yaml]\n"
        "  use:\n"
        "    cmd: cat settings.yaml\n"
        "    params: [{settings.yaml: [rate]}]\n",
    )
    assert not (target / "settings.yaml").exists()
    pipeline, problem = project_tool._dvc_pipeline(target, ["use"])
    assert not problem
    assert set(pipeline["selected"]) == {"prepare", "use"}
    consumer = next(stage for stage in pipeline["stages"] if stage["name"] == "use")
    assert consumer["deps"][0]["params_missing"] == ["rate"]
    _repro(target)
    assert (target / "settings.yaml").read_text(encoding="utf-8") == "rate: 1\n"


@skip_without_dvc
def test_staleness_reaches_a_stage_through_a_tracked_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: >-\n"
        "      mkdir -p evidence/nested && cp input.txt evidence/target.json &&\n"
        "      ln -sf ../target.json evidence/nested/link.json\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - evidence/nested:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: cp evidence/target.json evidence/result.json\n"
        "    deps: [evidence/target.json]\n"
        "    outs:\n"
        "      - evidence/result.json:\n"
        "          cache: false\n",
    )
    (target / "input.txt").write_text('{"v": 1}', encoding="utf-8")
    _repro(target, "prepare", "analyze")
    assert _labels(target)["evidence/result.json"] == "current"
    assert _final_report_passes(target, "V [1](../evidence/result.json#/v)")

    (target / "input.txt").write_text('{"v": 2}', encoding="utf-8")
    assert _labels(target)["evidence/result.json"] == "outdated"
    assert not _final_report_passes(target, "V [1](../evidence/result.json#/v)")


@skip_without_dvc
def test_real_pipeline_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: mkdir -p evidence && cp input.txt evidence/mid.txt\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - evidence/mid.txt:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: cp evidence/mid.txt evidence/out.txt\n"
        "    deps: [evidence/mid.txt]\n"
        "    outs:\n"
        "      - evidence/out.txt:\n"
        "          cache: false\n"
        "  each:\n"
        "    foreach: [a, b]\n"
        "    do:\n"
        "      cmd: mkdir -p reports/tables && echo ${item} > reports/tables/${item}.txt\n"
        "      outs:\n"
        "        - reports/tables/${item}.txt:\n"
        "            cache: false\n"
        "  inner:\n"
        "    wdir: sub\n"
        "    cmd: mkdir -p out && echo x > out/x.txt\n"
        "    outs:\n"
        "      - out/x.txt:\n"
        "          cache: false\n",
    )
    (target / "sub").mkdir()
    (target / "input.txt").write_text("1", encoding="utf-8")
    _repro(target)
    labels = _labels(target)
    assert labels["reports/tables/a.txt"] == labels["reports/tables/b.txt"] == "current"
    assert labels["sub/out/x.txt"] == "current"
    assert labels["evidence/out.txt"] == "current"

    (target / "input.txt").write_text("2", encoding="utf-8")
    labels = _labels(target)
    assert labels["evidence/mid.txt"] == labels["evidence/out.txt"] == "outdated"

    subprocess.run(["dvc", "freeze", "prepare"], cwd=target, check=True, capture_output=True)
    labels = _labels(target)
    assert labels["evidence/mid.txt"] == labels["evidence/out.txt"] == "unknown"


@skip_without_dvc
@pytest.mark.parametrize("change", ["delete", "retarget"])
def test_losing_the_tracked_symlink_never_leaves_the_result_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: >-\n"
        "      mkdir -p evidence/nested && cp input.txt evidence/target.json &&\n"
        "      ln -sf ../target.json evidence/nested/link.json\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - evidence/nested:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: cp evidence/target.json evidence/result.json\n"
        "    deps: [evidence/target.json]\n"
        "    outs:\n"
        "      - evidence/result.json:\n"
        "          cache: false\n",
    )
    (target / "input.txt").write_text('{"v": 1}', encoding="utf-8")
    _repro(target, "prepare", "analyze")
    link = target / "evidence/nested/link.json"
    link.unlink()
    if change == "retarget":
        (target / "evidence/other.json").write_text("{}", encoding="utf-8")
        link.symlink_to("../other.json")
    (target / "input.txt").write_text('{"v": 2}', encoding="utf-8")
    assert _labels(target)["evidence/result.json"] in {"outdated", "unknown"}
    assert not _final_report_passes(target, "V [1](../evidence/result.json#/v)")


@skip_without_dvc
def test_one_declared_file_does_not_cover_a_whole_folder_dep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: >-\n"
        "      mkdir -p evidence/inputs artifacts &&\n"
        "      cp input.txt evidence/inputs/side.json && echo ok > artifacts/stamp.txt\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - artifacts/stamp.txt:\n"
        "          cache: false\n"
        "  seed:\n"
        "    cmd: mkdir -p evidence/inputs && echo k > evidence/inputs/known.txt\n"
        "    outs:\n"
        "      - evidence/inputs/known.txt:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: cp evidence/inputs/side.json evidence/result.json\n"
        "    deps: [evidence/inputs]\n"
        "    outs:\n"
        "      - evidence/result.json:\n"
        "          cache: false\n",
    )
    (target / "input.txt").write_text('{"v": 1}', encoding="utf-8")
    _repro(target, "prepare", "seed", "analyze")
    assert _labels(target)["evidence/result.json"] == "unknown"
    (target / "input.txt").write_text('{"v": 2}', encoding="utf-8")
    assert _labels(target)["evidence/result.json"] == "unknown"
    assert not _final_report_passes(target, "V [1](../evidence/result.json#/v)")


@skip_without_dvc
def test_an_ignored_file_inside_a_declared_folder_is_not_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  seed:\n"
        "    cmd: mkdir -p evidence/inputs && echo k > evidence/inputs/known.txt\n"
        "    outs:\n"
        "      - evidence/inputs:\n"
        "          cache: false\n"
        "  prepare:\n"
        "    cmd: >-\n"
        "      mkdir -p artifacts && cp input.txt evidence/inputs/side.json &&\n"
        "      echo ok > artifacts/stamp.txt\n"
        "    deps: [input.txt, evidence/inputs/known.txt]\n"
        "    outs:\n"
        "      - artifacts/stamp.txt:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: cp evidence/inputs/side.json evidence/result.json\n"
        "    deps: [evidence/inputs]\n"
        "    outs:\n"
        "      - evidence/result.json:\n"
        "          cache: false\n",
    )
    (target / ".dvcignore").write_text("evidence/inputs/side.json\n", encoding="utf-8")
    (target / "input.txt").write_text('{"v": 1}', encoding="utf-8")
    _repro(target, "seed", "prepare", "analyze")
    assert _labels(target)["evidence/result.json"] == "unknown"
    assert not _final_report_passes(target, "V [1](../evidence/result.json#/v)")


@skip_without_dvc
def test_a_symlink_dep_to_a_declared_output_is_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: mkdir -p evidence && cp input.txt evidence/real.json\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - evidence/real.json:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: cp evidence/link.json evidence/result.json\n"
        "    deps: [evidence/link.json]\n"
        "    outs:\n"
        "      - evidence/result.json:\n"
        "          cache: false\n",
    )
    (target / "input.txt").write_text('{"v": 1}', encoding="utf-8")
    (target / "evidence").mkdir(exist_ok=True)
    (target / "evidence/link.json").symlink_to("real.json")
    _repro(target, "prepare", "analyze")
    assert _labels(target)["evidence/result.json"] == "current"
    assert _final_report_passes(target, "V [1](../evidence/result.json#/v)")
    (target / "input.txt").write_text('{"v": 2}', encoding="utf-8")
    assert _labels(target)["evidence/result.json"] == "outdated"


@skip_without_dvc
def test_a_checked_run_finds_undeclared_reads_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  prepare:\n"
        "    cmd: python scripts/prepare.py\n"
        "    deps: [scripts/prepare.py, input.txt]\n"
        "    outs:\n"
        "      - evidence/a/prepared.json:\n"
        "          cache: false\n"
        "  analyze:\n"
        "    cmd: python scripts/analyze.py\n"
        "    deps: [scripts/analyze.py, evidence/a/prepared.json]\n"
        "    outs:\n"
        "      - evidence/a/result.json:\n"
        "          cache: false\n"
        "  shell:\n"
        "    cmd: mkdir -p evidence/b && cp input.txt evidence/b/copy.txt\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - evidence/b/copy.txt:\n"
        "          cache: false\n",
    )
    (target / "input.txt").write_text("1", encoding="utf-8")
    (target / "config.yml").write_text("k: 1", encoding="utf-8")
    (target / "scripts/prepare.py").write_text(
        "from pathlib import Path\n"
        "Path('evidence/a').mkdir(parents=True, exist_ok=True)\n"
        "value = Path('input.txt').read_text()\n"
        "Path('evidence/a/prepared.json').write_text(value)\n"
        "Path('evidence/a/side.txt').write_text(value)\n",
        encoding="utf-8",
    )
    (target / "scripts/analyze.py").write_text(
        "from pathlib import Path\n"
        "value = Path('evidence/a/prepared.json').read_text() + Path('config.yml').read_text()\n"
        "Path('evidence/a/result.json').write_text(value)\n",
        encoding="utf-8",
    )
    result = project_tool.run_checked_dvc_stages(target, force=True, targets=[], environment={})
    stages = result["stages"]
    assert stages["prepare"] == {
        "undeclared_writes": ["evidence/a/side.txt"],
        "undeclared_reads": [],
        "untraced": None,
        "inputs_locked": True,
        "lock_gaps": [],
    }
    assert stages["analyze"]["undeclared_reads"] == ["config.yml"]
    assert stages["analyze"]["untraced"] is None
    assert "not a single Python process" in stages["shell"]["untraced"]
    _git_baseline(target)
    labels = _labels(target)
    assert labels["evidence/a/prepared.json"] == "unknown"
    assert labels["evidence/a/result.json"] == "unknown"
    assert labels["evidence/b/copy.txt"] == "current"
    shell = next(
        item
        for item in project_tool.output_status(target)["outputs"]
        if item["path"] == "evidence/b/copy.txt"
    )
    assert shell["dependency_check"] == "untraced"


@skip_without_dvc
def test_any_program_reading_an_undeclared_derived_file_fails_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    target = _pipeline(
        tmp_path,
        monkeypatch,
        "stages:\n"
        "  copy:\n"
        "    cmd: cat evidence/a/grid.txt > evidence/a/copy.txt\n"
        "    deps: [input.txt]\n"
        "    outs:\n"
        "      - evidence/a/copy.txt:\n"
        "          cache: false\n",
    )
    (target / "input.txt").write_text("1", encoding="utf-8")
    (target / "evidence/a").mkdir(parents=True)
    (target / "evidence/a/grid.txt").write_text("1", encoding="utf-8")
    with pytest.raises(RuntimeError, match="copy tried to read evidence/a/grid.txt"):
        project_tool.run_checked_dvc_stages(target, force=True, targets=[], environment={})
    assert os.access(target / "evidence/a/grid.txt", os.R_OK)
    assert not (target / ".cache/input-lock.json").exists()
    dvc_yaml = target / "dvc.yaml"
    dvc_yaml.write_text(
        dvc_yaml.read_text(encoding="utf-8").replace(
            "deps: [input.txt]", "deps: [evidence/a/grid.txt]"
        ),
        encoding="utf-8",
    )
    result = project_tool.run_checked_dvc_stages(target, force=True, targets=[], environment={})
    assert result["stages"]["copy"]["inputs_locked"] is True
    assert (target / "evidence/a/copy.txt").read_text(encoding="utf-8") == "1"
