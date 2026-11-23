"""Run the parallel integration tier while allowing projects with no integration tests."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "integration", "-n", "logical"],
        check=False,
    )
    return 0 if result.returncode in {0, 5} else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
