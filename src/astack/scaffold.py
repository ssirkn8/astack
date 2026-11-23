from __future__ import annotations

import importlib.metadata
import json
import keyword
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path, PureWindowsPath

SUPPORTED_TOOLS = {"dvc", "marimo", "quarto"}
QUARTO_SETUP_REF = "quarto-dev/quarto-actions/setup@8a96df13519ee81fd526f2dfca5962811136661b"
QUARTO_SETUP_ACTION = f"{QUARTO_SETUP_REF} # v2"
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".qmd",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


class LockGenerationError(RuntimeError):
    """Raised after a project is generated when its lockfile cannot be created."""


def repository_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "templates").exists() and (source_root / ".opencode").exists():
        return source_root
    return Path(__file__).resolve().parent / "assets"


def workflow_version(root: Path | None = None) -> str:
    base = root or repository_root()
    pyproject = base / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    return importlib.metadata.version("astack")


def _validate_relative_path(relative: str) -> None:
    path = Path(relative)
    windows_path = PureWindowsPath(relative)
    if (
        not relative
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"Unsafe managed path: {relative!r}")


def _target_path(target: Path, relative: str) -> Path:
    _validate_relative_path(relative)
    root = target.resolve()
    path = target / relative
    current = path
    while current != target and current != current.parent:
        if current.is_symlink():
            raise ValueError(f"Managed path uses a symlink: {relative}")
        current = current.parent
    resolved = path.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Managed path escapes target: {relative}")
    return path


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _ignored(path: Path) -> bool:
    ignored_names = {
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "justfile.fragment",
    }
    return path.name in ignored_names or path.suffix == ".pyc"


def _copy_tree(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if _ignored(path) or any(_ignored(parent) for parent in path.parents if parent != source):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _checks_workflow_source(root: Path, capabilities: list[str]) -> bytes:
    path = root / "templates" / "project" / ".github" / "workflows" / "checks.yml"
    text = path.read_text(encoding="utf-8")
    if "quarto" in capabilities:
        setup_just = "      - uses: extractions/setup-just@"
        if setup_just not in text:
            raise RuntimeError("Project CI template is missing the setup-just insertion point")
        text = text.replace(
            setup_just,
            f"      - uses: {QUARTO_SETUP_ACTION}\n{setup_just}",
            1,
        )
    return text.encode()


def _project_scaffold_overlays(root: Path, capabilities: list[str]) -> dict[str, bytes]:
    """Render project-owned baseline files used only when scaffolding a new project."""
    template = root / "templates" / "project"
    justfile = (template / "justfile").read_bytes()
    for capability in sorted(capabilities):
        fragment = root / "templates" / "capabilities" / capability / "justfile.fragment"
        if fragment.exists():
            justfile = justfile.rstrip() + b"\n\n" + fragment.read_bytes()
    return {
        "justfile": justfile,
        ".github/workflows/checks.yml": _checks_workflow_source(root, capabilities),
    }


def _render_project(
    target: Path,
    project_name: str,
    package_name: str,
    description: str,
    intended_use: str,
) -> None:
    toml_description = json.dumps(description, ensure_ascii=False)[1:-1]
    replacements = {
        "Project Title": project_name,
        "PROJECT_TITLE_YAML": json.dumps(project_name, ensure_ascii=False),
        "One-line project description.": description,
        "PROJECT_DESCRIPTION_TOML": toml_description,
        "INTENDED_USE": intended_use,
        "ds-project": package_name.replace("_", "-"),
        "ds_project": package_name,
    }
    project_config = target / ".astack/project.toml"
    if project_config.exists():
        replacements["TOOLS_TOML"] = "[]"
    old_package = target / "src" / "ds_project"
    new_package = target / "src" / package_name
    if old_package.exists() and old_package != new_package:
        old_package.rename(new_package)
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def _generate_lock(target: Path) -> None:
    result = subprocess.run(
        ["uv", "lock"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise LockGenerationError(result.stderr.strip() or "Could not generate uv.lock")


def _add_capability_dependencies(target: Path, capabilities: list[str]) -> None:
    dependencies = {
        "dvc": '  "dvc>=3.0",\n',
        # Upper bounds keep the review notebook on versions its smoke test covered.
        "marimo": '  "anywidget>=0.9,<1",\n  "marimo>=0.20,<1",\n  "pyarrow>=15",\n',
    }
    additions = "".join(dependencies[name] for name in sorted(capabilities) if name in dependencies)
    path = target / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("  # capability-dependencies\n", additions), encoding="utf-8")


def create_project(
    target: Path,
    *,
    project_name: str,
    package_name: str,
    description: str,
    intended_use: str = "Not yet defined",
    tools: list[str] | None = None,
    lock: bool = True,
) -> list[str]:
    """Write the Data Science project template, then the files `astack install` manages."""
    from .installation import MANIFEST, executable_targets, generated_files

    root = repository_root()
    selected = sorted(set(tools or []))
    unknown = set(selected) - SUPPORTED_TOOLS
    if unknown:
        raise ValueError(f"Unknown tools: {', '.join(sorted(unknown))}")
    if (
        not package_name.isidentifier()
        or keyword.iskeyword(package_name)
        or not re.fullmatch(r"[a-z][a-z0-9_]*", package_name)
    ):
        raise ValueError("Package name must be a lowercase Python identifier")
    if "\n" in project_name or "\r" in project_name:
        raise ValueError("Project name must be a single line")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Target is not empty: {target}")
    created_target = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    try:
        _copy_tree(root / "templates" / "project", target)
        for relative in [
            "data/raw",
            "data/manual",
            "data/interim",
            "data/processed",
            "artifacts",
            "evidence",
            "docs/work-items",
            "reports/audits",
            "reports/figures",
            "reports/tables",
            "scripts",
            "scratch",
        ]:
            (target / relative).mkdir(parents=True, exist_ok=True)
        for tool in selected:
            _copy_tree(root / "templates" / "capabilities" / tool, target)
        for relative, data in _project_scaffold_overlays(root, selected).items():
            (target / relative).write_bytes(data)
        managed, manifest = generated_files(root, "data-science")
        executables = executable_targets(root)
        for relative, data in managed.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if relative in executables:
                path.chmod(0o755)
        _render_project(target, project_name, package_name, description, intended_use)
        project_config = target / ".astack/project.toml"
        config_text = project_config.read_text(encoding="utf-8")
        config_text = config_text.replace("tools = []", f"tools = {json.dumps(selected)}")
        if "marimo" in selected:
            # CHECK also confirms the review notebook still parses.
            config_text = config_text.replace(
                'check = [["just", "check"]]',
                'check = [["just", "check"], ["just", "review-check"]]',
            )
        project_config.write_text(config_text, encoding="utf-8")
        tomllib.loads(project_config.read_text(encoding="utf-8"))
        _add_capability_dependencies(target, selected)
        if any((target / path).read_bytes() != data for path, data in managed.items()):
            raise RuntimeError("Project rendering changed a managed workflow file")
        # The copied template is project-owned. Only the installer's files remain managed.
        (target / MANIFEST).write_bytes(manifest)
    except Exception:
        for child in target.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
        if created_target:
            target.rmdir()
        raise
    if lock:
        _generate_lock(target)
    return sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file())


def scaffold_smoke_test() -> Path:
    temporary = Path(tempfile.mkdtemp(prefix="astack-"))
    target = temporary / "smoke-project"
    create_project(
        target,
        project_name="Smoke Project",
        package_name="smoke_project",
        description="Generated scaffold smoke test.",
        intended_use="Validate the generated scaffold.",
        lock=False,
    )
    return target
