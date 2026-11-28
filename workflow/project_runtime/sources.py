from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    _DIGEST_MEMO,
    _DIGEST_POLICY,
    DEFAULT_SNAPSHOT_LIMIT_GB,
    EPISTEMIC_FIELDS,
    EPISTEMIC_STATES,
    EXTERNAL_ROOT_ENV_PREFIX,
    IN_PLACE_PREFIX,
    IN_PLACE_REASONS,
    OS_METADATA_FILENAMES,
    PLACEHOLDER_EVIDENCE_VALUES,
    SOURCE_IDENTITY_METHODS,
    SOURCE_REQUIRED_FIELDS,
    SPATIAL_FIELDS,
    UTC,
    Any,
    Finding,
    Path,
    _cached_digest,
    _cached_sha256,
    _file_stamp,
    _finding,
    _run,
    _safe_managed_path,
    _sha256,
    _stable_stamp,
    _viewer_notebook,
    canonical_checkout,
    data_root,
    date,
    datetime,
    external_root,
    hashlib,
    json,
    os,
    project_config,
    re,
    tomllib,
    yaml,
)


def _expected_checksum(value: object) -> tuple[str, str] | None:
    """Parse a checksum as (kind, hex): kind is "file", "tree" (legacy v1) or "tree-v2"."""
    text = str(value or "").lower()
    kind = "file"
    if text.startswith("sha256-tree-v1:"):
        kind = "tree"
        text = text.removeprefix("sha256-tree-v1:")
    elif text.startswith("sha256-tree-v2:"):
        kind = "tree-v2"
        text = text.removeprefix("sha256-tree-v2:")
    elif text.startswith("sha256:"):
        text = text.removeprefix("sha256:")
    return (kind, text) if re.fullmatch(r"[0-9a-f]{64}", text) else None


def _is_os_metadata(path: Path) -> bool:
    """Finder and Explorer write these on browse. They are never source data.

    Only raw coverage skips them. Checksums hash every file, so keep them out of source trees.
    """
    return path.name in OS_METADATA_FILENAMES or path.name.startswith("._")


def _data_files(directory: Path) -> list[Path]:
    return sorted(
        item for item in directory.rglob("*") if item.is_file() and not _is_os_metadata(item)
    )


def _tree_sha256(directory: Path) -> str:
    """Legacy sha256-tree-v1. Its framing is ambiguous when file bytes contain NUL (#260)."""
    digest = hashlib.sha256()
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def _tree_sha256_v2(directory: Path) -> str:
    """sha256-tree-v2: each file is framed by its path, NUL, and its fixed-length SHA-256."""
    digest = hashlib.sha256(b"astack-sha256-tree-v2\0")
    for relative, path in sorted(_tree_v2_files(directory, "")):
        digest.update(relative.encode() + b"\0" + _cached_sha256(path).encode() + b"\n")
    return digest.hexdigest()


def _tree_v2_files(directory: Path, prefix: str) -> list[tuple[str, Path]]:
    """List every file under a tree, raising instead of skipping anything it cannot read."""
    files: list[tuple[str, Path]] = []
    with os.scandir(directory) as entries:  # an unreadable directory raises, never hides files
        for entry in entries:
            relative = f"{prefix}{entry.name}"
            path = Path(entry.path)
            if entry.is_symlink():
                if not path.is_file():
                    raise RuntimeError(
                        f"{relative} is a symlink to a directory or a missing target; copy the "
                        "files into the source directory instead"
                    )
                files.append((relative, path))
            elif entry.is_dir(follow_symlinks=False):
                files.extend(_tree_v2_files(path, f"{relative}/"))
            elif entry.is_file(follow_symlinks=False):
                files.append((relative, path))
            else:
                raise RuntimeError(f"{relative} is not a regular file or directory")
    return files


def _tree_manifest(directory: Path) -> list[list[object]] | None:
    """Every file's path and stamp, or None when any file is not cacheable."""
    entries: list[list[object]] = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        stamp = _file_stamp(path)
        if not _stable_stamp(stamp):
            return None
        entries.append([path.relative_to(directory).as_posix(), *stamp])
    return entries


def _cached_tree_sha256(directory: Path) -> str:
    """Return the tree digest, reusing it only while the whole file set and stamps are unchanged."""
    entries = _tree_manifest(directory)
    if entries is None:
        return _tree_sha256(directory)
    key = hashlib.sha256(json.dumps(entries).encode()).hexdigest()
    cache_key = f"tree:{directory.resolve()}"
    digest = _cached_digest(cache_key, key, lambda: _tree_sha256(directory))
    # Re-enumerate after hashing: a file added, removed, or changed meanwhile invalidates it.
    if _tree_manifest(directory) != entries:
        _DIGEST_MEMO.pop(cache_key, None)
        return _tree_sha256(directory)
    return digest


def _is_gitignored_raw_path(root: Path, path_value: str) -> bool:
    """True when `path_value` is a safe relative path under `data/raw/` ignored by Git."""
    text = path_value.strip()
    if not text or "://" in text or "\x00" in text:
        return False
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return False
    parts = path.parts
    if len(parts) < 2 or parts[:2] != ("data", "raw"):
        return False
    # Linked worktrees symlink `data/raw`; Git rejects check-ignore pathspecs beneath a symlink.
    target = "data/raw" if (root / "data/raw").is_symlink() else path.as_posix()
    # `git check-ignore` checks `.gitignore` rules without requiring the file on disk (CI).
    result = _run(["git", "check-ignore", "--quiet", "--", target], cwd=root)
    return result.returncode == 0


