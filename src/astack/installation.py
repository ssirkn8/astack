"""Dry-run-first General adoption with pinned source and checksum ownership."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .adapters import component_bytes
from .components import NATIVE_DISCOVERY_ROOTS, load_components
from .project_policy import (
    PROFILES,
    project_config,
    project_profile,
    validation_commands,
)
from .release_upgrade import release_version, resolve_release
from .scaffold import _atomic_write, _target_path, repository_root, workflow_version
from .upstream_skills import MANIFEST as SKILLS_MANIFEST
from .upstream_skills import RETIRED_SKILL_FILES

MANIFEST = ".astack/manifest.json"
TRANSACTION = ".astack/install-transaction.json"
# Managed paths of retired components. Upgrades release them, but recovery must still accept
# journals that older installers wrote while creating or updating them.
RETIRED_MANAGED_PATHS = frozenset(
    {
        ".opencode/skills/command-results/SKILL.md",
        ".agents/skills/command-results/SKILL.md",
        ".claude/skills/command-results/SKILL.md",
    }
)
# Retired upstream skill copies, which upgrades remove when their checksum still matches.
RETIRED_SKILL_PATHS = frozenset(
    f"{root}/{relative}"
    for root in NATIVE_DISCOVERY_ROOTS.values()
    for relative in RETIRED_SKILL_FILES
)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _source_commit(root: Path) -> str | None:
    # A checkout's actual HEAD takes precedence over unrelated installed package metadata.
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return result.stdout.strip()
    try:
        value = importlib.metadata.distribution("astack").read_text("direct_url.json")
        metadata = json.loads(value or "{}")
        commit = metadata.get("vcs_info", {}).get("commit_id")
        if isinstance(commit, str) and re.fullmatch("[0-9a-f]{40}", commit):
            return commit
    except (importlib.metadata.PackageNotFoundError, ValueError):
        pass
    return None


def _source_identity(root: Path, release: str | None, candidate: bool) -> dict[str, Any]:
    if bool(release) == bool(candidate):
        raise ValueError("Choose one published release or explicit candidate mode")
    commit = _source_commit(root)
    git_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if git_root.returncode == 0 and Path(git_root.stdout.strip()).resolve() == root.resolve():
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if dirty and not candidate:
            raise ValueError("Published-release installation refuses modified source checkout")
    if candidate:
        return {"kind": "candidate", "commit": commit, "published": False}
    resolved = resolve_release(str(release))
    if workflow_version(root) != resolved["tag"].removeprefix("v") or commit != resolved["commit"]:
        raise ValueError("Run the installer from the exact published commit: " + resolved["commit"])
    return {"kind": "release", **resolved, "published": True}


def _payload(root: Path) -> tuple[dict[str, bytes], dict[str, dict[str, str]]]:
    """Managed files. Every profile gets the same bytes, so a policy change never rewrites them."""
    files = {}
    inventory = {}
    delivered_components = []
    for component in load_components(root):
        data = component_bytes(root, component)
        files[component.target] = data
        delivered: dict[str, Any] = {
            "id": component.id,
            "kind": component.kind,
            "source": component.source,
            "target": component.target,
        }
        if component.executable:
            delivered["executable"] = True
        delivered_components.append(delivered)
        inventory[component.id] = {"path": component.target, "sha256": _hash(data)}
    catalog = ".astack/components.json"
    files[catalog] = _json({"schema_version": 1, "components": delivered_components})
    inventory["runtime.components"]["sha256"] = _hash(files[catalog])
    return files, inventory


def executable_targets(root: Path) -> set[str]:
    """Managed files that must be executable, such as upstream skill helper scripts."""
    return {component.target for component in load_components(root) if component.executable}


def generated_files(root: Path, profile: str) -> tuple[dict[str, bytes], bytes]:
    """Managed files and the manifest for a project `astack new` is creating.

    The project is new and empty, so there is nothing to plan or preserve. Later upgrades use
    the same manifest through `astack install`.
    """
    files, inventory = _payload(root)
    version = workflow_version(root)
    files[".astack/version"] = ("v" + version + "\n").encode()
    inventory["runtime.version"] = {
        "path": ".astack/version",
        "sha256": _hash(files[".astack/version"]),
    }
    manifest = {
        "schema_version": 1,
        "profile": profile,
        "workflow_version": version,
        "source": {"kind": "generated", "commit": _source_commit(root), "published": False},
        "managed_components": inventory,
    }
    return files, _json(manifest)


def _git_state(target: Path, initialize: bool) -> dict[str, Any]:
    if not target.exists() or not any(target.iterdir()):
        if not initialize:
            raise ValueError("Empty targets require explicit --initialize")
        return {"initialize": True, "head": None, "status": ""}
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode or Path(top.stdout.strip()).resolve() != target:
        raise ValueError("Target must be the root of an existing Git repository")
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=target,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        "initialize": False,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "status": status.stdout,
    }


def _configuration(commands: list[list[str]]) -> bytes:
    if not commands or any(
        not isinstance(c, list)
        or not c
        or not isinstance(c[0], str)
        or not c[0]
        or any(not isinstance(arg, str) or "\x00" in arg for arg in c)
        for c in commands
    ):
        raise ValueError("Provide exact check argv arrays with a nonempty executable")
    return (
        'schema_version = 1\nprofile = "general"\n\n[validation]\ncheck = '
        + json.dumps(commands)
        + "\n"
    ).encode()


def _plan(
    target: Path,
    *,
    release: str | None,
    candidate: bool,
    commands: list[list[str]] | None,
    initialize: bool,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    target = target.expanduser().resolve()
    root = repository_root()
    state = _git_state(target, initialize)
    source = _source_identity(root, release, candidate)
    config_path = _target_path(target, ".astack/project.toml")
    if config_path.exists() and not config_path.is_file():
        raise ValueError("Owner configuration must be a regular file")
    selected_profile = project_profile(target) if config_path.exists() else "general"
    desired, inventory = _payload(root)
    source["content_digest"] = _hash(_json({p: _hash(b) for p, b in sorted(desired.items())}))
    conflicts = []
    if _target_path(target, ".astack/install.lock").exists():
        conflicts.append(
            "Installation lock exists. Reconcile a live or interrupted installer first"
        )
    if _target_path(target, TRANSACTION).exists():
        conflicts.append(
            "Interrupted installation: inspect and recover the recorded transaction first"
        )
    if conflicts:
        blocked = {
            "schema_version": 1,
            "target": str(target),
            "profile": selected_profile,
            "source": source,
            "git": state,
            "actions": [],
            "conflicts": conflicts,
            "preserved": "All existing files",
            "limitations": [],
        }
        blocked["plan_digest"] = _hash(_json(blocked))
        return blocked, {}
    manifest_path = _target_path(target, MANIFEST)
    installed: dict[str, Any] = {}
    if manifest_path.exists() and not manifest_path.is_file():
        raise ValueError("Installation manifest must be a regular file")
    if manifest_path.exists():
        installed = json.loads(manifest_path.read_text())
        if (
            not isinstance(installed, dict)
            or installed.get("schema_version") != 1
            or installed.get("profile") not in PROFILES
            or installed.get("installation_layout", "general") != "general"
        ):
            raise ValueError("Unknown installation manifest, preserve it for explicit migration")
    if installed and release_version("v" + workflow_version(root)) < release_version(
        "v" + str(installed.get("workflow_version"))
    ):
        conflicts.append(
            "This project was last set up by a newer astack, so it was left unchanged. "
            "Install that astack version or newer, or roll the project back with Git."
        )
    owned = installed.get("managed_components", {})
    if not isinstance(owned, dict):
        raise ValueError("Invalid managed ownership evidence")
    if config_path.exists():
        project_config(target)
        # Project configuration is owner-owned after creation and never checksum-replaced.
        existing_commands = validation_commands(target, "check")
        if commands is not None and commands != existing_commands:
            conflicts.append("Requested checks differ from owner-owned configuration")
    else:
        desired[".astack/project.toml"] = _configuration(commands or [])
    # Every supported harness, Claude Code included, reads AGENTS.md, so astack keeps one
    # instruction file. An existing CLAUDE.md belongs to the owner and is left alone.
    agents = _target_path(target, "AGENTS.md")
    guidance = (
        b"# Project workflow\n\n"
        b"Read `.opencode/skills/project-policy/SKILL.md` and use the installed skills.\n"
        b"Keep application files and validation commands project-owned. Inspect the existing\n"
        b"repository structure, inputs, and checks before implementing changes.\n"
        b"Workflow runtime/configuration live in `.astack/`, canonical skills in `.opencode/`,\n"
        b"and thin native discovery wrappers in `.agents/`, `.claude/`, and `.pi/`.\n"
        b"Keep one coherent change per branch and PR. Merge/publication need owner authority.\n"
    )
    if not agents.exists():
        desired["AGENTS.md"] = guidance
    desired[".astack/version"] = ("v" + workflow_version(root) + "\n").encode()
    inventory["runtime.version"] = {
        "path": ".astack/version",
        "sha256": _hash(desired[".astack/version"]),
    }
    actions = []
    writes = {}
    legacy_skills = _legacy_skill_ownership(target)
    for path, data in sorted(desired.items()):
        destination = _target_path(target, path)
        current = destination.read_bytes() if destination.is_file() else None
        component = next((key for key, entry in inventory.items() if entry["path"] == path), None)
        owner = owned.get(component) if component else None
        if owner is None and path in legacy_skills:
            owner = {"path": path, "sha256": legacy_skills[path]}
        if (
            current == data
            and isinstance(owner, dict)
            and owner.get("path") == path
            and owner.get("sha256") == _hash(current)
        ):
            action = "unchanged"
        elif current is None and not destination.exists():
            action = "create"
        elif (
            isinstance(owner, dict)
            and owner.get("path") == path
            and owner.get("sha256") == _hash(current or b"")
        ):
            action = "update"
        else:
            action = "conflict"
            conflicts.append(path)
        actions.append(
            {
                "path": path,
                "action": action,
                "current_sha256": _hash(current) if current is not None else None,
                "incoming_sha256": _hash(data),
            }
        )
        if action in {"create", "update"}:
            writes[path] = data
    # `inventory` is the whole catalog for every profile, so an owned component missing from it
    # was retired. Its file stays on disk untouched, but it is no longer managed: the audit only
    # accepts manifest entries the installed catalog knows.
    released_paths = {
        str(value.get("path")) if isinstance(value, dict) else str(key)
        for key, value in owned.items()
        if key not in inventory
    }
    claims = _retired_skill_claims(owned, inventory, legacy_skills)
    retired, kept = _classify_retired(target, claims)
    released = sorted((released_paths - set(claims)) | set(kept))
    for path, digest in sorted(retired.items()):
        actions.append(
            {"path": path, "action": "remove", "current_sha256": digest, "incoming_sha256": None}
        )
    for path, data in _legacy_manifest_updates(target).items():
        writes[path] = data
        actions.append(
            {
                "path": path,
                "action": "update",
                "current_sha256": _hash(_target_path(target, path).read_bytes()),
                "incoming_sha256": _hash(data),
            }
        )
    manifest = {
        "schema_version": 1,
        "profile": selected_profile,
        "workflow_version": workflow_version(root),
        "source": source,
        "managed_components": inventory,
    }
    manifest_data = _json(manifest)
    if not manifest_path.exists() or manifest_path.read_bytes() != manifest_data:
        writes[MANIFEST] = manifest_data
        actions.append(
            {
                "path": MANIFEST,
                "action": "update" if manifest_path.exists() else "create",
                "current_sha256": _hash(manifest_path.read_bytes())
                if manifest_path.exists()
                else None,
                "incoming_sha256": _hash(manifest_data),
            }
        )
    if state["status"]:
        conflicts.append(
            "Target Git worktree is dirty. Preserve and commit owner work before apply"
        )
    plan = {
        "schema_version": 1,
        "target": str(target),
        "profile": selected_profile,
        "source": source,
        "git": state,
        "actions": actions,
        "conflicts": conflicts,
        "released": released,
        "retired": sorted(retired),
        "preserved": (
            "Application source, README, existing instructions, CI, dependencies, "
            "lockfiles, data, and Git history"
        ),
        "limitations": ["Candidate mode is not published release evidence"] if candidate else [],
    }
    plan["plan_digest"] = _hash(_json(plan))
    return plan, writes


def _legacy_skill_ownership(target: Path) -> dict[str, str]:
    """Ownership recorded by `astack skills --dest <root> --apply` before skills were managed.

    A file it installed and nobody has edited since is still workflow-owned, so the first
    managed upgrade can take it over instead of reporting a conflict.
    """
    owned: dict[str, str] = {}
    for root in sorted(set(NATIVE_DISCOVERY_ROOTS.values())):
        try:
            path = _target_path(target, f"{root}/{SKILLS_MANIFEST}")
            record = json.loads(path.read_text()) if path.is_file() else {}
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        for relative, entry in record.items():
            if isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
                owned[f"{root}/{relative}"] = entry["sha256"]
    return owned


def _retired_skill_claims(
    owned: dict[str, Any], inventory: dict[str, Any], legacy: dict[str, str]
) -> dict[str, str]:
    """Recorded checksums for retired skill files astack itself installed."""
    claims: dict[str, str] = {}
    for key, value in owned.items():
        if key in inventory or not isinstance(value, dict):
            continue
        path, digest = value.get("path"), value.get("sha256")
        if (
            isinstance(path, str)
            and path in RETIRED_SKILL_PATHS
            and key == f"upstream:{path}"
            and isinstance(digest, str)
        ):
            claims[path] = digest
    for path, digest in legacy.items():
        if path in RETIRED_SKILL_PATHS:
            claims.setdefault(path, digest)
    return claims


def _retired_skill_unit(path: str) -> str:
    """Group retired paths by discovery root and skill folder."""
    return "/".join(path.split("/")[:3])


def _classify_retired(target: Path, claims: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Retire each skill folder as a unit; any edited or independent file keeps the whole copy."""
    units: dict[str, list[str]] = {}
    for path in sorted(RETIRED_SKILL_PATHS):
        units.setdefault(_retired_skill_unit(path), []).append(path)
    remove: dict[str, str] = {}
    keep: list[str] = []
    for unit_paths in units.values():
        unit_remove: dict[str, str] = {}
        preserve_unit = False
        for path in unit_paths:
            try:
                destination = _target_path(target, path)
            except ValueError:
                preserve_unit = True
                continue
            if not destination.exists():
                continue
            digest = claims.get(path)
            if (
                digest is not None
                and destination.is_file()
                and _hash(destination.read_bytes()) == digest
            ):
                unit_remove[path] = digest
            else:
                preserve_unit = True
        if preserve_unit:
            keep.extend(
                path for path in unit_paths if path in claims and _safe_exists(target, path)
            )
        else:
            remove.update(unit_remove)
    return remove, sorted(keep)


