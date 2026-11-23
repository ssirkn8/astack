"""The review notebook's data and its one write, the per-step sign-off record."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from project_tool_test_support import Path, _git_baseline, _project, project_tool


def _commit(target: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Reviewer", "-c", "user.email=r@e", "commit", "-qm", message],
        cwd=target,
        check=True,
        capture_output=True,
    )


def _command_project(tmp_path: Path) -> Path:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text("print('pipeline')\n", encoding="utf-8")
    _git_baseline(target)
    subprocess.run(["git", "config", "user.name", "Reviewer"], cwd=target, check=True)
    return target


def _rows(target: Path) -> dict[str, dict[str, Any]]:
    """Code review rows only, without the audit the full review status runs."""
    rows, _ = project_tool._code_review(target, project_tool._project_config(target))
    return {row["unit"]: row for row in rows}


def test_a_review_holds_until_the_reviewed_code_changes(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    rows = _rows(target)
    assert rows["analysis"]["status"] == "unreviewed"
    assert "scripts/pipeline.py" in rows["analysis"]["files"]

    record = project_tool.record_review(target, "analysis")
    assert record["reviewer"] == "Reviewer"
    assert len(record["commit"]) == 40
    saved = json.loads((target / ".astack/reviews/analysis.json").read_text(encoding="utf-8"))
    assert saved["code_checksum"] == record["code_checksum"]
    assert _rows(target)["analysis"]["status"] == "reviewed"

    (target / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    _commit(target, "lock")
    assert _rows(target)["analysis"]["status"] == "reviewed"

    (target / "scripts/pipeline.py").write_text("print('changed')\n", encoding="utf-8")
    _commit(target, "code")
    assert _rows(target)["analysis"]["status"] == "changed-since-review"


def test_release_requires_every_component_with_code_reviewed(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    assert project_tool.pending_reviews(target) == ["analysis: unreviewed"]
    project_tool.record_review(target, "analysis")
    assert project_tool.pending_reviews(target) == []


def test_an_unknown_step_cannot_be_signed_off(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    with pytest.raises(RuntimeError, match="Unknown step"):
        project_tool.record_review(target, "nope")


def test_a_dvc_steps_code_is_its_non_data_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("tools = []", 'tools = ["dvc"]')
        + '\n[reproduction]\nengine = "dvc"\n',
        encoding="utf-8",
    )
    for relative in ("scripts/train.py", "src/pkg/core.py", "config.yaml", "data/raw/in.csv"):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (target / "src/pkg/__pycache__").mkdir()
    (target / "src/pkg/__pycache__/core.cpython-312.pyc").write_bytes(b"x")
    stage = {
        "name": "train",
        "deps": [
            {"path": "scripts/train.py"},
            {"path": "src/pkg"},
            {"path": "config.yaml"},
            {"path": "data/raw"},
            {"path": "evidence/other/features.json"},
            {"path": "uv.lock"},
            {"path": "params.yaml", "params": {"train.rate": 2}, "params_missing": []},
        ],
        "outs": [{"path": "evidence/model/metrics.json"}],
    }
    monkeypatch.setattr(
        project_tool,
        "_dvc_pipeline",
        lambda root, targets=None, read_only=False, stages_only=False: (
            {"status": {}, "stages": [stage]},
            "",
        ),
    )
    units, problem = project_tool._review_units(target, project_tool._project_config(target))
    assert problem is None
    assert units["train"]["files"] == ["config.yaml", "scripts/train.py", "src/pkg/core.py"]
    assert units["train"]["params"] == {"train": {"params.yaml:train.rate": 2}}
    assert units["train"]["unknown"] is None


def test_review_status_shows_only_the_persons_work(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    status = project_tool.review_status(target)
    assert status["header"]["commit"]
    assert status["to_sign_off"] == 1
    assert status["last_review"] is None
    assert {"results", "before_publication", "attention"}.isdisjoint(status)
    project_tool.record_review(target, "analysis")
    assert project_tool.review_status(target)["to_sign_off"] == 0


def test_review_status_cli_fails_release_until_reviewed(tmp_path: Path) -> None:
    import sys

    target = _command_project(tmp_path)
    command = [sys.executable, ".astack/workflow.py", "review-status", "--require-reviewed"]
    result = subprocess.run(command, cwd=target, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["pending"] == ["analysis: unreviewed"]
    project_tool.record_review(target, "analysis")
    result = subprocess.run(command, cwd=target, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_notebook_digests_match_their_templates() -> None:
    import hashlib

    notebooks = Path(__file__).resolve().parents[1] / "templates/capabilities/marimo/notebooks"

    def digest(name: str) -> str:
        return hashlib.sha256((notebooks / name).read_bytes()).hexdigest()

    assert digest("review.py") in project_tool.VIEWER_NOTEBOOK_SHA256["notebooks/review.py"]


def test_an_unchanged_prior_review_notebook_stays_a_viewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workflow.project_runtime import common

    previous = "a896fa013405169c7f5482959f96fe839492b008c56fa793dd02d69a1e04abbf"
    monkeypatch.setattr(common, "_sha256", lambda _path: previous)
    assert common._viewer_notebook(tmp_path, tmp_path / "notebooks/review.py")


def test_astack_notebooks_are_not_notebook_only_analysis(tmp_path: Path) -> None:
    from astack.scaffold import create_project

    target = tmp_path / "study"
    create_project(
        target,
        project_name="Study",
        package_name="study",
        description="d",
        intended_use="u",
        tools=["marimo"],
        lock=False,
    )
    (target / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "notebook-only-analysis" not in checks
    for name in ("review.py",):
        copy = target / "notebooks" / name
        original = copy.read_bytes()
        copy.write_bytes(original + b"# my analysis\n")
        checks = {item["check"] for item in project_tool.audit(target)["findings"]}
        assert "notebook-only-analysis" in checks
        copy.write_bytes(original)


def test_only_the_unchanged_review_notebook_needs_no_source_record(tmp_path: Path) -> None:
    from astack.scaffold import create_project

    target = tmp_path / "study"
    create_project(
        target,
        project_name="Study",
        package_name="study",
        description="d",
        intended_use="u",
        tools=["marimo"],
        lock=False,
    )
    (target / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    def source_coverage_flagged() -> bool:
        findings = project_tool.audit(target)["findings"]
        return "source-coverage" in {item["check"] for item in findings}

    assert not source_coverage_flagged()
    review = target / "notebooks/review.py"
    original = review.read_bytes()
    review.write_bytes(original + b"# my analysis\n")
    assert source_coverage_flagged()
    review.write_bytes(original)
    (target / "notebooks/analysis.py").write_text("import marimo\n", encoding="utf-8")
    assert source_coverage_flagged()


@pytest.mark.integration
@pytest.mark.skipif(
    __import__("importlib").util.find_spec("marimo") is None, reason="marimo is not installed"
)
def test_the_review_notebook_runs_on_an_empty_project(tmp_path: Path, monkeypatch: Any) -> None:
    import importlib.util

    from astack.scaffold import create_project

    target = tmp_path / "study"
    create_project(
        target,
        project_name="Study",
        package_name="study",
        description="d",
        intended_use="u",
        tools=["marimo"],
        lock=False,
    )
    (target / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git_baseline(target)
    monkeypatch.chdir(target)
    spec = importlib.util.spec_from_file_location("review_notebook", "notebooks/review.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, definitions = module.app.run()
    assert definitions["status"]["header"]["commit"]


def test_a_record_is_never_written_through_a_symlink(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    raw = target / "data/raw/important.csv"
    raw.write_text("precious\n", encoding="utf-8")
    (target / ".astack/reviews").mkdir()
    (target / ".astack/reviews/analysis.json").symlink_to(raw)
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool.record_review(target, "analysis")
    assert raw.read_text(encoding="utf-8") == "precious\n"


def test_a_tick_on_a_stale_page_is_refused(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    shown = _rows(target)["analysis"]["checksum"]
    (target / "scripts/pipeline.py").write_text("print('edited after loading')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed since this page loaded"):
        project_tool.record_review(target, "analysis", shown)
    assert not (target / ".astack/reviews/analysis.json").exists()


@pytest.mark.parametrize(
    "record",
    [
        {"unit": "other"},
        {"reviewer": ""},
        {"commit": "abc"},
        {"schema_version": 1},
        {"reviewed_at": None},
    ],
)
def test_an_invalid_record_does_not_count(tmp_path: Path, record: dict[str, Any]) -> None:
    target = _command_project(tmp_path)
    saved = project_tool.record_review(target, "analysis")
    path = target / ".astack/reviews/analysis.json"
    path.write_text(json.dumps(saved | record), encoding="utf-8")
    assert _rows(target)["analysis"]["status"] == "invalid-record"
    assert project_tool.pending_reviews(target) == ["analysis: invalid-record"]


def test_a_failed_git_listing_is_unknown_not_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _command_project(tmp_path)
    original = project_tool._run

    def run(args: list[str], **kwargs: Any) -> Any:
        if args[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal")
        return original(args, **kwargs)

    monkeypatch.setattr(project_tool, "_run", run)
    assert _rows(target)["analysis"]["status"] == "unknown"
    assert project_tool.pending_reviews(target) == ["analysis: unknown"]


def _dvc_review_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: dict[str, Any]
) -> Path:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("tools = []", 'tools = ["dvc"]')
        + '\n[reproduction]\nengine = "dvc"\n',
        encoding="utf-8",
    )
    (target / "scripts/train.py").write_text("x", encoding="utf-8")
    _git_baseline(target)
    subprocess.run(["git", "config", "user.name", "Reviewer"], cwd=target, check=True)
    state = {"stage": stage}

    def pipeline(
        root: Path, targets: Any = None, read_only: bool = False, stages_only: bool = False
    ) -> Any:
        assert read_only or project_tool._READ_ONLY["on"], "review must not configure DVC"
        return {"status": {}, "stages": [state["stage"]]}, ""

    monkeypatch.setattr(project_tool, "_dvc_pipeline", pipeline)
    monkeypatch.setattr(project_tool, "_state", state, raising=False)
    return target


def _stage(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "train",
        "cmd": "python scripts/train.py",
        "wdir": ".",
        "deps": [{"path": "scripts/train.py"}],
        "outs": [{"path": "evidence/model/metrics.json"}],
    } | overrides


def test_changing_a_stage_command_resets_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_review_project(tmp_path, monkeypatch, _stage())
    project_tool.record_review(target, "train")
    assert _rows(target)["train"]["status"] == "reviewed"
    project_tool._state["stage"] = _stage(cmd="python scripts/train.py --new-method")
    assert _rows(target)["train"]["status"] == "changed-since-review"


@pytest.mark.parametrize(
    ("stage", "why"),
    [
        (_stage(deps=[{"path": "scripts/missing.py"}]), "missing"),
        (_stage(deps=[{"path": "scripts"}]), "symlinked folder"),
    ],
)
def test_incomplete_code_is_unknown_and_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: dict[str, Any], why: str
) -> None:
    target = _dvc_review_project(tmp_path, monkeypatch, stage)
    linked = target / "src/linked"
    linked.mkdir(parents=True)
    (target / "scripts/pkg").mkdir()
    (target / "scripts/pkg/lib").symlink_to(linked, target_is_directory=True)
    row = _rows(target)["train"]
    assert row["status"] == "unknown"
    assert why in row["why"]
    assert project_tool.pending_reviews(target) == ["train: unknown"]


def test_undeclared_reads_make_the_code_set_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_review_project(tmp_path, monkeypatch, _stage())
    monkeypatch.setattr(
        project_tool,
        "dependency_checks",
        lambda root: {"train": {"undeclared_reads": ["src/pkg/helper.py"]}},
    )
    row = _rows(target)["train"]
    assert row["status"] == "unknown"
    assert "src/pkg/helper.py" in row["why"]


def test_viewing_writes_nothing_in_the_project(tmp_path: Path) -> None:
    target = _command_project(tmp_path)

    def listing() -> list[str]:
        # .git holds Git's own state and astack's digest cache, not project content.
        return sorted(
            str(path.relative_to(target))
            for path in target.rglob("*")
            if path.relative_to(target).parts[0] != ".git"
        )

    before = listing()
    project_tool.review_status(target)
    assert listing() == before


def test_changing_the_reproduction_command_resets_the_review(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'command = ["python", "scripts/pipeline.py"]',
            'command = ["python", "scripts/pipeline.py", "--fast"]',
        ),
        encoding="utf-8",
    )
    assert _rows(target)["analysis"]["status"] == "changed-since-review"


def test_a_deleted_code_file_is_unknown_and_cannot_be_reviewed(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/pipeline.py").unlink()
    row = _rows(target)["analysis"]
    assert row["status"] == "unknown"
    assert "scripts/pipeline.py" in row["why"]
    with pytest.raises(RuntimeError, match="cannot be reviewed"):
        project_tool.record_review(target, "analysis")


def test_concurrent_reviews_never_leave_an_empty_record(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    target = _command_project(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: project_tool.record_review(target, "analysis"), range(16)))
    saved = json.loads((target / ".astack/reviews/analysis.json").read_text(encoding="utf-8"))
    assert saved["unit"] == "analysis"
    assert not list((target / ".astack/reviews").glob("*.tmp"))


def test_non_ascii_code_file_names_are_reviewable(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/café.py").write_text("print('ok')\n", encoding="utf-8")
    _commit(target, "unicode")
    row = _rows(target)["analysis"]
    assert row["status"] == "unreviewed"
    assert "scripts/café.py" in row["files"]
    project_tool.record_review(target, "analysis")
    assert _rows(target)["analysis"]["status"] == "reviewed"


def test_a_re_review_shows_only_what_changed_since_the_reviewed_commit(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    assert _rows(target)["analysis"]["since_review"] == {
        "mode": "full",
        "why": "no earlier sign-off",
    }
    record = project_tool.record_review(target, "analysis")
    assert record["code_committed"] is True
    assert "scripts/pipeline.py" in record["files"]

    (target / "scripts/pipeline.py").write_text("print('changed')\n", encoding="utf-8")
    (target / "scripts/extra.py").write_text("print('new')\n", encoding="utf-8")
    _commit(target, "code")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "diff"
    assert since["since"] == record["commit"]
    assert "+print('changed')" in since["diff"]
    assert "-print('pipeline')" in since["diff"]
    assert since["files_added"] == ["scripts/extra.py"]
    assert "extra" not in since["diff"]
    assert since["truncated"] is False
    status = project_tool.review_status(target)
    assert status["last_review"]["commit"] == record["commit"]
    assert status["last_review"]["commits_since"] == 1
    assert status["to_sign_off"] == 1


@pytest.mark.integration
def test_a_forged_uncommitted_record_is_never_diffed(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    path = target / ".astack/reviews/analysis.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["code_committed"] = False
    path.write_text(json.dumps(saved), encoding="utf-8")
    (target / "scripts/pipeline.py").write_text("print('final')\n", encoding="utf-8")
    _commit(target, "code")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "full"
    assert "uncommitted" in since["why"]


@pytest.mark.integration
def test_a_review_from_another_history_is_never_diffed(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    path = target / ".astack/reviews/analysis.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["commit"] = "0" * 40
    path.write_text(json.dumps(saved), encoding="utf-8")
    (target / "scripts/pipeline.py").write_text("print('changed')\n", encoding="utf-8")
    _commit(target, "code")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "full"
    assert "could not verify" in since["why"]
    assert project_tool.review_status(target)["last_review"]["commits_since"] is None


def test_a_changed_stage_command_is_shown_beside_the_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_review_project(tmp_path, monkeypatch, _stage())
    project_tool.record_review(target, "train")
    project_tool._state["stage"] = _stage(cmd="python scripts/train.py --new-method")
    row = _rows(target)["train"]
    since = row["since_review"]
    assert since["mode"] == "diff"
    assert since["diff"] == ""
    assert any("--new-method" in command for command in since["commands_added"])
    assert any("--new-method" not in command for command in since["commands_removed"])
    assert row["fix"] == "Read the code shown, then sign off"


def test_uncommitted_code_cannot_be_signed_off(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/pipeline.py").write_text("print('draft')\n", encoding="utf-8")
    row = _rows(target)["analysis"]
    assert row["uncommitted"] == ["scripts/pipeline.py"]
    assert row["fix"].startswith("Commit these files, then sign off")
    with pytest.raises(RuntimeError, match="Commit the code of analysis"):
        project_tool.record_review(target, "analysis")
    assert not (target / ".astack/reviews/analysis.json").exists()
    _commit(target, "code")
    assert _rows(target)["analysis"]["uncommitted"] == []
    assert project_tool.record_review(target, "analysis")["code_committed"] is True


@pytest.mark.integration
def test_a_long_diff_is_cut_short_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_text("x = 1\n" * 50, encoding="utf-8")
    _commit(target, "code")
    monkeypatch.setattr(project_tool, "REVIEW_DIFF_LINE_LIMIT", 10)
    since = _rows(target)["analysis"]["since_review"]
    assert since["truncated"] is True
    assert len(since["diff"].splitlines()) == 10


def test_a_symlinked_code_file_cannot_be_signed_off(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/real.py").write_text("print('a')\n", encoding="utf-8")
    (target / "scripts/link.py").symlink_to("real.py")
    _commit(target, "link")
    assert _rows(target)["analysis"]["uncommitted"] == ["scripts/link.py"]
    with pytest.raises(RuntimeError, match="scripts/link.py"):
        project_tool.record_review(target, "analysis")


@pytest.mark.integration
def test_a_code_file_turned_symlink_needs_a_full_read(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/other.py").write_text("print('b')\n", encoding="utf-8")
    _commit(target, "other")
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").unlink()
    (target / "scripts/pipeline.py").symlink_to("other.py")
    _commit(target, "link")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "full"
    assert "symlinks" in since["why"]


@pytest.mark.integration
def test_a_diff_driver_cannot_hide_a_changed_file(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    (target / ".gitattributes").write_text("*.py diff=omit\n", encoding="utf-8")
    subprocess.run(["git", "config", "diff.omit.textconv", "true"], cwd=target, check=True)
    (target / "scripts/pipeline.py").write_text("print('hidden?')\n", encoding="utf-8")
    _commit(target, "code")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "diff"
    assert "+print('hidden?')" in since["diff"]


@pytest.mark.integration
def test_a_clean_filter_cannot_hide_code_from_the_diff(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    (target / ".gitattributes").write_text("*.py filter=omit\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "filter.omit.clean", "sed '/^secret/d'"], cwd=target, check=True
    )
    (target / "scripts/pipeline.py").write_text(
        "secret = 'hidden'\nprint('changed')\n", encoding="utf-8"
    )
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "diff"
    assert "+secret = 'hidden'" in since["diff"]


@pytest.mark.integration
def test_filtered_code_cannot_be_signed_off(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    (target / ".gitattributes").write_text("*.py filter=omit\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "filter.omit.clean", "sed '/^secret/d'"], cwd=target, check=True
    )
    _commit(target, "filter")
    (target / "scripts/pipeline.py").write_text("secret = 1\nprint('x')\n", encoding="utf-8")
    _commit(target, "code")
    with pytest.raises(RuntimeError, match="scripts/pipeline.py"):
        project_tool.record_review(target, "analysis")


@pytest.mark.integration
def test_a_line_ending_change_is_visible(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_bytes(b"print('pipeline')\r\n")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "diff"
    assert "+print('pipeline')␍" in since["diff"]


@pytest.mark.integration
def test_a_replacement_object_cannot_hide_a_change(tmp_path: Path) -> None:
    target = _command_project(tmp_path)
    record = project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_text("print('changed')\n", encoding="utf-8")
    _commit(target, "code")

    def blob(revision: str) -> str:
        return subprocess.run(
            ["git", "rev-parse", f"{revision}:scripts/pipeline.py"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    subprocess.run(["git", "replace", blob(record["commit"]), blob("HEAD")], cwd=target, check=True)
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "diff"
    assert "+print('changed')" in since["diff"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"print('a')\xe2\x90\x8d\n", b"print('a')\r\n"),
        (b"print('a')\r\n", b"print('a')\xe2\x90\x8d\n"),
    ],
)
def test_a_carriage_return_lookalike_needs_a_full_read(
    tmp_path: Path, before: bytes, after: bytes
) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/pipeline.py").write_bytes(before)
    _commit(target, "before")
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_bytes(after)
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "full"
    assert "used to show whitespace" in since["why"]


@pytest.mark.integration
@pytest.mark.parametrize("char", ["\u2028", "\u2029", "\u202e", "\u200b", "\x0c", "\x85"])
def test_invisible_characters_need_a_full_read(tmp_path: Path, char: str) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_text(f"s = 'a{char}b'\n", encoding="utf-8")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "full"
    assert "invisible or control characters" in since["why"]


def test_code_is_always_escaped_when_shown() -> None:
    rows = [{"kind": "add", "line": 1, "text": "<script>alert(1)</script> **x**"}]
    shown = project_tool.review_code_html(rows)
    assert "<script>alert" not in shown
    assert "&lt;script&gt;alert(1)&lt;/script&gt; **x**" in shown


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "after", "shown"),
    [
        (b"s = '\\t'\n", b"s = '        '\n", "+s = '        '"),
        (b"s = '\t'\n", b"s = '        '\n", "-s = '\u21e5'"),
        (b"x = 1\n", b"x = 1   \n", "+x = 1\u2423\u2423\u2423"),
    ],
)
def test_tabs_and_trailing_spaces_are_visible(
    tmp_path: Path, before: bytes, after: bytes, shown: str
) -> None:
    target = _command_project(tmp_path)
    (target / "scripts/pipeline.py").write_bytes(before)
    _commit(target, "before")
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_bytes(after)
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "diff"
    assert shown in since["diff"]


@pytest.mark.integration
@pytest.mark.parametrize("marker", ["\u21e5", "\u2423"])
def test_a_whitespace_marker_in_code_needs_a_full_read(tmp_path: Path, marker: str) -> None:
    target = _command_project(tmp_path)
    project_tool.record_review(target, "analysis")
    (target / "scripts/pipeline.py").write_text(f"s = '{marker}'\n", encoding="utf-8")
    since = _rows(target)["analysis"]["since_review"]
    assert since["mode"] == "full"
    assert "used to show whitespace" in since["why"]


@pytest.mark.integration
def test_a_commit_landing_during_sign_off_is_never_recorded_as_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _command_project(tmp_path)
    real = project_tool._uncommitted

    def commit_in_between(root: Path, files: list[str], revision: str = "HEAD") -> list[str]:
        if revision != "HEAD":
            (target / "scripts/pipeline.py").write_text("print('B')\n", encoding="utf-8")
            _commit(target, "B")
        found: list[str] = real(root, files, revision)
        return found

    monkeypatch.setattr(project_tool, "_uncommitted", commit_in_between)
    with pytest.raises(RuntimeError, match="analysis"):
        project_tool.record_review(target, "analysis")
    assert not (target / ".astack/reviews/analysis.json").exists()
