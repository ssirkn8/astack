from __future__ import annotations

import os
from typing import Any

import pytest
from project_tool_test_support import (
    Path,
    _git_baseline,
    _project,
    hashlib,
    json,
    project_tool,
    yaml,
)

pytestmark = pytest.mark.integration

ROOT_ENV = "ASTACK_EXTERNAL_ROOT_ARCHIVE"
IN_PLACE_CHECKS = {
    "source-storage",
    "source-in-place-reason",
    "source-path",
    "source-missing",
    "source-checksum",
    "source-should-snapshot",
    "data-snapshot-limit",
    "raw-checksum",
}


def _external(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int = 2048) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "survey.bin").write_bytes(b"x" * size)
    monkeypatch.setenv(ROOT_ENV, str(archive))
    return archive


def _write_source(target: Path, **overrides: Any) -> None:
    record: dict[str, Any] = {
        "title": "Survey",
        "path": "root:archive/survey.bin",
        "storage": "in_place",
        "in_place_reason": "size",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-11-14",
        "version": "2026-11-14",
        "license": "Restricted",
        "raw_checksum": "sha256:" + hashlib.sha256(b"x" * 2048).hexdigest(),
        "sensitivity": "internal",
        "allowed_outputs": ["internal-analysis"],
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
    }
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"survey": record}}), encoding="utf-8"
    )


def _set_limit(target: Path, value: str) -> None:
    config = target / ".astack/project.toml"
    text = config.read_text(encoding="utf-8")
    assert "snapshot_limit_gb = 5\n" in text
    config.write_text(
        text.replace("snapshot_limit_gb = 5\n", f"snapshot_limit_gb = {value}\n", 1),
        encoding="utf-8",
    )


def _checks(target: Path) -> dict[str, str]:
    return {
        item["check"]: item["priority"]
        for item in project_tool.audit(target)["findings"]
        if item["check"] in IN_PLACE_CHECKS
    }


def test_in_place_source_within_the_limit_must_be_snapshotted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _write_source(target)
    assert _checks(target) == {"source-should-snapshot": "P1"}


def test_in_place_source_above_the_limit_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _set_limit(target, "0.000001")
    _write_source(target)
    assert _checks(target) == {}


def test_license_reason_stays_in_place_at_any_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _write_source(target, in_place_reason="license")
    assert _checks(target) == {}


@pytest.mark.parametrize("reason", [None, "convenience"])
def test_in_place_source_requires_a_known_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str | None
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _set_limit(target, "0.000001")
    _write_source(target, in_place_reason=reason)
    assert _checks(target) == {"source-in-place-reason": "P1"}


def test_root_path_requires_in_place_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _write_source(target, storage=None)
    assert _checks(target) == {"source-storage": "P1"}


def test_unknown_storage_is_a_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _write_source(target, storage="mounted")
    assert _checks(target) == {"source-storage": "P1"}


def test_in_place_checksum_mismatch_is_p0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    _set_limit(target, "0.000001")
    _write_source(target)
    (archive / "survey.bin").chmod(0o644)
    (archive / "survey.bin").write_bytes(b"y" * 2048)
    assert _checks(target) == {"raw-checksum": "P0"}


def test_in_place_source_requires_a_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _set_limit(target, "0.000001")
    _write_source(target, raw_checksum=None)
    assert _checks(target) == {"source-checksum": "P1"}


def test_unavailable_in_place_source_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _write_source(target, path="root:archive/elsewhere.bin")
    assert _checks(target) == {"source-missing": "P1"}


@pytest.mark.parametrize(
    "path", ["root:archive/../survey.bin", "root:archive", "root:/survey.bin", "archive/x"]
)
def test_malformed_in_place_paths_are_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _write_source(target, path=path)
    assert _checks(target) == {"source-path": "P1"}


def test_unconfigured_external_root_is_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    monkeypatch.delenv(ROOT_ENV, raising=False)
    _write_source(target)
    assert _checks(target) == {"source-path": "P1"}


def test_local_toml_configures_an_external_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    monkeypatch.delenv(ROOT_ENV)
    (target / ".astack/local.toml").write_text(
        f'[data.external_roots]\narchive = "{archive}"\n', encoding="utf-8"
    )
    _set_limit(target, "0.000001")
    _write_source(target)
    assert _checks(target) == {}


def test_external_root_inside_the_project_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    inside = target / "data/manual"
    inside.mkdir(parents=True, exist_ok=True)
    (inside / "survey.bin").write_bytes(b"x" * 2048)
    monkeypatch.setenv(ROOT_ENV, str(inside))
    _write_source(target)
    assert _checks(target) == {"source-path": "P1"}


@pytest.mark.parametrize("value", ["0", "-1", '"five"', "true"])
def test_invalid_snapshot_limit_is_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    target = _project(tmp_path)
    _external(tmp_path, monkeypatch)
    _set_limit(target, value)
    _write_source(target, in_place_reason="license")
    assert _checks(target) == {"data-snapshot-limit": "P1"}


def _reproducible(target: Path) -> None:
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"source = Path(os.environ['{ROOT_ENV}']) / 'survey.bin'\n"
        "Path('data/processed/analysis').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/processed/analysis/result.txt').write_text(str(len(source.read_bytes())))\n",
        encoding="utf-8",
    )


