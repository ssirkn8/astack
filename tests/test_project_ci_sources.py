"""Explicit CI scope never manufactures missing scientific evidence."""

import subprocess
import sys

import pytest
from project_tool_test_support import (
    _append_validation,
    _git_baseline,
    _project,
    project_tool,
    yaml,
)

pytestmark = pytest.mark.integration


def local_project(tmp_path):
    root = _project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(
        config.read_text()
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\nrandom_seed = 42\n'
        'outputs = ["data/processed/analysis/result.txt"]\n'
    )
    (root / "scripts/pipeline.py").write_text('print("project-owned pipeline")\n')
    (root / "tests/test_pipeline.py").write_text(
        "def test_meaningful_contract():\n    assert 2+2 == 4\n"
    )
    source = {
        "title": "Private fixture",
        "path": "data/raw/private.csv",
        "origin": "Original supplied fixture",
        "provider": "Fixture provider",
        "access_date": "2026-11-03",
        "version": "snapshot-1",
        "license": "Restricted",
        "raw_checksum": "a" * 64,
        "sensitivity": "private",
        "allowed_outputs": ["aggregate"],
        "redistribution": "prohibited",
        "citation": "Fixture source (2026)",
        "availability": "local_only",
    }
    (root / "data/sources.yml").write_text(yaml.safe_dump({"sources": {"private": source}}))
    (root / "justfile").write_text(
        f'check:\n\t"{sys.executable}" -c "print(\'engineering-check\')"\n\n'
        f'integration:\n\t"{sys.executable}" -c "print(\'engineering-integration\')"\n\n'
        "ship-ready:\n\tuv run --locked python .astack/workflow.py "
        "audit --format text --run-tools --strict\n\n"
        "ci-check:\n\tuv run --locked python .astack/workflow.py "
        "audit --format text --run-tools --strict --ci\n\n"
        "reproduce:\n\tuv run --locked python .astack/workflow.py reproduce\n"
    )
    workflow = root / ".github/workflows/checks.yml"
    workflow.write_text(workflow.read_text().replace("run: just ship-ready", "run: just ci-check"))
    # A material analysis must declare VERIFY, or strict ship-ready blocks on it.
    _append_validation(root, "verify", [[sys.executable, "-c", "print('science-verify')"]])
    # A material analysis is expected to have a report.
    (root / "reports/study.md").write_text("---\ntitle: Study\nstatus: draft\n---\n\nDraft.\n")
    _git_baseline(root)
    return root


def test_limited_ci_passes_engineering_and_keeps_science_unverified(tmp_path):
    root = local_project(tmp_path)
    result = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    blocking = [f for f in result["findings"] if f["priority"] in {"P0", "P1"}]
    assert not blocking, blocking
    assert result["ci_validation"]["status"] == "pass"
    assert result["verdicts"]["computational_reproducibility"] == "not-assessed"
    assert result["verdicts"]["release_readiness"] == "not-assessed"
    assert result["ci_validation"]["source_identity"] == "not-verified-in-ci"
    assert result["ci_validation"]["reproduction"] == "not-verified-in-ci"
    assert not project_tool._strict_ready(result)
    local = project_tool.audit(root, strict=True)
    assert {"source-missing", "run-provenance"} <= {f["check"] for f in local["findings"]}
    assert local["verdicts"]["release_readiness"] == "fail"
    assert not any(f["check"] == "ci-ship-ready" for f in local["findings"])


def test_cli_scoped_pass_is_explicit_and_local_cli_still_fails(tmp_path):
    root = local_project(tmp_path)
    command = [sys.executable, ".astack/workflow.py", "audit", "--strict", "--run-tools"]
    ci = subprocess.run(command + ["--ci"], cwd=root, capture_output=True, text=True)
    assert ci.returncode == 0, ci.stdout + ci.stderr
    assert "NOT VERIFIED" in ci.stdout
    local = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert local.returncode != 0


@pytest.mark.parametrize(
    "change", ["no-policy", "no-availability", "checksum", "software", "no-verify"]
)
def test_ci_does_not_relax_undeclared_or_checkable_failures(tmp_path, change):
    root = local_project(tmp_path)
    if change == "no-policy":
        config = root / ".astack/project.toml"
        config.write_text(
            config.read_text().replace('mode = "local-only-sources"', 'mode = "invalid"')
        )
    elif change == "no-availability":
        sources = root / "data/sources.yml"
        sources.write_text(
            sources.read_text().replace("availability: local_only", "availability: ci_available")
        )
    elif change == "checksum":
        (root / "data/raw/private.csv").write_text("tampered")
    elif change == "no-verify":
        config = root / ".astack/project.toml"
        config.write_text(
            "".join(
                line
                for line in config.read_text().splitlines(keepends=True)
                if not line.startswith("verify = ")
            )
        )
    else:
        justfile = root / "justfile"
        justfile.write_text(
            justfile.read_text().replace("print('engineering-check')", "raise SystemExit(1)")
        )
    result = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert result["ci_validation"]["status"] == "fail"
    assert result["verdicts"]["release_readiness"] == "fail"


