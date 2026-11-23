"""DVC's resolved pipeline as astack sees it: stages, the edges between them, and their state."""

from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    _READ_ONLY,
    DERIVED_LIFECYCLES,
    DVC_PIPELINE_PROBE,
    Any,
    Path,
    PureWindowsPath,
    _run,
    canonical_checkout,
    configparser,
    dvc_cache_configured_path,
    dvc_cache_root,
    io,
    json,
    os,
    re,
    stat,
    subprocess,
    sys,
    tempfile,
)
from .validation import _classify_output_path


def _safe_pipeline_path(value: object) -> str | None:
    text = str(value or "").strip()
    path = Path(text)
    windows_path = PureWindowsPath(text)
    if (
        not text
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    return path.as_posix()


def _has_symlink_component(path: Path) -> bool:
    system_links = {Path("/tmp"), Path("/var"), Path("/etc")} if sys.platform == "darwin" else set()
    current = Path(path.anchor) if path.anchor else Path(".")
    for part in path.parts[1:] if path.anchor else path.parts:
        current = current / part
        if current in system_links:
            continue
        if current.is_symlink():
            return True
    return False


def _verify_private_ancestors(path: Path, *, label: str) -> None:
    if os.name == "nt":
        return
    system_links = {Path("/tmp"), Path("/var"), Path("/etc")} if sys.platform == "darwin" else set()
    parent = path.parent
    current = Path(parent.anchor) if parent.anchor else Path(".")
    for part in parent.parts[1:] if parent.anchor else parent.parts:
        current = current / part
        if current in system_links:
            continue
        if not current.exists():
            break
        info = current.stat()
        if info.st_uid not in {0, os.getuid()}:
            raise RuntimeError(f"Refusing to use {label} under a directory owned by another user")
        if info.st_mode & 0o022 != 0 and not (info.st_mode & stat.S_ISVTX):
            raise RuntimeError(f"Refusing to use {label} under a directory writable by other users")


def _verify_private_directory(path: Path, *, label: str, allow_other_read: bool = False) -> None:
    if _has_symlink_component(path):
        raise RuntimeError(f"Refusing to use a symlinked {label}")
    _verify_private_ancestors(path, label=label)
    if os.name != "nt" and path.exists():
        info = path.stat()
        forbidden = 0o022 if allow_other_read else 0o077
        if info.st_uid != os.getuid() or info.st_mode & forbidden != 0:
            detail = "writable by" if allow_other_read else "accessible to"
            raise RuntimeError(f"Refusing to use {label} {detail} other users")


def _ensure_private_directory(path: Path, *, label: str) -> None:
    # DVC's diskcache state uses pickle (CVE-2025-69872) and DVC creates site_cache_dir/repo with
    # mode 0777, so keep cache and state dirs owner-only on POSIX and refuse previously
    # group/other-writable directories or writable parents instead of reusing tampered contents.
    if _has_symlink_component(path):
        raise RuntimeError(f"Refusing to configure {label} through a symlink")
    _verify_private_ancestors(path, label=label)
    if os.name != "nt" and path.exists():
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022 != 0:
            raise RuntimeError(f"Refusing to reuse {label} writable by other users")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)


def _unquote_dvc_config_value(raw: str) -> str:
    # DVC loads .dvc/config with ConfigObj, which strips outer quotes and inline # comments.
    value = raw.strip()
    for quote in ('"""', "'''", '"', "'"):
        if value.startswith(quote):
            end = value.find(quote, len(quote))
            if end != -1 and not value[end + len(quote) :].split("#", 1)[0].strip():
                return value[len(quote) : end]
    return value.split("#", 1)[0].strip()


def _quote_dvc_config_value(value: str) -> str:
    # ConfigObj treats unquoted `#` as an inline comment, so quote paths written to config.local.
    if "\n" in value or "\r" in value:
        raise RuntimeError("DVC cache path cannot contain line breaks")
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    if '"""' not in value and not value.endswith('"'):
        return f'"""{value}"""'
    if "'''" not in value and not value.endswith("'"):
        return f"'''{value}'''"
    raise RuntimeError("DVC cache path cannot be safely serialized in DVC config")


