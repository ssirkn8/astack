from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    COMPONENT_LIFECYCLES,
    COMPONENT_PATTERN,
    MACHINE_OUTPUT_SUFFIXES,
    SCIENCE_CAPABILITIES,
    VALIDATION_TIERS,
    Any,
    Callable,
    Finding,
    Path,
    PureWindowsPath,
    _finding,
    _run,
    _scaffold_layout_applies,
    _science_applies,
    _viewer_notebook,
    project_config,
    re,
    shlex,
    tomllib,
    validation_commands,
    yaml,
)
from .sources import (
    _has_local_only_sources,
    _spatial_validators,
)


def _ship_ready_runs_strict_audit(text: str, *, ci: bool = False) -> bool:
    lines = text.splitlines()
    targets = [
        index
        for index, line in enumerate(lines)
        # ship-ready may take one optional analysis key (see the project justfile).
        if re.match(r"^" + ("ci-check" if ci else r'ship-ready(\s+analysis="")?') + r"\s*:", line)
    ]
    if len(targets) != 1:
        return False
    takes_analysis = not ci and "analysis" in lines[targets[0]].partition(":")[0]
    previous = targets[0] - 1
    while previous >= 0 and not lines[previous].strip():
        previous -= 1
    if previous >= 0 and lines[previous].lstrip().startswith("["):
        return False  # Script/interpreter attributes are not a plain audit recipe.
    index = targets[0] + 1
    recipe_lines: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line and not line[0].isspace():
            break
        if line.lstrip().startswith("#!"):
            return False
        if line.strip() and not line.lstrip().startswith("#"):
            recipe_lines.append(line.strip())
        index += 1
    if len(recipe_lines) != 1:
        return False  # Additional script commands can mask audit failures.
    expected = [
        "uv",
        "run",
        "--locked",
        "python",
        ".astack/workflow.py",
        "audit",
        "--format",
        "text",
        "--run-tools",
        "--strict",
    ]
    if ci:
        expected.append("--ci")
    # A recipe that takes an analysis must pass it, through just's quote() and nothing else:
    # extra shell quotes around the interpolation would undo quote() and let the key run code.
    interpolation = "{{quote(analysis)}}"
    for line in recipe_lines:
        if re.search(r"\|\||&&|[|;&`<>]|\$\(", line) or "#" in line:
            continue
        if takes_analysis:
            raw = line.split()
            if raw[-2:] != ["--analysis", interpolation] or line.count("{{") != 1:
                continue
            line = " ".join(raw[:-2])
        elif "{{" in line:
            continue
        try:
            tokens = shlex.split(line, comments=False)
        except ValueError:
            continue
        if tokens == expected:
            return True
    return False


def _workflow_steps(text: str) -> tuple[set[str], list[tuple[str, bool]]] | None:
    if yaml is None:
        return None
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(value, dict):
        return None
    on_value = value.get("on", value.get(True))
    if isinstance(on_value, str):
        triggers = {on_value}
    elif isinstance(on_value, list):
        triggers = {str(trigger) for trigger in on_value}
    elif isinstance(on_value, dict):
        triggers = {str(trigger) for trigger in on_value}
    else:
        triggers = set()
    relevant = triggers & {"pull_request", "pull_request_target", "push"}
    jobs = value.get("jobs")
    if not isinstance(jobs, dict):
        return relevant, []
    run_values = []
    for job in jobs.values():
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            continue
        job_continue_on_error = job.get("continue-on-error")
        job_active = "if" not in job and (
            job_continue_on_error is None
            or job_continue_on_error is False
            or (
                isinstance(job_continue_on_error, str)
                and job_continue_on_error.strip().casefold() == "false"
            )
        )
        for step in job["steps"]:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                continue_on_error = step.get("continue-on-error")
                active = (
                    job_active
                    and "if" not in step
                    and (
                        continue_on_error is None
                        or continue_on_error is False
                        or (
                            isinstance(continue_on_error, str)
                            and continue_on_error.strip().casefold() == "false"
                        )
                    )
                )
                run_values.append((step["run"].strip(), active))
    return relevant, run_values