def _is_local_only_source(
    root: Path, value: dict[str, Any], *, infer_defaults: bool = True
) -> bool:
    """True when limited CI treats a source as local-only."""
    availability = value.get("availability")
    if availability == "local_only":
        return True
    if availability == "ci_available" or not infer_defaults:
        return False
    if value.get("storage") == "in_place":
        return True
    return _is_gitignored_raw_path(root, str(value.get("path") or ""))


def _has_local_only_sources(root: Path, *, infer_defaults: bool = True) -> bool:
    """True when `data/sources.yml` declares at least one local-only source."""
    if yaml is None:
        return False
    sources_path = root / "data/sources.yml"
    try:
        data = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return False
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    if not isinstance(sources, dict):
        return False
    return any(
        isinstance(value, dict)
        and _is_local_only_source(root, value, infer_defaults=infer_defaults)
        for value in sources.values()
    )


def _local_source_path(root: Path, value: object) -> Path | None:
    text = str(value or "")
    if not text or "://" in text:
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    candidate = (root / path).resolve(strict=False)
    resolved_root = root.resolve()
    allowed_roots = {resolved_root}
    raw_path = root / "data/raw"
    if raw_path.exists():
        allowed_roots.add(raw_path.resolve())
    try:
        configured_data = data_root(root)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        configured_data = root / "data"
    allowed_roots.update({configured_data.resolve(), (configured_data / "raw").resolve()})
    if not any(candidate == allowed or allowed in candidate.parents for allowed in allowed_roots):
        return None
    return candidate


def _normalized_evidence(value: object) -> str:
    return " ".join(str(value).split()).casefold()


def _has_source_evidence(value: object) -> bool:
    if isinstance(value, dict):
        status = value.get("status")
        if status == "known":
            return _has_source_evidence(value.get("value"))
        if status == "not_applicable":
            return _has_source_evidence(value.get("reason"))
        return status in EPISTEMIC_STATES
    if isinstance(value, str):
        normalized = _normalized_evidence(value)
        return bool(normalized) and normalized not in PLACEHOLDER_EVIDENCE_VALUES
    return value is not None and bool(value)


def _has_text_source_evidence(value: object) -> bool:
    return isinstance(value, str) and _has_source_evidence(value)


def _has_allowed_outputs(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(output, str) and _has_source_evidence(output) for output in value)
    )


def _epistemic_state(value: object) -> tuple[bool, bool]:
    """Return structural validity and whether evidence remains unresolved."""
    if not isinstance(value, dict) or value.get("status") not in EPISTEMIC_STATES:
        return False, False
    status = str(value["status"])
    if status == "known":
        known_value = value.get("value")
        return (
            bool(known_value)
            if isinstance(known_value, dict)
            else _has_source_evidence(known_value),
            False,
        )
    if status == "not_applicable":
        return _has_source_evidence(value.get("reason")), False
    return True, True


def _source_identity(value: object) -> tuple[bool, bool]:
    valid, unresolved = _epistemic_state(value)
    if not valid or unresolved:
        return valid, unresolved
    assert isinstance(value, dict)
    if value.get("status") == "not_applicable":
        return True, False
    identity = value.get("value")
    if not isinstance(identity, dict):
        return False, False
    method = identity.get("method")
    identifier = identity.get("identifier")
    return (
        method in SOURCE_IDENTITY_METHODS and _has_text_source_evidence(identifier),
        False,
    )


def _declared_identity(value: dict[str, Any]) -> tuple[str | None, str | None]:
    identity = value.get("identity")
    if isinstance(identity, dict) and identity.get("status") == "known":
        declared = identity.get("value")
        if isinstance(declared, dict):
            method, identifier = declared.get("method"), declared.get("identifier")
            return (
                method if isinstance(method, str) else None,
                identifier if isinstance(identifier, str) else None,
            )
    return None, None


def _verification_record_problem(
    root: Path, source_id: str, value: dict[str, Any], path_value: str
) -> str | None:
    """Return why an external source's verification record does not support it, or None."""
    relative = value.get("verification")
    if not isinstance(relative, str) or not relative.strip():
        return "verification must be a project-relative path to a verification record"
    try:
        path = _safe_managed_path(root, relative.strip())
    except ValueError as exc:
        return str(exc)
    if not path.is_file():
        return f"verification record does not exist: {relative}"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"verification record is not valid JSON: {exc}"
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        return "verification record schema_version must be 1"
    digest = _expected_checksum(record.get("digest"))
    if digest is None:
        return (
            "verification record digest must be sha256:<hex>, sha256-tree-v2:<hex>, or the "
            "legacy sha256-tree-v1:<hex>"
        )
    if not _has_text_source_evidence(record.get("verified_at")):
        return "verification record requires verified_at"
    expected: dict[str, str | None] = {"source_id": source_id, "retrieved_from": path_value}
    method, identifier = _declared_identity(value)
    if identifier is not None:
        expected |= {"method": method, "identifier": identifier}
    for field, wanted in expected.items():
        if record.get(field) != wanted:
            return f"verification record {field} does not match data/sources.yml"
    return _digest_consistency_problem(value, method, identifier, digest)


