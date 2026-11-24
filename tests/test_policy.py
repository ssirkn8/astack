import tomllib
from pathlib import Path


def _recipe_commands(path: Path, target: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{target}:") + 1
    commands: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith((" ", "\t")):
            break
        if line.strip():
            commands.append(line.strip())
    return commands


def _nested_virtualenvs(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob(".venv") if path.is_dir())


def test_pyproject_is_single_version_authority() -> None:
    version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert Path(".astack/version").read_text(encoding="utf-8").strip() == f"v{version}"
    assert not Path("VERSION").exists()
    assert not Path("params.yaml").exists()


def test_current_distribution_has_no_sds_aliases() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "astack"
    scripts = project["scripts"]
    assert set(scripts) == {"astack"}
    assert not Path(".opencode/commands/ds-init.md").exists()
    assert not Path(".opencode/skills/ds-project-ceremony").exists()


def test_public_release_forbids_branch_sanitization() -> None:
    skill = Path(".opencode/skills/publish-public/SKILL.md").read_text(encoding="utf-8")
    policy = Path("docs/publishing.md").read_text(encoding="utf-8")
    assert "Never use a branch" in skill
    assert "A branch is not a privacy" in policy
    assert "allowlist" in skill
    assert "allowed_outputs" in skill
    assert "allowed_outputs" in policy


def test_maintainer_ci_runs_full_ship_readiness() -> None:
    workflow = Path(".github/workflows/checks.yml").read_text(encoding="utf-8")
    assert "- run: just ship-ready" in workflow
    assert workflow.count("- run: just ship-ready") == 1
    assert "validation_budget.py" not in workflow
    assert "- run: uv lock --check" not in workflow
    assert "- run: uv sync --locked" not in workflow


def test_check_recipes_exactly_sync_before_running_tools() -> None:
    for path in [Path("justfile"), Path("templates/project/justfile")]:
        commands = _recipe_commands(path, "check")
        assert commands[:2] == ["uv lock --check", "uv sync --locked"]
        assert all(
            commands.index("uv sync --locked") < index
            for index, command in enumerate(commands)
            if command.startswith("uv run ")
        )


def test_authored_templates_contain_no_nested_virtualenvs() -> None:
    assert _nested_virtualenvs(Path("templates")) == []


def test_template_virtualenv_policy_detects_ignored_directory(tmp_path: Path) -> None:
    hidden_environment = tmp_path / "templates/project/.venv"
    hidden_environment.mkdir(parents=True)

    assert _nested_virtualenvs(tmp_path / "templates") == [hidden_environment]


def test_scientific_audit_requires_honest_evidence_states() -> None:
    skill = Path(".opencode/skills/ds-repo-audit/SKILL.md").read_text(encoding="utf-8")
    for expected in [
        "Scientific credibility",
        "Missing evidence",
        "Needs domain review",
        "representativeness",
        "validation splits appropriate",
        "Never certify scientific validity",
    ]:
        assert expected in skill


def test_output_contract_guidance_preserves_scientific_judgment_and_priorities() -> None:
    skill = Path(".opencode/skills/ds-output-contract/SKILL.md").read_text(encoding="utf-8")
    for expected in [
        "research question",
        "publication-style outputs",
        "bounded literature reconnaissance",
        "ask the human to confirm or amend",
        "Deadline pressure never permits required",
        "follow-up issues",
        "without replacing scientific judgment",
        "rules engine",
    ]:
        assert expected in skill


def test_generated_project_guidance_distinguishes_required_and_optional_outputs() -> None:
    agents = Path("templates/project/AGENTS.md").read_text(encoding="utf-8")
    readme = Path("templates/project/README.md").read_text(encoding="utf-8")

    assert "Scientific Output Contract" in agents
    assert "optional diagnostics" in agents
    assert "Deadline pressure never permits required validation" in agents
    assert "human-facing figures/maps/tables" in readme
    assert "New ideas normally become follow-up work" in readme


def test_validation_tiers_remain_progressive_and_optional() -> None:
    guidance = Path("docs/checks-and-audit.md").read_text(encoding="utf-8")
    template = Path("templates/project/docs/validation.md").read_text(encoding="utf-8")

    for text in [guidance, template]:
        assert "CHECK" in text
        assert "VERIFY" in text
        assert "RELEASE" in text
        assert "Missing, skipped" in text or "unconfigured, skipped" in text
    assert "release:" in Path("templates/project/justfile").read_text(encoding="utf-8")


def test_marimo_review_surface_is_thin_and_machine_evidence_remains_primary() -> None:
    notebook = Path("templates/capabilities/marimo/notebooks/review.py").read_text(encoding="utf-8")

    # All logic lives in the managed runtime, so upgrades reach it without touching the notebook.
    assert "workflow.review_status(root)" in notebook
    # A sign-off records only the code shown on the page.
    assert "workflow.record_review(root, unit, checksum)" in notebook
    assert "data/raw" not in notebook
    assert "pandas" not in notebook
    assert "marimo.App" in notebook


def test_default_ci_does_not_run_expensive_validation_tiers() -> None:
    workflow = Path("templates/project/.github/workflows/checks.yml").read_text(encoding="utf-8")

    assert "just verify" not in workflow
    assert "just release" not in workflow
