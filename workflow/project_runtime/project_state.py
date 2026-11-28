from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    _DIGEST_POLICY,
    _READ_ONLY,
    BINARY_SUFFIXES,
    COMPONENT_PATTERN,
    MANIFEST_NAME,
    PRIORITY_ORDER,
    RAW_BASE_SHA_ENV,
    REPRODUCTION_ENGINES,
    WINDOWS_RESERVED_NAMES,
    WINDOWS_UNSAFE_PATH_CHARS,
    Any,
    Finding,
    Path,
    PureWindowsPath,
    ValidationSession,
    _ensure_audit_reports_ignored,
    _ensure_cache_ignored,
    _finding,
    _is_sensitive_filename,
    _load_digest_cache,
    _run,
    _safe_managed_path,
    _save_digest_cache,
    _scaffold_layout_applies,
    _science_applies,
    _sha256,
    _stream_contains_secret,
    asdict,
    canonical_checkout,
    data_root,
    json,
    load_diagnostic_summary,
    os,
    re,
    state_path,
    time,
    tomllib,
    yaml,
)
from .dependency_check import _lock_fingerprints, check_ignore_patterns, dependency_checks
from .output_contract import (
    _audit_output_contract_summary,
    _check_output_contract,
)
from .publication import (
    _check_publication,
    _publication_declaration,
)
from .reproduction import (
    _acquisition_command,
    _acquisition_staged,
    _dvc_pipeline_outputs,
    _dvc_reproduction_targets,
    _ensure_dvc_cache,
    _random_seed,
    _reproduction_command,
    _reproduction_config,
    _reproduction_engine,
    _reproduction_output_allowed,
    _reproduction_outputs,
    _valid_run_record,
    _verified_run_record,
)
from .sources import (
    _check_sources,
    _check_spatial,
    _is_local_only_source,
    _resolve_in_place_source,
)
from .validation import (
    _check_evidence,
    _check_optional_tools,
    _check_output_layout,
    _check_structure,
    _check_validation_config,
    _general_check_commands,
    _output_policy,
    _project_config,
    _scientific_verify_commands,
    general_stage_names,
    verify_stage_names,
)


def _load_manifest(root: Path, findings: list[Finding]) -> dict[str, Any] | None:
    path = root / MANIFEST_NAME
    if not path.exists():
        findings.append(
            _finding(
                "P1",
                "workflow",
                "managed-manifest",
                "Managed workflow manifest is missing, so workflow conformance cannot be verified.",
                MANIFEST_NAME,
            )
        )
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings.append(
            _finding("P1", "workflow", "managed-manifest", "Manifest is invalid.", str(exc))
        )
        return None
    if not isinstance(value, dict) or not isinstance(value.get("managed_components"), dict):
        findings.append(
            _finding(
                "P1",
                "workflow",
                "managed-manifest",
                "Manifest does not contain a managed-components mapping.",
                MANIFEST_NAME,
            )
        )
        return None
    catalog_path = root / ".astack/components.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        entries = catalog["components"]
        expected = {str(entry["id"]): str(entry["target"]) for entry in entries}
        expected["runtime.version"] = ".astack/version"
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "managed-manifest",
                "The installed component catalog is invalid.",
                str(exc),
            )
        )
        return None
    components = value["managed_components"]
    managed_files: dict[str, str] = {}
    try:
        for component_id, component in components.items():
            if not isinstance(component, dict):
                raise ValueError(f"invalid component record: {component_id}")
            relative = str(component.get("path", ""))
            checksum = str(component.get("sha256", ""))
            _safe_managed_path(root, relative)
            expected_path = expected.get(str(component_id))
            if expected_path != relative:
                raise ValueError(f"component path mismatch: {component_id}={relative}")
            managed_files[relative] = checksum
    except ValueError as exc:
        findings.append(
            _finding(
                "P0",
                "workflow",
                "managed-manifest",
                "Manifest contains an unsafe managed path.",
                str(exc),
                sensitive=True,
            )
        )
        return None
    component_ids = {str(component_id) for component_id in components}
    required_ids = set(expected)
    missing = sorted(required_ids - component_ids)
    extra = sorted(component_ids - required_ids)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"extra={','.join(extra)}")
        findings.append(
            _finding(
                "P1",
                "workflow",
                "managed-manifest",
                "Manifest does not match the installed canonical component catalog.",
                "; ".join(details),
            )
        )
        return None
    value["managed_files"] = managed_files
    return value