def _digest_consistency_problem(
    value: dict[str, Any],
    method: str | None,
    identifier: str | None,
    digest: tuple[str, str],
) -> str | None:
    """Compare a verified digest, including its file/tree kind, with every declared digest."""
    if value.get("raw_checksum") is not None:
        declared = _expected_checksum(value.get("raw_checksum"))
        if declared is None:
            return "raw_checksum is malformed"
        if declared != digest:
            return "verification digest does not match raw_checksum"
    if method in {"sha256", "sha256_tree"}:
        parsed = _expected_checksum(identifier)
        hex_digest = parsed[1] if parsed is not None else None
        kind = "file"
        if method == "sha256" and parsed is not None and parsed[0] != "file":
            return "sha256 identity must be a file digest, not a tree checksum"
        if method == "sha256_tree":
            if str(identifier or "").lower().startswith("sha256:"):
                return "sha256_tree identity must be a tree checksum, not a file digest"
            # A bare hex tree identifier predates versioned prefixes, so it means v1.
            kind = "tree" if parsed is None or parsed[0] == "file" else parsed[0]
        if hex_digest is None or (kind, hex_digest) != digest:
            return "sha256 identity does not match the verified digest"
    return None


def _check_external_verification(
    root: Path,
    source_id: str,
    value: dict[str, Any],
    path_value: str,
    findings: list[Finding],
) -> None:
    """A declared external identity is a claim; only a digest of retrieved bytes verifies it."""
    if value.get("verification") is None:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "source-identity-unverified",
                (
                    "External source identity is declared but not verified against a digest of "
                    "retrieved bytes, so it cannot support release. Run `python "
                    ".astack/workflow.py verify-source <id> <retrieved-path>`."
                ),
                source_id,
            )
        )
        return
    problem = _verification_record_problem(root, source_id, value, path_value)
    if problem is not None:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "source-identity-verification",
                "External source verification record does not support its declared identity.",
                f"{source_id}: {problem}",
            )
        )
        return
    # A valid record's digest equals every declared digest, so its kind speaks for all of them.
    if _verification_digest_kind(root, value) == "tree":
        findings.append(_legacy_tree_finding(source_id))


def _verification_digest_kind(root: Path, value: dict[str, Any]) -> str | None:
    try:
        path = _safe_managed_path(root, str(value.get("verification")).strip())
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    parsed = _expected_checksum(record.get("digest")) if isinstance(record, dict) else None
    return parsed[0] if parsed is not None else None


def _legacy_tree_finding(source_id: str) -> Finding:
    return _finding(
        "P1",
        "provenance",
        "legacy-tree-checksum",
        (
            "Source directory uses the legacy sha256-tree-v1 checksum, whose framing can let two "
            "different directories share a digest, so it cannot support release. Record the "
            "value from `python .astack/workflow.py checksum <path>` (sha256-tree-v2) instead, "
            "and re-run verify-source for an external source."
        ),
        source_id,
    )


def _retrieved_digest(path: Path) -> str:
    """Hash fresh bytes: a verification record or recorded checksum never trusts the cache."""
    previous = _DIGEST_POLICY["bypass"]
    _DIGEST_POLICY["bypass"] = True
    try:
        return _uncached_retrieved_digest(path)
    finally:
        _DIGEST_POLICY["bypass"] = previous


