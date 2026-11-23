"""Verify pinned upstream bytes and copy them offline. No lifecycle or network operations.

Thin source-checkout wrapper around ``astack.upstream_skills`` (also ``astack skills``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Load the stdlib-only module by path, so plain `python3` works without installing the package.
_spec = importlib.util.spec_from_file_location(
    "_astack_upstream_skills", ROOT / "src/astack/upstream_skills.py"
)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
LOCK = _module.LOCK
MANIFEST = _module.MANIFEST
digest = _module.digest
install = _module.install
payload = _module.payload
safe_path = _module.safe_path

__all__ = ["LOCK", "MANIFEST", "ROOT", "digest", "install", "payload", "safe_path"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, help="Explicit provider skill discovery directory")
    parser.add_argument("--apply", action="store_true", help="Copy after full ownership preflight")
    args = parser.parse_args()
    if args.dest is None:
        if args.apply:
            parser.error("--apply requires --dest")
        print(json.dumps({"verified_files": len(payload(ROOT)), "lock": str(LOCK)}))
    else:
        print(json.dumps(install(ROOT, args.dest, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
