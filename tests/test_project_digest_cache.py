"""Large raw data must not be rehashed or copied needlessly, without weakening detection."""

from __future__ import annotations

import os

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    hashlib,  # noqa: F401
    json,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    yaml,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture
def cacheable(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Treat fresh test files as settled, and count real hashing."""
    monkeypatch.setattr(project_tool, "DIGEST_CACHE_RECENT_SECONDS", 0)
    monkeypatch.delenv(project_tool.DIGEST_CACHE_ENV, raising=False)
    project_tool._DIGEST_MEMO.clear()
    hashed: list[Path] = []
    original = project_tool._sha256

    def counting(path: Path) -> str:
        hashed.append(path)
        return str(original(path))

    monkeypatch.setattr(project_tool, "_sha256", counting)
    return hashed


def _sourced_raw(target: Path, content: bytes = b"original") -> Path:
    raw = target / "data/raw/source.bin"
    raw.write_bytes(content)
    raw.chmod(0o444)  # an immutable snapshot, as data/README.md requires
    source = {
        "title": "Source",
        "path": "data/raw/source.bin",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-07-16",
        "version": "1",
        "license": "CC0",
        "raw_checksum": "sha256:" + hashlib.sha256(content).hexdigest(),
        "sensitivity": "public",
        "allowed_outputs": ["derived tables"],
        "redistribution": "allowed",
        "citation": "Provider (2026)",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"source": source}}), encoding="utf-8"
    )
    return raw


def _raw_checksum_findings(target: Path) -> list[object]:
    findings: list[object] = []
    project_tool._check_sources(target, findings)
    return [item for item in findings if getattr(item, "check", "") == "raw-checksum"]


def test_unchanged_raw_file_is_hashed_once(tmp_path: Path, cacheable: list[Path]) -> None:
    target = _project(tmp_path)
    raw = _sourced_raw(target)

    assert not _raw_checksum_findings(target)
    assert not _raw_checksum_findings(target)

    assert [path for path in cacheable if path == raw] == [raw]


def test_same_size_rewrite_with_restored_mtime_is_rehashed_and_detected(
    tmp_path: Path, cacheable: list[Path]
) -> None:
    target = _project(tmp_path)
    raw = _sourced_raw(target)
    assert not _raw_checksum_findings(target)
    metadata = raw.stat()

    raw.chmod(0o644)
    raw.write_bytes(b"modified")
    raw.chmod(0o444)
    os.utime(raw, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

    assert _raw_checksum_findings(target)


def test_cache_persists_outside_the_worktree(tmp_path: Path, cacheable: list[Path]) -> None:
    target = _project(tmp_path)
    _git_baseline(target)
    raw = _sourced_raw(target)
    project_tool.audit(target)
    cache = project_tool._digest_cache_path(target)
    assert cache is not None and cache.is_file()
    assert target.resolve() not in cache.resolve().parents or ".git" in cache.parts

    project_tool._DIGEST_MEMO.clear()
    cacheable.clear()
    project_tool.audit(target)

    assert raw not in cacheable


def test_forged_cache_entry_for_a_different_stamp_is_ignored(
    tmp_path: Path, cacheable: list[Path]
) -> None:
    target = _project(tmp_path)
    _git_baseline(target)
    raw = _sourced_raw(target)
    project_tool.audit(target)
    cache = project_tool._digest_cache_path(target)
    assert cache is not None
    value = json.loads(cache.read_text(encoding="utf-8"))
    key = f"file:{raw.resolve()}"
    value["entries"][key][1] = "0" * 64
    value["entries"][key][0][2] += 1  # a size that no longer matches the file
    cache.write_text(json.dumps(value), encoding="utf-8")
    project_tool._DIGEST_MEMO.clear()

    assert not _raw_checksum_findings(target)


def test_cache_can_be_disabled(tmp_path: Path, cacheable: list[Path], monkeypatch) -> None:
    monkeypatch.setenv(project_tool.DIGEST_CACHE_ENV, "off")
    target = _project(tmp_path)
    raw = _sourced_raw(target)

    _raw_checksum_findings(target)
    _raw_checksum_findings(target)

    assert [path for path in cacheable if path == raw] == [raw, raw]


def test_writable_files_are_never_served_from_the_cache(
    tmp_path: Path, cacheable: list[Path]
) -> None:
    target = _project(tmp_path)
    raw = _sourced_raw(target)
    raw.chmod(0o644)

    _raw_checksum_findings(target)
    _raw_checksum_findings(target)

    assert [path for path in cacheable if path == raw] == [raw, raw]


def test_strict_audit_always_rehashes(tmp_path: Path, cacheable: list[Path]) -> None:
    target = _project(tmp_path)
    raw = _sourced_raw(target)
    project_tool.audit(target)
    cacheable.clear()

    project_tool.audit(target, strict=True)

    assert raw in cacheable


def _acquisition(target: Path, script: str) -> None:
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[acquisition]\ncommand = ["python", "scripts/acquire.py"]\n',
        encoding="utf-8",
    )
    (target / "scripts/acquire.py").write_text(script, encoding="utf-8")


@pytest.mark.parametrize(
    "script",
    [
        "import os\nos.rename('data/raw/original.txt', 'data/raw/original.bak')\n",
        "import os\nos.rename('data/raw/original.txt', 'original.bak')\n",
        "import os\nos.makedirs('.cache/holding')\n"
        "os.rename('data/raw/original.txt', '.cache/holding/original.bak')\n",
    ],
)
def test_acquisition_cannot_move_read_only_originals(tmp_path: Path, script: str) -> None:
    """Staged acquisition needs read-only raw, so every way of moving an original fails."""
    target = _project(tmp_path)
    original = target / "data/raw/original.txt"
    original.write_text("original\n", encoding="utf-8")
    _acquisition(target, script)
    _git_baseline(target)
    original.chmod(0o444)
    original.parent.chmod(0o555)

    with pytest.raises(RuntimeError, match="Permission"):
        project_tool.run_acquisition(target)

    assert original.read_text(encoding="utf-8") == "original\n"


def test_file_added_while_hashing_a_tree_is_never_cached_under_the_old_set(
    tmp_path: Path, cacheable: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.bin").write_bytes(b"a")
    (tree / "a.bin").chmod(0o444)
    late = tree / "b.bin"
    original = project_tool._tree_sha256

    def hash_while_adding(directory: Path) -> str:
        if not late.exists():
            late.write_bytes(b"b")
            late.chmod(0o444)
        return str(original(directory))

    monkeypatch.setattr(project_tool, "_tree_sha256", hash_while_adding)
    project_tool._cached_tree_sha256(tree)
    late.chmod(0o644)
    late.unlink()
    monkeypatch.setattr(project_tool, "_tree_sha256", original)

    assert project_tool._cached_tree_sha256(tree) == original(tree)


@pytest.mark.parametrize("content", ["null", "[]", '"text"'])
def test_non_object_cache_file_is_ignored(tmp_path: Path, content: str) -> None:
    target = _project(tmp_path)
    _git_baseline(target)
    cache = project_tool._digest_cache_path(target)
    assert cache is not None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(content, encoding="utf-8")

    project_tool.audit(target)
