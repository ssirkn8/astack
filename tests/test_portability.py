"""Deterministic portability evidence is not a claim of live client parity."""

import json
import subprocess
from pathlib import Path

import pytest

from astack import environment
from astack.adapters import HARNESSES, main, native_adapters, orphaned_wrappers, skill_wrapper
from astack.components import load_components
from astack.installation import install_project
from astack.scaffold import create_project

ROOT = Path(__file__).parents[1]


def test_every_kept_skill_has_thin_native_wrappers_without_duplicated_rules():
    for source in sorted((ROOT / ".opencode/skills").glob("*/SKILL.md")):
        canonical = f".opencode/skills/{source.parent.name}/SKILL.md"
        for discovery in (".agents/skills", ".claude/skills"):
            native = ROOT / discovery / source.parent.name / "SKILL.md"
            assert native.read_bytes() == skill_wrapper(source.read_bytes(), canonical)
            assert "lifecycle contract" not in native.read_text()


def test_authored_native_surfaces_are_generated_without_drift():
    for relative, expected in native_adapters(ROOT, maintainer=True).items():
        assert (ROOT / relative).read_bytes() == expected


def test_wrapper_left_behind_by_a_removed_skill_is_reported(tmp_path):
    skill = tmp_path / ".opencode/skills/kept/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: kept\ndescription: Kept.\n---\n")
    expected = {".agents/skills/kept/SKILL.md", ".claude/skills/kept/SKILL.md"}
    for relative in expected:
        (tmp_path / relative).parent.mkdir(parents=True)
        (tmp_path / relative).write_bytes(
            skill_wrapper(skill.read_bytes(), ".opencode/skills/kept/SKILL.md")
        )
    stale = tmp_path / ".claude/skills/removed/SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(skill_wrapper(skill.read_bytes(), ".opencode/skills/removed/SKILL.md"))
    owned = tmp_path / ".claude/skills/hand-written/SKILL.md"
    owned.parent.mkdir(parents=True)
    owned.write_text("---\nname: hand-written\ndescription: Mine.\n---\n")

    orphans = orphaned_wrappers(tmp_path, expected)

    assert orphans == [".claude/skills/removed/SKILL.md (orphaned: its authored skill is gone)"]


def test_repository_has_no_orphaned_wrappers():
    assert orphaned_wrappers(ROOT, set(native_adapters(ROOT, maintainer=True))) == []


def test_regeneration_preserves_customized_generated_wrapper_before_any_write(
    tmp_path, monkeypatch
):
    root = tmp_path / "root"
    source = root / ".opencode/skills/example/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("---\nname: example\ndescription: Example\n---\n\nCanonical body.\n")
    (root / "workflow").mkdir()
    (root / "workflow/components.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "components": [
                    {
                        "id": "example",
                        "kind": "opencode",
                        "source": "skills/example/SKILL.md",
                        "target": ".opencode/skills/example/SKILL.md",
                    }
                ],
            }
        )
    )
    target = root / ".claude/skills/example/SKILL.md"
    target.parent.mkdir(parents=True)
    customized = (
        skill_wrapper(source.read_bytes(), ".opencode/skills/example/SKILL.md") + b"Custom\n"
    )
    target.write_bytes(customized)
    monkeypatch.setattr("sys.argv", ["generate_adapters", "--root", str(root)])
    with pytest.raises(ValueError, match="customized or unknown"):
        main()
    assert target.read_bytes() == customized
    assert not (root / ".agents/skills/example/SKILL.md").exists()


def test_project_generation_does_not_leak_maintainer_only_discovery():
    generated = native_adapters(ROOT, maintainer=False)
    assert not any("/workflow-" in path for path in generated)
    assert ".agents/skills/astack-upgrade/SKILL.md" in generated


@pytest.mark.parametrize("harness", ["codex", "claude-code"])
def test_single_install_and_safe_customized_wrapper_upgrade(tmp_path, harness):
    project = tmp_path / "project"
    create_project(
        project, project_name="Portable", package_name="portable", description="fixture", lock=False
    )
    root = ".agents/skills" if harness == "codex" else ".claude/skills"
    project_skills = {
        component.source.split("/")[1]
        for component in load_components(ROOT)
        if component.kind == "opencode" and component.source.startswith("skills/")
    }
    for name in project_skills:
        assert (project / root / name / "SKILL.md").is_file()
    assert not list(project.glob(".agents/skills/workflow-*/SKILL.md"))
    assert not list(project.glob(".claude/skills/workflow-*/SKILL.md"))
    relative = f"{root}/ds-repo-audit/SKILL.md"
    customized = project / relative
    customized.write_text(customized.read_text() + "\nUser-owned customization\n")
    before = customized.read_bytes()
    subprocess.run(["git", "init", "-qb", "main"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "x"],
        cwd=project,
        check=True,
    )
    plan = install_project(project, candidate=True)
    assert relative in plan["conflicts"]  # Upgrades preserve the customized wrapper.
    with pytest.raises(ValueError, match="conflicts"):
        install_project(project, candidate=True, apply=True, approve_plan=plan["plan_digest"])
    assert customized.read_bytes() == before
    targets = {component.target for component in load_components(ROOT)}
    assert relative in targets


@pytest.mark.parametrize("harness", HARNESSES)
def test_repository_portability_is_not_machine_installation(tmp_path, monkeypatch, harness):
    project = tmp_path / "project"
    create_project(
        project, project_name="Portable", package_name="portable", description="fixture", lock=False
    )
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    report = environment.doctor(project, harness)
    assert report["repository_portability"]["status"] == "READY"
    assert report["environment_readiness"]["status"] == "NOT INSTALLED"
    assert report["environment_readiness"]["native_discovery"] == "UNKNOWN"
    assert all(value.startswith("UNKNOWN") for value in report["control_surfaces"].values())
    assert report["status"] == "BLOCKED"


def test_maintainer_claude_skills_hold_exact_pinned_copies():
    from astack.adapters import maintainer_upstream_copies
    from astack.upstream_skills import skill_names

    copies = maintainer_upstream_copies(ROOT)
    assert {path.split("/")[2] for path in copies} == set(skill_names(ROOT))
    for relative, (data, executable) in copies.items():
        path = ROOT / relative
        assert path.read_bytes() == data, relative
        assert bool(path.stat().st_mode & 0o111) == executable, relative
        canonical = ROOT / ".agents/skills" / relative.removeprefix(".claude/skills/")
        assert canonical.read_bytes() == data


def test_adapter_check_reports_an_edited_pinned_copy(tmp_path):
    import shutil

    root = tmp_path / "root"
    for part in (".opencode", ".agents/skills", "integrations", "workflow", ".claude"):
        shutil.copytree(ROOT / part, root / part)
    edited = root / ".claude/skills/grilling/SKILL.md"
    edited.write_text(edited.read_text() + "\nlocal edit\n")
    result = subprocess.run(
        [
            "python",
            "-c",
            "from astack.adapters import main; main()",
            "--root",
            str(root),
            "--check",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and ".claude/skills/grilling/SKILL.md" in result.stderr