def _safe_exists(target: Path, path: str) -> bool:
    try:
        return _target_path(target, path).exists()
    except ValueError:
        return True


def _legacy_manifest_updates(target: Path) -> dict[str, bytes]:
    """Drop retired claims so a later independent install of the same bytes stays the owner's."""
    updates: dict[str, bytes] = {}
    for root in sorted(set(NATIVE_DISCOVERY_ROOTS.values())):
        relative = f"{root}/{SKILLS_MANIFEST}"
        try:
            path = _target_path(target, relative)
            record = json.loads(path.read_text()) if path.is_file() else {}
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        kept = {key: value for key, value in record.items() if key not in RETIRED_SKILL_FILES}
        if kept != record:
            updates[relative] = (json.dumps(kept, indent=2) + "\n").encode()
    return updates


def install_project(
    target: Path,
    *,
    release: str | None = None,
    candidate: bool = False,
    commands: list[list[str]] | None = None,
    initialize: bool = False,
    apply: bool = False,
    approve_plan: str | None = None,
) -> dict[str, Any]:
    plan, writes = _plan(
        target, release=release, candidate=candidate, commands=commands, initialize=initialize
    )
    if not apply:
        return {"status": "dry-run", **plan}
    if approve_plan != plan["plan_digest"]:
        raise ValueError("Apply requires the exact current dry-run plan digest")
    if plan["conflicts"]:
        raise ValueError("Installation conflicts block apply: " + "; ".join(plan["conflicts"]))
    return _apply_plan(plan, writes)


