import json
import subprocess
from pathlib import Path

import pytest

from astack import environment
from astack.scaffold import create_project


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".astack").mkdir()
    return root


def _generated(tmp_path: Path) -> Path:
    root = tmp_path / "generated"
    create_project(
        root,
        project_name="Generated",
        package_name="generated",
        description="Doctor fixture",
        lock=False,
    )
    return root


def test_doctor_reports_files_without_claiming_native_discovery(tmp_path, monkeypatch):
    root = _generated(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    report = environment.doctor(root, "opencode")
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["opencode-asset-.opencode/commands/ds-repo-audit.md"]["status"] == "READY"
    assert not any("ds-start" in identity for identity in checks)
    assert checks["native-discovery"]["status"] == "UNKNOWN"
    assert checks["native-discovery"]["required"] is False
    assert "Not verified" in checks["native-discovery"]["summary"]
    assert "local_runner" not in report
    assert "controller" not in report


def test_missing_native_skill_asset_blocks_doctor(tmp_path, monkeypatch):
    root = _generated(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    (root / ".claude/skills/ds-repo-audit/SKILL.md").unlink()
    report = environment.doctor(root, "claude-code")
    assert report["status"] == "BLOCKED"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["claude-code-asset-.claude/skills/ds-repo-audit/SKILL.md"]["status"] == "BLOCKED"


def test_direct_tool_readiness_does_not_require_native_discovery_or_run_executor(
    tmp_path, monkeypatch
):
    root = _generated(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "mock version", ""),
    )
    report = environment.doctor(root, "opencode")
    assert report["status"] == "READY"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["native-discovery"]["status"] == "UNKNOWN"
    assert checks["native-discovery"]["required"] is False
    assert all(item["status"] == "READY" for item in checks.values() if item["required"])
    assert not {"child-executor", "controller", "sandbox", "packet"}.intersection(checks)


def test_doctor_managed_integrity_and_generated_environment(tmp_path, monkeypatch):
    root = _generated(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    report = environment.doctor(root, "opencode")
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["managed-checksums"]["status"] == "READY"
    assert checks["native-discovery"]["status"] == "UNKNOWN"
    asset = root / ".opencode/commands/ds-repo-audit.md"
    asset.write_text(asset.read_text() + "tampered")
    report = environment.doctor(root, "opencode")
    assert (
        next(item for item in report["checks"] if item["id"] == "managed-checksums")["status"]
        == "BLOCKED"
    )


def test_invalid_harness_fails_closed(tmp_path):
    assert environment.doctor(tmp_path, "unknown")["status"] == "BLOCKED"


@pytest.mark.parametrize("missing", ["ownership", "file", "both"])
def test_doctor_rejects_incomplete_runtime_evidence(tmp_path, monkeypatch, missing):
    root = _generated(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "version", ""),
    )
    relative = ".astack/git_state.py"
    if missing in {"ownership", "both"}:
        path = root / ".astack/manifest.json"
        manifest = json.loads(path.read_text())
        manifest["managed_components"] = {
            key: value
            for key, value in manifest["managed_components"].items()
            if value["path"] != relative
        }
        path.write_text(json.dumps(manifest))
    if missing in {"file", "both"}:
        (root / relative).unlink()
    report = environment.doctor(root, "opencode")
    assert report["status"] == "BLOCKED"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["managed-checksums"]["status"] == "BLOCKED"


def test_doctor_without_harness_checks_all_skill_roots_and_reports_optional_tools(
    tmp_path, monkeypatch
):
    root = _generated(tmp_path)
    installed = {"git", "uv", "python3", "codex", "just"}
    monkeypatch.setattr(
        environment.shutil, "which", lambda name: f"/mock/{name}" if name in installed else None
    )
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "mock version", ""),
    )

    report = environment.doctor(root)

    assert report["status"] == "READY"
    assert report["harness"] is None
    assert report["repository_portability"]["status"] == "READY"
    assert report["environment_readiness"]["status"] == "READY"
    assert report["environment_readiness"]["detected_harnesses"] == ["codex"]
    assert report["environment_readiness"]["harnesses"] == {
        "opencode": "NOT INSTALLED",
        "pi": "NOT INSTALLED",
        "codex": "READY",
        "claude-code": "NOT INSTALLED",
    }
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["skill-asset-.opencode/skills/ds-repo-audit/SKILL.md"]["status"] == "READY"
    assert checks["skill-asset-.agents/skills/ds-repo-audit/SKILL.md"]["status"] == "READY"
    assert checks["skill-asset-.claude/skills/ds-repo-audit/SKILL.md"]["status"] == "READY"
    assert checks["skill-asset-.agents/skills/grilling/SKILL.md"]["status"] == "READY"
    assert checks["skill-asset-.claude/skills/grilling/SKILL.md"]["status"] == "READY"
    assert checks["just-version"]["status"] == "READY"
    assert checks["just-version"]["required"] is False
    assert checks["gh-version"]["status"] == "NOT INSTALLED"
    assert checks["gh-version"]["required"] is False
    assert checks["harness-codex"]["status"] == "READY"
    assert checks["harness-codex"]["required"] is False
    assert checks["harness-claude-code"]["status"] == "NOT INSTALLED"
    assert checks["harness-claude-code"]["required"] is False
    assert checks["native-discovery"]["status"] == "UNKNOWN"
    assert checks["native-discovery"]["required"] is False


@pytest.mark.parametrize(
    "relative",
    [
        ".opencode/skills/ds-repo-audit/SKILL.md",
        ".agents/skills/ds-repo-audit/SKILL.md",
        ".claude/skills/ds-repo-audit/SKILL.md",
    ],
)
def test_doctor_without_harness_blocks_on_missing_skill_file_in_any_root(
    tmp_path, monkeypatch, relative
):
    root = _generated(tmp_path)
    monkeypatch.setattr(environment.shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "mock version", ""),
    )
    (root / relative).unlink()

    report = environment.doctor(root)

    assert report["status"] == "BLOCKED"
    assert report["repository_portability"]["status"] == "BLOCKED"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks[f"skill-asset-{relative}"]["status"] == "BLOCKED"


def test_doctor_without_harness_blocks_when_required_project_tool_is_missing(tmp_path, monkeypatch):
    root = _generated(tmp_path)
    installed = {"git", "python3", "codex", "claude", "opencode", "pi", "just", "gh"}
    monkeypatch.setattr(
        environment.shutil, "which", lambda name: f"/mock/{name}" if name in installed else None
    )
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "mock version", ""),
    )

    report = environment.doctor(root)

    assert report["status"] == "BLOCKED"
    assert report["environment_readiness"]["status"] == "BLOCKED"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["uv-version"]["status"] == "BLOCKED"


def test_probe_binary_handles_non_utf8_version_output(tmp_path) -> None:
    import sys

    assert environment._probe_binary(
        tmp_path,
        sys.executable,
        ["-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe version\\n')"],
    ) == (True, True)
