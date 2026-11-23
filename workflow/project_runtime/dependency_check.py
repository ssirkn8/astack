"""Run DVC stages one at a time and check what each one reads and writes against dvc.yaml."""

from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    BYTECODE_CACHE,
    COMPONENT_LIFECYCLES,
    DEPENDENCY_CHECK_LIMITS,
    DEPENDENCY_CHECK_PATH,
    DEPENDENCY_CHECK_VERSION,
    INPUT_LOCK_JOURNAL,
    SHELL_SYNTAX,
    SNAPSHOT_SKIPPED,
    TRACE_FILE_ENV,
    TRACE_SITECUSTOMIZE,
    Any,
    Path,
    _open_regular_file,
    _run,
    fnmatch,
    hashlib,
    json,
    os,
    shlex,
    shutil,
    stat,
    tempfile,
    yaml,
)
from .pipeline import _dvc_pipeline, _real_relative, _stage_edges, _stage_states
from .validation import _project_config


def _stage_order(stages: list[dict[str, Any]], upstream: dict[str, set[str]]) -> list[str]:
    """Stage names with every upstream stage first, ties broken by name."""
    names = sorted(str(stage["name"]) for stage in stages)
    remaining = {name: set(upstream.get(name, set())) & set(names) for name in names}
    order: list[str] = []
    while remaining:
        ready = sorted(name for name, parents in remaining.items() if not parents)
        if not ready:  # DVC refuses cycles, so this is only a safety net.
            ready = sorted(remaining)
        for name in ready:
            order.append(name)
            del remaining[name]
        for parents in remaining.values():
            parents.difference_update(ready)
    return order


def _tooling(relative: str) -> bool:
    """Top-level tooling folders and bytecode caches, which no stage reads or writes as data."""
    parts = relative.split("/")
    return parts[0] in SNAPSHOT_SKIPPED or BYTECODE_CACHE in parts


def _snapshot(root: Path, gaps: list[str] | None = None) -> dict[str, tuple[int, ...]]:
    """Every file and symlink in the project with its inode, size, mtime, ctime and mode.

    A program can restore a file's mtime but not its ctime, so any rewrite of a file that
    persists is seen. A file created and deleted within one stage cannot reach a later stage.
    """
    files: dict[str, tuple[int, ...]] = {}
    # Writes into a folder that cannot be inspected may still be possible (an ACL can deny reading
    # its metadata but allow writing), so each such folder is recorded in gaps, never skipped
    # silently, and is still walked where that works.
    raw_path = root / "data" / "raw"
    try:
        raw: Path | None = raw_path.resolve()
    except OSError:
        raw = None

    def unlisted(error: OSError) -> None:
        if gaps is None:
            return
        try:
            relative = Path(error.filename or root).relative_to(root).as_posix()
        except ValueError:
            relative = str(error.filename)
        if not _tooling(relative) and relative != "data/raw":
            gaps.append(f"{relative} (writes there could not be checked)")

    for current, directories, names in os.walk(root, onerror=unlisted, followlinks=False):
        here = Path(current)
        base = here.relative_to(root).as_posix()
        kept = []
        for name in sorted(directories):
            path = here / name
            relative = name if base == "." else f"{base}/{name}"
            try:
                if path.is_symlink():
                    names.append(name)
                    continue
                skip_raw = path == raw_path or (raw is not None and path.resolve() == raw)
            except OSError:
                if gaps is not None and not _tooling(relative):
                    gaps.append(f"{relative} (writes there could not be checked)")
                skip_raw = path == raw_path
            if not _tooling(relative) and not skip_raw:
                kept.append(name)
        directories[:] = kept
        for name in names:
            path = here / name
            relative = path.relative_to(root).as_posix()
            if _tooling(relative):
                continue
            try:
                metadata = path.lstat()
            except OSError:
                if gaps is not None:
                    gaps.append(f"{relative} (writes there could not be checked)")
                continue
            if gaps is not None and relative != "data/raw" and stat.S_ISLNK(metadata.st_mode):
                # The snapshot sees the link, not what it points at. A target inside the project
                # is snapshotted where it lives. One outside it, or unresolvable, is a gap.
                try:
                    outside = not path.resolve(strict=True).is_relative_to(root.resolve())
                except (OSError, RuntimeError):
                    outside = True
                if outside:
                    gaps.append(
                        f"{relative} (symlink out of the project; writes through it are not seen)"
                    )
            files[relative] = (
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                metadata.st_mode,
            )
    return files


