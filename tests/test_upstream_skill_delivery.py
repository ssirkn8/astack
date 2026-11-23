"""Pinned upstream skills reach every supported harness's native discovery root.

Discovery roots were verified live on 2026-11-05: Claude Code 2.1.317 reads only
`.claude/skills`, Codex 0.166.0 and Pi 1.0.4 (after project trust) read `.agents/skills`, and
OpenCode 2.0.22 reads both. These tests pin that mapping and the managed ownership rules.
"""

import base64
import json
import subprocess
from pathlib import Path

import pytest

from astack import environment, installation, upstream_skills
from astack.installation import (
    MANIFEST,
    RETIRED_SKILL_PATHS,
    TRANSACTION,
    _hash,
    install_project,
    recover_installation,
)
from astack.scaffold import create_project

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
ROOTS = {"claude-code": ".claude/skills", "codex": ".agents/skills"}


def _locked() -> dict[str, tuple[bytes, bool]]:
    return upstream_skills.payload(ROOT)


def _commit(project: Path) -> None:
    subprocess.run(["git", "init", "-qb", "main"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "x"],
        cwd=project,
        check=True,
    )


def _assert_delivered(project: Path) -> None:
    owned = {
        entry["path"]: entry["sha256"]
        for entry in json.loads((project / MANIFEST).read_text())["managed_components"].values()
    }
    for relative, (data, executable) in _locked().items():
        if relative.startswith(".astack-notices/"):
            targets = [".astack/notices/" + relative.removeprefix(".astack-notices/")]
        else:
            targets = [f"{root}/{relative}" for root in ROOTS.values()]
        for target in targets:
            path = project / target
            assert path.read_bytes() == data, target
            assert bool(path.stat().st_mode & 0o111) == executable, target
            assert target in owned, target


def test_skill_count_comes_from_the_lock(tmp_path: Path) -> None:
    names = upstream_skills.skill_names(ROOT)
    assert len(names) == 13 and "grilling" in names and "full-stop" in names
    assert upstream_skills.install(ROOT, tmp_path)["skills"] == len(names)


def test_new_project_gets_pinned_skills_in_every_discovery_root(tmp_path: Path) -> None:
    project = tmp_path / "study"
    create_project(project, project_name="S", package_name="s", description="d", lock=False)
    _assert_delivered(project)


def test_install_delivers_pinned_skills_and_upgrades_are_unchanged(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    (project / "app.txt").write_text("owner\n")
    _commit(project)
    plan = install_project(project, candidate=True, commands=[["true"]])
    assert not plan["conflicts"]
    install_project(
        project,
        candidate=True,
        commands=[["true"]],
        apply=True,
        approve_plan=plan["plan_digest"],
    )
    _assert_delivered(project)
    _commit(project)
    again = install_project(project, candidate=True)
    skill_actions = {a["action"] for a in again["actions"] if "/skills/" in a["path"]}
    assert skill_actions == {"unchanged"}


def test_customized_upstream_skill_is_preserved_as_a_conflict(tmp_path: Path) -> None:
    project = tmp_path / "study"
    create_project(project, project_name="S", package_name="s", description="d", lock=False)
    edited = project / ".claude/skills/grilling/SKILL.md"
    edited.write_text(edited.read_text() + "\nMy own rule.\n")
    _commit(project)
    plan = install_project(project, candidate=True)
    assert ".claude/skills/grilling/SKILL.md" in plan["conflicts"]


@pytest.mark.parametrize("edited", [False, True])
def test_skills_copied_by_the_old_skills_command_are_taken_over_safely(
    tmp_path: Path, edited: bool
) -> None:
    project = tmp_path / "app"
    project.mkdir()
    upstream_skills.install(ROOT, project / ".agents/skills", apply=True)
    if edited:
        path = project / ".agents/skills/grill-me/SKILL.md"
        path.write_text(path.read_text() + "\nlocal\n")
    _commit(project)
    plan = install_project(project, candidate=True, commands=[["true"]])
    action = next(a for a in plan["actions"] if a["path"] == ".agents/skills/grill-me/SKILL.md")
    assert action["action"] == ("conflict" if edited else "unchanged")
    # Files the old command never installed still need managed ownership evidence.
    claude = next(a for a in plan["actions"] if a["path"] == ".claude/skills/grill-me/SKILL.md")
    assert claude["action"] == "create"


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude-code", ".claude/skills/grilling/SKILL.md"),
        ("codex", ".agents/skills/grilling/SKILL.md"),
        ("opencode", ".agents/skills/grilling/SKILL.md"),
        ("pi", ".agents/skills/grilling/SKILL.md"),
    ],
)
def test_doctor_checks_pinned_skills_where_each_harness_looks(
    tmp_path: Path, harness: str, expected: str
) -> None:
    project = tmp_path / "study"
    create_project(project, project_name="S", package_name="s", description="d", lock=False)
    checks = {c["id"]: c for c in environment.doctor(project, harness)["checks"]}
    assert checks[f"{harness}-asset-{expected}"]["status"] == "READY"
    (project / expected).unlink()
    checks = {c["id"]: c for c in environment.doctor(project, harness)["checks"]}
    assert checks[f"{harness}-asset-{expected}"]["status"] == "BLOCKED"


