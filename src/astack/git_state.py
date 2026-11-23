"""Plain Git facts shared by project safeguards, diagnostics, and release state.

This module deliberately holds no lifecycle registry. Worktree inventory comes from
``git worktree list --porcelain`` and local state lives under the Git common directory.
It is vendored into generated projects as ``.astack/git_state.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath
from typing import Any

try:
    from .project_policy import project_config, runtime_directory
except ImportError:
    from project_policy import (  # type: ignore[import-not-found, no-redef]
        project_config,
        runtime_directory,
    )

DATA_ROOT_ENV = "ASTACK_DATA_ROOT"
DVC_CACHE_ROOT_ENV = "ASTACK_DVC_CACHE_ROOT"
STATE_DIRECTORY = "astack"


def run(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{' '.join(args)} failed")
    return result


def git_toplevel(cwd: Path) -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(result.stdout.strip()).resolve()


def git_common_dir(cwd: Path) -> Path:
    root = git_toplevel(cwd)
    result = run(["git", "rev-parse", "--git-common-dir"], cwd=root)
    common = Path(result.stdout.strip())
    return (common if common.is_absolute() else root / common).resolve()


def state_path(cwd: Path, name: str) -> Path:
    """Return a machine-local state file shared by every checkout of one repository."""
    return git_common_dir(cwd) / STATE_DIRECTORY / name


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


@contextmanager
def state_lock(cwd: Path, name: str) -> Iterator[None]:
    """Serialize writers of one local state file across processes."""
    path = state_path(cwd, f"{name}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write("\0")
                handle.flush()
            handle.seek(0)
            windows_lock = vars(msvcrt)
            locking = windows_lock["locking"]
            locking(handle.fileno(), windows_lock["LK_LOCK"], 1)
            try:
                yield
            finally:
                handle.seek(0)
                locking(handle.fileno(), windows_lock["LK_UNLCK"], 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_worktree_list(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*text.splitlines(), ""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    return records


def git_worktrees(cwd: Path) -> list[dict[str, Any]]:
    result = run(["git", "worktree", "list", "--porcelain"], cwd=cwd)
    return parse_worktree_list(result.stdout)


def canonical_checkout(cwd: Path) -> Path:
    records = git_worktrees(git_toplevel(cwd))
    if not records:
        raise RuntimeError("Git did not report a canonical worktree")
    return Path(str(records[0]["worktree"])).resolve()


def list_worktrees(cwd: Path) -> list[dict[str, Any]]:
    """Inventory every Git worktree. Unknown cleanliness stays ``None``, never clean."""
    records: list[dict[str, Any]] = []
    for index, value in enumerate(git_worktrees(git_toplevel(cwd))):
        path = Path(str(value["worktree"]))
        exists = path.exists()
        status = None
        if exists and not value.get("prunable", False):
            try:
                status = run(["git", "status", "--short"], cwd=path, check=False)
            except OSError:
                status = None
        inspected = status is not None and status.returncode == 0
        records.append(
            {
                "path": str(path),
                "branch": str(value.get("branch", "")).removeprefix("refs/heads/") or None,
                "head": value.get("HEAD"),
                "canonical": index == 0,
                "exists": exists,
                "dirty": bool(status.stdout.strip()) if inspected and status is not None else None,
                "prunable": bool(value.get("prunable", False)),
            }
        )
    return records


def default_branch(canonical: Path) -> str:
    remote = run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=canonical,
        check=False,
    )
    if remote.returncode == 0 and remote.stdout.strip():
        return remote.stdout.strip().removeprefix("origin/")
    for candidate in ("main", "master"):
        for reference in (f"refs/heads/{candidate}", f"refs/remotes/origin/{candidate}"):
            if (
                run(
                    ["git", "show-ref", "--verify", "--quiet", reference],
                    cwd=canonical,
                    check=False,
                ).returncode
                == 0
            ):
                return candidate
    branch = run(["git", "branch", "--show-current"], cwd=canonical).stdout.strip()
    if not branch:
        raise RuntimeError("Could not resolve a default branch")
    return branch


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid TOML object: {path}")
    return value


def configs(canonical: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        project_config(canonical),
        _load_toml(runtime_directory(canonical) / "local.toml"),
    )


def _unresolved_configured_path(
    env_name: str,
    local: dict[str, Any],
    section: str,
    key: str,
    default: Path,
    base: Path,
) -> Path:
    local_section = local.get(section, {})
    local_value = local_section.get(key) if isinstance(local_section, dict) else None
    value = os.environ.get(env_name) or local_value or default
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else base / path


def _configured_path(
    env_name: str,
    local: dict[str, Any],
    section: str,
    key: str,
    default: Path,
    base: Path,
) -> Path:
    return _unresolved_configured_path(env_name, local, section, key, default, base).resolve()


def configured_source(canonical: Path, env_name: str, section: str, key: str) -> str:
    if os.environ.get(env_name):
        return "environment"
    _, local = configs(canonical)
    values = local.get(section, {})
    if isinstance(values, dict) and values.get(key):
        return ".astack/local.toml"
    return "default"


EXTERNAL_ROOT_ENV_PREFIX = "ASTACK_EXTERNAL_ROOT_"


def external_root(canonical: Path, name: str) -> Path | None:
    """Machine-local location of a named external data root, or None when not configured.

    Set ASTACK_EXTERNAL_ROOT_<NAME> or `[data.external_roots] <name> = "/abs/path"` in
    .astack/local.toml. Only absolute paths are accepted, because the location is per machine.
    """
    if not re.fullmatch(r"[a-z0-9_]+", name):
        return None
    env_name = EXTERNAL_ROOT_ENV_PREFIX + name.upper()
    value: object = os.environ.get(env_name)
    if not value:
        _, local = configs(canonical)
        data = local.get("data", {})
        roots = data.get("external_roots", {}) if isinstance(data, dict) else {}
        value = roots.get(name) if isinstance(roots, dict) else None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else None


def data_root(canonical: Path) -> Path:
    _, local = configs(canonical)
    return _configured_path(DATA_ROOT_ENV, local, "data", "root", canonical / "data", canonical)


def repository_identity(remote: str | None, canonical: Path) -> str:
    """Return a stable, credential-free identity shared by equivalent remote URLs."""
    value = (remote or "").strip()
    from urllib.parse import urlsplit

    host, path = "", ""
    try:
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.scheme in {"https", "http", "ssh", "git"} and parsed.hostname:
                host = parsed.hostname.lower()
                port = parsed.port
                if (
                    port is not None
                    and port != {"https": 443, "http": 80, "ssh": 22, "git": 9418}[parsed.scheme]
                ):
                    host += f":{port}"
                path = parsed.path.strip("/")
        else:
            match = re.fullmatch(r"(?:[^@/]+@)?([^/:]+):(.+)", value)
            if match:
                host, path = match.group(1).lower(), match.group(2).strip("/")
    except ValueError:
        pass
    path = path.removesuffix(".git")
    if host and path and all(part not in {"", ".", ".."} for part in path.split("/")):
        # GitHub repository paths are case-insensitive. Other servers need not be.
        return f"{host}/{path.lower() if host == 'github.com' else path}"
    return str(canonical.resolve())


def _repository_cache_id(canonical: Path) -> str:
    remote = run(
        ["git", "config", "--get", "remote.origin.url"], cwd=canonical, check=False
    ).stdout.strip()
    identity = repository_identity(remote, canonical)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    name = (
        re.sub(r"[^a-z0-9]+", "-", identity.rsplit("/", 1)[-1].lower()).strip("-") or "repository"
    )
    return f"{name}-{digest}"


def default_dvc_cache(canonical: Path) -> tuple[Path, str]:
    if sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    elif not sys.platform.startswith("win"):
        configured = os.environ.get("XDG_CACHE_HOME", "")
        base = Path(configured).expanduser() if configured else Path.home() / ".cache"
        if not base.is_absolute():
            base = Path.home() / ".cache"
    else:
        base = Path.home() / ".cache"
    return base / "astack/dvc" / _repository_cache_id(canonical), "platform-default"


def dvc_cache_configured_path(canonical: Path) -> Path:
    """Return the configured DVC cache path without resolving symlinks."""
    canonical = canonical_checkout(canonical)
    _, local = configs(canonical)
    default, _ = default_dvc_cache(canonical)
    return _unresolved_configured_path(
        DVC_CACHE_ROOT_ENV, local, "dvc", "cache_dir", default, canonical
    )


def dvc_cache_root(canonical: Path) -> Path:
    """Resolve one machine-local DVC cache for every checkout of a repository."""
    return dvc_cache_configured_path(canonical).resolve()


def _safe_relative(value: str, *, shared: bool = False) -> Path:
    candidate = PurePath(value)
    if not value or not candidate.parts or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Unsafe project-relative path: {value}")
    normalized = Path(*candidate.parts)
    if shared and normalized.as_posix().rstrip("/") in {"data", "."}:
        raise ValueError("A shared input must be narrower than the project root or data/")
    return normalized


def shared_inputs(canonical: Path) -> list[tuple[Path, Path]]:
    """Return (destination, source-under-data-root) pairs shared across worktrees."""
    project, _ = configs(canonical)
    data = project.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("The project data configuration must be a table")
    layout = str(data.get("layout", "standard"))
    if layout == "standard":
        share_raw = data.get("share_raw", False)
        if not isinstance(share_raw, bool):
            raise ValueError("data.share_raw must be true or false")
        return [(Path("data/raw"), Path("raw"))] if share_raw else []
    if layout != "legacy":
        raise ValueError("data.layout must be 'standard' or 'legacy'")
    values = data.get("legacy_shared_inputs", [])
    if not isinstance(values, list):
        raise ValueError("data.legacy_shared_inputs must be a list")
    mappings: list[tuple[Path, Path]] = []
    for value in values:
        if isinstance(value, str):
            destination = _safe_relative(value, shared=True)
            source = destination
        elif isinstance(value, dict):
            destination = _safe_relative(str(value.get("path", "")), shared=True)
            source = _safe_relative(str(value.get("source", value.get("path", ""))))
        else:
            raise ValueError("Each legacy shared input must be a path string or path/source table")
        mappings.append((destination, source))
    return mappings


def _require_read_only(source: Path) -> None:
    paths = [source, *source.rglob("*")] if source.is_dir() else [source]
    writable: list[str] = []
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"Shared input contains a symlink: {path}")
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            writable.append(str(path))
    if writable:
        preview = ", ".join(writable[:3])
        suffix = " ..." if len(writable) > 3 else ""
        raise ValueError("Shared inputs must be read-only before linking: " + preview + suffix)


def _check_destination_parents(worktree: Path, destination: Path) -> None:
    for parent in destination.parents:
        if parent == worktree:
            break
        if parent.is_symlink():
            raise ValueError(f"Shared-input destination has a symlinked ancestor: {parent}")
    if not destination.parent.resolve().is_relative_to(worktree.resolve()):
        raise ValueError("Shared-input destination ancestor escapes worktree")


def share_raw_inputs(root: Path, *, apply: bool = False) -> dict[str, Any]:
    """Link only immutable shared inputs from the canonical data root into a linked worktree.

    Dry-run is the default. Interim, processed, report, scratch, and environment state stay
    worktree-local. Writable sources, symlinks, escapes, and occupied destinations block.
    """
    worktree = git_toplevel(root)
    canonical = canonical_checkout(worktree)
    if worktree == canonical:
        return {"status": "not_applicable", "reason": "canonical checkout owns its data root"}
    data = data_root(canonical)
    planned: list[dict[str, str]] = []
    for destination_relative, source_relative in shared_inputs(canonical):
        source = (data / source_relative).resolve()
        if not source.is_relative_to(data):
            raise ValueError(f"Shared input escapes configured data root: {source_relative}")
        if not source.exists():
            raise FileNotFoundError(f"Shared input does not exist: {source}")
        _require_read_only(source)
        destination = worktree / destination_relative
        _check_destination_parents(worktree, destination)
        if destination.is_symlink() and destination.resolve() == source:
            continue
        if destination.exists() or destination.is_symlink():
            if not (destination.is_dir() and not destination.is_symlink()) or any(
                destination.iterdir()
            ):
                raise FileExistsError(
                    f"Refusing to replace shared-input destination: {destination}"
                )
        planned.append({"path": destination_relative.as_posix(), "source": str(source)})
    if apply:
        for item in planned:
            destination = worktree / item["path"]
            source = Path(item["source"])
            _check_destination_parents(worktree, destination)
            if not destination.resolve().is_relative_to(worktree.resolve()):
                raise ValueError("Shared-input destination escapes worktree")
            _require_read_only(source)
            if destination.is_dir() and not destination.is_symlink():
                destination.rmdir()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source, target_is_directory=source.is_dir())
    return {
        "status": "applied" if apply else "planned",
        "worktree": str(worktree),
        "canonical_checkout": str(canonical),
        "links": planned,
    }


def record_validation_diagnostics(cwd: Path, report: dict[str, Any]) -> None:
    """Retain one sanitized observation from an already finished validation session."""
    try:
        from .diagnostics import append_record, make_record
    except ImportError:
        from diagnostics import (  # type: ignore[import-not-found, no-redef]
            append_record,
            make_record,
        )
    try:
        canonical = canonical_checkout(cwd)
        with state_lock(canonical, "diagnostics"):
            append_record(
                state_path(canonical, "diagnostics.json").parent, make_record(validation=report)
            )
    except (OSError, RuntimeError, ValueError):
        pass