def _check_structure(root: Path, findings: list[Finding]) -> None:
    scaffold = _scaffold_layout_applies(root)
    required = ["AGENTS.md"]
    if scaffold:
        required += ["README.md", "pyproject.toml", "justfile"]
    if _science_applies(root):
        required.append("data/sources.yml")
    for relative in required:
        if not (root / relative).exists():
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "required-file",
                    "Required baseline file is missing.",
                    relative,
                )
            )
    if not scaffold:
        # A General project keeps its own build, lockfile, and CI. Only the layout that
        # `astack new` creates promises `uv.lock`, a `ship-ready` recipe, and CI that runs it.
        return
    if not (root / "uv.lock").exists():
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "dependency-lock",
                "Dependency lockfile is missing. Run `uv lock` and commit it.",
                "uv.lock",
            )
        )
    pyproject = root / "pyproject.toml"
    if pyproject.exists() and "[build-system]" not in pyproject.read_text(encoding="utf-8"):
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "package-install",
                "Python package build configuration is missing.",
                "pyproject.toml",
            )
        )
    justfile = root / "justfile"
    if justfile.exists():
        text = justfile.read_text(encoding="utf-8")
        if not _ship_ready_runs_strict_audit(text) or "|| true" in text:
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "ship-ready",
                    (
                        "Shipping must directly run the managed audit with --run-tools and "
                        "--strict without suppressing failures."
                    ),
                    "justfile",
                )
            )
    workflow_dir = root / ".github" / "workflows"
    workflows = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))
    if not workflows:
        findings.append(
            _finding(
                "P2",
                "reproducibility",
                "ci",
                "No GitHub Actions validation workflow was found.",
                ".github/workflows",
            )
        )
    else:
        validation_workflows = []
        invalid_validation_workflows = []
        unparseable_workflows = []
        ship_ready_only_workflows = []
        ci_declaration: Any = None
        is_data_science = False
        ci_config_readable = False
        try:
            parsed_config = _project_config(root)
            ci_declaration = parsed_config.get("ci")
            is_data_science = parsed_config.get("profile") == "data-science"
            ci_config_readable = True
        except (OSError, ValueError):
            pass  # Project configuration validation reports the actual failure.
        ci_recipe = (
            ci_config_readable
            and ci_declaration == {"mode": "local-only-sources"}
            and justfile.exists()
            and _ship_ready_runs_strict_audit(justfile.read_text(), ci=True)
        )
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            parsed = _workflow_steps(text)
            if parsed is None:
                unparseable_workflows.append(path.relative_to(root).as_posix())
                continue
            triggers, run_values = parsed
            if not triggers:
                continue
            relative = path.relative_to(root).as_posix()
            validation_workflows.append(relative)
            has_ship_ready = any(
                active and value == "just ship-ready" for value, active in run_values
            )
            has_ci_check = any(active and value == "just ci-check" for value, active in run_values)
            if not (has_ship_ready or (ci_recipe and has_ci_check)):
                invalid_validation_workflows.append(str(path.relative_to(root)))
            elif has_ship_ready and not has_ci_check:
                ship_ready_only_workflows.append(relative)
        if unparseable_workflows:
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "ci-workflow-parse",
                    "CI workflow YAML could not be parsed safely.",
                    ", ".join(sorted(unparseable_workflows)),
                )
            )
        if not validation_workflows:
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "ci-ship-ready",
                    (
                        "CI has no applicable pull_request or push validation workflow. "
                        "At least one must run exact `run: just ship-ready` "
                        "(or `run: just ci-check` in local-only mode)."
                    ),
                    ".github/workflows",
                )
            )
        elif invalid_validation_workflows:
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "ci-ship-ready",
                    (
                        "CI validation workflows must contain an active exact "
                        "`run: just ship-ready` (or `run: just ci-check` in local-only mode) step."
                    ),
                    ", ".join(sorted(invalid_validation_workflows)),
                )
            )
        if (
            ci_config_readable
            and is_data_science
            and ci_declaration in (None, {"mode": "local-only-sources"})
            and ship_ready_only_workflows
            and _science_applies(root)
            and _has_local_only_sources(root)
        ):
            hint_action = (
                "Replace `just ship-ready` with `just ci-check` in CI"
                if ci_declaration == {"mode": "local-only-sources"}
                else 'Set `[ci] mode = "local-only-sources"` in .astack/project.toml '
                "and run `just ci-check` in CI"
            )
            findings.append(
                _finding(
                    "P2",
                    "workflow",
                    "ci-local-sources-hint",
                    (
                        "CI runs `just ship-ready` while data/sources.yml declares local-only "
                        f"sources absent in CI. {hint_action} (see docs/checks-and-audit.md)."
                    ),
                    ", ".join(sorted(ship_ready_only_workflows)),
                )
            )


