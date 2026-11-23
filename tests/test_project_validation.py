from __future__ import annotations

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _append_validation,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    _set_capabilities,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
    sys,  # noqa: F401
)


def test_validation_tiers_are_optional_for_existing_projects(tmp_path: Path) -> None:
    target = _project(tmp_path)

    result = project_tool.audit(target)

    assert not any(item["check"] == "validation-config" for item in result["findings"])
    with pytest.raises(RuntimeError, match="not configured"):
        project_tool.run_validation(target, "verify")


def test_validation_config_rejects_non_command_arrays(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "[validation]\n", '[validation]\nverify = ["uv", "run", "pytest"]\n', 1
        ),
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "validation-config")
    assert finding["priority"] == "P1"
    with pytest.raises(RuntimeError, match="Invalid validation configuration"):
        project_tool.run_validation(target, "verify")


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ([sys.executable, "-c", "raise SystemExit(2)"], "validation failed"),
        (["missing-validation-executable"], "is unavailable"),
    ],
)
def test_validation_failures_are_not_passes(
    tmp_path: Path, command: list[str], message: str
) -> None:
    target = _project(tmp_path)
    _append_validation(target, "verify", [command])

    with pytest.raises(RuntimeError, match=message):
        project_tool.run_validation(target, "verify")


@pytest.mark.parametrize(
    ("path", "lifecycle", "component"),
    [
        ("data/raw/source.csv", "raw", None),
        ("data/manual/corrections.csv", "manual", None),
        ("data/interim/exposure/joined.parquet", "interim", "exposure"),
        ("data/processed/model/final.parquet", "processed", "model"),
        ("artifacts/model/diagnostic.tif", "artifact", "model"),
        ("evidence/model/qa.json", "evidence", "model"),
        ("reports/figures/model/map.png", "figure", "model"),
        ("reports/tables/model/results.csv", "table", "model"),
        ("scratch/notes.txt", "scratch", None),
    ],
)
def test_output_path_classification(path: str, lifecycle: str, component: str | None) -> None:
    result = project_tool._classify_output_path(path)

    assert result == {"lifecycle": lifecycle, "component": component, "problem": None}


def test_component_layout_audit_flags_legacy_unscoped_and_undeclared_outputs(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("components = []", 'components = ["model"]'),
        encoding="utf-8",
    )
    (target / "reports/qa").mkdir(parents=True)
    (target / "reports/qa/run.json").write_text("{}\n", encoding="utf-8")
    (target / "artifacts/result.json").write_text("{}\n", encoding="utf-8")
    (target / "evidence/unknown").mkdir(parents=True)
    (target / "evidence/unknown/check.json").write_text("{}\n", encoding="utf-8")

    findings = project_tool.audit(target)["findings"]
    checks = {item["check"] for item in findings}

    assert "legacy-output-path" in checks
    assert "component-output-path" in checks
    assert "undeclared-component" in checks
    assert all(
        item["priority"] == "P1"
        for item in findings
        if item["check"]
        in checks
        & {
            "legacy-output-path",
            "component-output-path",
            "undeclared-component",
        }
    )


@pytest.mark.parametrize(
    "declared",
    [
        "evidence/scientific-outputs.yml",
        "./evidence/scientific-outputs.yml",
        "evidence//scientific-outputs.yml",
    ],
)
def test_declared_scientific_output_evidence_file_is_not_a_misplaced_output(
    tmp_path: Path, declared: str
) -> None:
    # docs/scientific-outputs.md puts the registry's evidence file at evidence/<name>.yml.
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[scientific_outputs]\npath = "docs/scientific-outputs.yml"\n'
        + f'evidence_path = "{declared}"\n',
        encoding="utf-8",
    )
    (target / "evidence").mkdir(exist_ok=True)
    (target / "evidence/scientific-outputs.yml").write_text("schema_version: 2\n", "utf-8")
    (target / "evidence/stray.yml").write_text("{}\n", encoding="utf-8")
    (target / "evidence/unknown").mkdir()
    (target / "evidence/unknown/check.json").write_text("{}\n", encoding="utf-8")

    findings = project_tool.audit(target)["findings"]
    flagged = {item["evidence"] for item in findings if item["check"] == "component-output-path"}

    assert "evidence/scientific-outputs.yml" not in flagged
    assert "evidence/stray.yml" in flagged  # Only the declared file is exempt.
    # The exemption must not disturb the declared-component check.
    assert any(item["check"] == "undeclared-component" for item in findings)