def _check_managed_files(root: Path, findings: list[Finding]) -> None:
    manifest = _load_manifest(root, findings)
    if manifest is None:
        return
    installed_version = str(manifest.get("workflow_version", ""))
    version_path = root / ".astack/version"
    declared_version = (
        version_path.read_text(encoding="utf-8").strip().lstrip("v")
        if version_path.exists()
        else ""
    )
    if not declared_version or declared_version != installed_version:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "workflow-version",
                "Declared and installed workflow versions do not match.",
                (
                    f"declared={declared_version or 'missing'}, "
                    f"installed={installed_version or 'missing'}"
                ),
            )
        )
    managed = manifest["managed_files"]
    for relative, expected in sorted(managed.items()):
        path = _safe_managed_path(root, str(relative))
        if not path.exists():
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "managed-file",
                    "Managed workflow file is missing.",
                    str(relative),
                )
            )
        elif _sha256(path) != str(expected):
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "managed-file",
                    "Managed workflow file differs from its installed checksum.",
                    str(relative),
                )
            )


def _git_paths(root: Path, *args: str) -> list[str]:
    result = _run(["git", *args], cwd=root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return [line for line in result.stdout.splitlines() if line]


def _raw_comparison_base(root: Path) -> str:
    candidates: list[str] = []
    github_base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if github_base:
        candidates.extend([f"refs/remotes/origin/{github_base}", f"refs/heads/{github_base}"])
    remote_head = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd=root
    )
    if remote_head.returncode == 0 and remote_head.stdout.strip():
        candidates.append(remote_head.stdout.strip())
    candidates.extend(
        [
            "refs/remotes/origin/main",
            "refs/heads/main",
            "refs/remotes/origin/master",
            "refs/heads/master",
        ]
    )
    for candidate in dict.fromkeys(candidates):
        result = _run(["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"], cwd=root)
        if result.returncode == 0:
            return candidate
    raise RuntimeError("could not resolve the default or CI base branch")


def _raw_comparison_start(root: Path) -> str:
    pushed_from = os.environ.get(RAW_BASE_SHA_ENV, "").strip()
    if pushed_from:
        if not re.fullmatch(r"[0-9a-f]{40}", pushed_from):
            raise RuntimeError(f"{RAW_BASE_SHA_ENV} must be a full lowercase Git SHA")
        if pushed_from == "0" * 40:
            raise RuntimeError(
                f"{RAW_BASE_SHA_ENV} is the all-zero initial-push SHA, so no prior tree exists"
            )
        verified = _run(["git", "rev-parse", "--verify", f"{pushed_from}^{{commit}}"], cwd=root)
        if verified.returncode != 0 or verified.stdout.strip() != pushed_from:
            detail = verified.stderr.strip() or "git rev-parse did not return the requested SHA"
            raise RuntimeError(
                f"{RAW_BASE_SHA_ENV} commit is unavailable in the local clone: {detail}"
            )
        return pushed_from
    base = _raw_comparison_base(root)
    merge_base = _run(["git", "merge-base", "HEAD", base], cwd=root)
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise RuntimeError(
            merge_base.stderr.strip() or f"could not resolve merge-base between HEAD and {base}"
        )
    return merge_base.stdout.strip()


def _committed_raw_changes(root: Path) -> list[str]:
    comparison_start = _raw_comparison_start(root)
    return _git_paths(
        root,
        "diff",
        "--name-only",
        "--diff-filter=DMRT",
        comparison_start,
        "HEAD",
        "--",
        "data/raw",
    )


def _check_git_and_raw(root: Path, findings: list[Finding]) -> None:
    if _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root).returncode != 0:
        findings.append(
            _finding("P1", "reproducibility", "git", "Project is not in a Git worktree.", ".")
        )
        return
    audit_ignore = _run(
        ["git", "check-ignore", "--quiet", "reports/audits/sensitive-audit.md"], cwd=root
    )
    if audit_ignore.returncode == 1:
        findings.append(
            _finding(
                "P1",
                "security",
                "audit-report-ignore",
                "Local audit reports are not ignored and could be committed accidentally.",
                "add /reports/audits/ to .gitignore",
            )
        )
    elif audit_ignore.returncode != 0:
        findings.append(
            _finding(
                "P1",
                "security",
                "audit-report-ignore",
                "Could not verify that sensitive local audit reports are ignored.",
                audit_ignore.stderr.strip() or "git check-ignore failed",
            )
        )
    worktree = _run(["git", "status", "--porcelain"], cwd=root)
    if worktree.returncode != 0:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "worktree-status",
                "Could not verify whether the worktree is clean.",
                worktree.stderr.strip() or "git status failed",
            )
        )
    elif worktree.stdout.strip():
        findings.append(
            _finding(
                "P1",
                "workflow",
                "worktree-cleanliness",
                "Worktree has tracked or untracked changes and is not ready to ship or publish.",
                "commit or remove intended changes before shipping",
            )
        )
    try:
        raw_changes = set(
            _git_paths(root, "diff", "--name-only", "--diff-filter=DMRT", "--", "data/raw")
        )
        raw_changes.update(
            _git_paths(
                root, "diff", "--cached", "--name-only", "--diff-filter=DMRT", "--", "data/raw"
            )
        )
        raw_changes.update(_committed_raw_changes(root))
    except RuntimeError as exc:
        findings.append(
            _finding(
                "P1",
                "data-safety",
                "raw-immutability",
                "Git raw-data checks were unavailable.",
                str(exc),
            )
        )
        return
    if raw_changes:
        findings.append(
            _finding(
                "P0",
                "data-safety",
                "raw-immutability",
                "Tracked source snapshots under data/raw have changed.",
                ", ".join(sorted(raw_changes)),
                sensitive=True,
            )
        )