def _apply_plan(
    plan: dict[str, Any], writes: dict[str, bytes], *, purpose: str = "installation"
) -> dict[str, Any]:
    if purpose == "policy-transition":
        from .git_state import state_lock

        # Serialize against concurrent policy transitions and their recovery.
        with state_lock(Path(plan["target"]), "policy-transition"):
            return _apply_plan_locked(plan, writes, purpose=purpose)
    return _apply_plan_locked(plan, writes, purpose=purpose)


def _apply_plan_locked(
    plan: dict[str, Any], writes: dict[str, bytes], *, purpose: str
) -> dict[str, Any]:
    target = Path(plan["target"])
    target.mkdir(parents=True, exist_ok=True)
    if plan["git"]["initialize"]:
        subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    before = {}
    expected = {a["path"]: a["current_sha256"] for a in plan["actions"]}
    created_dirs = set()
    for path, data in writes.items():
        destination = _target_path(target, path)
        actual = _hash(destination.read_bytes()) if destination.is_file() else None
        if actual != expected[path]:
            raise ValueError("Target changed after dry-run validation: " + path)
        before[path] = {
            "bytes": base64.b64encode(destination.read_bytes()).decode()
            if destination.is_file()
            else None,
            "mode": destination.stat().st_mode & 0o777 if destination.exists() else None,
            "after_sha256": _hash(data),
        }
        parent = destination.parent
        while parent != target and not parent.exists():
            created_dirs.add(str(parent.relative_to(target)))
            parent = parent.parent
    removals = list(plan.get("retired", []))
    for path in removals:
        destination = _target_path(target, path)
        if destination.is_symlink() or not destination.is_file():
            raise ValueError("Target changed after dry-run validation: " + path)
        data = destination.read_bytes()
        if _hash(data) != expected[path]:
            raise ValueError("Target changed after dry-run validation: " + path)
        before[path] = {
            "bytes": base64.b64encode(data).decode(),
            "mode": destination.stat().st_mode & 0o777,
            "after_sha256": None,
        }
    journal = _target_path(target, TRANSACTION)
    journal.parent.mkdir(parents=True, exist_ok=True)
    lock = _target_path(target, ".astack/install.lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("Another or interrupted installer holds the installation lock") from exc
    journal_started = False
    try:
        os.write(lock_fd, str(os.getpid()).encode())
        # Revalidate after acquiring the lock, before owning any file side effect.
        for relative in before:
            destination = _target_path(target, relative)
            actual = _hash(destination.read_bytes()) if destination.is_file() else None
            if actual != expected[relative] or destination.exists() and not destination.is_file():
                raise ValueError("Target changed at installation lock boundary: " + relative)
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=target,
            capture_output=True,
            text=True,
            check=False,
        )
        current_head = head.stdout.strip() if head.returncode == 0 else None
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=target,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if current_head != plan["git"]["head"] or any(
            line != "?? .astack/install.lock" for line in status
        ):
            raise ValueError("Target Git state changed at installation lock boundary")
        if purpose == "policy-transition" and _policy_state(target) != plan["state"]:
            raise ValueError("Active worktrees changed at the policy transition lock boundary")
        _atomic_write(
            journal,
            _json(
                {
                    "schema_version": 1,
                    "purpose": purpose,
                    "policy_state": plan.get("state") if purpose == "policy-transition" else None,
                    "plan_digest": plan["plan_digest"],
                    "files": before,
                    "created_dirs": sorted(created_dirs),
                }
            ),
        )
        journal_started = True
        # Marker and manifest are finalized only after every other managed byte is verified.
        final = [p for p in (".astack/version", MANIFEST) if p in writes]
        executables = executable_targets(repository_root())

        def write(path: str) -> None:
            destination = _target_path(target, path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_install_file(destination, writes[path], expected[path])
            if destination.read_bytes() != writes[path]:
                raise RuntimeError("Installed bytes did not match the approved payload")
            if path in executables:
                destination.chmod(0o755)

        for path in writes:
            if path not in final:
                write(path)
        for path in removals:
            destination = _target_path(target, path)
            if (
                destination.is_symlink()
                or not destination.is_file()
                or _hash(destination.read_bytes()) != expected[path]
            ):
                raise RuntimeError("Preserving independently changed file: " + path)
            destination.unlink()
        for path in final:
            write(path)
    except BaseException:
        if journal_started:
            _restore(target, before, created_dirs)
            journal.unlink(missing_ok=True)
        raise
    finally:
        os.close(lock_fd)
        lock.unlink(missing_ok=True)
        _remove_empty_dirs(target, created_dirs)
    journal.unlink()
    # Only directories left empty go. Anything the owner added beside a retired skill stays.
    _remove_empty_dirs(
        target,
        {
            str(parent)
            for path in removals
            for parent in Path(path).parents
            if len(parent.parts) > 2
        },
    )
    return {"status": "applied", **plan}


def _remove_empty_dirs(target: Path, directories: set[str]) -> None:
    for relative in sorted(directories, key=lambda p: len(Path(p).parts), reverse=True):
        try:
            _target_path(target, relative).rmdir()
        except OSError:
            pass


def _write_install_file(path: Path, data: bytes, expected_sha256: str | None) -> None:
    # The installer lock coordinates installers, not independent owner edits.
    if path.is_symlink() or path.exists() and not path.is_file():
        raise RuntimeError("Preserving independently changed path type: " + str(path))
    current = _hash(path.read_bytes()) if path.is_file() else None
    if current != expected_sha256:
        raise RuntimeError("Preserving independently changed file before write: " + str(path))
    if expected_sha256 is not None:
        _atomic_write(path, data)
        return
    # Publish complete new bytes without replacing a file created after the check above.
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore(target: Path, files: dict[str, Any], directories: set[str]) -> None:
    preserved = []
    for relative, snapshot in reversed(list(files.items())):
        try:
            path = _target_path(target, relative)
        except ValueError:
            preserved.append(relative)
            continue
        current = _hash(path.read_bytes()) if path.is_file() else None
        prior = (
            _hash(base64.b64decode(snapshot["bytes"], validate=True))
            if snapshot["bytes"] is not None
            else None
        )
        if path.exists() and not path.is_file() or current not in {prior, snapshot["after_sha256"]}:
            # Restore other transaction-owned bytes, but keep this conflict and its journal.
            preserved.append(relative)
            continue
        if snapshot["bytes"] is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, base64.b64decode(snapshot["bytes"], validate=True))
            path.chmod(snapshot["mode"])
    if preserved:
        raise RuntimeError(
            "Preserving independently changed paths during rollback: " + ", ".join(preserved)
        )


