import importlib.util
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from astack import git_state

spec = importlib.util.spec_from_file_location(
    "validation_session", "workflow/validation_session.py"
)
assert spec is not None and spec.loader is not None
validation_session: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation_session)
ValidationSession = validation_session.ValidationSession


@pytest.mark.parametrize("exit_code", [0, 1])
def test_corrupt_diagnostics_do_not_change_validation_outcome(
    monkeypatch, tmp_path: Path, exit_code: int
) -> None:
    monkeypatch.setattr(validation_session, "_git_commit", lambda root: None)
    monkeypatch.setattr(validation_session, "_tool_version", lambda command: "test-tool")
    monkeypatch.setattr(git_state, "canonical_checkout", lambda root: tmp_path)
    monkeypatch.setattr(git_state, "state_lock", lambda root, name: nullcontext())
    monkeypatch.setattr(git_state, "state_path", lambda root, name: tmp_path / name)
    path = tmp_path / ".astack/diagnostics.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {
                        "schema_version": 1,
                        "metric_definition_version": 1,
                        "evidence": {"validation": {"status": []}},
                    }
                ],
            }
        )
    )
    session = ValidationSession(
        tmp_path,
        name="complete",
        planned_stages=["check"],
        runner=lambda command, cwd: subprocess.CompletedProcess(command, exit_code, "", ""),
    )
    session.run_command("check", ["just", "check"])
    assert session.finish()["status"] == ("passed" if exit_code == 0 else "failed")
    history = json.loads(path.read_text())
    assert len(history["records"]) == 2


def test_validation_session_records_one_current_run(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(validation_session, "_git_commit", lambda root: "a" * 40)
    monkeypatch.setattr(validation_session, "_tool_version", lambda command: "test-tool")
    session = ValidationSession(
        tmp_path,
        name="complete",
        planned_stages=["check", "integration"],
        runner=lambda command, cwd: subprocess.CompletedProcess(command, 0, "", ""),
    )
    (session.session_dir / "test-counts.json").write_text(
        json.dumps({"total": 4, "integration": 1, "fast": 3}), encoding="utf-8"
    )

    result = session.run_command("check", ["just", "check"])
    report = session.finish()

    assert result.returncode == 0
    assert report["status"] == "failed"
    assert report["commit"] == "a" * 40
    assert report["test_counts"] == {"total": 4, "integration": 1, "fast": 3}
    assert report["invocation_counts"] == {"check": 1}
    assert report["reliability"]["not_run_stages"] == ["integration"]
    assert report["max_rss_scope"] == "session"
    assert all("max_rss_bytes" not in stage for stage in report["stages"])
    assert (tmp_path / report["report"]).is_file()


def test_validation_session_marks_failed_stage_without_reusing_it(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(validation_session, "_git_commit", lambda root: None)
    monkeypatch.setattr(validation_session, "_tool_version", lambda command: "unavailable")

    def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "failed")

    session = ValidationSession(
        tmp_path,
        name="complete",
        planned_stages=["check"],
        runner=runner,
    )
    result = session.run_command("check", ["just", "check"])
    report = session.finish()

    assert result.returncode == 1
    assert report["status"] == "failed"
    assert report["reliability"]["failed_stages"] == ["check"]
    assert report["invocation_counts"] == {"check": 1}
