"""Run records must bind actual input bytes, the seed delivered, and the real environment."""

from __future__ import annotations

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    hashlib,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
)

PIPELINE = (
    "import os\n"
    "from pathlib import Path\n"
    "out = Path('data/processed/analysis')\n"
    "out.mkdir(parents=True, exist_ok=True)\n"
    "(out / 'result.txt').write_text(os.environ.get('ASTACK_RANDOM_SEED', 'unset') + '\\n')\n"
)


def _material(target: Path, extra: str = "") -> None:
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n' + extra,
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(PIPELINE, encoding="utf-8")


def test_record_binds_raw_input_bytes_not_only_the_sources_manifest(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    raw = target / "data/raw/tile.bin"
    raw.write_bytes(b"tile")
    _git_baseline(target)

    record = project_tool.run_reproduction(target)["provenance"]

    assert "input_fingerprint" not in record
    assert record["sources_manifest_fingerprint"] == project_tool._sha256(
        target / "data/sources.yml"
    )
    expected = hashlib.sha256(
        b"data/raw/tile.bin\0" + hashlib.sha256(b"tile").hexdigest().encode() + b"\0"
    ).hexdigest()
    assert record["inputs"] == {"files": 1, "bytes": 4, "sha256": expected}

    path = Path(project_tool.run_reproduction(target)["record"])
    assert project_tool._valid_run_record(target, path, project_tool._project_config(target))
    raw.write_bytes(b"TILE")
    assert not project_tool._valid_run_record(target, path, project_tool._project_config(target))


def test_configured_seed_is_delivered_to_the_command_and_recorded(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target, "random_seed = 42\n")
    _git_baseline(target)

    record = project_tool.run_reproduction(target)["provenance"]

    output = target / "data/processed/analysis/result.txt"
    assert output.read_text(encoding="utf-8") == "42\n"
    assert record["rng"]["seed"] == 42
    assert record["rng"]["delivered_as"] == "environment:ASTACK_RANDOM_SEED"
    assert "not verified" in record["rng"]["consumption"]


def test_unconfigured_seed_is_recorded_as_absent(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)

    record = project_tool.run_reproduction(target)["provenance"]

    assert record["rng"]["seed"] is None
    assert (target / "data/processed/analysis/result.txt").read_text() == "unset\n"


@pytest.mark.parametrize("seed", ['"42"', "4.2", "true"])
def test_non_integer_seed_is_rejected(tmp_path: Path, seed: str) -> None:
    target = _project(tmp_path)
    _material(target, f"random_seed = {seed}\n")
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="random_seed"):
        project_tool.run_reproduction(target)
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "random-seed" in checks


def test_record_captures_the_actual_environment(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)

    record = project_tool.run_reproduction(target)["provenance"]
    environment = record["environment"]

    assert environment["status"] == "captured"
    assert environment["platform"]["machine"]
    assert environment["python"]["version"]
    assert any(name.casefold() == "pyyaml" for name in environment["distributions"])
    assert isinstance(environment["native"], dict)
    assert len(record["environment_fingerprint"]) == 64


def test_environment_capture_failure_is_unavailable_not_invented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_tool, "ENVIRONMENT_PROBE", "raise SystemExit(4)")

    environment = project_tool._environment_manifest(tmp_path)

    assert environment["status"] == "unavailable"
    assert "distributions" not in environment


@pytest.mark.parametrize(
    ("command", "launcher"),
    [
        (["python", "scripts/pipeline.py"], ["python"]),
        (["uv", "run", "--locked", "python", "x.py"], ["uv", "run", "--locked", "python"]),
        (["uv", "run", "--locked", "dvc", "repro"], ["uv", "run", "--locked", "python"]),
        (["Rscript", "analysis.R"], None),
        (["python", "-S", "scripts/pipeline.py"], None),
        (["python", "-m", "pkg.pipeline"], ["python"]),
        (["uv", "run", "--python", "3.12", "python", "x.py"], None),
        (["uv", "run", "--locked", "python", "-I", "x.py"], None),
    ],
)
def test_environment_is_probed_through_the_command_launcher(
    command: list[str], launcher: list[str] | None
) -> None:
    assert project_tool._probe_launcher(command) == launcher


def test_non_python_command_environment_is_unavailable(tmp_path: Path) -> None:
    environment = project_tool._environment_manifest(tmp_path, ["Rscript", "analysis.R"])

    assert environment["status"] == "unavailable"


def test_unavailable_environments_never_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_tool, "ENVIRONMENT_PROBE", "raise SystemExit(4)")
    target = _project(tmp_path)
    _material(target)
    _git_baseline(target)
    project_tool.run_reproduction(target, promote=True)
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e.com", "commit", "-qm", "ref"],
        cwd=target,
        check=True,
        capture_output=True,
    )

    verification = project_tool.run_reproduction(target)["provenance"]["verification"]

    assert verification["environment_matches_reference"] == "unknown"


def test_nested_raw_file_symlink_is_identified_by_its_content(tmp_path: Path) -> None:
    target = _project(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"one")
    (target / "data/raw/linked.bin").symlink_to(outside)

    before = project_tool._raw_inputs_identity(target)
    outside.write_bytes(b"two")

    assert project_tool._raw_inputs_identity(target)["sha256"] != before["sha256"]


def test_nested_raw_directory_symlink_is_refused(tmp_path: Path) -> None:
    target = _project(tmp_path)
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (target / "data/raw/linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink to a directory"):
        project_tool._raw_inputs_identity(target)