def _ignored_change(relative: str) -> bool:
    """Files DVC itself writes while running a stage."""
    name = relative.rsplit("/", 1)[-1]
    return name in {"dvc.lock", ".gitignore"}


def _python_program(word: str, cwd: Path) -> bool:
    """Whether a command word starts Python: a python binary, or a script with a python shebang."""
    if Path(word).name.startswith("python"):
        return True
    found = shutil.which(word, path=os.environ.get("PATH")) if "/" not in word else None
    path = Path(found) if found else cwd / word
    try:
        with path.open("rb") as handle:
            first = handle.readline(256)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def _traceable(command: object, cwd: Path | None = None) -> bool:
    """Whether a stage command is one Python process, so all of its reads reach the hook."""
    if not isinstance(command, str) or any(token in command for token in SHELL_SYNTAX):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if words[:2] == ["uv", "run"]:
        words = words[2:]
        while words and words[0].startswith("-"):
            words = words[1:]
    if not words or "=" in words[0]:  # A leading VAR=value can drop PYTHONPATH.
        return False
    return _python_program(words[0], cwd or Path.cwd()) and not _disables_hook(words)


def _trace_environment(folder: Path, trace_file: Path) -> dict[str, str]:
    hook = folder / "hook"
    hook.mkdir(exist_ok=True)
    (hook / "sitecustomize.py").write_text(TRACE_SITECUSTOMIZE, encoding="utf-8")
    existing = os.environ.get("PYTHONPATH")
    return {
        "PYTHONPATH": str(hook) + (os.pathsep + existing if existing else ""),
        TRACE_FILE_ENV: str(trace_file),
    }


def _disables_hook(argv: list[str]) -> bool:
    """Interpreter options that stop the hook: -S skips site, -I and -E ignore PYTHONPATH.

    Short options are read one letter at a time, as CPython does, so clusters like -uS count.
    -X and -W take the rest of their word, or the next word when they end it. -c and -m end
    the options.
    """
    words = iter(argv[1:])
    for word in words:
        if word == "--" or not word.startswith("-") or word == "-":
            return False
        if word.startswith("--"):
            if word == "--check-hash-based-pycs":
                next(words, None)  # Its value is a separate word.
            continue
        for index, letter in enumerate(word[1:], start=1):
            if letter in {"c", "m"}:
                return False
            if letter in {"S", "I", "E"}:
                return True
            if letter in {"X", "W"}:
                if index == len(word) - 1:
                    next(words, None)  # A separate value, as in -X utf8 or -uX utf8.
                break  # Otherwise the rest of the word is the value, as in -Xutf8.
    return False


def _read_trace(trace_file: Path) -> tuple[set[str], list[str], int]:
    """Files read, launched programs whose reads are not seen, and the number of traced processes.

    Each launch is judged on its own: a non-Python program is untraced, and so is a Python one
    whose environment drops the hook or whose options (-S, -I, -E) stop it loading.
    """
    reads: set[str] = set()
    starts: set[int] = set()
    untraced: set[str] = set()
    if not trace_file.exists():
        return reads, [], 0
    for line in trace_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind, value, pid = event.get("kind"), event.get("value"), event.get("pid")
        if kind == "start" and isinstance(pid, int):
            starts.add(pid)
        elif kind == "read" and isinstance(value, str):
            reads.add(value)
        elif kind == "spawn" and isinstance(value, dict):
            program = str(value.get("program"))
            argv = [str(item) for item in value.get("argv") or []]
            if not Path(program).name.startswith("python"):
                untraced.add(program)
            elif value.get("hooked") is not True or _disables_hook(argv):
                untraced.add("a Python child process that cannot load the hook")
    return reads, sorted(untraced), len(starts)