def _uncached_retrieved_digest(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError("Retrieved source path must not be a symlink")
    if path.is_file():
        return f"sha256:{_sha256(path)}"
    if path.is_dir():
        return f"sha256-tree-v2:{_tree_sha256_v2(path)}"
    raise RuntimeError(f"Retrieved source path does not exist: {path}")


def verify_source(root: Path, source_id: str, retrieved: Path) -> dict[str, Any]:
    """Hash bytes retrieved for an external source and write its verification record."""
    if yaml is None:
        raise RuntimeError("PyYAML is unavailable, so data/sources.yml cannot be read")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", source_id):
        raise RuntimeError("Source id must use letters, digits, '.', '_' or '-'")
    data = yaml.safe_load((root / "data/sources.yml").read_text(encoding="utf-8")) or {}
    sources = data.get("sources") if isinstance(data, dict) else None
    value = sources.get(source_id) if isinstance(sources, dict) else None
    if not isinstance(value, dict):
        raise RuntimeError(f"Source {source_id!r} is not declared in data/sources.yml")
    path_value = str(value.get("path") or "").strip()
    if "://" not in path_value:
        raise RuntimeError("verify-source applies only to external sources (path with a scheme)")
    digest = _retrieved_digest(retrieved)
    method, identifier = _declared_identity(value)
    parsed_digest = _expected_checksum(digest)
    assert parsed_digest is not None
    problem = _digest_consistency_problem(value, method, identifier, parsed_digest)
    if problem is not None:
        raise RuntimeError(f"Retrieved bytes do not match the declaration: {problem}")
    record: dict[str, Any] = {
        "schema_version": 1,
        "source_id": source_id,
        "retrieved_from": path_value,
        "digest": digest,
        "verified_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    if identifier is not None:
        record |= {"method": method, "identifier": identifier}
    relative = f"data/source-verification/{source_id}.json"
    output = _safe_managed_path(root, relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "verified",
        "record": relative,
        "digest": digest,
        "next_step": f"Set sources.{source_id}.verification: {relative} in data/sources.yml",
        "limits": (
            "Proves which bytes were retrieved and hashed at verified_at. It does not prove the "
            "upstream location will serve the same bytes again."
        ),
    }


def _external_root_state(root: Path, name: str) -> tuple[Path | None, str | None]:
    """Return (resolved_path, config_error). (None, None) means the root is simply undeclared."""
    try:
        canonical = Path(canonical_checkout(root))
    except (OSError, RuntimeError):
        canonical = root
    try:
        found: Path | None = external_root(canonical, name)
    except (OSError, RuntimeError, ValueError, tomllib.TOMLDecodeError) as exc:
        return None, f"external root {name!r} configuration is invalid: {exc}"
    if found is not None:
        return found, None
    # external_root() returns None for both undeclared and invalid (e.g. relative) settings.
    env_name = EXTERNAL_ROOT_ENV_PREFIX + name.upper()
    if os.environ.get(env_name):
        return None, f"external root {name!r} in {env_name} must be an absolute path"
    local_toml = canonical / ".astack/local.toml"
    if local_toml.exists():
        try:
            local = tomllib.loads(local_toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None, f"external root {name!r} could not be read from .astack/local.toml"
        data = local.get("data")
        if data is not None and not isinstance(data, dict):
            return None, f"external root {name!r} configuration in .astack/local.toml is invalid"
        roots = data.get("external_roots") if isinstance(data, dict) else None
        if roots is not None and not isinstance(roots, dict):
            return None, f"external root {name!r} configuration in .astack/local.toml is invalid"
        if isinstance(roots, dict) and name in roots:
            return None, f"external root {name!r} in .astack/local.toml must be an absolute path"
    return None, None


def _external_root_for(root: Path, name: str) -> Path | None:
    """Look up an external root in the canonical checkout's local config, or this root's."""
    return _external_root_state(root, name)[0]


def _resolve_in_place_source(root: Path, value: object) -> tuple[Path | None, str, bool]:
    """Resolve "root:<name>/<relative path>" and flag only genuinely undeclared external roots."""
    text = str(value or "").strip()
    usage = "in-place source path must be root:<name>/<relative path>"
    if not text.startswith(IN_PLACE_PREFIX):
        return None, usage, False
    name, _, relative = text[len(IN_PLACE_PREFIX) :].partition("/")
    if not re.fullmatch(r"[a-z0-9_]+", name) or not relative:
        # Lowercase letters, digits and underscores map one-to-one to ASTACK_EXTERNAL_ROOT_<NAME>.
        return None, usage + ", where <name> uses lowercase letters, digits and underscores", False
    if "\x00" in relative:
        return None, "in-place source path must not contain a NUL byte", False
    relative_path = Path(relative)
    if not relative_path.parts or relative_path.is_absolute() or ".." in relative_path.parts:
        return None, "in-place source path must not be absolute or traverse upward", False
    base, config_error = _external_root_state(root, name)
    if config_error is not None:
        return None, config_error, False
    if base is None:
        return (
            None,
            (
                f"external root {name!r} is not configured on this machine. Set it in "
                ".astack/local.toml under [data.external_roots]"
            ),
            True,
        )
    try:
        base.stat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        return None, f"external root {name!r} is not accessible: {exc}", False
    else:
        if not base.is_dir():
            return None, f"external root {name!r} must be a directory", False
    try:
        candidate = (base / relative_path).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return None, f"in-place source path is not accessible: {exc}", False
    if candidate != base and base not in candidate.parents:
        return None, "in-place source path escapes its external root", False
    if candidate.is_relative_to(root.resolve()):
        return None, "an in-place source must live outside the project. Snapshot it instead", False
    try:
        candidate.stat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        return None, f"in-place source path is not accessible: {exc}", False
    return candidate, "", False


def _in_place_source_path(root: Path, value: object) -> tuple[Path | None, str]:
    """Resolve "root:<name>/<relative path>" against this machine's external roots."""
    candidate, error, _ = _resolve_in_place_source(root, value)
    return candidate, error


def _snapshot_limit_bytes(root: Path) -> tuple[int, str | None]:
    """The per-source size above which a source may stay in place, and any config error."""
    default = DEFAULT_SNAPSHOT_LIMIT_GB * (1 << 30)
    try:
        data = project_config(root).get("data", {})
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return default, None
    value = (
        data.get("snapshot_limit_gb", DEFAULT_SNAPSHOT_LIMIT_GB) if isinstance(data, dict) else None
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return default, f"[data].snapshot_limit_gb must be a positive number, not {value!r}"
    return int(value * (1 << 30)), None


def _total_bytes(path: Path) -> int:
    """Total size of a file or tree from metadata only, never following links."""
    if not path.is_dir():
        return path.stat().st_size
    total = 0
    for current, _directories, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def _check_source_bytes(
    source_path: Path, source_id: str, checksum: tuple[str, str], findings: list[Finding]
) -> set[Path]:
    """Compare a source's bytes with its checksum. Returns the files it covers."""
    checksum_kind, expected_digest = checksum
    if source_path.is_file():
        if checksum_kind != "file" or _cached_sha256(source_path) != expected_digest:
            findings.append(
                _finding(
                    "P0",
                    "data-safety",
                    "raw-checksum",
                    "A local source snapshot does not match its recorded checksum.",
                    source_id,
                    sensitive=True,
                )
            )
        return {source_path}
    if source_path.is_dir():
        _check_tree_checksum(source_path, source_id, checksum_kind, expected_digest, findings)
        return {path for path in source_path.rglob("*") if path.is_file()}
    if source_path.exists():
        # A FIFO, socket or device has no stable bytes to compare, so it never verifies.
        findings.append(
            _finding(
                "P0",
                "data-safety",
                "raw-checksum",
                "A source is neither a regular file nor a directory, so its bytes cannot be "
                "verified.",
                source_id,
                sensitive=True,
            )
        )
    return set()


def _in_place_checksum_error(source_path: Path, checksum: tuple[str, str] | None) -> str:
    """An in-place directory needs sha256-tree-v2, which refuses links and special files."""
    if checksum is not None and source_path.is_dir() and checksum[0] != "tree-v2":
        return "An in-place directory requires a sha256-tree-v2 checksum."
    return ""


def _check_in_place_source(
    root: Path, source_id: str, value: dict[str, Any], limit: int, findings: list[Finding]
) -> None:
    """A source verified where it lives instead of snapshotted. It needs a recorded reason."""
    if value.get("storage") != "in_place":
        findings.append(
            _finding(
                "P1",
                "provenance",
                "source-storage",
                "A root: path is valid only with storage: in_place.",
                source_id,
            )
        )
        return
    reason = value.get("in_place_reason")
    if reason not in IN_PLACE_REASONS:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "source-in-place-reason",
                "An in-place source needs in_place_reason: size or license. "
                "Otherwise snapshot it into data/raw through acquisition.",
                source_id,
            )
        )
    checksum = _expected_checksum(value.get("raw_checksum"))
    if checksum is None:
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "source-checksum",
                "An in-place source requires a valid SHA-256 checksum.",
                source_id,
            )
        )
    source_path, error = _in_place_source_path(root, value.get("path"))
    if source_path is None:
        findings.append(_finding("P1", "provenance", "source-path", error, source_id))
        return
    if not source_path.exists():
        findings.append(
            _finding(
                "P1",
                "reproducibility",
                "source-missing",
                "In-place source is not available on this machine. Mount its drive, fix its "
                'external root, or use limited CI ([ci] mode = "local-only-sources" and '
                "`just ci-check`; see docs/checks-and-audit.md).",
                source_id,
            )
        )
        return
    if checksum is not None:
        checksum_error = _in_place_checksum_error(source_path, checksum)
        if checksum_error:
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "source-checksum",
                    checksum_error,
                    source_id,
                )
            )
        else:
            _check_source_bytes(source_path, source_id, checksum, findings)
    if reason == "size":
        total = _total_bytes(source_path)
        if total <= limit:
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-should-snapshot",
                    "An in-place source within the snapshot limit must be snapshotted into "
                    "data/raw through acquisition.",
                    f"{source_id}: {total / (1 << 30):.2f} GiB, limit {limit / (1 << 30):g} GiB",
                )
            )


