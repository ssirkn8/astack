"""Raw directory checksums must identify a tree unambiguously (issue #260)."""

from __future__ import annotations

import json
import os
import sys

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _project,  # noqa: F401
    hashlib,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    yaml,  # noqa: F401
)


def _colliding_trees(base: Path) -> tuple[Path, Path]:
    """Two different trees whose sha256-tree-v1 inputs are byte-identical."""
    one, two = base / "one", base / "two"
    one.mkdir()
    two.mkdir()
    (one / "a").write_bytes(b"\0b\0Y")
    (two / "a").write_bytes(b"")
    (two / "b").write_bytes(b"Y")
    return one, two


def test_v1_framing_collides_on_the_issue_example(tmp_path: Path) -> None:
    one, two = _colliding_trees(tmp_path)

    assert project_tool._tree_sha256(one) == project_tool._tree_sha256(two)


def test_v2_distinguishes_trees_that_collide_under_v1(tmp_path: Path) -> None:
    one, two = _colliding_trees(tmp_path)

    assert project_tool._tree_sha256_v2(one) != project_tool._tree_sha256_v2(two)


def test_v2_frames_each_entry_with_a_fixed_length_file_digest(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested/b.txt").write_bytes(b"bee")
    (tree / "a.txt").write_bytes(b"ay")

    expected = hashlib.sha256(b"astack-sha256-tree-v2\0")
    for relative, content in (("a.txt", b"ay"), ("nested/b.txt", b"bee")):
        expected.update(relative.encode() + b"\0" + hashlib.sha256(content).hexdigest().encode())
        expected.update(b"\n")

    assert project_tool._tree_sha256_v2(tree) == expected.hexdigest()


def test_v2_refuses_a_directory_symlink_inside_the_tree(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/hidden.bin").write_bytes(b"x")
    (tree / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink to a directory"):
        project_tool._tree_sha256_v2(tree)


def _tree_source(target: Path, checksum: str) -> None:
    source = {
        "title": "Tiles",
        "path": "data/raw/tiles",
        "origin": "Provider archive",
        "provider": "Provider",
        "access_date": "2026-11-11",
        "version": "1",
        "license": "CC0",
        "raw_checksum": checksum,
        "sensitivity": "public",
        "allowed_outputs": ["derived tables"],
        "redistribution": "allowed",
        "citation": "Provider (2026)",
    }
    (target / "data/sources.yml").write_text(
        yaml.safe_dump({"sources": {"tiles": source}}), encoding="utf-8"
    )


def _checks(target: Path) -> dict[str, str]:
    findings: list[object] = []
    project_tool._check_sources(target, findings)
    return {getattr(item, "check", ""): getattr(item, "priority", "") for item in findings}


def test_v1_collision_hides_a_changed_raw_directory_but_v2_detects_it(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a").write_bytes(b"\0b\0Y")
    v1 = project_tool._tree_sha256(tiles)
    v2 = project_tool._tree_sha256_v2(tiles)
    (tiles / "a").write_bytes(b"")
    (tiles / "b").write_bytes(b"Y")

    _tree_source(target, f"sha256-tree-v1:{v1}")
    assert "raw-checksum" not in _checks(target)  # the ambiguity the issue describes
    _tree_source(target, f"sha256-tree-v2:{v2}")
    assert _checks(target).get("raw-checksum") == "P0"


def test_matching_v2_checksum_passes_without_a_legacy_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a.bin").write_bytes(b"tile")
    _tree_source(target, f"sha256-tree-v2:{project_tool._tree_sha256_v2(tiles)}")

    checks = _checks(target)

    assert "raw-checksum" not in checks
    assert "legacy-tree-checksum" not in checks


def test_matching_v1_checksum_is_compared_but_blocks_release(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a.bin").write_bytes(b"tile")
    _tree_source(target, f"sha256-tree-v1:{project_tool._tree_sha256(tiles)}")

    checks = _checks(target)

    assert "raw-checksum" not in checks
    assert checks.get("legacy-tree-checksum") == "P1"


def test_v2_checksum_on_a_tree_with_a_directory_symlink_is_a_finding_not_a_crash(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tiles / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    _tree_source(target, "sha256-tree-v2:" + "0" * 64)

    assert _checks(target).get("raw-checksum") == "P0"


def test_file_checksum_never_matches_a_tree_and_v1_never_matches_v2(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a.bin").write_bytes(b"tile")
    v2 = project_tool._tree_sha256_v2(tiles)

    for checksum in (f"sha256:{v2}", f"sha256-tree-v1:{v2}"):
        _tree_source(target, checksum)
        assert _checks(target).get("raw-checksum") == "P0"


def test_checksum_command_prints_v2_for_directories_and_sha256_for_files(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a.bin").write_bytes(b"tile")

    tree = project_tool.source_checksum(tiles)
    single = project_tool.source_checksum(tiles / "a.bin")

    assert tree["checksum"] == f"sha256-tree-v2:{project_tool._tree_sha256_v2(tiles)}"
    assert single["checksum"] == "sha256:" + hashlib.sha256(b"tile").hexdigest()


def test_checksum_command_runs_from_the_cli(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a.bin").write_bytes(b"tile")

    completed = subprocess.run(
        [sys.executable, ".astack/workflow.py", "checksum", "data/raw/tiles"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"sha256-tree-v2:{project_tool._tree_sha256_v2(tiles)}" in completed.stdout
    assert json.loads(completed.stdout)


def test_verify_source_records_v2_for_a_retrieved_directory(tmp_path: Path) -> None:
    directory = tmp_path / "retrieved"
    directory.mkdir()
    (directory / "a.bin").write_bytes(b"tile")

    assert project_tool._retrieved_digest(directory) == (
        f"sha256-tree-v2:{project_tool._tree_sha256_v2(directory)}"
    )


@pytest.mark.parametrize("prefix", ["sha256-tree-v1:", "sha256-tree-v2:"])
def test_sha256_tree_identity_compares_with_its_own_version(prefix: str) -> None:
    hex_digest = "a" * 64
    kind = "tree" if prefix.endswith("v1:") else "tree-v2"
    value = {"raw_checksum": f"{prefix}{hex_digest}"}

    assert (
        project_tool._digest_consistency_problem(
            value, "sha256_tree", f"{prefix}{hex_digest}", (kind, hex_digest)
        )
        is None
    )
    other = "tree-v2" if kind == "tree" else "tree"
    assert project_tool._digest_consistency_problem(
        {}, "sha256_tree", f"{prefix}{hex_digest}", (other, hex_digest)
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read unreadable directories")
def test_unreadable_subdirectory_added_to_a_tree_is_a_mismatch_not_skipped(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    _tree_source(target, f"sha256-tree-v2:{project_tool._tree_sha256_v2(tiles)}")
    hidden = tiles / "hidden"
    hidden.mkdir()
    (hidden / "added.bin").write_bytes(b"new")
    hidden.chmod(0)
    try:
        with pytest.raises(PermissionError):
            project_tool._tree_sha256_v2(tiles)
        assert _checks(target).get("raw-checksum") == "P0"
    finally:
        hidden.chmod(0o755)


def test_sha256_identity_with_a_tree_prefix_never_matches_a_file_digest() -> None:
    hex_digest = "b" * 64
    for prefix in ("sha256-tree-v1:", "sha256-tree-v2:"):
        assert project_tool._digest_consistency_problem(
            {}, "sha256", f"{prefix}{hex_digest}", ("file", hex_digest)
        )
    assert (
        project_tool._digest_consistency_problem(
            {}, "sha256", f"sha256:{hex_digest}", ("file", hex_digest)
        )
        is None
    )


def test_verification_and_checksum_command_always_hash_fresh_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "retrieved"
    directory.mkdir()
    (directory / "a.bin").write_bytes(b"tile")
    fresh = project_tool._tree_sha256_v2(directory)
    real = project_tool._cached_sha256

    def stale_unless_bypassed(path: Path) -> str:
        if project_tool._DIGEST_POLICY["bypass"]:
            return str(real(path))
        return "0" * 64  # what a stale cache entry would return

    monkeypatch.setattr(project_tool, "_cached_sha256", stale_unless_bypassed)

    assert project_tool._retrieved_digest(directory) == f"sha256-tree-v2:{fresh}"
    assert project_tool.source_checksum(directory)["checksum"] == f"sha256-tree-v2:{fresh}"
    assert project_tool._DIGEST_POLICY["bypass"] is False


def test_v1_tree_checksum_is_a_release_blocking_audit_finding(tmp_path: Path) -> None:
    target = _project(tmp_path)
    tiles = target / "data/raw/tiles"
    tiles.mkdir()
    (tiles / "a.bin").write_bytes(b"tile")
    _tree_source(target, f"sha256-tree-v1:{project_tool._tree_sha256(tiles)}")

    findings = project_tool.audit(target)["findings"]

    # release_readiness fails on any P0 or P1 finding.
    assert {"check": "legacy-tree-checksum", "priority": "P1"}.items() <= next(
        item for item in findings if item["check"] == "legacy-tree-checksum"
    ).items()