def _check_stage_purposes(root: Path, findings: list[Finding]) -> None:
    """A stage script with no docstring leaves its review page without a purpose."""
    import ast

    try:
        pipeline = (root / "dvc.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for path in sorted((root / "scripts").glob("*.py")) if (root / "scripts").is_dir() else []:
        relative = path.relative_to(root).as_posix()
        if relative not in pipeline:
            continue
        try:
            documented = ast.get_docstring(ast.parse(path.read_bytes()))
        except (OSError, SyntaxError, ValueError):
            continue
        if not documented:
            findings.append(
                _finding(
                    "P2",
                    "workflow",
                    "stage-script-purpose",
                    "This stage script has no docstring, so its review page has no purpose line.",
                    relative,
                )
            )


def _check_evidence(root: Path, findings: list[Finding]) -> None:
    _check_stage_purposes(root, findings)
    reports = list((root / "reports").rglob("*.qmd")) if (root / "reports").exists() else []
    notebooks = [
        path
        for path in ((root / "notebooks").rglob("*.py") if (root / "notebooks").exists() else [])
        if not _viewer_notebook(root, path)
    ]
    analysis_code = [
        path
        for folder in (root / "src", root / "scripts")
        if folder.exists()
        for path in folder.rglob("*.py")
        if path.name != "__init__.py"
    ]
    legacy_qa_files = (
        [path for path in (root / "reports" / "qa").rglob("*") if path.is_file()]
        if (root / "reports" / "qa").exists()
        else []
    )
    evidence_files = (
        [path for path in (root / "evidence").rglob("*") if path.is_file()]
        if (root / "evidence").exists()
        else []
    )
    durable_evidence = evidence_files or legacy_qa_files
    if reports and not durable_evidence:
        findings.append(
            _finding(
                "P1",
                "scientific-evidence",
                "report-qa",
                "A narrative report exists without durable QA artifacts.",
                f"reports={len(reports)}, evidence_files=0",
            )
        )
    if notebooks and not analysis_code and not durable_evidence:
        findings.append(
            _finding(
                "P1",
                "scientific-evidence",
                "notebook-only-analysis",
                "Notebooks appear to be the only analytical implementation and evidence location.",
                f"notebooks={len(notebooks)}, reusable_or_script_files=0, evidence_files=0",
            )
        )


def _classify_output_path(relative: str) -> dict[str, str | None]:
    windows_path = PureWindowsPath(relative)
    normalized = relative.replace("\\", "/").strip("/")
    parts = Path(normalized).parts
    if (
        not normalized
        or windows_path.is_absolute()
        or windows_path.drive
        or Path(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return {"lifecycle": "invalid", "component": None, "problem": "unsafe-path"}
    if normalized == "data/raw" or normalized.startswith("data/raw/"):
        return {"lifecycle": "raw", "component": None, "problem": None}
    if normalized == "data/manual" or normalized.startswith("data/manual/"):
        return {"lifecycle": "manual", "component": None, "problem": None}
    if normalized == "scratch" or normalized.startswith("scratch/"):
        return {"lifecycle": "scratch", "component": None, "problem": None}
    if normalized == ".cache" or normalized.startswith(".cache/"):
        return {"lifecycle": "routine", "component": None, "problem": None}
    if normalized == "reports/qa" or normalized.startswith("reports/qa/"):
        return {"lifecycle": "legacy-evidence", "component": None, "problem": "legacy-path"}
    for prefix, lifecycle in COMPONENT_LIFECYCLES.items():
        if normalized == prefix:
            return {"lifecycle": lifecycle, "component": None, "problem": None}
        if normalized.startswith(f"{prefix}/"):
            component = normalized.removeprefix(f"{prefix}/").split("/", 1)[0]
            problem = None if COMPONENT_PATTERN.fullmatch(component) else "invalid-component"
            return {"lifecycle": lifecycle, "component": component, "problem": problem}
    if normalized == "reports" or normalized.startswith("reports/"):
        return {"lifecycle": "report", "component": None, "problem": None}
    return {"lifecycle": "other", "component": None, "problem": None}


def _output_policy(config: dict[str, Any]) -> tuple[bool, set[str]]:
    outputs = config.get("outputs", {})
    if not isinstance(outputs, dict) or outputs.get("layout") != "component":
        return False, set()
    components = outputs.get("components", [])
    if not isinstance(components, list):
        return True, set()
    return True, {component for component in components if isinstance(component, str)}


def _check_output_layout(root: Path, config: dict[str, Any], findings: list[Finding]) -> None:
    component_layout, declared = _output_policy(config)
    priority = "P1" if component_layout else "P2"
    roots = [Path(prefix) for prefix in COMPONENT_LIFECYCLES]
    roots.extend([Path("reports/qa"), Path("reports")])
    # The scientific output registry's evidence file is project metadata, not a generated
    # output, and its documented home is evidence/<name>.yml.
    registry = config.get("scientific_outputs")
    evidence_path = registry.get("evidence_path") if isinstance(registry, dict) else None
    seen: set[str] = set()
    if isinstance(evidence_path, str):
        evidence_file = Path(evidence_path)
        # The registry loader's own rule: relative, no `..`, confined to evidence/.
        if (
            not evidence_file.is_absolute()
            and ".." not in evidence_file.parts
            and evidence_file.parts[:1] == ("evidence",)
        ):
            seen.add(evidence_file.as_posix())  # Same spelling as the walk below (`./`, `//`).
    for output_root in roots:
        absolute_root = root / output_root
        if not absolute_root.exists():
            continue
        for path in absolute_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            classification = _classify_output_path(relative)
            lifecycle = classification["lifecycle"]
            component = classification["component"]
            problem = classification["problem"]
            if lifecycle == "legacy-evidence":
                findings.append(
                    _finding(
                        priority,
                        "workflow",
                        "legacy-output-path",
                        "Machine-readable QA belongs under evidence/<component>/, not reports/qa/.",
                        relative,
                    )
                )
                continue
            if (
                lifecycle in {"figure", "table", "report"}
                and path.suffix.casefold() in MACHINE_OUTPUT_SUFFIXES
            ):
                findings.append(
                    _finding(
                        priority,
                        "workflow",
                        "machine-output-in-reports",
                        "Machine-readable outputs belong under artifacts/ or evidence/.",
                        relative,
                    )
                )
                continue
            if lifecycle in COMPONENT_LIFECYCLES.values():
                if problem or component is None:
                    findings.append(
                        _finding(
                            priority,
                            "workflow",
                            "component-output-path",
                            "Generated outputs must use a valid lifecycle/<component>/file path.",
                            relative,
                        )
                    )
                elif component_layout and component not in declared:
                    findings.append(
                        _finding(
                            "P1",
                            "workflow",
                            "undeclared-component",
                            "Output path uses a component not declared in .astack/project.toml.",
                            f"component={component}, path={relative}",
                        )
                    )
                continue


def _run_required_tool(
    root: Path,
    findings: list[Finding],
    *,
    check: str,
    configured_path: str,
    args: list[str],
    session: Any = None,
    stage_name: str | None = None,
) -> None:
    try:
        result = (
            session.run_command(stage_name or check, args)
            if session is not None
            else _run(args, cwd=root)
        )
    except FileNotFoundError:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                f"{check}-availability",
                "A configured validation tool is unavailable.",
                configured_path,
            )
        )
        return
    if result.returncode != 0:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                f"{check}-runtime",
                "A configured validation command failed.",
                " ".join(args),
            )
        )


