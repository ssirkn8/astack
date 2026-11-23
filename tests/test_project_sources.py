from __future__ import annotations

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    _set_capabilities,  # noqa: F401
    hashlib,  # noqa: F401
    json,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    yaml,  # noqa: F401
)


def test_audit_detects_ignored_raw_snapshot_mutation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    raw = target / "data/raw/source.bin"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"original")
    checksum = hashlib.sha256(raw.read_bytes()).hexdigest()
    sources = {
        "sources": {
            "source": {
                "title": "Source",
                "path": "data/raw/source.bin",
                "origin": "Provider archive",
                "provider": "Provider",
                "access_date": "2026-07-16",
                "version": "2026-07-16",
                "license": "Restricted",
                "raw_checksum": checksum,
                "sensitivity": "internal",
                "allowed_outputs": ["internal-analysis"],
                "redistribution": "prohibited",
                "citation": "Provider (2026)",
            }
        }
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    raw.write_bytes(b"mutated")
    result = project_tool.audit(target)
    assert any(
        item["check"] == "raw-checksum" and item["priority"] == "P0" for item in result["findings"]
    )


def test_provenance_allows_source_under_configured_external_raw_root(tmp_path: Path) -> None:
    target = _project(tmp_path)
    external = tmp_path / "external-data/raw"
    external.mkdir(parents=True)
    source = external / "source.bin"
    source.write_bytes(b"external raw source")
    (target / "data/raw").rmdir()
    (target / "data/raw").symlink_to(external, target_is_directory=True)
    (target / ".astack/local.toml").write_text(
        '[data]\nroot = "../external-data"\n', encoding="utf-8"
    )
    sources = {
        "sources": {
            "source": {
                "title": "External local source",
                "path": "data/raw/source.bin",
                "origin": "Provider archive",
                "provider": "Provider",
                "access_date": "2026-08-22",
                "version": "2026-08-22",
                "license": "Restricted",
                "raw_checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sensitivity": "internal",
                "allowed_outputs": ["internal-analysis"],
                "redistribution": "prohibited",
                "citation": "Provider (2026)",
            }
        }
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")

    result = project_tool.audit(target)
    checks = {item["check"] for item in result["findings"]}

    assert "source-path" not in checks
    assert "source-missing" not in checks
    assert "raw-checksum" not in checks


def test_audit_fails_for_missing_recorded_local_source(tmp_path: Path) -> None:
    target = _project(tmp_path)
    sources = {
        "sources": {
            "missing": {
                "title": "Missing source",
                "path": "data/raw/missing.bin",
                "origin": "Provider archive",
                "provider": "Provider",
                "access_date": "2026-07-16",
                "version": "2026-07-16",
                "license": "Restricted",
                "raw_checksum": "0" * 64,
                "sensitivity": "internal",
                "allowed_outputs": ["internal-analysis"],
                "redistribution": "prohibited",
                "citation": "Provider (2026)",
            }
        }
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    result = project_tool.audit(target)
    assert any(item["check"] == "source-missing" for item in result["findings"])


def test_directory_source_uses_verifiable_tree_checksum(tmp_path: Path) -> None:
    target = _project(tmp_path)
    source_dir = target / "data/raw/dataset"
    source_dir.mkdir(parents=True)
    (source_dir / "a.txt").write_text("a\n", encoding="utf-8")
    (source_dir / "b.txt").write_text("b\n", encoding="utf-8")
    checksum = project_tool._tree_sha256_v2(source_dir)
    sources = {
        "sources": {
            "dataset": {
                "title": "Dataset",
                "path": "data/raw/dataset",
                "origin": "Provider archive",
                "provider": "Provider",
                "access_date": "2026-07-16",
                "version": "2026-07-16",
                "license": "Restricted",
                "raw_checksum": f"sha256-tree-v2:{checksum}",
                "sensitivity": "internal",
                "allowed_outputs": ["internal-analysis"],
                "redistribution": "prohibited",
                "citation": "Provider (2026)",
            }
        }
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    result = project_tool.audit(target)
    assert not any(item["check"] == "raw-checksum" for item in result["findings"])


@pytest.mark.parametrize("field", ["acquisition", "retrieval"])
@pytest.mark.parametrize(
    "evidence", [None, "   ", "TBD", " UNKNOWN ", "Not Available", "TODO", "pending"]
)
def test_external_source_rejects_missing_or_placeholder_retrieval_evidence(
    tmp_path: Path, field: str, evidence: str | None
) -> None:
    target = _project(tmp_path)
    source: dict[str, object] = {
        "title": "External source",
        "path": "https://example.test/source.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "internal",
        "allowed_outputs": ["internal-analysis"],
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
        field: evidence,
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"external": source}}), encoding="utf-8"
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "source-acquisition" for item in result["findings"])


def test_external_source_accepts_either_valid_procedure_field(tmp_path: Path) -> None:
    target = _project(tmp_path)
    source = {
        "title": "External source",
        "path": "https://example.test/source.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "internal",
        "allowed_outputs": ["internal-analysis"],
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
        "acquisition": "TBD",
        "retrieval": "Versioned retrieval script at scripts/fetch_source.py",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"external": source}}), encoding="utf-8"
    )

    result = project_tool.audit(target)

    assert not any(item["check"] == "source-acquisition" for item in result["findings"])