def recover_installation(
    target: Path, *, apply: bool = False, approve_plan: str | None = None
) -> dict[str, Any]:
    target = target.resolve()
    journal = _target_path(target, TRANSACTION)
    lock = _target_path(target, ".astack/install.lock")
    if journal.exists() and not journal.is_file():
        raise ValueError("Interrupted installation journal must be a regular file")
    if journal.exists():
        record = json.loads(journal.read_text())
    elif lock.exists():
        record = {"schema_version": 1, "files": {}, "created_dirs": []}
    else:
        raise ValueError("No interrupted installation to recover")
    if record.get("schema_version") != 1 or not isinstance(record.get("files"), dict):
        raise ValueError("Invalid interrupted installation evidence")
    conflicts = []
    lock = _target_path(target, ".astack/install.lock")
    if lock.exists():
        try:
            pid = int(lock.read_text())
            if pid <= 0:
                raise ValueError("Invalid installer owner PID")
            if sys.platform == "win32":
                raise PermissionError("Installer liveness is unverified on Windows")
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        except (ValueError, PermissionError):
            conflicts.append("Installer lock owner is unknown")
        else:
            conflicts.append("Installer process is still alive")
    allowed = (
        set(_payload(repository_root())[0])
        | {
            MANIFEST,
            ".astack/project.toml",
            ".astack/version",
            "AGENTS.md",
            "CLAUDE.md",  # Journals written by installs that still created CLAUDE.md.
        }
        | RETIRED_MANAGED_PATHS
        | RETIRED_SKILL_PATHS
        | {f"{root}/{SKILLS_MANIFEST}" for root in NATIVE_DISCOVERY_ROOTS.values()}
    )
    for relative, snapshot in record["files"].items():
        # Recovery cannot turn a forged journal into writes to arbitrary application paths.
        if relative not in allowed:
            raise ValueError("Recovery path is outside workflow ownership")
        if not isinstance(snapshot, dict):
            raise ValueError("Invalid recovery file snapshot")
        # A null result records a retired skill copy removed by this transaction.
        removed = (
            snapshot.get("after_sha256", "") is None
            and relative in RETIRED_SKILL_PATHS
            and isinstance(snapshot.get("bytes"), str)
        )
        if (
            not removed
            and not re.fullmatch("[0-9a-f]{64}", str(snapshot.get("after_sha256", "")))
            or snapshot.get("bytes") is not None
            and not isinstance(snapshot.get("bytes"), str)
        ):
            raise ValueError("Invalid recovery file snapshot")
        if snapshot["bytes"] is not None and (
            type(snapshot.get("mode")) is not int or not 0 <= snapshot["mode"] <= 0o777
        ):
            raise ValueError("Invalid recovery file mode")
        if (
            relative in {"AGENTS.md", "CLAUDE.md"}
            or relative == ".astack/project.toml"
            and record.get("purpose") != "policy-transition"
        ) and snapshot["bytes"] is not None:
            raise ValueError("Recovery cannot replace owner-owned instructions or configuration")
        path = _target_path(target, relative)
        if path.exists() and not path.is_file():
            conflicts.append(relative)
            continue
        current = _hash(path.read_bytes()) if path.is_file() else None
        prior = (
            _hash(base64.b64decode(snapshot["bytes"], validate=True))
            if snapshot["bytes"] is not None
            else None
        )
        if current not in {None, prior, snapshot["after_sha256"]}:
            conflicts.append(relative)
    policy_state = None
    if record.get("purpose") == "policy-transition":
        policy_state = _policy_state(target)
        if policy_state != record.get("policy_state") or policy_state["active_worktrees"]:
            conflicts.append("Active worktrees changed since the policy transaction")
    directories_value = record.get("created_dirs", [])
    allowed_directories = {
        str(parent)
        for relative in allowed
        for parent in Path(relative).parents
        if str(parent) != "."
    }
    if not isinstance(directories_value, list) or any(
        not isinstance(d, str) or d not in allowed_directories for d in directories_value
    ):
        raise ValueError("Recovery directory is outside workflow ownership")
    plan = {
        "status": "dry-run",
        "target": str(target),
        "remove_empty_directories": sorted(directories_value),
        "restore": sorted(record["files"]),
        "conflicts": conflicts,
        "journal_sha256": _hash(journal.read_bytes()) if journal.exists() else None,
        "lock_sha256": _hash(lock.read_bytes()) if lock.exists() else None,
        "policy_state": policy_state,
    }
    plan["plan_digest"] = _hash(_json(plan))
    if not apply:
        return plan
    if approve_plan != plan["plan_digest"] or conflicts:
        raise ValueError("Recovery requires the exact conflict-free current plan")
    directories = set(record.get("created_dirs", []))
    if record.get("purpose") == "policy-transition":
        from .git_state import state_lock

        with state_lock(target, "policy-transition"):
            if _policy_state(target) != policy_state:
                raise ValueError("Active worktree obligations changed before policy recovery")
            _restore(target, record["files"], directories)
            journal.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
    else:
        _restore(target, record["files"], directories)
        journal.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    _remove_empty_dirs(target, directories)
    return {**plan, "status": "recovered"}