def _source_of(relative: str) -> str:
    """The source file a bytecode read stands for: pkg/__pycache__/m.cpython-312.pyc -> pkg/m.py."""
    parts = relative.split("/")
    if len(parts) >= 2 and parts[-2] == BYTECODE_CACHE and parts[-1].endswith(".pyc"):
        return "/".join([*parts[:-2], parts[-1].split(".", 1)[0] + ".py"])
    return relative


def _project_relative(root: Path, absolute: str) -> str | None:
    """A traced path relative to the project, or None outside it or inside tooling folders.

    A bytecode read stands for its source, so an import served from __pycache__ still counts.
    """
    for base in (root, root.resolve()):
        try:
            relative = _source_of(Path(absolute).relative_to(base).as_posix())
        except ValueError:
            continue
        if not _tooling(relative) and not _ignored_change(relative):
            return relative
    return None


def _paths(entries: object) -> set[str]:
    names: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            names |= {str(entry.get("path")), str(entry.get("real") or entry.get("path"))}
    return names


def check_ignore_patterns(config: dict[str, Any]) -> list[str]:
    """Project-declared scratch files a toolchain leaves behind, such as R's .Rhistory.

    Patterns match project-relative paths with fnmatch, where `*` also crosses folders.
    """
    reproduction = config.get("reproduction", {})
    value = reproduction.get("check_ignore", []) if isinstance(reproduction, dict) else []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RuntimeError("reproduction.check_ignore must be a list of path patterns")
    return list(value)


def _ignored_by_project(relative: str, patterns: list[str]) -> bool:
    """Whether a project pattern covers a path. Raw inputs can never be ignored."""
    if relative == "data/raw" or relative.startswith("data/raw/"):
        return False
    name = relative.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in patterns
    )


def _within_any(root: Path, relative: str, bases: set[str]) -> bool:
    real = _real_relative(root, relative)
    return any(
        path == base or path.startswith(base + "/")
        for path in {relative, real or relative}
        for base in bases
    )