def _configured_capabilities(root: Path, findings: list[Finding]) -> set[str]:
    path = root / ".astack/project.toml"
    if not path.exists():
        return set()
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        capabilities = value.get("tools", [])
    except (tomllib.TOMLDecodeError, OSError, AttributeError):
        return set()
    supported = {"dvc", "marimo", "quarto"}
    if not isinstance(capabilities, list) or any(
        not isinstance(capability, str) or capability not in supported
        for capability in capabilities
    ):
        findings.append(
            _finding(
                "P1",
                "workflow",
                "capabilities",
                "Project tools declaration is invalid.",
                ".astack/project.toml",
            )
        )
        return set()
    return set(capabilities)


def _check_optional_tools(
    root: Path,
    findings: list[Finding],
    run_tools: bool,
    *,
    ensure_dvc_cache: Callable[[Path], Path],
    session: Any = None,
    ci: bool = False,
) -> None:
    """`ensure_dvc_cache` comes from the reproduction module, which itself imports this one."""
    software_path = "justfile"
    configured: dict[str, list[list[str]]] = {
        "software": [["just", "check"], ["just", "integration"]]
    }
    if not _scaffold_layout_applies(root):
        # A General project has no astack justfile. Run its own declared CHECK instead.
        software_path = ".astack/project.toml"
        software = _general_check_commands(root, findings)
        if software:
            configured["software"] = software
        else:
            del configured["software"]
    else:
        configured["software"] += _scientific_verify_commands(root, findings) or []
    capabilities = _configured_capabilities(root, findings)
    dvc_present = (root / ".dvc/config").exists() or (root / "dvc.yaml").exists()
    reports = sorted((root / "reports").rglob("*.qmd")) if (root / "reports").exists() else []
    notebooks = sorted((root / "notebooks").rglob("*.py")) if (root / "notebooks").exists() else []
    discovered = {
        capability
        for capability, present in {
            "dvc": dvc_present,
            "quarto": bool(reports),
            "marimo": bool(notebooks),
        }.items()
        if present
    }
    for capability in sorted(discovered - capabilities):
        findings.append(
            _finding(
                "P1",
                "workflow",
                "unregistered-capability",
                "Capability files exist but the capability is not declared in project state.",
                capability,
            )
        )
    if "dvc" in capabilities:
        if not (root / ".dvc/config").exists():
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "dvc-configuration",
                    "DVC capability is declared but its project configuration is incomplete.",
                    ".dvc/config is required",
                )
            )
        configured["dvc"] = [["uv", "run", "--locked", "dvc", "status"]]
        if (root / "dvc.yaml").exists():
            configured["dvc"].append(
                ["uv", "run", "--locked", "dvc", "repro", "--dry", "--allow-missing"]
            )
    if "quarto" in capabilities:
        if not reports:
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "quarto-configuration",
                    "Quarto capability is declared but no report source exists.",
                    "reports/**/*.qmd",
                )
            )
        configured["quarto"] = [
            ["quarto", "render", str(path.relative_to(root))] for path in reports
        ]
    if "marimo" in capabilities:
        if not notebooks:
            findings.append(
                _finding(
                    "P1",
                    "workflow",
                    "marimo-configuration",
                    "Marimo capability is declared but no notebook source exists.",
                    "notebooks/**/*.py",
                )
            )
        configured["marimo"] = [
            ["uv", "run", "--locked", "marimo", "check", str(path.relative_to(root))]
            for path in notebooks
        ]
    spatial_validators = _spatial_validators(root)
    if spatial_validators:
        configured["spatial"] = spatial_validators
    if not run_tools:
        for capability in configured:
            findings.append(
                _finding(
                    "P2",
                    "reproducibility",
                    f"{capability}-not-run",
                    "Configured runtime validation was not performed.",
                    "rerun with --run-tools",
                )
            )
        return
    dvc_cache_failed = False
    if "dvc" in capabilities and not ci:
        try:
            ensure_dvc_cache(root)
        except (OSError, RuntimeError, ValueError) as exc:
            dvc_cache_failed = True
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "dvc-cache-configuration",
                    "The repository-local DVC cache could not be configured.",
                    str(exc),
                )
            )
    for capability, commands in configured.items():
        if capability == "dvc" and dvc_cache_failed:
            continue
        if ci and capability != "software":
            findings.append(
                _finding(
                    "P2",
                    "reproducibility",
                    f"{capability}-not-run",
                    "Limited CI does not execute source-dependent rendering/reproduction tools.",
                    "Strict local ship-ready remains required.",
                )
            )
            continue
        configured_path = {
            "software": software_path,
            "dvc": "dvc.yaml",
            "quarto": "reports/",
            "marimo": "notebooks/",
            "spatial": "data/spatial.yml",
        }[capability]
        for index, args in enumerate(commands):
            stage_name = capability
            if capability != "software":
                stage_name = f"capability.{capability}.{index + 1}"
            elif software_path != "justfile":
                stage_name = general_stage_names(len(commands))[index]
            elif index == 0:
                stage_name = "check"
            elif index == 1:
                stage_name = "integration"
            else:
                stage_name = verify_stage_names(len(commands) - 2)[index - 2]
            _run_required_tool(
                root,
                findings,
                check=capability,
                configured_path=configured_path,
                args=args,
                session=session,
                stage_name=stage_name,
            )


