"""Fresh generated-project setup and diagnostics without live service dependencies."""

import json
from pathlib import Path

import pytest

from astack import environment
from astack.scaffold import create_project


@pytest.mark.integration
@pytest.mark.parametrize("harness", ["opencode", "pi", "codex", "claude-code"])
def test_fresh_generated_direct_tool_setup(tmp_path: Path, harness: str, monkeypatch) -> None:
    project = tmp_path / "study"
    create_project(
        project,
        project_name="Study",
        package_name="study",
        description="Fresh setup acceptance",
        lock=False,
    )
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    if harness == "pi":
        settings = json.loads((project / ".pi/settings.json").read_text())
        assert settings["enableSkillCommands"] is True
    report = environment.doctor(project, harness)
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["skill-catalog"]["status"] == "READY"
    assert not any("lifecycle" in identity for identity in checks)
    assert checks["native-discovery"]["status"] == "UNKNOWN"
    assert checks["native-discovery"]["required"] is False
    assert "not verified" in checks["native-discovery"]["summary"].lower()