def _journal_entries(root: Path) -> dict[str, dict[str, int]] | None:
    """The lock journal, validated in full, or None when there is none."""
    journal = root / INPUT_LOCK_JOURNAL
    try:
        recorded = json.loads(journal.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        recorded = exc
    fields = ("mode", "locked_mode", "dev", "ino")
    if not isinstance(recorded, dict) or not all(
        isinstance(path, str)
        and isinstance(entry, dict)
        and all(isinstance(entry.get(field), int) for field in fields)
        for path, entry in recorded.items()
    ):
        raise RuntimeError(
            f"{INPUT_LOCK_JOURNAL} is malformed, so astack cannot tell which files it locked. "
            "It was kept. Restore read access by hand (for example `chmod u+r <file>` on files "
            "under the derived folders), then delete it."
        )
    return recorded


def _restore_locks(root: Path) -> list[str]:
    """Give locked files their recorded modes back, including after an interrupted run.

    Only a file that is still the locked one (same device, inode and locked mode) is touched,
    so a replacement, or a mode someone changed since, is left alone and returned.
    """
    recorded = _journal_entries(root)
    if recorded is None:
        return []
    skipped: list[str] = []
    failed: list[str] = []
    for relative, entry in sorted(recorded.items()):
        path = root / relative
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            skipped.append(relative)  # Gone, so there is nothing to restore.
            continue
        except OSError:
            failed.append(relative)  # Still there but unreachable, so keep the journal.
            continue
        if (metadata.st_dev, metadata.st_ino) != (
            entry["dev"],
            entry["ino"],
        ) or metadata.st_mode & 0o7777 != entry["locked_mode"]:
            skipped.append(relative)
            continue
        try:
            os.chmod(path, entry["mode"], follow_symlinks=False)
        except (OSError, NotImplementedError):
            try:
                os.chmod(path, entry["mode"])
            except OSError:
                failed.append(relative)
    if failed:
        raise RuntimeError(
            "Could not restore read access on: "
            + ", ".join(failed)
            + f". {INPUT_LOCK_JOURNAL} was kept; the next run retries."
        )
    (root / INPUT_LOCK_JOURNAL).unlink()
    return skipped


def _lock_inputs(
    root: Path, stage: dict[str, Any], ignore: list[str]
) -> tuple[list[str], list[str]]:
    """Make derived files the stage does not declare unreadable.

    Returns the locked paths and the gaps: paths that could not be locked safely and verifiably.
    A symlink or a file with other hard links is never changed, because that would change a file
    elsewhere, possibly a shared cache. A file still readable after locking, as an ACL can allow,
    is a gap too. The journal is written before any change.
    """
    allowed = _paths(stage.get("deps")) | _paths(stage.get("outs"))
    candidates: dict[str, dict[str, int]] = {}
    gaps: list[str] = []
    for prefix in COMPONENT_LIFECYCLES:
        folder = root / prefix
        # A symlink anywhere on the way, such as data/ itself, would lead outside the project.
        parts = prefix.split("/")
        try:
            linked = next(
                (
                    "/".join(parts[: index + 1])
                    for index in range(len(parts))
                    if (root / "/".join(parts[: index + 1])).is_symlink()
                ),
                None,
            )
            present = folder.is_dir()
        except OSError:
            if not _within_any(root, prefix, allowed):
                gaps.append(f"{prefix} (could not be inspected)")
            continue
        if linked is not None:
            if not _within_any(root, prefix, allowed):
                gaps.append(
                    f"{prefix} (symlinked folder)"
                    if linked == prefix
                    else f"{prefix} (through symlinked folder {linked})"
                )
            continue
        if not present:
            continue

        def unlisted(error: OSError, prefix: str = prefix) -> None:
            # A folder that cannot be listed still lets a known path be read, so it is a gap.
            path = Path(error.filename or root / prefix)
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = prefix
            if not _within_any(root, relative, allowed):
                gaps.append(f"{relative} (folder could not be listed)")

        for current, directories, files in os.walk(folder, onerror=unlisted, followlinks=False):
            for name in [*directories, *files]:
                path = Path(current, name)
                relative = path.relative_to(root).as_posix()
                if _within_any(root, relative, allowed) or _ignored_by_project(relative, ignore):
                    continue
                try:
                    metadata = path.lstat()
                except OSError:
                    # A folder that can be listed but not entered still hides this path.
                    gaps.append(f"{relative} (could not be inspected)")
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    gaps.append(f"{relative} (symlink)")
                    continue
                if name in directories:
                    continue
                if metadata.st_nlink > 1:
                    gaps.append(f"{relative} (hard link)")
                    continue
                mode = metadata.st_mode & 0o7777
                if not mode & 0o444:
                    if os.access(path, os.R_OK):
                        gaps.append(f"{relative} (still readable, for example through an ACL)")
                    continue
                candidates[relative] = {
                    "mode": mode,
                    "locked_mode": mode & ~0o444,
                    "dev": metadata.st_dev,
                    "ino": metadata.st_ino,
                }
    if not candidates:
        return [], sorted(gaps)
    journal = root / INPUT_LOCK_JOURNAL
    partial = journal.with_name(journal.name + ".tmp")
    try:
        journal.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(json.dumps(candidates, sort_keys=True), encoding="utf-8")
        os.replace(partial, journal)  # Atomic, so a crash never leaves half a journal.
    except OSError:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass  # Nothing was locked, and the next write replaces it.
        # Nothing is changed without a journal to undo it, so every candidate is a gap.
        return [], sorted(
            [*gaps, *(f"{relative} (lock journal could not be written)" for relative in candidates)]
        )
    locked: list[str] = []
    for relative, entry in sorted(candidates.items()):
        path = root / relative
        try:
            os.chmod(path, entry["locked_mode"])
        except OSError:
            gaps.append(f"{relative} (could not change permissions)")
            continue
        locked.append(relative)
        if os.access(path, os.R_OK):
            gaps.append(f"{relative} (still readable, for example through an ACL)")
    return locked, sorted(gaps)


def _locked_read_message(name: str, locked: list[str], output: str) -> str | None:
    """Name the undeclared derived files a failed stage tried to read, when its output shows it."""
    hits = [relative for relative in locked if relative in output]
    if not hits:
        return None
    return (
        f"Stage {name} tried to read {', '.join(hits)}, which it does not declare. "
        "Add it to the stage's deps in dvc.yaml."
    )


def _stage_check(
    root: Path,
    stage: dict[str, Any],
    changed: list[str],
    trace: tuple[set[str], list[str], int],
    ignore: list[str] | None = None,
) -> dict[str, Any]:
    outs = _paths(stage.get("outs"))
    deps = _paths(stage.get("deps"))
    ignore = ignore or []

    def within(relative: str, bases: set[str]) -> bool:
        return _within_any(root, relative, bases)

    writes = sorted(
        relative
        for relative in changed
        if not _ignored_change(relative)
        and not _tooling(relative)
        and not _ignored_by_project(relative, ignore)
        and not within(relative, outs)
    )
    reads_seen, untraced_programs, processes = trace
    reads = sorted(
        {
            relative
            for absolute in reads_seen
            if (relative := _project_relative(root, absolute)) is not None
            and not within(relative, deps | outs)
            and not _ignored_by_project(relative, ignore)
            and relative.rsplit("/", 1)[-1] != "dvc.yaml"
        }
    )
    untraced = None
    wdir = root / str(stage.get("wdir") or ".")
    if not _traceable(stage.get("cmd"), wdir):
        untraced = "its command is not a single Python process, so some reads are not seen"
    elif processes == 0:
        untraced = "the trace hook did not load in its Python process"
    elif untraced_programs:
        untraced = "it starts programs whose reads are not seen: " + ", ".join(untraced_programs)
    return {
        "undeclared_writes": writes,
        "undeclared_reads": reads,
        "untraced": untraced,
    }


def _lock_fingerprints(root: Path) -> dict[str, str]:
    """A digest of each stage's dvc.lock entry, so a check describes one exact run."""
    fingerprints: dict[str, str] = {}
    if yaml is None:
        return fingerprints
    for lock in sorted(root.rglob("dvc.lock")):
        if _tooling(lock.relative_to(root).as_posix()):
            continue
        try:
            with _open_regular_file(lock) as handle:
                data = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError):
            continue
        stages = data.get("stages") if isinstance(data, dict) else None
        folder = lock.parent.relative_to(root).as_posix()
        for name, entry in (stages or {}).items() if isinstance(stages, dict) else ():
            key = str(name) if folder == "." else f"{folder}/dvc.yaml:{name}"
            text = json.dumps(entry, sort_keys=True, default=str)
            fingerprints[key] = hashlib.sha256(text.encode()).hexdigest()
    return fingerprints