def in_place_root_environment(root: Path) -> dict[str, str]:
    """The external-root variables a run needs to find its in-place sources."""
    path = root / "data" / "sources.yml"
    if yaml is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}
    sources = data.get("sources") if isinstance(data, dict) else None
    environment: dict[str, str] = {}
    for value in sources.values() if isinstance(sources, dict) else ():
        text = str(value.get("path") or "").strip() if isinstance(value, dict) else ""
        if not text.startswith(IN_PLACE_PREFIX):
            continue
        name = text[len(IN_PLACE_PREFIX) :].partition("/")[0]
        base = _external_root_for(root, name)
        if base is not None:
            environment[EXTERNAL_ROOT_ENV_PREFIX + name.upper()] = str(base)
    return environment


def in_place_inputs_identity(root: Path) -> dict[str, str]:
    """Verify every in-place source a run reads and return its checksum by source id.

    A run must refuse an in-place source it cannot verify, because nothing in the project
    holds a copy of those bytes.
    """
    path = root / "data" / "sources.yml"
    if yaml is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        raise RuntimeError(f"Provenance YAML is invalid: {exc}") from exc
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, dict):
        return {}
    identity: dict[str, str] = {}
    for source_id, value in sorted(
        (str(key), record) for key, record in sources.items() if isinstance(record, dict)
    ):
        in_place = value.get("storage") == "in_place" or str(
            value.get("path") or ""
        ).strip().startswith(IN_PLACE_PREFIX)
        if not in_place:
            continue
        source_path, error = _in_place_source_path(root, value.get("path"))
        if source_path is None:
            raise RuntimeError(f"In-place source {source_id}: {error}")
        if not source_path.exists():
            raise RuntimeError(f"In-place source {source_id} is not available on this machine")
        checksum = _expected_checksum(value.get("raw_checksum"))
        if checksum is None:
            raise RuntimeError(f"In-place source {source_id} has no valid SHA-256 checksum")
        checksum_error = _in_place_checksum_error(source_path, checksum)
        if checksum_error:
            raise RuntimeError(f"In-place source {source_id}: {checksum_error}")
        mismatches: list[Finding] = []
        _check_source_bytes(source_path, source_id, checksum, mismatches)
        if any(finding.check == "raw-checksum" for finding in mismatches):
            raise RuntimeError(f"In-place source {source_id} does not match its checksum")
        identity[source_id] = f"{checksum[0]}:{checksum[1]}"
    return identity