def _candidate_files(root: Path, findings: list[Finding]) -> list[Path]:
    try:
        paths = _git_paths(root, "ls-files", "--cached", "--others", "--exclude-standard")
    except RuntimeError as exc:
        findings.append(
            _finding(
                "P1",
                "security",
                "commit-candidate-scan",
                "Commit-candidate secret and size checks were unavailable.",
                str(exc),
            )
        )
        return []
    return [root / path for path in paths if (root / path).is_file()]


def _check_secrets_and_size(root: Path, findings: list[Finding]) -> None:
    dvc_enabled = (root / ".dvc").exists()
    for path in _candidate_files(root, findings):
        relative = path.relative_to(root)
        if _is_sensitive_filename(path.name):
            findings.append(
                _finding(
                    "P0",
                    "security",
                    "sensitive-file",
                    "A likely sensitive configuration file is a commit candidate.",
                    str(relative),
                    sensitive=True,
                )
            )
        size = path.stat().st_size
        if size > 10 * 1024 * 1024 and not path.name.endswith(".dvc"):
            findings.append(
                _finding(
                    "P1",
                    "data-safety",
                    "large-file",
                    "A file larger than 10 MiB is not represented by a DVC pointer.",
                    f"{relative} ({size} bytes, dvc_configured={dvc_enabled})",
                    sensitive=True,
                )
            )
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            contains_secret = _stream_contains_secret(path)
        except (UnicodeDecodeError, OSError):
            if size > 2 * 1024 * 1024:
                findings.append(
                    _finding(
                        "P1",
                        "security",
                        "secret-scan-unavailable",
                        "A large commit-candidate file could not be scanned as UTF-8 text.",
                        str(relative),
                        sensitive=True,
                    )
                )
            continue
        if contains_secret:
            findings.append(
                _finding(
                    "P0",
                    "security",
                    "secret-pattern",
                    "A likely credential pattern was found. The value is intentionally omitted.",
                    str(relative),
                    sensitive=True,
                )
            )


def _safe_data_relative(value: object, *, destination: bool = False) -> Path | None:
    text = str(value or "")
    path = Path(text)
    windows_path = PureWindowsPath(text)
    if (
        not text
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
        or (destination and path.as_posix().rstrip("/") == "data")
    ):
        return None
    return path


def _safe_private_history_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if (
        "\\" in value
        or "//" in value
        or value.endswith("/")
        or any(char in WINDOWS_UNSAFE_PATH_CHARS or ord(char) < 32 for char in value)
    ):
        return None
    parts = value.split("/")
    if any(
        part in {"", ".", ".."}
        or part.endswith((".", " "))
        or part.rstrip(". ").split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        for part in parts
    ):
        return None
    path = Path(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or path.as_posix() != value
    ):
        return None
    return value


