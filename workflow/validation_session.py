"""Shared, single-run validation-session reporting and budget enforcement."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import resource as _resource_module
except ImportError:  # pragma: no cover - Windows has no resource module
    _resource: Any = None
else:
    _resource = _resource_module

SESSION_SCHEMA_VERSION = 1
DEFAULT_STAGE_BUDGETS = {
    "check": 90.0,
    "integration": 480.0,
    "scaffold-smoke": 180.0,
    "strict-audit": 180.0,
}
DEFAULT_COMPLETE_BUDGET = 600.0
BENCHMARK_COMMANDS = {
    "serial": ["uv", "run", "--locked", "pytest", "-m", "not integration"],
    "parallel": [
        "uv",
        "run",
        "--locked",
        "pytest",
        "-m",
        "not integration",
        "-n",
        "auto",
    ],
}
_Runner = Callable[..., subprocess.CompletedProcess[str]]


def _file_fingerprint(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if not path.is_file():
            digest.update(b"missing\0")
            continue
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def _tool_version(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    output = result.stdout.strip().splitlines()
    return output[0][:200] if output else "unavailable"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ValidationSession:
    """Run or record one complete gate without silently reusing stale evidence."""

    def __init__(
        self,
        root: Path,
        *,
        name: str,
        planned_stages: list[str],
        budgets: dict[str, float] | None = None,
        complete_budget: float = DEFAULT_COMPLETE_BUDGET,
        runner: _Runner | None = None,
    ) -> None:
        self.root = root.resolve()
        self.name = name
        self.planned_stages = list(planned_stages)
        self.budgets = {**DEFAULT_STAGE_BUDGETS, **(budgets or {})}
        self.complete_budget = complete_budget
        self.runner = runner
        self.started_monotonic = time.monotonic()
        self.started_at = _now()
        self.stages: list[dict[str, Any]] = []
        self.invocation_counts: dict[str, int] = {}
        self._test_counts: dict[str, int] = {}
        self._session_max_rss_bytes = 0
        self._session_id = f"{name}-{uuid.uuid4().hex}"
        self._session_dir = self.root / ".cache" / "validation" / "sessions" / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._previous_env = {
            key: os.environ.get(key)
            for key in ("ASTACK_VALIDATION_SESSION_DIR", "ASTACK_VALIDATION_SESSION_ID")
        }
        os.environ["ASTACK_VALIDATION_SESSION_DIR"] = str(self._session_dir)
        os.environ["ASTACK_VALIDATION_SESSION_ID"] = self._session_id

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    def _restore_environment(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _remember_test_counts(self) -> None:
        path = self._session_dir / "test-counts.json"
        if not path.is_file():
            return
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(value, dict) and all(
            isinstance(value.get(key), int) and value[key] >= 0
            for key in ("total", "integration", "fast")
        ):
            self._test_counts = {key: int(value[key]) for key in ("total", "integration", "fast")}

    def _record_stage(
        self,
        name: str,
        *,
        command: list[str] | None,
        started: float,
        status: str,
        returncode: int | None,
        budget: float | None,
        error: str | None = None,
        cpu_seconds: float | None = None,
        max_rss_bytes: int | None = None,
        count_invocation: bool = True,
    ) -> None:
        duration = time.monotonic() - started
        if count_invocation:
            self.invocation_counts[name] = self.invocation_counts.get(name, 0) + 1
        if max_rss_bytes is not None:
            self._session_max_rss_bytes = max(self._session_max_rss_bytes, max_rss_bytes)
        stage: dict[str, Any] = {
            "name": name,
            "status": status,
            "duration_seconds": round(duration, 3),
            "returncode": returncode,
        }
        if command is not None:
            stage["command"] = command
        if budget is not None:
            stage["budget_seconds"] = budget
            stage["budget_exceeded"] = duration > budget
        if error:
            stage["error"] = error[:200]
        if cpu_seconds is not None:
            stage["cpu_seconds"] = round(cpu_seconds, 3)
        self.stages.append(stage)
        self._remember_test_counts()

    def run_command(
        self,
        name: str,
        command: list[str],
        *,
        budget: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one stage and preserve its output while recording its evidence."""
        started = time.monotonic()
        usage_before = (
            _resource.getrusage(_resource.RUSAGE_CHILDREN) if _resource is not None else None
        )
        selected_budget = self.budgets.get(name, budget)
        try:
            if self.runner is None:
                result = subprocess.run(
                    command,
                    cwd=self.root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            else:
                result = self.runner(command, cwd=self.root)
        except OSError as exc:
            usage_after = (
                _resource.getrusage(_resource.RUSAGE_CHILDREN) if _resource is not None else None
            )
            self._record_stage(
                name,
                command=command,
                started=started,
                status="unavailable",
                returncode=None,
                budget=selected_budget,
                error=str(exc),
                cpu_seconds=self._cpu_delta(usage_before, usage_after),
                max_rss_bytes=self._rss_bytes(usage_after),
            )
            raise

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)
        duration = time.monotonic() - started
        usage_after = (
            _resource.getrusage(_resource.RUSAGE_CHILDREN) if _resource is not None else None
        )
        budget_exceeded = selected_budget is not None and duration > selected_budget
        status = "passed" if result.returncode == 0 and not budget_exceeded else "failed"
        error = (
            f"stage exceeded {selected_budget:.2f}s budget"
            if budget_exceeded and selected_budget is not None
            else None
        )
        self._record_stage(
            name,
            command=command,
            started=started,
            status=status,
            returncode=result.returncode if not budget_exceeded else 124,
            budget=selected_budget,
            error=error,
            cpu_seconds=self._cpu_delta(usage_before, usage_after),
            max_rss_bytes=self._rss_bytes(usage_after),
        )
        if budget_exceeded:
            return subprocess.CompletedProcess(command, 124, stdout, error or "budget exceeded")
        return result

    @staticmethod
    def _cpu_delta(before: Any, after: Any) -> float | None:
        if before is None or after is None:
            return None
        return float(
            max(
                0.0,
                (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime),
            )
        )

    @staticmethod
    def _rss_bytes(usage: Any) -> int | None:
        if usage is None:
            return None
        # macOS reports bytes while Linux reports KiB for ru_maxrss.
        multiplier = 1 if sys.platform == "darwin" else 1024
        return int(usage.ru_maxrss * multiplier)

    def record_stage(
        self,
        name: str,
        *,
        started: float,
        status: str,
        error: str | None = None,
    ) -> None:
        self._record_stage(
            name,
            command=None,
            started=started,
            status=status,
            returncode=0 if status == "passed" else 1,
            budget=self.budgets.get(name),
            error=error,
        )

    def finish(self, *, status: str | None = None) -> dict[str, Any]:
        """Write one machine-readable report and return it without exposing local paths."""
        self._remember_test_counts()
        elapsed = time.monotonic() - self.started_monotonic
        missing = [
            name
            for name in self.planned_stages
            if not any(stage["name"] == name for stage in self.stages)
        ]
        for name in missing:
            self._record_stage(
                name,
                command=None,
                started=time.monotonic(),
                status="not-run",
                returncode=None,
                budget=self.budgets.get(name),
                error="stage was not executed",
                count_invocation=False,
            )
        if elapsed > self.complete_budget:
            status = "failed"
        if status is None:
            status = (
                "passed" if all(stage["status"] == "passed" for stage in self.stages) else "failed"
            )
        finished_at = _now()
        capabilities = self._configured_capabilities()
        tools = {
            "uv": _tool_version(["uv", "--version"]),
            "just": _tool_version(["just", "--version"]),
            "pytest": _tool_version(["uv", "run", "--locked", "pytest", "--version"]),
            "ruff": _tool_version(["uv", "run", "--locked", "ruff", "--version"]),
            "mypy": _tool_version(["uv", "run", "--locked", "mypy", "--version"]),
        }
        optional_tools = {
            "dvc": ["uv", "run", "--locked", "dvc", "--version"],
            "quarto": ["quarto", "--version"],
            "marimo": ["uv", "run", "--locked", "marimo", "--version"],
        }
        for capability in capabilities:
            if capability in optional_tools:
                tools[capability] = _tool_version(optional_tools[capability])
        report: dict[str, Any] = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session": self.name,
            "status": status,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "total_duration_seconds": round(elapsed, 3),
            "complete_budget_seconds": self.complete_budget,
            "complete_budget_exceeded": elapsed > self.complete_budget,
            "compute_seconds": round(
                sum(float(stage.get("cpu_seconds", 0.0)) for stage in self.stages), 3
            ),
            "max_rss_bytes": self._session_max_rss_bytes,
            "max_rss_scope": "session",
            "commit": _git_commit(self.root),
            "fingerprints": {
                "lock": _file_fingerprint(self.root, ["uv.lock"]),
                "configuration": _file_fingerprint(
                    self.root,
                    [
                        "pyproject.toml",
                        "justfile",
                        ".astack/project.toml",
                        ".github/workflows/checks.yml",
                    ],
                ),
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
            },
            "tools": tools,
            "configured_capabilities": capabilities,
            "test_counts": self._test_counts,
            "stages": self.stages,
            "invocation_counts": self.invocation_counts,
            "reliability": {
                "failed_stages": [
                    stage["name"] for stage in self.stages if stage["status"] == "failed"
                ],
                "unavailable_stages": [
                    stage["name"] for stage in self.stages if stage["status"] == "unavailable"
                ],
                "not_run_stages": [
                    stage["name"] for stage in self.stages if stage["status"] == "not-run"
                ],
                "retries": 0,
            },
        }
        report_dir = self.root / ".cache" / "validation"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = report_dir / f"{self.name}-{timestamp}-{uuid.uuid4().hex[:8]}.json"
        report["report"] = f".cache/validation/{report_path.name}"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        latest_path = report_dir / f"{self.name}-latest.json"
        latest_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self._write_summary(report)
        self._restore_environment()
        # Reuse this run's evidence, never start a diagnostic benchmark or network probe.
        try:
            if (self.root / ".astack" / "git_state.py").is_file():
                from git_state import (  # type: ignore[import-not-found]
                    record_validation_diagnostics,
                )
            else:
                from astack.git_state import record_validation_diagnostics
            record_validation_diagnostics(self.root, report)
        except (ImportError, OSError, RuntimeError, ValueError):
            # Diagnostic history is observational, not validation or scientific authority.
            pass
        return report

    def _configured_capabilities(self) -> list[str]:
        path = self.root / ".astack" / "project.toml"
        if not path.is_file():
            return []
        try:
            import tomllib

            value = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        capabilities = value.get("tools", []) if isinstance(value, dict) else []
        return sorted(str(item) for item in capabilities) if isinstance(capabilities, list) else []

    def _write_summary(self, report: dict[str, Any]) -> None:
        path_value = os.environ.get("GITHUB_STEP_SUMMARY")
        if not path_value:
            return
        lines = [
            f"## Validation session: `{report['session']}`",
            "",
            f"- Status: **{report['status']}**",
            f"- Total duration: `{report['total_duration_seconds']:.3f}s`",
            f"- Commit: `{report['commit'] or 'unavailable'}`",
            "",
            "| Stage | Status | Duration | Budget |",
            "| --- | --- | ---: | ---: |",
        ]
        for stage in report["stages"]:
            budget = stage.get("budget_seconds", "-")
            budget_text = f"{budget:.1f}s" if isinstance(budget, (int, float)) else str(budget)
            lines.append(
                f"| `{stage['name']}` | {stage['status']} | "
                f"{stage['duration_seconds']:.3f}s | {budget_text} |"
            )
        lines.extend(
            [
                "",
                f"Report: `{report.get('report', '.cache/validation')}`",
                "",
            ]
        )
        try:
            with Path(path_value).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
        except OSError:
            pass