def test_ci_without_executed_tools_is_not_a_pass(tmp_path):
    result = project_tool.audit(local_project(tmp_path), strict=True, ci=True)
    assert result["ci_validation"]["status"] == "not-assessed"


def test_ci_recipe_cannot_be_masked_or_only_a_comment(tmp_path):
    root = local_project(tmp_path)
    justfile = root / "justfile"
    justfile.write_text(justfile.read_text().replace("--strict --ci", "--strict --ci || true"))
    result = project_tool.audit(root, strict=True, ci=True)
    assert any(f["check"] == "ci-ship-ready" for f in result["findings"])


@pytest.mark.parametrize("recipe", ["ci-check", "ship-ready"])
def test_script_recipes_cannot_hide_audit_failure(tmp_path, recipe):
    root = local_project(tmp_path)
    justfile = root / "justfile"
    text = justfile.read_text()
    expected = "uv run --locked python .astack/workflow.py audit --format text --run-tools --strict"
    if recipe == "ci-check":
        expected += " --ci"
    masked = f"{recipe}:\n\t#!/bin/sh\n\t{expected}\n\ttrue"
    text = text.replace(f"{recipe}:\n\t{expected}", masked)
    justfile.write_text(text)
    assert not project_tool._ship_ready_runs_strict_audit(text, ci=recipe == "ci-check")
    report = project_tool.audit(root, strict=True, ci=True)
    assert any(
        f["check"] == ("ci-ship-ready" if recipe == "ci-check" else "ship-ready")
        for f in report["findings"]
    )


