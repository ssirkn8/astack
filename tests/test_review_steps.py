"""Signing off a pipeline step by step: units, groups, shared code, revoking and every page."""

from __future__ import annotations

import ast
import copy
import errno
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from project_tool_test_support import Path, _git_baseline, _project, project_tool
from review_demo import PARAMS, REGIONS, SHARED, demo_stages, write_demo

NOTEBOOKS = Path(__file__).resolve().parents[1] / "templates/capabilities/marimo/notebooks"
NOTEBOOK_LINE_BUDGET = 340


def _commit(target: Path, message: str = "change") -> None:
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Reviewer", "-c", "user.email=r@e", "commit", "-qm", message],
        cwd=target,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("tools = []", 'tools = ["dvc"]')
        + '\n[reproduction]\nengine = "dvc"\n',
        encoding="utf-8",
    )
    write_demo(target)
    _git_baseline(target)
    subprocess.run(["git", "config", "user.name", "Reviewer"], cwd=target, check=True)
    state = {"stages": demo_stages()}
    monkeypatch.setattr(project_tool, "_demo", state, raising=False)
    monkeypatch.setattr(
        project_tool,
        "_dvc_pipeline",
        lambda root, targets=None, read_only=False, stages_only=False: (
            {"status": {}, "stages": copy.deepcopy(state["stages"])},
            "",
        ),
    )
    return target


def _rows(target: Path) -> dict[str, dict[str, Any]]:
    rows, _ = project_tool._code_review(target, project_tool._project_config(target))
    return {row["unit"]: row for row in rows}


def _status(target: Path) -> dict[str, Any]:
    status: dict[str, Any] = project_tool.review_status(target)
    return status


def _sign_all(target: Path) -> None:
    for unit in _rows(target):
        project_tool.record_review(target, unit)


def _set_param(stages: list[dict[str, Any]], stage: str, key: str, value: object) -> None:
    for item in stages:
        if item["name"] == stage or item["name"].startswith(f"{stage}@"):
            for dep in item["deps"]:
                if dep["path"] == PARAMS:
                    dep["params"][key] = value


def test_a_foreach_group_is_one_step_and_shared_code_stands_alone(demo: Path) -> None:
    rows = _rows(demo)
    assert len(rows) == 13
    assert len(rows["clean"]["items"]) == len(REGIONS)
    assert rows[f"shared:{SHARED}"]["kind"] == "shared"
    assert len(rows[f"shared:{SHARED}"]["used_by"]) == 12
    assert SHARED not in rows["clean"]["files"]
    assert rows["clean"]["shared"] == [SHARED]
    assert rows["clean"]["upstream"] == ["ingest"]
    assert list(rows).index("ingest") < list(rows).index("clean") < list(rows).index("summary")


@pytest.mark.integration
def test_signing_off_a_group_covers_every_item(demo: Path) -> None:
    project_tool.record_review(demo, "clean")
    assert _rows(demo)["clean"]["status"] == "reviewed"
    saved = project_tool._load_review(demo, "clean")[0]
    assert saved is not None
    assert len(saved["commands"]) == len(REGIONS)


@pytest.mark.integration
def test_an_odd_item_in_a_group_is_called_out(demo: Path) -> None:
    page = project_tool.review_page(demo, "clean", _status(demo), {})
    assert page["differences"] == ["Value after `--water-mask` is `off`, except delta (`on`)"]
    shown = page["difference_html"][0].replace(' style="white-space:pre-wrap"', "")
    assert shown == (
        "Value after <code>--water-mask</code> is <code>off</code>, except delta (<code>on</code>)"
    )
    assert page["inputs"][0]["input"] == "evidence/ingest/${key}.json"


def test_a_short_item_key_never_rewrites_other_words() -> None:
    assert project_tool._key_template("python scripts/clean.py n", "n") == (
        "python scripts/clean.py ${key}"
    )


@pytest.mark.integration
def test_a_parameter_only_resets_the_steps_that_use_it(demo: Path) -> None:
    _sign_all(demo)
    _set_param(project_tool._demo["stages"], "clean", "clean.min_coherence", 0.4)
    rows = _rows(demo)
    assert rows["clean"]["status"] == "changed-since-review"
    assert rows["velocity"]["status"] == "reviewed"
    change = rows["clean"]["since_review"]["params_changed"][0]
    assert (change["was"], change["now"]) == (0.5, 0.4)
    page = project_tool.review_page(demo, "clean", _status(demo), {})
    row = next(item for item in page["params"] if "min_coherence" in item["parameter"])
    assert row["since sign-off"] == "changed, was 0.5"
    assert page["param_usage"][0]["lines"]


@pytest.mark.integration
def test_a_shared_change_asks_for_one_review_not_one_per_step(demo: Path) -> None:
    _sign_all(demo)
    (demo / SHARED).write_text('"""Geodesy helpers."""\n\nX = 2\n', encoding="utf-8")
    _commit(demo)
    rows = _rows(demo)
    assert rows[f"shared:{SHARED}"]["status"] == "changed-since-review"
    assert [unit for unit, row in rows.items() if row["status"] != "reviewed"] == [
        f"shared:{SHARED}"
    ]
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert any(SHARED in note for note in page["notes"])


@pytest.mark.integration
def test_an_upstream_change_is_a_note_and_does_not_block(demo: Path) -> None:
    _sign_all(demo)
    (demo / "scripts/mosaic.py").write_text('"""Changed."""\n', encoding="utf-8")
    _commit(demo)
    assert _rows(demo)["velocity"]["status"] == "reviewed"
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert "Upstream step mosaic is changed." in page["notes"]


@pytest.mark.integration
@pytest.mark.parametrize("path", ["dvc.yaml", PARAMS])
def test_an_uncommitted_command_or_parameter_file_blocks_sign_off(demo: Path, path: str) -> None:
    with (demo / path).open("a", encoding="utf-8") as handle:
        handle.write("# edit\n")
    assert path in _rows(demo)["clean"]["uncommitted"]
    with pytest.raises(RuntimeError, match="Commit the code of clean"):
        project_tool.record_review(demo, "clean")


@pytest.mark.integration
def test_a_missing_parameter_is_unknown_never_signed(demo: Path) -> None:
    for stage in project_tool._demo["stages"]:
        if stage["name"] == "velocity":
            stage["deps"][-1]["params_missing"] = ["velocity.gone"]
    row = _rows(demo)["velocity"]
    assert row["status"] == "unknown"
    assert "velocity.gone" in row["why"]
    with pytest.raises(RuntimeError, match="cannot be reviewed"):
        project_tool.record_review(demo, "velocity")


@pytest.mark.integration
def test_revoking_blocks_release_keeps_history_and_can_be_signed_again(demo: Path) -> None:
    _sign_all(demo)
    assert project_tool.pending_reviews(demo) == []
    revocation = project_tool.revoke_review(demo, "velocity", "  wrong   reference frame ")
    assert revocation["reason"] == "wrong reference frame"
    assert revocation["previous"]["unit"] == "velocity"
    assert project_tool.pending_reviews(demo) == ["velocity: revoked"]
    row = _rows(demo)["velocity"]
    assert row["since_review"]["why"] == "its sign-off was revoked"
    with pytest.raises(RuntimeError, match="no sign-off to revoke"):
        project_tool.revoke_review(demo, "velocity")
    project_tool.record_review(demo, "velocity")
    assert project_tool.pending_reviews(demo) == []


