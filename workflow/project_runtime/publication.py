from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    ALLOWED_SCOPES,
    BINARY_SUFFIXES,
    EXPORT_PRIVATE_PREFIXES,
    EXPORT_SOURCE_METADATA,
    PUBLICATION_FILES,
    REDISTRIBUTION_DENIALS,
    SECRET_PATTERNS,
    Any,
    Finding,
    Path,
    _finding,
    _is_sensitive_filename,
    _run,
    _safe_managed_path,
    _stream_contains_secret,
    hashlib,
    json,
    re,
    shutil,
    stat,
    subprocess,
    tempfile,
    tomllib,
    yaml,
)
from .sources import _has_source_evidence
from .validation import _project_config


def _marker(commit: str, scope: str) -> str:
    return f"<!-- ds-repo-audit:v1 commit={commit} scope={scope} -->"


def _fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _sanitize_public(body: str) -> str:
    start = "<!-- public-summary:start -->"
    end = "<!-- public-summary:end -->"
    if body.count(start) != 1 or body.count(end) != 1:
        raise RuntimeError("Public audit requires exactly one explicit public-summary block")
    start_index = body.index(start)
    end_index = body.index(end)
    if start_index >= end_index:
        raise RuntimeError("Public audit summary markers are reversed")
    summary = body[start_index + len(start) : end_index].strip()
    if not summary:
        raise RuntimeError("Public audit summary is empty")
    if "<!-- sensitive-details:" in summary:
        raise RuntimeError("Public audit summary contains a sensitive-details marker")
    long_token = re.search(r"[A-Za-z0-9_+=.-]{32,}", summary)
    if (
        "/" in summary
        or "\\" in summary
        or "@" in summary
        or long_token
        or any(pattern.search(summary) for pattern in SECRET_PATTERNS)
    ):
        raise RuntimeError("Public audit summary contains a likely path, identifier, or credential")
    return summary + "\n\nSensitive remediation details remain in the ignored local audit report."


def _gh_json(root: Path, args: list[str]) -> Any:
    result = _run(["gh", *args], cwd=root)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "GitHub CLI command failed")
    return json.loads(result.stdout or "null")


def _write_body_file(body: str) -> str:
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".md")
    with handle:
        handle.write(body)
    return handle.name


def _validated_audit_report(root: Path, report: Path) -> Path:
    audit_path = root / "reports" / "audits"
    current = audit_path
    while current != root and current != current.parent:
        if current.is_symlink():
            raise RuntimeError("Audit report directory must not use symlinks")
        current = current.parent
    audit_directory = audit_path.resolve()
    candidate = report.resolve()
    if candidate.parent != audit_directory or not candidate.is_file():
        raise RuntimeError("Audit report must be a file directly under reports/audits")
    current = report
    while current != root and current != current.parent:
        if current.is_symlink():
            raise RuntimeError("Audit report path must not use symlinks")
        current = current.parent
    return candidate


def _verify_audit_identity(root: Path, commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Audit commit must be a full lowercase Git SHA")
    top_level = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if top_level.returncode != 0 or Path(top_level.stdout.strip()).resolve() != root:
        raise RuntimeError("Audit publication root is not the current Git repository root")
    if head.returncode != 0 or head.stdout.strip() != commit:
        raise RuntimeError("Audit commit does not match repository HEAD")
    if status.returncode != 0 or status.stdout.strip():
        raise RuntimeError("Audit issue publication requires a clean worktree")


def _assert_remote_report_safe(root: Path, report: Path) -> None:
    relative = report.relative_to(root).as_posix()
    tracked = _run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root)
    if tracked.returncode == 0:
        raise RuntimeError("Audit report must be untracked before remote publication")
    if tracked.returncode != 1:
        raise RuntimeError(tracked.stderr.strip() or "Could not verify audit report tracking")
    ignored = _run(["git", "check-ignore", "--quiet", "--", relative], cwd=root)
    if ignored.returncode == 0:
        return
    if ignored.returncode == 1:
        raise RuntimeError("Audit report must be Git-ignored before remote publication")
    raise RuntimeError(ignored.stderr.strip() or "Could not verify audit report ignore status")