def _project(tmp_path: Path, profile: str) -> Path:
    project = tmp_path / "project"
    if profile == "ds":
        create_project(project, project_name="S", package_name="s", description="d", lock=False)
        _commit(project)
        return project
    project.mkdir()
    (project / "app.txt").write_text("owner\n")
    _commit(project)
    plan = install_project(project, candidate=True, commands=[["true"]])
    install_project(
        project,
        candidate=True,
        commands=[["true"]],
        apply=True,
        approve_plan=plan["plan_digest"],
    )
    _commit(project)
    return project


def _seed_retired_install(project: Path) -> None:
    """Put the project in the state an older astack left: retired skills, managed and clean."""
    manifest = json.loads((project / MANIFEST).read_text())
    for relative in sorted(RETIRED_SKILL_PATHS):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pinned " + relative.encode())
        manifest["managed_components"][f"upstream:{relative}"] = {
            "path": relative,
            "sha256": _hash(path.read_bytes()),
        }
    (project / MANIFEST).write_text(json.dumps(manifest))
    _commit(project)


def _upgrade(project: Path) -> dict:
    plan = install_project(project, candidate=True)
    assert not plan["conflicts"]
    return install_project(project, candidate=True, apply=True, approve_plan=plan["plan_digest"])


@pytest.mark.parametrize("profile", ["general", "ds"])
def test_fresh_install_exposes_only_grill_me(tmp_path: Path, profile: str) -> None:
    project = _project(tmp_path, profile)
    for root in ROOTS.values():
        names = {path.parent.name for path in (project / root).glob("*/SKILL.md")}
        assert {name for name in names if "grill" in name} == {"grill-me", "grilling"}
        assert "domain-modeling" not in names
        entry = (project / root / "grill-me/SKILL.md").read_text()
        assert 'Call the Skill tool with "grilling".' in entry
        assert "disable-model-invocation: true" in entry
        assert "**rounds**" in (project / root / "grilling/SKILL.md").read_text()
    commands = project / ".opencode/commands"
    assert not [path for path in commands.glob("*") if "grill" in path.name]
    managed = json.loads((project / MANIFEST).read_text())["managed_components"]
    assert not {entry["path"] for entry in managed.values()} & RETIRED_SKILL_PATHS


@pytest.mark.parametrize("profile", ["general", "ds"])
def test_upgrade_removes_unedited_retired_skills(tmp_path: Path, profile: str) -> None:
    project = _project(tmp_path, profile)
    _seed_retired_install(project)
    preview = install_project(project, candidate=True)
    assert set(preview["retired"]) == RETIRED_SKILL_PATHS
    assert all((project / path).exists() for path in RETIRED_SKILL_PATHS)
    _upgrade(project)
    for root in ROOTS.values():
        assert not (project / root / "grill-with-docs").exists()
        assert not (project / root / "domain-modeling").exists()
        assert (project / root / "grill-me/SKILL.md").is_file()
    managed = json.loads((project / MANIFEST).read_text())["managed_components"]
    assert not {entry["path"] for entry in managed.values()} & RETIRED_SKILL_PATHS
    _commit(project)
    assert not install_project(project, candidate=True)["retired"]