@pytest.mark.integration
def test_a_forged_revocation_without_its_fields_does_not_count(demo: Path) -> None:
    saved = project_tool.record_review(demo, "velocity")
    path = demo / _rows(demo)["velocity"]["record"]
    path.write_text(json.dumps(saved | {"revoked": False}), encoding="utf-8")
    assert _rows(demo)["velocity"]["status"] == "invalid-record"


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError(errno.EACCES, "Permission denied", "scripts/clean.py"),
        OSError(errno.E2BIG, "Argument list too long", "git"),
        subprocess.TimeoutExpired("git", 30),
        subprocess.CompletedProcess(["git", "hash-object"], 128, "", "fatal: cannot hash file"),
    ],
)
def test_unreadable_code_or_failed_git_blocks_only_its_unit(
    demo: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception | subprocess.CompletedProcess[str],
) -> None:
    open_file, run = project_tool._open_regular_file, project_tool._run

    def open_regular_file(path: Path) -> Any:
        if isinstance(failure, PermissionError) and path == demo / "scripts/clean.py":
            raise failure
        return open_file(path)

    def git(args: list[str], **kwargs: Any) -> Any:
        if not isinstance(failure, PermissionError) and "hash-object" in args:
            if "scripts/clean.py" in args:
                assert kwargs["timeout"] == 30
                if isinstance(failure, subprocess.CompletedProcess):
                    return failure
                raise failure
        return run(args, **kwargs)

    monkeypatch.setattr(project_tool, "_open_regular_file", open_regular_file)
    monkeypatch.setattr(project_tool, "_run", git)
    status = _status(demo)
    rows = {row["unit"]: row for row in status["units"]}
    assert rows["clean"]["status"] == "unknown"
    assert rows["clean"]["checksum"] is None
    if isinstance(failure, subprocess.CompletedProcess):
        assert "fatal: cannot hash file" in rows["clean"]["why"]
    assert rows["velocity"]["status"] == "unreviewed"
    assert status["problem"] is None