def publish_audit(
    root: Path,
    report: Path,
    *,
    commit: str,
    scope: str,
    publish: str,
    actionable_count: int,
    remove_on_success: bool,
) -> dict[str, Any]:
    root = root.resolve()
    if scope not in ALLOWED_SCOPES:
        raise RuntimeError(f"Unsupported audit scope: {scope}")
    report = _validated_audit_report(root, report)
    if publish in {"local", "none"}:
        return {"status": publish, "report": str(report), "url": None}
    try:
        _verify_audit_identity(root, commit)
        repository = _gh_json(
            root, ["repo", "view", "--json", "isPrivate,hasIssuesEnabled,nameWithOwner"]
        )
        if not repository["hasIssuesEnabled"]:
            raise RuntimeError("GitHub Issues are disabled")
        is_private = bool(repository["isPrivate"])
        _assert_remote_report_safe(root, report)
        body = report.read_text(encoding="utf-8")
        if any(pattern.search(body) for pattern in SECRET_PATTERNS):
            raise RuntimeError("Audit report contains a likely credential and cannot be published")
        if not is_private:
            body = _sanitize_public(body)
        marker = _marker(commit, scope)
        body = body.rstrip() + f"\n\n{marker}\n"
        fingerprint = _fingerprint(body)
        body += f"<!-- ds-repo-audit:fingerprint={fingerprint} -->\n"
        issue_pages = _gh_json(
            root,
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository['nameWithOwner']}/issues?state=all&per_page=100",
            ],
        )
        issues = (
            [issue for page in issue_pages for issue in page]
            if issue_pages and isinstance(issue_pages[0], list)
            else issue_pages
        )
        issues = [issue for issue in issues if "pull_request" not in issue]
        matches = [issue for issue in issues if marker in str(issue.get("body", ""))]
        if len(matches) > 1:
            raise RuntimeError("Multiple audit issues match the same commit and scope")
        body_file = _write_body_file(body)
        try:
            if matches:
                issue = matches[0]
                if f"ds-repo-audit:fingerprint={fingerprint}" not in str(issue.get("body", "")):
                    result = _run(
                        ["gh", "issue", "edit", str(issue["number"]), "--body-file", body_file],
                        cwd=root,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip() or "Could not update audit issue")
                number = str(issue["number"])
                url = str(issue.get("url") or issue.get("html_url"))
                state = str(issue["state"]).upper()
            else:
                title = f"[Audit] {scope} at {commit[:12]}"
                result = _run(
                    [
                        "gh",
                        "issue",
                        "create",
                        "--title",
                        title,
                        "--body-file",
                        body_file,
                    ],
                    cwd=root,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "Could not create audit issue")
                url = result.stdout.strip()
                number = url.rsplit("/", 1)[-1]
                state = "OPEN"
            if actionable_count > 0 and state == "CLOSED":
                result = _run(["gh", "issue", "reopen", number], cwd=root)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "Could not reopen audit issue")
            if actionable_count == 0 and state == "OPEN":
                result = _run(["gh", "issue", "close", number, "--reason", "completed"], cwd=root)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "Could not close clean audit issue")
        finally:
            Path(body_file).unlink(missing_ok=True)
        if remove_on_success and is_private:
            report.unlink(missing_ok=True)
        return {
            "status": "github",
            "report": None if not report.exists() else str(report),
            "url": url,
        }
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        if publish == "github":
            return {
                "status": "github-failed-local-retained",
                "report": str(report),
                "url": None,
                "error": str(exc),
            }
        return {"status": "local-fallback", "report": str(report), "url": None, "error": str(exc)}


def _export_matches(path: str, patterns: list[str]) -> bool:
    """A pattern is an exact tracked path or a directory prefix ending in `/**`."""
    for pattern in patterns:
        literal = pattern.removesuffix("/**")
        if any(character in literal for character in "*?["):
            raise RuntimeError(f"Unsupported export pattern {pattern!r}: use a path or 'dir/**'")
        if (pattern.endswith("/**") and path.startswith(literal + "/")) or path == pattern:
            return True
    return False


def _git_bytes(root: Path, args: list[str]) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "Git read failed")
    return result.stdout