def test_plain_ci_recipe_propagates_actual_child_failure(tmp_path):
    import os

    root = local_project(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text("#!/bin/sh\nexit 7\n")
    uv.chmod(0o755)
    run = subprocess.run(
        ["just", "ci-check"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
    )
    assert run.returncode != 0


def _exploratory_project(tmp_path):
    root = _project(tmp_path)
    (root / "justfile").write_text(
        f'check:\n\t"{sys.executable}" -c "print(\'engineering-check\')"\n\n'
        f'integration:\n\t"{sys.executable}" -c "print(\'engineering-integration\')"\n\n'
        "ship-ready:\n\tuv run --locked python .astack/workflow.py "
        "audit --format text --run-tools --strict\n\n"
        "ci-check:\n\tuv run --locked python .astack/workflow.py "
        "audit --format text --run-tools --strict --ci\n"
    )
    return root


def _base_source(path: str, **extra):
    return {
        "title": "Fixture",
        "path": path,
        "origin": "Original supplied fixture",
        "provider": "Fixture provider",
        "access_date": "2026-11-03",
        "version": "snapshot-1",
        "license": {"status": "known", "value": "Restricted"},
        "raw_checksum": "a" * 64,
        "sensitivity": {"status": "known", "value": "private"},
        "allowed_outputs": ["aggregate"],
        "redistribution": {"status": "known", "value": "prohibited"},
        "citation": {"status": "known", "value": "Fixture source (2026)"},
        **extra,
    }


def test_fresh_ds_scaffold_passes_limited_ci_with_empty_sources(tmp_path):
    root = _exploratory_project(tmp_path)
    _git_baseline(root)
    result = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    blocking = [f for f in result["findings"] if f["priority"] in {"P0", "P1"}]
    assert not blocking, blocking
    assert result["ci_validation"]["status"] == "pass"
    assert result["verdicts"]["computational_reproducibility"] == "not-assessed"


def test_gitignored_raw_and_in_place_sources_default_to_local_only_in_limited_ci(tmp_path):
    root = _exploratory_project(tmp_path)
    sources = {
        "raw_snapshot": _base_source("data/raw/survey.csv"),
        "external_archive": _base_source(
            "root:archive/survey",
            storage="in_place",
            in_place_reason="size",
            raw_checksum="sha256-tree-v2:" + "b" * 64,
        ),
    }
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    blocking = [f for f in ci["findings"] if f["priority"] in {"P0", "P1"}]
    assert not blocking, blocking
    assert ci["ci_validation"]["status"] == "pass"
    assert {
        (f["check"], f["evidence"])
        for f in ci["findings"]
        if f["check"].endswith("-not-verified-in-ci")
    } == {
        ("source-missing-not-verified-in-ci", "raw_snapshot"),
        ("source-path-not-verified-in-ci", "external_archive"),
    }

    local = project_tool.audit(root, strict=True)
    assert {(f["check"], f["evidence"]) for f in local["findings"] if f["priority"] == "P1"} >= {
        ("source-missing", "raw_snapshot"),
        ("source-path", "external_archive"),
    }


def test_malformed_in_place_source_path_still_fails_limited_ci(tmp_path):
    root = _exploratory_project(tmp_path)
    sources = {
        "bad_archive": _base_source(
            "root:archive/../escape.csv",
            storage="in_place",
            in_place_reason="size",
            raw_checksum="sha256:" + "b" * 64,
        ),
    }
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(f["check"] == "source-path" and f["priority"] == "P1" for f in ci["findings"])


def test_missing_committed_manual_source_still_fails_limited_ci(tmp_path):
    root = _exploratory_project(tmp_path)
    sources = {"manual_lookup": _base_source("data/manual/lookup.csv")}
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(f["check"] == "source-missing" and f["priority"] == "P1" for f in ci["findings"])


def test_material_project_with_empty_sources_cannot_use_limited_ci_to_skip_reproduction(tmp_path):
    root = local_project(tmp_path)
    (root / "data/sources.yml").write_text("schema_version: 2\nsources: {}\n")
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(
        f["check"] == "ci-source-declaration" and f["priority"] == "P1" for f in ci["findings"]
    )


def test_existing_project_running_ship_ready_with_raw_sources_gets_migration_hints(tmp_path):
    root = _exploratory_project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(config.read_text().replace('[ci]\nmode = "local-only-sources"\n', ""))
    workflow = root / ".github/workflows/checks.yml"
    workflow.write_text(workflow.read_text().replace("run: just ci-check", "run: just ship-ready"))
    (root / "data/sources.yml").write_text(
        yaml.safe_dump(
            {"schema_version": 2, "sources": {"raw_snapshot": _base_source("data/raw/s.csv")}}
        )
    )
    _git_baseline(root)

    result = project_tool.audit(root, strict=True)
    hint = next(f for f in result["findings"] if f["check"] == "ci-local-sources-hint")
    assert hint["priority"] == "P2"
    assert "just ci-check" in hint["message"]
    assert "docs/checks-and-audit.md" in hint["message"]

    missing = next(f for f in result["findings"] if f["check"] == "source-missing")
    assert missing["priority"] == "P1"
    assert "just ci-check" in missing["message"]
    assert "docs/checks-and-audit.md" in missing["message"]


def test_exploratory_project_with_ci_available_source_passes_present_and_fails_absent(tmp_path):
    import hashlib

    root = _exploratory_project(tmp_path)
    manual = root / "data/manual/lookup.csv"
    manual.write_text("k,v\n1,a\n", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(manual.read_bytes()).hexdigest()
    (root / "data/sources.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "sources": {
                    "lookup": _base_source(
                        "data/manual/lookup.csv",
                        raw_checksum=digest,
                        availability="ci_available",
                    )
                },
            }
        )
    )
    _git_baseline(root)

    present = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert present["ci_validation"]["status"] == "pass"

    manual.unlink()
    absent = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert absent["ci_validation"]["status"] == "fail"
    assert any(f["check"] == "source-missing" and f["priority"] == "P1" for f in absent["findings"])


@pytest.mark.parametrize(
    ("path", "extra", "expected_check"),
    [
        ("root:archive/bad\x00.csv", {}, "source-path"),
        ("root:archive/survey.csv", {"availability": "everywhere"}, "source-availability"),
        (
            "root:archive/survey.csv",
            {
                "raw_checksum": "not-a-digest",
                "identity": {"status": "not_applicable", "reason": "none"},
            },
            "source-checksum",
        ),
    ],
)
def test_invalid_absent_in_place_source_still_fails_limited_ci(
    tmp_path, path, extra, expected_check
):
    root = _exploratory_project(tmp_path)
    fields = {
        "storage": "in_place",
        "in_place_reason": "size",
        "raw_checksum": "sha256:" + "b" * 64,
    }
    fields.update(extra)
    source = _base_source(path, **fields)
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"bad": source}})
    )
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(f["check"] == expected_check and f["priority"] == "P1" for f in ci["findings"])


def test_symlinked_data_raw_worktree_still_detects_local_raw_source_and_hint(tmp_path):
    root = _exploratory_project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(config.read_text().replace('[ci]\nmode = "local-only-sources"\n', ""))
    workflow = root / ".github/workflows/checks.yml"
    workflow.write_text(workflow.read_text().replace("run: just ci-check", "run: just ship-ready"))
    shared_raw = tmp_path / "canonical-raw"
    shared_raw.mkdir()
    (shared_raw / "s.csv").write_text("a\n1\n", encoding="utf-8")
    raw_dir = root / "data/raw"
    raw_dir.rmdir()
    raw_dir.symlink_to(shared_raw, target_is_directory=True)
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"raw": _base_source("data/raw/s.csv")}})
    )
    _git_baseline(root)

    result = project_tool.audit(root, strict=True)
    assert any(
        f["check"] == "ci-local-sources-hint" and f["priority"] == "P2" for f in result["findings"]
    )