def run_benchmark(root: Path, mode: str) -> dict[str, Any]:
    """Run one fast-gate variant and return comparable machine-readable evidence."""
    try:
        command = BENCHMARK_COMMANDS[mode]
    except KeyError as exc:
        raise ValueError(f"Unknown benchmark mode: {mode}") from exc

    started = time.monotonic()
    usage_before = _resource.getrusage(_resource.RUSAGE_CHILDREN) if _resource is not None else None
    error: str | None = None
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        result = None
        error = str(exc)
    usage_after = _resource.getrusage(_resource.RUSAGE_CHILDREN) if _resource is not None else None
    if result is not None:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

    report: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "fast-gate",
        "mode": mode,
        "command": command,
        "test_selector": "pytest -m 'not integration'",
        "status": "unavailable"
        if result is None
        else ("passed" if result.returncode == 0 else "failed"),
        "returncode": None if result is None else result.returncode,
        "error": error,
        "wall_time_seconds": round(time.monotonic() - started, 3),
        "compute_seconds": round(ValidationSession._cpu_delta(usage_before, usage_after) or 0.0, 3),
        "max_rss_bytes": ValidationSession._rss_bytes(usage_after),
        "max_rss_scope": "benchmark-process-tree",
        "commit": _git_commit(root),
        "fingerprints": {
            "lock": _file_fingerprint(root, ["uv.lock"]),
            "configuration": _file_fingerprint(
                root,
                ["pyproject.toml", "justfile", ".github/workflows/checks.yml"],
            ),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "tools": {
            "uv": _tool_version(["uv", "--version"]),
            "pytest": _tool_version(["uv", "run", "--locked", "pytest", "--version"]),
        },
    }
    return report