def _committed_text(root: Path, head: str, relative: str) -> str | None:
    """Read a file from the pinned commit, never from the worktree; None when absent."""
    result = subprocess.run(
        ["git", "show", f"{head}:{relative}"], cwd=root, check=False, capture_output=True
    )
    return result.stdout.decode("utf-8") if result.returncode == 0 else None


def _export_config(root: Path, head: str) -> tuple[list[str], list[str], list[list[str]]]:
    text = _committed_text(root, head, ".astack/project.toml")
    try:
        config = tomllib.loads(text) if text is not None else {}
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        raise RuntimeError(f"Committed .astack/project.toml is invalid: {exc}") from exc
    declaration = config.get("public_export")
    if not isinstance(declaration, dict):
        raise RuntimeError("Public export requires a [public_export] include allowlist")
    include = declaration.get("include")
    exclude = declaration.get("exclude", [])
    scan = declaration.get("scan", [])
    if (
        not isinstance(include, list)
        or not include
        or not all(isinstance(item, str) and item for item in include)
    ):
        raise RuntimeError("public_export.include must be a non-empty list of paths")
    if not isinstance(exclude, list) or not all(isinstance(item, str) for item in exclude):
        raise RuntimeError("public_export.exclude must be a list of paths")
    if not isinstance(scan, list) or not all(
        isinstance(command, list)
        and command
        and all(isinstance(part, str) and part for part in command)
        for command in scan
    ):
        raise RuntimeError("public_export.scan must be a list of command arrays")
    return include, exclude, scan


def _pinned_commit_entries(root: Path) -> tuple[str, list[tuple[str, str, str]]]:
    """Pin HEAD and list (mode, blob, path) from that commit's tree, not from the worktree."""
    status = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if status.returncode != 0 or status.stdout.strip():
        raise RuntimeError("Public export requires a clean Git worktree with no untracked files")
    head = _run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=root)
    if head.returncode != 0:
        raise RuntimeError("Public export requires a committed HEAD")
    commit = head.stdout.strip()
    listing = _git_bytes(root, ["ls-tree", "-r", "-z", "--full-tree", commit]).decode("utf-8")
    entries = []
    for entry in filter(None, listing.split("\0")):
        meta, path = entry.split("\t", 1)
        mode, _kind, blob = meta.split(" ")
        entries.append((mode, blob, path))
    return commit, entries


def _source_allows_public(record: object) -> bool:
    """Structured known redistribution evidence that does not deny it, plus explicit permission.

    Free text cannot be read for intent reliably, so the explicit `public-export` entry in
    `allowed_outputs` is the grant, and doubtful redistribution wording always refuses.
    """
    if not isinstance(record, dict):
        return False
    redistribution = record.get("redistribution")
    if not isinstance(redistribution, dict) or redistribution.get("status") != "known":
        return False
    redistribution = redistribution.get("value")
    if not isinstance(redistribution, str) or not _has_source_evidence(redistribution):
        return False
    folded = " ".join(redistribution.split()).casefold()
    if any(denial in folded for denial in REDISTRIBUTION_DENIALS):
        return False
    outputs = record.get("allowed_outputs")
    return isinstance(outputs, list) and "public-export" in outputs


def _export_private(path: str) -> bool:
    """Never exported, compared case-insensitively for case-insensitive filesystems."""
    return path.casefold().startswith(EXPORT_PRIVATE_PREFIXES) or _is_sensitive_filename(
        Path(path).name
    )


def _derived_export_path(path: str) -> bool:
    folded = path.casefold()
    return folded.startswith(("data/", "artifacts/", "evidence/", "reports/")) and (
        folded not in EXPORT_SOURCE_METADATA
    )