SCIENCE_CAPABILITIES = {"scientific-review", "scientific-validation"}


def _policy_state(target: Path) -> dict[str, Any]:
    from .git_state import list_worktrees

    worktrees = list_worktrees(target)
    active = [
        {key: item.get(key) for key in ("path", "branch", "head", "dirty")}
        for item in worktrees
        if not item["canonical"] and item["exists"]
    ]
    config = project_config(target)
    scientific = [
        key for key in ("scientific_outputs", "output_contract") if config.get(key) is not None
    ]
    if (target / "data/sources.yml").exists():
        scientific.append("data/sources.yml")
    review = config.get("review", {})
    if not isinstance(review, dict):
        raise ValueError("Scientific review policy is malformed")
    if review.get("scientific") or review.get("scientific_paths"):
        scientific.append("review policy")
    return {
        "digest": _hash(_json({"active_worktrees": active})),
        "active_worktrees": active,
        "scientific_obligations": scientific,
        "historical_evidence": "Preserved in place, never relabeled or removed",
    }


def _policy_text(text: str, profile: str, capabilities: list[str]) -> bytes:
    # Edit only recognized root policy fields. Preserve owner tables, comments and checks.
    boundary = re.search(r"^\s*\[", text, re.M)
    stop = boundary.start() if boundary else len(text)
    prefix, tables = text[:stop], text[stop:]
    for key in ("profile", "capabilities"):
        pattern = rf"^{key}\s*=.*$"
        matches = re.findall(pattern, prefix, re.M)
        if key == "capabilities" and matches:
            # Multiline arrays are valid TOML, but require owner normalization before this edit.
            try:
                tomllib.loads(matches[0])
            except tomllib.TOMLDecodeError as exc:
                raise ValueError(
                    "Normalize multiline capabilities before a policy transition"
                ) from exc
        prefix = re.sub(pattern + r"\n?", "", prefix, flags=re.M)
    result = (
        "profile = "
        + json.dumps(profile)
        + "\ncapabilities = "
        + json.dumps(capabilities)
        + "\n"
        + prefix
        + tables
    ).encode()
    tomllib.loads(result.decode())
    return result


