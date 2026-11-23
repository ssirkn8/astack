"""What the review notebook shows and the one thing it writes: a sign-off record per step."""

from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    _READ_ONLY,
    NOT_CODE_LIFECYCLES,
    REVIEW_DIFF_LINE_LIMIT,
    REVIEW_HEADING_STYLES,
    REVIEW_RECORD_ROOT,
    REVIEW_STATUS_COLORS,
    REVIEW_STATUS_LABELS,
    RISK_LEVELS,
    UNREVIEWED_CODE,
    UTC,
    Any,
    Path,
    _open_regular_file,
    _require_regular_file,
    _run,
    _sha256,
    datetime,
    hashlib,
    json,
    os,
    re,
    review_policy,
    review_policy_for_config,
    stat,
    subprocess,
    tempfile,
    tomllib,
)
from .dependency_check import dependency_checks
from .pipeline import _dvc_pipeline, _stage_edges
from .reproduction import _reproduction_command, _reproduction_engine
from .staleness import output_status
from .validation import _classify_output_path, _project_config


def _review_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return _run(["git", *arguments], cwd=root, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise OSError("Git timed out while reading review state") from error
    except UnicodeError as error:
        raise OSError("Git returned unreadable review state") from error


def _git(root: Path, *arguments: str) -> str | None:
    try:
        result = _review_git(root, "--no-replace-objects", *arguments)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git_on_paths(
    root: Path, *arguments: str, paths: list[str]
) -> subprocess.CompletedProcess[str]:
    """Run git on exact paths: literal pathspecs, so a name like `a*b.py` never globs."""
    return _review_git(
        root, "--no-replace-objects", "--literal-pathspecs", *arguments, "--", *paths
    )


def _stored_blobs(root: Path, commit: str, files: list[str]) -> dict[str, tuple[str, str]] | None:
    """Each file's mode and blob id at a commit, or None when git cannot say."""
    tree = _git_on_paths(root, "ls-tree", "-z", commit, paths=files)
    if tree.returncode != 0:
        return None
    blobs: dict[str, tuple[str, str]] = {}
    for item in filter(None, tree.stdout.split("\0")):
        meta, _, relative = item.partition("\t")
        mode, _, blob = meta.split(" ")
        blobs[relative] = (mode, blob)
    return blobs


def _raw_blob_ids(root: Path, files: list[str]) -> list[str]:
    """The blob id of each file's raw bytes, with no clean filter or line-ending conversion."""
    for relative in files:
        _require_regular_file(root / relative)
    result = _git_on_paths(root, "hash-object", "--no-filters", paths=files)
    hashes = result.stdout.split()
    if result.returncode != 0 or len(hashes) != len(files):
        detail = result.stderr.strip().splitlines()
        raise OSError(
            "Git could not hash review files" + (f": {detail[-1][:200]}" if detail else "")
        )
    return hashes


def _uncommitted(root: Path, files: list[str], revision: str = "HEAD") -> list[str]:
    """Code files a commit (HEAD by default) does not store byte for byte.

    Compared on raw bytes: a clean filter or line-ending conversion can make git call a file
    unchanged while the bytes that run differ. A symlink counts as uncommitted, since the code
    read is its target, which HEAD does not store under that name. If git cannot tell, every
    file counts.
    """
    if not files:
        return []
    links = [relative for relative in files if (root / relative).is_symlink()]
    if links:
        return links
    stored = _stored_blobs(root, revision, files)
    if stored is None:
        return list(files)
    hashes = _raw_blob_ids(root, files)
    return [
        relative
        for relative, blob in zip(files, hashes, strict=True)
        if stored.get(relative) not in {("100644", blob), ("100755", blob)}
    ]


def _visible(text: str) -> str:
    """Carriage returns, tabs and spaces at a line's end as markers, so every byte shows."""
    text = text.replace("\r", "\u240d").replace("\t", "\u21e5")
    # Try each run of spaces once. Retrying at every space makes internal runs quadratic.
    return re.sub(r"(?<! ) +(?=\n|\Z)", lambda match: "\u2423" * len(match.group()), text)


def _raw_diff(
    root: Path, relative: str, blob: str | None, data: bytes, context: int = 3
) -> tuple[str | None, str]:
    """A unified diff of a file's stored bytes against its bytes on disk, or why there is none.

    Built here rather than by git diff, so no filter, driver or setting can change what is shown.
    Carriage returns, tabs and trailing spaces show as markers. A file that already contains a
    marker needs a full read, as does any change that would display as no change.
    """
    import difflib

    old = b""
    if blob is not None:
        try:
            shown = subprocess.run(
                ["git", "--no-replace-objects", "cat-file", "blob", blob],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, f"git could not read the reviewed {relative}"
        if shown.returncode != 0:
            return None, f"git could not read the reviewed {relative}"
        old = shown.stdout
    try:
        before, after = old.decode("utf-8"), data.decode("utf-8")
    except UnicodeDecodeError:
        return None, f"{relative} changed and is not UTF-8 text, so it cannot be shown as a diff"
    # Shown so every byte is visible: carriage returns, tabs and spaces at a line's end get a
    # marker, and a file already holding a marker needs a full read, so no two texts look alike.
    markers = "\u240d\u21e5\u2423"
    if any(marker in before or marker in after for marker in markers):
        return None, f"{relative} contains a character used to show whitespace"
    # An invisible, control or line-separator character (other than newline, tab and carriage
    # return) could hide or fake a change.
    if any(not (char.isprintable() or char in "\n\t\r") for char in before + after):
        return None, f"{relative} contains invisible or control characters"
    before, after = _visible(before), _visible(after)
    if before == after:
        return None, f"{relative} changed in a way a text diff cannot show"
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        f"a/{relative}" if blob else "/dev/null",
        f"b/{relative}",
        n=context,
    )
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines
    ), ""


def _header(root: Path) -> dict[str, Any]:
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    return {
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git(root, "rev-parse", "--short", "HEAD"),
        "uncommitted_changes": None if status is None else bool(status),
    }


def _files(root: Path, relative: str) -> tuple[list[str], str | None]:
    """A code dep's files, or why they cannot all be listed.

    A missing dep, or a symlinked folder inside one (its target could change unseen), means the
    code set is unknown, never empty. Symlinked files are hashed by their target's bytes.
    """
    path = root / relative

    def fail(error: OSError) -> None:
        raise error

    files: list[str] = []
    try:
        mode = path.stat().st_mode
        if stat.S_ISREG(mode):
            return [relative], None
        if not stat.S_ISDIR(mode):
            return [], f"{relative} is not a regular file or folder"
        for current, directories, names in os.walk(path, followlinks=False, onerror=fail):
            here = Path(current)
            for name in directories:
                if (here / name).is_symlink():
                    return [], f"{(here / name).relative_to(root).as_posix()} is a symlinked folder"
            directories[:] = [name for name in directories if name != "__pycache__"]
            files.extend((here / name).relative_to(root).as_posix() for name in names)
    except FileNotFoundError:
        return [], f"{relative} is missing"
    except (OSError, ValueError) as error:
        return [], f"Could not list {relative}: {error}"
    return sorted(files), None


def _step_name(stage: str) -> str:
    """The step a DVC stage belongs to: `clean@north` is an item of the `clean` group."""
    return stage.split("@", 1)[0]


def _new_unit(unit: str, kind: str) -> dict[str, Any]:
    return {
        "unit": unit,
        "kind": kind,
        "items": [],
        "files": set(),
        "shared": [],
        "commands": [],
        "params": {},
        "declared_in": set(),
        "unknown": None,
        "upstream": set(),
        "downstream": set(),
        "used_by": [],
    }


def _add_stage(root: Path, unit: dict[str, Any], stage: dict[str, Any], reads: list[str]) -> None:
    """Add one resolved DVC stage to its step: its command, parameters, code and data inputs."""
    name = str(stage.get("name"))
    item: dict[str, Any] = {
        "name": name,
        "key": name.split("@", 1)[1] if "@" in name else None,
        "command": stage.get("cmd"),
        "wdir": str(stage.get("wdir") or "."),
        "frozen": bool(stage.get("frozen")),
        "settings": stage.get("settings") or {},
        "code": [],
        "inputs": [],
        "outputs": [str(out.get("path")) for out in stage.get("outs", []) if isinstance(out, dict)],
    }
    unit["items"].append(item)
    if stage.get("dvcfile"):
        unit["declared_in"].add(str(stage["dvcfile"]))
    if reads and unit["unknown"] is None:
        unit["unknown"] = f"stage {name} reads undeclared files: {', '.join(reads[:3])}"
    values: dict[str, Any] = {}
    for dep in stage.get("deps", []):
        path = str(dep.get("path")) if isinstance(dep, dict) else ""
        if isinstance(dep, dict) and "params_problem" in dep:
            unit["unknown"] = unit["unknown"] or dep["params_problem"]
            continue
        if isinstance(dep, dict) and "params" in dep:
            values |= {f"{path}:{key}": value for key, value in dep["params"].items()}
            unit["declared_in"].add(path)
            if dep.get("params_missing") and unit["unknown"] is None:
                missing = ", ".join(map(str, dep["params_missing"][:3]))
                unit["unknown"] = f"stage {name} names parameters {path} lacks: {missing}"
            continue
        if not path or path in UNREVIEWED_CODE:
            continue
        if _classify_output_path(path)["lifecycle"] in NOT_CODE_LIFECYCLES:
            item["inputs"].append(path)
            continue
        item["code"].append(path)
        found, missing_dep = _files(root, path)
        unit["files"].update(found)
        if missing_dep and unit["unknown"] is None:
            unit["unknown"] = missing_dep
    unit["params"][name] = values
    # Which data a stage reads and writes is signed off by path. Data contents are not code.
    unit["commands"].append(
        json.dumps(
            [
                name,
                stage.get("cmd"),
                stage.get("wdir"),
                sorted(item["inputs"]),
                sorted(item["outputs"]),
                sorted(item["code"]),
                item["frozen"],
                item["settings"],
            ],
            default=str,
        )
    )


