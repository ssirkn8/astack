from __future__ import annotations

from typing import Any, cast

import pytest
from project_tool_test_support import (
    Path,
    _git_baseline,
    _project,
    _set_capabilities,
    json,
    project_tool,
    subprocess,
)

pytestmark = pytest.mark.integration

PIPELINE = (
    "stages:\n"
    "  model:\n"
    "    cmd: python scripts/model.py\n"
    "    deps:\n"
    "      - scripts/model.py\n"
    "    outs:\n"
    "      - evidence/model/metrics.json:\n"
    "          cache: false\n"
    "      - reports/figures/model:\n"
    "          cache: false\n"
)
LOCK = "schema: '2.0'\nstages:\n  model:\n    cmd: python scripts/model.py\n"


def _dvc_project(tmp_path: Path, *, locked: bool = True) -> Path:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    (target / ".dvc").mkdir(exist_ok=True)
    (target / ".dvc/config").write_text("[core]\nanalytics = false\n", encoding="utf-8")
    (target / "dvc.yaml").write_text(PIPELINE, encoding="utf-8")
    if locked:
        (target / "dvc.lock").write_text(LOCK, encoding="utf-8")
    (target / "scripts/model.py").write_text("print('model')\n", encoding="utf-8")
    metrics = target / "evidence/model/metrics.json"
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text('{"auc": 0.82}', encoding="utf-8")
    figure = target / "reports/figures/model/roc.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    figure.write_bytes(b"png")
    _git_baseline(target)
    return target


STAGES: list[dict[str, Any]] = [
    {
        "name": "model",
        "frozen": False,
        "outs": [
            {"path": "evidence/model/metrics.json", "dir": False},
            {
                "path": "reports/figures/model",
                "dir": True,
                "files": ["reports/figures/model/roc.png"],
            },
        ],
    }
]


def _fake_status(
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, Any] | None,
    returncode: int = 0,
    stages: list[dict[str, Any]] | None = None,
) -> None:
    """Answer the DVC pipeline probe the way DVC would, without running it."""
    original = project_tool._run

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if args[-1] == project_tool.DVC_PIPELINE_PROBE:
            payload = {"status": status, "stages": STAGES if stages is None else stages}
            return subprocess.CompletedProcess(
                args, returncode, json.dumps(payload) if status is not None else "", "boom"
            )
        return cast(
            subprocess.CompletedProcess[str],
            original(args, cwd=cwd, input_text=input_text, env=env),
        )

    monkeypatch.setattr(project_tool, "_run", fake_run)


def _labels(target: Path) -> dict[str, tuple[str, str]]:
    return {
        entry["path"]: (entry["label"], entry["reason"])
        for entry in project_tool.output_status(target)["outputs"]
    }


def test_up_to_date_stage_outputs_are_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, {})
    labels = _labels(target)
    assert labels["evidence/model/metrics.json"][0] == "current"
    assert labels["reports/figures/model"][0] == "current"


def test_changed_deps_make_outputs_outdated_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, {"model": [{"changed deps": {"scripts/model.py": "modified"}}]})
    label, reason = _labels(target)["evidence/model/metrics.json"]
    assert label == "outdated"
    assert "scripts/model.py" in reason


def test_changed_and_deleted_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(
        monkeypatch,
        {
            "model": [
                {
                    "changed outs": {
                        "evidence/model/metrics.json": "modified",
                        "reports/figures/model": "deleted",
                    }
                }
            ]
        },
    )
    labels = _labels(target)
    assert labels["evidence/model/metrics.json"][0] == "outdated"
    assert labels["reports/figures/model"][0] == "missing"


def test_an_absent_output_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _dvc_project(tmp_path)
    (target / "evidence/model/metrics.json").unlink()
    _fake_status(monkeypatch, {})
    assert _labels(target)["evidence/model/metrics.json"][0] == "missing"


def test_unreadable_dvc_status_is_unknown_never_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, None, returncode=1)
    result = project_tool.output_status(target)
    assert result["status"] == "unknown"
    assert not any(entry["label"] == "current" for entry in result["outputs"])