def test_source_requires_machine_readable_allowed_outputs(tmp_path: Path) -> None:
    target = _project(tmp_path)
    source = {
        "title": "Source",
        "path": "https://example.test/source.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "internal",
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
        "retrieval": "Recorded retrieval procedure",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"external": source}}), encoding="utf-8"
    )

    result = project_tool.audit(target)

    assert any(
        item["check"] == "source-fields" and "allowed_outputs" in item["evidence"]
        for item in result["findings"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("access_date", "YYYY-MM-DD"), ("provider", ["Provider"]), ("citation", True)],
)
def test_source_rejects_format_placeholders_and_non_text_scalars(
    tmp_path: Path, field: str, value: object
) -> None:
    target = _project(tmp_path)
    source: dict[str, object] = {
        "title": "Source",
        "path": "https://example.test/source.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "internal",
        "allowed_outputs": ["internal-analysis"],
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
        "retrieval": "Versioned retrieval procedure",
    }
    source[field] = value
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"external": source}}), encoding="utf-8"
    )

    result = project_tool.audit(target)

    assert any(
        item["check"] == "source-fields" and field in item["evidence"]
        for item in result["findings"]
    )


@pytest.mark.parametrize(
    "allowed_outputs", ["   ", "TBD", "unknown", ["Not Available"], ["TODO"], ["pending"]]
)
def test_source_rejects_placeholder_allowed_outputs(
    tmp_path: Path, allowed_outputs: object
) -> None:
    target = _project(tmp_path)
    source = {
        "title": "Source",
        "path": "https://example.test/source.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "internal",
        "allowed_outputs": allowed_outputs,
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
        "retrieval": "Recorded retrieval procedure",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"external": source}}), encoding="utf-8"
    )

    result = project_tool.audit(target)

    assert any(
        item["check"] == "source-fields" and "allowed_outputs" in item["evidence"]
        for item in result["findings"]
    )


