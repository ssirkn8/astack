import json
from pathlib import Path

import yaml

from astack.components import component_source, load_components
from astack.scaffold import create_project

COMMAND_FIELDS = {"description", "agent", "model", "variant", "subtask"}
SKILL_FIELDS = {"name", "description", "license", "compatibility", "metadata"}
AGENT_FIELDS = {
    "name",
    "model",
    "variant",
    "description",
    "mode",
    "hidden",
    "color",
    "steps",
    "options",
    "permission",
    "disable",
    "temperature",
    "top_p",
}


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    _, raw, _ = text.split("---", 2)
    value = yaml.safe_load(raw)
    assert isinstance(value, dict), path
    return value


def test_skill_frontmatter_uses_supported_fields() -> None:
    for path in Path(".opencode/skills").glob("*/SKILL.md"):
        data = _frontmatter(path)
        assert set(data) <= SKILL_FIELDS, path
        assert data["name"] == path.parent.name
        assert data.get("description")


def test_command_frontmatter_uses_supported_fields() -> None:
    for path in Path(".opencode/commands").glob("*.md"):
        data = _frontmatter(path)
        assert set(data) <= COMMAND_FIELDS, path
        assert data.get("description")


def test_agent_frontmatter_uses_supported_fields() -> None:
    for path in Path(".opencode/agents").glob("*.md"):
        data = _frontmatter(path)
        assert set(data) <= AGENT_FIELDS, path
        assert data.get("description")
        assert data.get("mode") in {"primary", "subagent", "all"}


LIFECYCLE_NAMES = (
    "start",
    "resume",
    "status",
    "checkpoint",
    "ship",
    "review-pr",
    "fix-pr",
    "land-pr",
    "sync-base",
    "supersede-pr",
    "merge-review-pr",
    "routing",
    "direct",
    "maintenance",
)


def test_human_decision_policy_is_explicit_for_direct_commands() -> None:
    policy = Path(".opencode/skills/human-decisions/SKILL.md").read_text(encoding="utf-8")
    normalized_policy = " ".join(policy.split())
    for required in [
        "MUST_ASK",
        "MAY_ASK",
        "MUST_NOT_ASK",
        "Batch foreseeable questions",
        "no arbitrary numeric question limit",
        "durable evidence",
        "native question tool",
        "canceled question",
        "ask an open-ended question in text",
        "tool-permission response is never approval",
        "Missing, skipped, or unavailable evidence is never a pass",
    ]:
        assert required in normalized_policy, required
    for no_approval in (
        "declined choice",
        "dismissed or canceled question",
        "rejected tool call",
        "empty answer",
        "unavailable question interaction",
    ):
        assert no_approval in normalized_policy
    assert "is not approval" in normalized_policy
    for retired in ("record-plan-approval", "execute-to-pr", "Land operation", "work-unit"):
        assert retired not in normalized_policy, retired


def test_human_decision_policy_is_referenced_by_authored_sources_and_generated_projects(
    tmp_path: Path,
) -> None:
    shared_path = ".opencode/skills/human-decisions/SKILL.md"
    shared = Path(shared_path)
    assert shared.is_file()
    for skill in Path(".opencode/skills").glob("*/SKILL.md"):
        if skill.parent.name != "human-decisions":
            assert shared_path in skill.read_text(encoding="utf-8"), skill
    assert shared_path in Path("templates/project/AGENTS.md").read_text(encoding="utf-8")
    for command in Path(".opencode/commands").glob("*.md"):
        assert "Read `AGENTS.md`" in command.read_text(encoding="utf-8"), command

    components = load_components(Path("."))
    component = next(item for item in components if item.target == shared_path)
    assert component.source == "skills/human-decisions/SKILL.md"
    assert component_source(Path("."), component).resolve() == shared.resolve()

    generated = tmp_path / "generated-human-decisions"
    create_project(
        generated,
        project_name="Human Decisions",
        package_name="human_decisions",
        description="Install the shared human-decision contract.",
        lock=False,
    )
    assert (generated / shared_path).read_bytes() == shared.read_bytes()


def test_no_lifecycle_skill_command_or_adapter_is_authored_or_generated() -> None:
    authored = [
        *Path(".opencode/skills").iterdir(),
        *Path(".opencode/commands").iterdir(),
        *Path(".claude/skills").iterdir(),
    ]
    for path in authored:
        name = path.stem if path.suffix == ".md" else path.name
        assert not name.startswith("workflow-") or name == "workflow-upgrade", path
        assert name.removeprefix("ds-") not in LIFECYCLE_NAMES, path
    assert not Path(".pi/skills").exists()
    assert not Path("workflow/lifecycle_contract.json").exists()
    upstream = json.loads(Path("integrations/t3/upstream.lock.json").read_text(encoding="utf-8"))
    pinned = {skill for source in upstream["sources"] for skill in source["skills"]}
    assert len(pinned) == 13
    for path in Path(".agents/skills").iterdir():
        if path.name in pinned:
            continue
        assert path.name.removeprefix("ds-") not in LIFECYCLE_NAMES, path
        assert not path.name.startswith("workflow-"), path


def test_generated_inventory_ships_ds_safeguards_without_the_lifecycle_engine(
    tmp_path: Path,
) -> None:
    catalog = json.loads(Path("workflow/components.json").read_text(encoding="utf-8"))
    targets = {component["target"] for component in catalog["components"]}
    for kept in (
        ".opencode/commands/ds-repo-audit.md",
        ".opencode/commands/ds-output-contract.md",
        ".opencode/commands/publish-public.md",
        ".opencode/commands/ds-report.md",
        ".opencode/commands/astack-upgrade.md",
        ".opencode/skills/astack-upgrade/SKILL.md",
        ".opencode/skills/project-policy/SKILL.md",
        ".astack/workflow.py",
        ".astack/git_state.py",
        ".pi/settings.json",
    ):
        assert kept in targets, kept
    for retired in (
        ".astack/worktrees.py",
        ".astack/control_plane.py",
        ".astack/execution_authority.py",
        ".astack/landing_readiness.py",
        ".astack/merge_readiness.py",
        ".astack/pr_description.py",
        ".astack/lifecycle_contract.json",
        ".pi/skills/ds-adapter/SKILL.md",
    ):
        assert retired not in targets, retired

    generated = tmp_path / "generated"
    create_project(
        generated,
        project_name="Generated Inventory",
        package_name="generated_inventory",
        description="Validate the retired lifecycle surface.",
        lock=False,
    )
    assert not (generated / ".astack/worktrees.py").exists()
    assert not list((generated / ".opencode/skills").glob("ds-start*"))
    assert not (generated / ".pi/skills").exists()
    assert json.loads((generated / ".pi/settings.json").read_text()) == {
        "skills": ["../.opencode/skills"],
        "enableSkillCommands": True,
    }
