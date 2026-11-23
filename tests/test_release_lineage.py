"""Release proposal safeguards that hold for any astack lineage, including a clean public one."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lineage_preflight", ROOT / "scripts/check_release_proposal.py"
)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

CONFIG = {
    "include-v-in-tag": True,
    "include-component-in-tag": False,
    "skip-github-release": True,
    "draft-pull-request": True,
    "separate-pull-requests": False,
    "bump-minor-pre-major": True,
    "packages": {".": {"release-type": "python"}},
}


def _lineage(root: Path, version: str = "0.1.0") -> Path:
    (root / ".astack").mkdir(exist_ok=True)
    (root / ".release-please-manifest.json").write_text(json.dumps({".": version}))
    (root / "release-please-config.json").write_text(json.dumps(CONFIG))
    (root / "pyproject.toml").write_text(f'[project]\nname = "astack"\nversion = "{version}"\n')
    (root / "uv.lock").write_text(f'[[package]]\nname = "astack"\nversion = "{version}"\n')
    (root / ".astack/version").write_text(f"v{version}\n")
    (root / "CHANGELOG.md").write_text(f"# Changelog\n\n## v{version}\n")
    return root


def _edit_config(root: Path, **changes: object) -> None:
    path = root / "release-please-config.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))


def test_clean_baseline_agrees_offline_and_against_its_published_tag(tmp_path):
    _lineage(tmp_path)
    assert preflight.check_metadata(tmp_path) == "0.1.0"
    assert preflight.check_metadata(tmp_path, "v0.1.0") == "0.1.0"


def test_generated_changelog_heading_is_accepted(tmp_path):
    _lineage(tmp_path, "0.2.0")
    (tmp_path / "CHANGELOG.md").write_text("## [0.2.0](https://example.invalid) (2026-01-01)\n")
    assert preflight.check_metadata(tmp_path, "v0.2.0") == "0.2.0"


def test_merged_but_unpublished_proposal_holds_the_next_one(tmp_path):
    _lineage(tmp_path, "0.2.0")
    with pytest.raises(ValueError, match="does not match the latest published release v0.1.0"):
        preflight.check_metadata(tmp_path, "v0.1.0")


@pytest.mark.parametrize("path", ["pyproject.toml", "uv.lock", ".astack/version"])
def test_version_disagreement_names_the_file(tmp_path, path):
    _lineage(tmp_path)
    target = tmp_path / path
    target.write_text(target.read_text().replace("0.1.0", "0.1.1"))
    with pytest.raises(ValueError, match=path.replace(".", r"\.")):
        preflight.check_metadata(tmp_path)


def test_missing_changelog_heading_stops_proposals(tmp_path):
    _lineage(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    with pytest.raises(ValueError, match="CHANGELOG"):
        preflight.check_metadata(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.1.0-rc.1\n")
    with pytest.raises(ValueError, match="CHANGELOG"):
        preflight.check_metadata(tmp_path)


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"skip-github-release": False}, "PR-only"),
        ({"draft-pull-request": False}, "PR-only"),
        ({"last-release-sha": "a" * 40}, "last-release-sha"),
        ({"bump-minor-pre-major": False}, "bump-minor-pre-major"),
        ({"bootstrap-sha": "not-a-sha"}, "full commit SHA"),
        ({"bootstrap-sha": "a" * 40}, "needs release/intent.toml"),
    ],
)
def test_unsafe_configuration_stops_proposals(tmp_path, changes, message):
    _lineage(tmp_path)
    _edit_config(tmp_path, **changes)
    with pytest.raises(ValueError, match=message):
        preflight.check_metadata(tmp_path)


def test_intent_without_its_lineage_marker_stops_proposals(tmp_path):
    _lineage(tmp_path)
    (tmp_path / "release").mkdir()
    (tmp_path / "release/intent.toml").write_text('[release]\nversion = "0.1.0"\n')
    with pytest.raises(ValueError, match="bootstrap-sha"):
        preflight.check_metadata(tmp_path)


def test_no_published_release_is_a_hold_not_a_pass(monkeypatch):
    def missing(_endpoint):
        raise subprocess.CalledProcessError(1, "gh", stderr="gh: Not Found (HTTP 404)")

    monkeypatch.setattr(preflight, "api", missing)
    with pytest.raises(ValueError, match="No published release"):
        preflight.latest_release("owner/repo")


def test_other_github_failures_are_not_reported_as_unpublished(monkeypatch):
    def denied(_endpoint):
        raise subprocess.CalledProcessError(1, "gh", stderr="gh: Forbidden (HTTP 403)")

    monkeypatch.setattr(preflight, "api", denied)
    with pytest.raises(subprocess.CalledProcessError):
        preflight.latest_release("owner/repo")


@pytest.mark.parametrize("release", [{"draft": True}, {"prerelease": True}])
def test_unpublished_release_kinds_do_not_count(monkeypatch, release):
    monkeypatch.setattr(preflight, "api", lambda _endpoint: {"tag_name": "v0.1.0", **release})
    with pytest.raises(ValueError, match="stable published"):
        preflight.latest_release("owner/repo")


@pytest.mark.parametrize("status", ["behind", "diverged", None])
def test_tag_outside_main_stops_proposals(status):
    with pytest.raises(ValueError, match="not an ancestor"):
        preflight.check_release_on_main("v0.1.0", {"status": status})


@pytest.mark.parametrize("status", ["ahead", "identical"])
def test_tag_on_main_passes(status):
    preflight.check_release_on_main("v0.1.0", {"status": status})


def test_conflicting_or_stale_proposals_stop_before_bot_writes():
    proposal = {
        "number": 1,
        "draft": True,
        "head": {
            "ref": "release-please--branches--main",
            "repo": {"full_name": "ssirkn8/astack"},
        },
        "base": {"repo": {"full_name": "ssirkn8/astack"}},
    }
    other = {"number": 2, "head": {"ref": "manual-release"}}
    fork_spoof = {
        "number": 4,
        "draft": True,
        "head": {
            "ref": "release-please--branches--main",
            "repo": {"full_name": "attacker/astack"},
        },
        "base": {"repo": {"full_name": "ssirkn8/astack"}},
    }
    preflight.check_open_candidates([proposal], {1: ["pyproject.toml"]}, "ssirkn8/astack")
    with pytest.raises(ValueError, match="already owns"):
        preflight.check_open_candidates([proposal], {1: ["release/intent.toml"]}, "ssirkn8/astack")
    with pytest.raises(ValueError, match="already owns"):
        preflight.check_open_candidates(
            [proposal, other], {1: [], 2: ["release/intent.toml"]}, "ssirkn8/astack"
        )
    with pytest.raises(ValueError, match="already owns"):
        preflight.check_open_candidates(
            [proposal, other], {1: [], 2: [".release-please-manifest.json"]}, "ssirkn8/astack"
        )
    with pytest.raises(ValueError, match="already owns"):
        preflight.check_open_candidates(
            [fork_spoof], {4: [".release-please-manifest.json"]}, "ssirkn8/astack"
        )
    renamed = preflight._pr_changed_paths(
        [{"filename": "moved.json", "previous_filename": ".release-please-manifest.json"}]
    )
    with pytest.raises(ValueError, match="already owns"):
        preflight.check_open_candidates([other], {2: renamed}, "ssirkn8/astack")
    with pytest.raises(ValueError, match="marked ready"):
        preflight.check_open_candidates(
            [{**proposal, "draft": False}], {1: ["pyproject.toml"]}, "ssirkn8/astack"
        )
    with pytest.raises(ValueError, match="maintainer edits"):
        preflight.check_open_candidates(
            [proposal], {1: ["pyproject.toml", "uv.lock"]}, "ssirkn8/astack"
        )
    with pytest.raises(ValueError, match="Multiple"):
        preflight.check_open_candidates(
            [proposal, {**proposal, "number": 3}], {1: [], 3: []}, "ssirkn8/astack"
        )


def test_ui_squash_default_must_use_the_pr_title():
    settings = {
        "default_branch": "main",
        "allow_squash_merge": True,
        "squash_merge_commit_title": "PR_TITLE",
        "squash_merge_commit_message": "PR_BODY",
    }
    preflight.check_repository_settings(settings)
    preflight.check_repository_settings({**settings, "squash_merge_commit_message": "BLANK"})
    with pytest.raises(ValueError, match="PR_TITLE"):
        preflight.check_repository_settings(
            {**settings, "squash_merge_commit_title": "COMMIT_OR_PR_TITLE"}
        )
    with pytest.raises(ValueError, match="squash body"):
        preflight.check_repository_settings(
            {**settings, "squash_merge_commit_message": "COMMIT_MESSAGES"}
        )


@pytest.mark.parametrize(
    "title,valid",
    [
        ("feat: add a capability", True),
        ("fix(core)!: preserve data", True),
        ("refactor: retire engine", True),
        ("docs: explain upgrades (#12)", True),
        ("chore(main): release 0.2.0", True),
        ("fix: $(touch title-injection)", True),
        ("Add feature", False),
        ("feature: add a capability", False),
        ("fix:missing space", False),
        ('Revert "feat: add a capability"', False),
        ("Merge pull request #12 from owner/branch", False),
        ("", False),
        ("fix: first\nfeat: second", False),
        ("fix: first\rsecond", False),
    ],
)
def test_squash_subject_check(tmp_path, title, valid):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_pr_title.py")],
        cwd=tmp_path,
        env={**os.environ, "PR_TITLE": title},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is valid, result.stderr
    assert not (tmp_path / "title-injection").exists()
    if not valid:
        assert "Conventional Commit" in result.stderr


def test_ci_checks_the_title_on_every_edit_and_passes_it_as_data():
    workflow = yaml.safe_load((ROOT / ".github/workflows/checks.yml").read_text())
    assert "edited" in workflow[True]["pull_request"]["types"]
    step = next(
        step
        for step in workflow["jobs"]["check"]["steps"]
        if step.get("name") == "Validate the intended squash subject"
    )
    assert step["run"] == "uv run --locked python scripts/check_pr_title.py"
    assert "github.event" not in step["run"]
    assert "pull_request.title" in step["env"]["PR_TITLE"]


def test_proposal_workflow_cannot_merge_tag_or_release():
    text = (ROOT / ".github/workflows/release-proposal.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    assert "RELEASE_PROPOSALS_ENABLED" in workflow["jobs"]["propose"]["if"]
    steps = workflow["jobs"]["propose"]["steps"]
    action = next(s for s in steps if s.get("uses", "").startswith("googleapis/"))
    assert action["with"]["skip-github-release"] is True
    for forbidden in ("gh pr merge", "git tag", "git push", "gh release"):
        assert forbidden not in text


def test_downstream_projects_do_not_inherit_the_release_policy():
    templates = [
        p.relative_to(ROOT / "templates").as_posix() for p in (ROOT / "templates").rglob("*")
    ]
    assert not [p for p in templates if "release-please" in p or "release-proposal" in p]