def test_component_layout_audit_rejects_machine_outputs_in_nested_reports(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("components = []", 'components = ["model"]'),
        encoding="utf-8",
    )
    paths = [
        "reports/figures/model/diagnostic.npy",
        "reports/tables/model/results.parquet",
    ]
    for relative in paths:
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"machine output")

    machine_outputs = [
        item
        for item in project_tool.audit(target)["findings"]
        if item["check"] == "machine-output-in-reports"
    ]

    assert {item["evidence"] for item in machine_outputs} == set(paths)
    assert all(item["priority"] == "P1" for item in machine_outputs)


def test_missing_ci_is_not_a_reproducibility_pass(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/checks.yml").unlink()
    result = project_tool.audit(target)
    assert result["verdicts"]["computational_reproducibility"] != "pass"


@pytest.mark.parametrize(
    "justfile",
    [
        (
            "# ship-ready:\n"
            "# uv run python .astack/workflow.py audit --run-tools --strict\n"
            "ship-ready:\n\t@echo not-an-audit\n"
        ),
        ("ship-ready: check\n\t# uv run python .astack/workflow.py audit --run-tools --strict\n"),
        "ship-ready:\n\tuv run python .astack/workflow.py audit --run-tools\n",
        (
            "ship-ready:\n"
            "\tuv run python .astack/workflow.py audit --format text --run-tools --strict || true\n"
        ),
        (
            "ship-ready:\n"
            "\tuv run python .astack/workflow.py audit --format text --run-tools --strict "
            "| tee audit.txt\n"
        ),
        (
            "ship-ready:\n"
            "\tuv run python .astack/workflow.py audit --format text --run-tools --strict; true\n"
        ),
        (
            "ship-ready:\n"
            "\tuv run python .astack/workflow.py audit --format text --run-tools --strict && true\n"
        ),
        (
            "ship-ready:\n"
            "\tuv run python .astack/workflow.py audit --format text --run-tools --strict $(true)\n"
        ),
        (
            "ship-ready:\n"
            "\tuv run python .astack/workflow.py audit --format text --run-tools --strict `true`\n"
        ),
    ],
)
def test_structure_audit_requires_direct_strict_ship_ready_recipe(
    tmp_path: Path, justfile: str
) -> None:
    target = _project(tmp_path)
    (target / "justfile").write_text(justfile, encoding="utf-8")

    result = project_tool.audit(target)

    assert any(item["check"] == "ship-ready" for item in result["findings"])


_STRICT = "uv run --locked python .astack/workflow.py audit --format text --run-tools --strict"


@pytest.mark.parametrize(
    "recipe",
    [
        # Projects created before ship-ready took an analysis keep their recipe.
        f"ship-ready:\n\t{_STRICT}\n",
        f'ship-ready analysis="":\n\t{_STRICT} --analysis {{{{quote(analysis)}}}}\n',
    ],
)
def test_ship_ready_recipe_may_take_an_analysis(recipe: str) -> None:
    assert project_tool._ship_ready_runs_strict_audit(recipe)


@pytest.mark.parametrize(
    "recipe",
    [
        # Free-form arguments could add `--ci` and turn the strict audit into limited CI.
        f"ship-ready *args:\n\t{_STRICT} {{{{args}}}}\n",
        f'ship-ready analysis="":\n\t{_STRICT} --analysis {{{{analysis}}}} --ci\n',
        # Taking an analysis but not passing it would silently audit the whole project.
        f'ship-ready analysis="":\n\t{_STRICT}\n',
        f"ship-ready:\n\t{_STRICT} --analysis {{{{quote(analysis)}}}}\n",
        # Shell quotes around quote() undo it, so a key like `" ; exit 0 #` could run code.
        f'ship-ready analysis="":\n\t{_STRICT} --analysis "{{{{quote(analysis)}}}}"\n',
        f"ship-ready analysis=\"\":\n\t{_STRICT} --analysis '{{{{quote(analysis)}}}}'\n",
        f'ship-ready analysis="":\n\t{_STRICT} --analysis {{{{analysis}}}}\n',
    ],
)
def test_ship_ready_recipe_rejects_free_form_arguments(recipe: str) -> None:
    assert not project_tool._ship_ready_runs_strict_audit(recipe)


def test_structure_audit_accepts_generated_strict_ship_ready_recipe(tmp_path: Path) -> None:
    target = _project(tmp_path)
    text = (target / "justfile").read_text(encoding="utf-8")
    assert project_tool._ship_ready_runs_strict_audit(text)
    result = project_tool.audit(target)
    assert not any(item["check"] == "ship-ready" for item in result["findings"])


@pytest.mark.parametrize(
    "workflow",
    [
        "on:\n  pull_request:\njobs:\n  check:\n    steps:\n      - run: just check\n",
        (
            "# - run: just ship-ready\n"
            "on:\n  pull_request:\njobs:\n  check:\n    steps:\n      - run: just check\n"
        ),
        (
            "on:\n  pull_request:\njobs:\n  check:\n    steps:\n"
            "      - if: false\n        run: just ship-ready\n"
        ),
        (
            "on:\n  pull_request:\njobs:\n  check:\n    steps:\n"
            "      - continue-on-error: true\n        run: just ship-ready\n"
        ),
        (
            "on:\n  pull_request:\njobs:\n  check:\n    continue-on-error: true\n"
            "    steps:\n      - run: just ship-ready\n"
        ),
    ],
)
def test_structure_audit_requires_active_ci_ship_ready_step(tmp_path: Path, workflow: str) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/checks.yml").write_text(workflow, encoding="utf-8")

    result = project_tool.audit(target)

    assert any(item["check"] == "ci-ship-ready" for item in result["findings"])


def test_structure_audit_accepts_active_ci_ship_ready_without_comment(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/checks.yml").write_text(
        "on:\n  pull_request:\njobs:\n  check:\n    steps:\n      - run: just ship-ready\n",
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert not any(item["check"] == "ci-ship-ready" for item in result["findings"])


def test_structure_audit_does_not_use_unrelated_scheduled_ship_ready_workflow(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/scheduled.yml").write_text(
        (
            "on:\n  schedule:\n    - cron: '0 0 * * *'\n"
            "jobs:\n  check:\n    steps:\n      - run: just ship-ready\n"
        ),
        encoding="utf-8",
    )
    (target / ".github/workflows/pull-request.yml").write_text(
        "on:\n  pull_request:\njobs:\n  check:\n    steps:\n      - run: just check\n",
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "ci-ship-ready" for item in result["findings"])
    finding = next(item for item in result["findings"] if item["check"] == "ci-ship-ready")
    assert "pull-request.yml" in finding["evidence"]


@pytest.mark.parametrize(
    "jobs",
    [
        "jobs:\n  check:\n    steps: []\n",
        "jobs:\n  check:\n    steps:\n      - uses: actions/checkout@v4\n",
    ],
)
def test_structure_audit_requires_applicable_validation_workflow(tmp_path: Path, jobs: str) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/checks.yml").write_text(
        "on:\n  pull_request:\n" + jobs,
        encoding="utf-8",
    )
    (target / ".github/workflows/scheduled.yml").write_text(
        (
            "on:\n  schedule:\n    - cron: '0 0 * * *'\n"
            "jobs:\n  check:\n    steps:\n      - run: just ship-ready\n"
        ),
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "ci-ship-ready" for item in result["findings"])


def test_structure_audit_requires_ship_ready_for_deployment_workflow(
    tmp_path: Path,
) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/deploy.yml").write_text(
        ("on:\n  push:\njobs:\n  deploy:\n    steps:\n      - run: just deploy\n"),
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "ci-ship-ready")
    assert "deploy.yml" in finding["evidence"]


