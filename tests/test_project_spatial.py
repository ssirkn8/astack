"""Optional structured spatial evidence: declared, checked, and validated by the project."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _project,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    yaml,  # noqa: F401
)


def _known(value: object) -> dict[str, object]:
    return {"status": "known", "value": value}


SPATIAL_RECORD: dict[str, object] = {
    "crs": _known({"id": "EPSG:32633", "axis_order": "easting,northing", "units": "metre"}),
    "vertical_datum": {"status": "not_applicable", "reason": "Two-dimensional land cover"},
    "temporal_extent": _known({"start": "2020-01-01", "end": "2020-12-31"}),
    "resolution": _known({"x": 10, "y": 10, "units": "metre"}),
    "nodata": _known(255),
    "geometry_validity": {"status": "not_applicable", "reason": "Raster data"},
    "reprojection": [
        {"from": "EPSG:4326", "to": "EPSG:32633", "method": "nearest", "step": "scripts/warp.py"}
    ],
}


def _write(target: Path, document: dict[str, object]) -> None:
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"landcover": {"title": "x"}}}),
        encoding="utf-8",
    )
    (target / "data/spatial.yml").write_text(yaml.safe_dump(document), encoding="utf-8")


def _document(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "validators": [["python", "scripts/validate_spatial.py"]],
        "sources": {"landcover": copy.deepcopy(SPATIAL_RECORD)},
        "outputs": {"data/processed/analysis/landcover.tif": copy.deepcopy(SPATIAL_RECORD)},
    }
    document.update(changes)
    return document


def _spatial(target: Path) -> dict[str, str]:
    findings: list[Any] = []
    project_tool._check_spatial(target, findings)
    return {item.check: item.evidence for item in findings}


def test_spatial_extension_is_optional(tmp_path: Path) -> None:
    target = _project(tmp_path)

    assert _spatial(target) == {}
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert not any(check.startswith("spatial") for check in checks)


def test_complete_spatial_evidence_has_no_findings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write(target, _document())

    assert _spatial(target) == {}


@pytest.mark.parametrize(
    "field",
    ["crs", "vertical_datum", "temporal_extent", "resolution", "nodata", "geometry_validity"],
)
def test_every_spatial_field_must_be_declared(tmp_path: Path, field: str) -> None:
    target = _project(tmp_path)
    document = _document()
    del document["sources"]["landcover"][field]  # type: ignore[index]
    _write(target, document)

    assert field in _spatial(target)["spatial-metadata"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("crs", _known({"id": "EPSG:32633"})),
        ("crs", _known({"id": "TBD", "axis_order": "x,y", "units": "metre"})),
        ("resolution", _known({"x": 0, "y": 10, "units": "metre"})),
        ("temporal_extent", _known({"start": "2020-01-01"})),
        ("nodata", {"status": "known"}),
    ],
)
def test_known_spatial_values_must_be_complete(tmp_path: Path, field: str, value: object) -> None:
    target = _project(tmp_path)
    document = _document()
    document["outputs"]["data/processed/analysis/landcover.tif"][field] = value  # type: ignore[index]
    _write(target, document)

    assert field in _spatial(target)["spatial-metadata"]


def test_unverified_spatial_evidence_cannot_support_release(tmp_path: Path) -> None:
    target = _project(tmp_path)
    document = _document()
    document["sources"]["landcover"]["geometry_validity"] = {  # type: ignore[index]
        "status": "not_yet_verified"
    }
    _write(target, document)

    assert "geometry_validity" in _spatial(target)["spatial-evidence-unresolved"]


def test_reprojection_steps_must_name_from_to_and_method(tmp_path: Path) -> None:
    target = _project(tmp_path)
    document = _document()
    document["sources"]["landcover"]["reprojection"] = [{"from": "EPSG:4326"}]  # type: ignore[index]
    _write(target, document)

    assert "reprojection" in _spatial(target)["spatial-metadata"]


def test_spatial_sources_must_exist_and_outputs_must_be_safe(tmp_path: Path) -> None:
    target = _project(tmp_path)
    document = _document(
        sources={"missing": copy.deepcopy(SPATIAL_RECORD)},
        outputs={"../outside.tif": copy.deepcopy(SPATIAL_RECORD)},
    )
    _write(target, document)
    findings = _spatial(target)

    assert "missing" in findings["spatial-source"]
    assert "../outside.tif" in findings["spatial-output"]


def test_declared_spatial_metadata_without_validators_is_unvalidated(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write(target, _document(validators=[]))

    assert "spatial-unvalidated" in _spatial(target)


def test_validators_run_with_run_tools_and_failures_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _write(target, _document(validators=[["python", "-c", "raise SystemExit(2)"]]))
    calls: list[list[str]] = []
    original_run = project_tool._run

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:1] == ["just"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return original_run(args, cwd=cwd, input_text=input_text)  # type: ignore[no-any-return]

    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.audit(target, run_tools=True)

    assert ["python", "-c", "raise SystemExit(2)"] in calls
    checks = {item["check"]: item["priority"] for item in result["findings"]}
    assert checks.get("spatial-runtime") == "P1"
    assert result["verdicts"]["release_readiness"] == "fail"


def test_validators_not_run_are_reported(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write(target, _document())

    checks = {item["check"] for item in project_tool.audit(target)["findings"]}

    assert "spatial-not-run" in checks


def test_numeric_zero_nodata_is_valid(tmp_path: Path) -> None:
    target = _project(tmp_path)
    document = _document()
    document["sources"]["landcover"]["nodata"] = _known(0)  # type: ignore[index]
    _write(target, document)

    assert _spatial(target) == {}


def test_non_string_status_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    target = _project(tmp_path)
    document = _document()
    document["sources"]["landcover"]["crs"] = {"status": ["known"]}  # type: ignore[index]
    _write(target, document)

    assert "crs" in _spatial(target)["spatial-metadata"]


@pytest.mark.parametrize(
    "content",
    [b"schema_version: 1\nsources: \xff\xfe\n", b"schema_version: 1\nwhen: 2020-99-99\n"],
)
def test_unparseable_spatial_file_is_a_finding(tmp_path: Path, content: bytes) -> None:
    target = _project(tmp_path)
    (target / "data/spatial.yml").write_bytes(content)

    assert "spatial-metadata" in _spatial(target)


@pytest.mark.parametrize(
    "validators",
    [
        [["python", "-V"], "python scripts/validate_spatial.py"],
        [["python", "-c", "print(1)\0"]],
    ],
)
def test_malformed_validators_are_findings_and_none_run(
    tmp_path: Path, validators: list[object]
) -> None:
    target = _project(tmp_path)
    _write(target, _document(validators=validators))

    assert "validators" in _spatial(target)["spatial-metadata"]
    assert project_tool._spatial_validators(target) == []


def test_structured_values_are_checked_in_their_original_types(tmp_path: Path) -> None:
    target = _project(tmp_path)
    document = _document()
    document["sources"]["landcover"]["crs"] = _known(  # type: ignore[index]
        {"id": {"status": "unknown"}, "axis_order": ["x", "y"], "units": ["metre"]}
    )
    _write(target, document)

    assert "crs" in _spatial(target)["spatial-metadata"]


def test_unquoted_yaml_dates_are_accepted(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write(target, _document())
    path = target / "data/spatial.yml"
    path.write_text(path.read_text(encoding="utf-8").replace("'2020-01-01'", "2020-01-01"), "utf-8")

    assert _spatial(target) == {}


@pytest.mark.parametrize("value", ["", "unknown", "TBD", None, True])
def test_known_nodata_rejects_missing_or_placeholder_values(tmp_path: Path, value: object) -> None:
    target = _project(tmp_path)
    document = _document()
    document["sources"]["landcover"]["nodata"] = _known(value)  # type: ignore[index]
    _write(target, document)

    assert "nodata" in _spatial(target)["spatial-metadata"]