def test_half_migrated_project_keeps_hint_until_workflow_runs_ci_check(tmp_path):
    root = _exploratory_project(tmp_path)
    workflow = root / ".github/workflows/checks.yml"
    workflow.write_text(workflow.read_text().replace("run: just ci-check", "run: just ship-ready"))
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": {"raw": _base_source("data/raw/s.csv")}})
    )
    _git_baseline(root)

    result = project_tool.audit(root, strict=True)
    hint = next(f for f in result["findings"] if f["check"] == "ci-local-sources-hint")
    assert hint["priority"] == "P2"
    assert "Replace `just ship-ready` with `just ci-check`" in hint["message"]


def test_general_project_still_requires_explicit_local_only_in_limited_ci(tmp_path):
    root = _exploratory_project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(config.read_text().replace('profile = "data-science"', 'profile = "general"'))
    sources = {
        "external_archive": _base_source(
            "root:archive/survey",
            storage="in_place",
            in_place_reason="size",
            raw_checksum="sha256-tree-v2:" + "b" * 64,
        )
    }
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(
        f["check"] == "ci-source-declaration" and f["priority"] == "P1" for f in ci["findings"]
    )


def test_general_project_explicit_local_only_in_place_source_still_fails_without_root(tmp_path):
    root = _exploratory_project(tmp_path)
    config = root / ".astack/project.toml"
    config.write_text(config.read_text().replace('profile = "data-science"', 'profile = "general"'))
    sources = {
        "external_archive": _base_source(
            "root:archive/survey",
            storage="in_place",
            in_place_reason="size",
            raw_checksum="sha256-tree-v2:" + "b" * 64,
            availability="local_only",
        )
    }
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(f["check"] == "source-path" and f["priority"] == "P1" for f in ci["findings"])


@pytest.mark.parametrize("via", ["env", "local_toml"])
def test_relative_external_root_configuration_still_fails_limited_ci(tmp_path, monkeypatch, via):
    root = _exploratory_project(tmp_path)
    sources = {
        "external_archive": _base_source(
            "root:archive/survey",
            storage="in_place",
            in_place_reason="size",
            raw_checksum="sha256-tree-v2:" + "b" * 64,
        )
    }
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)
    if via == "env":
        monkeypatch.setenv("ASTACK_EXTERNAL_ROOT_ARCHIVE", "relative/archive")
    else:
        (root / ".astack/local.toml").write_text(
            '[data.external_roots]\narchive = "relative/archive"\n', encoding="utf-8"
        )

    ci = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci["ci_validation"]["status"] == "fail"
    assert any(f["check"] == "source-path" and f["priority"] == "P1" for f in ci["findings"])


def test_configured_external_root_symlink_loop_or_regular_file_still_fails_limited_ci(
    tmp_path, monkeypatch
):
    root = _exploratory_project(tmp_path)
    sources = {
        "external_archive": _base_source(
            "root:archive/survey",
            storage="in_place",
            in_place_reason="size",
            raw_checksum="sha256-tree-v2:" + "b" * 64,
        )
    }
    (root / "data/sources.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "sources": sources})
    )
    _git_baseline(root)

    loop = tmp_path / "is not configured on this machine."
    loop.symlink_to(loop)
    monkeypatch.setenv("ASTACK_EXTERNAL_ROOT_ARCHIVE", str(loop))
    ci_loop = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci_loop["ci_validation"]["status"] == "fail"
    assert any(f["check"] == "source-path" and f["priority"] == "P1" for f in ci_loop["findings"])

    regular_file = tmp_path / "archive-file"
    regular_file.write_text("not a directory\n", encoding="utf-8")
    for bad_root in (regular_file, regular_file / "nested"):
        monkeypatch.setenv("ASTACK_EXTERNAL_ROOT_ARCHIVE", str(bad_root))
        ci_file = project_tool.audit(root, strict=True, run_tools=True, ci=True)
        assert ci_file["ci_validation"]["status"] == "fail"
        assert any(
            f["check"] == "source-path" and f["priority"] == "P1" for f in ci_file["findings"]
        )

    valid_root = tmp_path / "valid-archive"
    valid_root.mkdir()
    nested_loop = valid_root / "survey"
    nested_loop.symlink_to(nested_loop)
    monkeypatch.setenv("ASTACK_EXTERNAL_ROOT_ARCHIVE", str(valid_root))
    ci_nested_loop = project_tool.audit(root, strict=True, run_tools=True, ci=True)
    assert ci_nested_loop["ci_validation"]["status"] == "fail"
    assert any(
        f["check"] == "source-path" and f["priority"] == "P1" for f in ci_nested_loop["findings"]
    )
