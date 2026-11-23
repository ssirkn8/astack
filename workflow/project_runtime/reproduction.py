from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    ACQUISITION_DIR_ENV,
    COMPONENT_LIFECYCLES,
    COMPONENT_PATTERN,
    DVC_CONTROL_PATHS,
    DVC_HASH_PATTERN,
    DVC_PIPELINE_FIELDS,
    ENVIRONMENT_PROBE,
    MACHINE_OUTPUT_SUFFIXES,
    RANDOM_SEED_ENV,
    REPRODUCTION_ENGINES,
    RUN_RECORD_SCHEMA_VERSION,
    RUNTIME_SETTING_ENV,
    UTC,
    UV_RUN_SAFE_FLAGS,
    VALIDATION_TIERS,
    Any,
    Path,
    PathSnapshot,
    _cached_sha256,
    _clone_or_copy,
    _copy_exclusive,
    _load_digest_cache,
    _open_regular_file,
    _run,
    _safe_managed_path,
    _same_inode,
    _save_digest_cache,
    _sha256,
    _unlink_if_same,
    canonical_checkout,
    datetime,
    dvc_cache_root,
    errno,
    hashlib,
    json,
    list_worktrees,
    os,
    project_profile,
    re,
    run_project_validation,
    signal,
    stat,
    state_path,
    subprocess,
    sys,
    tempfile,
    yaml,
)
from .dependency_check import _ignored_by_project, check_ignore_patterns, run_checked_dvc_stages
from .pipeline import _ensure_dvc_cache, _read_dvc_config_value, _safe_pipeline_path
from .sources import in_place_inputs_identity, in_place_root_environment
from .validation import (
    _classify_output_path,
    _output_policy,
    _project_config,
    _validation_commands,
)


def _reproduction_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("reproduction", {})
    return value if isinstance(value, dict) else {}


def _reproduction_engine(root: Path, config: dict[str, Any]) -> str:
    reproduction = _reproduction_config(config)
    engine = reproduction.get("engine")
    if isinstance(engine, str) and engine:
        return engine
    tools = config.get("tools", [])
    if isinstance(tools, list) and "dvc" in tools and (root / "dvc.yaml").is_file():
        if "command" not in reproduction:
            return "dvc"
    return "command"


def _dvc_entry_path(entry: object) -> object:
    """A dvc.yaml output is a path, or a one-key map from the path to options like cache."""
    if not isinstance(entry, dict):
        return entry
    if "path" in entry:
        return entry["path"]
    return next(iter(entry)) if len(entry) == 1 else None


def _dvc_pipeline_outputs(root: Path) -> tuple[list[str], list[str]]:
    path = root / "dvc.yaml"
    if not path.exists():
        return [], ["dvc.yaml is missing"]
    if yaml is None:
        return [], ["PyYAML is unavailable"]
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [], [f"dvc.yaml is invalid: {exc}"]
    if not isinstance(value, dict) or not isinstance(value.get("stages"), dict):
        return [], ["dvc.yaml must contain a stages mapping"]
    stages = value["stages"]
    if not stages:
        return [], ["dvc.yaml must declare at least one stage"]
    outputs: list[str] = []
    errors: list[str] = []
    for stage_name, stage in stages.items():
        if not isinstance(stage_name, str) or not isinstance(stage, dict):
            errors.append("stage names and definitions must be mappings")
            continue
        for field in DVC_PIPELINE_FIELDS:
            entries = stage.get(field, [])
            if isinstance(entries, dict):
                entries = list(entries.values())
            if not isinstance(entries, list):
                errors.append(f"stage {stage_name!r} field {field!r} must be a list")
                continue
            for entry in entries:
                candidate = _dvc_entry_path(entry)
                relative = _safe_pipeline_path(candidate)
                if relative is None:
                    errors.append(f"stage {stage_name!r} has an unsafe {field} path")
                else:
                    outputs.append(relative)
    return sorted(set(outputs)), errors


