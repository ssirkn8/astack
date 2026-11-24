"""Read-only workflow diagnostics. Project files come from `astack new` and `astack install`."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .adapters import HARNESSES
from .components import Component, load_components
from .scaffold import repository_root, workflow_version


def _check(
    checks: list[dict[str, Any]],
    identity: str,
    ready: bool,
    capability: str,
    recovery: str,
    detail: str = "",
) -> None:
    checks.append(
        {
            "id": identity,
            "required": True,
            "status": "READY" if ready else "BLOCKED",
            "capability": capability,
            "recovery": "" if ready else recovery,
            "summary": detail or ("Available" if ready else recovery),
        }
    )


def _optional_check(
    checks: list[dict[str, Any]],
    identity: str,
    *,
    found: bool,
    ready: bool,
    capability: str,
    recovery: str,
    label: str,
) -> str:
    if ready:
        status, summary, advice = "READY", "Available", ""
    elif found:
        status, summary, advice = (
            "UNAVAILABLE",
            f"{label} did not respond to --version",
            recovery,
        )
    else:
        status, summary, advice = "NOT INSTALLED", f"{label} not found on PATH", recovery
    checks.append(
        {
            "id": identity,
            "required": False,
            "status": status,
            "capability": capability,
            "recovery": advice,
            "summary": summary,
        }
    )
    return status


def _probe_binary(root: Path, name: str, args: list[str]) -> tuple[bool, bool]:
    """Return whether `name` is on PATH and whether running it with `args` succeeds."""
    executable = shutil.which(name)
    if not executable:
        return False, False
    try:
        # Capture raw bytes so non-UTF-8 version output from any CLI on PATH cannot crash doctor.
        result = subprocess.run([executable, *args], cwd=root, capture_output=True, timeout=15)
        return True, result.returncode == 0 and bool((result.stdout or result.stderr).strip())
    except (OSError, subprocess.TimeoutExpired):
        return True, False


def _inside(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe workflow path: {relative}")
    result = root / path
    if not result.resolve().is_relative_to(root):
        raise ValueError(f"Workflow path escapes root: {relative}")
    return result


PI_SETTINGS = ".pi/settings.json"
HARNESS_BINARIES = {
    "opencode": "opencode",
    "pi": "pi",
    "codex": "codex",
    "claude-code": "claude",
}
# Where each harness natively discovers the pinned upstream skills. Pi needs project trust.
UPSTREAM_DISCOVERY = {
    "claude-code": ".claude/skills/",
    "codex": ".agents/skills/",
    "opencode": ".agents/skills/",
    "pi": ".agents/skills/",
}
PI_SKILLS_ROOTS = ("../.opencode/skills",)


def _is_maintainer(root: Path) -> bool:
    return (root / "workflow/components.json").is_file() and (root / "src/astack").is_dir()


def _skill_assets(
    root: Path,
    harness: str,
    *,
    maintainer: bool,
    components: tuple[Component, ...] | None = None,
) -> list[str]:
    """Map the kept skill and command catalog to the files one harness discovers."""
    catalog = (
        components
        if components is not None
        else load_components(root if maintainer else repository_root())
    )
    # Pinned upstream skills are delivered to projects. A maintainer checkout holds the locked
    # canonical copies under `.agents/skills`, verified by the lock rather than by doctor.
    upstream = [
        component.target
        for component in catalog
        if component.kind == "upstream"
        and not maintainer
        and component.target.startswith(UPSTREAM_DISCOVERY[harness])
    ]
    if harness in {"opencode", "pi"}:
        authored = [component.target for component in catalog if component.kind == "opencode"]
        return authored + upstream
    discovery = {"codex": ".agents/skills/", "claude-code": ".claude/skills/"}[harness]
    return [
        component.target
        for component in catalog
        if component.kind == "generated-adapter" and component.target.startswith(discovery)
    ] + upstream


def _all_skill_assets(root: Path, *, maintainer: bool) -> list[str]:
    """Collect deduplicated skill and command assets across every supported harness."""
    catalog = load_components(root if maintainer else repository_root())
    return list(
        dict.fromkeys(
            asset
            for harness in HARNESSES
            for asset in _skill_assets(root, harness, maintainer=maintainer, components=catalog)
        )
    )


def doctor(root: Path, harness: str | None = None) -> dict[str, Any]:
    """Report skill-asset, installed-file, settings, and direct-tool evidence only."""
    root = root.expanduser().resolve()
    checks: list[dict[str, Any]] = []
    paths: dict[str, Any] = {"effective": str(root), "source": "argument"}
    try:
        from . import git_state

        canonical = git_state.canonical_checkout(root)
        linked = root != canonical
        cache_source = git_state.configured_source(
            canonical, git_state.DVC_CACHE_ROOT_ENV, "dvc", "cache_dir"
        )
        paths = {
            "canonical_checkout": str(canonical),
            "worktree": str(root) if linked else None,
            "data": {
                "effective": str(git_state.data_root(canonical)),
                "source": git_state.configured_source(
                    canonical, git_state.DATA_ROOT_ENV, "data", "root"
                ),
            },
            "dvc_cache": {
                "effective": str(git_state.dvc_cache_root(canonical)),
                "source": cache_source
                if cache_source != "default"
                else git_state.default_dvc_cache(canonical)[1],
            },
        }
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        paths = {"status": "unavailable", "reason": str(exc)}
    if harness is not None and harness not in HARNESSES:
        _check(checks, "harness", False, "harness", "Use opencode, pi, codex, or claude-code")
        return {
            "root": str(root),
            "paths": paths,
            "harness": harness,
            "checks": checks,
            "status": "BLOCKED",
        }
    maintainer = _is_maintainer(root)
    try:
        assets = (
            _all_skill_assets(root, maintainer=maintainer)
            if harness is None
            else _skill_assets(root, harness, maintainer=maintainer)
        )
    except (OSError, ValueError, TypeError) as exc:
        assets = []
        _check(checks, "skill-catalog", False, "workflow", f"Restore the component catalog: {exc}")
    else:
        _check(checks, "skill-catalog", bool(assets), "workflow", "Restore the component catalog")
    asset_prefix = "skill" if harness is None else harness
    for relative in assets:
        _check(
            checks,
            f"{asset_prefix}-asset-{relative}",
            _inside(root, relative).is_file(),
            "skill-assets",
            f"Restore {relative} with a safe workflow upgrade",
            relative,
        )

    if harness == "pi":
        target = _inside(root, PI_SETTINGS)
        try:
            settings = json.loads(target.read_text()) if target.exists() else {}
            _check(
                checks,
                "pi-settings",
                isinstance(settings, dict) and settings.get("enableSkillCommands") is True,
                "pi-skill-commands",
                'Add "enableSkillCommands": true to .pi/settings.json',
            )
            required = PI_SKILLS_ROOTS
            roots_ok = (
                isinstance(settings, dict)
                and isinstance(settings.get("skills", []), list)
                and all(item in settings.get("skills", []) for item in required)
            )
            _check(
                checks,
                "pi-skills-roots",
                roots_ok,
                "pi-skills",
                'Add "../.opencode/skills" to "skills" in .pi/settings.json',
            )
        except (OSError, ValueError):
            _check(
                checks,
                "pi-settings",
                False,
                "pi-skill-commands",
                "Repair Pi settings as valid JSON",
            )
            _check(
                checks, "pi-skills-roots", False, "pi-skills", "Repair Pi settings as valid JSON"
            )

    # Presence is evidence of installation, not proof that native tools discover these assets.
    checks.append(
        {
            "id": "native-discovery",
            "required": False,
            "status": "UNKNOWN",
            "capability": "native-discovery",
            "recovery": "Verify native discovery separately when that evidence is required",
            "summary": "Not verified: mapped files do not prove native harness discovery",
        }
    )
    for name, args, capability, advice in (
        ("git", ["--version"], "git", "Install Git"),
        ("uv", ["--version"], "python-tooling", "Install uv"),
    ):
        _, ready = _probe_binary(root, name, args)
        _check(checks, f"{name}-version", ready, capability, advice)
    harness_found = False
    harness_statuses: dict[str, str] = {}
    if harness is not None:
        harness_name = HARNESS_BINARIES[harness]
        harness_found, harness_ready = _probe_binary(root, harness_name, ["--version"])
        _check(checks, f"{harness_name}-version", harness_ready, "harness", f"Install {harness}")
    else:
        for name, args, capability, advice in (
            ("just", ["--version"], "project-tools", "Install just to run project recipes"),
            (
                "gh",
                ["--version"],
                "project-tools",
                "Install GitHub CLI (gh) for GitHub issue and PR workflows",
            ),
        ):
            found, ready = _probe_binary(root, name, args)
            _optional_check(
                checks,
                f"{name}-version",
                found=found,
                ready=ready,
                capability=capability,
                recovery=advice,
                label=name,
            )
        for name in HARNESSES:
            binary = HARNESS_BINARIES[name]
            found, ready = _probe_binary(root, binary, ["--version"])
            harness_statuses[name] = _optional_check(
                checks,
                f"harness-{name}",
                found=found,
                ready=ready,
                capability="harness",
                recovery=f"Install {name} if you use it",
                label=name,
            )
    try:
        expected = workflow_version()
        actual = (
            workflow_version(root)
            if maintainer
            else (root / ".astack/version").read_text().strip().removeprefix("v")
        )
        _check(
            checks,
            "workflow-version",
            actual == expected,
            "workflow-runtime",
            "Install or upgrade the generated workflow",
            f"Installed {actual}, package {expected}",
        )
    except (OSError, KeyError, ValueError):
        _check(
            checks,
            "workflow-version",
            False,
            "workflow-runtime",
            "Restore generated workflow version",
        )
    files: dict[str, str | None]
    try:
        if maintainer:
            manifest = json.loads((root / "workflow/components.json").read_text())
            files = {
                item["target"]: None
                for item in manifest["components"]
                if item["kind"] in {"opencode", "pi"}
            }
            files.update(
                {
                    component.target: None
                    for component in load_components(root)
                    if component.kind == "generated-adapter"
                }
            )
        else:
            manifest = json.loads((root / ".astack/manifest.json").read_text())
            files = {
                str(entry["path"]): str(entry["sha256"])
                for entry in manifest["managed_components"].values()
            }
            # A partial manifest cannot certify missing managed runtime assets.
            catalog = json.loads((root / ".astack/components.json").read_text())
            required_targets = {str(entry["target"]) for entry in catalog["components"]}
            if not required_targets.issubset(files):
                raise ValueError("Managed inventory is incomplete")
        if not isinstance(files, dict) or not files:
            raise ValueError("Managed inventory is empty")
        bad = [
            name
            for name, digest in files.items()
            if not _inside(root, name).is_file()
            or (
                digest is not None
                and hashlib.sha256(_inside(root, name).read_bytes()).hexdigest() != digest
            )
        ]
        ok = not bad
    except (OSError, ValueError, KeyError, TypeError):
        ok, bad = False, ["managed inventory"]
    _check(
        checks,
        "authored-inventory" if maintainer else "managed-checksums",
        ok,
        "managed-files",
        "Restore or safely upgrade managed files",
        ", ".join(bad),
    )
    try:
        executable = shutil.which("python") or shutil.which("python3")
        python_ok = bool(executable)
        if executable:
            python_ok = (
                subprocess.run(
                    [executable, "--version"], capture_output=True, timeout=15
                ).returncode
                == 0
            )
    except (OSError, subprocess.TimeoutExpired):
        python_ok = False
    _check(checks, "python", python_ok, "python", "Install Python 3")
    environment_checks = [
        item
        for item in checks
        if item["required"] and item["capability"] in {"harness", "git", "python-tooling", "python"}
    ]
    repository_checks = [
        item for item in checks if item["required"] and item not in environment_checks
    ]
    if harness is not None:
        environment_readiness: dict[str, Any] = {
            "harness": harness,
            "status": "NOT INSTALLED"
            if not harness_found
            else "READY"
            if all(c["status"] == "READY" for c in environment_checks)
            else "BLOCKED",
            "native_discovery": "UNKNOWN",
        }
    else:
        environment_readiness = {
            "harness": None,
            "status": "READY"
            if all(c["status"] == "READY" for c in environment_checks)
            else "BLOCKED",
            "detected_harnesses": [name for name in HARNESSES if harness_statuses[name] == "READY"],
            "harnesses": harness_statuses,
            "native_discovery": "UNKNOWN",
        }
    return {
        "root": str(root),
        "paths": paths,
        "harness": harness,
        "checks": checks,
        "status": (
            "READY"
            if all(item["status"] == "READY" for item in checks if item["required"])
            else "BLOCKED"
        ),
        "readiness_scope": "direct tools and file conformance; native discovery not verified",
        "repository_portability": {
            "harness": harness,
            "status": "READY"
            if all(c["status"] == "READY" for c in repository_checks)
            else "BLOCKED",
            "scope": "repository assets and configuration only",
        },
        "environment_readiness": environment_readiness,
        "control_surfaces": {
            "t3": "UNKNOWN: provider context, discovery, tools, and interaction not verified",
            "codex-cloud": "UNKNOWN: execution capabilities and portable evidence not verified",
        },
    }