def test_v2_provenance_accepts_uncertainty_but_does_not_pass_it(tmp_path: Path) -> None:
    target = _project(tmp_path)
    state = {"status": "unknown"}
    sources = {
        "schema_version": 2,
        "sources": {
            "external": {
                "allowed_outputs": ["aggregated statistics"],
                "title": "External source",
                "path": "https://example.com/data",
                "origin": "https://example.com",
                "provider": "Example",
                "access_date": "2026-08-22",
                "version": "2026-08",
                "license": state,
                "sensitivity": {"status": "not_applicable", "reason": "Public aggregate"},
                "redistribution": state,
                "citation": state,
                "identity": {"status": "not_yet_verified"},
                "retrieval": "Download the immutable August 2026 snapshot.",
            }
        },
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")

    result = project_tool.audit(target)
    checks = {item["check"] for item in result["findings"]}

    assert "source-fields" not in checks
    assert "source-evidence-unresolved" in checks
    assert "source-checksum" in checks
    assert result["verdicts"]["computational_reproducibility"] == "fail"


def test_external_source_can_use_known_immutable_identity_without_checksum(tmp_path: Path) -> None:
    target = _project(tmp_path)

    def known(value: object) -> dict[str, object]:
        return {"status": "known", "value": value}

    sources = {
        "schema_version": 2,
        "sources": {
            "external": {
                "allowed_outputs": ["derived tables"],
                "title": "Versioned source",
                "path": "https://example.com/data",
                "origin": "https://example.com",
                "provider": "Example",
                "access_date": "2026-08-22",
                "version": "release-7",
                "license": known("CC-BY-4.0"),
                "sensitivity": {"status": "not_applicable", "reason": "Public data"},
                "redistribution": known("Allowed with attribution"),
                "citation": known("Example (2026)"),
                "identity": known({"method": "immutable_version", "identifier": "release-7"}),
                "retrieval": "Download release-7 from the provider archive.",
            }
        },
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")

    result = project_tool.audit(target)

    assert not any(item["check"] == "source-checksum" for item in result["findings"])
    assert not any(item["check"] == "source-evidence-unresolved" for item in result["findings"])


def test_external_source_cannot_mark_verifiable_identity_not_applicable(tmp_path: Path) -> None:
    target = _project(tmp_path)

    def known(value: object) -> dict[str, object]:
        return {"status": "known", "value": value}

    source = {
        "allowed_outputs": ["derived tables"],
        "title": "Unidentified source",
        "path": "https://example.com/data",
        "origin": "https://example.com",
        "provider": "Example",
        "access_date": "2026-08-22",
        "version": "release-7",
        "license": known("CC-BY-4.0"),
        "sensitivity": {"status": "not_applicable", "reason": "Public data"},
        "redistribution": known("Allowed"),
        "citation": known("Example (2026)"),
        "identity": {"status": "not_applicable", "reason": "Not recorded"},
        "retrieval": "Download release-7.",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"external": source}}),
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "source-checksum" for item in result["findings"])


@pytest.mark.integration
def test_material_pipeline_reproduction_writes_local_run_provenance(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + (
            '\n[reproduction]\ncomponent = "analysis"\n'
            'command = ["python", "scripts/pipeline.py"]\nrandom_seed = 42\n'
            'outputs = ["data/processed/analysis/result.txt"]\n'
        ),
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        "Path('data/processed/analysis').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/processed/analysis/result.txt').write_text('result\\n')\n",
        encoding="utf-8",
    )
    (target / "tests/test_pipeline.py").write_text(
        "def test_pipeline_contract():\n    assert True\n", encoding="utf-8"
    )
    justfile = target / "justfile"
    justfile.write_text(
        justfile.read_text(encoding="utf-8")
        + "\nreproduce:\n\tuv run --locked python .astack/workflow.py reproduce\n",
        encoding="utf-8",
    )
    _git_baseline(target)

    result = project_tool.run_reproduction(target)
    record_path = Path(result["record"])
    record = json.loads(record_path.read_text(encoding="utf-8"))

    assert record_path.is_relative_to(target / ".cache/runs/analysis")
    assert record["git_commit"]
    assert record["sources_manifest_fingerprint"]
    assert record["inputs"]["sha256"]
    assert record["config_fingerprint"]
    assert record["lockfile_fingerprint"]
    assert record["engine"] == "command"
    assert record["random_seed"] == 42
    assert record["workflow_version"]
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "reproduction-command" not in checks
    assert "reproduction-interface" not in checks
    assert "run-provenance" not in checks
    assert "analytical-tests" not in checks
    strict_checks = {item["check"] for item in project_tool.audit(target, strict=True)["findings"]}
    assert "run-provenance" in strict_checks
    promoted = project_tool.run_reproduction(target, promote=True)
    promoted_path = Path(promoted["promoted_record"])
    assert promoted_path.is_relative_to(target / "evidence/analysis/runs")
    subprocess.run(["git", "add", str(promoted_path)], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "Record promoted reproduction",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    strict_checks = {item["check"] for item in project_tool.audit(target, strict=True)["findings"]}
    assert "run-provenance" not in strict_checks
    assert not any(
        item["check"] == "run-provenance" for item in project_tool.audit(target)["findings"]
    )
    for local_record in (target / ".cache/runs/analysis").glob("*.json"):
        local_record.unlink()
    promoted_path.write_text("{}\n", encoding="utf-8")
    assert any(item["check"] == "run-provenance" for item in project_tool.audit(target)["findings"])


def test_reproduction_detects_same_metadata_raw_content_mutation(tmp_path: Path) -> None:
    target = _project(tmp_path)
    raw = target / "data/raw/source.txt"
    raw.write_text("original\n", encoding="utf-8")
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
        "path = Path('data/raw/source.txt')\n"
        "metadata = path.stat()\n"
        "path.write_text('modified\\n')\n"
        "os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))\n",
        encoding="utf-8",
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="raw input"):
        project_tool.run_reproduction(target)