def _unquote_dvc_section_name(raw: str) -> str:
    # Inside `[...]`, `#` is part of the section name; strip only matching outer quotes.
    value = raw.strip()
    for quote in ('"""', "'''", '"', "'"):
        if len(value) >= 2 * len(quote) and value.startswith(quote) and value.endswith(quote):
            return value[len(quote) : -len(quote)]
    return value


def _normalize_dvc_section_headers(text: str) -> str:
    # Match ConfigObj's section-header grammar so ConfigParser does not accept trailing garbage
    # or swallow bracketed inline comments like `[cache] # [note]` into the section name.
    pattern = re.compile(
        r"""^
        (\s*)
        ((?:\[\s*)+)
        (
            (?:"\s*\S.*?\s*")|
            (?:'\s*\S.*?\s*')|
            (?:[^'"\s].*?)
        )
        ((?:\s*\])+)
        \s*(\#.*)?
        $""",
        re.VERBOSE,
    )
    normalized: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        if not raw_line.lstrip().startswith("["):
            normalized.append(raw_line)
            continue
        ending = "\n" if raw_line.endswith("\n") else ""
        stripped_line = raw_line.removesuffix("\n").removesuffix("\r")
        match = pattern.match(stripped_line)
        if match is None or match.group(2).count("[") != match.group(4).count("]"):
            raise RuntimeError("DVC configuration has an invalid section header")
        normalized.append(
            f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}{ending}"
        )
    return "".join(normalized)


def _read_dvc_config_text(text: str, section: str, key: str) -> str | None:
    text = text.removeprefix("\ufeff")
    try:
        from configobj import ConfigObj, ConfigObjError  # type: ignore[import-not-found]
    except ImportError:
        normalized = _normalize_dvc_section_headers(text)
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(normalized)
        except configparser.Error as exc:
            raise RuntimeError(f"DVC configuration is invalid: {exc}") from exc
        seen_section = False
        found: str | None = None
        for sec_name in parser.sections():
            if _unquote_dvc_section_name(sec_name).lower() != section:
                continue
            if seen_section:
                raise RuntimeError(f"Duplicate DVC configuration section: {section}") from None
            seen_section = True
            for raw_key, raw_value in parser.items(sec_name):
                if _unquote_dvc_config_value(raw_key).lower() == key:
                    if found is not None:
                        raise RuntimeError(
                            f"Duplicate DVC configuration for {section}.{key}"
                        ) from None
                    found = _unquote_dvc_config_value(raw_value)
        return found
    try:
        loaded = ConfigObj(io.StringIO(text)).dict()
    except ConfigObjError as exc:
        raise RuntimeError(f"DVC configuration is invalid: {exc}") from exc
    seen_section = False
    found = None
    for sec_name, values in loaded.items():
        if (
            not isinstance(sec_name, str)
            or sec_name.lower() != section
            or not isinstance(values, dict)
        ):
            continue
        if seen_section:
            raise RuntimeError(f"Duplicate DVC configuration section: {section}")
        seen_section = True
        for k, val in values.items():
            if isinstance(k, str) and k.lower() == key:
                if not isinstance(val, str):
                    raise RuntimeError(f"DVC configuration {section}.{key} must be a string")
                if found is not None:
                    raise RuntimeError(f"Duplicate DVC configuration for {section}.{key}")
                found = val
    return found


def _read_dvc_config_value(config_path: Path, section: str, key: str) -> str | None:
    if config_path.is_symlink():
        raise RuntimeError(f"Refusing to read symlinked DVC configuration: {config_path.name}")
    if not config_path.is_file():
        return None
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"DVC configuration is invalid: {exc}") from exc
    return _read_dvc_config_text(text, section, key)


def _set_dvc_config_value(
    parser: configparser.ConfigParser, section: str, key: str, value: str
) -> bool:
    matches = [
        name for name in parser.sections() if _unquote_dvc_section_name(name).lower() == section
    ]
    if len(matches) > 1:
        raise RuntimeError(f"Duplicate DVC configuration section: {section}")
    if matches:
        target_section = matches[0]
    else:
        parser.add_section(section)
        target_section = section
    key_matches = [
        (raw_key, raw_value)
        for raw_key, raw_value in parser.items(target_section)
        if _unquote_dvc_config_value(raw_key).lower() == key
    ]
    if len(key_matches) > 1:
        raise RuntimeError(f"Duplicate DVC configuration for {section}.{key}")
    if key_matches:
        existing_key, existing_value = key_matches[0]
        if existing_key == key and existing_value == value:
            return False
        if existing_key != key:
            parser.remove_option(target_section, existing_key)
    parser.set(target_section, key, value)
    return True