@pytest.mark.parametrize(
    "workflow",
    [
        ("on:\n  pull_request_target:\njobs:\n  check:\n    steps:\n      - run: just check\n"),
        "on: [pull_request]\njobs:\n  check:\n    steps:\n      - run: make verify\n",
    ],
)
def test_structure_audit_classifies_additional_validation_forms(
    tmp_path: Path, workflow: str
) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/checks.yml").unlink()
    (target / ".github/workflows/custom.yml").write_text(workflow, encoding="utf-8")

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "ci-ship-ready")
    assert "custom.yml" in finding["evidence"]


def test_structure_audit_ignores_run_keys_outside_job_steps(tmp_path: Path) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/checks.yml").write_text(
        (
            "on:\n  pull_request:\nenv:\n  run: just ship-ready\n"
            "jobs:\n  check:\n    steps:\n      - uses: actions/checkout@v4\n"
        ),
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    assert any(item["check"] == "ci-ship-ready" for item in result["findings"])


@pytest.mark.parametrize(
    "jobs",
    [
        "jobs:\n  custom:\n    steps:\n      - run: uv run --locked pytest\n",
        "jobs:\n  custom:\n    uses: owner/repository/.github/workflows/check.yml@main\n",
    ],
)
def test_every_relevant_workflow_requires_the_strict_gate(tmp_path: Path, jobs: str) -> None:
    target = _project(tmp_path)
    (target / ".github/workflows/custom.yml").write_text(
        "on: pull_request\n" + jobs,
        encoding="utf-8",
    )

    result = project_tool.audit(target)

    finding = next(item for item in result["findings"] if item["check"] == "ci-ship-ready")
    assert "custom.yml" in finding["evidence"]