def test_acquisition_rejects_rewriting_existing_raw_snapshot(tmp_path: Path) -> None:
    target = _project(tmp_path)
    existing = target / "data/raw/existing.txt"
    existing.write_text("original\n", encoding="utf-8")
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[acquisition]\ncommand = ["python", "scripts/acquire.py"]\n',
        encoding="utf-8",
    )
    (target / "scripts/acquire.py").write_text(
        "from pathlib import Path\nPath('data/raw/existing.txt').write_text('changed\\n')\n",
        encoding="utf-8",
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="existing raw input"):
        project_tool.run_acquisition(target)

    assert existing.read_text(encoding="utf-8") == "original\n"


def test_reproduction_rejects_tracked_source_mutation(tmp_path: Path) -> None:
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
        "from pathlib import Path\nPath('README.md').write_text('changed\\n')\n",
        encoding="utf-8",
    )
    _git_baseline(target)

    with pytest.raises(RuntimeError, match="prohibited worktree changes"):
        project_tool.run_reproduction(target)
    assert not list((target / "evidence/analysis/runs").glob("*.json"))


def test_nested_report_requires_provenance_and_durable_qa(tmp_path: Path) -> None:
    target = _project(tmp_path)
    report = target / "reports/nested/report.qmd"
    report.parent.mkdir(parents=True)
    report.write_text("# Nested report\n", encoding="utf-8")
    _set_capabilities(target, ["quarto"])
    result = project_tool.audit(target)
    checks = {item["check"] for item in result["findings"]}
    assert "source-coverage" in checks
    assert "report-qa" in checks


def test_raw_coverage_ignores_os_metadata(tmp_path: Path) -> None:
    target = _project(tmp_path)
    data = target / "data/raw/table.csv"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("a\n", encoding="utf-8")
    record = {
        "title": "Table",
        "path": "data/raw/table.csv",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "2026-07-16",
        "license": "Restricted",
        "raw_checksum": "sha256:" + hashlib.sha256(data.read_bytes()).hexdigest(),
        "sensitivity": "internal",
        "allowed_outputs": ["internal-analysis"],
        "redistribution": "prohibited",
        "citation": "Provider (2026)",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"table": record}}), encoding="utf-8"
    )
    # Browsing data/raw in Finder or Explorer leaves these behind; they are not sources.
    for name in (".DS_Store", "._table.csv", "Thumbs.db", "desktop.ini"):
        (target / "data/raw" / name).write_bytes(b"metadata")
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "raw-source-coverage" not in checks
    (target / "data/raw/other.csv").write_text("b\n", encoding="utf-8")
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "raw-source-coverage" in checks  # Real data must still be declared.


@pytest.mark.parametrize("version", ["[]", "{}", "true", "'2'", "2.0"])
def test_malformed_schema_version_is_a_finding(tmp_path: Path, version: str) -> None:
    target = _project(tmp_path)
    (target / "data/sources.yml").write_text(
        f"schema_version: {version}\nsources: {{}}\n", encoding="utf-8"
    )
    findings: list[project_tool.Finding] = []
    project_tool._check_sources(target, findings)
    assert [item.check for item in findings] == ["sources-schema-version"]


def test_non_string_source_ids_are_findings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "data/sources.yml").write_text(
        "schema_version: 2\nsources:\n  1: {}\n  '': {}\n  x: {}\n", encoding="utf-8"
    )
    findings: list[project_tool.Finding] = []
    project_tool._check_sources(target, findings)
    by_check = {item.check: item for item in findings}
    assert by_check["source-id"].evidence == "'', 1"
    # Valid keys are still audited after the invalid ones are reported.
    assert by_check["source-fields"].evidence.startswith("x:")