def test_reproduction_records_and_exposes_in_place_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    _set_limit(target, "0.000001")
    _write_source(target)
    _reproducible(target)
    _git_baseline(target)
    # The run must get the root from astack, not from the caller's environment.
    monkeypatch.delenv(ROOT_ENV)
    (target / ".astack/local.toml").write_text(
        f'[data.external_roots]\narchive = "{archive}"\n', encoding="utf-8"
    )

    result = project_tool.run_reproduction(target)
    record = json.loads(Path(result["record"]).read_text(encoding="utf-8"))

    assert record["in_place_inputs"] == {
        "survey": "file:" + hashlib.sha256(b"x" * 2048).hexdigest()
    }
    assert (target / "data/processed/analysis/result.txt").read_text() == "2048"


def test_reproduction_refuses_a_changed_in_place_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    _set_limit(target, "0.000001")
    _write_source(target)
    _reproducible(target)
    _git_baseline(target)
    (archive / "survey.bin").write_bytes(b"y" * 2048)

    with pytest.raises(RuntimeError, match="does not match its checksum"):
        project_tool.run_reproduction(target)


def test_snapshot_only_records_have_no_in_place_field(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _reproducible(target)
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        "Path('data/processed/analysis').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/processed/analysis/result.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    _git_baseline(target)
    record = json.loads(
        Path(project_tool.run_reproduction(target)["record"]).read_text(encoding="utf-8")
    )
    assert "in_place_inputs" not in record


def test_special_file_never_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    os.mkfifo(archive / "pipe")
    _write_source(target, path="root:archive/pipe", in_place_reason="license")
    assert _checks(target) == {"raw-checksum": "P0"}
    with pytest.raises(RuntimeError, match="does not match its checksum"):
        project_tool.in_place_inputs_identity(target)


def test_in_place_directory_requires_tree_v2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    tree = archive / "tree"
    tree.mkdir()
    (tree / "a.bin").write_bytes(b"a")
    legacy = project_tool._cached_tree_sha256(tree)
    _write_source(
        target,
        path="root:archive/tree",
        in_place_reason="license",
        raw_checksum=f"sha256-tree-v1:{legacy}",
    )
    assert _checks(target) == {"source-checksum": "P1"}
    with pytest.raises(RuntimeError, match="sha256-tree-v2"):
        project_tool.in_place_inputs_identity(target)
    current = project_tool.source_checksum(tree)["checksum"]
    _write_source(target, path="root:archive/tree", in_place_reason="license", raw_checksum=current)
    assert _checks(target) == {}


def _local_source(target: Path, relative: str) -> None:
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 2048)
    _write_source(target, path=relative, storage="snapshot", in_place_reason=None)


def test_snapshot_source_in_an_ignored_folder_is_a_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _local_source(target, "data/interim/survey.bin")
    _git_baseline(target)
    assert _checks(target) == {"source-storage": "P1"}


def test_committed_snapshot_source_outside_raw_is_allowed(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _local_source(target, "data/manual/survey.bin")
    _git_baseline(target)
    assert _checks(target) == {}


def test_committed_symlink_to_an_ignored_file_is_a_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _local_source(target, "data/interim/survey.bin")
    link = target / "data/manual/survey.bin"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target / "data/interim/survey.bin")
    _write_source(target, path="data/manual/survey.bin", storage="snapshot", in_place_reason=None)
    _git_baseline(target)
    assert _checks(target) == {"source-storage": "P1"}


def test_symlink_inside_a_committed_source_directory_is_a_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _local_source(target, "data/interim/survey.bin")
    tree = target / "data/manual/survey"
    tree.mkdir(parents=True)
    (tree / "survey.bin").symlink_to(target / "data/interim/survey.bin")
    checksum = project_tool.source_checksum(tree)["checksum"]
    _write_source(
        target,
        path="data/manual/survey",
        storage="snapshot",
        in_place_reason=None,
        raw_checksum=checksum,
    )
    _git_baseline(target)
    assert _checks(target) == {"source-storage": "P1"}


@pytest.mark.parametrize("name", ["My_Root", "my-root"])
def test_root_names_must_map_to_one_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    target = _project(tmp_path)
    archive = _external(tmp_path, monkeypatch)
    (target / ".astack/local.toml").write_text(
        f'[data.external_roots]\n"{name}" = "{archive}"\n', encoding="utf-8"
    )
    _write_source(target, path=f"root:{name}/survey.bin", in_place_reason="license")
    assert _checks(target) == {"source-path": "P1"}
    assert project_tool.in_place_root_environment(target) == {}


@pytest.mark.parametrize("relative", ["data/vendor/survey.bin", "data/vendor"])
def test_snapshot_source_inside_a_nested_repository_is_a_finding(
    tmp_path: Path, relative: str
) -> None:
    target = _project(tmp_path)
    vendor = target / "data/vendor"
    vendor.mkdir(parents=True)
    (vendor / ".git").write_text("gitdir: ../../.git/modules/vendor\n", encoding="utf-8")
    (vendor / "survey.bin").write_bytes(b"x" * 2048)
    checksum = project_tool.source_checksum(target / relative)["checksum"]
    _write_source(
        target, path=relative, storage="snapshot", in_place_reason=None, raw_checksum=checksum
    )
    assert project_tool._outside_project_git(target, Path(relative))
    assert _checks(target) == {"source-storage": "P1"}


def test_nested_repository_below_a_source_directory_is_detected(tmp_path: Path) -> None:
    target = _project(tmp_path)
    nested = target / "data/manual/survey/vendor"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    assert project_tool._outside_project_git(target, Path("data/manual/survey"))
    assert not project_tool._outside_project_git(target, Path("data/manual/other"))