def test_strict_readiness_rejects_failed_release_verdict() -> None:
    result = {
        "verdicts": {
            "workflow_conformance": "pass",
            "computational_reproducibility": "pass",
            "release_readiness": "fail",
        }
    }
    assert not project_tool._strict_ready(result)


def test_skipped_configured_tool_is_not_a_pass(tmp_path: Path) -> None:
    target = _project(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    (target / "dvc.yaml").write_text("stages: {}\n", encoding="utf-8")
    (target / ".dvc").mkdir()
    (target / ".dvc/config").write_text("[core]\n", encoding="utf-8")
    _set_capabilities(target, ["dvc"])
    subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "baseline",
        ],
        cwd=target,
        check=True,
        capture_output=True,
    )
    result = project_tool.audit(target)
    assert result["verdicts"]["computational_reproducibility"] == "not-assessed"


def test_run_tools_checks_software_and_optional_capabilities(tmp_path: Path, monkeypatch) -> None:
    target = _project(tmp_path)
    (target / "dvc.yaml").write_text("stages: {}\n", encoding="utf-8")
    (target / ".dvc").mkdir()
    (target / ".dvc/config").write_text("[core]\n", encoding="utf-8")
    (target / "reports/report.qmd").parent.mkdir(parents=True, exist_ok=True)
    (target / "reports/report.qmd").write_text("# Report\n", encoding="utf-8")
    (target / "notebooks/analysis.py").parent.mkdir(parents=True, exist_ok=True)
    (target / "notebooks/analysis.py").write_text("print('ok')\n", encoding="utf-8")
    _set_capabilities(target, ["dvc", "marimo", "quarto"])
    _git_baseline(target)
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(tmp_path / "dvc-cache"))
    monkeypatch.delenv("DVC_SITE_CACHE_DIR", raising=False)
    calls: list[list[str]] = []

    def fake_run(
        args: list[str], *, cwd: Path, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.audit(target, run_tools=True)
    assert ["just", "check"] in calls
    assert ["just", "integration"] in calls
    assert ["uv", "run", "--locked", "dvc", "status"] in calls
    assert ["uv", "run", "--locked", "dvc", "repro", "--dry", "--allow-missing"] in calls
    assert ["quarto", "render", "reports/report.qmd"] in calls
    assert ["uv", "run", "--locked", "marimo", "check", "notebooks/analysis.py"] in calls
    session = result["validation_session"]
    assert session["invocation_counts"]["check"] == 1
    assert session["invocation_counts"]["integration"] == 1
    assert session["invocation_counts"]["strict-audit"] == 1
    assert session["configured_capabilities"] == ["dvc", "marimo", "quarto"]