def _private_history_policy(config: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    privacy = config.get("privacy")
    if privacy is None:
        return [], [], []
    if not isinstance(privacy, dict):
        return [], [], ["privacy must be a table"]
    supported = {"private_history_paths", "private_history_safe_paths"}
    errors = [f"unsupported privacy setting {key!r}" for key in privacy if key not in supported]
    values: dict[str, list[str]] = {}
    for key in sorted(supported):
        raw = privacy.get(key, [])
        if not isinstance(raw, list):
            errors.append(f"privacy.{key} must be a list of project-relative paths")
            continue
        normalized: list[str] = []
        for index, value in enumerate(raw):
            path = _safe_private_history_path(value)
            if path is None:
                errors.append(f"privacy.{key}[{index}] is not a safe project-relative path")
            elif path in normalized:
                errors.append(f"privacy.{key} contains a duplicate path: {path}")
            else:
                normalized.append(path)
        values[key] = normalized
    private = values.get("private_history_paths", [])
    safe = values.get("private_history_safe_paths", [])
    for path in safe:
        if not any(path == root or path.startswith(root + "/") for root in private):
            errors.append(
                f"privacy.private_history_safe_paths contains a path outside private roots: {path}"
            )
    return private, safe, errors


def _committed_history_paths(root: Path) -> set[str]:
    commits = _run(["git", "rev-list", "--all"], cwd=root)
    if commits.returncode != 0:
        raise RuntimeError(commits.stderr.strip() or "could not enumerate Git history")
    commit_ids = [commit for commit in commits.stdout.splitlines() if commit]
    if not commit_ids:
        raise RuntimeError("could not enumerate any Git history")
    paths: set[str] = set()
    for commit in commit_ids:
        tree = _run(["git", "ls-tree", "-r", "-z", "--name-only", commit], cwd=root)
        if tree.returncode != 0:
            raise RuntimeError(tree.stderr.strip() or f"could not inspect Git commit {commit}")
        paths.update(path for path in tree.stdout.split("\0") if path)
    return paths


def _check_private_history_paths(root: Path, findings: list[Finding]) -> None:
    try:
        config = _project_config(root)
        private, safe, errors = _private_history_policy(config)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        findings.append(
            _finding(
                "P1",
                "security",
                "private-history-config",
                "Committed-history privacy configuration is invalid.",
                str(exc),
                sensitive=True,
            )
        )
        return
    if errors:
        findings.append(
            _finding(
                "P1",
                "security",
                "private-history-config",
                "Committed-history privacy configuration is invalid.",
                "; ".join(errors),
                sensitive=True,
            )
        )
        return
    if not private:
        return
    try:
        history_paths = _committed_history_paths(root)
    except RuntimeError as exc:
        findings.append(
            _finding(
                "P1",
                "security",
                "private-history",
                "Committed-history privacy checks were unavailable.",
                str(exc),
                sensitive=True,
            )
        )
        return
    violations = sorted(
        path
        for path in history_paths
        if any(path == root_path or path.startswith(root_path + "/") for root_path in private)
        and path not in safe
    )
    if violations:
        findings.append(
            _finding(
                "P0",
                "security",
                "private-history-path",
                "Committed history contains a path declared private by project policy.",
                ", ".join(violations),
                sensitive=True,
            )
        )


def _check_data_layout(root: Path, data: object, findings: list[Finding]) -> None:
    if not isinstance(data, dict):
        findings.append(
            _finding(
                "P1",
                "data-safety",
                "data-layout",
                "Data configuration must be a table.",
                ".astack/project.toml",
            )
        )
        return
    layout = data.get("layout")
    if layout not in {"standard", "legacy"}:
        findings.append(
            _finding(
                "P1",
                "data-safety",
                "data-layout",
                "Data layout must be standard or an explicit legacy migration exception.",
                str(layout),
            )
        )
        return
    try:
        root_path = data_root(root)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        findings.append(
            _finding(
                "P1",
                "data-safety",
                "data-layout",
                "Local data-root configuration is invalid.",
                str(exc),
                sensitive=True,
            )
        )
        return
    if layout == "standard":
        share_raw = data.get("share_raw", True)
        if not isinstance(share_raw, bool):
            findings.append(
                _finding(
                    "P1",
                    "data-safety",
                    "data-layout",
                    "Standard shared-raw configuration must be true or false.",
                    str(share_raw),
                    sensitive=True,
                )
            )
        return
    mappings = data.get("legacy_shared_inputs", [])
    if not isinstance(mappings, list):
        mappings = [None]
    invalid: list[str] = []
    for index, mapping in enumerate(mappings):
        if isinstance(mapping, str):
            destination = _safe_data_relative(mapping, destination=True)
            source = _safe_data_relative(mapping)
        elif isinstance(mapping, dict):
            destination = _safe_data_relative(mapping.get("path"), destination=True)
            source = _safe_data_relative(mapping.get("source", mapping.get("path")))
        else:
            destination = source = None
        if destination is None or source is None or not (root_path / source).exists():
            invalid.append(str(index))
    if invalid:
        findings.append(
            _finding(
                "P1",
                "data-safety",
                "legacy-data-layout",
                "Legacy shared-input mappings are unsafe or unavailable.",
                f"mapping_indexes={','.join(invalid)}",
            )
        )


def _check_dependency_checks(root: Path, findings: list[Finding]) -> None:
    """Report what the last checked run of each stage read or wrote without declaring it."""
    checks = dependency_checks(root)
    unchecked = sorted(set(_lock_fingerprints(root)) - set(checks))
    if unchecked:
        findings.append(
            _finding(
                "P2",
                "reproducibility",
                "unchecked-stage",
                "These DVC stages last ran outside `just reproduce`, so what they read and wrote "
                "was not checked. Run `just reproduce` to check them.",
                ", ".join(unchecked[:10]),
            )
        )
    for name, check in sorted(checks.items()):
        writes = check.get("undeclared_writes") or []
        reads = check.get("undeclared_reads") or []
        if writes:
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "undeclared-write",
                    "A DVC stage wrote files its dvc.yaml outs do not declare, so later stages "
                    "that read them cannot be traced. Declare them as outs.",
                    f"{name}: {', '.join(writes[:5])}",
                )
            )
        if reads:
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "undeclared-read",
                    "A DVC stage read project files its dvc.yaml deps do not declare, so a change "
                    "to them would not make its outputs outdated. Declare them as deps.",
                    f"{name}: {', '.join(reads[:5])}",
                )
            )
        if check.get("inputs_locked") is False:
            findings.append(
                _finding(
                    "P2",
                    "reproducibility",
                    "inputs-not-locked",
                    "Some files could not be locked or checked safely while this stage ran, so "
                    "reads of them by compiled libraries or other programs, or writes to them, "
                    "may not have been caught.",
                    f"{name}: {', '.join((check.get('lock_gaps') or [])[:5])}",
                )
            )
        if check.get("untraced"):
            findings.append(
                _finding(
                    "P2",
                    "reproducibility",
                    "untraced-stage",
                    "Part of a DVC stage's reads could not be checked. Its writes still were.",
                    f"{name}: {check['untraced']}",
                )
            )


