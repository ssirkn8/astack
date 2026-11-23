"""Exact-release consumer tests use mocked GitHub evidence, never live releases."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from astack import release_upgrade as consumer
from astack import scaffold

pytestmark = pytest.mark.integration


def project(tmp_path: Path, version: str = "1.1.0") -> Path:
    root = tmp_path / "study"
    # Historical fixture versions must not drift when #40 bumps the release authority.
    with patch("astack.scaffold.workflow_version", return_value=version):
        scaffold.create_project(
            root,
            project_name="Study",
            package_name="study",
            description="Study",
            intended_use="Test upgrade ownership",
            lock=False,
        )
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return root


def published(monkeypatch, tag="v1.2.1", sha="a" * 40):
    def api(endpoint):
        if endpoint.startswith("releases/tags/"):
            return {
                "tag_name": tag,
                "draft": False,
                "prerelease": False,
                "published_at": "2026-10-07T00:00:00Z",
                "id": 42,
            }
        return {"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": sha}}

    monkeypatch.setattr(consumer, "_github_json", api)


@pytest.mark.parametrize("tag", ["main", "v1.2", "1.2.1", "v1.2.1-rc1", "a" * 40])
def test_moving_and_unpublished_refs_refused(tag):
    with pytest.raises(ValueError, match="exact release"):
        consumer.resolve_release(tag)


def test_unpublished_and_draft_release_refused(monkeypatch):
    monkeypatch.setattr(
        consumer,
        "_github_json",
        lambda _: {
            "tag_name": "v1.2.1",
            "draft": True,
            "prerelease": False,
            "published_at": None,
            "id": 42,
        },
    )
    with pytest.raises(ValueError, match="published stable"):
        consumer.resolve_release("v1.2.1")


def test_missing_release_refused(monkeypatch):
    def unavailable(_):
        raise RuntimeError("Published release evidence is unavailable")

    monkeypatch.setattr(consumer, "_github_json", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        consumer.resolve_release("v9.9.9")


def test_annotated_tag_peeled(monkeypatch):
    published(monkeypatch)
    api = consumer._github_json

    def annotated(endpoint):
        if endpoint.startswith("git/ref/"):
            return {"ref": "refs/tags/v1.2.1", "object": {"type": "tag", "sha": "b" * 40}}
        if endpoint.startswith("git/tags/"):
            return {"object": {"type": "commit", "sha": "c" * 40}}
        return api(endpoint)

    monkeypatch.setattr(consumer, "_github_json", annotated)
    assert consumer.resolve_release("v1.2.1")["commit"] == "c" * 40


def test_generated_project_has_standalone_upgrade_entry(tmp_path):
    import os
    import sys

    import yaml

    root = project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-S", str(root / ".astack/workflow.py"), "upgrade", "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(yaml.__file__).parent.parent)},
    )
    assert result.returncode == 0, result.stderr
    assert "--approve-plan" in result.stdout


def test_generated_project_upgrades_through_the_exact_release_installer(monkeypatch, tmp_path):
    root = project(tmp_path)
    release = {"tag": "v1.2.1", "commit": "a" * 40, "release_id": "42"}
    monkeypatch.setattr(consumer, "resolve_release", lambda _: release)
    calls = []

    def run(command, target):
        calls.append(command)
        payload = {
            "status": "dry-run",
            "profile": "data-science",
            "source": {"kind": "release", **release},
            "conflicts": [],
            "plan_digest": "b" * 64,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(consumer, "_run", run)
    plan = consumer.upgrade_release(root, "v1.2.1")
    assert plan["status"] == "dry-run" and plan["profile"] == "data-science"
    assert calls[0][4] == f"git+https://github.com/{consumer.REPOSITORY}.git@{'a' * 40}"
    assert calls[0][5:7] == ["astack", "install"]


def test_upgrade_without_an_installation_is_refused(tmp_path):
    with pytest.raises(ValueError, match="No astack installation"):
        consumer.upgrade_release(tmp_path, "v1.2.1")