def test_files_no_stage_declares_are_outdated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    loose = target / "reports/tables/hand/summary.csv"
    loose.parent.mkdir(parents=True)
    loose.write_text("a\n", encoding="utf-8")
    _fake_status(monkeypatch, {})
    labels = _labels(target)
    assert labels["reports/tables/hand/summary.csv"] == ("outdated", "no run record")
    assert "reports/figures/model/roc.png" not in labels


def test_mapping_form_dvc_outputs_are_valid_pipeline_outputs(tmp_path: Path) -> None:
    target = _dvc_project(tmp_path)
    outputs, errors = project_tool._dvc_pipeline_outputs(target)
    assert errors == []
    assert outputs == ["evidence/model/metrics.json", "reports/figures/model"]


def _report(root: Path, body: str, status: str) -> None:
    path = root / "reports/study.md"
    path.write_text(f"---\ntitle: Study\nstatus: {status}\n---\n\n{body}\n", encoding="utf-8")


def _kinds(result: dict[str, Any]) -> list[str]:
    return [problem["kind"] for report in result["reports"] for problem in report["problems"]]


def test_report_cannot_cite_an_outdated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, {"model": [{"changed deps": {"scripts/model.py": "modified"}}]})
    for status in ("draft", "final"):
        _report(target, "AUC [0.82](../evidence/model/metrics.json#/auc).", status)
        assert _kinds(project_tool.report_check(target, [])) == ["outdated-evidence"]


def test_report_citing_current_outputs_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, {})
    _report(
        target,
        "AUC [0.82](../evidence/model/metrics.json#/auc) and ![ROC](figures/model/roc.png).",
        "final",
    )
    assert project_tool.report_check(target, [])["status"] == "passed"


def test_unrecorded_evidence_fails_a_final_report_and_is_listed_for_a_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    loose = target / "reports/tables/hand/summary.json"
    loose.parent.mkdir(parents=True)
    loose.write_text('{"n": 3}', encoding="utf-8")
    _fake_status(monkeypatch, {})
    _report(target, "N was [3](tables/hand/summary.json#/n).", "draft")
    draft = project_tool.report_check(target, [])
    assert draft["status"] == "passed"
    assert [note["kind"] for note in draft["reports"][0]["evidence_notes"]] == [
        "unrecorded-evidence"
    ]
    _report(target, "N was [3](tables/hand/summary.json#/n).", "final")
    assert _kinds(project_tool.report_check(target, [])) == ["unrecorded-evidence"]


def test_unknown_state_fails_only_a_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, None, returncode=1)
    _report(target, "AUC [0.82](../evidence/model/metrics.json#/auc).", "draft")
    assert project_tool.report_check(target, [])["status"] == "passed"
    _report(target, "AUC [0.82](../evidence/model/metrics.json#/auc).", "final")
    assert _kinds(project_tool.report_check(target, [])) == ["unverified-evidence"]


def _material(target: Path) -> None:
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace('stage = "exploratory"', 'stage = "material"'),
        encoding="utf-8",
    )


