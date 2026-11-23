"""Verify pinned upstream skill bytes and copy them offline. No network operations.

Works from a source checkout and from an installed wheel, whose assets mirror the same paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

LOCK = Path("integrations/t3/upstream.lock.json")
MANIFEST = ".astack-upstream-skills.json"
# Files of skills astack used to pin. Upgrades remove a copy only while it still matches the
# checksum astack recorded, so edited or independently installed skills survive.
RETIRED_SKILL_FILES = frozenset(
    {
        "domain-modeling/ADR-FORMAT.md",
        "domain-modeling/GLOSSARY-FORMAT.md",
        "domain-modeling/SKILL.md",
        "domain-modeling/agents/openai.yaml",
        "grill-with-docs/SKILL.md",
        "grill-with-docs/agents/openai.yaml",
    }
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(p in {".", ".."} for p in parts):
        raise ValueError(f"Unsafe path: {relative}")
    path = root / relative
    # Include the destination itself and every existing ancestor, including above root.
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f"Refusing symlink: {relative}")
    if any(p.exists() and not p.is_dir() for p in path.parents):
        raise ValueError(f"Refusing non-directory ancestor: {relative}")
    return path


def payload(root: Path) -> dict[str, tuple[bytes, bool]]:
    lock = json.loads((root / LOCK).read_text())
    if lock["schema_version"] != 1:
        raise ValueError("Unsupported upstream lock schema")
    result = {}
    for source in lock["sources"]:
        commit = source["commit"]
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError("Source must be pinned to a full commit")
        for item in source["files"]:
            relative = item["path"]
            if relative.startswith(".agents/skills/"):
                target = relative.removeprefix(".agents/skills/")
                if target.split("/")[0] not in source["skills"]:
                    raise ValueError("Unexpected skill in lock")
            elif relative.startswith("integrations/t3/licenses/"):
                target = ".astack-notices/" + relative.removeprefix("integrations/t3/licenses/")
            else:
                raise ValueError("Unexpected source path in lock")
            source_path = safe_path(root, relative)
            data = source_path.read_bytes()
            if (
                digest(data) != item["sha256"]
                or bool(source_path.stat().st_mode & 0o111) != item["executable"]
            ):
                raise ValueError(f"Upstream integrity mismatch: {relative}")
            if target in result:
                raise ValueError(f"Duplicate skill path: {target}")
            result[target] = (data, item["executable"])
    return result


def _matches_recorded(path: Path, entry: dict) -> bool:
    return (
        path.is_file()
        and digest(path.read_bytes()) == entry["sha256"]
        and bool(path.stat().st_mode & 0o111) == entry["executable"]
    )


def install(root: Path, destination: Path, *, apply: bool = False) -> dict:
    """Preflight the whole copy before writing. Never replace an unknown or edited file."""
    files = payload(root)
    destination = destination.absolute()
    manifest_path = safe_path(destination, MANIFEST)
    prior = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if not isinstance(prior, dict) or any(
        not isinstance(k, str)
        or not isinstance(v, dict)
        or set(v) != {"sha256", "executable"}
        or not isinstance(v["sha256"], str)
        or not isinstance(v["executable"], bool)
        for k, v in prior.items()
    ):
        raise ValueError("Invalid installed skill ownership manifest")
    dropped = set(prior) - set(files)
    if dropped - RETIRED_SKILL_FILES:
        raise ValueError("Managed retirement requires explicit migration, not a skill update")
    retired_units: dict[str, list[str]] = {}
    for relative in sorted(RETIRED_SKILL_FILES):
        retired_units.setdefault(PurePosixPath(relative).parts[0], []).append(relative)
    retired: list[str] = []
    preserved: list[str] = []
    for unit_paths in retired_units.values():
        if not (set(unit_paths) & dropped):
            continue
        unit_retired: list[str] = []
        preserve_unit = False
        for relative in unit_paths:
            try:
                path = safe_path(destination, relative)
            except ValueError:
                preserve_unit = True
                continue
            if not path.exists():
                continue
            entry = prior.get(relative)
            if entry is not None and _matches_recorded(path, entry):
                unit_retired.append(relative)
            else:
                preserve_unit = True
        if preserve_unit:
            for relative in unit_paths:
                if relative not in dropped:
                    continue
                try:
                    exists = safe_path(destination, relative).exists()
                except ValueError:
                    exists = True
                if exists:
                    preserved.append(relative)
        else:
            retired.extend(unit_retired)
    actions = []
    for relative, (data, _executable) in files.items():
        path = safe_path(destination, relative)
        if path.exists():
            if not path.is_file():
                raise ValueError(f"Preserve non-file: {relative}")
            current = path.read_bytes()
            if relative not in prior or digest(current) not in {
                prior[relative]["sha256"],
                digest(data),
            }:
                raise ValueError(f"Preserve unknown or customized file: {relative}")
            if bool(path.stat().st_mode & 0o111) != prior[relative]["executable"]:
                raise ValueError(f"Preserve customized executable mode: {relative}")
            action = "unchanged" if current == data else "replace"
        else:
            action = "add"
        actions.append({"path": relative, "action": action})
    actions.extend({"path": relative, "action": "remove"} for relative in retired)
    if apply:
        # Manifest-last never claims completion for a partial copy. Only retired files that
        # still match their recorded checksum are removed.
        for relative, (data, executable) in files.items():
            path = safe_path(destination, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            try:
                temporary.chmod(0o755 if executable else 0o644)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        # Revalidate every retired file before unlinking any of them, and restore already-removed
        # files if a later check or unlink fails mid-update.
        snapshots: list[tuple[Path, str, bytes, int]] = []
        for relative in retired:
            path = safe_path(destination, relative)
            if not _matches_recorded(path, prior[relative]):
                raise ValueError(f"Retired skill file changed during update: {relative}")
            snapshots.append(
                (
                    path,
                    relative,
                    path.read_bytes(),
                    0o755 if prior[relative]["executable"] else 0o644,
                )
            )
        removed: list[tuple[str, bytes, int]] = []
        try:
            for path, relative, data, mode in snapshots:
                if not _matches_recorded(path, prior[relative]):
                    raise ValueError(f"Retired skill file changed during update: {relative}")
                path.unlink()
                removed.append((relative, data, mode))
        except Exception:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            for relative, data, mode in reversed(removed):
                try:
                    restore = safe_path(destination, relative)
                except ValueError:
                    continue
                restore.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(restore, flags, mode)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    os.fchmod(stream.fileno(), mode)
            raise
        for path, _relative, _data, _mode in snapshots:
            for parent in path.parents:
                if parent == destination:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
        manifest_path.write_text(
            json.dumps(
                {
                    p: {"sha256": digest(d), "executable": executable}
                    for p, (d, executable) in files.items()
                },
                indent=2,
            )
            + "\n"
        )
    return {
        "applied": apply,
        "files": actions,
        "skills": len(skill_names(root)),
        "preserved_retired": preserved,
    }


def skill_names(root: Path) -> list[str]:
    """Every pinned upstream skill, from the lock rather than a hand-kept count."""
    lock = json.loads((root / LOCK).read_text())
    return sorted(name for source in lock["sources"] for name in source["skills"])