def _has_markdown_report(root: Path) -> bool:
    """Whether reports/ holds a report, not counting figures, tables, audits or READMEs."""
    reports = root / "reports"
    if not reports.is_dir():
        return False
    return any(
        path.is_file()
        and path.name != "README.md"
        and not {"figures", "tables", "audits"} & set(path.relative_to(reports).parts)
        for pattern in ("*.md", "*.qmd")
        for path in reports.rglob(pattern)
    )


def _check_project_state(
    root: Path,
    findings: list[Finding],
    *,
    require_durable_evidence: bool = False,
    output_scope: str | None = None,
) -> None:
    try:
        config = _project_config(root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        findings.append(
            _finding("P1", "workflow", "project-state", "Project state is invalid.", str(exc))
        )
        return
    if config.get("schema_version") != 1:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "project-state",
                "Project state schema version is missing or unsupported.",
                ".astack/project.toml",
            )
        )
        return
    if not _science_applies(root):
        _check_validation_config(config, findings)
        return
    analysis = config.get("analysis", {})
    stage = analysis.get("stage") if isinstance(analysis, dict) else None
    if stage not in {"exploratory", "material"}:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "analysis-stage",
                "Analysis stage must be exploratory or material.",
                str(stage),
            )
        )
        return
    _check_validation_config(config, findings)
    _check_data_layout(root, config.get("data", {}), findings)
    outputs = config.get("outputs")
    component_layout, declared_components = _output_policy(config)
    if outputs is not None and (
        not isinstance(outputs, dict)
        or outputs.get("layout") != "component"
        or not isinstance(outputs.get("components"), list)
        or any(
            not isinstance(component, str) or not COMPONENT_PATTERN.fullmatch(component)
            for component in outputs.get("components", [])
        )
        or len(declared_components) != len(outputs.get("components", []))
    ):
        findings.append(
            _finding(
                "P1",
                "workflow",
                "output-layout",
                (
                    "Output configuration requires component layout and unique lowercase "
                    "component slugs."
                ),
                ".astack/project.toml",
            )
        )
    _check_output_layout(root, config, findings)
    # One finding per registry blocker, including unknown evidence and stale confirmation.
    _check_output_contract(root, findings, output_scope)
    if stage != "material":
        return
    if not _has_markdown_report(root):
        findings.append(
            _finding(
                "P1",
                "scientific-output",
                "report-missing",
                "Material analysis needs a Markdown report under reports/ that cites its "
                "evidence. Write one with the ds-report-generation skill, then run "
                "`just report-check`.",
                "reports/",
            )
        )
    reproduction = _reproduction_config(config)
    engine = _reproduction_engine(root, config)
    reproduction_component = reproduction.get("component")
    if engine not in REPRODUCTION_ENGINES:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "reproduction-engine",
                "Reproduction engine must be `command` or `dvc`.",
                str(engine),
            )
        )
    elif engine == "command" and _reproduction_command(root, config) is None:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "reproduction-command",
                "Command-based reproduction requires a configured command array.",
                ".astack/project.toml",
            )
        )
    elif engine == "dvc":
        if "dvc" not in config.get("tools", []):
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "dvc-capability",
                    "DVC reproduction requires the dvc capability to be declared.",
                    ".astack/project.toml",
                )
            )
        pipeline_outputs, pipeline_errors = _dvc_pipeline_outputs(root)
        for error in pipeline_errors:
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "dvc-pipeline",
                    "DVC reproduction requires a valid non-empty dvc.yaml pipeline.",
                    error,
                )
            )
        for output in pipeline_outputs:
            if not _reproduction_output_allowed(root, output, config):
                findings.append(
                    _finding(
                        "P1",
                        "data-safety",
                        "dvc-output-path",
                        (
                            "DVC pipeline outputs must use an allowed component output path and "
                            "must not overwrite retained raw inputs."
                        ),
                        output,
                    )
                )
        _check_dependency_checks(root, findings)
        try:
            check_ignore_patterns(config)
        except RuntimeError as exc:
            findings.append(
                _finding("P1", "reproducibility", "check-ignore", str(exc), ".astack/project.toml")
            )
        if _dvc_reproduction_targets(config) is None:
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "dvc-targets",
                    (
                        "DVC reproduction targets must be a list of non-empty strings that do not "
                        "begin with `-`."
                    ),
                    ".astack/project.toml",
                )
            )
    elif "dvc" in config.get("tools", []) and "engine" not in reproduction:
        findings.append(
            _finding(
                "P2",
                "reproducibility",
                "legacy-dvc-command",
                (
                    "DVC capability is using legacy command reproduction. Declare "
                    "reproduction.engine = 'command' or migrate to dvc.yaml."
                ),
                ".astack/project.toml",
            )
        )
    if reproduction_component is not None and (
        not isinstance(reproduction_component, str)
        or not COMPONENT_PATTERN.fullmatch(reproduction_component)
    ):
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "reproduction-component",
                "Material analysis requires a safe lowercase reproduction component slug.",
                str(reproduction_component),
            )
        )
    elif component_layout and reproduction_component not in declared_components:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "reproduction-component",
                "Material analysis requires a declared reproduction.component.",
                str(reproduction_component),
            )
        )
    try:
        _random_seed(config)
    except RuntimeError as exc:
        findings.append(
            _finding("P1", "reproducibility", "random-seed", str(exc), ".astack/project.toml")
        )
    justfile = root / "justfile"
    just_text = justfile.read_text(encoding="utf-8") if justfile.exists() else ""
    if not re.search(r"(?m)^reproduce\b[^:\n=]*:(?!=)", just_text):
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "reproduction-interface",
                "Material analysis requires the canonical `just reproduce` interface.",
                "justfile",
            )
        )
    routine_records = list((root / ".cache/runs").glob("**/*.json"))
    run_records = list((root / "reports/qa/runs").glob("*.json"))
    if isinstance(reproduction_component, str) and COMPONENT_PATTERN.fullmatch(
        reproduction_component
    ):
        try:
            evidence_runs = _safe_managed_path(root, f"evidence/{reproduction_component}/runs")
        except ValueError as exc:
            findings.append(
                _finding(
                    "P1",
                    "data-safety",
                    "reproduction-record-path",
                    "Durable reproduction evidence must remain inside the project.",
                    str(exc),
                )
            )
        else:
            run_records.extend(evidence_runs.glob("*.json"))
    _, output_errors = _reproduction_outputs(root, config)
    for error in output_errors:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "reproduction-outputs",
                (
                    "Material analysis must declare the outputs a reproduction produces, so a "
                    "rerun can be compared with the promoted reference."
                ),
                error,
            )
        )
    record_paths = run_records if require_durable_evidence else routine_records + run_records
    if any(_valid_run_record(root, path, config) for path in record_paths) and not any(
        _verified_run_record(root, path, config) for path in record_paths
    ):
        findings.append(
            _finding(
                "P1" if require_durable_evidence else "P2",
                "reproducibility",
                "reproduction-unverified",
                (
                    "The pipeline ran, but no promoted rerun has matched a promoted reference "
                    "record, so reproduction is not verified."
                ),
                "run `just reproduce --promote` again after committing the reference record",
            )
        )
    if not any(_valid_run_record(root, path, config) for path in record_paths):
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "run-provenance",
                (
                    "Material analysis has no valid durable run record for the current committed "
                    "state."
                    if require_durable_evidence
                    else (
                        "Material analysis has no valid run record for the current committed state."
                    )
                ),
                "evidence/<component>/runs/"
                if require_durable_evidence
                else ".cache/runs/ or evidence/",
            )
        )
    acquisition = config.get("acquisition")
    if acquisition is not None and _acquisition_command(config) is None:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "acquisition-command",
                "Configured acquisition requires a non-empty command array.",
                ".astack/project.toml",
            )
        )
    if acquisition is not None and _acquisition_staged(config) is None:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "acquisition-staged",
                "In-place acquisition was removed. Remove [acquisition].staged and write "
                "new inputs to $ASTACK_ACQUISITION_DIR "
                "(docs/migrations/staged-acquisition.md).",
                ".astack/project.toml",
            )
        )
    project_tests = [
        path
        for path in (root / "tests").glob("test_*.py")
        if path.name not in {"test_smoke.py", "test_sources_schema.py"}
    ]
    if not project_tests:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "analytical-tests",
                "Material analysis requires project-specific analytical tests.",
                "tests/",
            )
        )