def _export_source_problems(root: Path, head: str, selected: list[str]) -> list[str]:
    """Data and derived outputs need data/sources.yml permission; code and docs do not."""
    governed = [path for path in selected if _derived_export_path(path)]
    if not governed:
        return []
    try:
        document = yaml.safe_load(_committed_text(root, head, "data/sources.yml") or "") or {}
    except (UnicodeError, ValueError, yaml.YAMLError) as exc:
        return [f"data/sources.yml is unreadable: {exc}"]
    sources = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(sources, dict):
        return ["data/sources.yml has no sources mapping"]
    problems = []
    for path in governed:
        folded = path.casefold()
        covering = [
            source_id
            for source_id, record in sources.items()
            if isinstance(record, dict)
            and isinstance(record.get("path"), str)
            and (
                folded == record["path"].casefold()
                or folded.startswith(record["path"].rstrip("/").casefold() + "/")
            )
        ]
        # A derived product may embed any source, so every source must permit publication.
        responsible = covering or list(sources)
        if not responsible:
            problems.append(f"{path}: no source in data/sources.yml grants public-export")
            continue
        refused = [
            str(source_id)
            for source_id in responsible
            if not _source_allows_public(sources[source_id])
        ]
        if refused:
            problems.append(
                f"{path}: source(s) {', '.join(sorted(refused))} lack known redistribution "
                "and `public-export` in allowed_outputs"
            )
    return problems


def _secret_hits(directory: Path, files: list[str]) -> list[str]:
    """Stream-scan every text file; a file that cannot be scanned fails closed."""
    hits = []
    for relative in files:
        path = directory / relative
        if _is_sensitive_filename(path.name):
            hits.append(relative)
            continue
        if path.suffix.casefold() in BINARY_SUFFIXES:
            continue
        try:
            if _stream_contains_secret(path):
                hits.append(relative)
        except (OSError, UnicodeError):
            hits.append(f"{relative} (not scannable as UTF-8 text)")
    return hits


def _staged_manifest(staging: Path) -> dict[str, tuple[str, int]]:
    """(SHA-256, permission bits) of every staged entry; anything but a regular file refuses."""
    manifest = {}
    for path in sorted(staging.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"Public export staging holds a non-regular file: {path.name}")
        manifest[path.relative_to(staging).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            stat.S_IMODE(metadata.st_mode),
        )
    return manifest


def export_public(root: Path, out: Path) -> dict[str, Any]:
    """Write an allowlisted, history-free public copy of the committed project to `out`."""
    root = root.resolve()
    out = out.resolve()
    if out == root or root in out.parents:
        raise RuntimeError("Public export destination must be outside the project")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise RuntimeError(f"Public export destination must be new or empty: {out}")
    head, entries = _pinned_commit_entries(root)
    include, exclude, scans = _export_config(root, head)
    selected: list[str] = []
    modes: dict[str, str] = {}
    blobs: dict[str, str] = {}
    refused_private: list[str] = []
    for mode, blob, path in entries:
        if not _export_matches(path, include) or _export_matches(path, exclude):
            continue
        if _export_private(path):
            refused_private.append(path)
            continue
        if mode in {"120000", "160000"}:
            raise RuntimeError(f"Public export refuses symlinks and submodules: {path}")
        selected.append(path)
        modes[path] = mode
        blobs[path] = blob
    if not selected:
        raise RuntimeError("Public export allowlist selected no tracked files")
    problems = _export_source_problems(root, head, selected)
    if problems:
        raise RuntimeError("Public export blocked by data/sources.yml: " + "; ".join(problems))
    staging = Path(tempfile.mkdtemp(prefix=".astack-export-", dir=out.parent))
    try:
        for relative in sorted(selected):
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bytes and executable bit come from the pinned commit, never from the worktree.
            target.write_bytes(_git_bytes(root, ["cat-file", "blob", blobs[relative]]))
            target.chmod(0o755 if modes[relative] == "100755" else 0o644)
        hits = _secret_hits(staging, selected)
        if hits:
            raise RuntimeError("Public export blocked by the secret scan: " + ", ".join(hits))
        scanned = _staged_manifest(staging)
        staging_identity = (staging.lstat().st_dev, staging.lstat().st_ino)
        for command in scans:
            argv = [part.replace("{out}", str(staging)) for part in command]
            try:
                checked = subprocess.run(argv, cwd=root, check=False, capture_output=True)
            except OSError as exc:
                raise RuntimeError(f"Public export scan is unavailable: {argv[0]}") from exc
            if checked.returncode != 0:
                raise RuntimeError(f"Public export scan failed: {' '.join(command)}")
        root_metadata = staging.lstat()
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or (
                root_metadata.st_dev,
                root_metadata.st_ino,
            )
            != staging_identity
        ):
            raise RuntimeError("A public export scan hook replaced the staging directory")
        manifest = _staged_manifest(staging)
        if manifest != scanned or sorted(manifest) != sorted(selected):
            raise RuntimeError("A public export scan hook added, removed, or changed files")
        digest = hashlib.sha256()
        for relative in sorted(selected):
            digest.update(f"{relative}\0{modes[relative]}\0{manifest[relative][0]}\0".encode())
        if out.exists():
            out.rmdir()
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "status": "exported",
        "destination": str(out),
        "source_commit": head,
        "files": sorted(selected),
        "file_sha256": {relative: value[0] for relative, value in manifest.items()},
        "export_sha256": digest.hexdigest(),
        "refused_private": sorted(refused_private),
        "scans": len(scans),
        "history": "none: blobs are read from the pinned commit without .git",
        "publication": "not performed: creating or pushing a repository needs separate approval",
    }


