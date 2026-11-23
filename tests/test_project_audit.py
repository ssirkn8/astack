from __future__ import annotations

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _append_private_history_policy,  # noqa: F401
    _append_validation,  # noqa: F401
    _declare_scientific_outputs,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    _set_capabilities,  # noqa: F401
    _set_progress,  # noqa: F401
    hashlib,  # noqa: F401
    json,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    sys,  # noqa: F401
    yaml,  # noqa: F401
)


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_vendored_summary_and_audit_reject_ignored_evidence_symlinks(
    tmp_path: Path, link_kind: str
) -> None:
    target = _project(tmp_path)
    _, progress = _declare_scientific_outputs(target)
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    ignored = target / "scratch/symlink-evidence/result.txt"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("ignored evidence")
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(ignored)], cwd=target
        ).returncode
        == 0
    )
    link = target / "reports/linked"
    link.symlink_to(ignored if link_kind == "file" else ignored.parent)
    evidence = "reports/linked" if link_kind == "file" else "reports/linked/result.txt"
    _set_progress(
        progress, "validation", "depth-qa", {"status": "complete", "evidence": [evidence]}
    )
    shown = subprocess.run(
        [sys.executable, ".astack/workflow.py", "output-contract"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(shown.stdout)
    assert summary["status"] == "incomplete"
    assert summary["required_validation"][0]["evidence_status"] == "invalid"
    result = project_tool.audit(target)
    assert result["output_contract"] == summary
    assert any(item["check"] == "scientific-output-registry" for item in result["findings"])


def test_audit_requires_the_canonical_managed_manifest_set(tmp_path: Path) -> None:
    target = _project(tmp_path)
    manifest_path = target / ".astack/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["managed_components"].pop("runtime.workflow")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "managed-manifest")
    assert finding["priority"] == "P1"
    assert "runtime.workflow" in finding["evidence"]
    assert result["verdicts"]["workflow_conformance"] == "fail"


def test_fresh_project_has_the_canonical_managed_manifest_set(tmp_path: Path) -> None:
    result = project_tool.audit(_project(tmp_path))

    assert not any(item["check"] == "managed-manifest" for item in result["findings"])


def test_audit_ignores_private_history_policy_when_not_configured(tmp_path: Path) -> None:
    target = _project(tmp_path)
    private = target / "client-private/record.txt"
    private.parent.mkdir(parents=True)
    private.write_text("private\n", encoding="utf-8")
    _git_baseline(target)

    result = project_tool.audit(target)

    assert not any(item["check"] == "private-history-path" for item in result["findings"])


def test_audit_checks_configured_private_history_and_exact_safe_paths(tmp_path: Path) -> None:
    target = _project(tmp_path)
    safe = target / "client-private/README.md"
    unsafe = target / "client-private/nested/README.md"
    safe.parent.mkdir(parents=True)
    unsafe.parent.mkdir(parents=True)
    safe.write_text("public explanation\n", encoding="utf-8")
    unsafe.write_text("private record\n", encoding="utf-8")
    _append_private_history_policy(target, ["client-private"], ["client-private/README.md"])
    _git_baseline(target)

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "private-history-path")
    assert finding["priority"] == "P0"
    assert finding["evidence"] == "client-private/nested/README.md"


def test_audit_matches_private_history_paths_with_git_special_characters(tmp_path: Path) -> None:
    target = _project(tmp_path)
    private = target / "client-private"
    private.mkdir()
    special = private / "record\n.txt"
    special.write_text("private record\n", encoding="utf-8")
    _append_private_history_policy(target, ["client-private"])
    _git_baseline(target)

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "private-history-path")
    assert finding["priority"] == "P0"
    assert finding["evidence"] == "client-private/record\n.txt"


@pytest.mark.parametrize(
    "value",
    ["/absolute", "C:/drive", "\\\\server\\share", "../traversal", "a/../b", "a//b", "a/"],
)
def test_audit_rejects_unsafe_private_history_paths(tmp_path: Path, value: str) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\n[privacy]\n"
        + f"private_history_paths = [{value!r}]\n",
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "private-history-config")
    assert finding["priority"] == "P1"


@pytest.mark.parametrize(
    "value",
    ["foo:bar", "CON", "CON.txt", "foo?bar", "foo\tbar", "foo\nbar"],
)
def test_private_history_path_rejects_cross_platform_unsafe_components(value: str) -> None:
    assert project_tool._safe_private_history_path(value) is None