def _new_private_temp_site_cache() -> tempfile.TemporaryDirectory[str]:
    base = Path(tempfile.gettempdir())
    if _has_symlink_component(base):
        raise RuntimeError("Refusing to create a temporary DVC site cache through a symlink")
    _verify_private_ancestors(base / "probe", label="temporary DVC_SITE_CACHE_DIR")
    temp_dir = tempfile.TemporaryDirectory(prefix="astack-dvc-site-cache-")
    try:
        _verify_private_directory(Path(temp_dir.name), label="temporary DVC_SITE_CACHE_DIR")
    except Exception:
        temp_dir.cleanup()
        raise
    return temp_dir


def _configured_site_cache_dir(root: Path) -> tuple[Path | None, str]:
    # Match DVC's dvc.dirs.site_cache_dir(): use the exact DVC_SITE_CACHE_DIR string without
    # stripping whitespace or expanding ~ so validation checks the same path DVC opens.
    env_value = os.environ.get("DVC_SITE_CACHE_DIR")
    if env_value:
        raw = Path(env_value)
        return raw if raw.is_absolute() else root / raw, "env"
    for config_path in (root / ".dvc" / "config.local", root / ".dvc" / "config"):
        value = _read_dvc_config_value(config_path, "core", "site_cache_dir")
        if value is not None and value != "":
            raw = Path(value)
            return raw if raw.is_absolute() else root / raw, "config"
    return None, "default"


def _ensure_dvc_cache(root: Path) -> Path:
    config_path = root / ".dvc" / "config.local"
    tmp_dir = config_path.parent / "tmp"
    site_cache = tmp_dir / "site-cache"
    if (
        config_path.parent.is_symlink()
        or config_path.is_symlink()
        or tmp_dir.is_symlink()
        or site_cache.is_symlink()
    ):
        raise RuntimeError("Refusing to configure a DVC cache through a symlink")
    canonical = Path(canonical_checkout(root))
    raw_cache = Path(dvc_cache_configured_path(canonical))
    if _has_symlink_component(raw_cache):
        raise RuntimeError("Refusing to configure a DVC cache through a symlink")
    cache = Path(dvc_cache_root(canonical))
    if _has_symlink_component(cache):
        raise RuntimeError("Refusing to configure a DVC cache through a symlink")
    env_site, site_source = _configured_site_cache_dir(root)
    if site_source == "env" and env_site is not None:
        _verify_private_directory(env_site, label="DVC_SITE_CACHE_DIR")
        if not env_site.exists():
            _ensure_private_directory(env_site, label="DVC_SITE_CACHE_DIR")
    _ensure_private_directory(cache, label="DVC cache")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_directory(tmp_dir, label="DVC tmp directory")
    _ensure_private_directory(site_cache, label="DVC site_cache_dir")
    parser = configparser.ConfigParser(interpolation=None)
    original_text: str | None = None
    if config_path.exists():
        try:
            original_text = config_path.read_text(encoding="utf-8")
            cleaned = _normalize_dvc_section_headers(original_text.removeprefix("\ufeff"))
            parser.read_string(cleaned)
        except (OSError, UnicodeDecodeError, configparser.Error) as exc:
            raise RuntimeError(f"DVC local configuration is invalid: {exc}") from exc
    expected_cache = str(cache)
    expected_site_cache = str(site_cache.resolve())
    changed = _set_dvc_config_value(parser, "cache", "dir", _quote_dvc_config_value(expected_cache))
    changed = (
        _set_dvc_config_value(
            parser, "core", "site_cache_dir", _quote_dvc_config_value(expected_site_cache)
        )
        or changed
    )
    if changed:
        buffer = io.StringIO()
        parser.write(buffer)
        candidate_text = buffer.getvalue()
    else:
        candidate_text = original_text or ""
    if _read_dvc_config_text(candidate_text, "cache", "dir") != expected_cache:
        raise RuntimeError("DVC local cache.dir did not round-trip to the validated path")
    if _read_dvc_config_text(candidate_text, "core", "site_cache_dir") != expected_site_cache:
        raise RuntimeError("DVC local core.site_cache_dir did not round-trip to the validated path")
    if changed:
        config_path.write_text(candidate_text, encoding="utf-8")
    return cache


