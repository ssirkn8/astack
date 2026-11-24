"""Project-local bootstrap for an explicitly selected published workflow release.

The installed bootstrap resolves provenance only. Managed-file decisions are always
made by the requested release's isolated runtime, never by this older installation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = "ssirkn8/astack"
TAG_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def release_version(tag: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError("Select an exact release tag such as v1.2.1, not main or a moving ref")
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def _github_json(endpoint: str) -> dict[str, Any]:
    try:
        # Reuse the user's authenticated GitHub CLI, including private-repository access.
        result = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/{endpoint}"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise ValueError("GitHub release evidence was not available")
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RuntimeError(
            "Published release evidence is unavailable. Check the tag and GitHub authentication."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("Invalid published release evidence")
    return data


def resolve_release(tag: str) -> dict[str, str]:
    release_version(tag)
    release = _github_json(f"releases/tags/{tag}")
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not release.get("published_at")
        or not isinstance(release.get("id"), int)
    ):
        raise ValueError("Target must be an exact published stable workflow release")
    reference = _github_json(f"git/ref/tags/{tag}")
    if reference.get("ref") != f"refs/tags/{tag}":
        raise ValueError("Published release tag does not match the requested ref")
    obj = reference.get("object", {})
    # Peel annotated tags without ever accepting a branch or a moving target_commitish.
    for _ in range(8):
        sha = obj.get("sha") if isinstance(obj, dict) else None
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError("Release tag has no verifiable commit identity")
        if obj.get("type") == "commit":
            return {"tag": tag, "commit": sha, "release_id": str(release["id"])}
        if obj.get("type") != "tag":
            raise ValueError("Release tag must resolve to a commit")
        obj = _github_json(f"git/tags/{sha}").get("object", {})
    raise ValueError("Release tag nesting exceeds the supported limit")


def resolve_latest_release() -> dict[str, str]:
    """Resolve the latest published stable release tag, then verify it via `resolve_release`."""
    release = _github_json("releases/latest")
    tag = release.get("tag_name")
    if (
        not isinstance(tag, str)
        or TAG_PATTERN.fullmatch(tag) is None
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not release.get("published_at")
        or type(release.get("id")) is not int
    ):
        raise ValueError("Latest GitHub release is not a published stable workflow release")
    return resolve_release(tag)


def _run(command: list[str], target: Path) -> subprocess.CompletedProcess[str]:
    # Do not let a project's module path shadow the exact-release tool installation.
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(command, cwd=target, env=env, text=True, capture_output=True, check=False)


def upgrade_release(
    target: Path,
    tag: str | None = None,
    *,
    apply: bool = False,
    approve_plan: str | None = None,
) -> dict[str, Any]:
    """Upgrade an astack project with the installer from a published release."""
    target = target.expanduser().resolve()
    if not (target / ".astack/manifest.json").is_file():
        raise ValueError("No astack installation found. Adopt the project with `astack install`.")
    if tag is None or tag == "latest":
        release = resolve_latest_release()
    else:
        release_version(tag)
        release = resolve_release(tag)
    return _upgrade_release(target, release, apply=apply, approve_plan=approve_plan)


def _upgrade_release(
    target: Path, release: dict[str, str], *, apply: bool, approve_plan: str | None
) -> dict[str, Any]:
    """Use the exact release's installer, without a maintainer checkout."""
    manifest = json.loads((target / ".astack/manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("profile", "general") not in {
        "general",
        "data-science",
    }:
        raise ValueError("Installed policy identity is unknown")
    expected_profile = manifest.get("profile", "general")
    command = [
        "uv",
        "tool",
        "run",
        "--from",
        f"git+https://github.com/{REPOSITORY}.git@{release['commit']}",
        "astack",
        "install",
        str(target),
        "--release",
        release["tag"],
    ]

    def invoke(extra: list[str]) -> dict[str, Any]:
        result = _run([*command, *extra], target)
        try:
            payload, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
        except ValueError as exc:
            raise RuntimeError(
                "The selected release does not provide the astack installer"
            ) from exc
        if result.returncode or not isinstance(payload, dict):
            raise RuntimeError("The exact release's installer failed")
        source = payload.get("source", {})
        if (
            payload.get("profile") != expected_profile
            or source.get("kind") != "release"
            or source.get("commit") != release["commit"]
            or source.get("tag") != release["tag"]
            or not re.fullmatch("[0-9a-f]{64}", str(payload.get("plan_digest", "")))
        ):
            raise ValueError("The release installer returned mismatched source evidence")
        return payload

    plan = invoke([])
    if plan.get("status") != "dry-run":
        raise ValueError("Release planning did not return a dry-run")
    if apply:
        if approve_plan != plan["plan_digest"] or plan.get("conflicts"):
            raise ValueError("Upgrade requires the exact conflict-free current dry-run")
        plan = invoke(["--apply", "--approve-plan", plan["plan_digest"]])
        if plan.get("status") != "applied":
            raise ValueError("Release application did not complete")
    return {
        **plan,
        "target_version": release["tag"],
        "release": release,
        "approval_required": not apply,
    }
