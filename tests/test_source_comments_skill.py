"""The source-comments skill reaches projects without taking over any other skill's triggers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from astack.adapters import skill_wrapper
from astack.installation import MANIFEST, install_project
from astack.scaffold import create_project

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ".opencode/skills/source-comments/SKILL.md"
WRAPPERS = (".agents/skills/source-comments/SKILL.md", ".claude/skills/source-comments/SKILL.md")


def _create(tmp_path: Path) -> Path:
    target = tmp_path / "project"
    create_project(
        target,
        project_name="Comments",
        package_name="comments",
        description="Receive the source-comments skill.",
        lock=False,
    )
    return target


def _commit(project: Path) -> None:
    if not (project / ".git").exists():
        subprocess.run(["git", "init", "-qb", "main"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "x"],
        cwd=project,
        check=True,
    )


def test_skill_has_no_slash_command() -> None:
    assert not (ROOT / ".opencode/commands/source-comments.md").exists()


def test_new_project_receives_skill_and_matching_wrappers(tmp_path: Path) -> None:
    target = _create(tmp_path)
    source = (ROOT / CANONICAL).read_bytes()
    assert (target / CANONICAL).read_bytes() == source
    for wrapper in WRAPPERS:
        assert (target / wrapper).read_bytes() == skill_wrapper(source, CANONICAL)
        assert (ROOT / wrapper).read_bytes() == skill_wrapper(source, CANONICAL)


def test_upgrade_adds_skill_and_keeps_owner_guidance(tmp_path: Path) -> None:
    target = _create(tmp_path)
    paths = (CANONICAL, *WRAPPERS)
    manifest_path = target / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["managed_components"] = {
        key: value
        for key, value in manifest["managed_components"].items()
        if value["path"] not in paths
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for path in paths:
        (target / path).unlink()
        (target / path).parent.rmdir()
    owner_guidance = target / "AGENTS.md"
    owner_guidance.write_text("# Owner rules\n", encoding="utf-8")
    _commit(target)

    plan = install_project(target, candidate=True)
    assert not plan["conflicts"]
    actions = {action["path"]: action["action"] for action in plan["actions"]}
    assert all(actions[path] != "unchanged" for path in paths)
    install_project(target, candidate=True, apply=True, approve_plan=plan["plan_digest"])

    assert (target / CANONICAL).read_bytes() == (ROOT / CANONICAL).read_bytes()
    assert owner_guidance.read_text(encoding="utf-8") == "# Owner rules\n"
