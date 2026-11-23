"""Verify that the fast and integration pytest gates partition the collected suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest


class _Collection:
    def __init__(self, allow_empty_integration: bool) -> None:
        self.allow_empty_integration = allow_empty_integration
        self.total = 0
        self.integration = 0

    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        self.total = len(items)
        self.integration = sum(item.get_closest_marker("integration") is not None for item in items)

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        if self.total == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            return
        if self.integration == 0 and not self.allow_empty_integration:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            return
        if self.integration > self.total:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _input_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    candidates = [root / "pyproject.toml", root / "uv.lock"]
    candidates.extend(sorted((root / "tests").rglob("*.py")) if (root / "tests").exists() else [])
    for path in candidates:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()


def _session_cache(root: Path, allow_empty_integration: bool) -> tuple[Path, str] | None:
    value = os.environ.get("ASTACK_VALIDATION_SESSION_DIR")
    if not value:
        return None
    directory = Path(value).resolve()
    if root not in directory.parents:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    suffix = "allow-empty" if allow_empty_integration else "require-integration"
    return directory / f"test-collection-{suffix}.json", _input_fingerprint(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-empty-integration",
        action="store_true",
        help="Allow a generated project to have no integration-marked tests.",
    )
    args = parser.parse_args()
    root = Path.cwd().resolve()
    cached = _session_cache(root, args.allow_empty_integration)
    if cached is not None:
        cache_path, fingerprint = cached
        try:
            value = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = None
        if (
            isinstance(value, dict)
            and value.get("input_fingerprint") == fingerprint
            and value.get("status") == "passed"
        ):
            print(json.dumps({**value, "cached": True}, sort_keys=True))
            return 0
    collector = _Collection(args.allow_empty_integration)
    result = pytest.main(["--collect-only", "-q"], plugins=[collector])
    payload = {
        "total": collector.total,
        "integration": collector.integration,
        "fast": collector.total - collector.integration,
        "status": "passed" if result == pytest.ExitCode.OK else "failed",
    }
    if cached is not None:
        cache_path, fingerprint = cached
        cache_path.write_text(
            json.dumps({**payload, "input_fingerprint": fingerprint}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (cache_path.parent / "test-counts.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(payload, sort_keys=True))
    if result != pytest.ExitCode.OK:
        if collector.total == 0:
            print("No tests were collected.", file=sys.stderr)
        elif collector.integration == 0 and not args.allow_empty_integration:
            print("No integration-marked tests were collected.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