def _publication_declaration(root: Path) -> object:
    """Return the raw [publication] value, or None when the key is absent."""
    try:
        return _project_config(root).get("publication")
    except (OSError, ValueError):
        return None


def _check_publication(root: Path, findings: list[Finding]) -> None:
    """Opt-in journal/archive handoff. Undeclared means not assessed, never complete."""
    declaration = _publication_declaration(root)
    if declaration is None:
        return
    if not isinstance(declaration, dict):
        findings.append(
            _finding(
                "P1",
                "publication",
                "publication-metadata",
                "Declared publication metadata is missing or incomplete.",
                "[publication] must be a table of file paths",
            )
        )
        return

    def incomplete(field: str, problem: str) -> None:
        findings.append(
            _finding(
                "P1",
                "publication",
                "publication-metadata",
                "Declared publication metadata is missing or incomplete.",
                f"{field}: {problem}",
            )
        )

    texts: dict[str, str] = {}
    for field in PUBLICATION_FILES:
        relative = declaration.get(field)
        if not isinstance(relative, str) or not relative.strip():
            incomplete(field, "declare its project-relative path in [publication]")
            continue
        try:
            path = _safe_managed_path(root, relative.strip())
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            incomplete(field, f"{relative} is unreadable or missing ({type(exc).__name__})")
            continue
        if "TODO(publication)" in text:
            incomplete(field, f"{relative} still contains TODO(publication)")
        elif len(" ".join(text.split())) < 40:
            incomplete(field, f"{relative} is too short to be a real {field.replace('_', ' ')}")
        else:
            texts[field] = text
    doi = declaration.get("doi")
    citation_doi = None
    if "citation" in texts:
        citation: object = None
        try:
            citation = yaml.safe_load(texts["citation"]) if yaml is not None else None
        except (ValueError, yaml.YAMLError) as exc:
            incomplete("citation", f"CITATION.cff is not valid YAML: {exc}")
        else:
            if not isinstance(citation, dict):
                incomplete("citation", "CITATION.cff must be a mapping of citation fields")
        if isinstance(citation, dict):
            missing = [
                key
                for key in ("cff-version", "message", "title", "authors", "version")
                if not citation.get(key)
            ]
            if missing:
                incomplete("citation", "CITATION.cff lacks " + ", ".join(missing))
            citation_doi = citation.get("doi")
    for label, value in (("[publication].doi", doi), ("CITATION.cff doi", citation_doi)):
        if value is not None and not (
            isinstance(value, str) and re.fullmatch(r"10\.\d{4,9}/\S+", value)
        ):
            incomplete(label, "a DOI must look like 10.NNNN/suffix")
    if doi is not None and citation_doi is not None and doi != citation_doi:
        incomplete("doi", "[publication].doi and CITATION.cff doi differ")
    if doi is not None or citation_doi is not None:
        findings.append(
            _finding(
                "P2",
                "publication",
                "publication-doi-unverified",
                (
                    "A DOI is recorded but astack cannot check offline that it resolves to this "
                    "release's archive deposit. Confirm it before submission."
                ),
                str(doi or citation_doi),
            )
        )