def _general_check_commands(root: Path, findings: list[Finding]) -> list[list[str]]:
    """CHECK argv for a General project, plus VERIFY when scientific validation declares it.

    Uses the same parser as `validate`, so the audit accepts and rejects exactly what it does.
    """
    try:
        commands = list(validation_commands(root, "check"))
    except ValueError as exc:
        findings.append(_software_config_finding(exc))
        return []
    verify = _scientific_verify_commands(root, findings)
    if verify is None:
        return []
    return commands + verify


def _scientific_verify_commands(root: Path, findings: list[Finding]) -> list[list[str]] | None:
    """VERIFY argv for a project that enables scientific validation.

    Undeclared VERIFY is reported as not run, never as a pass. It blocks only a material
    analysis, which a deliverable relies on. Exploratory work still sees the finding. Returns
    None when the declaration is malformed.
    """
    config = _project_config(root)
    capabilities = config.get("capabilities", [])
    if not isinstance(capabilities, list) or SCIENCE_CAPABILITIES[1] not in capabilities:
        return []
    declared = config.get("validation", {})
    if isinstance(declared, dict) and "verify" not in declared:
        analysis = config.get("analysis", {})
        material = isinstance(analysis, dict) and analysis.get("stage") == "material"
        findings.append(
            _finding(
                "P1" if material else "P2",
                "scientific-evidence",
                "verify-undeclared",
                "Scientific validation is enabled but validation.verify is not declared, "
                "so VERIFY did not run.",
                "declare validation.verify in .astack/project.toml"
                + ("" if material else " before analysis.stage becomes material"),
            )
        )
        return []
    try:
        return list(validation_commands(root, "verify"))
    except ValueError as exc:
        findings.append(_software_config_finding(exc))
        return None