def test_audit_rejects_safe_history_paths_outside_private_roots(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _append_private_history_policy(target, ["private"], ["public/README.md"])

    result = project_tool.audit(target)

    assert any(item["check"] == "private-history-config" for item in result["findings"])


def test_private_history_check_does_not_replace_secret_scanning(tmp_path: Path) -> None:
    target = _project(tmp_path)
    private = target / "client-private/record.txt"
    private.parent.mkdir(parents=True)
    private.write_text("private\n", encoding="utf-8")
    secret = target / "public.txt"
    secret.write_text("api_key = '1234567890123456'\n", encoding="utf-8")
    _append_private_history_policy(target, ["client-private"], ["client-private/record.txt"])
    _git_baseline(target)

    result = project_tool.audit(target)
    checks = {item["check"] for item in result["findings"]}

    assert "private-history-path" not in checks
    assert "secret-pattern" in checks


def test_private_history_policy_does_not_replace_raw_immutability(tmp_path: Path) -> None:
    target = _project(tmp_path)
    raw = target / "data/raw/source.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("original\n", encoding="utf-8")
    _append_private_history_policy(target, ["client-private"], ["client-private/README.md"])
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "-f", "data/raw/source.txt"], cwd=target, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "-b", "feature/raw-change"], cwd=target, check=True)
    raw.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", "data/raw/source.txt"], cwd=target, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "raw change",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "raw-immutability" for item in result["findings"])


def test_private_history_check_reports_unavailable_history(tmp_path: Path, monkeypatch) -> None:
    target = _project(tmp_path)
    _append_private_history_policy(target, ["private"])
    original_run = project_tool._run

    def fail_history(args: list[str], *, cwd: Path, input_text: str | None = None):
        if args[:3] == ["git", "rev-list", "--all"]:
            return subprocess.CompletedProcess(args, 1, "", "history unavailable")
        return original_run(args, cwd=cwd, input_text=input_text)

    monkeypatch.setattr(project_tool, "_run", fail_history)

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "private-history")
    assert finding["priority"] == "P1"


