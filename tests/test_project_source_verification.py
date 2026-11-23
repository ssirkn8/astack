"""External source identity must be verified against retrieved bytes, never only declared."""

from __future__ import annotations

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _project,  # noqa: F401
    hashlib,  # noqa: F401
    json,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    yaml,  # noqa: F401
)


def _known(value: object) -> dict[str, object]:
    return {"status": "known", "value": value}


def _external_source(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "allowed_outputs": ["derived tables"],
        "title": "Versioned source",
        "path": "https://example.invalid/mutable",
        "origin": "https://example.invalid",
        "provider": "Example",
        "access_date": "2026-08-22",
        "version": "release-7",
        "license": _known("CC-BY-4.0"),
        "sensitivity": {"status": "not_applicable", "reason": "Public data"},
        "redistribution": _known("Allowed with attribution"),
        "citation": _known("Example (2026)"),
        "identity": _known({"method": "immutable_version", "identifier": "release-7"}),
        "acquisition": "Download URL",
    }
    record.update(overrides)
    return record


def _write_sources(target: Path, sources: dict[str, object]) -> None:
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources}), encoding="utf-8"
    )


def _checks(target: Path) -> set[str]:
    return {item["check"] for item in project_tool.audit(target)["findings"]}


def test_audit_reproduction_declared_only_identity_is_unverified(tmp_path: Path) -> None:
    """Regression for the pre-publication audit reproduction (row R3)."""
    target = _project(tmp_path)
    record = _external_source(
        identity=_known({"method": "other", "identifier": "trust me"}),
    )
    _write_sources(target, {"x": record})

    findings: list[object] = []
    project_tool._check_sources(target, findings)

    assert [getattr(item, "check", None) for item in findings] == ["source-identity-unverified"]
    assert getattr(findings[0], "priority", None) == "P1"


def test_declared_external_checksum_without_verification_is_unverified(tmp_path: Path) -> None:
    target = _project(tmp_path)
    record = _external_source(raw_checksum="sha256:" + "a" * 64)
    del record["identity"]
    _write_sources(target, {"x": record})

    assert "source-identity-unverified" in _checks(target)


def _retrieved(tmp_path: Path, content: bytes = b"retrieved bytes") -> Path:
    path = tmp_path / "download" / "source.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_verify_source_records_digest_and_audit_accepts_it(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write_sources(target, {"x": _external_source()})
    retrieved = _retrieved(tmp_path)

    result = project_tool.verify_source(target, "x", retrieved)

    digest = hashlib.sha256(b"retrieved bytes").hexdigest()
    assert result["status"] == "verified"
    assert result["record"] == "data/source-verification/x.json"
    evidence = json.loads((target / result["record"]).read_text(encoding="utf-8"))
    assert evidence["digest"] == f"sha256:{digest}"
    assert evidence["identifier"] == "release-7"
    assert evidence["retrieved_from"] == "https://example.invalid/mutable"
    assert "source-identity-unverified" in _checks(target)

    _write_sources(target, {"x": _external_source(verification=result["record"])})
    checks = _checks(target)
    assert "source-identity-unverified" not in checks
    assert "source-identity-verification" not in checks


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "other"),
        ("retrieved_from", "https://example.invalid/elsewhere"),
        ("identifier", "release-8"),
        ("digest", "sha256:not-a-digest"),
    ],
)
def test_verification_record_must_match_declared_identity(
    tmp_path: Path, field: str, value: str
) -> None:
    target = _project(tmp_path)
    _write_sources(target, {"x": _external_source()})
    result = project_tool.verify_source(target, "x", _retrieved(tmp_path))
    path = target / result["record"]
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence[field] = value
    path.write_text(json.dumps(evidence), encoding="utf-8")
    _write_sources(target, {"x": _external_source(verification=result["record"])})

    assert "source-identity-verification" in _checks(target)