def _software_config_finding(exc: ValueError) -> Finding:
    return _finding(
        "P1",
        "workflow",
        "software-config",
        "The declared validation commands cannot be run.",
        str(exc),
    )


def verify_stage_names(count: int) -> list[str]:
    """Session stage names for VERIFY commands that a scaffolded project runs after CHECK."""
    return ["verify" if index == 0 else f"verify.{index + 1}" for index in range(count)]


def general_stage_names(count: int) -> list[str]:
    """Session stage names for a General project's own commands."""
    return ["check" if index == 0 else f"check.{index + 1}" for index in range(count)]


def _project_config(root: Path) -> dict[str, Any]:
    return dict(project_config(root))


def _validation_commands(
    config: dict[str, Any],
) -> tuple[dict[str, list[list[str]]], list[str]]:
    declaration = config.get("validation")
    if declaration is None:
        return {}, []
    if not isinstance(declaration, dict):
        return {}, ["validation must be a table"]

    # CHECK runs through the shared project policy. VERIFY and RELEASE are interpreted here.
    errors = [
        f"unsupported tier {key!r}"
        for key in declaration
        if key not in ("check", *VALIDATION_TIERS)
    ]
    commands: dict[str, list[list[str]]] = {}
    for tier in VALIDATION_TIERS:
        if tier not in declaration:
            continue
        value = declaration[tier]
        if not isinstance(value, list) or not value:
            errors.append(f"validation.{tier} must contain one or more command arrays")
            continue
        if any(
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
            for command in value
        ):
            errors.append(f"validation.{tier} must contain non-empty string command arrays")
            continue
        commands[tier] = value
    return commands, errors


def _check_validation_config(config: dict[str, Any], findings: list[Finding]) -> None:
    _, errors = _validation_commands(config)
    for error in errors:
        findings.append(
            _finding(
                "P1",
                "workflow",
                "validation-config",
                "Validation tier configuration is invalid.",
                error,
            )
        )