def test_a_failed_history_hash_blocks_the_affected_unit(
    demo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_tool.record_review(demo, "clean")
    (demo / "scripts/clean.py").write_text("# Changed\n", encoding="utf-8")
    run = project_tool._run

    def fail_history_hash(args: list[str], **kwargs: Any) -> Any:
        if "hash-object" in args and args[-1:] == ["scripts/clean.py"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: cannot hash historical diff")
        return run(args, **kwargs)

    monkeypatch.setattr(project_tool, "_run", fail_history_hash)
    rows = _rows(demo)
    assert rows["clean"]["status"] == "unknown"
    assert rows["clean"]["checksum"] is None
    assert "cannot hash historical diff" in rows["clean"]["why"]
    assert rows["velocity"]["status"] == "unreviewed"


@pytest.mark.parametrize(
    "config,problem",
    [
        (b"[reproduction", "Could not read .astack/project.toml"),
        (b"reproduction = 12\n", "reproduction must be a table"),
        (b"[reproduction]\ncommand = 12\n", "reproduction.command must be a"),
    ],
)
def test_bad_project_config_is_reported_in_the_notebook(
    tmp_path: Path, config: bytes, problem: str
) -> None:
    target = _project(tmp_path)
    (target / ".astack/project.toml").write_bytes(config)
    status = _status(target)
    assert problem in status["problem"]
    assert status["units"] == []
    assert status["to_sign_off"] == 1
    assert project_tool.review_labels(target) == {}


def test_visible_whitespace_at_exact_line_ends() -> None:
    spaces = " " * 40_000
    assert project_tool._visible(f"{spaces}x \r\n\t  \n \v\u2028 {spaces}") == (
        f"{spaces}x ␍\n⇥␣␣\n \v\u2028" + "␣" * (len(spaces) + 1)
    )


def test_odd_unit_names_get_safe_distinct_record_files(tmp_path: Path) -> None:
    first = project_tool._review_record_path(tmp_path, "shared:src/a b.py")
    second = project_tool._review_record_path(tmp_path, "shared:src/a-b.py")
    third = project_tool._review_record_path(tmp_path, "sub/dvc.yaml:Train")
    assert first != second
    assert first.parent.name == "shared"
    assert third.parent == tmp_path / ".astack/reviews"
    assert re.fullmatch(r"[a-z0-9_-]+\.json", third.name)


@pytest.mark.integration
def test_every_page_builds_and_the_queue_visits_every_item(demo: Path) -> None:
    status = _status(demo)
    for row in status["units"]:
        page = project_tool.review_page(demo, row["unit"], status, {})
        assert project_tool.review_sections(page)
        assert project_tool.review_title_html(page)
    seen: list[str] = []
    current = None
    for _ in status["units"]:
        current = project_tool.next_to_sign_off(status, current)
        assert current is not None
        seen.append(current)
    assert sorted(seen) == sorted(row["unit"] for row in status["units"])


@pytest.mark.integration
def test_a_changed_file_shows_its_changes_and_the_full_file(demo: Path) -> None:
    _sign_all(demo)
    path = demo / "scripts/velocity.py"
    path.write_text(path.read_text(encoding="utf-8") + "print('new line')\n", encoding="utf-8")
    _commit(demo)
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    view = page["code"][0]
    assert view["state"] == "changed"
    assert any(row["kind"] == "add" and "new line" in row["text"] for row in view["changes"])
    assert len(view["full"]) >= len(view["changes"]) - 1


@pytest.mark.integration
def test_a_full_read_shows_invisible_characters(demo: Path) -> None:
    (demo / "scripts/velocity.py").write_text("s = 'a‮b'\n", encoding="utf-8")
    _commit(demo)
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert "U+202E" in page["code"][0]["full"][0]["text"]


@pytest.mark.integration
def test_the_map_has_every_unit_and_only_real_edges(demo: Path) -> None:
    status = _status(demo)
    drawn = project_tool.review_map(status)
    ids = {node["id"] for node in drawn["nodes"]}
    assert ids == {row["unit"] for row in status["units"]}
    assert {"from": "ingest", "to": "clean"} in drawn["edges"]
    assert all(edge["from"] in ids and edge["to"] in ids for edge in drawn["edges"])
    assert len({(node["x"], node["y"]) for node in drawn["nodes"]}) == len(drawn["nodes"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_map_widget_is_valid_javascript(tmp_path: Path) -> None:
    copy_path = tmp_path / "review_map.mjs"
    copy_path.write_bytes((NOTEBOOKS / "review_map.js").read_bytes())
    subprocess.run(["node", "--check", str(copy_path)], check=True, capture_output=True)


def test_the_map_widget_loads_nothing_external() -> None:
    source = (NOTEBOOKS / "review_map.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\b|\bfetch\(|https?://(?!www\.w3\.org)", source, re.M)


@pytest.mark.integration
def test_bulk_sign_off_covers_only_opened_items_at_the_code_seen() -> None:
    def row(unit: str, state: str) -> dict[str, Any]:
        return {"unit": unit, "kind": "step", "items": [{}], "status": state}

    status = {"units": [row("a", "unreviewed"), row("b", "unreviewed"), row("c", "reviewed")]}
    picked = [{"item": "a"}, {"item": "b"}, {"item": "c"}]
    assert project_tool.review_bulk_plan(status, picked, {"a": "seen", "c": "x"}) == (
        [("a", "seen")],
        ["b (open it first)"],
    )


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["single", "bulk"])
def test_restoring_code_after_opening_a_stale_page_never_signs_unseen_code(
    demo: Path, mode: str
) -> None:
    unit = "velocity"
    status = _status(demo)
    path = demo / "scripts/velocity.py"
    committed = path.read_bytes()
    path.write_bytes(b"UNSEEN_EDIT = True\n")
    page = project_tool.review_page(demo, unit, status, {})
    assert page["code"][0]["full"][0]["text"] == "UNSEEN_EDIT = True"
    path.write_bytes(committed)

    def sign(name: str, checksum: str) -> None:
        project_tool.record_review(demo, name, checksum)

    if mode == "single":
        gate = project_tool.ReviewActionGate()
        view = project_tool.review_action_view(page, status, None, None, None)
        token = gate.issue(view["allowed"])
        unused = Mock()
        project_tool.review_apply_action(
            gate.accept(_click(token, "sign")), page, None, sign, unused, unused, unused, unused
        )
    else:
        opened = {unit: page["checksum"]} if page["checksum"] else {}
        allowed, _ = project_tool.review_bulk_plan(status, [{"item": unit}], opened)
        for name, checksum in allowed:
            sign(name, checksum)
    assert not (demo / _rows(demo)[unit]["record"]).exists()

    # Opening the committed bytes with the same cached status makes sign-off available again.
    fresh = project_tool.review_page(demo, unit, status, {})
    assert not fresh["blocked"]
    sign(unit, fresh["checksum"])
    assert _rows(demo)[unit]["status"] == "reviewed"


@pytest.mark.integration
@pytest.mark.parametrize("change", ["settings", "parameter"])
def test_sign_off_rejects_declarations_changed_after_render(demo: Path, change: str) -> None:
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    if change == "settings":
        stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
        stage["settings"] = {"always_changed": True}
        path = demo / "dvc.yaml"
    else:
        _set_param(project_tool._demo["stages"], "velocity", "velocity.threshold", 3)
        path = demo / PARAMS
    with path.open("a", encoding="utf-8") as handle:
        handle.write("# declaration changed\n")
    _commit(demo)
    with pytest.raises(RuntimeError, match="changed since this page loaded"):
        project_tool.record_review(demo, "velocity", page["checksum"])


def test_a_stage_script_without_a_docstring_is_a_p2_finding(demo: Path) -> None:
    (demo / "dvc.yaml").write_text("# python scripts/velocity.py\n", encoding="utf-8")
    (demo / "scripts/velocity.py").write_text("x = 1\n", encoding="utf-8")
    findings: list[Any] = []
    project_tool._check_stage_purposes(demo, findings)
    assert [(item.priority, item.check) for item in findings] == [("P2", "stage-script-purpose")]


@pytest.mark.integration
def test_every_differing_group_value_is_shown_with_its_item(demo: Path) -> None:
    for stage in project_tool._demo["stages"]:
        if stage["name"] == "clean@north":
            stage["deps"][-1]["params"]["clean.min_coherence"] = 1234567
    page = project_tool.review_page(demo, "clean", _status(demo), {})
    row = next(item for item in page["params"] if "clean@north" in item["parameter"])
    assert row["value"] == "1234567"
    assert sum("clean@" in item["parameter"] for item in page["params"]) == len(REGIONS)


@pytest.mark.integration
def test_changing_a_steps_data_input_resets_it_and_shows_how(demo: Path) -> None:
    _sign_all(demo)
    stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
    dep = next(item for item in stage["deps"] if item["path"] == "evidence/prepare/result.json")
    dep["path"] = "data/raw/north.csv"
    assert _rows(demo)["velocity"]["status"] == "changed-since-review"
    assert project_tool.pending_reviews(demo) == ["velocity: changed-since-review"]
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    change = page["run_changes"][0]
    assert change["what"] == "inputs"
    assert "data/raw/north.csv" in change["now"]
    assert "evidence/prepare/result.json" in change["was"]


@pytest.mark.integration
def test_a_steps_working_folder_is_shown_and_its_change_called_out(demo: Path) -> None:
    _sign_all(demo)
    stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
    stage["wdir"] = "elsewhere"
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert page["commands"][0].startswith("[in elsewhere] ")
    assert page["run_changes"] == [
        {"item": "velocity", "what": "folder", "was": ".", "now": "elsewhere"}
    ]


def _command_demo(tmp_path: Path, command: str, outputs: str) -> Path:
    target = _project(tmp_path)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + f'\n[reproduction]\ncomponent = "analysis"\ncommand = {command}\noutputs = {outputs}\n',
        encoding="utf-8",
    )
    (target / "scripts/run.py").write_text('"""Run."""\n', encoding="utf-8")
    _git_baseline(target)
    subprocess.run(["git", "config", "user.name", "Reviewer"], cwd=target, check=True)
    return target


def test_a_command_projects_outputs_are_signed_off(tmp_path: Path) -> None:
    target = _command_demo(tmp_path, '["python", "scripts/run.py"]', '["reports/a.csv"]')
    project_tool.record_review(target, "analysis")
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("reports/a.csv", "reports/b.csv"),
        encoding="utf-8",
    )
    _commit(target)
    row = _rows(target)["analysis"]
    assert row["status"] == "changed-since-review"
    change = project_tool._run_changes(row["since_review"])[0]
    assert change["what"] == "outputs"
    assert "reports/b.csv" in change["now"]


def test_command_arguments_keep_their_boundaries(tmp_path: Path) -> None:
    target = _command_demo(tmp_path, '["run", "--mode", "safe --delete"]', "[]")
    assert _rows(target)["analysis"]["items"][0]["command"] == "run --mode 'safe --delete'"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
def test_opening_a_page_after_code_becomes_a_pipe_blocks_sign_off(tmp_path: Path) -> None:
    target = _command_demo(tmp_path, '["python", "scripts/run.py"]', "[]")
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from pathlib import Path
import sys

sys.path.insert(0, ".astack")
from workflow import review_page, review_status

root = Path.cwd()
status = review_status(root)
assert status["units"][0]["checksum"] is not None
source = root / "scripts/run.py"
source.unlink()
os.mkfifo(source)
page = review_page(root, "analysis", status, {})
assert page["stale"] and page["blocked"] and page["checksum"] is None
assert page["code"][0]["state"] == "unreadable"
""",
        ],
        cwd=target,
        check=True,
        timeout=15,
    )


@pytest.fixture
def command_review_record(tmp_path: Path) -> tuple[Path, str, Path]:
    output = "data/processed/analysis/result.parquet"
    command = ["python", "scripts/run.py"]
    target = _command_demo(tmp_path, json.dumps(command), json.dumps([output]))
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("components = []", 'components = ["analysis"]'),
        encoding="utf-8",
    )
    _commit(target)
    (target / output).parent.mkdir(parents=True, exist_ok=True)
    (target / output).write_text("{}", encoding="utf-8")
    record = project_tool._reproduction_state(target) | {
        "command": command,
        "schema_version": project_tool.RUN_RECORD_SCHEMA_VERSION,
        "timestamp": "2026-01-01T00:00:00Z",
        "verification": {"status": "unverified"},
        "outputs": {output: project_tool._output_digest(target, output)},
    }
    record_path = target / ".cache/runs/analysis/record.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert project_tool.review_labels(target)[output] == "current"
    return target, output, record_path


def test_malformed_run_record_leaves_other_labels_available(
    command_review_record: tuple[Path, str, Path],
) -> None:
    target, output, valid_record = command_review_record
    record = json.loads(valid_record.read_text(encoding="utf-8"))
    record["verification"]["status"] = []
    bad_record = valid_record.with_name("bad.json")
    bad_record.write_text(json.dumps(record), encoding="utf-8")
    assert project_tool.review_labels(target)[output] == "current"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
@pytest.mark.parametrize("metadata", ["uv.lock", "data/sources.yml", ".astack/version"])
def test_command_review_does_not_read_run_metadata_from_a_pipe(
    command_review_record: tuple[Path, str, Path], metadata: str
) -> None:
    target, _, _ = command_review_record
    pipe = target / metadata
    pipe.unlink()
    os.mkfifo(pipe)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.astack'); "
            "from workflow import review_labels; from pathlib import Path; "
            "print(review_labels(Path.cwd()))",
        ],
        cwd=target,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == "{}"


@pytest.mark.integration
def test_swapping_which_item_uses_which_code_file_resets_the_step(demo: Path) -> None:
    for name in ("a", "b"):
        (demo / f"scripts/{name}.json").write_text("{}\n", encoding="utf-8")
    _commit(demo)
    stages = project_tool._demo["stages"]
    north = next(item for item in stages if item["name"] == "clean@north")
    south = next(item for item in stages if item["name"] == "clean@south")
    north["deps"].insert(0, {"path": "scripts/a.json"})
    south["deps"].insert(0, {"path": "scripts/b.json"})
    project_tool.record_review(demo, "clean")
    north["deps"][0], south["deps"][0] = south["deps"][0], north["deps"][0]
    assert _rows(demo)["clean"]["status"] == "changed-since-review"
    page = project_tool.review_page(demo, "clean", _status(demo), {})
    assert {row["what"] for row in page["run_changes"]} == {"code"}
    shown = next(row for row in page["items"] if row["item"] == "north")
    assert "scripts/b.json" in shown["code"]


@pytest.mark.integration
def test_a_missing_final_newline_is_shown_on_a_full_read(demo: Path) -> None:
    view = project_tool._code_view(demo, "scripts/velocity.py", {}, b"a")
    assert view["full"][-1] == {"kind": "note", "line": None, "text": "No newline at end of file"}
    assert len(project_tool._code_view(demo, "scripts/velocity.py", {}, b"a\n")["full"]) == 1


@pytest.mark.integration
def test_freezing_a_stage_resets_its_sign_off_and_shows(demo: Path) -> None:
    _sign_all(demo)
    stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
    stage["frozen"] = True
    assert _rows(demo)["velocity"]["status"] == "changed-since-review"
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert page["commands"][0].startswith("[frozen")
    assert page["run_changes"] == [
        {"item": "velocity", "what": "frozen", "was": "False", "now": "True"}
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "settings",
    [
        {"always_changed": True},
        {"output evidence/velocity/result.json": {"cache": False}},
        {"output evidence/velocity/result.json": {"persist": True}},
        {"output evidence/velocity/result.json": {"push": False, "remote": "backup"}},
    ],
)
def test_a_run_setting_resets_the_sign_off_and_shows(demo: Path, settings: dict[str, Any]) -> None:
    _sign_all(demo)
    stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
    stage["settings"] = settings
    assert _rows(demo)["velocity"]["status"] == "changed-since-review"
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert [row["what"] for row in page["run_changes"]] == ["settings"]
    assert {row["setting"] for row in page["settings"]} == set(settings)
    assert any(
        block["type"] == "table" and block["rows"] == page["settings"]
        for block in project_tool.review_sections(page)
    )


def test_full_text_never_shows_two_different_files_alike() -> None:
    shown = project_tool._shown_text
    samples = [
        "a\tb",
        "a\u21e5b",
        "a\rb",
        "a\u240db",
        "a \n",
        "a\u2423\n",
        "a\u202eb",
        "a\u27e6U+202E\u27e7b",
    ]
    rendered = [shown(sample.encode()) for sample in samples]
    assert len(set(rendered)) == len(samples)


@pytest.mark.integration
def test_a_single_steps_declared_code_paths_are_shown(demo: Path) -> None:
    stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
    stage["deps"][0] = {"path": "scripts"}
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    assert {"declared code": "scripts"} in page["declared_code"]
    assert any(
        block["type"] == "table" and {"declared code": "scripts"} in block["rows"]
        for block in project_tool.review_sections(page)
    )


@pytest.mark.integration
def test_a_parameter_only_some_items_declare_names_them(demo: Path) -> None:
    for stage in project_tool._demo["stages"]:
        if stage["name"].startswith("clean@") and stage["name"] != "clean@north":
            stage["deps"][-1]["params"] = {}
    page = project_tool.review_page(demo, "clean", _status(demo), {})
    assert [row["parameter"] for row in page["params"]] == [
        "params.yaml:clean.min_coherence (clean@north)"
    ]


@pytest.mark.integration
def test_the_overview_counts_removed_and_shared_code(demo: Path) -> None:
    _sign_all(demo)
    stage = next(item for item in project_tool._demo["stages"] if item["name"] == "velocity")
    stage["deps"] = [dep for dep in stage["deps"] if dep["path"] != "scripts/velocity.py"]
    stage["deps"] = [dep for dep in stage["deps"] if dep["path"] != SHARED]
    rows = project_tool.review_overview(demo, _status(demo), {})
    row = next(item for item in rows if item["item"] == "velocity")
    assert "1 removed file," in row["changed since sign-off"]
    assert row["changed since sign-off"].endswith("1 shared code change")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_map_keeps_prototype_names_as_plain_names() -> None:
    source = (NOTEBOOKS / "review_map.js").read_text(encoding="utf-8")
    assert "const ups = Object.create(null)" in source
    assert "const downs = Object.create(null)" in source
    assert "const boxes = Object.create(null)" in source


def _cells() -> list[ast.FunctionDef]:
    tree = ast.parse((NOTEBOOKS / "review.py").read_text(encoding="utf-8"))
    return [node for node in tree.body if isinstance(node, ast.FunctionDef)]


def test_the_notebook_stays_thin() -> None:
    lines = (NOTEBOOKS / "review.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= NOTEBOOK_LINE_BUDGET, "Move logic into workflow/project_runtime/review.py"


def test_no_closure_reads_a_private_cell_variable_marimo_deleted() -> None:
    """marimo deletes `_private` cell variables after a cell runs, breaking later callbacks."""
    problems: list[str] = []
    for cell in _cells():
        private = {
            target.id
            for node in cell.body
            if isinstance(node, ast.Assign | ast.For)
            for target in ast.walk(node.targets[0] if isinstance(node, ast.Assign) else node.target)
            if isinstance(target, ast.Name) and target.id.startswith("_") and target.id != "_"
        }
        for node in ast.walk(cell):
            if not isinstance(node, ast.Lambda | ast.FunctionDef) or node is cell:
                continue
            bound = {arg.arg for arg in [*node.args.args, *node.args.kwonlyargs]}
            # Default values run while the cell runs, so only the body can outlive it.
            body = [node.body] if isinstance(node, ast.Lambda) else node.body
            names = [item for part in body for item in ast.walk(part) if isinstance(item, ast.Name)]
            bound |= {item.id for item in names if isinstance(item.ctx, ast.Store)}
            problems += sorted(({item.id for item in names} & private) - bound)
    assert problems == []


def test_every_ui_element_with_a_callback_is_a_named_variable() -> None:
    """marimo tracks a UI element reliably only through a cell-level variable."""
    problems: list[int] = []
    for cell in _cells():
        named: set[int] = set()
        for statement in cell.body:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and not target.id.startswith("_")
                for target in statement.targets
            ):
                named |= {id(node) for node in ast.walk(statement)}
        for node in ast.walk(cell):
            if (
                isinstance(node, ast.Call)
                and any(item.arg in {"on_click", "on_change"} for item in node.keywords)
                and id(node) not in named
            ):
                problems.append(node.lineno)
    assert problems == []


@pytest.mark.integration
@pytest.mark.skipif(importlib.util.find_spec("marimo") is None, reason="marimo is not installed")
def test_every_page_of_the_real_notebook_runs_headless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from astack.scaffold import create_project

    target = tmp_path / "study"
    create_project(
        target,
        project_name="Study",
        package_name="study",
        description="d",
        intended_use="u",
        tools=["dvc", "marimo"],
        lock=False,
    )
    (target / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    write_demo(target)
    _git_baseline(target)
    broken_record = target / ".astack/reviews/clean.json"
    broken_record.parent.mkdir(exist_ok=True)
    broken_record.write_bytes(b"\xff")
    # The bundle is flat, so replacing this one name swaps DVC for the demo's stages.
    with (target / ".astack/workflow.py").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nimport sys as _sys\n"
            "_sys.path.insert(0, " + repr(str(Path(__file__).parent)) + ")\n"
            "from review_demo import demo_stages as _demo_stages\n"
            "def _dvc_pipeline(root, targets=None, *, read_only=False, stages_only=False):\n"
            "    stages = [*_demo_stages(), {'name': 'report', 'cmd': 'python scripts/summary.py', "
            "'wdir': '.', 'dvcfile': 'dvc.yaml', 'deps': [{'path': 'scripts/summary.py'}], "
            "'outs': [{'path': 'evidence/report/out.json'}]}]\n"
            "    return {'status': {}, 'stages': stages}, ''\n"
            "def output_status(root):\n"
            "    return {'outputs': []}\n"
        )
    monkeypatch.chdir(target)
    spec = importlib.util.spec_from_file_location("review_notebook", "notebooks/review.py")
    assert spec is not None and spec.loader is not None
    units = ["overview", "report", "unit:report", "ingest", "clean", "velocity", f"shared:{SHARED}"]
    for unit in units:
        monkeypatch.setenv("ASTACK_REVIEW_PAGE", unit)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _, definitions = module.app.run()
        assert len(definitions["status"]["units"]) == 15
        rows = {row["unit"]: row for row in definitions["status"]["units"]}
        assert rows["clean"]["status"] == "invalid-record"
        assert rows["velocity"]["status"] == "unreviewed"
        if unit == "report":
            assert "report_blocks" in definitions and "page" not in definitions
            assert "report" not in definitions["get_opened"]()
        elif unit == "overview":
            assert "pipeline_map" in definitions and "page" not in definitions
        else:
            expected = unit.removeprefix("unit:")
            assert definitions["page"]["unit"] == expected
            assert "report_blocks" not in definitions
    # A failed write is reported, so its one-use click cannot leave the action bar dead.
    denied = PermissionError("read-only reviews folder")
    for name in ("record_review", "revoke_review"):
        monkeypatch.setattr(definitions["workflow"], name, Mock(side_effect=denied))
    assert definitions["sign_off"]("ingest", "checksum") is False
    assert definitions["get_refused"]() == {"unit": "ingest", "text": str(denied)}
    assert definitions["revoke"]("ingest", "reason") is None


def test_signing_and_moving_on_are_separate_controls() -> None:
    source = (NOTEBOOKS / "review.py").read_text(encoding="utf-8")
    assert "Sign off and next" not in source
    assert "mo.ui.checkbox" not in source
    assert source.count("review_page_view") == 1
    assert project_tool.REVIEW_BAR_STYLE["position"] == "sticky"
    assert project_tool.REVIEW_BAR_STYLE["top"] == "0"


def _click(token: str, action: str, reason: str = "") -> dict[str, str]:
    return {"token": token, "action": action, "reason": reason}


def test_a_token_works_once() -> None:
    gate = project_tool.ReviewActionGate()
    token = gate.issue(["sign", "next"])
    assert gate.accept(_click(token, "sign")) == ("sign", "")
    assert gate.accept(_click(token, "sign")) is None
    assert gate.accept(_click(token, "next")) is None


def test_a_click_from_an_earlier_render_is_refused() -> None:
    gate = project_tool.ReviewActionGate()
    old = gate.issue(["sign"])
    new = gate.issue(["sign"])
    assert gate.accept(_click(old, "sign")) is None
    assert gate.accept(_click(new, "sign")) == ("sign", "")


@pytest.mark.parametrize("change", ["navigate", "refresh", "cancel"])
def test_a_queued_click_is_refused_once_the_page_is_changing(change: str) -> None:
    gate = project_tool.ReviewActionGate()
    seen: list[object] = []
    setter = gate.voiding(seen.append)
    token = gate.issue(["sign", "confirm", "cancel"])
    setter({"navigate": "unit:next", "refresh": 1, "cancel": None}[change])
    assert seen and gate.accept(_click(token, "sign")) is None
    assert gate.accept(_click(token, "confirm", "old reason")) is None


def test_a_reason_from_a_stale_form_is_never_returned() -> None:
    gate = project_tool.ReviewActionGate()
    stale = gate.issue(["confirm", "cancel"])
    fresh = gate.issue(["confirm", "cancel"])
    assert gate.accept(_click(stale, "confirm", "abandoned")) is None
    assert gate.accept(_click(fresh, "confirm", "xyz")) == ("confirm", "xyz")
    other = gate.issue(["sign"])
    assert gate.accept(_click(other, "sign", "smuggled")) == ("sign", "")


@pytest.mark.parametrize(
    "event",
    [
        None,
        [],
        {},
        {"token": 1, "action": "sign", "reason": ""},
        {"action": "sign"},
        {"action": "sign", "reason": 3},
        {"action": "publish", "reason": ""},
    ],
)
def test_a_malformed_click_does_nothing_and_keeps_the_token(event: object) -> None:
    gate = project_tool.ReviewActionGate()
    token = gate.issue(["sign"])
    sent = (
        {**event, "token": event.get("token", token)}
        if isinstance(event, dict) and event
        else event
    )
    assert gate.accept(sent) is None
    assert gate.accept(_click(token, "sign")) == ("sign", "")


def test_the_action_bar_offers_only_what_applies() -> None:
    page = {"unit": "clean", "status": "signed off", "revoked": None, "blocked": False}
    status = {"to_sign_off": 3}
    view = project_tool.review_action_view
    signed = view(page, status, "next", None, None)
    assert signed["allowed"] == ["next", "revoke"]
    assert signed["sign"]["label"] == "✓ Signed off"
    assert signed["next"]["label"] == "Next → (3 left)"
    assert view(page, status, None, "clean", None)["allowed"] == ["revoke", "confirm", "cancel"]
    pending = {**page, "status": "needs sign-off"}
    assert view(pending, status, "next", "clean", None)["allowed"] == ["sign", "next"]
    assert view(pending, status, "next", None, None)["next"]["label"] == "Next → (2 left)"
    blocked = view({**pending, "blocked": True}, status, None, None, None)
    assert blocked["allowed"] == [] and blocked["sign"]["label"] == "Commit the code first"
    refused = {"unit": "clean", "text": "The code changed"}
    assert "refresh" in view(pending, status, None, None, refused)["allowed"]
    other = {"unit": "velocity", "text": "The code changed"}
    assert "refresh" not in view(pending, status, None, None, other)["allowed"]


def test_an_accepted_click_runs_only_its_own_action() -> None:
    calls: list[tuple[object, ...]] = []
    page = {"unit": "clean", "checksum": "abc"}

    def run(accepted: tuple[str, str] | None, next_step: str | None = "hotspots") -> None:
        project_tool.review_apply_action(
            accepted,
            page,
            next_step,
            lambda unit, checksum: calls.append(("sign", unit, checksum)),
            lambda unit, reason: calls.append(("revoke", unit, reason)),
            lambda route: calls.append(("page", route)),
            lambda unit: calls.append(("revoking", unit)),
            lambda: calls.append(("refresh",)),
        )

    run(None)
    assert calls == []
    run(("sign", ""))
    run(("next", ""))
    run(("next", ""), None)
    run(("confirm", "xyz"))
    assert calls == [
        ("sign", "clean", "abc"),
        ("page", "unit:hotspots"),
        ("revoke", "clean", "xyz"),
        ("revoking", None),
    ]


def test_the_action_script_drops_a_double_clicks_second_click() -> None:
    assert "event.detail <= 1" in project_tool.REVIEW_ACTIONS_ESM


def test_a_native_button_click_closes_the_action_bar_in_the_browser() -> None:
    source = project_tool.REVIEW_ACTIONS_ESM
    assert 'node.tagName === "MARIMO-BUTTON"' in source
    assert 'addEventListener("click", closeOnNative, true)' in source
    assert 'removeEventListener("click", closeOnNative, true)' in source


def _group(*commands: str) -> list[dict[str, Any]]:
    return [{"key": f"k{index}", "command": command} for index, command in enumerate(commands)]


def test_a_difference_without_a_flag_names_its_argument() -> None:
    items = _group("run a 1", "run a 1", "run a 2")
    assert project_tool._item_differences(items) == ["Argument 3 is `1`, except k2 (`2`)"]


def test_a_negative_number_is_not_taken_for_a_flag() -> None:
    items = _group("run -5 on", "run -5 on", "run -5 off")
    assert project_tool._item_differences(items) == ["Argument 3 is `on`, except k2 (`off`)"]


def test_a_flag_that_differs_between_items_does_not_name_the_value() -> None:
    items = _group("run --a 1", "run --b 2")
    notes = project_tool._item_differences(items)
    assert all(not note.startswith("`--") or " is `1`" not in note for note in notes[1:])
    assert notes[1].startswith("Argument 3 is")


def test_difference_html_escapes_values_and_keys() -> None:
    items = [
        {"key": "a", "command": "run --mode <b>x</b>"},
        {"key": "a", "command": "run --mode <b>x</b>"},
        {"key": "<i>k</i>", "command": "run --mode <img>"},
    ]
    shown = " ".join(project_tool._difference_html(items))
    assert "<b>" not in shown and "<img>" not in shown and "<i>" not in shown
    assert "&lt;img&gt;" in shown and "&lt;i&gt;k&lt;/i&gt;" in shown


def test_a_compact_table_escapes_everything_and_keeps_every_column() -> None:
    shown = project_tool.review_table_html(
        [{"<th>": "<script>x</script>", "a": None}, {"b": "  two  spaces"}]
    )
    assert "<script>" not in shown and "<th><th>" not in shown
    assert "&lt;script&gt;" in shown and "&lt;th&gt;" in shown
    assert shown.count("<th>") == 3 and "  two  spaces" in shown


def _code_page(declared: list[str], code: list[str], shared: list[str]) -> dict[str, Any]:
    return {
        "declared_code": [{"declared code": path} for path in declared],
        "code": [{"path": path} for path in code],
        "shared": [{"path": path, "unit": f"shared:{path}"} for path in shared],
    }


def test_declared_code_hides_only_when_every_path_is_on_the_page() -> None:
    check = project_tool._declared_code_on_page
    assert check(_code_page(["a.py", "lib.py"], ["a.py"], ["lib.py"]))
    assert not check(_code_page(["scripts"], ["scripts/a.py"], []))
    assert not check(_code_page(["a.py", "gone.py"], ["a.py"], []))
    assert not check(_code_page(["scripts/${key}.py"], ["scripts/north.py"], []))


def test_a_shared_link_shows_the_whole_path_it_stands_for() -> None:
    assert project_tool.review_link_label("shared:src/pkg/a.py") == "src/pkg/a.py"
    assert project_tool.review_link_label("velocity") == "velocity"


@pytest.mark.integration
def test_the_declared_table_goes_only_when_nothing_is_lost(demo: Path) -> None:
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    blocks = project_tool.review_sections(page)
    assert not any(
        block["type"] == "table" and "declared code" in block["rows"][0] for block in blocks
    )
    shown = {view["path"] for view in page["code"]} | {item["path"] for item in page["shared"]}
    assert {row["declared code"] for row in page["declared_code"]} <= shown


@pytest.mark.integration
def test_inputs_and_parameters_have_visible_headings(demo: Path) -> None:
    page = project_tool.review_page(demo, "velocity", _status(demo), {})
    shown = " ".join(
        block["html"] for block in project_tool.review_sections(page) if block["type"] == "html"
    )
    assert ">Inputs</h4>" in shown and ">Parameters</h4>" in shown and ">Command</h4>" in shown


def test_heading_levels_read_apart() -> None:
    size = {
        level: float(re.search(r"font-size:([0-9.]+)rem", style).group(1))  # type: ignore[union-attr]
        for level, style in project_tool.REVIEW_HEADING_STYLES.items()
    }
    assert size[2] > size[3] > size[4] > size[5]


def test_a_file_title_stays_one_escaped_block() -> None:
    title = project_tool.review_file_title("a<b>.py", "changed")
    assert title.startswith("<h4 ") and "<code>a&lt;b&gt;.py</code> · changed" in title


def test_the_legend_is_one_wrapped_row() -> None:
    row = {"status": "unreviewed", "kind": "step", "items": [{}]}
    shown = project_tool.review_legend_html({"units": [row]})
    assert "display:flex" in shown and "row wrap" in shown and "not signed off 1" in shown


def _luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    parts = [
        v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in (red, green, blue)
    ]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def test_white_text_is_readable_on_every_status_and_action_color() -> None:
    source = project_tool.REVIEW_ACTIONS_ESM
    actions = re.findall(r"button\.(?:primary|danger) \{ background: (#[0-9a-f]{6})", source)
    assert len(actions) == 2
    for color in [*project_tool.REVIEW_STATUS_COLORS.values(), *actions]:
        assert 1.05 / (_luminance(color) + 0.05) >= 4.5, color


def test_the_map_draws_status_text_without_fading_it() -> None:
    source = (NOTEBOOKS / "review_map.js").read_text(encoding="utf-8")
    assert '"font-size": SUB_SIZE, opacity' not in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_map_fits_down_to_a_readable_minimum(tmp_path: Path) -> None:
    copy_path = tmp_path / "review_map.mjs"
    copy_path.write_bytes((NOTEBOOKS / "review_map.js").read_bytes())
    script = (
        f"import {{ fitScale }} from {json.dumps(copy_path.as_uri())};"
        "console.log(JSON.stringify([2000, 900, 100].map((room) => fitScale(room, 1000))))"
    )
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script], check=True, capture_output=True, text=True
    )
    large, fitted, smallest = json.loads(done.stdout)
    assert large == 1 and fitted == 0.9
    assert smallest * 13 == pytest.approx(11)


def test_items_with_no_rows_never_claim_more_than_is_true() -> None:
    empty = project_tool.review_empty_items
    assert empty({"problem": "dvc failed", "units": []})[0] == "warn"
    assert empty({"problem": None, "units": []})[0] == "info"
    kind, text = empty({"problem": None, "units": [{"status": "reviewed"}]})
    assert kind == "success" and text.startswith("All done")


class _Table:
    """A stand-in for a marimo table that records how it was built."""

    def __init__(self, rows: list[Any], selection: str, format_mapping: Any = None) -> None:
        self.rows, self.selection, self.formats = rows, selection, format_mapping


def test_the_items_table_keeps_plain_unit_ids_and_draws_buttons_by_format() -> None:
    mo = SimpleNamespace(ui=SimpleNamespace(table=_Table))
    rows = [{"item": "clean", "status": "changed"}]
    drawn = project_tool.review_items_table(mo, rows, {"clean": "button"})
    assert drawn.rows == rows and list(drawn.rows[0]) == ["item", "status"]
    assert drawn.selection == "multi" and drawn.formats["item"]("clean") == "button"


def test_an_empty_items_table_has_no_format() -> None:
    mo = SimpleNamespace(ui=SimpleNamespace(table=_Table))
    assert project_tool.review_items_table(mo, [], {}).formats is None


def _collision_status() -> dict[str, Any]:
    def row(unit: str, count: int) -> dict[str, Any]:
        return {"unit": unit, "kind": "step", "items": [{}] * count, "status": "unreviewed"}

    shared = {"unit": "shared:src/a.py", "kind": "shared", "items": [], "status": "unreviewed"}
    return {"units": [row("clean", 2), row("clean × 2", 1), shared]}


def _buttons() -> tuple[Any, dict[str, Any]]:
    made: dict[str, Any] = {}

    def button(label: str, on_click: Any) -> Any:
        return SimpleNamespace(label=label, on_click=on_click)

    def dictionary(elements: dict[str, Any]) -> dict[str, Any]:
        made.update(elements)
        return elements

    return SimpleNamespace(ui=SimpleNamespace(button=button, dictionary=dictionary)), made


def test_a_group_and_a_step_with_its_display_name_open_their_own_pages() -> None:
    mo, made = _buttons()
    routes: list[str] = []
    rows = [{"item": "clean"}, {"item": "clean × 2"}, {"item": "shared:src/a.py"}]
    project_tool.review_item_links(mo, _collision_status(), rows, routes.append)
    assert len(made) == 3
    for unit in made:
        made[unit].on_click(None)
    assert routes == ["unit:clean", "unit:clean × 2", "unit:shared:src/a.py"]
    literal = project_tool.review_literal_label
    assert made["clean"].label == literal("clean × 2")
    assert made["clean × 2"].label == literal("clean × 2")
    assert made["shared:src/a.py"].label == literal("src/a.py")


def test_bulk_sign_off_covers_the_exact_unit_when_names_collide() -> None:
    status = _collision_status()
    opened = {"clean × 2": "seen"}
    picked = [{"item": "clean × 2"}]
    assert project_tool.review_bulk_plan(status, picked, opened) == ([("clean × 2", "seen")], [])
    picked = [{"item": "clean"}]
    assert project_tool.review_bulk_plan(status, picked, opened) == (
        [],
        ["clean × 2 (open it first)"],
    )


@pytest.mark.parametrize(
    "text",
    [
        "src/subsidence/__init__.py",
        "a<b>c</b>",
        "<img src=x onerror=alert(1)>",
        "$x$ :smile: `c` [a](b) *s* ~~t~~",
        "1. first",
        "# heading",
        "clean × 2",
        "Open a_b_c.py",
    ],
)
def test_a_button_label_survives_markdown_exactly(text: str) -> None:
    import html

    import marimo as mo

    rendered = mo.md(project_tool.review_literal_label(text)).text
    assert html.unescape(re.sub(r"<[^>]+>", "", rendered)) == text
    assert "<img" not in rendered and "<b>" not in rendered and "<code" not in rendered


def test_page_and_report_links_use_literal_labels() -> None:
    mo, made = _buttons()
    routes: list[str] = []
    units = ["shared:src/pkg/__init__.py", "shared:src/pkg/__init__.py", "a_b"]
    project_tool.review_link_buttons(mo, units, routes.append, prefix="Open ")
    assert list(made) == ["shared:src/pkg/__init__.py", "a_b"]
    literal = project_tool.review_literal_label
    assert made["a_b"].label == literal("Open a_b")
    assert made["shared:src/pkg/__init__.py"].label == literal("Open src/pkg/__init__.py")
    made["a_b"].on_click(None)
    assert routes == ["unit:a_b"]


def test_a_joined_flag_is_named_for_its_value() -> None:
    items = _group("run --verbose --water-mask=off", "run --verbose --water-mask=on")
    assert project_tool._item_differences(items) == ["`--water-mask` is `off`, except k1 (`on`)"]


@pytest.mark.parametrize(
    "commands",
    [
        ("run --verbose --a=1", "run --verbose --b=1"),
        ("run --verbose --on", "run --verbose --off"),
        ("run --verbose --a=1", "run --verbose plain"),
        ("run --a=1 x", "run --a=1 y"),
    ],
)
def test_an_ambiguous_flag_falls_back_to_the_argument_position(commands: tuple[str, str]) -> None:
    notes = project_tool._item_differences(_group(*commands))
    assert len(notes) == 1 and notes[0].startswith("Argument 3 is") and "except k" in notes[0]


def test_every_odd_item_stays_named_under_a_joined_flag() -> None:
    items = _group("run --m=a", "run --m=a", "run --m=b", "run --m=c")
    assert project_tool._item_differences(items) == ["`--m` is `a`, except k2 (`b`), k3 (`c`)"]


def test_a_value_after_a_flag_is_not_claimed_to_belong_to_it() -> None:
    items = _group("run --verbose input-a", "run --verbose input-b")
    assert project_tool._item_differences(items) == [
        "Value after `--verbose` is `input-a`, except k1 (`input-b`)"
    ]


def test_a_quoted_value_stays_whole() -> None:
    items = _group('run --label="deep water"', 'run --label="shallow water"')
    assert project_tool._item_differences(items) == [
        '`--label` is `"deep water"`, except k1 (`"shallow water"`)'
    ]


def test_spacing_inside_quotes_is_a_real_difference() -> None:
    notes = project_tool._item_differences(
        _group('run --label "deep water"', 'run --label "deep  water"')
    )
    assert len(notes) == 1 and '`"deep water"`' in notes[0] and '`"deep  water"`' in notes[0]
    shown = project_tool._difference_html(
        _group('run --label "deep water"', 'run --label "deep  water"')
    )
    assert '<code style="white-space:pre-wrap">&quot;deep  water&quot;</code>' in shown[0]


def test_a_quoting_difference_is_not_hidden_by_a_flag_difference() -> None:
    items = _group("run --mask=off '$mode'", "run --mask=on '$mode'", "run --mask=off $mode")
    assert project_tool._item_differences(items) == [
        "`--mask` is `off`, except k1 (`on`)",
        "Argument 3 is `'$mode'`, except k2 (`$mode`)",
    ]


def test_a_quoting_difference_is_not_hidden_by_differences_in_every_item() -> None:
    items = _group(
        "run --mask=off '$mode' blue", "run --mask=on '$mode' green", "run --mask=off $mode green"
    )
    assert project_tool._item_differences(items) == [
        "`--mask` is `off`, except k1 (`on`)",
        "Argument 3 is `'$mode'`, except k2 (`$mode`)",
        "Argument 4 is `green`, except k0 (`blue`)",
    ]


def test_a_quoted_command_separator_is_shown_as_written() -> None:
    items = _group("echo 'a;echo' done", "echo b;echo done")
    assert project_tool._item_differences(items) == [
        "Argument 2 is `'a;echo'`, except k1 (`b;echo`)"
    ]


@pytest.mark.parametrize(
    "commands",
    [
        ("echo echo second", "echo\necho second"),
        ("run --mask=off a", "run --mask=on\na"),
        ("run  --mask=off", "run --mask=off"),
    ],
)
def test_different_text_between_arguments_always_warns(commands: tuple[str, str]) -> None:
    notes = project_tool._item_differences(_group(*commands))
    assert notes[-1] == "Items run different commands: compare them in the table below."


def test_identical_commands_have_no_difference() -> None:
    assert project_tool._item_differences(_group("run --m='a b'", "run --m='a b'")) == []


@pytest.mark.parametrize("text", ["run same\\", "run 'open", 'run "open'])
def test_a_command_the_shell_cannot_parse_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        project_tool._shell_arguments(text)


def test_an_escaped_trailing_space_stays_in_its_argument() -> None:
    assert project_tool._shell_arguments("run same\\ ") == (["run", "same\\ "], ("", " ", ""))
    assert project_tool._shell_arguments("run same ") == (["run", "same"], ("", " ", " "))
    assert project_tool._item_differences(_group("run same\\ ", "run same ")) == [
        "Argument 2 is `same`, except k0 (`same\\ `)",
        "Items run different commands: compare them in the table below.",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "run --a='b c'  \"d\" e\\ f ''",
        "  a\n\tb  ",
        "",
        "a",
        "run same\\ ",
        "a\\ \tb\\\t c",
        "'a b'",
        "x  y ",
    ],
)
def test_shell_arguments_rebuild_the_exact_command(text: str) -> None:
    arguments, gaps = project_tool._shell_arguments(text)
    assert len(arguments) == len(shlex.split(text))
    rebuilt = "".join(gap + spelling for gap, spelling in zip(gaps, arguments, strict=False))
    assert rebuilt + gaps[-1] == text


def test_commands_that_cannot_be_parsed_still_warn() -> None:
    assert project_tool._item_differences(_group('run "open', "run closed")) == [
        "Items run different commands: compare them in the table below."
    ]


def test_a_value_quoted_one_way_is_named_against_the_other() -> None:
    assert project_tool._item_differences(_group("run '$x'", "run $x")) == [
        "Argument 2 is `$x`, except k0 (`'$x'`)"
    ]


def test_a_long_compact_table_scrolls_instead_of_pushing_code_down() -> None:
    shown = project_tool.review_table_html([{"item": str(index)} for index in range(40)])
    assert "max-height:320px" in shown and shown.count("<tr>") == 41


def test_the_facts_share_one_responsive_escaped_layout() -> None:
    shown = project_tool.review_facts_html(
        {
            "inputs": [{"input": "<i>a</i>"}],
            "params": [{"parameter": "b"}],
            "commands": ["python <run>.py"],
        }
    )
    assert "<i>" not in shown and "&lt;i&gt;a" in shown and "python &lt;run&gt;.py" in shown
    assert "display:grid" in shown and "auto-fit" in shown and "min(100%" in shown
    assert project_tool.review_facts_html({"inputs": [], "params": [], "commands": []}) == ""


def test_upstream_and_downstream_share_one_row() -> None:
    page: dict[str, Any] = {
        key: []
        for key in (
            "upstream",
            "downstream",
            "used_by",
            "notes",
            "run_changes",
            "code_moves",
            "inputs",
            "params",
            "differences",
            "items",
            "commands",
            "settings",
            "declared_code",
            "shared",
            "code",
            "param_usage",
            "outputs",
        )
    } | {"why": None, "fix": None, "kind": "step"}
    page["upstream"], page["downstream"] = ["a"], ["b"]
    rows = [block for block in project_tool.review_sections(page) if block["type"] == "link_row"]
    assert [group["title"] for group in rows[0]["groups"]] == ["Upstream", "Downstream"]


def test_a_long_file_title_scrolls_instead_of_wrapping() -> None:
    title = project_tool.review_file_title("a/" * 80 + "b.py", "changed", 3)
    assert title.startswith("<h3 ") and "white-space:nowrap" in title and "overflow-x:auto" in title


def test_the_only_pending_switch_can_rerun_its_own_cell() -> None:
    source = (NOTEBOOKS / "review.py").read_text(encoding="utf-8")
    assert "set_only_pending = mo.state(True, allow_self_loops=True)" in source