def _check_policy(root: Path) -> str:
    """What a saved check was judged under, so changing ignores or the checker invalidates it."""
    ignore = sorted(check_ignore_patterns(_project_config(root)))
    text = json.dumps({"version": DEPENDENCY_CHECK_VERSION, "ignore": ignore})
    return hashlib.sha256(text.encode()).hexdigest()


def _save_checks(root: Path, results: dict[str, dict[str, Any]]) -> None:
    path = root / DEPENDENCY_CHECK_PATH
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    stages = saved.get("stages") if isinstance(saved, dict) else None
    stages = stages if isinstance(stages, dict) else {}
    fingerprints = _lock_fingerprints(root)
    policy = _check_policy(root)
    for name, result in results.items():
        stages[name] = {**result, "lock": fingerprints.get(name), "policy": policy}
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "limits": DEPENDENCY_CHECK_LIMITS, "stages": stages}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dependency_checks(root: Path) -> dict[str, dict[str, Any]]:
    """Saved checks that still describe each stage's current dvc.lock entry."""
    try:
        with _open_regular_file(root / DEPENDENCY_CHECK_PATH) as handle:
            saved = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    stages = saved.get("stages") if isinstance(saved, dict) else None
    if not isinstance(stages, dict):
        return {}
    fingerprints = _lock_fingerprints(root)
    try:
        policy = _check_policy(root)
    except RuntimeError:
        return {}
    return {
        str(name): result
        for name, result in stages.items()
        if isinstance(result, dict)
        and result.get("lock") is not None
        and result.get("lock") == fingerprints.get(str(name))
        and result.get("policy") == policy
    }