def _dvc_command(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    _ensure_dvc_cache(root)
    return _run(["uv", "run", "--locked", "dvc", *arguments], cwd=root)


def _dvc_reproduction_targets(config: dict[str, Any]) -> list[str] | None:
    reproduction = _reproduction_config(config)
    targets = reproduction.get("targets", [])
    if not isinstance(targets, list) or not all(
        isinstance(target, str) and target and not target.startswith("-") for target in targets
    ):
        return None
    return targets


def _reproduction_command(root: Path, config: dict[str, Any]) -> list[str] | None:
    reproduction = _reproduction_config(config)
    if _reproduction_engine(root, config) == "dvc":
        targets = _dvc_reproduction_targets(config)
        if targets is None:
            return None
        return ["uv", "run", "--locked", "dvc", "repro", *targets]
    command = reproduction.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        return None
    return command


def _acquisition_staged(config: dict[str, Any]) -> bool | None:
    """Acquisition is always staged. None means a removed in-place declaration remains.

    `staged = true` is accepted for projects written while staging was opt-in.
    """
    acquisition = config.get("acquisition", {})
    if not isinstance(acquisition, dict):
        return None
    return True if acquisition.get("staged", True) is True else None


def _acquisition_command(config: dict[str, Any]) -> list[str] | None:
    acquisition = config.get("acquisition", {})
    if not isinstance(acquisition, dict):
        return None
    command = acquisition.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        return None
    return command


def _reproduction_state(root: Path) -> dict[str, Any]:
    commit = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if commit.returncode != 0:
        raise RuntimeError("Reproduction requires a committed Git revision")
    sources_path = root / "data/sources.yml"
    lock_path = root / "uv.lock"
    version_path = root / ".astack/version"
    version = None
    if version_path.exists():
        with _open_regular_file(version_path) as handle:
            version = handle.read().decode("utf-8").strip().lstrip("v")
    state: dict[str, Any] = {
        "git_commit": commit.stdout.strip(),
        "sources_manifest_fingerprint": (
            _sha256(sources_path, regular_only=True) if sources_path.exists() else None
        ),
        "inputs": _raw_inputs_identity(root),
        "config_fingerprint": _sha256(root / ".astack/project.toml", regular_only=True),
        "lockfile_fingerprint": _sha256(lock_path, regular_only=True)
        if lock_path.exists()
        else None,
        "python_version": sys.version.split()[0],
        "workflow_version": version,
    }
    # Present only when in-place sources exist, so records from snapshot-only projects stay current.
    in_place = in_place_inputs_identity(root)
    if in_place:
        state["in_place_inputs"] = in_place
    return state


def _raw_inputs_identity(root: Path) -> dict[str, Any]:
    """Identify the raw bytes a run consumed, using cached per-file digests."""
    digest = hashlib.sha256()
    files = 0
    size = 0
    raw = root / "data/raw"
    for path in _raw_paths(raw):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(metadata.st_mode) and path != raw:
            # A link's pathname is not byte identity: hash what it points to, or refuse.
            if not path.is_file():
                raise RuntimeError(
                    f"Raw input {relative} is a symlink to a directory or missing target; "
                    "link the whole data/raw root instead or copy the files"
                )
            target = os.readlink(path)
            digest.update(f"{relative}\0link:{target}\0{_cached_sha256(path)}\0".encode())
            files += 1
            size += path.stat().st_size
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(f"{relative}\0{_cached_sha256(path)}\0".encode())
            files += 1
            size += metadata.st_size
    return {"files": files, "bytes": size, "sha256": digest.hexdigest()}


def _random_seed(config: dict[str, Any]) -> int | None:
    seed = _reproduction_config(config).get("random_seed")
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RuntimeError("reproduction.random_seed must be an integer")
    return int(seed)


def _probe_launcher(command: list[str] | None) -> list[str] | None:
    """Return how to start the Python interpreter the command itself runs, or None if unknown.

    Parsing is deliberately conservative: an interpreter flag other than `-m`, or a `uv run`
    option outside a small set that takes no value and does not change the environment, makes
    the environment unknown rather than guessed.
    """
    if command is None:
        return [sys.executable]

    def python_launcher(prefix: list[str], arguments: list[str]) -> list[str] | None:
        first = arguments[0] if arguments else ""
        if first.startswith("-") and first != "-m":
            return None
        return prefix

    program = Path(command[0]).name
    if re.fullmatch(r"python(3(\.\d+)?)?", program):
        return python_launcher([command[0]], command[1:])
    if program == "uv" and command[1:2] == ["run"]:
        for index in range(2, len(command)):
            if command[index] in UV_RUN_SAFE_FLAGS:
                continue
            if command[index].startswith("-"):
                return None
            target = Path(command[index]).name
            prefix = [*command[:index], "python"]
            if re.fullmatch(r"python(3(\.\d+)?)?", target):
                return python_launcher(prefix, command[index + 1 :])
            return prefix
    return None


def _environment_manifest(root: Path, command: list[str] | None = None) -> dict[str, Any]:
    """Capture the command's actual installed environment; never invent missing parts."""
    launcher = _probe_launcher(command)
    if launcher is None:
        return {
            "status": "unavailable",
            "reason": "the reproduction command does not start a recognized Python interpreter",
        }
    try:
        probe = subprocess.run(
            [*launcher, "-P", "-c", ENVIRONMENT_PROBE],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        value = json.loads(probe.stdout) if probe.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__}
    if not isinstance(value, dict):
        return {"status": "unavailable", "reason": f"probe exited {probe.returncode}"}
    settings = {name: os.environ[name] for name in RUNTIME_SETTING_ENV if name in os.environ}
    return {"status": "captured", "launcher": launcher, **value, "settings": settings}


def _reproduction_path_fingerprint(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        if relative.rstrip("/") == "data/raw" and path.is_dir():
            digest = hashlib.sha256()
            for item in [path, *sorted(path.rglob("*"))]:
                metadata = item.lstat()
                digest.update(item.relative_to(root).as_posix().encode())
                digest.update(
                    f"\0{metadata.st_mode}\0{metadata.st_size}\0{metadata.st_mtime_ns}\0".encode()
                )
                if item.is_symlink():
                    digest.update(os.readlink(item).encode())
                elif item.is_file():
                    digest.update(_cached_sha256(item).encode())
                digest.update(b"\0")
            return f"raw-metadata:{digest.hexdigest()}"
        metadata = path.lstat()
        kind = "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file"
        target = os.readlink(path) if path.is_symlink() else ""
        return f"{kind}:{metadata.st_mode}:{metadata.st_size}:{metadata.st_mtime_ns}:{target}"
    except OSError:
        return None


def _tooling_path(relative: str) -> bool:
    """The virtual environment and bytecode caches, which running Python rewrites as it imports.

    The environment manifest records the interpreter's packages separately.
    """
    parts = relative.rstrip("/").split("/")
    return parts[0] == ".venv" or "__pycache__" in parts


def _reproduction_git_state(root: Path) -> dict[str, tuple[str, str | None]]:
    result = _run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored=matching",
            "-z",
        ],
        cwd=root,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not capture Git worktree state")
    fields = result.stdout.split("\0")
    state: dict[str, tuple[str, str | None]] = {}
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2] != " ":
            raise RuntimeError("Could not parse Git worktree state")
        status = entry[:2]
        paths = [entry[3:]]
        if status[0] in {"R", "C"}:
            if index >= len(fields) or not fields[index]:
                raise RuntimeError("Could not parse Git rename state")
            paths.append(fields[index])
            index += 1
        for relative in paths:
            path = root / relative
            if _tooling_path(relative):
                continue
            if relative.rstrip("/") == "data/raw" and path.is_dir() and not path.is_symlink():
                expanded = [relative]
            elif path.is_dir() and not path.is_symlink():
                expanded = [child.relative_to(root).as_posix() for child in sorted(path.rglob("*"))]
            else:
                expanded = [relative]
            for expanded_relative in expanded:
                if _tooling_path(expanded_relative):
                    continue
                state[expanded_relative] = (
                    status,
                    _reproduction_path_fingerprint(root, expanded_relative),
                )
    return state


def _reproduction_output_allowed(root: Path, relative: str, config: dict[str, Any]) -> bool:
    try:
        _safe_managed_path(root, relative)
    except ValueError:
        return False
    if relative in DVC_CONTROL_PATHS or relative.startswith(".dvc/"):
        return _reproduction_engine(root, config) == "dvc"
    classification = _classify_output_path(relative)
    lifecycle = classification["lifecycle"]
    component_layout, declared = _output_policy(config)
    if (
        component_layout
        and lifecycle in {"figure", "table", "report"}
        and Path(relative).suffix.casefold() in MACHINE_OUTPUT_SUFFIXES
    ):
        return False
    if lifecycle == "legacy-evidence":
        return not component_layout
    if lifecycle == "scratch":
        return True
    if lifecycle == "routine":
        return True
    if lifecycle not in COMPONENT_LIFECYCLES.values():
        return False
    if not component_layout:
        return True
    component = classification["component"]
    return not classification["problem"] and component is not None and component in declared


def _record_matches_state(root: Path, record: object, config: dict[str, Any]) -> bool:
    """Return whether a run record describes the current committed inputs and configuration."""
    try:
        current = _reproduction_state(root)
    except RuntimeError:
        return False
    if not isinstance(record, dict) or record.get("schema_version") != RUN_RECORD_SCHEMA_VERSION:
        return False
    expected_command = _reproduction_command(root, config)
    if record.get("command") != expected_command:
        return False
    expected_engine = _reproduction_engine(root, config)
    if record.get("engine", expected_engine) != expected_engine:
        return False
    required = {
        "git_commit",
        "sources_manifest_fingerprint",
        "config_fingerprint",
        "lockfile_fingerprint",
        "python_version",
        "timestamp",
        "workflow_version",
    }
    if any(not record.get(field) for field in required):
        return False
    recorded_commit = str(record["git_commit"])
    if not re.fullmatch(r"[0-9a-f]{40,64}", recorded_commit):
        return False
    for field in ("sources_manifest_fingerprint", "config_fingerprint", "lockfile_fingerprint"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(record[field])):
            return False
    inputs = record.get("inputs")
    if not isinstance(inputs, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(inputs.get("sha256"))):
        return False
    for field, value in current.items():
        if field != "git_commit" and record.get(field) != value:
            return False
    if not isinstance(record.get("outputs"), dict) or not record["outputs"]:
        return False
    verification = record.get("verification")
    if not isinstance(verification, dict) or verification.get("status") not in (
        "verified",
        "unverified",
    ):
        return False
    if recorded_commit == current["git_commit"]:
        return True
    ancestor = _run(["git", "merge-base", "--is-ancestor", recorded_commit, "HEAD"], cwd=root)
    if ancestor.returncode != 0:
        return False
    changed = _run(["git", "diff", "--name-only", f"{recorded_commit}..HEAD"], cwd=root)
    if changed.returncode != 0:
        return False
    return all(
        (
            relative.startswith("reports/qa/runs/")
            or (relative.startswith("evidence/") and "/runs/" in relative)
        )
        for relative in changed.stdout.splitlines()
        if relative
    )


