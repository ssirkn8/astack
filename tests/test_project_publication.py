from __future__ import annotations

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    hashlib,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
)


def test_publish_creates_one_private_issue_and_removes_pending_report(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/pending.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Audit\n\nNo actionable findings.\n", encoding="utf-8")
    _git_baseline(target)

    def fake_gh_json(root: Path, args: list[str]) -> object:
        if args[:2] == ["repo", "view"]:
            return {"isPrivate": True, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        return []

    calls: list[list[str]] = []

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["git", "ls-files", "--error-unmatch", "--"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[:3] == ["git", "check-ignore", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        stdout = (
            "https://github.com/owner/repo/issues/7\n"
            if args[:3] == ["gh", "issue", "create"]
            else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.publish_audit(
        target,
        report,
        commit="a" * 40,
        scope="full",
        publish="auto",
        actionable_count=0,
        remove_on_success=True,
    )
    assert result["status"] == "github"
    assert result["url"].endswith("/7")
    assert not report.exists()
    assert any(call[:3] == ["gh", "issue", "close"] for call in calls)
    assert not any(call[:3] == ["gh", "label", "create"] for call in calls)


@pytest.mark.parametrize("report_state", ["tracked", "unignored"])
def test_publish_retains_report_when_removal_safety_cannot_be_verified(
    tmp_path: Path, monkeypatch, publication_ready: None, report_state: str
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/pending.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    if report_state == "tracked":
        report.write_text("# Audit\n", encoding="utf-8")
        _git_baseline(target)
        subprocess.run(
            ["git", "add", "-f", str(report.relative_to(target))],
            cwd=target,
            check=True,
            capture_output=True,
        )
    else:
        gitignore = target / ".gitignore"
        gitignore.write_text(
            gitignore.read_text(encoding="utf-8").replace("/reports/audits/\n", ""),
            encoding="utf-8",
        )
        _git_baseline(target)
        report.write_text("# Audit\n", encoding="utf-8")

    remote_calls: list[list[str]] = []

    def fake_gh_json(root: Path, args: list[str]) -> object:
        remote_calls.append(args)
        if args[:2] == ["repo", "view"]:
            return {"isPrivate": True, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        raise AssertionError("issue calls must not happen before removal safety checks")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    result = project_tool.publish_audit(
        target,
        report,
        commit="a" * 40,
        scope="full",
        publish="auto",
        actionable_count=1,
        remove_on_success=True,
    )

    assert result["status"] == "local-fallback"
    assert report.exists()
    assert remote_calls == [["repo", "view", "--json", "isPrivate,hasIssuesEnabled,nameWithOwner"]]
    assert ("tracked" if report_state == "tracked" else "ignored") in result["error"]


def test_publish_is_idempotent_for_same_body(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/audit.md"
    base = "# Audit\n"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(base, encoding="utf-8")
    _git_baseline(target)
    marker = project_tool._marker("b" * 40, "workflow")
    body_without_fingerprint = base.rstrip() + f"\n\n{marker}\n"
    fingerprint = hashlib.sha256(body_without_fingerprint.encode()).hexdigest()
    existing_body = body_without_fingerprint + f"<!-- ds-repo-audit:fingerprint={fingerprint} -->\n"

    def fake_gh_json(root: Path, args: list[str]) -> object:
        if args[:2] == ["repo", "view"]:
            return {"isPrivate": True, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        return [
            {"number": 4, "title": "audit", "state": "OPEN", "body": existing_body, "url": "url"}
        ]

    calls: list[list[str]] = []

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["git", "ls-files", "--error-unmatch", "--"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[:3] == ["git", "check-ignore", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.publish_audit(
        target,
        report,
        commit="b" * 40,
        scope="workflow",
        publish="auto",
        actionable_count=1,
        remove_on_success=False,
    )
    assert result["url"] == "url"
    assert not any(call[:3] == ["gh", "issue", "edit"] for call in calls)


def test_public_issue_redacts_sensitive_block_and_retains_local_report(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/public.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        (
            "<!-- public-summary:start -->\nSummary only\n<!-- public-summary:end -->\n"
            "<!-- sensitive-details:start -->private path<!-- sensitive-details:end -->\n"
        ),
        encoding="utf-8",
    )
    _git_baseline(target)
    published: list[str] = []

    def fake_gh_json(root: Path, args: list[str]) -> object:
        if args[:2] == ["repo", "view"]:
            return {"isPrivate": False, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        return []

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if args[:4] == ["git", "ls-files", "--error-unmatch", "--"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[:3] == ["git", "check-ignore", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["gh", "issue", "create"]:
            body_path = Path(args[args.index("--body-file") + 1])
            published.append(body_path.read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(
                args, 0, "https://github.com/owner/repo/issues/9\n", ""
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    monkeypatch.setattr(project_tool, "_run", fake_run)
    project_tool.publish_audit(
        target,
        report,
        commit="c" * 40,
        scope="full",
        publish="auto",
        actionable_count=1,
        remove_on_success=True,
    )
    assert report.exists()
    assert "private path" not in published[0]
    assert "remain in the ignored local audit report" in published[0]


def test_public_issue_rejects_tracked_audit_report(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/public.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "<!-- public-summary:start -->\nSummary only\n<!-- public-summary:end -->\n",
        encoding="utf-8",
    )
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
            return {"isPrivate": False, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        raise AssertionError("tracked report must not reach issue APIs")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    result = project_tool.publish_audit(
        target,
        report,
        commit="c" * 40,
        scope="full",
        publish="auto",
        actionable_count=1,
        remove_on_success=False,
    )

    assert result["status"] == "local-fallback"
    assert report.exists()
    assert calls == [["repo", "view", "--json", "isPrivate,hasIssuesEnabled,nameWithOwner"]]


def test_public_issue_fails_closed_without_explicit_summary(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/public.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Unmarked private audit body.\n", encoding="utf-8")
    _git_baseline(target)

    def fake_gh_json(root: Path, args: list[str]) -> object:
        return {"isPrivate": False, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    result = project_tool.publish_audit(
        target,
        report,
        commit="d" * 40,
        scope="full",
        publish="auto",
        actionable_count=1,
        remove_on_success=True,
    )
    assert result["status"] == "local-fallback"
    assert report.exists()


def test_public_summary_rejects_reversed_markers() -> None:
    body = "<!-- public-summary:end -->private\n<!-- public-summary:start -->trailing"
    with pytest.raises(RuntimeError, match="reversed"):
        project_tool._sanitize_public(body)


def test_public_summary_rejects_any_concrete_path() -> None:
    body = (
        "<!-- public-summary:start -->\n"
        "Input at /private/var/client-study/input.csv\n"
        "<!-- public-summary:end -->"
    )
    with pytest.raises(RuntimeError, match="path"):
        project_tool._sanitize_public(body)


def test_publish_rejects_unsupported_scope(tmp_path: Path) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/audit.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Audit\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="scope"):
        project_tool.publish_audit(
            target,
            report,
            commit="a" * 40,
            scope="full\nprivate path",
            publish="local",
            actionable_count=1,
            remove_on_success=False,
        )


def test_pull_request_marker_is_not_treated_as_matching_issue(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/private.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("Private audit.\n", encoding="utf-8")
    _git_baseline(target)
    marker = project_tool._marker("f" * 40, "workflow")

    def fake_gh_json(root: Path, args: list[str]) -> object:
        if args[:2] == ["repo", "view"]:
            return {"isPrivate": True, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}
        return [[{"number": 3, "body": marker, "pull_request": {}, "html_url": "pr-url"}]]

    calls: list[list[str]] = []

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["git", "ls-files", "--error-unmatch", "--"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[:3] == ["git", "check-ignore", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        stdout = (
            "https://github.com/owner/repo/issues/10\n"
            if args[:3] == ["gh", "issue", "create"]
            else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.publish_audit(
        target,
        report,
        commit="f" * 40,
        scope="workflow",
        publish="auto",
        actionable_count=1,
        remove_on_success=False,
    )
    assert result["url"].endswith("/10")
    assert any(call[:3] == ["gh", "issue", "create"] for call in calls)


def test_issue_publication_rejects_likely_credentials(
    tmp_path: Path, monkeypatch, publication_ready: None
) -> None:
    target = _project(tmp_path)
    report = target / "reports/audits/credential.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('api_key = "abcdefghijklmnop"\n', encoding="utf-8")
    _git_baseline(target)

    def fake_gh_json(root: Path, args: list[str]) -> object:
        return {"isPrivate": True, "hasIssuesEnabled": True, "nameWithOwner": "owner/repo"}

    monkeypatch.setattr(project_tool, "_gh_json", fake_gh_json)
    result = project_tool.publish_audit(
        target,
        report,
        commit="e" * 40,
        scope="full",
        publish="auto",
        actionable_count=1,
        remove_on_success=True,
    )
    assert result["status"] == "local-fallback"
    assert "credential" in result["error"]