def test_upgrade_preserves_edited_and_independent_retired_skills(tmp_path: Path) -> None:
    project = _project(tmp_path, "general")
    _seed_retired_install(project)
    edited = project / ".claude/skills/grill-with-docs/SKILL.md"
    edited.write_text("my edit\n")
    extra = project / ".agents/skills/domain-modeling/my-notes.md"
    extra.write_text("mine\n")
    # Never recorded by astack, so it is independently installed even at a retired path.
    manifest = json.loads((project / MANIFEST).read_text())
    del manifest["managed_components"]["upstream:.claude/skills/domain-modeling/SKILL.md"]
    (project / MANIFEST).write_text(json.dumps(manifest))
    independent = project / ".claude/skills/domain-modeling/SKILL.md"
    independent.write_text("independent\n")
    _commit(project)

    result = _upgrade(project)

    assert edited.read_text() == "my edit\n"
    assert (project / ".claude/skills/grill-with-docs/agents/openai.yaml").is_file()
    assert extra.read_text() == "mine\n"
    assert independent.read_text() == "independent\n"
    assert (project / ".claude/skills/domain-modeling/ADR-FORMAT.md").is_file()
    assert (project / ".claude/skills/domain-modeling/GLOSSARY-FORMAT.md").is_file()
    assert ".claude/skills/grill-with-docs/SKILL.md" in result["released"]
    assert ".claude/skills/grill-with-docs/agents/openai.yaml" in result["released"]
    assert ".claude/skills/domain-modeling/ADR-FORMAT.md" in result["released"]
    assert not (project / ".agents/skills/domain-modeling/SKILL.md").exists()
    managed = json.loads((project / MANIFEST).read_text())["managed_components"]
    assert not {entry["path"] for entry in managed.values()} & RETIRED_SKILL_PATHS


def test_upgrade_clears_legacy_claims_on_retired_skills(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    dest = project / ".agents/skills"
    upstream_skills.install(ROOT, dest, apply=True)
    record = json.loads((dest / upstream_skills.MANIFEST).read_text())
    retired = dest / "grill-with-docs/SKILL.md"
    retired.parent.mkdir()
    retired.write_bytes(b"pinned")
    record["grill-with-docs/SKILL.md"] = {"sha256": _hash(b"pinned"), "executable": False}
    (dest / upstream_skills.MANIFEST).write_text(json.dumps(record))
    _commit(project)
    plan = install_project(project, candidate=True, commands=[["true"]])
    install_project(
        project,
        candidate=True,
        commands=[["true"]],
        apply=True,
        approve_plan=plan["plan_digest"],
    )
    assert not retired.exists()
    remaining = json.loads((dest / upstream_skills.MANIFEST).read_text())
    assert "grill-with-docs/SKILL.md" not in remaining
    # Reinstalling the same bytes yourself afterward makes them yours.
    retired.parent.mkdir()
    retired.write_bytes(b"pinned")
    _commit(project)
    assert not install_project(project, candidate=True)["retired"]


def test_failed_upgrade_restores_retired_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, "general")
    _seed_retired_install(project)
    before = {path: (project / path).read_bytes() for path in RETIRED_SKILL_PATHS}
    plan = install_project(project, candidate=True)
    real = installation._write_install_file

    def fail_on_manifest(path: Path, data: bytes, expected: str | None) -> None:
        if path.name == "manifest.json":
            raise OSError("simulated interruption")
        real(path, data, expected)

    monkeypatch.setattr(installation, "_write_install_file", fail_on_manifest)
    with pytest.raises(OSError, match="interruption"):
        install_project(project, candidate=True, apply=True, approve_plan=plan["plan_digest"])
    assert {path: (project / path).read_bytes() for path in before} == before
    assert not (project / TRANSACTION).exists()


def test_recovery_restores_a_retired_skill_removed_before_a_crash(tmp_path: Path) -> None:
    project = _project(tmp_path, "general")
    relative = ".claude/skills/grill-with-docs/SKILL.md"
    (project / TRANSACTION).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {
                    relative: {
                        "bytes": base64.b64encode(b"pinned").decode(),
                        "mode": 0o644,
                        "after_sha256": None,
                    }
                },
                "created_dirs": [],
            }
        )
    )
    plan = recover_installation(project)
    assert not plan["conflicts"]
    recover_installation(project, apply=True, approve_plan=plan["plan_digest"])
    assert (project / relative).read_bytes() == b"pinned"


def test_recovery_rejects_a_forged_removal_outside_retired_skills(tmp_path: Path) -> None:
    project = _project(tmp_path, "general")
    (project / TRANSACTION).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {
                    ".claude/skills/grill-me/SKILL.md": {
                        "bytes": base64.b64encode(b"forged").decode(),
                        "mode": 0o644,
                        "after_sha256": None,
                    }
                },
                "created_dirs": [],
            }
        )
    )
    with pytest.raises(ValueError, match="Invalid recovery file snapshot"):
        recover_installation(project)