def _dvc_pipeline(
    root: Path,
    targets: list[str] | None = None,
    *,
    read_only: bool = False,
    stages_only: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """DVC's resolved stages and status, or None with the reason they could not be read.

    With targets, DVC also resolves which stages `dvc repro <targets>` would consider, and an
    unknown target fails the probe.
    Code review can use the stage declarations without hashing outputs or dependencies for status.
    """
    command = ["uv", "run", "--locked", "python", "-c", DVC_PIPELINE_PROBE]
    if stages_only:
        command.append("--stages-only")
    reading = read_only or _READ_ONLY["on"]
    temp_site_cache: tempfile.TemporaryDirectory[str] | None = None
    try:
        effective_site_cache: str | None = None
        if not reading:
            _ensure_dvc_cache(root)
            configured_site, site_source = _configured_site_cache_dir(root)
            if site_source == "env" and configured_site is not None:
                effective_site_cache = str(configured_site.resolve())
            else:
                effective_site_cache = str((root / ".dvc" / "tmp" / "site-cache").resolve())
        else:
            dvc_dir = root / ".dvc"
            tmp_dir = dvc_dir / "tmp"
            if dvc_dir.is_symlink() or tmp_dir.is_symlink():
                raise RuntimeError("Refusing to run DVC with a symlinked .dvc/tmp")
            _verify_private_directory(tmp_dir, label="DVC tmp directory", allow_other_read=True)
            configured_site, site_source = _configured_site_cache_dir(root)
            if site_source == "env" and configured_site is not None:
                _verify_private_directory(configured_site, label="DVC_SITE_CACHE_DIR")
                if os.name != "nt" and not configured_site.exists():
                    raise RuntimeError("DVC_SITE_CACHE_DIR does not exist")
                effective_site_cache = str(configured_site.resolve())
            elif site_source == "config" and configured_site is not None:
                _verify_private_directory(configured_site, label="DVC site_cache_dir")
                if configured_site.exists():
                    effective_site_cache = str(configured_site.resolve())
                else:
                    temp_site_cache = _new_private_temp_site_cache()
                    effective_site_cache = temp_site_cache.name
            else:
                temp_site_cache = _new_private_temp_site_cache()
                effective_site_cache = temp_site_cache.name
        options: dict[str, Any] = {"timeout": 120} if reading else {}
        env = os.environ.copy()
        if targets:
            env["ASTACK_DVC_TARGETS"] = json.dumps(targets)
        if effective_site_cache is not None:
            env["DVC_SITE_CACHE_DIR"] = effective_site_cache
        options["env"] = env
        result = _run(command, cwd=root, **options)
    except subprocess.TimeoutExpired:
        return None, "DVC timed out reading the pipeline"
    except (OSError, RuntimeError, UnicodeError) as exc:
        return None, f"DVC could not run: {exc}"
    finally:
        if temp_site_cache is not None:
            temp_site_cache.cleanup()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return None, "DVC could not report: " + (detail[-1][:200] if detail else "no output")
    try:
        value = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return None, "DVC did not return JSON"
    if (
        not isinstance(value, dict)
        or (not stages_only and not isinstance(value.get("status"), dict))
        or not isinstance(value.get("stages"), list)
    ):
        return None, "DVC returned an unexpected shape"
    return value, ""


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _derived_location(relative: str) -> bool:
    """Paths a pipeline writes, so a stage may read them only as another stage's output."""
    return _classify_output_path(relative)["lifecycle"] in DERIVED_LIFECYCLES


def _real_relative(root: Path, relative: str) -> str | None:
    """A project path's real location relative to the real project root, or None outside it."""
    try:
        return (root / relative).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError, RuntimeError):
        return None