def _uncommitted_outside_raw(root: Path, path_value: str) -> bool:
    """Whether a local source outside data/raw has bytes this repository's Git does not record.

    Reproduction fingerprints data/raw and refuses uncommitted changes, so any other location is
    covered only when Git does not ignore it.
    """
    parts = Path(path_value).parts
    if parts[:2] == ("data", "raw"):
        return False
    ignored = _run(
        ["git", "ls-files", "--others", "--exclude-standard", "--ignored", "--", path_value],
        cwd=root,
    )
    # Outside Git, reproduction refuses to run. Untracked files make it refuse too.
    if ignored.returncode == 0 and ignored.stdout.strip():
        return True
    return _outside_project_git(root, Path(path_value))


def _outside_project_git(root: Path, relative: Path) -> bool:
    """Whether a project path's bytes escape this repository's Git record.

    Git keeps a symlink, not its target's bytes, and a nested repository or submodule keeps its
    own ignore rules and dirty state, which this checkout's status does not see.
    """
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (current / ".git").exists():
            return True
    if not current.is_dir():
        return False
    for directory, subdirectories, files in os.walk(current, followlinks=False):
        names = (*subdirectories, *files)
        if ".git" in names or any(Path(directory, name).is_symlink() for name in names):
            return True
    return False


def _check_sources(root: Path, findings: list[Finding]) -> None:
    path = root / "data" / "sources.yml"
    if not path.exists():
        findings.append(
            _finding(
                "P1", "provenance", "sources", "Canonical provenance file is missing.", str(path)
            )
        )
        return
    if yaml is None:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "sources-parser",
                "PyYAML is unavailable, so provenance was not validated.",
                "uv sync --group dev",
            )
        )
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        findings.append(
            _finding("P1", "provenance", "sources", "Provenance YAML is invalid.", str(exc))
        )
        return
    schema_version = data.get("schema_version", 1) if isinstance(data, dict) else None
    # YAML can yield lists, maps or booleans here; only the integers 1 and 2 are versions.
    if type(schema_version) is not int or schema_version not in {1, 2}:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "sources-schema-version",
                "Provenance schema version is unsupported.",
                str(schema_version),
            )
        )
        return
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, dict):
        findings.append(
            _finding(
                "P1", "provenance", "sources", "`sources` must be a mapping.", "data/sources.yml"
            )
        )
        return
    raw_files = _data_files(root / "data" / "raw") if (root / "data" / "raw").exists() else []
    source_code = [
        path
        for folder in (root / "src", root / "scripts")
        if folder.exists()
        for path in folder.rglob("*.py")
        if path.name != "__init__.py"
    ]
    notebooks = (
        [
            path
            for path in (root / "notebooks").rglob("*.py")
            if path.is_file() and not _viewer_notebook(root, path)
        ]
        if (root / "notebooks").exists()
        else []
    )
    reports_dir = root / "reports"
    reports = list(reports_dir.rglob("*.qmd")) if reports_dir.exists() else []
    if (raw_files or source_code or notebooks or reports) and not sources:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "source-coverage",
                "Data or analytical artifacts exist but no provenance records are defined.",
                (
                    f"raw_files={len(raw_files)}, analytical_code={len(source_code)}, "
                    f"notebooks={len(notebooks)}, reports={len(reports)}"
                ),
                sensitive=True,
            )
        )
    invalid_ids = sorted(
        repr(key) for key in sources if not isinstance(key, str) or not key.strip()
    )
    if invalid_ids:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "source-id",
                "Source IDs must be non-empty strings. Quote numeric or empty keys.",
                ", ".join(invalid_ids),
            )
        )
    limit, limit_error = _snapshot_limit_bytes(root)
    if limit_error:
        findings.append(
            _finding("P1", "provenance", "data-snapshot-limit", limit_error, ".astack/project.toml")
        )
    covered_raw: set[Path] = set()
    for source_id, value in sorted(
        (key, record) for key, record in sources.items() if isinstance(key, str) and key.strip()
    ):
        if not isinstance(value, dict):
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-record",
                    "Source record must be a mapping.",
                    str(source_id),
                )
            )
            continue
        missing = sorted(
            field
            for field in SOURCE_REQUIRED_FIELDS
            if not (
                _has_allowed_outputs(value.get(field))
                if field == "allowed_outputs"
                else _epistemic_state(value.get(field))[0]
                if schema_version == 2 and field in EPISTEMIC_FIELDS
                else _has_text_source_evidence(value.get(field))
            )
        )
        if missing:
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-fields",
                    "Source record is missing required evidence.",
                    f"{source_id}: {', '.join(missing)}",
                )
            )
        if schema_version == 2:
            unresolved: list[str] = []
            invalid_states: list[str] = []
            for field in sorted(EPISTEMIC_FIELDS):
                valid, field_unresolved = _epistemic_state(value.get(field))
                if not valid:
                    invalid_states.append(field)
                elif field_unresolved:
                    unresolved.append(field)
            checksum_identity = _expected_checksum(value.get("raw_checksum")) is not None
            identity_valid, identity_unresolved = (
                (True, False)
                if value.get("identity") is None and checksum_identity
                else _source_identity(value.get("identity"))
            )
            if not identity_valid:
                invalid_states.append("identity")
            elif identity_unresolved:
                unresolved.append("identity")
            if invalid_states:
                findings.append(
                    _finding(
                        "P1",
                        "provenance",
                        "source-evidence-state",
                        "Source evidence state is invalid or lacks its required value or reason.",
                        f"{source_id}: {', '.join(invalid_states)}",
                    )
                )
            if unresolved:
                findings.append(
                    _finding(
                        "P1",
                        "provenance",
                        "source-evidence-unresolved",
                        "Source evidence is explicitly unresolved and cannot support release.",
                        f"{source_id}: {', '.join(unresolved)}",
                    )
                )
        if value.get("availability") not in (None, "local_only", "ci_available"):
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-availability",
                    "Availability must be local_only or ci_available when declared.",
                    str(source_id),
                )
            )
        storage = value.get("storage", "snapshot")
        if storage not in ("snapshot", "in_place"):
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-storage",
                    "Source storage must be snapshot or in_place.",
                    str(source_id),
                )
            )
            continue
        if storage == "in_place" or str(value.get("path") or "").strip().startswith(
            IN_PLACE_PREFIX
        ):
            _check_in_place_source(root, str(source_id), value, limit, findings)
            continue
        checksum = _expected_checksum(value.get("raw_checksum"))
        path_value = str(value.get("path") or "").strip()
        external_source = "://" in path_value
        identity_valid, identity_unresolved = (
            (True, False)
            if value.get("identity") is None and checksum is not None
            else _source_identity(value.get("identity"))
        )
        identity_value = value.get("identity")
        identity_known = (
            isinstance(identity_value, dict)
            and identity_value.get("status") == "known"
            and identity_valid
            and not identity_unresolved
        )
        if checksum is None and (not external_source or not identity_known):
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "source-checksum",
                    "Source requires a valid SHA-256 checksum or verifiable external identity.",
                    str(source_id),
                )
            )
        source_path = _local_source_path(root, path_value)
        procedures = [value[field] for field in ("acquisition", "retrieval") if field in value]
        if not external_source and source_path is None:
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-path",
                    "Local source path is absolute, traversing, or otherwise unsafe.",
                    str(source_id),
                )
            )
        elif source_path is not None and not source_path.exists():
            message = "Recorded local source path does not exist."
            if value.get("availability") != "ci_available" and _is_gitignored_raw_path(
                root, path_value
            ):
                message = (
                    "Recorded local source path does not exist. In CI, gitignored data/raw/ "
                    'snapshots require limited CI ([ci] mode = "local-only-sources" and '
                    "`just ci-check`; see docs/checks-and-audit.md)."
                )
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "source-missing",
                    message,
                    str(source_id),
                )
            )
        if external_source and not any(
            _has_text_source_evidence(procedure) for procedure in procedures
        ):
            findings.append(
                _finding(
                    "P1",
                    "reproducibility",
                    "source-acquisition",
                    "External source requires a versioned acquisition or retrieval procedure.",
                    str(source_id),
                )
            )
        if external_source:
            _check_external_verification(root, str(source_id), value, path_value, findings)
        if source_path and source_path.exists() and _uncommitted_outside_raw(root, path_value):
            findings.append(
                _finding(
                    "P1",
                    "provenance",
                    "source-storage",
                    "A snapshot source must be in data/raw or committed to Git. Snapshot it "
                    "through acquisition, or declare storage: in_place with a reason.",
                    str(source_id),
                )
            )
        if source_path and source_path.exists() and checksum:
            covered_raw.update(_check_source_bytes(source_path, str(source_id), checksum, findings))
    uncovered = [path for path in raw_files if path.resolve() not in covered_raw]
    if uncovered and sources:
        findings.append(
            _finding(
                "P1",
                "provenance",
                "raw-source-coverage",
                "Some local raw snapshots are not covered by a source path and checksum.",
                f"uncovered_count={len(uncovered)}",
                sensitive=True,
            )
        )


