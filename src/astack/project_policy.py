"""Small project policy seam shared by packaged and vendored lifecycle runtimes."""

from __future__ import annotations

import fnmatch
import subprocess
import tomllib
from pathlib import Path
from typing import Any

PROFILE_REQUIRED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "general": (),
    "data-science": ("scientific-review", "scientific-validation"),
}
PROFILES = tuple(PROFILE_REQUIRED_CAPABILITIES)
VALIDATION_TIERS = ("check", "verify", "release")


def project_config_path(root: Path) -> Path:
    return root / ".astack/project.toml"


def validate_project_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate a parsed `.astack/project.toml` mapping."""
    profile = config.get("profile", "general")
    if profile not in PROFILES:
        raise ValueError(f"Unsupported workflow profile: {profile!r}")
    capabilities = config.get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities):
        raise ValueError("Project capabilities must be a list of strings")
    if len(capabilities) != len(set(capabilities)):
        raise ValueError("Project capabilities must be unique")
    required = PROFILE_REQUIRED_CAPABILITIES[profile]
    if required and not set(required).issubset(capabilities):
        raise ValueError("Data Science requires scientific review and validation capabilities")
    if "schema_version" in config and (
        type(config["schema_version"]) is not int or config["schema_version"] != 1
    ):
        raise ValueError("Unsupported project policy schema_version")
    return config


def project_config(root: Path) -> dict[str, Any]:
    path = project_config_path(root)
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    return validate_project_config(config)


def project_profile(root: Path) -> str:
    return str(project_config(root).get("profile", "general"))


def runtime_directory(root: Path) -> Path:
    return root / ".astack"


RISK_LEVELS = ("low", "medium", "high", "critical")
PROTECTED_REVIEW_PATHS = (
    ".github/**",
    ".opencode/**",
    ".astack/**",
    "release/**",
    "**/installation*",
    "**/worktrees*",
    "auth*",
    "*credential*",
    "permission*",
    "secret*",
    "export*",
    "install*",
    "upgrade*",
    "migration*",
    "validat*",
    "reproduc*",
    "publish*",
    "release*",
    "workflow*",
    "privacy*",
    "licens*",
    "source*",
    "pyproject.toml",
    "uv.lock",
    "package*.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.*",
    "go.mod",
    "go.sum",
    "requirements*.txt",
    "LICENSE*",
)
ROOT_PROTECTED_REVIEW_PATHS = ("REVIEW.md",)
SCIENTIFIC_REVIEW_PATHS = (
    "analysis/**",
    "analyses/**",
    "data/**",
    "reports/**",
    "notebooks/**",
    "**/*.ipynb",
)


def _normalize_review_paths(paths: list[str], *, literal_paths: bool = False) -> list[str]:
    """Validate candidate relative paths before policy glob matching.

    When `literal_paths=True`, paths come from Git's POSIX NUL-delimited output, so literal
    backslashes and carriage returns are preserved verbatim instead of rewritten as separators.
    """
    if not isinstance(paths, list):
        raise ValueError("Review paths must be relative candidate paths")
    normalized: list[str] = []
    for path in paths:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("Review paths must be relative candidate paths")
        if literal_paths:
            raw_parts = path.split("/")
            if path.startswith("/") or ".." in raw_parts:
                raise ValueError("Review paths must be relative candidate paths")
            parts = [part for part in raw_parts if part and part != "."]
            if not parts:
                raise ValueError("Review paths must be relative candidate paths")
            normalized.append(path)
            continue
        if path.startswith(("/", "\\")) or (len(path) > 1 and path[1] == ":"):
            raise ValueError("Review paths must be relative candidate paths")
        raw_parts = path.replace("\\", "/").split("/")
        if ".." in raw_parts:
            raise ValueError("Review paths must be relative candidate paths")
        parts = [part for part in raw_parts if part and part != "."]
        if not parts:
            raise ValueError("Review paths must be relative candidate paths")
        normalized.append("/".join(parts))
    return normalized


def review_risk(
    paths: list[str],
    *,
    requested: str = "auto",
    scientific_changes: bool = False,
    unknown_factors: bool = False,
    literal_paths: bool = False,
) -> dict[str, Any]:
    """Classify scrutiny, never readiness or reviewer count, from conservative evidence."""
    if requested not in ("auto", *RISK_LEVELS):
        raise ValueError("Unknown requested review risk")
    if type(scientific_changes) is not bool or type(unknown_factors) is not bool:
        raise ValueError("Review risk flags must be boolean")
    normalized = _normalize_review_paths(paths, literal_paths=literal_paths)
    protected = sorted(
        {
            path
            for path in normalized
            if path in ROOT_PROTECTED_REVIEW_PATHS
            or any(
                fnmatch.fnmatchcase(path, pattern)
                or fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern)
                for pattern in PROTECTED_REVIEW_PATHS
            )
        }
    )
    science_paths = sorted(
        {
            path
            for path in normalized
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in SCIENTIFIC_REVIEW_PATHS)
        }
    )
    reasons = []
    documentation_only = bool(normalized) and all(
        path.endswith(".md") and (path.startswith("docs/") or path in {"README.md", "CHANGELOG.md"})
        for path in normalized
    )
    floor = "low" if documentation_only else "medium"
    if protected:
        reasons.append("Protected permissions, dependencies, workflow, upgrade, or export surface")
    if scientific_changes or science_paths:
        reasons.append("Scientific claims, transformations, validation, or provenance")
    if unknown_factors or not normalized:
        reasons.append("Material risk evidence is unknown")
    if reasons:
        floor = "high"
    effective = floor if requested == "auto" else max((floor, requested), key=RISK_LEVELS.index)
    return {
        "risk": effective,
        "risk_floor": floor,
        "risk_reasons": reasons,
        "protected_paths": protected,
        "scientific_paths": science_paths,
        "reviewer_configuration": "single",
        "promotion": "not_enabled",
    }


def review_policy_for_config(
    config: dict[str, Any],
    paths: list[str],
    *,
    requested_risk: str = "auto",
    scientific_changes: bool = False,
    unknown_factors: bool = False,
    literal_paths: bool = False,
) -> dict[str, Any]:
    """Evaluate `review_policy` against an already-loaded project configuration."""
    validated = validate_project_config(config)
    declaration = validated.get("review", {})
    if not isinstance(declaration, dict):
        raise ValueError("review must be a table")
    science = declaration.get("scientific", False)
    patterns = declaration.get("scientific_paths", [])
    if (
        type(science) is not bool
        or not isinstance(patterns, list)
        or any(not isinstance(p, str) or not p for p in patterns)
    ):
        raise ValueError(
            "review.scientific must be boolean and scientific_paths must be string patterns"
        )
    normalized = _normalize_review_paths(paths, literal_paths=literal_paths)
    declared = (
        validated.get("scientific_outputs") is not None
        or validated.get("output_contract") is not None
    )
    matched = sorted(
        {
            path
            for path in normalized
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
        }
    )
    capability_science = bool(
        {"scientific-review", "scientific-validation"}.intersection(
            validated.get("capabilities", [])
        )
    )
    risk = review_risk(
        normalized,
        requested=requested_risk,
        scientific_changes=bool(
            science or declared or matched or capability_science or scientific_changes
        ),
        unknown_factors=unknown_factors,
        literal_paths=literal_paths,
    )
    applicable = bool(
        science
        or declared
        or matched
        or capability_science
        or scientific_changes
        or risk["scientific_paths"]
    )
    return {
        **risk,
        "profile": str(validated.get("profile", "general")),
        "required_axes": ["acceptance", "engineering_reproducibility"]
        + (["scientific_credibility"] if applicable else []),
        "scientific_applicability": "required" if applicable else "interpretive",
        "matched_scientific_paths": matched,
    }


def review_policy(
    root: Path,
    paths: list[str],
    *,
    requested_risk: str = "auto",
    scientific_changes: bool = False,
    unknown_factors: bool = False,
    literal_paths: bool = False,
) -> dict[str, Any]:
    """Conservative applicability floor, independent review still assesses actual claims."""
    return review_policy_for_config(
        project_config(root),
        paths,
        requested_risk=requested_risk,
        scientific_changes=scientific_changes,
        unknown_factors=unknown_factors,
        literal_paths=literal_paths,
    )


def validation_commands(root: Path, tier: str) -> list[list[str]]:
    if tier not in VALIDATION_TIERS:
        raise ValueError(f"Unknown validation tier: {tier}")
    config = project_config(root)
    declaration = config.get("validation", {})
    if not isinstance(declaration, dict):
        raise ValueError("validation must be a table")
    unsupported = set(declaration) - set(VALIDATION_TIERS)
    if unsupported:
        raise ValueError(f"Unsupported validation tiers: {sorted(unsupported)}")
    commands = declaration.get(tier)
    if not isinstance(commands, list) or not commands:
        raise ValueError(
            f"Validation tier {tier!r} is not configured in {project_config_path(root)}"
        )
    if any(
        not isinstance(command, list)
        or not command
        or not isinstance(command[0], str)
        or not command[0]
        or any(not isinstance(arg, str) or "\x00" in arg for arg in command)
        for command in commands
    ):
        raise ValueError(
            f"validation.{tier} must contain string argv arrays with a nonempty executable"
        )
    return commands


def run_project_validation(root: Path, tier: str, *, timeout: int = 600) -> dict[str, Any]:
    """Execute exact argv in the project, with bounded failures and no implicit shell."""
    root = root.resolve()
    commands = validation_commands(root, tier)
    config = project_config(root)
    verify: dict[str, str] | None = None
    if tier == "check" and "scientific-validation" in config.get("capabilities", []):
        declared = config.get("validation", {})
        if isinstance(declared, dict) and "verify" not in declared:
            # VERIFY is opt-in. Undeclared, it is reported as not run, which is never a pass.
            verify = {
                "status": "not-run",
                "reason": "validation.verify is not declared in .astack/project.toml",
            }
        else:
            commands = commands + validation_commands(root, "verify")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("Validation timeout must be 1 through 3600 seconds")
    if tier == "release":
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode or status.stdout.strip():
            raise RuntimeError("RELEASE validation requires a clean Git worktree")
    for command in commands:
        try:
            result = subprocess.run(
                command, cwd=root, check=False, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f"{tier.upper()} validation unavailable or timed out: {command[0]}"
            ) from exc
        if result.returncode:
            raise RuntimeError(
                f"{tier.upper()} validation failed ({result.returncode}): {command[0]}"
            )
    outcome: dict[str, Any] = {"status": "passed", "tier": tier, "commands": commands}
    if verify is not None:
        outcome["verify"] = verify
    return outcome
