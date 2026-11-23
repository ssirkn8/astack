import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from wheel_test_support import installed_wheel

from astack import installation, scaffold
from astack.scaffold import create_project


def _create(tmp_path: Path, tools: list[str] | None = None) -> Path:
    target = tmp_path / "study"
    create_project(
        target,
        project_name="Coastal Study",
        package_name="coastal_study",
        description="Evaluate coastal exposure.",
        intended_use="Support exploratory coastal planning.",
        tools=tools,
        lock=False,
    )
    return target


def test_create_project_is_lean_and_runnable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _create(tmp_path)
    assert (target / "src/coastal_study/__init__.py").exists()
    assert (target / ".astack/workflow.py").exists()
    assert not (target / ".astack/worktrees.py").exists()
    assert (target / ".astack/git_state.py").read_bytes() == Path(
        "src/astack/git_state.py"
    ).read_bytes()
    for retired in ("ds-status", "ds-start", "ds-fix-pr"):
        assert not (target / f".opencode/commands/{retired}.md").exists()
        assert not (target / f".opencode/skills/{retired}/SKILL.md").exists()
    assert (target / ".opencode/commands/ds-repo-audit.md").exists()
    assert not (target / ".opencode/agents/ds-orchestrator.md").exists()
    assert not (target / ".opencode/commands/ds-init.md").exists()
    for path in Path(".opencode").glob("commands/workflow-*.md"):
        assert not (target / path).exists()
    for path in Path(".opencode").glob("skills/workflow-*/SKILL.md"):
        assert not (target / path).exists()
    assert not (target / "SKILLS.md").exists()
    assert not (target / "dvc.yaml").exists()
    assert not (target / "notebooks").exists()
    assert not (target / "reports/report.qmd").exists()
    assert (target / "docs/validation.md").exists()
    assert not (target / ".ruff_cache").exists()
    for relative in [
        "data/raw",
        "data/manual",
        "data/interim",
        "data/processed",
        "artifacts",
        "evidence",
        "reports/figures",
        "reports/tables",
        "scratch",
    ]:
        assert (target / relative).is_dir()
    assert "Support exploratory coastal planning." in (target / "README.md").read_text(
        encoding="utf-8"
    )
    generated_smoke = (target / "tests/test_smoke.py").read_text(encoding="utf-8")
    assert 'PACKAGE_NAME = "coastal_study"' in generated_smoke
    assert "keyword.iskeyword(PACKAGE_NAME)" in generated_smoke
    assert "TBD" not in (target / "data/sources.yml").read_text(encoding="utf-8")
    data_readme = (target / "data/README.md").read_text(encoding="utf-8")
    assert "interim/<component>/" in data_readme
    assert "processed/<component>/" in data_readme
    project_state = tomllib.loads((target / ".astack/project.toml").read_text(encoding="utf-8"))
    assert project_state["outputs"] == {"layout": "component", "components": []}
    project_config = (target / ".astack/project.toml").read_text(encoding="utf-8")
    assert "private_history_paths" in project_config
    assert "private_history_safe_paths" in project_config
    assert "/data/manual" not in (target / ".gitignore").read_text(encoding="utf-8")
    project = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["build-system"]["build-backend"] == "uv_build"
    assert (target / ".python-version").read_text(encoding="utf-8") == "3.12\n"
    checks = (target / ".github/workflows/checks.yml").read_text(encoding="utf-8")
    setup_just = "extractions/setup-just@f8a3cce218d9f83db3a2ecd90e41ac3de6cdfd9b"
    assert setup_just in checks
    assert "fetch-depth: 0" in checks
    assert "- run: just ship-ready" in checks
    assert checks.count("- run: just ship-ready") == 1
    assert "validation_budget.py" not in checks
    assert "- run: uv lock --check" not in checks
    assert "- run: uv sync --locked" not in checks
    assert "ASTACK_AUDIT_BASE_SHA:" in checks
    assert "github.event.before" in checks
    assert checks.index(setup_just) < checks.index("- run: just ship-ready")
    manifest = json.loads((target / ".astack/manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow_version"] == scaffold.workflow_version()
    assert manifest["profile"] == "data-science"
    assert manifest["source"]["kind"] == "generated"
    managed_paths = {component["path"] for component in manifest["managed_components"].values()}
    assert "AGENTS.md" not in managed_paths
    assert "justfile" not in managed_paths
    assert all(
        path == ".astack/version"
        or path.startswith(".astack/")
        or path.startswith(".opencode/")
        or path.startswith(".pi/")
        or path.startswith(".agents/skills/")
        or path.startswith(".claude/skills/")
        for path in managed_paths
    )
    assert "quarto-dev/quarto-actions/setup@" not in checks
    generated_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [target / "AGENTS.md", target / "README.md"]
        + sorted((target / ".opencode").rglob("*.md"))
    ).casefold()
    assert "spatial" not in generated_text
    assert "sds" not in generated_text


