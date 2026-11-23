"""Read-only proposal preflight. Missing evidence stops the bot before any write.

`--local` checks version agreement offline and needs no credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

NO_PUBLISHED_RELEASE = (
    "No published release exists yet. Hold proposals until the owner publishes the baseline "
    "version, because Release Please would otherwise read the whole history as unreleased"
)


def _check_config(root: Path, config: dict[str, Any], version: str) -> None:
    if (
        config.get("skip-github-release") is not True
        or config.get("include-component-in-tag") is not False
        or config.get("include-v-in-tag") is not True
        or config.get("draft-pull-request") is not True
        or config.get("separate-pull-requests") is not False
        or set(config.get("packages", {})) != {"."}
    ):
        raise ValueError("Expected the reviewed single-package, draft, PR-only configuration")
    if "last-release-sha" in config:
        raise ValueError("Remove last-release-sha. The latest published release is the boundary")
    if version.startswith("0.") and config.get("bump-minor-pre-major") is not True:
        raise ValueError("A 0.x lineage needs bump-minor-pre-major so a breaking change stays 0.x")
    intent = root / "release/intent.toml"
    if "bootstrap-sha" in config:
        if not re.fullmatch(r"[0-9a-f]{40}", str(config["bootstrap-sha"])):
            raise ValueError("bootstrap-sha must be a full commit SHA")
        if not intent.is_file():
            raise ValueError("A lineage with bootstrap-sha needs release/intent.toml")
    elif intent.exists():
        raise ValueError("release/intent.toml needs the lineage bootstrap-sha. Reconcile them")


def _version_sources(root: Path, name: str) -> dict[str, str | None]:
    lock = tomllib.loads((root / "uv.lock").read_text())
    own = [p["version"] for p in lock["package"] if p["name"] == name]
    sources: dict[str, str | None] = {
        "uv.lock": own[0] if len(own) == 1 else None,
        ".astack/version": (root / ".astack/version").read_text().strip().removeprefix("v"),
    }
    intent = root / "release/intent.toml"
    if intent.is_file():
        sources["release/intent.toml"] = tomllib.loads(intent.read_text())["release"]["version"]
    return sources


def check_metadata(root: Path, published_tag: str | None = None) -> str:
    """Return the agreed version. Pass the published tag to also prove it is the baseline."""
    manifest = json.loads((root / ".release-please-manifest.json").read_text())
    config = json.loads((root / "release-please-config.json").read_text())
    if not isinstance(manifest, dict) or set(manifest) != {"."}:
        raise ValueError("Expected one stable published version in the root manifest")
    version = manifest["."]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected one stable published version in the root manifest")
    if published_tag is not None and published_tag != f"v{version}":
        raise ValueError(
            f"Manifest {version} does not match the latest published release {published_tag}. "
            "Reconcile it first"
        )
    _check_config(root, config, version)
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    sources = {"pyproject.toml": project["version"], **_version_sources(root, project["name"])}
    wrong = sorted(path for path, found in sources.items() if found != version)
    if wrong:
        raise ValueError(
            f"An unpublished or incomplete release candidate is present: {', '.join(wrong)} "
            f"disagree with manifest {version}. Hold proposals, finish or reject that candidate, "
            "then reconcile the published manifest baseline"
        )
    changelog = (root / "CHANGELOG.md").read_text()
    if not re.search(rf"^## \[?v?{re.escape(version)}(?=\]|\s|$)", changelog, re.MULTILINE):
        raise ValueError(f"CHANGELOG.md has no heading for {version}")
    return version


BOT_MANAGED_PROPOSAL_PATHS = frozenset(
    {".release-please-manifest.json", "pyproject.toml", "CHANGELOG.md"}
)


def _is_same_repo_proposal(pr: dict[str, Any], repository: str | None = None) -> bool:
    head = pr.get("head") or {}
    if head.get("ref") != "release-please--branches--main":
        return False
    expected = repository or ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    actual = (head.get("repo") or {}).get("full_name")
    if expected is not None:
        return actual == expected
    return actual is None


def _pr_changed_paths(files: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in files:
        if filename := item.get("filename"):
            paths.append(filename)
        if previous := item.get("previous_filename"):
            paths.append(previous)
    return paths


def check_open_candidates(
    pulls: list[dict[str, Any]],
    changed_paths: dict[int, list[str]],
    repository: str | None = None,
) -> None:
    proposals = []
    for pr in pulls:
        paths = changed_paths[pr["number"]]
        is_proposal = _is_same_repo_proposal(pr, repository)
        # Once owner preparation writes intent (or another PR bumps the manifest), hold bot updates.
        if "release/intent.toml" in paths or (
            not is_proposal and ".release-please-manifest.json" in paths
        ):
            raise ValueError(f"Release PR #{pr['number']} already owns preparation. Hold proposals")
        if is_proposal:
            if pr.get("draft") is False:
                raise ValueError(
                    f"Release proposal PR #{pr['number']} is marked ready. Hold bot updates"
                )
            extra = sorted(set(paths) - BOT_MANAGED_PROPOSAL_PATHS)
            if extra:
                raise ValueError(
                    f"Release proposal PR #{pr['number']} has maintainer edits "
                    f"({', '.join(extra)}). Hold bot updates"
                )
            proposals.append(pr)
    if len(proposals) > 1:
        raise ValueError("Multiple proposal branches need owner reconciliation")


def check_repository_settings(repository: dict[str, Any]) -> None:
    if (
        repository.get("default_branch") != "main"
        or repository.get("allow_squash_merge") is not True
        or repository.get("squash_merge_commit_title") != "PR_TITLE"
        or repository.get("squash_merge_commit_message") not in {"PR_BODY", "BLANK"}
    ):
        raise ValueError(
            "Require main, squash merging, PR_TITLE squash subject, and a PR_BODY or BLANK "
            "squash body"
        )


def check_release_on_main(tag: str, comparison: dict[str, Any]) -> None:
    if comparison.get("status") not in {"ahead", "identical"}:
        raise ValueError(f"Published tag {tag} is not an ancestor of main. Reconcile it first")


def api(endpoint: str) -> Any:
    # gh handles authentication. Never print or accept a credential as a script argument.
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        check=True,
        capture_output=True,
        text=True,
    )
    pages = json.loads(result.stdout)
    if not isinstance(pages, list) or not pages:
        raise ValueError("GitHub evidence is unavailable")
    return [item for page in pages for item in page] if isinstance(pages[0], list) else pages[0]


def latest_release(repository: str) -> dict[str, Any]:
    try:
        release: dict[str, Any] = api(f"repos/{repository}/releases/latest")
    except subprocess.CalledProcessError as error:
        if "404" in (error.stderr or ""):
            raise ValueError(NO_PUBLISHED_RELEASE) from error
        raise
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("A stable published release is required")
    return release


def preflight(root: Path) -> None:
    repository = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    check_repository_settings(api(f"repos/{repository}"))
    tag = latest_release(repository)["tag_name"]
    check_metadata(root, tag)
    check_release_on_main(tag, api(f"repos/{repository}/compare/{tag}...main"))
    pulls = api(f"repos/{repository}/pulls?state=open&base=main&per_page=100")
    paths = {
        pr["number"]: _pr_changed_paths(
            api(f"repos/{repository}/pulls/{pr['number']}/files?per_page=100")
        )
        for pr in pulls
    }
    check_open_candidates(pulls, paths, repository=repository)
    print("Published baseline and exclusive proposal preparation verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="Check version agreement offline")
    args = parser.parse_args()
    root = Path.cwd()
    try:
        if args.local:
            print(f"Release metadata agrees on {check_metadata(root)}")
        else:
            preflight(root)
    except ValueError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