def _load_run_record(path: Path) -> dict[str, Any] | None:
    try:
        with _open_regular_file(path) as handle:
            record = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def _outputs_match_record(root: Path, record: dict[str, Any]) -> bool:
    """Deleted or altered results must not keep being supported by an older record."""
    outputs = record.get("outputs")
    if not isinstance(outputs, dict):
        return False
    try:
        return all(
            isinstance(expected, dict) and _output_digest(root, relative) == expected
            for relative, expected in outputs.items()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _valid_run_record(root: Path, path: Path, config: dict[str, Any]) -> bool:
    record = _load_run_record(path)
    return (
        record is not None
        and _record_matches_state(root, record, config)
        and _outputs_match_record(root, record)
    )


def _verified_run_record(root: Path, path: Path, config: dict[str, Any]) -> bool:
    record = _load_run_record(path)
    if record is None or not _valid_run_record(root, path, config):
        return False
    verification = record["verification"]
    reference = verification.get("reference")
    if verification.get("status") != "verified" or not isinstance(reference, str):
        return False
    try:
        reference_path = _safe_managed_path(root, reference)
    except ValueError:
        return False
    if not reference_path.is_file() or _sha256(reference_path) != verification.get(
        "reference_sha256"
    ):
        return False
    # The reference must itself still be a valid promoted record for this state.
    reference_record = _load_run_record(reference_path)
    return reference_record is not None and _record_matches_state(root, reference_record, config)


def _reproduction_outputs(root: Path, config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return declared reproduction outputs and configuration errors."""
    reproduction = _reproduction_config(config)
    declared = reproduction.get("outputs")
    if declared is None and _reproduction_engine(root, config) == "dvc":
        declared, errors = _dvc_pipeline_outputs(root)
        if errors:
            return [], errors
    if not isinstance(declared, list) or not declared:
        return [], [
            "material analysis must declare reproduction.outputs as a non-empty list of paths"
        ]
    outputs: list[str] = []
    errors = []
    for value in declared:
        relative = _safe_pipeline_path(value)
        if relative is None or not _reproduction_output_allowed(root, relative, config):
            errors.append(f"reproduction output is unsafe or not an allowed output path: {value}")
        elif _classify_output_path(relative)["lifecycle"] in {"raw", "routine", "scratch"}:
            errors.append(f"reproduction output must be a durable derived product: {value}")
        else:
            outputs.append(relative)
    tolerance = reproduction.get("tolerance", {})
    if not isinstance(tolerance, dict):
        errors.append("reproduction.tolerance must map output paths to command arrays")
    else:
        for relative, command in tolerance.items():
            if relative not in outputs:
                errors.append(f"reproduction.tolerance names an undeclared output: {relative}")
            elif (
                not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
            ):
                errors.append(f"reproduction.tolerance for {relative} must be a command array")
    return sorted(set(outputs)), errors


def _output_digest(root: Path, relative: str) -> dict[str, str] | None:
    path = _safe_managed_path(root, relative)
    if path.is_symlink():
        raise RuntimeError(f"Reproduction output must not be a symlink: {relative}")
    if path.is_file():
        return {"kind": "file", "sha256": _cached_sha256(path)}
    if path.is_dir():
        return {"kind": "tree-v2", "sha256": _output_tree_sha256(path)}
    return None


def _output_stamp(root: Path, relative: str) -> dict[str, tuple[int, int, int]] | None:
    """Return per-file stamps for a declared output, so every retained file is checked."""
    path = root / relative
    if not path.exists() and not path.is_symlink():
        return None
    items = sorted(path.rglob("*")) if path.is_dir() else [path]
    stamps = {}
    for item in items:
        metadata = item.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            stamps[item.relative_to(root).as_posix()] = (
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
    return stamps


def _stale_output_files(
    before: dict[str, tuple[int, int, int]] | None,
    after: dict[str, tuple[int, int, int]] | None,
) -> list[str]:
    """A retained file whose inode, size, and mtime are all unchanged was not rewritten."""
    if not before or not after:
        return []
    return sorted(name for name, stamp in before.items() if after.get(name) == stamp)


def _output_tree_sha256(directory: Path) -> str:
    """Unambiguous tree digest: each entry frames its path with NUL and a fixed-length hash."""
    digest = hashlib.sha256(b"astack-output-tree-v2\0")
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RuntimeError(f"Reproduction output tree contains a non-regular file: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode() + b"\0" + _cached_sha256(path).encode() + b"\n")
    return digest.hexdigest()


def _reference_record(
    root: Path, config: dict[str, Any], component_layout: bool, component: object
) -> tuple[Path, dict[str, Any]] | None:
    """Return the most recent promoted record that describes the current state."""
    durable = _durable_record_root(root, component_layout, component)
    for path in sorted(durable.glob("*.json"), reverse=True):
        record = _load_run_record(path)
        if record is not None and _record_matches_state(root, record, config):
            return path, record
    return None


def _compare_outputs(
    root: Path,
    config: dict[str, Any],
    outputs: dict[str, dict[str, str]],
    reference: tuple[Path, dict[str, Any]] | None,
) -> dict[str, Any]:
    if reference is None:
        return {
            "status": "unverified",
            "reason": (
                "no promoted reference record matches the current state; run "
                "`reproduce --promote`, commit the record, then rerun to verify"
            ),
        }
    reference_path, reference_record = reference
    reference_outputs = reference_record.get("outputs", {})
    tolerance = _reproduction_config(config).get("tolerance", {})
    methods: dict[str, str] = {}
    mismatches: list[dict[str, Any]] = []
    before = _reproduction_git_state(root)
    for relative, digest in sorted(outputs.items()):
        expected = reference_outputs.get(relative) if isinstance(reference_outputs, dict) else None
        if expected == digest:
            methods[relative] = "digest"
            continue
        command = tolerance.get(relative) if isinstance(tolerance, dict) else None
        if isinstance(expected, dict) and isinstance(command, list):
            environment = os.environ | {
                "ASTACK_OUTPUT": relative,
                "ASTACK_REFERENCE_RECORD": str(reference_path),
                "ASTACK_REFERENCE_SHA256": str(expected.get("sha256", "")),
            }
            checked = subprocess.run(
                command, cwd=root, env=environment, check=False, capture_output=True, text=True
            )
            if checked.returncode == 0:
                methods[relative] = "project-tolerance"
                continue
            mismatches.append(
                {
                    "path": relative,
                    "expected": expected,
                    "actual": digest,
                    "tolerance_check": command,
                    "detail": (checked.stderr or checked.stdout).strip()[-500:],
                }
            )
            continue
        mismatches.append({"path": relative, "expected": expected, "actual": digest})
    if _reproduction_git_state(root) != before:
        raise RuntimeError("A reproduction tolerance check changed the worktree")
    return {
        "status": "mismatch" if mismatches else "verified",
        "reference": reference_path.relative_to(root).as_posix(),
        "reference_sha256": _sha256(reference_path),
        "methods": methods,
        "mismatches": mismatches,
    }


def _snapshot_path(
    root: Path,
    path: Path,
    *,
    backup_root: Path | None = None,
) -> PathSnapshot:
    metadata = path.lstat()
    relative = path.relative_to(root).as_posix()
    backup = None
    target = None
    target_is_directory = False
    target_mode = None
    if stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
        target = os.readlink(path)
        target_is_directory = path.is_dir()
        if path.exists():
            target_mode = path.stat().st_mode & 0o777
        digest = None
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
        digest = _cached_sha256(path)
        if backup_root is not None:
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            _clone_or_copy(path, backup)
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
        digest = None
    else:
        kind = "other"
        digest = None
    return PathSnapshot(
        digest=digest,
        mode=metadata.st_mode & 0o777,
        kind=kind,
        target=target,
        target_is_directory=target_is_directory,
        target_mode=target_mode,
        backup=backup,
    )


def _raw_paths(raw: Path) -> list[Path]:
    paths = [raw] if raw.exists() or raw.is_symlink() else []
    if raw.is_dir():
        for current, directories, files in os.walk(raw, followlinks=False):
            directories.sort()
            files.sort()
            paths.extend(Path(current) / name for name in [*directories, *files])
    return paths


def _raw_snapshot(root: Path) -> dict[str, PathSnapshot]:
    """Metadata and digests of every raw path. Staged acquisition never copies raw data."""
    return {
        path.relative_to(root).as_posix(): _snapshot_path(root, path)
        for path in _raw_paths(root / "data/raw")
    }


def _snapshot_paths(
    root: Path, relatives: set[str], *, backup_root: Path
) -> dict[str, PathSnapshot]:
    snapshots: dict[str, PathSnapshot] = {}
    for relative in sorted(relatives):
        path = root / relative
        if path.exists() or path.is_symlink():
            snapshots[relative] = _snapshot_path(root, path, backup_root=backup_root)
    return snapshots


def _tracked_paths(root: Path) -> set[str]:
    result = _run(["git", "ls-files", "-z"], cwd=root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not list tracked worktree paths")
    return {relative for relative in result.stdout.split("\0") if relative}


def _same_snapshot(left: PathSnapshot | None, right: PathSnapshot | None) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.digest,
        left.mode,
        left.kind,
        left.target,
        left.target_is_directory,
        left.target_mode,
    ) == (
        right.digest,
        right.mode,
        right.kind,
        right.target,
        right.target_is_directory,
        right.target_mode,
    )


def _restore_snapshot_path(root: Path, relative: str, state: PathSnapshot) -> None:
    """Restore one snapshot without deleting anything: an existing path raises FileExistsError."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if state.kind == "file":
        if state.backup is None:
            raise RuntimeError("File snapshot backup is unavailable")
        _clone_or_copy(state.backup, path)
        path.chmod(state.mode)
    elif state.kind == "directory":
        if path.is_symlink() or path.exists():
            raise FileExistsError(errno.EEXIST, "Path exists", str(path))
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(state.mode)
    elif state.kind == "symlink":
        if state.target is None:
            raise RuntimeError("Symlink snapshot target is unavailable")
        path.symlink_to(state.target, target_is_directory=state.target_is_directory)
    else:
        raise RuntimeError(f"Cannot restore unsupported path type: {relative}")


def _restore_worktree_changes(
    root: Path,
    changed: set[str],
    snapshots: dict[str, PathSnapshot],
    protected: set[tuple[int, int]],
    kept: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    """Undo worktree changes, never removing an original raw inode.

    Kept paths and cleanup errors are appended to `kept` and `errors` as they happen.
    """
    kept = [] if kept is None else kept
    errors = [] if errors is None else errors
    for relative in sorted(changed, key=lambda value: len(Path(value).parts), reverse=True):
        state = snapshots.get(relative)
        path = root / relative
        # Every deletion goes through the inode check. Restores only create, never replace,
        # so a path that reappears after the check is left in place and reported.
        if path.is_symlink() or path.exists():
            held, failed = len(kept), len(errors)
            _remove_unprotected(path, protected, kept, errors)
            if len(kept) > held or len(errors) > failed:
                continue
        if state is not None:
            try:
                _restore_snapshot_path(root, relative, state)
            except FileExistsError:
                errors.append(f"{path}: reappeared during rollback and was left in place")
            except (OSError, RuntimeError) as exc:
                errors.append(f"{path}: {exc}")


def _raw_identities(root: Path) -> set[tuple[int, int]]:
    """Device and inode of every raw file and directory, from metadata only."""
    raw = root / "data/raw"
    identities: set[tuple[int, int]] = set()
    if not (raw.exists() or raw.is_symlink()):
        return identities

    def unreadable(error: OSError) -> None:
        raise RuntimeError(
            "Staged acquisition must read all of data/raw to protect it, but cannot list "
            f"{error.filename}. Make raw directories readable (chmod -R a+rX data/raw)."
        ) from error

    metadata = os.lstat(raw)
    identities.add((metadata.st_dev, metadata.st_ino))
    for current, directories, files in os.walk(raw, onerror=unreadable, followlinks=False):
        for name in [*directories, *files]:
            metadata = os.lstat(Path(current) / name)
            identities.add((metadata.st_dev, metadata.st_ino))
    if raw.is_symlink() and raw.is_dir():
        metadata = os.stat(raw)
        identities.add((metadata.st_dev, metadata.st_ino))
    return identities


def _remove_unprotected(
    path: Path, protected: set[tuple[int, int]], kept: list[str], errors: list[str]
) -> None:
    """Delete a tree, keeping every entry that is the last name of an original raw inode.

    Never raises for a filesystem error. Kept paths and errors are appended as they happen, so a
    later failure cannot hide where an original was kept. A directory holding a kept or
    undeletable entry is kept too. Nothing is followed through a symlink.
    """

    def visit(current: Path) -> bool:
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return True
        except OSError as exc:
            errors.append(f"{current}: {exc}")
            return False
        if (metadata.st_dev, metadata.st_ino) in protected and (
            stat.S_ISDIR(metadata.st_mode) or metadata.st_nlink <= 1
        ):
            kept.append(str(current))
            return False
        try:
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    current.chmod(stat.S_IMODE(metadata.st_mode) | 0o700)
                except OSError:
                    pass
                removable = True
                for child in sorted(current.iterdir()):
                    removable = visit(child) and removable
                if not removable:
                    return False
                current.rmdir()
            else:
                # A protected name with another link may go: the bytes survive elsewhere, and each
                # name is checked again, so the last one is kept.
                current.unlink()
        except FileNotFoundError:
            return True
        except OSError as exc:
            errors.append(f"{current}: {exc}")
            return False
        return True

    visit(path)


def _acquisition_change_allowed(relative: str) -> bool:
    return (
        relative == "data/raw"
        or relative.startswith("data/raw/")
        or relative == "data/sources.yml"
        or relative.startswith("data/source-verification/")
        or relative == ".gitignore"
        or relative.startswith(".dvc/")
        or relative.endswith(".dvc")
        or relative.startswith(".cache/")
    )


def _is_raw_path(relative: str) -> bool:
    return relative == "data/raw" or relative.startswith("data/raw/")


def run_acquisition(root: Path) -> dict[str, Any]:
    _load_digest_cache(root)
    # Staged mode fills this in: raw inodes the cache must not replace, and whether raw
    # is still unverified.
    cache_guard: dict[str, Any] = {}
    try:
        return _run_acquisition(root, cache_guard)
    finally:
        if not cache_guard.get("unverified"):
            _save_digest_cache(root, cache_guard.get("protected"))


def _acquisition_scratch_candidates(root: Path) -> list[Path]:
    """Where scratch may go, best first. Same device as data/raw first, so promotion links.

    Candidates are the state directory beside the Git metadata and a hidden directory beside raw's
    real location. A candidate that resolves inside the tracked worktree is never used, because
    scratch there would count as a prohibited worktree change. Git metadata is allowed.
    """
    raw = root / "data/raw"
    worktree = root.resolve()
    named: list[Path] = []
    try:
        named.append(Path(state_path(Path(canonical_checkout(root)), "acquisition")))
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        raw_device: int | None = os.stat(raw).st_dev
        named.append(Path(os.path.realpath(raw)).parent / ".astack-acquisition")
    except OSError:
        raw_device = None
    same: list[Path] = []
    other: list[Path] = []
    for candidate in named:
        if candidate.is_symlink():
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            resolved = candidate.resolve(strict=True)
            device = os.stat(resolved).st_dev
        except OSError:
            continue
        if resolved.is_relative_to(worktree):
            inside = resolved.relative_to(worktree).parts
            if not inside or inside[0] != ".git":
                continue
        (same if raw_device is None or device == raw_device else other).append(resolved)
    return [*same, *other, Path(tempfile.gettempdir())]


def _acquisition_work_directory(root: Path) -> Path:
    """The preferred scratch parent. Creation still falls through to later candidates."""
    return _acquisition_scratch_candidates(root)[0]


def _create_acquisition_scratch(root: Path) -> tempfile.TemporaryDirectory[str]:
    """Create a private scratch directory in the first candidate that accepts one."""
    failures: list[str] = []
    for candidate in _acquisition_scratch_candidates(root):
        try:
            return tempfile.TemporaryDirectory(
                prefix="astack-acquisition-", dir=candidate, delete=False
            )
        except OSError as exc:
            failures.append(f"{candidate}: {exc}")
    raise RuntimeError("Could not create acquisition scratch: " + "; ".join(failures))


def _acquisition_worktree_rollback(
    root: Path,
    before: dict[str, tuple[str, str | None]],
    before_worktree: dict[str, PathSnapshot],
    protected: set[tuple[int, int]],
    kept: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    try:
        after = _reproduction_git_state(root)
    except RuntimeError:
        after = before
    changed = {
        relative
        for relative in before.keys() | after.keys()
        if before.get(relative) != after.get(relative) and not _is_raw_path(relative)
    }
    _restore_worktree_changes(root, changed, before_worktree, protected, kept, errors)


def _prohibited_acquisition_changes(
    root: Path, before: dict[str, tuple[str, str | None]]
) -> list[str]:
    after = _reproduction_git_state(root)
    return sorted(
        relative
        for relative in before.keys() | after.keys()
        if before.get(relative) != after.get(relative) and not _acquisition_change_allowed(relative)
    )


def _run_acquisition(root: Path, cache_guard: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _project_config(root)
    command = _acquisition_command(config)
    if command is None:
        raise RuntimeError("Acquisition requires [acquisition].command as a non-empty string array")
    if _acquisition_staged(config) is None:
        raise RuntimeError(
            "In-place acquisition was removed. Remove [acquisition].staged and have the command "
            f"write new inputs to ${ACQUISITION_DIR_ENV} instead of data/raw. "
            "See docs/migrations/staged-acquisition.md."
        )
    status = _run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root)
    if status.returncode != 0 or status.stdout.strip():
        raise RuntimeError("Acquisition requires a clean Git worktree")
    return _run_staged_acquisition(root, command, {} if cache_guard is None else cache_guard)


def _writable_raw_paths(snapshot: dict[str, PathSnapshot]) -> list[str]:
    writable = []
    for relative, state in sorted(snapshot.items()):
        mode = state.mode
        if relative == "data/raw" and state.kind == "symlink":
            if state.target_mode is None:
                continue
            mode = state.target_mode
        elif state.kind not in {"file", "directory"}:
            continue
        if mode & 0o222:
            writable.append(relative)
    return writable


class AcquisitionLaunchError(OSError):
    """The acquisition command could not be started, so it changed nothing."""


def _read_output(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _raw_anchor(root: Path) -> tuple[object, ...]:
    """Identify where data/raw lives: the project, data and raw inodes and raw's real target.

    Equal snapshots alone are not enough. A command could move the tree and leave a link that
    shows the same names and bytes, so cleanup would delete the only copy.
    """
    identities: list[object] = []
    for path in (root, root / "data", root / "data/raw"):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            identities.append(None)
            continue
        identities.append((metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)))
    raw = root / "data/raw"
    try:
        target = os.stat(raw)
        identities.append((os.path.realpath(raw), target.st_dev, target.st_ino))
    except FileNotFoundError:
        identities.append(None)
    return tuple(identities)


def _run_in_own_session(
    command: list[str], *, cwd: Path, env: dict[str, str], scratch: Path
) -> subprocess.CompletedProcess[str]:
    """Run a command, then stop anything it left running before astack touches its output.

    Output goes to files, not pipes, so a surviving background process cannot hold the
    acquisition open. A process that starts its own session escapes this cleanup.
    """
    stdout_path = scratch / "stdout"
    stderr_path = scratch / "stderr"
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise AcquisitionLaunchError(str(exc)) from exc
        try:
            returncode = process.wait()
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if process.poll() is None:
                process.kill()
                process.wait()
    return subprocess.CompletedProcess(
        command,
        returncode,
        _read_output(stdout_path),
        _read_output(stderr_path),
    )


def _run_staged_acquisition(
    root: Path, command: list[str], cache_guard: dict[str, Any]
) -> dict[str, Any]:
    """The command writes only into an empty staging directory. astack adds its files to raw.

    No copy of existing raw data is made, so existing raw inputs must already be read-only.
    astack then never needs to restore or delete anything in data/raw except paths it created.
    """
    before_raw = _raw_snapshot(root)
    linked = sorted(
        relative
        for relative, state in before_raw.items()
        if relative != "data/raw" and state.kind == "symlink"
    )
    if linked:
        # A nested link can point at a writable file outside raw that no snapshot protects.
        raise RuntimeError(
            "Staged acquisition requires raw inputs to be regular files, not symlinks. "
            "Use in-place acquisition, or replace these links with read-only files: "
            + ", ".join(linked[:10])
        )
    writable = _writable_raw_paths(before_raw)
    if writable:
        raise RuntimeError(
            "Staged acquisition keeps no copy of data/raw, so existing raw inputs must be "
            "read-only first. Run `chmod -R a-w data/raw`. Writable: " + ", ".join(writable[:10])
        )
    # Cleanup never deletes these, so a raw input moved anywhere astack cleans is kept.
    protected = _raw_identities(root)
    cache_guard["protected"] = protected
    held: list[str] = []
    cleanup_errors: list[str] = []

    def rollback() -> None:
        try:
            _acquisition_worktree_rollback(
                root, before, before_worktree, protected, held, cleanup_errors
            )
        except (OSError, RuntimeError) as exc:
            cleanup_errors.append(f"worktree rollback: {exc}")

    holder = _create_acquisition_scratch(root)
    scratch = Path(holder.name)
    keep_scratch = False
    try:
        staging = scratch / "staged"
        staging.mkdir()
        before = _reproduction_git_state(root)
        worktree_paths = _tracked_paths(root) | {
            relative for relative in before if not _is_raw_path(relative)
        }
        before_worktree = _snapshot_paths(root, worktree_paths, backup_root=scratch / "worktree")
        environment = {**os.environ, ACQUISITION_DIR_ENV: str(staging)}
        anchor = _raw_anchor(root)
        # From here until raw is shown intact, an error must not delete scratch or replace the
        # digest cache, either of which could hold a relocated original.
        keep_scratch = True
        cache_guard["unverified"] = True
        try:
            command_result = _run_in_own_session(
                command, cwd=root, env=environment, scratch=scratch
            )
        except AcquisitionLaunchError:
            keep_scratch = False
            cache_guard["unverified"] = False
            rollback()
            raise
        after_raw = _raw_snapshot(root)
        changed = sorted(
            path
            for path in before_raw.keys() | after_raw.keys()
            if not _same_snapshot(before_raw.get(path), after_raw.get(path))
        )
        if _raw_anchor(root) != anchor:
            changed.insert(0, "the location of data/raw")
        if changed:
            # Raw no longer matches, so originals may have been moved anywhere, including into
            # scratch. Deleting anything could destroy the only copy, so nothing is removed.
            raise RuntimeError(
                "Staged acquisition must not write to data/raw, but the command changed: "
                + ", ".join(changed[:20])
                + f". astack deleted nothing and kept its scratch directory at {scratch}, which "
                "may hold relocated raw inputs. Restore data/raw before acquiring again."
            )
        keep_scratch = False
        cache_guard["unverified"] = False
        if command_result.returncode != 0:
            rollback()
            raise RuntimeError(command_result.stderr.strip() or "Acquisition command failed")
        prohibited = _prohibited_acquisition_changes(root, before)
        if prohibited:
            rollback()
            raise RuntimeError(
                "Acquisition command made prohibited worktree changes: " + ", ".join(prohibited)
            )
        try:
            added = _promote_staged_inputs(root, staging, protected)
        except (OSError, RuntimeError):
            rollback()
            raise
        result = {
            "status": "acquired",
            "mode": "staged",
            "command": command,
            "added_raw_inputs": sorted(added),
            "added_raw_sha256": {
                relative: _cached_sha256(root / relative)
                for relative in sorted(added)
                if (root / relative).is_file()
            },
        }
    except BaseException as exc:
        if not keep_scratch:
            _remove_unprotected(scratch, protected, held, cleanup_errors)
        if held:
            exc.add_note(
                "astack kept these paths because they are original raw inputs: "
                + ", ".join(held[:20])
            )
        if cleanup_errors:
            exc.add_note("Cleanup could not remove: " + "; ".join(cleanup_errors[:20]))
        raise
    _remove_unprotected(scratch, protected, held, cleanup_errors)
    if held:
        failure = RuntimeError(
            "The inputs were added: "
            + ", ".join(result["added_raw_inputs"])
            + ". But original raw inputs were found outside data/raw and kept in place: "
            + ", ".join(held[:20])
        )
        if cleanup_errors:
            failure.add_note("Cleanup could not remove: " + "; ".join(cleanup_errors[:20]))
        raise failure
    if cleanup_errors:
        result["cleanup_errors"] = cleanup_errors
    return result


def _staged_entries(staging: Path) -> list[Path]:
    entries: list[Path] = []
    for current, directories, files in os.walk(staging, followlinks=False):
        directories.sort()
        files.sort()
        for name in [*directories, *files]:
            path = Path(current) / name
            metadata = path.lstat()
            relative = path.relative_to(staging).as_posix()
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise RuntimeError(
                    "Staged inputs must be regular files or directories: " + relative
                )
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                # A hard link shares its inode, so promoting it would alias another file.
                raise RuntimeError("Staged inputs must not be hard links: " + relative)
            entries.append(path)
    return entries


def _promote_staged_inputs(
    root: Path, staging: Path, protected: set[tuple[int, int]] | None = None
) -> list[str]:
    """Add staged files to data/raw, read-only. Refuse any existing path before moving anything.

    Every created path is recorded by inode as soon as it exists. On failure, cleanup removes a
    path only while it is still that inode, so an original raw input is never deleted.
    """
    raw = root / "data/raw"
    entries = _staged_entries(staging)
    conflicts = []
    for path in entries:
        target = raw / path.relative_to(staging)
        if not (target.is_symlink() or target.exists()):
            continue
        if path.is_dir() and target.is_dir() and not target.is_symlink():
            continue
        conflicts.append(target.relative_to(root).as_posix())
    if conflicts:
        raise RuntimeError(
            "Staged acquisition would replace existing raw inputs: " + ", ".join(conflicts)
        )
    if not entries:
        return []
    created: list[tuple[Path, os.stat_result]] = []
    unlocked: list[tuple[Path, int]] = []
    failure: BaseException | None = None
    try:
        if not (raw.is_symlink() or raw.exists()):
            raw.mkdir(parents=True, mode=0o755)
            created.append((raw, os.lstat(raw)))
        parents = sorted(
            {
                (raw / path.relative_to(staging)).parent
                for path in entries
                if (raw / path.relative_to(staging)).parent.is_dir()
            }
        )
        for parent in parents:
            mode = parent.stat().st_mode & 0o7777
            parent.chmod(mode | 0o700)
            unlocked.append((parent, mode))
        for path in entries:
            target = raw / path.relative_to(staging)
            if path.is_dir():
                if not target.is_dir():
                    target.mkdir(mode=0o755)
                    created.append((target, os.lstat(target)))
                continue
            _place_staged_file(path, target, created)
            target.chmod(path.stat().st_mode & 0o777 & ~0o222)
    except BaseException as exc:
        failure = exc
    restore_errors: list[str] = []
    preserved: list[str] = []
    if failure is not None:
        for target, identity in reversed(created):
            try:
                if not _same_inode(target, identity):
                    if target.exists() or target.is_symlink():
                        preserved.append(target.relative_to(root).as_posix())
                    continue
                current = os.lstat(target)
                if (
                    protected is not None
                    and (current.st_dev, current.st_ino) in protected
                    and (stat.S_ISDIR(current.st_mode) or current.st_nlink <= 1)
                ):
                    # An original raw inode moved over a staged source: this is its last name.
                    preserved.append(target.relative_to(root).as_posix())
                    continue
                if stat.S_ISDIR(identity.st_mode):
                    target.rmdir()
                else:
                    _unlink_if_same(target, identity)
            except OSError:
                preserved.append(target.relative_to(root).as_posix())
    else:
        for target, identity in created:
            if stat.S_ISDIR(identity.st_mode) and _same_inode(target, identity):
                try:
                    target.chmod(0o555)
                except OSError as exc:
                    restore_errors.append(f"{target.relative_to(root).as_posix()}: {exc}")
    for parent, mode in reversed(unlocked):
        try:
            parent.chmod(mode)
        except OSError as exc:
            restore_errors.append(f"{parent.relative_to(root).as_posix()}: {exc}")
    notes = []
    if restore_errors:
        notes.append("Could not restore raw directory permissions: " + "; ".join(restore_errors))
    if preserved:
        notes.append(
            "Left in place because astack could not prove it created them: " + ", ".join(preserved)
        )
    if failure is not None:
        for note in notes:
            failure.add_note(note)
        raise failure
    added = [target.relative_to(root).as_posix() for target, _ in created]
    if notes:
        raise RuntimeError(" ".join([*notes, "The inputs were added: " + ", ".join(added)]))
    return added


def _place_staged_file(
    source: Path, target: Path, created: list[tuple[Path, os.stat_result]]
) -> None:
    """Hard-link or copy one staged file into a new path. Never replaces an existing file."""
    identity = source.lstat()
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        _copy_exclusive(source, target, lambda made: created.append((target, made)))
        return
    created.append((target, identity))


def run_dvc_status(root: Path) -> dict[str, Any]:
    result = _dvc_command(root, ["status"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "DVC status failed")
    canonical = Path(canonical_checkout(root))
    return {
        "status": "ok",
        "cache": str(Path(dvc_cache_root(canonical))),
        "output": result.stdout,
    }


def _dvc_metadata_hashes(value: object) -> set[str]:
    hashes: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"md5", "etag", "checksum", "hash"} and isinstance(nested, str):
                if DVC_HASH_PATTERN.fullmatch(nested):
                    hashes.add(nested)
            hashes.update(_dvc_metadata_hashes(nested))
    elif isinstance(value, list):
        for nested in value:
            hashes.update(_dvc_metadata_hashes(nested))
    return hashes


def _dvc_hashes_from_text(text: str) -> set[str]:
    if yaml is None:
        return set()
    try:
        return _dvc_metadata_hashes(yaml.safe_load(text))
    except yaml.YAMLError:
        return set()


def _dvc_hashes_from_paths(root: Path, paths: list[Path]) -> set[str]:
    hashes: set[str] = set()
    for path in paths:
        try:
            hashes.update(_dvc_hashes_from_text(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    return hashes


def _dvc_worktree_metadata_paths(root: Path) -> list[Path]:
    paths = [root / "dvc.lock"]
    paths.extend(sorted(root.glob("**/*.dvc")))
    return [
        path
        for path in paths
        if path.is_file() and ".git" not in path.parts and ".dvc" not in path.parts[:-1]
    ]


def _dvc_git_metadata_hashes(root: Path) -> tuple[set[str], str | None]:
    commits = _run(["git", "rev-list", "--all"], cwd=root)
    if commits.returncode != 0:
        return set(), commits.stderr.strip() or "could not enumerate Git history"
    hashes: set[str] = set()
    for commit in commits.stdout.splitlines():
        paths = _run(["git", "ls-tree", "-r", "--name-only", commit], cwd=root)
        if paths.returncode != 0:
            return set(), paths.stderr.strip() or "could not enumerate Git tree"
        metadata = [
            path
            for path in paths.stdout.splitlines()
            if path == "dvc.lock" or path.endswith(".dvc")
        ]
        for path in metadata:
            value = _run(["git", "show", f"{commit}:{path}"], cwd=root)
            if value.returncode != 0:
                return set(), value.stderr.strip() or f"could not read {commit}:{path}"
            hashes.update(_dvc_hashes_from_text(value.stdout))
    return hashes, None


def _dvc_cache_object_path(cache: Path, digest: str) -> list[Path]:
    bases = [cache / "files" / "md5", cache / "md5", cache]
    return [
        candidate
        for base in bases
        for candidate in (
            base / digest,
            base / digest[:2] / digest[2:],
            base / f"{digest}.dir",
            base / digest[:2] / f"{digest[2:]}.dir",
        )
    ]


def _expand_dvc_directory_hashes(cache: Path, hashes: set[str]) -> set[str]:
    expanded = set(hashes)
    pending = list(hashes)
    while pending:
        digest = pending.pop()
        object_path = next(
            (path for path in _dvc_cache_object_path(cache, digest) if path.is_file()), None
        )
        if object_path is None:
            continue
        try:
            value = json.loads(object_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for nested in _dvc_metadata_hashes(value):
            if nested not in expanded:
                expanded.add(nested)
                pending.append(nested)
    return expanded


def _dvc_cache_configuration(root: Path) -> tuple[Path, str | None]:
    canonical = Path(canonical_checkout(root))
    expected = Path(dvc_cache_root(canonical))
    configured: Path | None = None
    for path in (root / ".dvc/config", root / ".dvc/config.local"):
        if not path.is_file():
            continue
        try:
            value = _read_dvc_config_value(path, "cache", "dir")
        except (OSError, RuntimeError) as exc:
            return expected, str(exc)
        if value:
            candidate = Path(value).expanduser()
            configured = (
                candidate if candidate.is_absolute() else path.parent / candidate
            ).resolve()
    if configured is not None and configured != expected:
        return expected, f"DVC cache {configured} differs from the repository cache {expected}"
    return expected, None


def _dvc_cache_inventory(cache: Path) -> tuple[list[dict[str, Any]], list[str]]:
    objects: list[dict[str, Any]] = []
    unknown: list[str] = []
    if not cache.is_dir():
        return objects, unknown
    seen: set[Path] = set()
    for path in sorted(cache.rglob("*")):
        if not path.is_file() or path in seen:
            continue
        seen.add(path)
        relative = path.relative_to(cache).as_posix()
        if path.name == ".gitignore":
            continue
        candidates = [path.name.removesuffix(".dir")]
        if len(path.parts) >= 2:
            candidates.append("".join(path.parts[-2:]).removesuffix(".dir"))
        digest = next((value for value in candidates if DVC_HASH_PATTERN.fullmatch(value)), None)
        if digest is None:
            unknown.append(relative)
            continue
        objects.append({"path": relative, "digest": digest, "bytes": path.stat().st_size})
    return objects, unknown


def _dvc_active_worktree_hashes(root: Path) -> tuple[set[str], list[str]]:
    hashes: set[str] = set()
    active: list[str] = []
    for record in list_worktrees(root):
        path = Path(str(record["path"]))
        if not path.exists():
            continue
        active.append(str(path))
        hashes.update(_dvc_hashes_from_paths(path, _dvc_worktree_metadata_paths(path)))
    return hashes, active


def run_dvc_cache_status(root: Path) -> dict[str, Any]:
    config = _project_config(root)
    tools = config.get("tools", [])
    if "dvc" not in tools or not (root / ".dvc/config").is_file():
        return {
            "status": "not-configured",
            "cache": None,
            "reason": "DVC is not configured for this project",
        }
    cache, configuration_error = _dvc_cache_configuration(root)
    if configuration_error:
        return {"status": "blocked", "cache": str(cache), "reason": configuration_error}
    if not cache.is_dir():
        return {
            "status": "blocked",
            "cache": str(cache),
            "cache_exists": False,
            "reason": "DVC cache is unavailable",
        }
    try:
        objects, unknown = _dvc_cache_inventory(cache)
    except OSError as exc:
        return {
            "status": "blocked",
            "cache": str(cache),
            "cache_exists": True,
            "reason": f"DVC cache could not be inspected: {exc}",
        }
    current_hashes = _dvc_hashes_from_paths(root, _dvc_worktree_metadata_paths(root))
    history_hashes, history_error = _dvc_git_metadata_hashes(root)
    active_hashes, active_worktrees = _dvc_active_worktree_hashes(root)
    protected = _expand_dvc_directory_hashes(cache, current_hashes | history_hashes | active_hashes)
    candidates = [value for value in objects if value["digest"] not in protected]
    status = "blocked" if history_error or unknown else "ok"
    reason = history_error or (
        f"unrecognized cache entries: {', '.join(unknown)}" if unknown else None
    )
    return {
        "status": status,
        "cache": str(cache),
        "cache_exists": cache.is_dir(),
        "object_count": len(objects),
        "cache_bytes": sum(int(value["bytes"]) for value in objects),
        "protected_objects": len(
            {value["digest"] for value in objects if value["digest"] in protected}
        ),
        "reclaimable_objects": len(candidates),
        "reclaimable_bytes": sum(int(value["bytes"]) for value in candidates),
        "candidates": candidates,
        "unknown_entries": unknown,
        "active_worktrees": sorted(set(active_worktrees)),
        "reason": reason,
    }


def run_dvc_cache_gc(root: Path, *, apply: bool = False) -> dict[str, Any]:
    status = run_dvc_cache_status(root)
    if status["status"] == "not-configured":
        return status | {"operation": "gc", "applied": False}
    if status["status"] != "ok":
        return status | {"operation": "gc", "applied": False, "reason": status["reason"]}
    canonical = Path(canonical_checkout(root))
    current = root.resolve()
    other_worktrees = [
        path
        for path in status["active_worktrees"]
        if Path(path).resolve() not in {canonical.resolve(), current}
    ]
    if apply and other_worktrees:
        return status | {
            "operation": "gc",
            "applied": False,
            "reason": "refusing cache deletion while linked worktrees are active",
            "blocking_worktrees": other_worktrees,
        }
    if apply:
        dirty = _run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root)
        if dirty.returncode != 0 or dirty.stdout.strip():
            return status | {
                "operation": "gc",
                "applied": False,
                "reason": "refusing cache deletion from a dirty project worktree",
            }
        removed: list[str] = []
        cache = Path(status["cache"])
        for value in status["candidates"]:
            path = cache / str(value["path"])
            if path.is_file():
                path.unlink()
                removed.append(str(value["path"]))
        return status | {"operation": "gc", "applied": True, "removed": removed}
    return status | {"operation": "gc", "applied": False, "dry_run": True}


def _validated_component(component: object) -> str:
    if not isinstance(component, str) or not COMPONENT_PATTERN.fullmatch(component):
        raise RuntimeError("Reproduction component must be a lowercase slug")
    return component


def _run_record_root(root: Path, component: object) -> Path:
    slug = _validated_component(component) if component is not None else "project"
    return _safe_managed_path(root, f".cache/runs/{slug}")


def _durable_record_root(root: Path, component_layout: bool, component: object) -> Path:
    if component_layout:
        slug = _validated_component(component)
        return _safe_managed_path(root, f"evidence/{slug}/runs")
    return _safe_managed_path(root, "reports/qa/runs")


def _record_filename(timestamp: str) -> str:
    return f"{timestamp.replace(':', '').replace('.', '-')}.json"


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_validation(root: Path, tier: str) -> dict[str, Any]:
    if project_profile(root) != "data-science" or tier == "check":
        return dict(run_project_validation(root, tier))
    if tier not in VALIDATION_TIERS:
        raise RuntimeError(f"Validation tier must be one of: {', '.join(VALIDATION_TIERS)}")
    config = _project_config(root)
    commands, errors = _validation_commands(config)
    if errors:
        raise RuntimeError("Invalid validation configuration: " + "; ".join(errors))
    configured = commands.get(tier)
    if configured is None:
        raise RuntimeError(f"Validation tier {tier!r} is not configured in .astack/project.toml")
    if tier == "release":
        status = _run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root)
        if status.returncode != 0 or status.stdout.strip():
            raise RuntimeError("RELEASE validation requires a clean Git worktree")
    for command in configured:
        try:
            result = _run(command, cwd=root)
        except OSError as exc:
            raise RuntimeError(
                f"{tier.upper()} validation command is unavailable: {' '.join(command)}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "command failed"
            raise RuntimeError(f"{tier.upper()} validation failed: {' '.join(command)}: {detail}")
    return {"status": "passed", "tier": tier, "commands": configured}


def run_reproduction(root: Path, *, promote: bool = False, verify: bool = False) -> dict[str, Any]:
    _load_digest_cache(root)
    try:
        return _run_reproduction(root, promote=promote, verify=verify)
    finally:
        _save_digest_cache(root)


def _run_reproduction(root: Path, *, promote: bool, verify: bool) -> dict[str, Any]:
    """Run the pipeline, digest declared outputs, and compare with the promoted reference.

    `command-succeeded` means the command ran and produced its declared outputs. Only
    `verified` means a rerun matched a promoted reference record.
    """
    del verify  # Verification is attempted on every run; the CLI enforces --verify.
    config = _project_config(root)
    analysis = config.get("analysis", {})
    if not isinstance(analysis, dict) or analysis.get("stage") != "material":
        raise RuntimeError("Reproduction is available only after analysis.stage is set to material")
    reproduction = _reproduction_config(config)
    engine = _reproduction_engine(root, config)
    command = _reproduction_command(root, config)
    component_layout, declared_components = _output_policy(config)
    component = reproduction.get("component")
    if engine not in REPRODUCTION_ENGINES:
        raise RuntimeError("Reproduction engine must be `command` or `dvc`")
    if command is None:
        raise RuntimeError("Configured reproduction engine has no valid command")
    if component_layout and component not in declared_components:
        raise RuntimeError("Material analysis requires a declared reproduction.component")
    record_root = _run_record_root(root, component)
    durable_root = _durable_record_root(root, component_layout, component)
    if engine == "dvc":
        _, pipeline_errors = _dvc_pipeline_outputs(root)
        if pipeline_errors:
            raise RuntimeError("Invalid DVC pipeline: " + "; ".join(pipeline_errors))
        _ensure_dvc_cache(root)
    seed = _random_seed(config)
    declared_outputs, output_errors = _reproduction_outputs(root, config)
    if output_errors:
        raise RuntimeError(
            "Reproduction requires declared outputs; declare reproduction.outputs: "
            + "; ".join(output_errors)
        )
    status = _run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root)
    if status.returncode != 0 or status.stdout.strip():
        raise RuntimeError("Reproduction requires a clean Git worktree")
    reference = _reference_record(root, config, component_layout, component)
    executed = list(command)
    if engine == "dvc" and reference is not None:
        # A verification run must recompute stages instead of reusing cached outputs.
        executed = [*command[:4], "repro", "--force", *command[5:]]
    # Probe first: starting the interpreter through `uv run` may sync the environment, which must
    # not be mistaken for a change made by the pipeline.
    environment = _environment_manifest(root, executed)
    before = _reproduction_git_state(root)
    state = _reproduction_state(root)
    stamps = {relative: _output_stamp(root, relative) for relative in declared_outputs}
    timestamp = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    run_environment = in_place_root_environment(root)
    if seed is not None:
        run_environment[RANDOM_SEED_ENV] = str(seed)
    dependency_check: dict[str, Any] | None = None
    if engine == "dvc":
        # Stage by stage, so each one's reads and writes can be checked against dvc.yaml.
        dependency_check = run_checked_dvc_stages(
            root,
            force=reference is not None,
            targets=_dvc_reproduction_targets(config) or [],
            environment=run_environment,
        )
    elif run_environment:
        command_result = _run(executed, cwd=root, env=os.environ | run_environment)
    else:
        command_result = _run(executed, cwd=root)
    if engine != "dvc" and command_result.returncode != 0:
        raise RuntimeError(command_result.stderr.strip() or "Reproduction command failed")
    after = _reproduction_git_state(root)
    changed = sorted(
        relative
        for relative in before.keys() | after.keys()
        if before.get(relative) != after.get(relative)
    )
    ignore = check_ignore_patterns(config)
    prohibited = [
        relative
        for relative in changed
        if not _reproduction_output_allowed(root, relative, config)
        and not _ignored_by_project(relative, ignore)
    ]
    if prohibited:
        raw_changes = [
            relative
            for relative in prohibited
            if relative == "data/raw" or relative.startswith("data/raw/")
        ]
        if raw_changes:
            raise RuntimeError("Reproduction command changed raw input: " + ", ".join(raw_changes))
        raise RuntimeError(
            "Reproduction command made prohibited worktree changes: " + ", ".join(prohibited)
        )
    if _reproduction_state(root) != state:
        raise RuntimeError("Reproduction command changed its recorded input or environment state")
    outputs: dict[str, dict[str, str]] = {}
    for relative in declared_outputs:
        digest = _output_digest(root, relative)
        if digest is None:
            raise RuntimeError(f"Reproduction did not produce declared output: {relative}")
        if engine == "command":
            stale = _stale_output_files(stamps[relative], _output_stamp(root, relative))
            if stale:
                raise RuntimeError(
                    f"Declared output was not written by this run: {', '.join(stale)}. "
                    "Make the pipeline regenerate it or remove the stale copy."
                )
        outputs[relative] = digest
    verification = _compare_outputs(root, config, outputs, reference)
    environment_fingerprint = hashlib.sha256(
        json.dumps(environment, sort_keys=True).encode()
    ).hexdigest()
    if reference is not None:
        # Informational: a verified rerun on a different environment is stronger evidence.
        reference_environment = reference[1].get("environment")
        both_captured = environment.get("status") == "captured" and (
            isinstance(reference_environment, dict)
            and reference_environment.get("status") == "captured"
        )
        verification["environment_matches_reference"] = (
            reference[1].get("environment_fingerprint") == environment_fingerprint
            if both_captured
            else "unknown"
        )
    record = {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "timestamp": timestamp,
        "command": command,
        "executed_command": executed,
        "engine": engine,
        "random_seed": seed,
        "rng": {
            "seed": seed,
            "delivered_as": f"environment:{RANDOM_SEED_ENV}" if seed is not None else None,
            "consumption": (
                "project-defined; astack delivers the seed but has not verified that the "
                "pipeline uses it. Repeatability is shown only by a verified rerun."
            ),
        },
        "environment": environment,
        "environment_fingerprint": environment_fingerprint,
        "outputs": outputs,
        "verification": verification,
        **state,
    }
    if dependency_check is not None:
        record["dependency_check"] = dependency_check
    filename = _record_filename(timestamp)
    output = record_root / filename
    _write_record(output, record)
    result_status = {
        "verified": "verified",
        "unverified": "command-succeeded",
        "mismatch": "mismatch",
    }[verification["status"]]
    result: dict[str, Any] = {
        "status": result_status,
        "record": str(output),
        "provenance": record,
    }
    if promote and result_status != "mismatch":
        durable = durable_root / filename
        _write_record(durable, record)
        result["promoted_record"] = str(durable)
    return result