def _optimization_observations(root: Path) -> dict[str, Any]:
    try:
        state_root = state_path(canonical_checkout(root), "diagnostics.json").parent
        return dict(load_diagnostic_summary(state_root))
    except (ImportError, OSError, RuntimeError, ValueError):
        return {"evidence_state": "unavailable", "reason": "local_state_unavailable"}


def _apply_ci_limits(root: Path, findings: list[Finding]) -> list[str]:
    """Restrict CI's claim, never manufacture source or reproduction verification."""
    config = _project_config(root)
    if config.get("ci") != {"mode": "local-only-sources"}:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "ci-mode",
                "CI limits require explicit ci.mode = local-only-sources.",
                ".astack/project.toml",
            )
        )
        return []
    sources_path = root / "data/sources.yml"
    if yaml is None:
        return []
    try:
        data = yaml.safe_load(sources_path.read_text())
    except (OSError, ValueError, yaml.YAMLError):
        return []  # Existing source validation retains the actual parse failure.
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    if not isinstance(sources, dict):
        return []
    is_data_science = config.get("profile") == "data-science"
    local = {
        str(key)
        for key, value in sources.items()
        if isinstance(value, dict)
        and _is_local_only_source(root, value, infer_defaults=is_data_science)
    }
    # Data Science projects default to limited CI from scaffold time, so exploratory projects
    # may have no local-only sources yet. Material DS and General projects still require one.
    if not local and (not is_data_science or _analysis_stage(root) == "material"):
        findings.append(
            _finding(
                "P1",
                "provenance",
                "ci-source-declaration",
                (
                    "Limited CI requires at least one local-only source (explicit `availability: "
                    "local_only`, gitignored `data/raw/`, or `storage: in_place`). Switch CI to "
                    "`just ship-ready` if every source is available in CI."
                    if is_data_science
                    else "Limited CI requires at least one explicitly local_only source."
                ),
                "data/sources.yml",
            )
        )
        return []
    undeclared_in_place = (
        {
            key
            for key in local
            if isinstance(sources.get(key), dict)
            and sources[key].get("storage") == "in_place"
            and _resolve_in_place_source(root, sources[key].get("path"))[2]
        }
        if is_data_science
        else set()
    )
    excluded = []
    for index, finding in enumerate(findings):
        unconfigured_external_root = (
            finding.check == "source-path" and finding.evidence in undeclared_in_place
        )
        if (
            (finding.check == "source-missing" and finding.evidence in local)
            or unconfigured_external_root
            or finding.check
            in {
                "run-provenance",
                "reproduction-unverified",
            }
        ):
            excluded.append(finding.check + ":" + finding.evidence)
            findings[index] = _finding(
                "P2",
                finding.area,
                finding.check + "-not-verified-in-ci",
                "Not verified in limited CI. "
                "Strict local source/reproduction verification is still required.",
                finding.evidence,
                sensitive=finding.sensitive,
            )
    return excluded