def test_verification_digest_must_match_declared_checksum(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write_sources(target, {"x": _external_source()})
    result = project_tool.verify_source(target, "x", _retrieved(tmp_path))
    _write_sources(
        target,
        {"x": _external_source(verification=result["record"], raw_checksum="b" * 64)},
    )

    assert "source-identity-verification" in _checks(target)


def test_sha256_identity_must_equal_verified_digest(tmp_path: Path) -> None:
    target = _project(tmp_path)
    identity = _known({"method": "sha256", "identifier": "c" * 64})
    _write_sources(target, {"x": _external_source(identity=identity)})

    with pytest.raises(RuntimeError, match="do not match"):
        project_tool.verify_source(target, "x", _retrieved(tmp_path))
    assert not (target / "data/source-verification/x.json").exists()


@pytest.mark.parametrize("record", ["../outside.json", "/tmp/evidence.json", "missing.json"])
def test_verification_record_path_must_be_a_project_file(tmp_path: Path, record: str) -> None:
    target = _project(tmp_path)
    _write_sources(target, {"x": _external_source(verification=record)})

    assert "source-identity-verification" in _checks(target)


def test_verify_source_hashes_a_retrieved_directory_as_a_tree(tmp_path: Path) -> None:
    target = _project(tmp_path)
    identity = _known({"method": "object_generation", "identifier": "gs://bucket/x#123"})
    _write_sources(target, {"x": _external_source(identity=identity)})
    directory = tmp_path / "download-tree"
    (directory / "nested").mkdir(parents=True)
    (directory / "nested/a.txt").write_bytes(b"a")

    result = project_tool.verify_source(target, "x", directory)

    assert result["digest"] == f"sha256-tree-v2:{project_tool._tree_sha256_v2(directory)}"


def test_verify_source_rejects_unknown_or_local_sources(tmp_path: Path) -> None:
    target = _project(tmp_path)
    local = _external_source(path="data/raw/source.csv")
    _write_sources(target, {"local": local})

    with pytest.raises(RuntimeError, match="not declared"):
        project_tool.verify_source(target, "missing", _retrieved(tmp_path))
    with pytest.raises(RuntimeError, match="external"):
        project_tool.verify_source(target, "local", _retrieved(tmp_path))


@pytest.mark.parametrize("checksum", ["sha256:garbage", "sha256-tree-v1:{hex}"])
def test_declared_checksum_must_parse_and_match_kind(tmp_path: Path, checksum: str) -> None:
    target = _project(tmp_path)
    _write_sources(target, {"x": _external_source()})
    result = project_tool.verify_source(target, "x", _retrieved(tmp_path))
    hex_digest = result["digest"].removeprefix("sha256:")
    _write_sources(
        target,
        {
            "x": _external_source(
                verification=result["record"], raw_checksum=checksum.format(hex=hex_digest)
            )
        },
    )

    assert "source-identity-verification" in _checks(target)


def test_sha256_tree_identity_rejects_a_file_digest(tmp_path: Path) -> None:
    target = _project(tmp_path)
    retrieved = _retrieved(tmp_path)
    hex_digest = hashlib.sha256(retrieved.read_bytes()).hexdigest()
    identity = _known({"method": "sha256_tree", "identifier": hex_digest})
    _write_sources(target, {"x": _external_source(identity=identity)})

    with pytest.raises(RuntimeError, match="do not match"):
        project_tool.verify_source(target, "x", retrieved)


def test_sha256_tree_identity_with_an_explicit_file_prefix_never_matches_a_tree() -> None:
    hex_digest = "c" * 64
    assert project_tool._digest_consistency_problem(
        {}, "sha256_tree", f"sha256:{hex_digest}", ("tree", hex_digest)
    )
    # A bare hex identifier predates versioned prefixes and still means v1.
    assert (
        project_tool._digest_consistency_problem(
            {}, "sha256_tree", hex_digest, ("tree", hex_digest)
        )
        is None
    )


def _v1_verified_external(target: Path, tmp_path: Path) -> None:
    directory = tmp_path / "download-v1"
    directory.mkdir()
    (directory / "a.txt").write_bytes(b"a")
    identity = _known({"method": "object_generation", "identifier": "gs://bucket/x#1"})
    _write_sources(target, {"x": _external_source(identity=identity)})
    record = Path(project_tool.verify_source(target, "x", directory)["record"])
    stored = target / record
    evidence = json.loads(stored.read_text(encoding="utf-8"))
    evidence["digest"] = f"sha256-tree-v1:{project_tool._tree_sha256(directory)}"  # an older record
    stored.write_text(json.dumps(evidence), encoding="utf-8")
    _write_sources(target, {"x": _external_source(identity=identity, verification=str(record))})


def test_external_source_verified_with_a_v1_tree_digest_is_reported_as_legacy(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    _v1_verified_external(target, tmp_path)

    findings: list[object] = []
    project_tool._check_sources(target, findings)
    checks = {getattr(item, "check", ""): getattr(item, "priority", "") for item in findings}

    assert "source-identity-verification" not in checks
    assert checks.get("legacy-tree-checksum") == "P1"


def test_external_source_verified_with_v2_has_no_legacy_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    directory = tmp_path / "download-v2"
    directory.mkdir()
    (directory / "a.txt").write_bytes(b"a")
    identity = _known({"method": "object_generation", "identifier": "gs://bucket/x#2"})
    _write_sources(target, {"x": _external_source(identity=identity)})
    record = project_tool.verify_source(target, "x", directory)["record"]
    _write_sources(target, {"x": _external_source(identity=identity, verification=record)})

    assert "legacy-tree-checksum" not in _checks(target)
