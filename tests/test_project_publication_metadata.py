"""Opt-in journal/archive handoff metadata must be complete and never invent identifiers."""

from __future__ import annotations

import shutil

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _project,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    yaml,  # noqa: F401
)

DECLARATION = (
    "\n[publication]\n"
    'license = "LICENSE"\n'
    'citation = "CITATION.cff"\n'
    'data_availability = "docs/publication/data-availability.md"\n'
    'code_availability = "docs/publication/code-availability.md"\n'
)
STATEMENT = "This statement names every source, its license, and how readers can obtain it.\n"


def _declare(target: Path, extra: str = "") -> None:
    config = target / ".astack/project.toml"
    config.write_text(config.read_text(encoding="utf-8") + DECLARATION + extra, encoding="utf-8")


def _complete(target: Path, citation: dict[str, object] | None = None) -> None:
    (target / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026 The Authors.\n" * 2)
    record = citation or {
        "cff-version": "1.2.0",
        "message": "Please cite this release.",
        "title": "Audit Study",
        "authors": [{"family-names": "Doe", "given-names": "Ada"}],
        "version": "1.0.0",
    }
    (target / "CITATION.cff").write_text(yaml.safe_dump(record), encoding="utf-8")
    for name in ("data-availability.md", "code-availability.md"):
        (target / "docs/publication" / name).write_text(STATEMENT, encoding="utf-8")


def _publication(target: Path) -> list[dict[str, str]]:
    result = project_tool.audit(target)
    return [item for item in result["findings"] if item["area"] == "publication"]


def test_templates_ship_inert_and_undeclared_is_not_assessed(tmp_path: Path) -> None:
    target = _project(tmp_path)

    assert (target / "docs/publication/CITATION.cff").is_file()
    result = project_tool.audit(target)
    assert not [item for item in result["findings"] if item["area"] == "publication"]
    assert any("[publication] is not declared" in item for item in result["not_assessed"])


def test_declared_templates_block_release_until_completed(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target)
    shutil.copy(target / "docs/publication/CITATION.cff", target / "CITATION.cff")

    findings = _publication(target)
    evidence = " ".join(item["evidence"] for item in findings)

    assert {item["priority"] for item in findings} == {"P1"}
    assert "license" in evidence  # no LICENSE is ever generated
    assert "TODO(publication)" in evidence
    assert project_tool.audit(target)["verdicts"]["release_readiness"] == "fail"


def test_complete_publication_metadata_has_no_findings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target)
    _complete(target)

    assert _publication(target) == []


def test_citation_requires_core_fields(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target)
    _complete(target, {"cff-version": "1.2.0", "message": "Cite it, please, as described below."})

    evidence = " ".join(item["evidence"] for item in _publication(target))

    assert "title" in evidence and "authors" in evidence


@pytest.mark.parametrize("doi", ["pending", "10.5281", "doi:10.5281/zenodo.1"])
def test_malformed_doi_is_rejected(tmp_path: Path, doi: str) -> None:
    target = _project(tmp_path)
    _declare(target, f'doi = "{doi}"\n')
    _complete(target)

    assert any(item["check"] == "publication-metadata" for item in _publication(target))


def test_recorded_doi_is_flagged_as_unverified_offline(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target, 'doi = "10.5281/zenodo.1234567"\n')
    _complete(target)

    findings = _publication(target)

    assert [(item["check"], item["priority"]) for item in findings] == [
        ("publication-doi-unverified", "P2")
    ]


def test_doi_in_citation_must_match_declaration(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target, 'doi = "10.5281/zenodo.1234567"\n')
    _complete(target)
    citation = yaml.safe_load((target / "CITATION.cff").read_text(encoding="utf-8"))
    citation["doi"] = "10.5281/zenodo.7654321"
    (target / "CITATION.cff").write_text(yaml.safe_dump(citation), encoding="utf-8")

    assert any("differ" in item["evidence"] for item in _publication(target))


def test_publication_paths_must_stay_in_the_project(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target)
    _complete(target)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace('license = "LICENSE"', 'license = "../LICENSE"'),
        encoding="utf-8",
    )

    assert any(item["evidence"].startswith("license") for item in _publication(target))


@pytest.mark.parametrize(
    "content",
    [
        "# A comment that is long enough to pass the length check, but holds no fields.\n",
        "null  # an explicit YAML null with a comment long enough to pass length\n",
    ],
)
def test_citation_without_fields_is_incomplete(tmp_path: Path, content: str) -> None:
    target = _project(tmp_path)
    _declare(target)
    _complete(target)
    (target / "CITATION.cff").write_text(content, encoding="utf-8")

    assert any(item["evidence"].startswith("citation") for item in _publication(target))


def test_invalid_citation_date_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _declare(target)
    _complete(target)
    path = target / "CITATION.cff"
    path.write_text(path.read_text(encoding="utf-8") + "date-released: 2026-99-99\n", "utf-8")

    assert any(item["evidence"].startswith("citation") for item in _publication(target))


def test_non_table_publication_declaration_is_a_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        'publication = "enabled"\n' + config.read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "publication-metadata" for item in result["findings"])
    assert not any("[publication] is not declared" in item for item in result["not_assessed"])