def run_checked_dvc_stages(
    root: Path, *, force: bool, targets: list[str], environment: dict[str, str]
) -> dict[str, Any]:
    """Run each stage that needs it, in pipeline order, and check its reads and writes.

    DVC still decides nothing here: astack forces each stage it runs, so every run is traced.
    """
    ignore = check_ignore_patterns(_project_config(root))
    _restore_locks(root)  # Repair anything an interrupted run left locked.
    pipeline, problem = _dvc_pipeline(root, targets)
    if pipeline is None:
        raise RuntimeError(problem)
    stages = [
        stage
        for stage in pipeline["stages"]
        if isinstance(stage, dict) and isinstance(stage.get("outs"), list)
    ]
    by_name = {str(stage["name"]): stage for stage in stages}
    upstream, _ = _stage_edges(root, stages)
    states = _stage_states(root, stages, pipeline["status"])
    order = _stage_order(stages, upstream)
    selected = pipeline.get("selected")
    chosen = set(order) if not targets else {str(name) for name in selected or []}
    if targets and not chosen:
        raise RuntimeError("The reproduction targets select no DVC stage: " + ", ".join(targets))
    checked = dependency_checks(root)
    results: dict[str, dict[str, Any]] = {}
    ran: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="astack-trace-") as scratch:
        folder = Path(scratch)
        for name in order:
            stage = by_name[name]
            if name not in chosen or stage.get("frozen"):
                continue
            # A stage without a check for its current run reruns, so `just reproduce` repairs it.
            needed = (
                force
                or states[name][0] != "current"
                or bool(upstream[name] & ran)
                or name not in checked
            )
            if not needed:
                continue
            trace_file = folder / f"{len(ran)}.jsonl"
            locked: list[str] = []
            gaps: list[str] = []
            result = None
            try:
                locked, gaps = _lock_inputs(root, stage, ignore)
                before = _snapshot(root, gaps)
                result = _run(
                    ["uv", "run", "--locked", "dvc", "repro", "--single-item", "--force", name],
                    cwd=root,
                    env=os.environ | environment | _trace_environment(folder, trace_file),
                )
                after = _snapshot(root, gaps)
            finally:
                try:
                    _restore_locks(root)
                except RuntimeError as restore_error:
                    # Keep the stage's own failure next to the restore failure.
                    if result is not None and result.returncode != 0:
                        output = ((result.stderr or "") + (result.stdout or "")).strip()
                        raise RuntimeError(
                            f"{restore_error} Stage {name} also failed: {output[-2000:]}"
                        ) from restore_error
                    raise
            if result.returncode != 0:
                output = (result.stderr or "") + (result.stdout or "")
                raise RuntimeError(
                    _locked_read_message(name, locked, output)
                    or output.strip()
                    or f"Stage {name} failed"
                )
            changed = sorted(
                relative
                for relative in before.keys() | after.keys()
                if before.get(relative) != after.get(relative)
            )
            ran.add(name)
            results[name] = _stage_check(root, stage, changed, _read_trace(trace_file), ignore)
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                gaps = ["every file (running as root ignores permissions)"]
            gaps = sorted(set(gaps))
            results[name]["inputs_locked"] = not gaps
            results[name]["lock_gaps"] = gaps
    _save_checks(root, results)
    return {"stages": results, "limits": DEPENDENCY_CHECK_LIMITS}