@pytest.mark.integration
def test_generated_ship_ready_cannot_skip_a_failing_integration_test(tmp_path: Path) -> None:
    target = tmp_path / "integration-gate"
    create_project(
        target,
        project_name="Integration Gate",
        package_name="integration_gate",
        description="Verify the generated complete gate.",
        intended_use="Test generated shipping validation.",
        lock=True,
    )
    failing_test = target / "tests/test_generated_integration_failure.py"
    failing_test.write_text(
        "import pytest\n\n"
        "@pytest.mark.integration\n"
        "def test_integration_gate_is_required() -> None:\n"
        "    raise AssertionError('integration gate must run')\n",
        encoding="utf-8",
    )
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "generated integration fixture",
        ],
    ):
        subprocess.run(args, cwd=target, check=True, capture_output=True)

    result = subprocess.run(
        ["just", "ship-ready"], cwd=target, check=False, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "just integration" in result.stdout


@pytest.mark.integration
def test_generated_project_allows_an_empty_integration_tier(tmp_path: Path) -> None:
    target = _create(tmp_path)
    subprocess.run(["uv", "lock"], cwd=target, check=True, capture_output=True)

    result = subprocess.run(
        ["just", "integration"], cwd=target, check=False, capture_output=True, text=True
    )

    assert result.returncode == 0


def test_tools_are_opt_in(tmp_path: Path) -> None:
    target = _create(tmp_path, ["dvc", "marimo", "quarto"])
    pipeline = (target / "dvc.yaml").read_text(encoding="utf-8")
    assert "stages: {}" in pipeline
    assert "src/coastal_study" in pipeline
    assert "cache: false" in pipeline
    assert (target / ".dvc/config").exists()
    assert (target / ".dvcignore").exists()
    assert not (target / "notebooks/explore.py").exists()
    assert (target / "reports/report.qmd").exists()
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert '"dvc>=3.0"' in pyproject
    assert '"marimo>=0.20,<1"' in pyproject
    justfile = (target / "justfile").read_text(encoding="utf-8")
    assert "dvc-status:" in justfile
    assert "dvc-cache-status:" in justfile
    assert "dvc-cache-gc:" in justfile
    assert "reproduce *args:" in justfile
    assert "acquire:" in justfile
    assert "report:" in justfile
    assert "\nnotebook:" not in justfile
    assert "review:" in justfile
    assert "review-check:" in justfile
    assert (target / "notebooks/review.py").exists()
    checks = (target / ".github/workflows/checks.yml").read_text(encoding="utf-8")
    quarto_setup = "quarto-dev/quarto-actions/setup@8a96df13519ee81fd526f2dfca5962811136661b"
    assert quarto_setup in checks
    assert checks.index(quarto_setup) < checks.index("just ship-ready")


@pytest.mark.parametrize(
    "tools",
    [
        [],
        ["dvc"],
        ["marimo"],
        ["quarto"],
        ["dvc", "marimo"],
        ["dvc", "quarto"],
        ["marimo", "quarto"],
        ["dvc", "marimo", "quarto"],
    ],
)
def test_tool_matrix_has_explicit_project_state(tmp_path: Path, tools: list[str]) -> None:
    target = _create(tmp_path, tools)
    state = tomllib.loads((target / ".astack/project.toml").read_text(encoding="utf-8"))

    assert state["tools"] == tools
    assert state["profile"] == "data-science"
    assert state["capabilities"] == ["scientific-review", "scientific-validation"]
    assert (target / ".dvc/config").exists() == ("dvc" in tools)
    assert (target / "notebooks/review.py").exists() == ("marimo" in tools)
    assert (target / "reports/report.qmd").exists() == ("quarto" in tools)


@pytest.mark.parametrize("arguments", [["--help"], ["audit", "--help"]])
def test_generated_runtime_is_offline_and_package_independent(
    tmp_path: Path, arguments: list[str]
) -> None:
    target = _create(tmp_path)
    assert (target / ".astack/git_state.py").exists()

    isolated_environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, ".astack/workflow.py", *arguments],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
        env=isolated_environment,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_project_name_must_be_safe_python_identifier(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Python identifier"):
        create_project(
            tmp_path / "bad",
            project_name="Bad",
            package_name="Bad-Package",
            description="Bad package name.",
            lock=False,
        )


@pytest.mark.parametrize("package_name", ["class", "for", "import"])
def test_package_name_rejects_python_keywords(tmp_path: Path, package_name: str) -> None:
    with pytest.raises(ValueError, match="Python identifier"):
        create_project(
            tmp_path / package_name,
            project_name="Keyword package",
            package_name=package_name,
            description="Must not generate invalid Python.",
            lock=False,
        )


def test_description_is_toml_escaped(tmp_path: Path) -> None:
    target = tmp_path / "quoted"
    create_project(
        target,
        project_name="Quoted Study",
        package_name="quoted_study",
        description='A "quoted" description with a carriage return.\rNext.',
        intended_use="Internal exploration.",
        lock=False,
    )
    project = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))
    assert (
        project["project"]["description"] == 'A "quoted" description with a carriage return.\rNext.'
    )