def _analysis_stage(root: Path) -> str | None:
    try:
        analysis = _project_config(root).get("analysis", {})
    except (OSError, tomllib.TOMLDecodeError):
        return None
    stage = analysis.get("stage") if isinstance(analysis, dict) else None
    return stage if isinstance(stage, str) else None


def audit(
    root: Path,
    *,
    run_tools: bool = False,
    strict: bool = False,
    ci: bool = False,
    output_scope: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Audit the project. With read_only, nothing in the project is written, not even markers."""
    if not read_only:
        _ensure_cache_ignored(root)
        _ensure_audit_reports_ignored(root)
    _load_digest_cache(root)
    _DIGEST_POLICY["bypass"] = strict
    previous = _READ_ONLY["on"]
    _READ_ONLY["on"] = previous or read_only
    try:
        return _audit(root, run_tools=run_tools, strict=strict, ci=ci, output_scope=output_scope)
    finally:
        _READ_ONLY["on"] = previous
        _DIGEST_POLICY["bypass"] = False
        _save_digest_cache(root)


def _audit(
    root: Path,
    *,
    run_tools: bool,
    strict: bool,
    ci: bool,
    output_scope: str | None,
) -> dict[str, Any]:
    findings: list[Finding] = []
    verify_count = len(_scientific_verify_commands(root, []) or [])
    planned = ["check", "integration", *verify_stage_names(verify_count), "strict-audit"]
    if not _scaffold_layout_applies(root):
        # A General project runs its own declared commands, not `just check` and `integration`.
        count = len(_general_check_commands(root, [])) or 1
        planned = [*general_stage_names(count), "strict-audit"]
    session = (
        ValidationSession(
            root,
            name="limited-ci" if ci else "ship-ready",
            planned_stages=planned,
            runner=_run,
        )
        if run_tools
        else None
    )
    if session is not None:
        _check_optional_tools(
            root,
            findings,
            run_tools,
            ensure_dvc_cache=_ensure_dvc_cache,
            session=session,
            **({"ci": True} if ci else {}),
        )
        strict_started = time.monotonic()
    else:
        strict_started = time.monotonic()
    _check_structure(root, findings)
    _check_project_state(root, findings, require_durable_evidence=strict, output_scope=output_scope)
    _check_managed_files(root, findings)
    _check_git_and_raw(root, findings)
    _check_private_history_paths(root, findings)
    _check_secrets_and_size(root, findings)
    science = _science_applies(root)
    if science:
        _check_sources(root, findings)
    _check_spatial(root, findings)
    _check_publication(root, findings)
    if science:
        _check_evidence(root, findings)
    if session is None:
        _check_optional_tools(
            root,
            findings,
            run_tools,
            ensure_dvc_cache=_ensure_dvc_cache,
            **({"ci": True} if ci else {}),
        )
    # CI limits exist for sources CI cannot read. Without science checks there are none.
    excluded = _apply_ci_limits(root, findings) if ci and science else []
    findings.sort(key=lambda item: (PRIORITY_ORDER[item.priority], item.area, item.check))
    blocking = any(item.priority in {"P0", "P1"} for item in findings)
    by_area = {
        area: [item for item in findings if item.area == area]
        for area in {item.area for item in findings}
    }
    workflow_blocking = any(item.priority in {"P0", "P1"} for item in by_area.get("workflow", []))
    reproducibility_blocking = any(
        item.priority in {"P0", "P1"}
        for area in {"reproducibility", "provenance", "data-safety", "security"}
        for item in by_area.get(area, [])
    )
    reproducibility_not_assessed = any(
        item.check.endswith("-not-run") or item.check in {"ci", "reproduction-unverified"}
        for item in findings
    )
    software_findings = [item for item in findings if item.check.startswith("software-")]
    software_verdict = "pass"
    if any(item.priority in {"P0", "P1"} for item in software_findings):
        software_verdict = "fail"
    elif not run_tools or any(item.check == "software-not-run" for item in software_findings):
        software_verdict = "not-assessed"
    analysis_stage = _analysis_stage(root)
    not_assessed = [
        "scientific design and domain validity require interpretive review",
        "full data reproduction is not implied by repository checks",
    ]
    # Passing software checks is not reproduction evidence. Only a material analysis with a
    # valid run record (findings above report its absence) can support a pass.
    reproducibility_verdict = "pass"
    if reproducibility_blocking:
        reproducibility_verdict = "fail"
    elif reproducibility_not_assessed:
        reproducibility_verdict = "not-assessed"
    elif not science:
        reproducibility_verdict = "not-assessed"
        not_assessed.append(
            "science checks: this General project declares no scientific capability, "
            "outputs, analysis stage, or data/sources.yml"
        )
    elif analysis_stage != "material":
        reproducibility_verdict = "not-assessed"
        not_assessed.append(
            "computational reproducibility: no reproduction evidence exists while "
            "analysis.stage is not material"
        )
    release_verdict = "needs-interpretive-review"
    if blocking:
        release_verdict = "fail"
    elif reproducibility_verdict != "pass":
        release_verdict = "not-assessed"
    result: dict[str, Any] = {
        "schema_version": 1,
        "root": str(root),
        "analysis_stage": analysis_stage,
        "verdicts": {
            "workflow_conformance": "fail" if workflow_blocking else "pass",
            "software_checks": software_verdict,
            "computational_reproducibility": reproducibility_verdict,
            "scientific_credibility": "needs-interpretive-review",
            "release_readiness": release_verdict,
        },
        "counts": {
            priority: sum(item.priority == priority for item in findings)
            for priority in PRIORITY_ORDER
        },
        "findings": [asdict(item) for item in findings],
        "output_contract": _audit_output_contract_summary(root, output_scope),
        "optimization_observations": _optimization_observations(root),
        "not_assessed": [
            *not_assessed,
            *(
                []
                if _publication_declaration(root) is not None
                else ["journal and archive handoff: [publication] is not declared"]
            ),
        ],
    }
    if ci:
        result["verdicts"]["computational_reproducibility"] = (
            "fail" if reproducibility_blocking else "not-assessed"
        )
        result["verdicts"]["release_readiness"] = "fail" if blocking else "not-assessed"
        result["ci_validation"] = {
            "scope": "engineering-and-metadata-only",
            "status": "pass"
            if run_tools and not blocking
            else "fail"
            if blocking
            else "not-assessed",
            "excluded_checks": excluded,
            "source_identity": "not-verified-in-ci",
            "reproduction": "not-verified-in-ci",
            "requires_strict_local_ship_ready": True,
        }
    if session is not None:
        session.record_stage(
            "strict-audit",
            started=strict_started,
            status="passed" if not blocking else "failed",
        )
        result["validation_session"] = session.finish(status="passed" if not blocking else "failed")
    return result