def test_verify_runs_a_representative_fixture_backed_path(tmp_path: Path) -> None:
    target = _project(tmp_path)
    manual_input = target / "data/manual/tiny_measurements.csv"
    manual_input.write_text(
        Path("tests/fixtures/validation/tiny_measurements.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (target / "scripts/prepare.py").write_text(
        "from pathlib import Path\n"
        "rows = Path('data/manual/tiny_measurements.csv').read_text().splitlines()[1:]\n"
        "total = sum(int(row.split(',')[1]) for row in rows)\n"
        "output = Path('data/interim/analysis')\n"
        "output.mkdir(parents=True, exist_ok=True)\n"
        "(output / 'value.txt').write_text(f'{total}\\n')\n",
        encoding="utf-8",
    )
    (target / "scripts/check.py").write_text(
        "from pathlib import Path\n"
        "value = Path('data/interim/analysis/value.txt').read_text()\n"
        "assert value == '5\\n'\n"
        "output = Path('evidence/analysis')\n"
        "output.mkdir(parents=True, exist_ok=True)\n"
        "(output / 'verify.json').write_text('passed\\n')\n",
        encoding="utf-8",
    )
    _append_validation(
        target,
        "verify",
        [
            [sys.executable, "scripts/prepare.py"],
            [sys.executable, "scripts/check.py"],
        ],
    )

    result = project_tool.run_validation(target, "verify")

    assert result["status"] == "passed"
    assert (target / "evidence/analysis/verify.json").exists()


def test_release_validation_requires_a_clean_worktree(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _append_validation(target, "release", [[sys.executable, "-c", "pass"]])
    _git_baseline(target)

    result = project_tool.run_validation(target, "release")

    assert result["status"] == "passed"
    (target / "uncommitted.txt").write_text("not clean\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean Git worktree"):
        project_tool.run_validation(target, "release")


def test_audit_reports_managed_drift(tmp_path: Path) -> None:
    target = _project(tmp_path)
    managed = target / ".opencode/commands/ds-repo-audit.md"
    managed.write_text("changed\n", encoding="utf-8")
    result = project_tool.audit(target)
    finding = next(item for item in result["findings"] if item["check"] == "managed-file")
    assert finding["priority"] == "P1"
    assert finding["evidence"] == ".opencode/commands/ds-repo-audit.md"
    assert result["verdicts"]["workflow_conformance"] == "fail"


def test_audit_does_not_checksum_project_owned_scaffold_files(tmp_path: Path) -> None:
    target = _project(tmp_path)
    for relative in [
        "AGENTS.md",
        "justfile",
        "pyproject.toml",
        ".github/workflows/checks.yml",
        "data/README.md",
        "data/sources.yml",
    ]:
        path = target / relative
        path.write_text(path.read_text(encoding="utf-8") + "\nproject-owned\n", encoding="utf-8")
    result = project_tool.audit(target)
    managed_evidence = {
        item["evidence"] for item in result["findings"] if item["check"] == "managed-file"
    }
    assert not managed_evidence.intersection(
        {
            "AGENTS.md",
            "justfile",
            "pyproject.toml",
            ".github/workflows/checks.yml",
            "data/README.md",
            "data/sources.yml",
        }
    )


def test_audit_does_not_treat_scientific_review_as_passed(tmp_path: Path) -> None:
    result = project_tool.audit(_project(tmp_path))
    assert result["verdicts"]["scientific_credibility"] == "needs-interpretive-review"
    assert result["not_assessed"]


def test_dvc_output_rejects_symlinked_parent(tmp_path: Path) -> None:
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
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "artifacts/analysis").symlink_to(outside, target_is_directory=True)
    (target / "dvc.yaml").write_text(
        "stages:\n"
        "  analysis:\n"
        "    cmd: python scripts/pipeline.py\n"
        "    outs:\n"
        "      - artifacts/analysis/result.txt\n",
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(
        item["check"] == "dvc-output-path" and item["evidence"] == "artifacts/analysis/result.txt"
        for item in result["findings"]
    )


def test_audit_rejects_unsafe_legacy_data_mapping(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'layout = "standard"\nshare_raw = true',
            'layout = "legacy"\nlegacy_shared_inputs = ["data"]',
        ),
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "legacy-data-layout" for item in result["findings"])


def test_legacy_project_warns_instead_of_blocking_for_legacy_qa(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    text = config.read_text(encoding="utf-8")
    config.write_text(text[: text.index("\n[outputs]")] + "\n", encoding="utf-8")
    (target / "reports/qa").mkdir(parents=True)
    (target / "reports/qa/run.json").write_text("{}\n", encoding="utf-8")

    findings = project_tool.audit(target)["findings"]
    legacy = [item for item in findings if item["check"] == "legacy-output-path"]

    assert legacy and all(item["priority"] == "P2" for item in legacy)


def test_audit_allows_missing_empty_raw_directory_after_clone(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / "data/raw").rmdir()

    result = project_tool.audit(target)

    assert not any(item["check"] == "data-layout" for item in result["findings"])


def test_failed_git_checks_are_not_reported_as_passed(tmp_path: Path, monkeypatch) -> None:
    target = _project(tmp_path)

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(args, 0, "true\n", "")
        if args and args[0] == "git":
            return subprocess.CompletedProcess(args, 1, "", "injected git failure")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.audit(target)
    checks = {item["check"] for item in result["findings"]}
    assert "raw-immutability" in checks
    assert "commit-candidate-scan" in checks
    assert result["verdicts"]["computational_reproducibility"] == "fail"


@pytest.mark.parametrize("change", ["modify", "delete"])
def test_audit_detects_committed_raw_change_from_main(tmp_path: Path, change: str) -> None:
    target = _project(tmp_path)
    raw = target / "data/raw/source.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("original\n", encoding="utf-8")
    checksum = hashlib.sha256(raw.read_bytes()).hexdigest()
    sources = {
        "sources": {
            "source": {
                "title": "Source",
                "path": "data/raw/source.txt",
                "origin": "Provider archive",
                "provider": "Provider",
                "access_date": "2026-08-10",
                "version": "1",
                "license": "Internal",
                "raw_checksum": checksum,
                "sensitivity": "internal",
                "allowed_outputs": ["internal-analysis"],
                "redistribution": "prohibited",
                "citation": "Provider (2026)",
            }
        }
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "add", "-f", "data/raw/source.txt"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature/raw-change"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    if change == "modify":
        raw.write_text("changed\n", encoding="utf-8")
        sources["sources"]["source"]["raw_checksum"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    else:
        raw.unlink()
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            f"{change} raw snapshot",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )

    result = project_tool.audit(target)

    assert any(
        item["check"] == "raw-immutability"
        and item["priority"] == "P0"
        and item["evidence"] == "data/raw/source.txt"
        for item in result["findings"]
    )


def test_audit_uses_pre_push_sha_on_main_push(tmp_path: Path, monkeypatch) -> None:
    target = _project(tmp_path)
    raw = target / "data/raw/source.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("original\n", encoding="utf-8")
    sources = {
        "sources": {
            "source": {
                "title": "Source",
                "path": "data/raw/source.txt",
                "origin": "Provider archive",
                "provider": "Provider",
                "access_date": "2026-08-10",
                "version": "1",
                "license": "Internal",
                "raw_checksum": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "sensitivity": "internal",
                "allowed_outputs": ["internal-analysis"],
                "redistribution": "prohibited",
                "citation": "Provider (2026)",
            }
        }
    }
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "add", "-f", "data/raw/source.txt"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    raw.write_text("changed\n", encoding="utf-8")
    sources["sources"]["source"]["raw_checksum"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    (target / "data/sources.yml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "modify raw on main",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv(project_tool.RAW_BASE_SHA_ENV, before)

    result = project_tool.audit(target)

    assert any(
        item["check"] == "raw-immutability"
        and item["priority"] == "P0"
        and item["evidence"] == "data/raw/source.txt"
        for item in result["findings"]
    )


@pytest.mark.parametrize("base_sha", ["invalid", "0" * 40, "f" * 40])
def test_audit_blocks_invalid_or_unavailable_pre_push_sha(
    tmp_path: Path, monkeypatch, base_sha: str
) -> None:
    target = _project(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv(project_tool.RAW_BASE_SHA_ENV, base_sha)

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "raw-immutability")
    assert finding["priority"] == "P1"
    assert project_tool.RAW_BASE_SHA_ENV in finding["evidence"]
    assert result["verdicts"]["computational_reproducibility"] == "fail"


def test_large_text_file_is_stream_scanned_through_end(tmp_path: Path) -> None:
    target = _project(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    candidate = target / "large-notes.txt"
    with candidate.open("w", encoding="utf-8") as handle:
        for _ in range(33):
            handle.write("x" * (64 * 1024))
        handle.write('\napi_key = "abcdefghijklmnop"\n')

    result = project_tool.audit(target)

    assert any(
        item["check"] == "secret-pattern"
        and item["priority"] == "P0"
        and item["evidence"] == "large-notes.txt"
        for item in result["findings"]
    )


def test_private_publication_rejects_tracked_report_without_removal(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/tracked.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Tracked audit\n", encoding="utf-8")
    _git_baseline(target)
    subprocess.run(
        ["git", "add", "-f", str(report.relative_to(target))],
        cwd=target,
        check=True,
        capture_output=True,
    )

    calls: list[list[str]] = []

    def fake_gh_json(root: Path, args: list[str]) -> object:
        calls.append(args)
        if args[:2] == ["repo", "view"]:
            return {"isPrivate": True, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        raise AssertionError("tracked report must not reach issue APIs")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    result = project_tool.publish_audit(
        target,
        report,
        commit="a" * 40,
        scope="full",
        publish="auto",
        actionable_count=1,
        remove_on_success=False,
    )

    assert result["status"] == "local-fallback"
    assert report.exists()
    assert calls == [["repo", "view", "--json", "isPrivate,hasIssuesEnabled,nameWithOwner"]]
    assert "untracked" in result["error"]


def test_audit_report_must_stay_inside_audit_directory(tmp_path: Path) -> None:
    target = _project(tmp_path)
    with pytest.raises(RuntimeError, match="reports/audits"):
        project_tool.publish_audit(
            target,
            target / "README.md",
            commit="a" * 40,
            scope="workflow",
            publish="local",
            actionable_count=1,
            remove_on_success=False,
        )


def test_audit_report_directory_must_not_be_symlink(tmp_path: Path) -> None:
    target = _project(tmp_path)
    audit_directory = target / "reports/audits"
    audit_directory.rmdir()
    external = tmp_path / "external-audits"
    external.mkdir()
    audit_directory.symlink_to(external, target_is_directory=True)
    report = external / "audit.md"
    report.write_text("Audit\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool.publish_audit(
            target,
            audit_directory / "audit.md",
            commit="a" * 40,
            scope="workflow",
            publish="local",
            actionable_count=1,
            remove_on_success=False,
        )


def test_audit_identity_requires_head_and_clean_worktree(tmp_path: Path) -> None:
    target = _project(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    project_tool._verify_audit_identity(target.resolve(), head)
    (target / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean worktree"):
        project_tool._verify_audit_identity(target.resolve(), head)