def test_default_creation_generates_lock(tmp_path: Path, monkeypatch) -> None:
    called: list[Path] = []

    def fake_lock(target: Path) -> None:
        called.append(target)
        (target / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    monkeypatch.setattr(scaffold, "_generate_lock", fake_lock)
    target = tmp_path / "locked"
    create_project(
        target,
        project_name="Locked Study",
        package_name="locked_study",
        description="A locked project.",
        intended_use="Internal exploration.",
    )
    assert called == [target]
    assert (target / "uv.lock").exists()


def test_failed_lock_generation_preserves_new_target(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "lock-failure"

    def fail_lock(path: Path) -> None:
        raise RuntimeError("injected lock failure")

    monkeypatch.setattr(scaffold, "_generate_lock", fail_lock)
    with pytest.raises(RuntimeError, match="injected"):
        create_project(
            target,
            project_name="Lock Failure",
            package_name="lock_failure",
            description="Exercise cleanup.\rStill valid TOML when escaped.",
            intended_use="Test failure cleanup.",
        )
    assert (target / "README.md").exists()
    assert (target / ".astack/manifest.json").exists()
    assert (target / ".opencode/commands/ds-repo-audit.md").exists()


def test_template_has_no_authored_opencode_mirror_or_operational_diaries() -> None:
    template = Path("templates/project")
    assert not list((template / ".opencode").rglob("*.md"))
    for relative in [
        "SKILLS.md",
        "docs/assumptions.md",
        "docs/handoff.md",
        "docs/inbox.md",
        "docs/lessons.md",
        "docs/log.md",
        "docs/risks.md",
        "docs/todo.md",
    ]:
        assert not (template / relative).exists()


@pytest.mark.integration
def test_installed_wheel_scaffolds_and_upgrades_with_packaged_assets(tmp_path: Path) -> None:
    python, cli, source = installed_wheel(tmp_path)

    # The installed wheel carries the pinned upstream skills, their lock and notices.
    verified = subprocess.run(
        [str(cli), "skills"], cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert json.loads(verified.stdout)["verified_files"] == 24
    skill_destination = tmp_path / "wheel-skills"
    preview = subprocess.run(
        [str(cli), "skills", "--dest", str(skill_destination)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(preview.stdout)["applied"] is False
    assert not skill_destination.exists()
    subprocess.run(
        [str(cli), "skills", "--dest", str(skill_destination), "--apply"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert (skill_destination / "full-stop/SKILL.md").read_bytes() == (
        source / ".agents/skills/full-stop/SKILL.md"
    ).read_bytes()
    assert (skill_destination / ".astack-upstream-skills.json").is_file()
    assert not (skill_destination / "ds-repo-audit").exists()
    refused = subprocess.run(
        [str(cli), "skills", "--apply"], cwd=tmp_path, capture_output=True, text=True
    )
    assert refused.returncode != 0

    generated = tmp_path / "installed-project"
    subprocess.run(
        [
            str(cli),
            "new",
            str(generated),
            "--name",
            "Installed Project",
            "--package",
            "installed_project",
            "--description",
            "Validate packaged scaffold assets.",
            "--intended-use",
            "Integration testing.",
        ],
        check=True,
        capture_output=True,
    )
    manifest = json.loads((generated / ".astack/manifest.json").read_text(encoding="utf-8"))
    assert (generated / ".astack/workflow.py").exists()
    assert not (generated / ".astack/worktrees.py").exists()
    assert (generated / ".astack/git_state.py").exists()
    assert manifest["profile"] == "data-science"
    assert (generated / ".opencode/commands/ds-repo-audit.md").exists()
    assert not (generated / ".opencode/commands/ds-resume.md").exists()
    assert not (generated / ".opencode/commands/ds-direct.md").exists()
    assert not (generated / ".opencode/agents/ds-orchestrator.md").exists()
    runtime_help = subprocess.run(
        [str(python), ".astack/workflow.py", "work-unit", "--help"],
        cwd=generated,
        check=False,
        capture_output=True,
        text=True,
    )
    assert runtime_help.returncode != 0
    assert "invalid choice" in runtime_help.stderr
    validate_help = subprocess.run(
        [str(python), ".astack/workflow.py", "validate", "--help"],
        cwd=generated,
        check=False,
        capture_output=True,
        text=True,
    )
    assert validate_help.returncode == 0, validate_help.stderr
    assert "verify" in validate_help.stdout
    assert "release" in validate_help.stdout
    assert "AGENTS.md" not in {
        component["path"] for component in manifest["managed_components"].values()
    }
    assert 'PACKAGE_NAME = "installed_project"' in (generated / "tests/test_smoke.py").read_text(
        encoding="utf-8"
    )

    # An explicitly synthetic older install, upgraded by the same installer `astack install` uses.
    _commit_all(generated)
    owned = _make_older(generated, manifest)
    preview = subprocess.run(
        [str(cli), "install", str(generated), "--candidate"],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(preview.stdout)
    assert not plan["conflicts"]
    subprocess.run(
        [
            str(cli),
            "install",
            str(generated),
            "--candidate",
            "--apply",
            "--approve-plan",
            plan["plan_digest"],
        ],
        check=True,
        capture_output=True,
    )
    assert (generated / ".astack/version").read_text() == f"v{scaffold.workflow_version()}\n"
    assert all(path.read_bytes() == content for path, content in owned.items())
    validation = subprocess.run(
        [str(python), "-S", ".astack/workflow.py", "validate", "check"],
        cwd=generated,
        # The generated project has one smoke test and runs inside the maintainer test suite.
        env={**os.environ, "PYTEST_XDIST_AUTO_NUM_WORKERS": "1"},
        capture_output=True,
        text=True,
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr
    result = json.loads(validation.stdout)
    assert result["status"] == "passed"
    assert result["commands"] == [["just", "check"], ["just", "review-check"]]


def _commit_all(root: Path) -> None:
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-qb", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
        + ["commit", "-qm", "fixture", "--allow-empty"],
        cwd=root,
        check=True,
    )


def _make_older(target: Path, manifest: dict) -> dict[Path, bytes]:
    """Record a synthetic older astack install, plus owner files an upgrade must keep."""
    owned = {
        target / "owner-notes.md": b"owner notes\n",
        target / "reports/figures/retained.txt": b"scientific output\n",
    }
    for path, content in owned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    marker = target / ".astack/version"
    marker.write_text("v0.0.1\n")
    manifest["workflow_version"] = "0.0.1"
    manifest["managed_components"]["runtime.version"]["sha256"] = hashlib.sha256(
        marker.read_bytes()
    ).hexdigest()
    (target / ".astack/manifest.json").write_text(json.dumps(manifest))
    _commit_all(target)
    return owned


def _manifest(target: Path) -> dict:
    value: dict = json.loads((target / ".astack/manifest.json").read_text(encoding="utf-8"))
    return value


def test_generated_project_is_already_what_install_would_write(tmp_path: Path) -> None:
    target = _create(tmp_path)
    _commit_all(target)
    plan = installation.install_project(target, candidate=True)
    assert plan["profile"] == "data-science"
    assert not plan["conflicts"]
    changed = {a["path"] for a in plan["actions"] if a["action"] != "unchanged"}
    # Only the recorded source changes, from `generated` to this candidate.
    assert changed == {installation.MANIFEST}


def test_install_upgrades_a_generated_project_and_keeps_owner_files(tmp_path: Path) -> None:
    target = _create(tmp_path, ["dvc"])
    _commit_all(target)
    owned = _make_older(target, _manifest(target))
    owner_files = {
        path: (target / path).read_bytes()
        for path in ("README.md", "AGENTS.md", "justfile", "pyproject.toml", ".astack/project.toml")
    }
    plan = installation.install_project(target, candidate=True)
    assert not plan["conflicts"]
    assert {a["path"] for a in plan["actions"] if a["action"] == "update"} == {
        ".astack/version",
        installation.MANIFEST,
    }
    installation.install_project(
        target, candidate=True, apply=True, approve_plan=plan["plan_digest"]
    )
    assert _manifest(target)["workflow_version"] == scaffold.workflow_version()
    assert all(path.read_bytes() == content for path, content in owned.items())
    assert all((target / path).read_bytes() == data for path, data in owner_files.items())


def test_install_preserves_an_edited_managed_skill_in_a_generated_project(
    tmp_path: Path,
) -> None:
    target = _create(tmp_path)
    skill = target / ".opencode/skills/ds-repo-audit/SKILL.md"
    skill.write_text(skill.read_text() + "\nOwner review policy.\n")
    _commit_all(target)
    before = skill.read_bytes()
    plan = installation.install_project(target, candidate=True)
    assert ".opencode/skills/ds-repo-audit/SKILL.md" in plan["conflicts"]
    with pytest.raises(ValueError, match="conflicts"):
        installation.install_project(
            target, candidate=True, apply=True, approve_plan=plan["plan_digest"]
        )
    assert skill.read_bytes() == before


def test_install_refuses_a_generated_project_recorded_by_a_newer_astack(tmp_path: Path) -> None:
    target = _create(tmp_path)
    manifest = _manifest(target)
    manifest["workflow_version"] = "99.0.0"
    (target / ".astack/manifest.json").write_text(json.dumps(manifest))
    _commit_all(target)
    plan = installation.install_project(target, candidate=True)
    assert any("newer astack" in conflict for conflict in plan["conflicts"])
