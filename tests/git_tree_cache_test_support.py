"""Reuse an expensive Git fixture while giving each test its own full copy."""

from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

_TREES: dict[str, Path] = {}


def _assert_relocatable(source: Path, cache: Path) -> None:
    """A copy is only equivalent if the fixture never recorded where it was built."""
    marker = cache.name.encode()
    for path in source.rglob("*"):
        if path.is_symlink():
            embedded = marker in str(path.readlink()).encode()
        elif path.is_file():
            embedded = marker in path.read_bytes()
        else:
            continue
        if embedded:
            raise AssertionError(f"Cached fixture records its build path: {path}")


def copy_cached_git_tree(key: str, parent: Path, build: Callable[[Path], Path]) -> Path:
    """Build `key` once per test process, then copy it into `parent`."""
    source = _TREES.get(key)
    if source is None:
        cache = Path(tempfile.mkdtemp(prefix="astack-git-fixture-")).resolve()
        atexit.register(shutil.rmtree, cache, ignore_errors=True)
        source = build(cache)
        _assert_relocatable(source, cache)
        _TREES[key] = source
    target = parent / source.name
    shutil.copytree(source, target, symlinks=True)
    # Copies have new inodes and change times, so Git must re-stat them against its index.
    subprocess.run(
        ["git", "update-index", "-q", "--refresh"], cwd=target, check=False, capture_output=True
    )
    return target