def transition_project_policy(
    target: Path,
    *,
    profile: str,
    capabilities: list[str],
    release: str | None = None,
    candidate: bool = False,
    apply: bool = False,
    approve_plan: str | None = None,
) -> dict[str, Any]:
    if (
        profile not in PROFILES
        or len(capabilities) != len(set(capabilities))
        or set(capabilities) - SCIENCE_CAPABILITIES
    ):
        raise ValueError(
            "Choose General/DS and supported scientific-review/scientific-validation capabilities"
        )
    target = target.expanduser().resolve()
    if not (target / MANIFEST).is_file() or not (target / ".astack/project.toml").is_file():
        raise ValueError("Policy transitions require an existing verified installation")
    plan, writes = _plan(
        target, release=release, candidate=candidate, commands=None, initialize=False
    )
    installed = json.loads((target / MANIFEST).read_text())
    config = project_config(target)
    current_profile = project_profile(target)
    current_caps = config.get("capabilities", [])
    if not isinstance(current_caps, list) or any(not isinstance(c, str) for c in current_caps):
        raise ValueError("Invalid installed capability configuration")
    if set(current_caps) - SCIENCE_CAPABILITIES:
        raise ValueError(
            "Tooling capability changes are unsupported here. Preserve the existing configuration."
        )
    # The runtime is upgraded separately, then reused byte-for-byte by every policy.
    if (
        installed.get("workflow_version") != workflow_version(repository_root())
        or set(writes) - {MANIFEST}
        or plan.get("retired")
    ):
        plan["conflicts"].append(
            "Upgrade managed runtime bytes to this exact source before changing policy"
        )
    if installed.get("source") != plan["source"]:
        plan["conflicts"].append("Policy transition must use the exact installed source identity")
    state = _policy_state(target)
    if state["active_worktrees"]:
        plan["conflicts"].append(
            "Active linked worktrees must finish before changing canonical policy"
        )
    requested = sorted(capabilities)
    if profile == "data-science" and not SCIENCE_CAPABILITIES.issubset(requested):
        plan["conflicts"].append(
            "Data Science policy requires scientific review and validation capabilities"
        )
    declared_validation = project_config(target).get("validation", {})
    verify_declared = isinstance(declared_validation, dict) and "verify" in declared_validation
    if "scientific-validation" in requested and verify_declared:
        try:
            validation_commands(target, "verify")
        except ValueError as exc:
            plan["conflicts"].append(f"Fix the declared validation.verify argv: {exc}")
    reducing = set(current_caps) - set(requested)
    if reducing and state["scientific_obligations"]:
        plan["conflicts"].append(
            "Scientific obligations remain active. Preserve capabilities and resolve obligations "
            "before a safety reduction"
        )
    config_path = ".astack/project.toml"
    original = (target / config_path).read_bytes()
    incoming = _policy_text(original.decode(), profile, requested)
    changed = current_profile != profile or set(current_caps) != set(requested)
    writes = {config_path: incoming} if changed else {}
    manifest = dict(installed)
    manifest["profile"] = profile
    manifest["installation_layout"] = "general"
    if changed:
        writes[MANIFEST] = _json(manifest)
    plan.update(
        {
            "operation": "policy-transition",
            "profile": profile,
            "current_policy": {"profile": current_profile, "capabilities": sorted(current_caps)},
            "proposed_policy": {"profile": profile, "capabilities": requested},
            "state": state,
            "validation": {
                "check": validation_commands(target, "check"),
                "scientific_verify": project_config(target).get("validation", {}).get("verify"),
                "claim": "Apply verifies policy bytes, not scientific correctness or completed QA",
            },
            "safety_reduction": bool(reducing),
            "actions": [
                {
                    "path": path,
                    "action": "update",
                    "current_sha256": _hash((target / path).read_bytes()),
                    "incoming_sha256": _hash(data),
                }
                for path, data in sorted(writes.items())
            ],
            "preserved": (
                "Shared runtime, application files, linked worktrees, receipts, "
                "provenance and historical evidence"
            ),
            "limitations": [
                "Multiline capability edits are unsupported",
                "Active linked worktrees block policy changes",
                "Policy does not supply scientific methods or verification commands",
                *(
                    [
                        "validation.verify is not declared. VERIFY is reported as not run, "
                        "and strict ship-ready fails once analysis.stage is material"
                    ]
                    if "scientific-validation" in requested and not verify_declared
                    else []
                ),
            ],
        }
    )
    plan.pop("plan_digest", None)
    plan["plan_digest"] = _hash(_json(plan))
    if not apply:
        return {"status": "dry-run", **plan}
    if approve_plan != plan["plan_digest"] or plan["conflicts"]:
        raise ValueError("Policy transition requires the exact conflict-free current plan")
    return _apply_plan(plan, writes, purpose="policy-transition")