def _tracked_files(root: Path, stages: list[dict[str, Any]]) -> set[str]:
    """Real paths of every file DVC tracks as a stage output, ignored files excluded."""
    tracked: set[str] = set()
    for stage in stages:
        for out in stage["outs"]:
            if not isinstance(out, dict):
                continue
            members = (
                [str(item) for item in out.get("files", [])]
                if out.get("dir")
                else [str(out.get("path"))]
            )
            for member in members:
                real = _real_relative(root, member)
                if real is not None:
                    tracked.add(real)
    return tracked


def _uncovered_file(root: Path, dep: str, tracked: set[str]) -> str | None:
    """The first file under a derived dep that DVC does not track as an output, or None.

    Files are compared by real path, so a symlink to a declared output is covered, while a file
    `.dvcignore` hides inside a declared folder, or any undeclared file, is not.
    """
    real = _real_relative(root, dep)
    if real is None:
        return dep
    if real in tracked:
        return None
    folder = root.resolve() / real
    if not folder.is_dir():
        return dep
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in sorted(directories):
            if Path(current, name).is_symlink():
                return Path(current, name).relative_to(root.resolve()).as_posix()
        for name in sorted(files):
            member = Path(current, name).relative_to(root.resolve()).as_posix()
            if _real_relative(root, member) not in tracked:
                return member
    return None


def _stage_edges(
    root: Path, stages: list[dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Each stage's upstream stages, and the undeclared derived input of any stage that has one.

    A dep links to a stage when it overlaps one of that stage's outputs, by path, real path, or
    where a tracked symlink inside it points. Separately, a dep in a derived location must be
    fully covered by declared outputs: anything else means the pipeline wrote data DVC does not
    track, so nothing can vouch for it.
    """
    produced: list[tuple[str, str]] = []
    for stage in stages:
        for out in stage["outs"]:
            if not isinstance(out, dict):
                continue
            names = {str(out.get("path")), str(out.get("real") or out.get("path"))}
            aliases = out.get("aliases")
            names |= {str(item) for item in aliases} if isinstance(aliases, dict) else set()
            produced.extend((name, str(stage["name"])) for name in names)
    tracked = _tracked_files(root, stages)
    upstream: dict[str, set[str]] = {}
    orphans: dict[str, str] = {}
    for stage in stages:
        name = str(stage["name"])
        upstream[name] = set()
        for dep in stage.get("deps", []):
            if not isinstance(dep, dict):
                continue
            paths = {str(dep.get("path")), str(dep.get("real") or dep.get("path"))}
            upstream[name] |= {
                producer
                for path in paths
                for output, producer in produced
                if _overlaps(path, output)
            } - {name}
            lexical = str(dep.get("path"))
            if any(_safe_pipeline_path(path) and _derived_location(path) for path in paths):
                uncovered = _uncovered_file(root, lexical, tracked)
                if uncovered is not None:
                    orphans.setdefault(name, uncovered)
    return upstream, orphans


def _stage_states(
    root: Path,
    stages: list[dict[str, Any]],
    status: dict[str, Any],
    untrusted: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Each stage's own state, then carried down the pipeline.

    `dvc status` checks only a stage's direct deps, so a stage downstream of a stale one looks
    unchanged until the upstream stage reruns. Unknown wins over outdated, so a frozen,
    unreadable or untraceable stage never lets anything below it count as current.
    """
    upstream, orphans = _stage_edges(root, stages)
    states: dict[str, tuple[str, str]] = {}
    for stage in stages:
        name = str(stage["name"])
        if stage.get("frozen"):
            states[name] = ("unknown", "its stage is frozen, so DVC does not check its inputs")
        elif untrusted and name in untrusted:
            states[name] = ("unknown", untrusted[name])
        elif name in orphans:
            states[name] = (
                "unknown",
                f"its stage reads {orphans[name]}, which no stage declares as an output",
            )
        elif name in status:
            states[name] = ("outdated", "")
        else:
            states[name] = ("current", "matches dvc.lock")
    changed = True
    while changed:
        changed = False
        for name, parents in upstream.items():
            if states[name][0] == "unknown":
                continue
            for parent in sorted(parents):
                label = states.get(parent, ("unknown", ""))[0]
                if label == "unknown" or (label != "current" and states[name][0] == "current"):
                    states[name] = (
                        "unknown" if label == "unknown" else "outdated",
                        f"upstream stage {parent} is {label}",
                    )
                    changed = True
                    break
    return states
