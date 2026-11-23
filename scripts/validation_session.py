"""Run the maintainer complete validation gate once and publish timing evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.validation_session import ValidationSession, run_benchmark

STAGES = [
    ("check", ["just", "check"]),
    ("integration", ["just", "integration"]),
    ("scaffold-smoke", ["just", "scaffold-smoke"]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="maintainer")
    parser.add_argument(
        "--benchmark-mode",
        choices=["serial", "parallel"],
        help="Run one fast-gate benchmark variant instead of the complete validation session.",
    )
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if args.benchmark_mode is not None:
        report = run_benchmark(root, args.benchmark_mode)
        benchmark_dir = root / ".cache" / "validation" / "benchmarks"
        benchmark_dir.mkdir(parents=True, exist_ok=True)
        report_path = benchmark_dir / f"fast-gate-{args.benchmark_mode}.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "passed" else 1
    session = ValidationSession(
        root,
        name=args.name,
        planned_stages=[name for name, _ in STAGES],
        budgets={"scaffold-smoke": 180.0},
        complete_budget=600.0,
    )
    status = "passed"
    try:
        for name, command in STAGES:
            result = session.run_command(name, command)
            if result.returncode != 0:
                status = "failed"
                break
    except OSError:
        status = "failed"
    report = session.finish(status=status)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