def _dvc_units(root: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    """One unit per DVC step. `foreach` and `matrix` items are one step, since they share code."""
    pipeline, problem = _dvc_pipeline(root, read_only=True, stages_only=True)
    if pipeline is None:
        return {}, problem
    stages = [stage for stage in pipeline["stages"] if isinstance(stage, dict)]
    checks = dependency_checks(root)
    units: dict[str, dict[str, Any]] = {}
    for stage in stages:
        name = str(stage.get("name"))
        step = _step_name(name)
        reads = (checks.get(name) or {}).get("undeclared_reads") or []
        _add_stage(root, units.setdefault(step, _new_unit(step, "step")), stage, reads)
    edges, _ = _stage_edges(root, stages)
    for name, parents in edges.items():
        for parent in parents:
            child, source = _step_name(name), _step_name(parent)
            if child != source:
                units[child]["upstream"].add(source)
                units[source]["downstream"].add(child)
    return units, None


def _command_unit(root: Path, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A project without DVC has one step: its reproduction command and tracked code."""
    import shlex

    reproduction = config.get("reproduction", {})
    command = reproduction.get("command")
    if not command:
        return {}
    name = str(reproduction.get("component") or "pipeline")
    unit = _new_unit(name, "step")
    # -z gives raw names: without it Git quotes non-ASCII paths like caf\303\251.py.
    try:
        listing = _review_git(root, "ls-files", "-z", "--", "src", "scripts")
    except OSError as error:
        unit["unknown"] = f"Git could not list src/ and scripts/: {error}"
    else:
        if listing.returncode != 0:
            unit["unknown"] = "git could not list src/ and scripts/"
        else:
            unit["files"].update(item for item in listing.stdout.split("\0") if item)
    # How the code is run is part of what is reviewed.
    outputs = [str(item) for item in reproduction.get("outputs", [])]
    # Same shape as a DVC stage's identity, so outputs are signed off and changes read the same.
    unit["commands"].append(
        json.dumps(["command", command, ".", [], sorted(outputs), [], False, {}], default=str)
    )
    unit["declared_in"].add(".astack/project.toml")
    # Quoted, so `["a b"]` and `["a", "b"]` never read the same.
    text = shlex.join(map(str, command)) if isinstance(command, list) else str(command)
    unit["items"].append(
        {
            "name": name,
            "key": None,
            "command": text,
            "wdir": ".",
            "frozen": False,
            "settings": {},
            "code": [],
            "inputs": [],
            "outputs": outputs,
        }
    )
    unit["params"][name] = {}
    return {name: unit}


def _in_pipeline_order(units: dict[str, dict[str, Any]]) -> list[str]:
    """Steps with everything upstream first, ties in the order DVC lists them."""
    depth: dict[str, int] = {}

    def depth_of(name: str, seen: frozenset[str] = frozenset()) -> int:
        if name not in depth:
            parents = [item for item in units[name]["upstream"] if item not in seen]
            depth[name] = 1 + max((depth_of(item, seen | {name}) for item in parents), default=-1)
        return depth[name]

    for name in units:
        units[name]["depth"] = depth_of(name)
    return sorted(units, key=lambda name: units[name]["depth"])


def _review_units(
    root: Path, config: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Everything a person signs off: each pipeline step, then code that several steps share.

    A file used by two or more steps is signed off once, on its own, so a change to it asks for
    one review rather than one per step. Which files a step uses comes from its declared deps,
    and a saved dependency check showing undeclared reads makes the step `unknown`.
    """
    reproduction = config.get("reproduction", {})
    if not isinstance(reproduction, dict):
        return {}, "Project configuration: reproduction must be a table"
    if _reproduction_engine(root, config) == "dvc":
        found, problem = _dvc_units(root)
        if problem:
            return {}, problem
    else:
        if not isinstance(reproduction.get("outputs", []), list):
            return {}, "Project configuration: reproduction.outputs must be a list of paths"
        if "command" in reproduction and not _reproduction_command(root, config):
            return (
                {},
                "Project configuration: reproduction.command must be a non-empty command array",
            )
        found = _command_unit(root, config)
    steps = {name: found[name] for name in _in_pipeline_order(found)}
    users: dict[str, list[str]] = {}
    for name, unit in steps.items():
        unit["files"] -= UNREVIEWED_CODE
        for relative in unit["files"]:
            users.setdefault(relative, []).append(name)
    common = {relative for relative, names in users.items() if len(names) > 1}
    units = dict(steps)
    for unit in steps.values():
        unit["shared"] = sorted(unit["files"] & common)
        unit["files"] = sorted(unit["files"] - common)
    for relative in sorted(common):
        unit = _new_unit(f"shared:{relative}", "shared")
        unit["files"] = [relative]
        unit["used_by"] = users[relative]
        units[unit["unit"]] = unit
    for unit in units.values():
        unit["commands"] = sorted(unit["commands"])
    return units, None


def _code_checksum(
    root: Path,
    files: list[str],
    commands: list[str],
    params: dict[str, Any] | None = None,
    shared: list[str] | None = None,
    *,
    rendered: dict[str, bytes] | None = None,
) -> str:
    digest = hashlib.sha256(b"astack-review-code-v3\0")
    for command in commands:
        digest.update(f"command\0{command}\n".encode())
    digest.update(f"params\0{json.dumps(params or {}, sort_keys=True, default=str)}\n".encode())
    digest.update(f"shared\0{json.dumps(shared or [])}\n".encode())
    for relative in files:
        file_hash = (
            _sha256(root / relative, regular_only=True)
            if rendered is None
            else hashlib.sha256(rendered[relative]).hexdigest()
        )
        digest.update(f"{relative}\0{file_hash}\n".encode())
    return digest.hexdigest()


def _record_name(text: str) -> str:
    """A file name for any unit: readable, and unique through a short hash of the exact name."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")[:80]
    return f"{slug}-{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def _review_record_path(root: Path, unit: str) -> Path:
    """Records are workflow metadata, kept out of the derived folders pipelines read."""
    if unit.startswith("shared:"):
        return root / REVIEW_RECORD_ROOT / "shared" / f"{_record_name(unit[7:])}.json"
    name = unit if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", unit) else _record_name(unit)
    return root / REVIEW_RECORD_ROOT / f"{name}.json"


def _valid_sign_off(record: dict[str, Any]) -> bool:
    return (
        isinstance(record.get("reviewer"), str)
        and bool(record["reviewer"].strip())
        and isinstance(record.get("commit"), str)
        and re.fullmatch(r"[0-9a-f]{40,64}", record["commit"]) is not None
        and isinstance(record.get("reviewed_at"), str)
        and bool(record["reviewed_at"])
        and isinstance(record.get("code_checksum"), str)
        and re.fullmatch(r"[0-9a-f]{64}", record["code_checksum"]) is not None
    )


def _valid_review(record: object, unit: str) -> bool:
    """A record counts only with its own unit and either a complete sign-off or revocation."""
    if not isinstance(record, dict):
        return False
    if record.get("schema_version") != 2 or record.get("unit") != unit:
        return False
    if record.get("revoked") is True:
        return (
            isinstance(record.get("by"), str)
            and bool(record["by"].strip())
            and isinstance(record.get("at"), str)
            and bool(record["at"])
            and isinstance(record.get("reason"), str)
        )
    return "revoked" not in record and _valid_sign_off(record)


def _load_review(root: Path, unit: str) -> tuple[dict[str, Any] | None, bool]:
    """The record and whether it is valid. A missing record is (None, True)."""
    path = _review_record_path(root, unit)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    try:
        if not stat.S_ISREG(mode):
            return None, False
        with _open_regular_file(path) as handle:
            record = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None, False
    return (record, True) if _valid_review(record, unit) else (None, False)


def _param_changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Each parameter value that differs from the signed-off one, per stage."""
    changes: list[dict[str, Any]] = []
    for stage in sorted(set(old) | set(new)):
        before, after = old.get(stage) or {}, new.get(stage) or {}
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after or before[key] != after[key]:
                changes.append(
                    {
                        "stage": stage,
                        "key": key,
                        "was": before.get(key),
                        "now": after.get(key),
                        "added": key not in before,
                        "removed": key not in after,
                    }
                )
    return changes


def _since_review(root: Path, row: dict[str, Any], commands: list[str]) -> dict[str, Any]:
    """What changed in a unit's code since its last sign-off, or why only a full read will do.

    A diff stands in for a full review only when it provably covers every change: the sign-off
    recorded its files, commands and parameters, the signed-off code was committed, that commit
    is in this branch's history, and every file still in the unit is tracked. Otherwise the
    reviewer is told to read the whole unit and why.
    """
    record = row["review"]

    def full(why: str) -> dict[str, Any]:
        return {"mode": "full", "why": why}

    if not isinstance(record, dict):
        return full("its sign-off was revoked" if row.get("revoked") else "no earlier sign-off")
    old_files, old_commands = record.get("files"), record.get("commands")
    old_params, old_shared = record.get("params"), record.get("shared")
    if (
        not isinstance(old_files, list)
        or not isinstance(old_commands, list)
        or not isinstance(old_params, dict)
        or any(not isinstance(values, dict) for values in old_params.values())
        or not isinstance(old_shared, list)
    ):
        return full("the earlier sign-off did not record what it covered")
    if record.get("code_committed") is not True:
        return full("the earlier review covered uncommitted code, so no commit shows what was read")
    commit = str(record["commit"])
    ancestor = _review_git(
        root, "--no-replace-objects", "merge-base", "--is-ancestor", commit, "HEAD"
    )
    if ancestor.returncode != 0:
        return full(
            f"git could not verify the reviewed commit {commit[:12]} is in this branch's history"
        )
    kept = sorted(set(row["files"]) & {str(item) for item in old_files})
    symlinked = "some of its files are symlinks, and git diffs the link, not the code it points to"
    if any((root / relative).is_symlink() for relative in kept):
        return full(symlinked)
    changed: list[str] = []
    reviewed: dict[str, tuple[str, str]] = {}
    if kept:
        listing = _git_on_paths(root, "ls-files", "-z", paths=kept)
        tracked = set(filter(None, listing.stdout.split("\0")))
        if listing.returncode != 0:
            return full("git could not list the tracked files")
        if tracked != set(kept):
            return full("some of its files are not tracked by git, so a diff would miss them")
        # Which files changed, and what is shown, come from raw bytes: the stored blob against
        # the file on disk, the same bytes the checksum hashes. git diff is never used, since
        # filters, drivers and settings can change what it shows.
        stored_blobs = _stored_blobs(root, commit, kept)
        if stored_blobs is None:
            return full("git could not read the reviewed commit")
        hashes = _raw_blob_ids(root, kept)
        reviewed = stored_blobs
        if any(mode == "120000" for mode, _ in reviewed.values()):
            return full(symlinked)
        changed = [
            relative
            for relative, blob in zip(kept, hashes, strict=True)
            if reviewed.get(relative, ("", ""))[1] != blob
        ]
    parts: list[str] = []
    for relative in changed:
        stored = reviewed.get(relative)
        try:
            with _open_regular_file(root / relative) as handle:
                data = handle.read()
        except OSError:
            return full(f"{relative} could not be read")
        part, why = _raw_diff(root, relative, stored[1] if stored else None, data)
        if part is None:
            return full(why)
        parts.append(part)
    text = "".join(parts)
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    result = {
        "mode": "diff",
        "since": commit,
        "commands_removed": sorted(set(map(str, old_commands)) - set(commands)),
        "commands_added": sorted(set(commands) - set(map(str, old_commands))),
        "files_added": sorted(set(row["files"]) - {str(item) for item in old_files}),
        "files_removed": sorted({str(item) for item in old_files} - set(row["files"])),
        "files_changed": changed,
        "shared_added": sorted(set(row["shared"]) - {str(item) for item in old_shared}),
        "shared_removed": sorted({str(item) for item in old_shared} - set(row["shared"])),
        "params_changed": _param_changes(old_params, row["params"]),
        "diff": "\n".join(lines[:REVIEW_DIFF_LINE_LIMIT]),
        "truncated": len(lines) > REVIEW_DIFF_LINE_LIMIT,
    }
    if not (
        text.strip()
        or any(
            result[key]
            for key in (
                "commands_removed",
                "commands_added",
                "files_added",
                "files_removed",
                "shared_added",
                "shared_removed",
                "params_changed",
            )
        )
    ):
        return full("its code changed in a way git does not show, such as a file mode")
    return result


def _code_fix(row: dict[str, Any]) -> str | None:
    """The next step that clears a unit's status, or None when nothing is needed."""
    status = row["status"]
    if status in {"unreviewed", "changed-since-review", "revoked"}:
        if row.get("uncommitted"):
            return "Commit these files, then sign off: " + ", ".join(row["uncommitted"][:5])
        return "Read the code shown, then sign off"
    if status == "invalid-record":
        return f"Delete {row['record']}, then review again"
    if status == "unknown":
        return "Fix the cause shown, then rerun this notebook"
    return None


def _code_review(root: Path, config: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """One row per step or shared file. Status is recomputed every time from the code as it is."""
    units, problem = _review_units(root, config)
    rows: list[dict[str, Any]] = []
    for name, unit in units.items():
        record, valid = _load_review(root, name)
        revoked = record if record and record.get("revoked") else None
        signed = None if revoked else record
        row: dict[str, Any] = {
            key: unit[key]
            for key in ("unit", "kind", "items", "files", "shared", "commands", "params")
        } | {
            "upstream": sorted(unit["upstream"]),
            "downstream": sorted(unit["downstream"]),
            "used_by": unit["used_by"],
            "depth": unit.get("depth", 0),
            "declared_in": sorted(unit["declared_in"]),
            "review": signed,
            "revoked": revoked,
            "checksum": None,
            "record": _review_record_path(root, name).relative_to(root).as_posix(),
        }
        try:
            if unit["unknown"]:
                raise OSError(unit["unknown"])
            checksum = _code_checksum(
                root, unit["files"], unit["commands"], unit["params"], unit["shared"]
            )
            row["checksum"] = checksum
            if not valid:
                row["status"] = "invalid-record"
            elif revoked:
                row["status"] = "revoked"
            elif signed is None:
                row["status"] = "unreviewed"
            elif signed["code_checksum"] == checksum:
                row["status"] = "reviewed"
            else:
                row["status"] = "changed-since-review"
            if row["status"] in {"unreviewed", "changed-since-review", "revoked"}:
                uncommitted = _uncommitted(root, [*unit["files"], *sorted(unit["declared_in"])])
                row["since_review"] = _since_review(root, row, unit["commands"])
                row["uncommitted"] = uncommitted
        except (OSError, ValueError) as error:
            row |= {"status": "unknown", "checksum": None, "why": str(error)}
        row["fix"] = _code_fix(row)
        rows.append(row)
    return rows, problem


def _last_review(root: Path, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest sign-off on record and how far the branch has moved since, when git can tell."""
    records = [row["review"] for row in rows if isinstance(row["review"], dict)]
    if not records:
        return None
    newest = max(records, key=lambda record: str(record["reviewed_at"]))
    commit = str(newest["commit"])
    summary: dict[str, Any] = {
        "reviewer": newest["reviewer"],
        "reviewed_at": newest["reviewed_at"],
        "commit": commit,
        "commits_since": None,
        "files_changed": None,
    }
    if _git(root, "merge-base", "--is-ancestor", commit, "HEAD") is not None:
        count = _git(root, "rev-list", "--count", f"{commit}..HEAD")
        changed = _git(root, "diff", "--name-only", "-z", commit)
        summary["commits_since"] = int(count) if count and count.isdigit() else None
        if changed is not None:
            summary["files_changed"] = len(list(filter(None, changed.split("\0"))))
    return summary


def review_status(root: Path) -> dict[str, Any]:
    """What the review notebook shows: each step and shared file, and whether a person signed it.

    Only the person's work. Output labels (`outputs`) and audit findings (`audit`) are the
    agent's, so they are not repeated here.
    """
    previous = _READ_ONLY["on"]
    _READ_ONLY["on"] = True  # Viewing never writes: no markers, no DVC cache configuration.
    rows: list[dict[str, Any]]
    problem: str | None
    try:
        try:
            _check_review_config_file(root)
            config = _project_config(root)
        except (OSError, ValueError) as error:
            rows, problem = [], f"Could not read .astack/project.toml: {error}"
        else:
            rows, problem = _code_review(root, config)
        return {
            "header": _header(root),
            "to_sign_off": sum(row["status"] != "reviewed" for row in rows) + bool(problem),
            "last_review": _last_review(root, rows),
            "units": rows,
            "problem": problem,
        }
    finally:
        _READ_ONLY["on"] = previous


def _check_review_config_file(root: Path) -> None:
    try:
        _require_regular_file(root / ".astack/project.toml")
    except FileNotFoundError:
        pass  # The project policy supports a missing configuration.


def _review_output_status(root: Path) -> dict[str, Any]:
    try:
        _check_review_config_file(root)
        return output_status(root)
    except (OSError, RuntimeError, ValueError) as error:
        return {"status": "unknown", "problem": str(error), "outputs": []}


def review_labels(root: Path) -> dict[str, str]:
    """Each output's current label, read without writing anything."""
    previous = _READ_ONLY["on"]
    _READ_ONLY["on"] = True
    try:
        outputs = _review_output_status(root).get("outputs", [])
    finally:
        _READ_ONLY["on"] = previous
    return {str(item.get("path")): str(item.get("label")) for item in outputs}


def review_display_status(row: dict[str, Any]) -> str:
    """The status a person sees. Code that must be committed first says so."""
    if row["status"] != "reviewed" and row.get("uncommitted"):
        return "commit first"
    return REVIEW_STATUS_LABELS[row["status"]]


def review_label(row: dict[str, Any]) -> str:
    """A unit's short name: a group shows its item count, shared code its file name."""
    if row["kind"] == "shared":
        return str(row["unit"]).rsplit("/", 1)[-1]
    count = len(row["items"])
    return f"{row['unit']} × {count}" if count > 1 else str(row["unit"])


def review_item(row: dict[str, Any]) -> str:
    """A unit's name in the overview table: shared code shows its whole path."""
    return str(row["unit"])[7:] if row["kind"] == "shared" else review_label(row)


def _purpose(root: Path, row: dict[str, Any]) -> str | None:
    """A unit's one-line purpose: the docstring of the script its command runs."""
    import ast

    commands = " ".join(str(item.get("command")) for item in row["items"])
    named = [item for item in row["files"] if item in commands]
    for relative in [*named, *row["files"]]:
        if not relative.endswith(".py"):
            continue
        try:
            with _open_regular_file(root / relative) as handle:
                doc = ast.get_docstring(ast.parse(handle.read()))
        except (OSError, SyntaxError, ValueError):
            continue
        if doc and doc.strip():
            return doc.strip().splitlines()[0]
    return None


def review_overview(
    root: Path,
    status: dict[str, Any],
    labels: dict[str, str],
    *,
    opened: frozenset[str] = frozenset(),
    only_pending: bool = True,
) -> list[dict[str, str]]:
    """The overview table: one row per unit, most of it answerable without opening anything."""
    rows: list[dict[str, str]] = []
    for row in status["units"]:
        if only_pending and row["status"] == "reviewed":
            continue
        since = row.get("since_review") or {}
        if row["status"] == "reviewed":
            changed = "none"
        elif since.get("mode") == "diff":
            # A parameter changed for all six items of a group is one change.
            counts_by_kind = {
                "changed file": len(since["files_changed"]),
                "new file": len(since["files_added"]),
                "parameter": len({change["key"] for change in since["params_changed"]}),
                "run setting": len(_run_changes(since)),
                "removed file": len(since["files_removed"]),
                "shared code change": len(since["shared_added"]) + len(since["shared_removed"]),
            }
            parts = [
                f"{count} {name}{'' if count == 1 else 's'}"
                for name, count in counts_by_kind.items()
                if count
            ]
            changed = ", ".join(parts) or "see page"
        else:
            changed = str(since.get("why") or row.get("why") or "")
        counts: dict[str, int] = {}
        for item in row["items"]:
            for path in item["outputs"]:
                label = labels.get(path, "unknown")
                counts[label] = counts.get(label, 0) + 1
        rows.append(
            {
                "item": str(row["unit"]),
                "status": review_display_status(row),
                "changed since sign-off": changed,
                "outputs": ", ".join(f"{n} {name}" for name, n in sorted(counts.items())) or "-",
                "opened": "yes" if row["unit"] in opened else "no",
                "purpose": _purpose(root, row) or "",
            }
        )
    return rows


def review_literal_label(text: str) -> str:
    """A `mo.ui.button` label that shows `text` exactly, since marimo renders labels as Markdown."""
    return "".join(char if char.isalnum() else f"&#{ord(char)};" for char in text)


def review_item_links(
    mo: Any, status: dict[str, Any], rows: list[dict[str, str]], set_page: Any
) -> Any:
    """One button per row, keyed by unit id. The notebook holds this as a named dictionary."""
    names = {str(row["unit"]): review_item(row) for row in status["units"]}
    return mo.ui.dictionary(
        {
            row["item"]: mo.ui.button(
                label=review_literal_label(names[row["item"]]),
                on_click=lambda _, unit=row["item"]: set_page(f"unit:{unit}"),
            )
            for row in rows
        }
    )


def review_link_buttons(mo: Any, units: list[str], set_page: Any, prefix: str = "") -> Any:
    """Buttons that open units, each showing its name literally, in one named dictionary."""
    return mo.ui.dictionary(
        {
            unit: mo.ui.button(
                label=review_literal_label(f"{prefix}{review_link_label(unit)}"),
                on_click=lambda _, name=unit: set_page(f"unit:{name}"),
            )
            for unit in dict.fromkeys(units)
        }
    )


def review_items_table(mo: Any, rows: list[dict[str, str]], links: Any) -> Any:
    """The Items table. Cells hold plain unit ids for search, sort and bulk. Buttons are display."""
    formats = {"item": lambda unit: links[unit]} if rows else None
    return mo.ui.table(rows, selection="multi", format_mapping=formats)


def review_empty_items(status: dict[str, Any]) -> tuple[str, str]:
    """Why the Items table has no rows. Only real sign-offs may read as done."""
    if status["problem"]:
        return "warn", "Steps could not be read. Fix the problem shown above."
    if not status["units"]:
        return "info", "This project has no steps to review yet."
    return "success", "All done. Every step and shared file is signed off."


def review_overview_view(
    mo: Any,
    status: dict[str, Any],
    rows: list[dict[str, str]],
    pipeline_map: Any,
    only_pending: Any,
    picker: Any,
) -> Any:
    """The overview's map and Items table, or a clear message when no row needs showing."""
    if rows:
        hint = "Click an item's name to open it. Tick boxes only to sign off several at once."
        table = mo.vstack([mo.md(hint), picker])
    else:
        kind, text = review_empty_items(status)
        table = mo.callout(mo.plain_text(text), kind=kind)
    return mo.vstack(
        [
            mo.md("Click a box to open it. Hover to see what feeds it and what it feeds."),
            pipeline_map,
            mo.hstack([mo.md("**Items**"), only_pending], justify="space-between"),
            table,
        ]
    )


def review_link_label(unit: str) -> str:
    """A page link's label. Shared code shows its whole path, so that path is on the page."""
    return unit[7:] if unit.startswith("shared:") else unit.rsplit("/", 1)[-1]


def review_map(status: dict[str, Any]) -> dict[str, Any]:
    """The pipeline map: steps by depth, left to right, with shared code in a strip on top."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    filled: dict[int, int] = {}
    strip = 0
    for row in status["units"]:
        shown = review_display_status(row)
        if row["kind"] == "shared":
            count = len(row["used_by"])
            nodes.append(
                {
                    "id": row["unit"],
                    "label": review_label(row),
                    "status": shown,
                    "x": strip,
                    "y": -1,
                    "shape": "pill",
                    "sub": f"{count} steps",
                }
            )
            strip += 1
            continue
        column = int(row["depth"])
        filled[column] = filled.get(column, -1) + 1
        nodes.append(
            {
                "id": row["unit"],
                "label": review_label(row),
                "status": shown,
                "x": column,
                "y": filled[column],
                "shape": "box",
                "sub": "",
            }
        )
        edges.extend({"from": parent, "to": row["unit"]} for parent in row["upstream"])
    return {"nodes": nodes, "edges": edges, "colors": REVIEW_STATUS_COLORS}


def review_bulk_plan(
    status: dict[str, Any], picked: list[dict[str, Any]], opened: dict[str, str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """What a bulk sign-off may cover: only items opened, at the code seen when opened."""
    rows = {str(row["unit"]): row for row in status["units"]}
    allowed: list[tuple[str, str]] = []
    skipped: list[str] = []
    for choice in picked:
        row = rows[str(choice["item"])]
        if row["status"] == "reviewed":
            continue
        if row["unit"] in opened:
            allowed.append((str(row["unit"]), opened[row["unit"]]))
        else:
            skipped.append(f"{review_item(row)} (open it first)")
    return allowed, skipped


def next_to_sign_off(status: dict[str, Any], after: str | None = None) -> str | None:
    """The next unit needing a sign-off after this one, wrapping around, in pipeline order."""
    order = [str(row["unit"]) for row in status["units"]]
    pending = {str(row["unit"]) for row in status["units"] if row["status"] != "reviewed"}
    start = order.index(after) + 1 if after in order else 0
    for unit in order[start:] + order[:start]:
        if unit in pending and unit != after:
            return unit
    return None


def _key_template(text: str, key: str | None) -> str:
    """Mask an item's key where it stands alone, so key `n` never rewrites `clean.py`."""
    if not key:
        return text
    return re.sub(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", "${key}", text)


def _difference_flag(
    values: dict[str, str], before: set[str]
) -> tuple[str | None, dict[str, str], bool]:
    """The flag these differing tokens sit under, their values, and whether `=` joins them."""
    joined = [re.fullmatch(r"(--?[A-Za-z][\w-]*)=(.+)", value) for value in values.values()]
    if all(joined) and len({match.group(1) for match in joined if match}) == 1:
        flag = next(match.group(1) for match in joined if match)
        return flag, {key: value.partition("=")[2] for key, value in values.items()}, True
    if any(re.match(r"--?[A-Za-z]", value) for value in values.values()) or len(before) != 1:
        return None, values, False
    previous = next(iter(before))
    return (previous if re.fullmatch(r"--?[A-Za-z][\w-]*", previous) else None), values, False


def _shell_arguments(text: str) -> tuple[list[str], tuple[str, ...]]:
    """Each argument exactly as written, and the exact text between arguments.

    Together they rebuild `text`, so two commands that match on both are the same command.
    Unbalanced quotes raise ValueError.
    """
    import io
    import shlex

    shlex.split(text)  # Refuses unbalanced quotes and a dangling escape.
    # With a final separator, the lexer always reads exactly one character past an argument.
    stream = io.StringIO(f"{text} ")
    lexer = shlex.shlex(stream, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    arguments: list[str] = []
    gaps: list[str] = []
    start = previous_end = 0
    for _ in lexer:
        end = stream.tell()
        stop = end - 1
        raw = text[start:stop]
        first = start + len(raw) - len(raw.lstrip(lexer.whitespace))
        gaps.append(text[previous_end:first])
        arguments.append(text[first:stop])
        previous_end = stop
        start = end
    gaps.append(text[previous_end:])
    return arguments, tuple(gaps)


def _difference_parts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How a group's items differ beyond their own key, so an odd one out cannot hide.

    Compared on what actually runs: each item's resolved command with its key masked. A value
    set on an item that its command, inputs or outputs never use has no effect. A value is named
    by its flag only when every item clearly has that flag. Otherwise it is named by position.
    Commands that differ in a way the arguments cannot show still get a warning.
    """
    if len(items) < 2:
        return []
    masked = {str(item["key"]): _key_template(str(item["command"]), item["key"]) for item in items}
    if len(set(masked.values())) < 2:
        return []
    unclear = [{"plain": "Items run different commands: compare them in the table below."}]
    try:
        parsed = {key: _shell_arguments(text) for key, text in masked.items()}
    except ValueError:
        return unclear
    shapes = {key: arguments for key, (arguments, _) in parsed.items()}
    lengths = {len(tokens) for tokens in shapes.values()}
    if len(lengths) > 1:
        return unclear
    parts: list[dict[str, Any]] = []
    for position in range(lengths.pop()):
        values = {key: tokens[position] for key, tokens in shapes.items()}
        if len(set(values.values())) < 2:
            continue
        before = {tokens[position - 1] for tokens in shapes.values()} if position else set()
        flag, values, joined = _difference_flag(values, before)
        distinct = sorted(set(values.values()))
        common = max(distinct, key=lambda value: list(values.values()).count(value))
        parts.append(
            {
                "flag": flag,
                "joined": joined,
                "position": position + 1,
                "common": common,
                "odd": [(key, value) for key, value in values.items() if value != common],
            }
        )
    # Arguments are compared as written, quotes included, so every difference in them is named.
    # Only the text between them can still differ, and a newline there starts a new command.
    return parts + unclear if len({gaps for _, gaps in parsed.values()}) > 1 else parts


def _difference_note(part: dict[str, Any], code: Any, plain: Any = str) -> str:
    """One difference as a sentence, with values marked by `code` and other text by `plain`."""
    if "plain" in part:
        return str(plain(part["plain"]))
    if part["flag"] and part["joined"]:
        subject = code(part["flag"])
    elif part["flag"]:
        # A space does not prove the value belongs to the flag, which may take no value.
        subject = f"{plain('Value after ')}{code(part['flag'])}"
    else:
        subject = plain(f"Argument {part['position']}")
    odd = ", ".join(f"{plain(key)} ({code(value)})" for key, value in part["odd"])
    return f"{subject} is {code(part['common'])}, except {odd}"


def _item_differences(items: list[dict[str, Any]]) -> list[str]:
    """Each difference as plain text."""
    return [_difference_note(part, lambda value: f"`{value}`") for part in _difference_parts(items)]


def _difference_html(items: list[dict[str, Any]]) -> list[str]:
    """Each difference as escaped HTML, with its flag and values set as code."""
    import html

    return [
        _difference_note(
            part,
            lambda value: f'<code style="white-space:pre-wrap">{html.escape(value)}</code>',
            html.escape,
        )
        for part in _difference_parts(items)
    ]


def _input_rows(row: dict[str, Any], owners: dict[str, str]) -> list[dict[str, str]]:
    """Data inputs, written once. A group's per-item paths collapse to their `${key}` template."""
    seen: dict[str, str] = {}
    for item in row["items"]:
        for path in item["inputs"]:
            shown = _key_template(path, item["key"])
            source = owners.get(path)
            seen.setdefault(shown, f"step {source}" if source else "source data")
    return [{"input": path, "from": source} for path, source in seen.items()]


def _param_rows(row: dict[str, Any], since: dict[str, Any]) -> list[dict[str, str]]:
    """Parameters and what changed. Items agreeing on both share a row, others get their own."""
    changes = {(c["stage"], c["key"]): c for c in since.get("params_changed") or []}

    def note(stage: str, key: str) -> str:
        change = changes.get((stage, key))
        if change is None:
            return "unchanged" if row["review"] else ""
        if change["added"]:
            return "new"
        return f"changed, was {json.dumps(change['was'], default=str)}"

    rows: list[dict[str, str]] = []
    for key in sorted({key for values in row["params"].values() for key in values}):
        shown = {
            stage: (json.dumps(values[key], sort_keys=True, default=str), note(stage, key))
            for stage, values in row["params"].items()
            if key in values
        }
        # One row only when every item declares it and agrees, so its scope is never implied.
        if len(shown) == len(row["params"]) and len(set(shown.values())) == 1:
            value, change = next(iter(shown.values()))
            rows.append({"parameter": key, "value": value, "since sign-off": change})
            continue
        rows.extend(
            {"parameter": f"{key} ({stage})", "value": value, "since sign-off": change}
            for stage, (value, change) in shown.items()
        )
    for (stage, key), change in sorted(changes.items()):
        if change["removed"]:
            was = json.dumps(change["was"], default=str)
            rows.append(
                {
                    "parameter": f"{key} ({stage})",
                    "value": "-",
                    "since sign-off": f"removed, was {was}",
                }
            )
    return rows


def _run_changes(since: dict[str, Any]) -> list[dict[str, str]]:
    """What changed in how each stage runs: its command, folder, data inputs or outputs."""

    def parse(entries: list[str]) -> dict[str, list[Any]]:
        parsed = [json.loads(entry) for entry in entries]
        return {str(entry[0]): entry[1:] for entry in parsed}

    before = parse(since.get("commands_removed") or [])
    after = parse(since.get("commands_added") or [])
    fields = ("command", "folder", "inputs", "outputs", "code", "frozen", "settings")
    rows: list[dict[str, str]] = []
    for stage in sorted(set(before) | set(after)):
        if stage not in before or stage not in after:
            entry = after.get(stage) or before[stage]
            text = "; ".join(f"{name}: {value}" for name, value in zip(fields, entry, strict=False))
            added = stage in after
            rows.append(
                {
                    "item": stage,
                    "what": "new item" if added else "removed item",
                    "was": "" if added else text,
                    "now": text if added else "",
                }
            )
            continue
        for field, old, new in zip(fields, before[stage], after[stage], strict=False):
            if old != new:
                rows.append({"item": stage, "what": field, "was": str(old), "now": str(new)})
    return rows


def _shown_text(data: bytes) -> str | None:
    """A file as text where no two different files look alike, or None when it is not UTF-8.

    Anything that could be mistaken for a marker, and anything invisible, shows as its code
    point first. So every `⇥` on screen is a real tab and every `⟦` starts an escape.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    reserved = "\u240d\u21e5\u2423\u27e6\u27e7"
    escaped = "".join(
        f"\u27e6U+{ord(char):04X}\u27e7"
        if char in reserved or not (char.isprintable() or char in "\n\t\r")
        else char
        for char in text
    )
    return _visible(escaped)


def _diff_rows(diff: str) -> list[dict[str, Any]]:
    """A single-file unified diff as rows. Its first two lines are the file header."""
    rows: list[dict[str, Any]] = []
    number = 0
    for line in diff.split("\n")[2:]:
        if line.startswith("@@"):
            found = re.search(r"\+(\d+)", line)
            number = int(found.group(1)) - 1 if found else number
            rows.append({"kind": "gap", "line": None, "text": f"line {number + 1}"})
        elif line.startswith("\\"):
            rows.append({"kind": "note", "line": None, "text": line[2:]})
        elif line.startswith("-"):
            rows.append({"kind": "del", "line": None, "text": line[1:]})
        elif line.startswith("+"):
            number += 1
            rows.append({"kind": "add", "line": number, "text": line[1:]})
        elif line.startswith(" "):
            number += 1
            rows.append({"kind": "same", "line": number, "text": line[1:]})
    return rows


def _code_view(
    root: Path, relative: str, since: dict[str, Any], data: bytes | None
) -> dict[str, Any]:
    """One file to read: its changes in context when a diff is trustworthy, and always in full."""
    view: dict[str, Any] = {"path": relative, "state": "unchanged", "changes": None, "note": ""}
    text = _shown_text(data) if data is not None else None
    if data is None or text is None:
        return view | {"state": "unreadable", "full": [], "truncated": False}
    lines = text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    view["full"] = [
        {"kind": "same", "line": number, "text": line}
        for number, line in enumerate(lines[:REVIEW_DIFF_LINE_LIMIT], 1)
    ]
    view["truncated"] = len(lines) > REVIEW_DIFF_LINE_LIMIT
    if text and not text.endswith("\n") and not view["truncated"]:
        view["full"].append({"kind": "note", "line": None, "text": "No newline at end of file"})
    if since.get("mode") == "full":
        return view | {"state": "read in full", "note": str(since["why"])}
    if since.get("mode") != "diff":
        return view
    if relative in since["files_added"]:
        return view | {"state": "new"}
    if relative not in since["files_changed"]:
        return view
    try:
        stored = _stored_blobs(root, str(since["since"]), [relative])
    except OSError:
        stored = None
    if stored is None:
        return view | {"state": "read in full", "note": "Git could not read the reviewed file"}
    blob = stored.get(relative, ("", None))[1]
    near, why = _raw_diff(root, relative, blob, data, context=5)
    whole, _ = _raw_diff(root, relative, blob, data, context=1_000_000)
    if near is None or whole is None:
        return view | {"state": "read in full", "note": why}
    changes, full = _diff_rows(near), _diff_rows(whole)
    return view | {
        "state": "changed",
        "changes": changes[:REVIEW_DIFF_LINE_LIMIT],
        "full": full[:REVIEW_DIFF_LINE_LIMIT],
        "truncated": max(len(changes), len(full)) > REVIEW_DIFF_LINE_LIMIT,
    }


def review_code_html(rows: list[dict[str, Any]]) -> str:
    """Code rows as an HTML table. All file content is escaped, so it can only ever be text."""
    import html

    marks = {"add": "+", "del": "−"}
    cells = [
        f'<tr class="{row["kind"]}"><td>{row["line"] or ""}</td>'
        f"<td>{marks.get(row['kind'], '')}</td><td>{html.escape(row['text']) or ' '}</td></tr>"
        for row in rows
    ]
    style = (
        "<style>table.astack-code{border-collapse:collapse;width:100%;font-size:12px;"
        "font-family:ui-monospace,monospace}table.astack-code td{padding:0 8px;white-space:pre}"
        "table.astack-code td:nth-child(-n+2){opacity:.55;width:1%;text-align:right}"
        "table.astack-code tr.add{background:rgba(46,160,67,.18)}"
        "table.astack-code tr.del{background:rgba(248,81,73,.18)}"
        "table.astack-code tr.gap td,table.astack-code tr.note td{opacity:.55;font-style:italic}"
        "</style>"
    )
    return (
        f'{style}<div style="overflow:auto;max-height:520px"><table class="astack-code">'
        f"{''.join(cells)}</table></div>"
    )


def _param_usage(
    root: Path, row: dict[str, Any], since: dict[str, Any], rendered: dict[str, bytes]
) -> list[dict[str, Any]]:
    """For each changed parameter, the lines of this step's code that name it."""
    found: list[dict[str, Any]] = []
    for key in sorted({change["key"] for change in since.get("params_changed") or []}):
        name = key.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        lines: list[dict[str, Any]] = []
        for relative in [*row["files"], *row["shared"]]:
            try:
                data = rendered.get(relative)
                if relative in row["shared"]:
                    with _open_regular_file(root / relative) as handle:
                        data = handle.read()
            except OSError:
                continue
            text = _shown_text(data) if data is not None else None
            for number, line in enumerate((text or "").split("\n"), 1):
                if pattern.search(line):
                    lines.append({"path": relative, "line": number, "text": line})
        found.append({"key": key, "lines": lines})
    return found


def _review_notes(row: dict[str, Any], by_unit: dict[str, dict[str, Any]]) -> list[str]:
    """Non-blocking context: upstream steps or shared code this step relies on have moved."""
    seen: set[str] = set()
    stack = list(row["upstream"])
    while stack:
        name = stack.pop()
        if name not in seen and name in by_unit:
            seen.add(name)
            stack.extend(by_unit[name]["upstream"])
    notes = [
        f"Upstream step {name} is {review_display_status(by_unit[name])}."
        for name in sorted(seen)
        if by_unit[name]["status"] != "reviewed"
    ]
    for relative in row["shared"]:
        shared = by_unit.get(f"shared:{relative}")
        if shared and shared["status"] != "reviewed":
            notes.append(f"Shared {relative} is {review_display_status(shared)}.")
    return notes


def review_page(
    root: Path, unit: str, status: dict[str, Any], labels: dict[str, str]
) -> dict[str, Any]:
    """Everything one step's or shared file's page shows, ready to display."""
    by_unit = {str(row["unit"]): row for row in status["units"]}
    row = by_unit[unit]
    owners = {
        path: str(other["unit"])
        for other in status["units"]
        for item in other["items"]
        for path in item["outputs"]
    }
    since = row.get("since_review") or {}
    rendered: dict[str, bytes] = {}
    for relative in row["files"]:
        try:
            with _open_regular_file(root / relative) as handle:
                rendered[relative] = handle.read()
        except OSError:
            continue
    # Commands, parameters and settings come from the same row that produced the checksum.
    # Code is read when this page opens. Verify the exact bytes used for both views of each file.
    checksum = row["checksum"]
    stale = checksum is not None and (
        len(rendered) != len(row["files"])
        or checksum
        != _code_checksum(
            root, row["files"], row["commands"], row["params"], row["shared"], rendered=rendered
        )
    )
    items = [
        {
            "item": str(item["key"]),
            "command": str(item["command"]),
            "folder": str(item["wdir"]),
            "frozen": "yes" if item["frozen"] else "no",
            "code": ", ".join(item["code"]),
            "inputs": ", ".join(item["inputs"]),
            "outputs": ", ".join(
                f"{path} ({labels.get(path, 'unknown')})" for path in item["outputs"]
            ),
        }
        for item in row["items"]
        if item["key"] is not None
    ]
    return {
        "unit": unit,
        "kind": row["kind"],
        "title": review_label(row),
        "purpose": _purpose(root, row),
        "status": review_display_status(row),
        "checksum": None if stale else checksum,
        "blocked": stale or bool(row.get("uncommitted")) or checksum is None,
        "stale": stale,
        "why": "The code shown no longer matches the loaded review status."
        if stale
        else row.get("why"),
        "fix": "Refresh and review the page again" if stale else row["fix"],
        "review": row["review"],
        "revoked": row["revoked"],
        "since": since,
        "upstream": row["upstream"],
        "downstream": row["downstream"],
        "used_by": row["used_by"],
        "shared": [
            {
                "unit": f"shared:{path}",
                "path": path,
                "status": review_display_status(by_unit[f"shared:{path}"]),
            }
            for path in row["shared"]
            if f"shared:{path}" in by_unit
        ],
        "notes": _review_notes(row, by_unit),
        "inputs": _input_rows(row, owners),
        "params": _param_rows(row, since),
        "param_usage": _param_usage(root, row, since, rendered),
        "items": items,
        "differences": _item_differences(row["items"]),
        "difference_html": _difference_html(row["items"]),
        "commands": sorted(
            {
                ("[frozen, DVC will not rerun it] " if i["frozen"] else "")
                + ("" if i["wdir"] == "." else f"[in {i['wdir']}] ")
                + _key_template(str(i["command"]), i["key"])
                for i in row["items"]
            }
        ),
        "run_changes": _run_changes(since),
        "settings": [
            {
                "item": str(item["name"]),
                "setting": name,
                "value": json.dumps(value, sort_keys=True, default=str),
            }
            for item in row["items"]
            for name, value in sorted(item["settings"].items())
        ],
        "code_moves": [
            {"file": path, "change": label}
            for key, label in (
                ("files_removed", "no longer part of this step"),
                ("shared_added", "now shared code"),
                ("shared_removed", "no longer shared code"),
            )
            for path in since.get(key) or []
        ],
        "declared_code": [
            {"declared code": path}
            for path in dict.fromkeys(
                _key_template(path, item["key"]) for item in row["items"] for path in item["code"]
            )
        ],
        "code": [
            _code_view(root, relative, since, rendered.get(relative)) for relative in row["files"]
        ],
        "outputs": [
            {
                "item": str(item["key"]) if item["key"] is not None else None,
                "path": path,
                "label": labels.get(path, "unknown"),
            }
            for item in row["items"]
            for path in item["outputs"]
        ],
    }


def review_heading_html(inner: str, level: int) -> str:
    """A heading from text that is already escaped, sized so levels read apart in any theme."""
    return f'<h{level} style="{REVIEW_HEADING_STYLES[level]}">{inner}</h{level}>'


def review_file_title(path: str, state: str, level: int = 4) -> str:
    """A file's name and state, kept on one line that scrolls instead of wrapping."""
    import html

    pair = f"<code>{html.escape(path)}</code> · {html.escape(state)}"
    inner = f'<span style="display:block;overflow-x:auto;white-space:nowrap">{pair}</span>'
    return review_heading_html(inner, level)


def review_table_html(rows: list[dict[str, Any]]) -> str:
    """Facts as a compact static table. Every heading and value is escaped."""
    import html

    columns = list(dict.fromkeys(key for row in rows for key in row))
    head = "".join(f"<th>{html.escape(str(name))}</th>" for name in columns)

    def cell(value: Any) -> str:
        return f"<td>{'' if value is None else html.escape(str(value))}</td>"

    body = "".join(f"<tr>{''.join(cell(row.get(name)) for name in columns)}</tr>" for row in rows)
    style = (
        "<style>table.astack-facts{border-collapse:collapse;width:100%;font-size:13px}"
        "table.astack-facts th,table.astack-facts td{padding:2px 8px;text-align:left;"
        "vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere;"
        "border-bottom:1px solid var(--border,#d4d4d8)}"
        "table.astack-facts th{font-weight:600}</style>"
    )
    return (
        f'{style}<div style="overflow:auto;max-height:320px"><table class="astack-facts">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def review_facts_html(page: dict[str, Any]) -> str:
    """Inputs, parameters and command, side by side when there is room. All text is escaped."""
    import html

    cells = [
        review_heading_html(title, 4) + review_table_html(rows)
        for title, rows in (("Inputs", page["inputs"]), ("Parameters", page["params"]))
        if rows
    ]
    if page["commands"]:
        command = html.escape("\n".join(page["commands"]))
        cells.append(
            review_heading_html("Command", 4) + '<pre style="margin:0;font-size:13px;'
            f'white-space:pre-wrap;overflow-wrap:anywhere">{command}</pre>'
        )
    if not cells:
        return ""
    inner = "".join(f'<div style="min-width:0">{cell}</div>' for cell in cells)
    return (
        '<div style="display:grid;gap:.1rem 1.25rem;'
        f'grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr))">{inner}</div>'
    )


def _heading(text: str, level: int = 3) -> dict[str, Any]:
    import html

    return {"type": "html", "html": review_heading_html(html.escape(text), level)}


def _declared_code_on_page(page: dict[str, Any]) -> bool:
    """Whether every declared code path already appears by its exact name on this page."""
    shown = {view["path"] for view in page["code"]} | {item["path"] for item in page["shared"]}
    return all(row["declared code"] in shown for row in page["declared_code"])


def _file_sections(view: dict[str, Any]) -> list[dict[str, Any]]:
    title = review_file_title(view["path"], view["state"])
    sections: list[dict[str, Any]] = [{"type": "html", "html": title}]
    if view["state"] == "unreadable":
        text = "Not UTF-8 text. Open it in your editor to read it."
        return [*sections, {"type": "callout", "kind": "warn", "text": text}]
    if view["note"]:
        text = f"Read in full: {view['note']}"
        sections.append({"type": "callout", "kind": "info", "text": text})
    if view["changes"] is not None:
        sections.append({"type": "html", "html": review_code_html(view["changes"])})
        sections.append(
            {"type": "fold", "title": "Full file", "html": review_code_html(view["full"])}
        )
    else:
        sections.append({"type": "html", "html": review_code_html(view["full"])})
    if view["truncated"]:
        text = "Cut short. Open the file and read the rest before signing off."
        sections.append({"type": "callout", "kind": "warn", "text": text})
    return sections


def review_sections(page: dict[str, Any]) -> list[dict[str, Any]]:
    """A page's content, top to bottom, as simple typed blocks the notebook only has to draw."""
    sections: list[dict[str, Any]] = []

    def note(kind: str, text: str) -> None:
        sections.append({"type": "callout", "kind": kind, "text": text})

    if page["why"]:
        note("danger", str(page["why"]))
    if page["fix"]:
        sections.append({"type": "text", "text": f"Next: {page['fix']}"})
    if page["kind"] == "shared":
        sections.append({"type": "links", "title": "Used by", "units": page["used_by"]})
    else:
        link_groups = [
            {"title": "Upstream", "units": page["upstream"]},
            {"title": "Downstream", "units": page["downstream"]},
        ]
        sections.append({"type": "link_row", "groups": link_groups})
    if page["notes"]:
        note("info", "Check its assumptions still hold:\n" + "\n".join(page["notes"]))
    for title, rows in (
        ("How it runs changed", page["run_changes"]),
        ("Code that moved", page["code_moves"]),
    ):
        if rows:
            sections.append(_heading(title))
            sections.append({"type": "table", "rows": rows})
    facts = review_facts_html(page)
    if facts:
        sections.append({"type": "html", "html": facts})
    if page["differences"]:
        lead = "Not every item is the same:"
        sections.append(
            {
                "type": "callout",
                "kind": "warn",
                "text": "\n".join([lead, *page["differences"]]),
                "html": "<br>".join([lead, *page["difference_html"]]),
            }
        )
    if page["items"]:
        sections.append(_heading(f"{len(page['items'])} items, one sign-off"))
        sections.append({"type": "table", "rows": page["items"]})
    if page["settings"]:
        sections.append(_heading("Run settings"))
        sections.append({"type": "table", "rows": page["settings"]})
    sections.append(_heading("Code"))
    if page["declared_code"] and not _declared_code_on_page(page):
        sections.append({"type": "table", "rows": page["declared_code"]})
    if page["shared"]:
        units = [item["unit"] for item in page["shared"]]
        sections.append({"type": "links", "title": "Uses shared code", "units": units})
    if not page["code"] and page["kind"] == "step":
        note("warn", "No code file is declared for this step.")
    for view in page["code"]:
        sections.extend(_file_sections(view))
    for usage in page["param_usage"]:
        sections.append(_heading(f"{usage['key']} changed. Lines that use it:", 4))
        rows = [
            {"kind": "same", "line": line["line"], "text": f"{line['path']}: {line['text']}"}
            for line in usage["lines"]
        ]
        if rows:
            sections.append({"type": "html", "html": review_code_html(rows)})
        else:
            sections.append({"type": "text", "text": "Not named in this step's code."})
    if page["outputs"]:
        sections.append(_heading("Outputs"))
        if page["items"]:
            groups: dict[str, list[dict[str, Any]]] = {}
            for output in page["outputs"]:
                groups.setdefault(str(output["item"]), []).append(output)
            listed = [{"title": title, "outputs": items} for title, items in groups.items()]
            sections.append({"type": "output_groups", "groups": listed})
        else:
            sections.append({"type": "outputs", "outputs": page["outputs"]})
    return sections


def review_title_html(page: dict[str, Any]) -> str:
    """A page's title and one-line purpose."""
    import html

    purpose = html.escape(page["purpose"] or "No docstring.")
    title = review_heading_html(html.escape(page["title"]), 2)
    return f"{title}<p style='margin:0 0 .35rem'><em>{purpose}</em></p>"


def review_legend_html(status: dict[str, Any]) -> str:
    """The colored count of each status in the notebook's header."""
    counts: dict[str, int] = {}
    for row in status["units"]:
        shown = review_display_status(row)
        counts[shown] = counts.get(shown, 0) + 1
    pills = "".join(
        f"<span style='background:{color};color:#fff;border-radius:999px;padding:1px 9px;"
        f"font-size:12px;white-space:nowrap'>{name} {counts[name]}</span>"
        for name, color in REVIEW_STATUS_COLORS.items()
        if name in counts
    )
    return f"<div style='display:flex;flex-flow:row wrap;gap:4px 6px'>{pills}</div>"


def _safe_record_path(root: Path, unit: str) -> Path:
    """The record's path, refusing any symlink on the way so a write never follows a link."""
    path = _review_record_path(root, unit)
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"Refusing to write a review record through symlink {current}")
    return path


def _review_write_record(root: Path, unit: str, record: dict[str, Any]) -> None:
    path = _safe_record_path(root, unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each writer gets its own exclusively created temp file, so concurrent ticks never publish
    # each other's half-written file. os.replace swaps the entry itself, never a link's target.
    handle, partial = tempfile.mkstemp(prefix=".record.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        os.replace(partial, path)
    finally:
        Path(partial).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _reviewer(root: Path) -> str:
    return _git(root, "config", "user.name") or os.environ.get("USER") or "unknown reviewer"


def record_review(root: Path, unit: str, expected_checksum: str | None = None) -> dict[str, Any]:
    """Record that a person signed off a step or shared file. Only the notebook calls this.

    With expected_checksum, the code must still be what the person saw, so a click on a page
    loaded before the code changed is refused instead of approving code nobody looked at.
    """
    config = _project_config(root)
    rows, problem = _code_review(root, config)
    row = next((item for item in rows if item["unit"] == unit), None)
    if row is None:
        raise RuntimeError(f"Unknown step: {unit}")
    if row["checksum"] is None:
        reason = row.get("why") or problem or "it has no code to review"
        raise RuntimeError(f"{unit} cannot be reviewed: {reason}")
    if expected_checksum is not None and row["checksum"] != expected_checksum:
        raise RuntimeError(f"The code of {unit} changed since this page loaded. Review it again.")
    commit = _git(root, "rev-parse", "HEAD")
    if commit is None:
        raise RuntimeError("A sign-off needs a committed revision")
    # A sign-off always points at a commit that stores exactly the code read, so the next one
    # can show what changed since. Uncommitted code cannot be signed off. That covers the files
    # declaring its command and parameters too.
    # Checked against the commit the record names, not a later HEAD, and the code is hashed
    # again after, so a commit or edit landing meanwhile can never be recorded as read.
    uncommitted = _uncommitted(root, [*row["files"], *row["declared_in"]], commit)
    if uncommitted:
        raise RuntimeError(
            f"Commit the code of {unit} before signing off. Not committed as it is: "
            + ", ".join(uncommitted[:5])
        )
    checksum = _code_checksum(root, row["files"], row["commands"], row["params"], row["shared"])
    if checksum != row["checksum"]:
        raise RuntimeError(f"The code of {unit} changed while signing off. Review it again.")
    record = {
        "schema_version": 2,
        "unit": unit,
        "reviewer": _reviewer(root),
        "commit": commit,
        "code_checksum": row["checksum"],
        "files": row["files"],
        "commands": row["commands"],
        "params": row["params"],
        "shared": row["shared"],
        "code_committed": True,
        "reviewed_at": _now(),
    }
    _review_write_record(root, unit, record)
    return record


def revoke_review(root: Path, unit: str, reason: str = "") -> dict[str, Any]:
    """Withdraw a sign-off. The old record is kept inside the new one, and RELEASE blocks."""
    record, valid = _load_review(root, unit)
    if not valid or record is None or record.get("revoked"):
        raise RuntimeError(f"{unit} has no sign-off to revoke")
    revocation = {
        "schema_version": 2,
        "unit": unit,
        "revoked": True,
        "reason": " ".join(reason.split())[:500],
        "by": _reviewer(root),
        "at": _now(),
        "previous": record,
    }
    _review_write_record(root, unit, revocation)
    return revocation


def pending_reviews(root: Path) -> list[str]:
    """Steps and shared files no person has signed off as they are now. Only RELEASE blocks."""
    rows, problem = _code_review(root, _project_config(root))
    if problem:
        return [f"code review could not be checked: {problem}"]
    return [f"{row['unit']}: {row['status']}" for row in rows if row["status"] != "reviewed"]


def _review_git_bytes(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run read-only Git plumbing in binary mode so literal CR/LF bytes in paths stay intact."""
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "--no-replace-objects", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise OSError("Git timed out while reading review policy state") from error


def _decode_git_path(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OSError("Git returned a non-UTF-8 path while inspecting review policy") from error


def _verify_git_commit(root: Path, ref: str) -> tuple[str | None, str]:
    verified = _review_git_bytes(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    commit = verified.stdout.decode("ascii", "replace").strip()
    if verified.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", commit):
        return commit, ""
    detail = verified.stderr.decode("utf-8", "replace").strip() or "ref is not a commit"
    return None, detail


def _review_policy_base(root: Path, base: str | None) -> tuple[str, str]:
    """Resolve the Git comparison base ref and commit SHA without silent CI fallback."""
    if base is not None:
        candidate = base.strip()
        if (
            not candidate
            or "\x00" in candidate
            or candidate.startswith("-")
            or any(char.isspace() for char in candidate)
        ):
            raise ValueError(f"Invalid Git base ref: {base!r}")
        commit, detail = _verify_git_commit(root, candidate)
        if commit is None:
            raise ValueError(f"Could not resolve base ref {candidate!r}: {detail}")
        return candidate, commit

    github_base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if github_base:
        if (
            "\x00" in github_base
            or github_base.startswith("-")
            or any(char.isspace() for char in github_base)
        ):
            raise RuntimeError(f"Invalid GITHUB_BASE_REF value: {github_base!r}")
        for ref in (f"refs/remotes/origin/{github_base}", f"refs/heads/{github_base}"):
            commit, _ = _verify_git_commit(root, ref)
            if commit is not None:
                return ref, commit
        raise RuntimeError(
            f"Could not resolve GITHUB_BASE_REF {github_base!r}; "
            "fetch the base branch or pass --base <ref>"
        )

    candidates: list[str] = []
    remote_head = _review_git_bytes(
        root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
    )
    if remote_head.returncode == 0:
        head_ref = _decode_git_path(remote_head.stdout).strip()
        if head_ref:
            candidates.append(head_ref)
    candidates.extend(
        [
            "refs/remotes/origin/main",
            "refs/heads/main",
            "refs/remotes/origin/master",
            "refs/heads/master",
        ]
    )
    for candidate in dict.fromkeys(candidates):
        commit, _ = _verify_git_commit(root, candidate)
        if commit is not None:
            return candidate, commit
    raise RuntimeError(
        "Could not resolve a default Git base branch; pass --base <ref> or explicit paths"
    )


def _review_policy_git_paths(root: Path, *arguments: str) -> list[str]:
    result = _review_git_bytes(root, *arguments)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(arguments)}"
        raise RuntimeError(f"Could not inspect changed files: {detail}")
    return [_decode_git_path(item) for item in result.stdout.split(b"\0") if item]


def _git_blob_digest(data: bytes, oid_len: int) -> str:
    hasher = hashlib.sha256() if oid_len == 64 else hashlib.sha1()
    hasher.update(f"blob {len(data)}\0".encode("ascii"))
    hasher.update(data)
    return hasher.hexdigest()


def _git_file_blob_digest(path: Path, oid_len: int) -> str | None:
    """Hash raw file bytes as a Git blob without invoking Git clean filters."""
    hasher = hashlib.sha256() if oid_len == 64 else hashlib.sha1()
    with _open_regular_file(path) as handle:
        size = os.fstat(handle.fileno()).st_size
        hasher.update(f"blob {size}\0".encode("ascii"))
        total = 0
        while chunk := handle.read(65536):
            total += len(chunk)
            hasher.update(chunk)
    return hasher.hexdigest() if total == size else None


def _skip_worktree_paths(root: Path) -> set[str]:
    """Paths marked `skip-worktree` by Git sparse-checkout, read without running filters."""
    listing = _review_git_bytes(root, "ls-files", "-v", "-z")
    if listing.returncode != 0:
        detail = listing.stderr.decode("utf-8", "replace").strip() or "git ls-files -v failed"
        raise RuntimeError(f"Could not inspect Git sparse-checkout flags: {detail}")
    return {
        _decode_git_path(entry[2:])
        for entry in listing.stdout.split(b"\0")
        if len(entry) >= 2 and entry[:2] == b"S "
    }


def _submodule_is_dirty(sub_root: Path, *, depth: int) -> bool:
    """Check for staged, unstaged, or untracked edits inside an initialized submodule."""
    if depth > 4:
        return True
    try:
        staged = _review_policy_git_paths(
            sub_root,
            "diff-index",
            "--cached",
            "--no-renames",
            "--ignore-submodules=none",
            "--name-only",
            "-z",
            "HEAD",
        )
        if staged:
            return True
        untracked = _review_policy_git_paths(
            sub_root, "ls-files", "--others", "--exclude-standard", "-z"
        )
        if untracked:
            return True
        head_paths = set(
            _review_policy_git_paths(sub_root, "ls-tree", "-r", "--name-only", "-z", "HEAD")
        )
        return bool(_review_policy_worktree_changes(sub_root, head_paths, depth=depth))
    except (OSError, RuntimeError):
        return True


def _review_policy_worktree_changes(
    root: Path, merge_base_paths: set[str], *, depth: int = 0
) -> set[str]:
    """Detect unstaged worktree changes against the index using raw bytes (no clean filters)."""
    listing = _review_git_bytes(root, "ls-files", "--stage", "-z")
    if listing.returncode != 0:
        detail = listing.stderr.decode("utf-8", "replace").strip() or "git ls-files --stage failed"
        raise RuntimeError(f"Could not inspect Git index entries: {detail}")

    skip_worktree = _skip_worktree_paths(root)
    filemode_cfg = _review_git_bytes(root, "config", "--bool", "core.filemode")
    check_filemode = os.name != "nt" and filemode_cfg.stdout.strip() != b"false"

    changed: set[str] = set()
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        meta_raw, sep, path_raw = entry.partition(b"\t")
        if not sep:
            raise RuntimeError("Git returned malformed index entry while inspecting review policy")
        path = _decode_git_path(path_raw)
        meta_parts = meta_raw.decode("ascii", "replace").split(" ")
        if len(meta_parts) != 3:
            raise RuntimeError("Git returned malformed index metadata for review policy")
        mode, oid, stage = meta_parts
        if stage != "0" or path not in merge_base_paths:
            changed.add(path)
            continue
        if len(oid) not in {40, 64}:
            raise RuntimeError(f"Unsupported Git object id length for {path!r}")
        full_path = root / path
        try:
            st = full_path.lstat()
        except FileNotFoundError:
            if path not in skip_worktree:
                changed.add(path)
            continue
        except OSError:
            changed.add(path)
            continue
        if mode == "160000":
            if not stat.S_ISDIR(st.st_mode):
                changed.add(path)
                continue
            if (full_path / ".git").exists():
                sub_head, _ = _verify_git_commit(full_path, "HEAD")
                if sub_head != oid or _submodule_is_dirty(full_path, depth=depth + 1):
                    changed.add(path)
            continue
        if mode == "120000":
            if not stat.S_ISLNK(st.st_mode):
                changed.add(path)
                continue
            try:
                target_bytes = os.fsencode(os.readlink(full_path))
            except OSError:
                changed.add(path)
                continue
            if _git_blob_digest(target_bytes, len(oid)) != oid:
                changed.add(path)
            continue
        if mode in {"100644", "100755"}:
            if not stat.S_ISREG(st.st_mode):
                changed.add(path)
                continue
            if check_filemode and bool(st.st_mode & 0o100) != (mode == "100755"):
                changed.add(path)
                continue
            try:
                digest = _git_file_blob_digest(full_path, len(oid))
            except OSError:
                changed.add(path)
                continue
            if digest != oid:
                changed.add(path)
            continue
        changed.add(path)
    return changed


def _parse_project_config_blob(root: Path, oid: str, *, label: str) -> dict[str, Any]:
    shown = _review_git_bytes(root, "cat-file", "blob", oid)
    if shown.returncode != 0:
        detail = shown.stderr.decode("utf-8", "replace").strip() or "git cat-file failed"
        raise RuntimeError(f"Could not read {label} .astack/project.toml: {detail}")
    try:
        return tomllib.loads(shown.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Invalid {label} .astack/project.toml: {error}") from error


def _tree_project_config(root: Path, tree_ref: str, *, label: str) -> dict[str, Any]:
    """Load `.astack/project.toml` blob from a Git commit/tree without following symlinks."""
    listing = _review_git_bytes(root, "ls-tree", "-z", tree_ref, "--", ".astack/project.toml")
    if listing.returncode != 0:
        detail = listing.stderr.decode("utf-8", "replace").strip() or "git ls-tree failed"
        raise RuntimeError(f"Could not inspect {label} .astack/project.toml: {detail}")
    entries = [item for item in listing.stdout.split(b"\0") if item]
    if not entries:
        return {}
    meta_raw, sep, _ = entries[0].partition(b"\t")
    if not sep:
        raise RuntimeError(f"Git returned malformed ls-tree entry for {label} .astack/project.toml")
    parts = meta_raw.decode("ascii", "replace").split(" ")
    if len(parts) != 3:
        raise RuntimeError(
            f"Git returned malformed ls-tree metadata for {label} .astack/project.toml"
        )
    mode, obj_type, oid = parts
    if mode not in {"100644", "100755"} or obj_type != "blob":
        raise OSError(f"{label.capitalize()} .astack/project.toml must be a regular file blob")
    return _parse_project_config_blob(root, oid, label=label)


def _index_project_configs(root: Path) -> list[dict[str, Any]]:
    """Load staged `.astack/project.toml` blob(s) from the Git index without symlinks."""
    listing = _review_git_bytes(root, "ls-files", "--stage", "-z", "--", ".astack/project.toml")
    if listing.returncode != 0:
        detail = listing.stderr.decode("utf-8", "replace").strip() or "git ls-files --stage failed"
        raise RuntimeError(f"Could not inspect index .astack/project.toml: {detail}")
    configs: list[dict[str, Any]] = []
    for entry in listing.stdout.split(b"\0"):
        if not entry:
            continue
        meta_raw, sep, _ = entry.partition(b"\t")
        if not sep:
            raise RuntimeError("Git returned malformed index entry for .astack/project.toml")
        parts = meta_raw.decode("ascii", "replace").split(" ")
        if len(parts) != 3:
            raise RuntimeError("Git returned malformed index metadata for .astack/project.toml")
        mode, oid, _ = parts
        if mode not in {"100644", "100755"}:
            raise OSError("Staged .astack/project.toml must be a regular file blob")
        configs.append(_parse_project_config_blob(root, oid, label="staged"))
    return configs


def _merge_review_policies(
    base_policy: dict[str, Any], candidate_policy: dict[str, Any]
) -> dict[str, Any]:
    """Preserve trusted base scrutiny while allowing candidate config to add requirements."""
    science_required = (
        "scientific_credibility" in base_policy["required_axes"]
        or "scientific_credibility" in candidate_policy["required_axes"]
    )
    return {
        **candidate_policy,
        "risk": max((base_policy["risk"], candidate_policy["risk"]), key=RISK_LEVELS.index),
        "risk_floor": max(
            (base_policy["risk_floor"], candidate_policy["risk_floor"]), key=RISK_LEVELS.index
        ),
        "risk_reasons": list(
            dict.fromkeys([*candidate_policy["risk_reasons"], *base_policy["risk_reasons"]])
        ),
        "protected_paths": sorted(
            set(candidate_policy["protected_paths"]) | set(base_policy["protected_paths"])
        ),
        "scientific_paths": sorted(
            set(candidate_policy["scientific_paths"]) | set(base_policy["scientific_paths"])
        ),
        "matched_scientific_paths": sorted(
            set(candidate_policy["matched_scientific_paths"])
            | set(base_policy["matched_scientific_paths"])
        ),
        "required_axes": ["acceptance", "engineering_reproducibility"]
        + (["scientific_credibility"] if science_required else []),
        "scientific_applicability": "required" if science_required else "interpretive",
    }


def _git_worktree_root(root: Path) -> Path | None:
    top = _review_git_bytes(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout:
        return None
    raw = top.stdout.rstrip(b"\r\n")
    return Path(_decode_git_path(raw)).resolve()


def _review_policy_changed_paths(root: Path, base: str | None) -> tuple[str, str, str, list[str]]:
    """Collect committed, staged, unstaged, and untracked changes without running Git filters."""
    toplevel = _git_worktree_root(root)
    if toplevel is None:
        raise RuntimeError("Project is not in a Git worktree; pass explicit paths")
    if toplevel != root:
        raise RuntimeError(
            f"Run review-policy from the Git repository root ({toplevel}), "
            f"not a subdirectory ({root}), or pass explicit paths"
        )
    head_commit, _ = _verify_git_commit(root, "HEAD")
    if head_commit is None:
        raise RuntimeError("Git HEAD has no commits yet; pass explicit paths")
    base_ref, base_commit = _review_policy_base(root, base)
    merge_base_result = _review_git_bytes(root, "merge-base", "HEAD", base_commit)
    merge_base = merge_base_result.stdout.decode("ascii", "replace").strip()
    if merge_base_result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", merge_base):
        detail = (
            merge_base_result.stderr.decode("utf-8", "replace").strip()
            or f"no common ancestor with {base_ref}"
        )
        raise RuntimeError(
            f"Could not resolve merge-base between HEAD and {base_ref}: {detail}; "
            "pass --base <ref> or explicit paths"
        )
    changed: set[str] = set()
    # Plumbing `diff-tree` and `diff-index --cached` never touch the worktree or run clean filters.
    # `--no-renames` inspects both old and new paths; `--ignore-submodules=none` keeps gitlinks.
    changed.update(
        _review_policy_git_paths(
            root,
            "diff-tree",
            "-r",
            "--no-renames",
            "--ignore-submodules=none",
            "--name-only",
            "-z",
            merge_base,
            "HEAD",
        )
    )
    changed.update(
        _review_policy_git_paths(
            root,
            "diff-index",
            "--cached",
            "--no-renames",
            "--ignore-submodules=none",
            "--name-only",
            "-z",
            merge_base,
        )
    )
    merge_base_paths = set(
        _review_policy_git_paths(root, "ls-tree", "-r", "--name-only", "-z", merge_base)
    )
    changed.update(_review_policy_worktree_changes(root, merge_base_paths))
    changed.update(
        _review_policy_git_paths(root, "ls-files", "--others", "--exclude-standard", "-z")
    )
    return base_ref, base_commit, merge_base, sorted(changed)


def run_review_policy(
    root: Path,
    *,
    base: str | None = None,
    paths: list[str] | None = None,
    requested_risk: str = "auto",
    scientific_changes: bool = False,
) -> dict[str, Any]:
    """Classify changed or explicit paths with `project_policy.review_policy` and active lenses."""
    root = root.resolve()
    if base is not None and paths is not None:
        raise ValueError("--base cannot be combined with explicit paths")
    if (root / ".astack").is_symlink() or (root / ".astack/project.toml").is_symlink():
        raise OSError("Project configuration must not use a symlink")
    _check_review_config_file(root)
    if paths is not None:
        toplevel = _git_worktree_root(root)
        if toplevel is not None and toplevel != root:
            raise RuntimeError(
                f"Run review-policy from the Git repository root ({toplevel}), "
                f"not a subdirectory ({root})"
            )
        source = "explicit"
        base_ref: str | None = None
        base_commit: str | None = None
        merge_base: str | None = None
        candidate_paths = list(paths)
    else:
        source = "git"
        base_ref, base_commit, merge_base, candidate_paths = _review_policy_changed_paths(
            root, base
        )
    literal = source == "git"
    candidate_policy = review_policy(
        root,
        candidate_paths,
        requested_risk=requested_risk,
        scientific_changes=scientific_changes,
        literal_paths=literal,
    )
    if merge_base is not None:
        # Union merge_base, HEAD, staged index, and worktree configs so neither candidate edits
        # nor sparse-checkout omissions can hide declared scientific review requirements.
        snapshot_configs = [
            _tree_project_config(root, merge_base, label="base"),
            _tree_project_config(root, "HEAD", label="HEAD"),
            *_index_project_configs(root),
        ]
        policy = candidate_policy
        for snapshot_config in snapshot_configs:
            snapshot_policy = review_policy_for_config(
                snapshot_config,
                candidate_paths,
                requested_risk=requested_risk,
                scientific_changes=scientific_changes,
                literal_paths=literal,
            )
            policy = _merge_review_policies(snapshot_policy, policy)
    else:
        policy = candidate_policy
    if literal:
        reported_paths = sorted(set(candidate_paths))
    else:
        reported_paths = sorted(
            {
                "/".join(
                    part for part in item.replace("\\", "/").split("/") if part and part != "."
                )
                for item in candidate_paths
            }
        )
    lenses: list[str] = []
    if policy["protected_paths"]:
        lenses.append("security")
    if "scientific_credibility" in policy["required_axes"]:
        lenses.append("scientific-robustness")
    return {
        **policy,
        "source": source,
        "base": base_ref,
        "base_commit": base_commit,
        "merge_base": merge_base,
        "paths": reported_paths,
        "lenses": lenses,
    }