def test_material_analysis_expects_a_report(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _material(target)
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "report-missing" in checks
    (target / "reports/audits").mkdir(parents=True, exist_ok=True)
    (target / "reports/audits/audit.md").write_text("# Audit\n", encoding="utf-8")
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "report-missing" in checks
    (target / "reports/study.md").write_text("# Study\n", encoding="utf-8")
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "report-missing" not in checks


def test_exploratory_analysis_does_not_expect_a_report(tmp_path: Path) -> None:
    target = _project(tmp_path)
    checks = {item["check"] for item in project_tool.audit(target)["findings"]}
    assert "report-missing" not in checks


def _commit(target: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-qm", message],
        cwd=target,
        check=True,
        capture_output=True,
    )


def test_command_run_labels_follow_run_records(tmp_path: Path) -> None:
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
    script = target / "scripts/pipeline.py"
    script.write_text(
        "from pathlib import Path\n"
        "Path('data/processed/analysis').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/processed/analysis/result.txt').write_text('1')\n",
        encoding="utf-8",
    )
    _git_baseline(target)
    output = "data/processed/analysis/result.txt"
    assert _labels(target)[output] == ("missing", "not produced yet")

    project_tool.run_reproduction(target)
    assert _labels(target)[output][0] == "current"

    # Any committed change, even to Markdown the pipeline might read, makes a command run outdated.
    (target / "README.md").write_text("# Notes\n", encoding="utf-8")
    _commit(target, "docs")
    assert _labels(target)[output][0] == "outdated"
    project_tool.run_reproduction(target)
    assert _labels(target)[output][0] == "current"

    script.write_text(script.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    _commit(target, "code")
    assert _labels(target)[output][0] == "outdated"


def test_frozen_stage_outputs_are_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _dvc_project(tmp_path)
    frozen = [dict(STAGES[0], frozen=True)]
    _fake_status(monkeypatch, {}, stages=frozen)
    assert _labels(target)["evidence/model/metrics.json"][0] == "unknown"
    _report(target, "AUC [0.82](../evidence/model/metrics.json#/auc).", "final")
    assert _kinds(project_tool.report_check(target, [])) == ["unverified-evidence"]


def test_untracked_file_in_a_directory_output_is_unrecorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    (target / "reports/figures/model/hand.png").write_bytes(b"png")
    _fake_status(monkeypatch, {})
    _report(target, "![Hand](figures/model/hand.png) and ![ROC](figures/model/roc.png)", "final")
    assert _kinds(project_tool.report_check(target, [])) == ["unrecorded-evidence"]


def test_resolved_stage_paths_are_used_not_the_written_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    moved = [{"name": "inner", "frozen": False, "outs": [{"path": "sub/x.json", "dir": False}]}]
    (target / "sub").mkdir()
    (target / "sub/x.json").write_text("{}", encoding="utf-8")
    _fake_status(monkeypatch, {}, stages=moved)
    labels = _labels(target)
    assert labels["sub/x.json"][0] == "current"
    assert labels["reports/figures/model/roc.png"] == ("outdated", "no run record")


def test_unknown_state_makes_every_uncovered_citation_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(monkeypatch, None, returncode=1)
    _report(target, "![ROC](figures/model/roc.png)", "final")
    assert _kinds(project_tool.report_check(target, [])) == ["unverified-evidence"]


def test_a_hand_written_file_under_runs_is_not_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    fake = target / "evidence/model/runs/metrics.json"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text('{"auc": 0.99}', encoding="utf-8")
    _fake_status(monkeypatch, {})
    for status in ("draft", "final"):
        _report(target, "AUC [0.99](../evidence/model/runs/metrics.json#/auc).", status)
        assert _kinds(project_tool.report_check(target, [])) == ["outdated-evidence"]


def test_a_current_run_record_may_be_cited(tmp_path: Path) -> None:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace('stage = "exploratory"', 'stage = "material"')
        .replace("components = []", 'components = ["analysis"]')
        + '\n[reproduction]\ncomponent = "analysis"\n'
        'command = ["python", "scripts/pipeline.py"]\nrandom_seed = 42\n'
        'outputs = ["data/processed/analysis/result.txt"]\n',
        encoding="utf-8",
    )
    (target / "scripts/pipeline.py").write_text(
        "from pathlib import Path\n"
        "Path('data/processed/analysis').mkdir(parents=True, exist_ok=True)\n"
        "Path('data/processed/analysis/result.txt').write_text('1')\n",
        encoding="utf-8",
    )
    _git_baseline(target)
    promoted = Path(project_tool.run_reproduction(target, promote=True)["promoted_record"])
    relative = promoted.relative_to(target).as_posix()
    _report(target, f"Seed [42](../{relative}#/random_seed).", "final")
    assert project_tool.report_check(target, [])["status"] == "passed"
    (target / "scripts/pipeline.py").write_text("print('changed')\n", encoding="utf-8")
    _commit(target, "code")
    assert _kinds(project_tool.report_check(target, [])) == ["outdated-evidence"]


CHAIN: list[dict[str, Any]] = [
    {
        "name": "prepare",
        "frozen": False,
        "deps": [{"path": "data/raw/in.csv"}],
        "outs": [{"path": "evidence/model/metrics.json", "dir": False}],
    },
    {
        "name": "analyze",
        "frozen": False,
        "deps": [{"path": "evidence/model/metrics.json"}],
        "outs": [
            {
                "path": "reports/figures/model",
                "dir": True,
                "files": ["reports/figures/model/roc.png"],
            }
        ],
    },
]


def test_a_stale_upstream_stage_makes_downstream_outputs_outdated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    _fake_status(
        monkeypatch, {"prepare": [{"changed deps": {"data/raw": "modified"}}]}, stages=CHAIN
    )
    labels = _labels(target)
    assert labels["evidence/model/metrics.json"][0] == "outdated"
    assert labels["reports/figures/model"] == ("outdated", "upstream stage prepare is outdated")
    _report(target, "![ROC](figures/model/roc.png)", "final")
    assert _kinds(project_tool.report_check(target, [])) == ["outdated-evidence"]


def test_a_frozen_upstream_stage_makes_downstream_outputs_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    frozen = [dict(CHAIN[0], frozen=True), CHAIN[1]]
    _fake_status(monkeypatch, {}, stages=frozen)
    assert _labels(target)["reports/figures/model"][0] == "unknown"


def test_a_symlinked_output_matches_through_its_real_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    real = target / "evidence/model/target.json"
    real.write_text('{"auc": 0.82}', encoding="utf-8")
    link = target / "evidence/model/linked.json"
    link.symlink_to(real)
    stages = [
        {
            "name": "model",
            "frozen": False,
            "outs": [
                {
                    "path": "evidence/model/linked.json",
                    "real": "evidence/model/target.json",
                    "dir": False,
                }
            ],
        }
    ]
    _fake_status(monkeypatch, {}, stages=stages)
    _report(target, "AUC [0.82](../evidence/model/linked.json#/auc).", "final")
    assert project_tool.report_check(target, [])["status"] == "passed"


def test_a_tracked_symlink_inside_a_directory_output_matches_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    (target / "evidence/target.json").write_text('{"auc": 0.82}', encoding="utf-8")
    nested = target / "evidence/nested"
    nested.mkdir()
    (nested / "link.json").symlink_to("../target.json")
    out = {
        "path": "evidence/nested",
        "dir": True,
        "files": ["evidence/nested/link.json"],
        "aliases": {"evidence/target.json": "evidence/nested/link.json"},
    }
    _fake_status(monkeypatch, {}, stages=[{"name": "model", "frozen": False, "outs": [out]}])
    _report(target, "AUC [0.82](../evidence/nested/link.json#/auc).", "final")
    assert project_tool.report_check(target, [])["status"] == "passed"


def test_reading_an_undeclared_derived_file_makes_a_stage_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    stages = [
        {
            "name": "analyze",
            "frozen": False,
            "deps": [{"path": "evidence/model/side-effect.json"}, {"path": "src/pkg"}],
            "outs": [{"path": "evidence/model/metrics.json", "dir": False}],
        }
    ]
    _fake_status(monkeypatch, {}, stages=stages)
    label, reason = _labels(target)["evidence/model/metrics.json"]
    assert label == "unknown"
    assert "evidence/model/side-effect.json" in reason


def test_source_and_raw_deps_need_no_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    stages = [
        {
            "name": "model",
            "frozen": False,
            "deps": [{"path": "src/pkg"}, {"path": "data/raw/in.csv"}, {"path": "params.yaml"}],
            "outs": [{"path": "evidence/model/metrics.json", "dir": False}],
        }
    ]
    _fake_status(monkeypatch, {}, stages=stages)
    assert _labels(target)["evidence/model/metrics.json"][0] == "current"


def test_a_folder_dep_fully_made_of_declared_outputs_is_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _dvc_project(tmp_path)
    stages = [
        {
            "name": "metrics",
            "frozen": False,
            "deps": [{"path": "src/pkg"}],
            "outs": [{"path": "evidence/model/metrics.json", "dir": False}],
        },
        {
            "name": "plot",
            "frozen": False,
            "deps": [{"path": "evidence/model"}],
            "outs": [{"path": "reports/figures/model", "dir": True, "files": []}],
        },
    ]
    _fake_status(monkeypatch, {}, stages=stages)
    assert _labels(target)["reports/figures/model"][0] == "current"
    (target / "evidence/model/extra.json").write_text("{}", encoding="utf-8")
    label, reason = _labels(target)["reports/figures/model"]
    assert label == "unknown"
    assert "evidence/model/extra.json" in reason