def _check_tree_checksum(
    source_path: Path,
    source_id: str,
    kind: str,
    expected_digest: str,
    findings: list[Finding],
) -> None:
    """Compare a source directory with its tree checksum, and flag the ambiguous v1 format."""
    try:
        if kind == "tree":
            actual_digest: str | None = _cached_tree_sha256(source_path)
        elif kind == "tree-v2":
            actual_digest = _tree_sha256_v2(source_path)
        else:
            actual_digest = None
    except (OSError, RuntimeError):
        actual_digest = None
    if actual_digest != expected_digest:
        findings.append(
            _finding(
                "P0",
                "data-safety",
                "raw-checksum",
                "A local source directory does not match its tree checksum.",
                source_id,
                sensitive=True,
            )
        )
    elif kind == "tree":
        findings.append(_legacy_tree_finding(source_id))


def source_checksum(path: Path) -> dict[str, Any]:
    """Return the raw_checksum value for a local file or directory."""
    return {"path": str(path), "checksum": _retrieved_digest(path)}


def _spatial_document(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return the optional spatial extension, or why it cannot be read."""
    path = root / "data/spatial.yml"
    if not path.exists():
        return None, None
    if yaml is None:
        return None, "PyYAML is unavailable"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        return None, f"data/spatial.yml is invalid: {exc}"
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        return None, "data/spatial.yml must be a mapping with schema_version: 1"
    return document, None


def _spatial_validator_entries(document: dict[str, Any]) -> tuple[list[list[str]], list[str]]:
    """Return runnable validator commands and a description of every malformed entry."""
    validators = document.get("validators", [])
    if not isinstance(validators, list):
        return [], ["validators must be a list of command arrays"]
    commands: list[list[str]] = []
    problems: list[str] = []
    for index, command in enumerate(validators):
        if (
            isinstance(command, list)
            and command
            and all(isinstance(part, str) and part and "\0" not in part for part in command)
        ):
            commands.append(command)
        else:
            problems.append(f"validators[{index}] must be a non-empty array of strings without NUL")
    return commands, problems


def _spatial_validators(root: Path) -> list[list[str]]:
    """Validators run only when every declared entry is well formed."""
    document, _ = _spatial_document(root)
    if document is None:
        return []
    commands, problems = _spatial_validator_entries(document)
    return [] if problems else commands


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _spatial_text(value: object) -> bool:
    """A real, non-placeholder value in its original type (YAML dates allowed for dates)."""
    if isinstance(value, (date, datetime)):
        return True
    return isinstance(value, str) and _has_text_source_evidence(value)


def _known_spatial_value(field: str, value: object) -> bool:
    if field == "nodata":
        # Numeric zero is a real nodata value; empty or placeholder text is missing evidence.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        return _spatial_text(value)
    if field == "vertical_datum":
        return _spatial_text(value) or (isinstance(value, dict) and _spatial_text(value.get("id")))
    required = {
        "crs": ("id", "axis_order", "units"),
        "temporal_extent": ("start", "end"),
        "resolution": ("units",),
        "geometry_validity": ("method", "result"),
    }[field]
    if not isinstance(value, dict):
        return False
    if not all(_spatial_text(value.get(key)) for key in required):
        return False
    if field == "resolution":
        return _positive_number(value.get("x")) and _positive_number(value.get("y"))
    return True


def _spatial_record_problems(record: object) -> tuple[list[str], list[str]]:
    """Return (invalid fields, unresolved fields) for one spatial record."""
    if not isinstance(record, dict):
        return ["record"], []
    invalid: list[str] = []
    unresolved: list[str] = []
    for field in SPATIAL_FIELDS:
        state = record.get(field)
        status = state.get("status") if isinstance(state, dict) else None
        if not isinstance(state, dict) or not isinstance(status, str):
            invalid.append(field)
        elif status == "known":
            # Checked here, not by _epistemic_state, so a valid falsy value such as nodata 0 passes.
            if "value" not in state or not _known_spatial_value(field, state["value"]):
                invalid.append(field)
        else:
            valid, field_unresolved = _epistemic_state(state)
            if not valid:
                invalid.append(field)
            elif field_unresolved:
                unresolved.append(field)
    steps = record.get("reprojection", [])
    if not isinstance(steps, list) or not all(
        isinstance(step, dict)
        and all(_spatial_text(step.get(key)) for key in ("from", "to", "method"))
        for step in steps
    ):
        invalid.append("reprojection")
    return invalid, unresolved


def _check_spatial(root: Path, findings: list[Finding]) -> None:
    """Check the optional spatial extension. Absent means not declared, never a pass."""
    document, error = _spatial_document(root)
    if error is not None:
        findings.append(
            _finding("P1", "spatial", "spatial-metadata", "Spatial metadata is invalid.", error)
        )
        return
    if document is None:
        return
    sources_path = root / "data/sources.yml"
    try:
        provenance = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        provenance = {}
    declared_sources = provenance.get("sources") if isinstance(provenance, dict) else None
    known_sources = set(declared_sources) if isinstance(declared_sources, dict) else set()
    entries: list[tuple[str, object]] = []
    for section in ("sources", "outputs"):
        records = document.get(section, {})
        if not isinstance(records, dict):
            findings.append(
                _finding(
                    "P1",
                    "spatial",
                    "spatial-metadata",
                    "Spatial metadata sections must be mappings.",
                    section,
                )
            )
            continue
        for key, record in sorted(records.items(), key=lambda item: str(item[0])):
            name = f"{section}.{key}"
            if section == "sources" and key not in known_sources:
                findings.append(
                    _finding(
                        "P1",
                        "spatial",
                        "spatial-source",
                        "Spatial metadata names a source not declared in data/sources.yml.",
                        name,
                    )
                )
            if section == "outputs":
                try:
                    _safe_managed_path(root, str(key))
                except ValueError:
                    findings.append(
                        _finding(
                            "P1",
                            "spatial",
                            "spatial-output",
                            "Spatial output path is unsafe.",
                            name,
                        )
                    )
                    continue
            entries.append((name, record))
    for name, record in entries:
        invalid, unresolved = _spatial_record_problems(record)
        if invalid:
            findings.append(
                _finding(
                    "P1",
                    "spatial",
                    "spatial-metadata",
                    "Spatial evidence is missing, malformed, or lacks its value or reason.",
                    f"{name}: {', '.join(invalid)}",
                )
            )
        if unresolved:
            findings.append(
                _finding(
                    "P1",
                    "spatial",
                    "spatial-evidence-unresolved",
                    "Spatial evidence is explicitly unresolved and cannot support release.",
                    f"{name}: {', '.join(unresolved)}",
                )
            )
    _, validator_problems = _spatial_validator_entries(document)
    for problem in validator_problems:
        findings.append(
            _finding(
                "P1",
                "spatial",
                "spatial-metadata",
                "Spatial validators are malformed, so none were run.",
                problem,
            )
        )
    if entries and not validator_problems and not _spatial_validators(root):
        findings.append(
            _finding(
                "P1",
                "spatial",
                "spatial-unvalidated",
                (
                    "Spatial metadata is declared but no project-defined validator checks it "
                    "against the data. Declare `validators` in data/spatial.yml."
                ),
                "data/spatial.yml",
            )
        )
