"""Per-stage read and write checks against dvc.yaml."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from project_tool_test_support import Path, _project, project_tool


@pytest.mark.parametrize(
    ("command", "traceable"),
    [
        ("python scripts/a.py", True),
        ("python3 -m pkg.run", True),
        ("uv run --locked python scripts/a.py", True),
        ("/usr/bin/python3.12 scripts/a.py", True),
        ("python a.py && python b.py", False),
        ("python a.py > out.txt", False),
        ("Rscript a.R", False),
        ("cp a b", False),
        (["python a.py", "python b.py"], False),
        ("python 'unterminated", False),
        ("python -S scripts/a.py", False),
        ("python -IS scripts/a.py", False),
        ("python -E scripts/a.py", False),
        ("uv run --locked python -u scripts/a.py", True),
        ("PYTHONPATH= python scripts/a.py", False),
        ("python -X utf8 -S scripts/a.py", False),
        ("python -Xutf8 -S scripts/a.py", False),
        ("python -W ignore -E scripts/a.py", False),
        ("python -X utf8 scripts/a.py", True),
        ("python --check-hash-based-pycs always -I a.py", False),
        ("python -uX utf8 -S a.py", False),
        ("python -uS a.py", False),
        ("python -uX utf8 a.py", True),
        ("python -Xdev -W ignore a.py", True),
        ("python -XS a.py", True),
    ],
)
def test_only_a_single_python_process_is_fully_traceable(command: object, traceable: bool) -> None:
    assert project_tool._traceable(command) is traceable


def test_stages_run_upstream_first() -> None:
    stages = [{"name": name} for name in ("report", "prepare", "train", "each@b", "each@a")]
    upstream = {
        "report": {"train"},
        "train": {"prepare"},
        "prepare": set(),
        "each@a": set(),
        "each@b": set(),
    }
    order = project_tool._stage_order(stages, upstream)
    assert order.index("prepare") < order.index("train") < order.index("report")


def _stage(**overrides: Any) -> dict[str, Any]:
    stage = {
        "name": "model",
        "cmd": "python scripts/model.py",
        "deps": [{"path": "scripts/model.py"}, {"path": "src/pkg"}, {"path": "data/raw"}],
        "outs": [{"path": "evidence/model/metrics.json"}, {"path": "reports/figures/model"}],
    }
    return stage | overrides


def test_writes_outside_declared_outs_are_undeclared(tmp_path: Path) -> None:
    changed = [
        "evidence/model/metrics.json",
        "reports/figures/model/roc.png",
        "evidence/model/side.json",
        "dvc.lock",
        "reports/figures/.gitignore",
    ]
    check = project_tool._stage_check(tmp_path, _stage(), changed, (set(), [], 1))
    assert check["undeclared_writes"] == ["evidence/model/side.json"]
    assert check["untraced"] is None


def test_reads_outside_declared_deps_are_undeclared(tmp_path: Path) -> None:
    reads = {
        str(tmp_path / "scripts/model.py"),
        str(tmp_path / "src/pkg/core.py"),
        str(tmp_path / "data/raw/in.csv"),
        str(tmp_path / "evidence/model/metrics.json"),
        str(tmp_path / "configs/model.yml"),
        str(tmp_path / ".venv/lib/python3.12/site-packages/pandas/__init__.py"),
        str(tmp_path / "src/pkg/__pycache__/core.cpython-312.pyc"),
        str(tmp_path / "dvc.yaml"),
        "/usr/lib/python3.12/os.py",
    }
    check = project_tool._stage_check(tmp_path, _stage(), [], (reads, [], 1))
    assert check["undeclared_reads"] == ["configs/model.yml"]


@pytest.mark.parametrize(
    ("stage", "spawns", "processes", "reason"),
    [
        (_stage(cmd="cp a b"), [], 0, "not a single Python process"),
        (_stage(), [], 0, "did not load"),
        (_stage(), ["/usr/bin/git"], 1, "/usr/bin/git"),
    ],
)
def test_untraceable_stages_are_disclosed(
    tmp_path: Path, stage: dict[str, Any], spawns: list[str], processes: int, reason: str
) -> None:
    check = project_tool._stage_check(tmp_path, stage, [], (set(), spawns, processes))
    assert reason in check["untraced"]


def test_a_bytecode_read_counts_as_its_source(tmp_path: Path) -> None:
    reads = {str(tmp_path / "src/helper/__pycache__/util.cpython-312.pyc")}
    check = project_tool._stage_check(tmp_path, _stage(), [], (reads, [], 1))
    assert check["undeclared_reads"] == ["src/helper/util.py"]


def test_writes_under_cache_folders_are_checked(tmp_path: Path) -> None:
    changed = [
        ".cache/side.json",
        "evidence/.cache/x.json",
        ".venv/lib/x.py",
        "src/__pycache__/a.pyc",
    ]
    check = project_tool._stage_check(tmp_path, _stage(), changed, (set(), [], 1))
    assert check["undeclared_writes"] == [".cache/side.json", "evidence/.cache/x.json"]


def test_a_rewrite_with_its_mtime_restored_is_still_a_change(tmp_path: Path) -> None:
    import os

    target = tmp_path / "evidence/side.json"
    target.parent.mkdir(parents=True)
    target.write_text("aaaa", encoding="utf-8")
    before = project_tool._snapshot(tmp_path)
    metadata = target.stat()
    target.write_text("bbbb", encoding="utf-8")
    os.utime(target, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert before["evidence/side.json"] != project_tool._snapshot(tmp_path)["evidence/side.json"]


def test_a_script_with_a_python_shebang_is_traceable(tmp_path: Path) -> None:
    script = tmp_path / "step01_prepare.py"
    script.write_text("#!/usr/bin/env python3\nprint(1)\n", encoding="utf-8")
    assert project_tool._traceable("./step01_prepare.py -i data", tmp_path)
    (tmp_path / "run.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    assert not project_tool._traceable("./run.sh", tmp_path)


def _lock(target: Path, md5: str) -> None:
    (target / "dvc.lock").write_text(
        f"schema: '2.0'\nstages:\n  model:\n    cmd: python m.py\n    outs:\n    - md5: {md5}\n",
        encoding="utf-8",
    )


def test_a_saved_check_counts_only_for_the_run_it_describes(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _lock(target, "a" * 32)
    finding = {"undeclared_writes": ["evidence/x.json"], "undeclared_reads": [], "untraced": None}
    project_tool._save_checks(target, {"model": finding})
    saved = project_tool.dependency_checks(target)
    assert saved["model"]["undeclared_writes"] == ["evidence/x.json"]
    _lock(target, "b" * 32)  # A later `dvc repro` outside astack.
    assert project_tool.dependency_checks(target) == {}


def test_undeclared_reads_and_writes_are_audit_findings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _lock(target, "a" * 32)
    project_tool._save_checks(
        target,
        {
            "model": {
                "undeclared_writes": ["evidence/x.json"],
                "undeclared_reads": ["configs/m.yml"],
                "untraced": "it starts programs whose reads are not seen: /usr/bin/git",
            }
        },
    )
    findings: list[Any] = []
    project_tool._check_dependency_checks(target, findings)
    assert [(item.priority, item.check) for item in findings] == [
        ("P1", "undeclared-write"),
        ("P1", "undeclared-read"),
        ("P2", "untraced-stage"),
    ]


def test_an_untrusted_stage_is_unknown_and_so_is_everything_below_it(tmp_path: Path) -> None:
    stages = [
        {"name": "prepare", "deps": [], "outs": [{"path": "evidence/a.json"}]},
        {
            "name": "analyze",
            "deps": [{"path": "evidence/a.json"}],
            "outs": [{"path": "evidence/b.json"}],
        },
    ]
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/a.json").write_text("{}", encoding="utf-8")
    states = project_tool._stage_states(
        tmp_path, stages, {}, {"prepare": "its stage wrote undeclared files: evidence/x.json"}
    )
    assert states["prepare"][0] == "unknown"
    assert states["analyze"] == ("unknown", "upstream stage prepare is unknown")


def test_the_trace_hook_records_reads_and_skips_dvc(tmp_path: Path) -> None:
    import subprocess
    import sys

    trace = tmp_path / "trace.jsonl"
    environment = project_tool._trace_environment(tmp_path, trace)
    data = tmp_path / "data.txt"
    data.write_text("x", encoding="utf-8")
    script = (
        f"open({str(data)!r}).read(); open({str(tmp_path / 'out.txt')!r}, 'w').write('y'); "
        f"open({str(tmp_path / 'both.txt')!r}, 'a+').read()"
    )
    subprocess.run([sys.executable, "-c", script], env={**environment, "PATH": ""}, check=True)
    reads, untraced, processes = project_tool._read_trace(trace)
    assert str(data) in reads
    assert str(tmp_path / "both.txt") in reads
    assert str(tmp_path / "out.txt") not in reads
    assert processes == 1
    assert untraced == []
    fake_dvc = tmp_path / "dvc"
    fake_dvc.write_text(f"open({str(data)!r}).read()\n", encoding="utf-8")
    trace.unlink()
    subprocess.run([sys.executable, str(fake_dvc)], env={**environment, "PATH": ""}, check=True)
    assert project_tool._read_trace(trace) == (set(), [], 0)


def test_a_stage_run_outside_astack_is_reported_unchecked(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _lock(target, "a" * 32)
    findings: list[Any] = []
    project_tool._check_dependency_checks(target, findings)
    assert [(item.priority, item.check, item.evidence) for item in findings] == [
        ("P2", "unchecked-stage", "model")
    ]


def test_a_python_child_without_the_hook_is_untraced(tmp_path: Path) -> None:
    import subprocess
    import sys

    trace = tmp_path / "trace.jsonl"
    environment = project_tool._trace_environment(tmp_path, trace)
    child = f"import subprocess, sys; subprocess.run([sys.executable, {'-S'!r}, '-c', 'pass'])"
    subprocess.run([sys.executable, "-c", child], env={**environment, "PATH": ""}, check=True)
    _, untraced, processes = project_tool._read_trace(trace)
    assert processes == 1
    assert untraced == ["a Python child process that cannot load the hook"]
    trace.unlink()
    child = "import subprocess, sys; subprocess.run([sys.executable, '-c', 'pass'])"
    subprocess.run([sys.executable, "-c", child], env={**environment, "PATH": ""}, check=True)
    assert project_tool._read_trace(trace)[1:] == ([], 2)


def test_project_ignore_patterns_cover_any_toolchain_scratch_files(tmp_path: Path) -> None:
    changed = [".Rhistory", "Rplots.pdf", "work/tmp/a.log", "evidence/side.json"]
    reads = {str(tmp_path / ".Rprofile"), str(tmp_path / "configs/model.yml")}
    check = project_tool._stage_check(
        tmp_path, _stage(), changed, (reads, [], 1), [".Rhistory", "Rplots.pdf", "work/tmp/*"]
    )
    assert check["undeclared_writes"] == ["evidence/side.json"]
    assert check["undeclared_reads"] == [".Rprofile", "configs/model.yml"]
    check = project_tool._stage_check(tmp_path, _stage(), [], (reads, [], 1), [".Rprofile"])
    assert check["undeclared_reads"] == ["configs/model.yml"]


@pytest.mark.parametrize("value", ['".Rhistory"', "[1]", '[""]'])
def test_malformed_check_ignore_is_refused(value: str) -> None:
    with pytest.raises(RuntimeError, match="check_ignore"):
        project_tool.check_ignore_patterns({"reproduction": {"check_ignore": eval(value)}})


def test_an_untraced_stage_keeps_its_dvc_label_and_is_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    target = _project(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    (target / "evidence/model").mkdir(parents=True)
    (target / "evidence/model/metrics.json").write_text('{"auc": 0.82}', encoding="utf-8")
    _lock(target, "a" * 32)
    untraced = "its command is not a single Python process, so some reads are not seen"
    project_tool._save_checks(
        target, {"model": {"undeclared_writes": [], "undeclared_reads": [], "untraced": untraced}}
    )
    stage = {
        "name": "model",
        "frozen": False,
        "deps": [{"path": "scripts/model.R"}],
        "outs": [{"path": "evidence/model/metrics.json", "dir": False}],
    }
    payload = json.dumps({"status": {}, "stages": [stage]})
    original = project_tool._run

    def run(args: list[str], **kwargs: Any) -> Any:
        if args[-1] == project_tool.DVC_PIPELINE_PROBE:
            import subprocess

            return subprocess.CompletedProcess(args, 0, payload, "")
        return original(args, **kwargs)

    monkeypatch.setattr(project_tool, "_run", run)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("tools = []", 'tools = ["dvc"]')
        + '\n[reproduction]\nengine = "dvc"\n',
        encoding="utf-8",
    )
    (target / "dvc.yaml").write_text("stages: {}\n", encoding="utf-8")
    entry = next(
        item
        for item in project_tool.output_status(target)["outputs"]
        if item["path"] == "evidence/model/metrics.json"
    )
    assert (entry["label"], entry["dependency_check"]) == ("current", "untraced")
    gap: dict[str, Any] = {
        "undeclared_writes": [],
        "undeclared_reads": [],
        "untraced": None,
        "inputs_locked": False,
        "lock_gaps": ["evidence/x.db (symlink)"],
    }
    project_tool._save_checks(target, {"model": gap})
    entry = next(
        item
        for item in project_tool.output_status(target)["outputs"]
        if item["path"] == "evidence/model/metrics.json"
    )
    assert (entry["label"], entry["dependency_check"]) == ("current", "inputs-not-locked")
    report = target / "reports/study.md"
    report.parent.mkdir(exist_ok=True)
    report.write_text(
        "---\ntitle: S\nstatus: final\n---\n\nAUC [0.82](../evidence/model/metrics.json#/auc)\n",
        encoding="utf-8",
    )
    result = project_tool.report_check(target, [])
    assert result["status"] == "passed"
    assert [note["kind"] for note in result["reports"][0]["evidence_notes"]] == [
        "reads-not-checked"
    ]


def _traced(tmp_path: Path, script: str) -> tuple[list[str], int]:
    import subprocess
    import sys

    trace = tmp_path / "trace.jsonl"
    trace.unlink(missing_ok=True)
    environment = project_tool._trace_environment(tmp_path, trace)
    program = tmp_path / "stage.py"
    program.write_text(script, encoding="utf-8")
    subprocess.run([sys.executable, str(program)], env={**environment, "PATH": ""}, check=True)
    _, untraced, processes = project_tool._read_trace(trace)
    return untraced, processes


def test_a_multiprocessing_worker_cannot_hide_an_unhooked_child(tmp_path: Path) -> None:
    untraced, _ = _traced(
        tmp_path,
        "import multiprocessing, subprocess, sys\n"
        "def work():\n    pass\n"
        "if __name__ == '__main__':\n"
        "    multiprocessing.set_start_method('spawn')\n"
        "    p = multiprocessing.Process(target=work); p.start(); p.join()\n"
        "    subprocess.run([sys.executable, '-S', '-c', 'pass'])\n",
    )
    assert untraced == ["a Python child process that cannot load the hook"]


def test_multiprocessing_workers_alone_stay_traced(tmp_path: Path) -> None:
    untraced, processes = _traced(
        tmp_path,
        "import multiprocessing\n"
        "def work():\n    pass\n"
        "if __name__ == '__main__':\n"
        "    multiprocessing.set_start_method('spawn')\n"
        "    p = multiprocessing.Process(target=work); p.start(); p.join()\n",
    )
    assert untraced == []
    assert processes >= 2


def test_a_child_with_a_stripped_environment_is_untraced(tmp_path: Path) -> None:
    untraced, _ = _traced(
        tmp_path,
        "import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'pass'], env={})\n",
    )
    assert untraced == ["a Python child process that cannot load the hook"]


def test_a_python_re_exec_keeps_the_trace(tmp_path: Path) -> None:
    untraced, _ = _traced(
        tmp_path,
        "import os, sys\n"
        "if len(sys.argv) == 1:\n"
        "    os.execv(sys.executable, [sys.executable, __file__, 'again'])\n",
    )
    assert untraced == []


def test_changing_check_ignore_invalidates_saved_checks(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _lock(target, "a" * 32)
    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + '\n[reproduction]\ncheck_ignore = ["secret.txt"]\n',
        encoding="utf-8",
    )
    clean: dict[str, Any] = {"undeclared_writes": [], "undeclared_reads": [], "untraced": None}
    project_tool._save_checks(target, {"model": clean})
    assert "model" in project_tool.dependency_checks(target)
    config.write_text(
        config.read_text(encoding="utf-8").replace('check_ignore = ["secret.txt"]', ""),
        encoding="utf-8",
    )
    assert project_tool.dependency_checks(target) == {}


def test_a_child_inheriting_a_stripped_environment_is_untraced(tmp_path: Path) -> None:
    untraced, _ = _traced(
        tmp_path,
        "import os, subprocess, sys\n"
        "os.environ.pop('PYTHONPATH')\n"
        "subprocess.run([sys.executable, '-c', 'pass'])\n",
    )
    assert untraced == ["a Python child process that cannot load the hook"]


@pytest.mark.parametrize("mapping", ["os.environ", "os.environb", "dict(os.environ)"])
def test_environment_mappings_that_keep_the_hook_stay_traced(tmp_path: Path, mapping: str) -> None:
    untraced, processes = _traced(
        tmp_path,
        "import os, subprocess, sys\n"
        f"subprocess.run([sys.executable, '-c', 'pass'], env={mapping})\n",
    )
    assert untraced == []
    assert processes == 2


def test_a_clustered_hook_disabling_option_is_untraced(tmp_path: Path) -> None:
    untraced, _ = _traced(
        tmp_path,
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-uX', 'utf8', '-S', '-c', 'pass'])\n",
    )
    assert untraced == ["a Python child process that cannot load the hook"]


def _derived(tmp_path: Path) -> Path:
    for relative in (
        "data/processed/model/grid.parquet",
        "data/processed/model/own.parquet",
        "data/interim/model/tmp.nc",
        "evidence/model/metrics.json",
        "artifacts/model/scratch.log",
        "data/raw/in.tif",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (tmp_path / "data/processed/model/link.parquet").symlink_to("grid.parquet")
    return tmp_path


def test_undeclared_derived_files_are_locked_and_restored(tmp_path: Path) -> None:
    import os

    root = _derived(tmp_path)
    stage = {
        "deps": [{"path": "data/interim/model"}],
        "outs": [{"path": "data/processed/model/own.parquet"}],
    }
    locked, gaps = project_tool._lock_inputs(root, stage, ["artifacts/*/scratch.log"])
    assert locked == ["data/processed/model/grid.parquet", "evidence/model/metrics.json"]
    assert gaps == ["data/processed/model/link.parquet (symlink)"]
    assert not os.access(root / "data/processed/model/grid.parquet", os.R_OK)
    assert os.access(root / "data/processed/model/own.parquet", os.R_OK)
    assert os.access(root / "data/raw/in.tif", os.R_OK)
    assert (root / ".cache/input-lock.json").exists()
    project_tool._restore_locks(root)
    assert os.access(root / "data/processed/model/grid.parquet", os.R_OK)
    assert not (root / ".cache/input-lock.json").exists()


def test_a_run_interrupted_while_locked_is_repaired(tmp_path: Path) -> None:
    import os

    root = _derived(tmp_path)
    (root / "data/processed/model/link.parquet").unlink()
    project_tool._lock_inputs(root, {"deps": [], "outs": []}, [])
    assert not os.access(root / "evidence/model/metrics.json", os.R_OK)
    # A crash left the journal behind. The next run restores before doing anything else.
    project_tool._restore_locks(root)
    assert os.access(root / "evidence/model/metrics.json", os.R_OK)
    project_tool._restore_locks(root)  # Nothing left to do.


def test_a_failed_stage_names_the_locked_file_it_read() -> None:
    locked = ["data/processed/model/grid.parquet", "evidence/model/metrics.json"]
    output = "OSError: [Errno 13] Permission denied: 'data/processed/model/grid.parquet'"
    message = project_tool._locked_read_message("analyze", locked, output)
    assert message is not None
    assert "data/processed/model/grid.parquet" in message
    assert "evidence/model/metrics.json" not in message
    assert project_tool._locked_read_message("analyze", locked, "ValueError: bad") is None


def test_unlocked_inputs_are_disclosed(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _lock(target, "a" * 32)
    clean: dict[str, Any] = {
        "undeclared_writes": [],
        "undeclared_reads": [],
        "untraced": None,
        "inputs_locked": False,
        "lock_gaps": ["evidence/x.db (symlink)"],
    }
    project_tool._save_checks(target, {"model": clean})
    findings: list[Any] = []
    project_tool._check_dependency_checks(target, findings)
    assert [(item.priority, item.check) for item in findings] == [("P2", "inputs-not-locked")]


def test_a_hard_link_is_never_changed(tmp_path: Path) -> None:
    import os

    root = tmp_path
    allowed = root / "evidence/model/allowed.json"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("x", encoding="utf-8")
    os.link(allowed, root / "evidence/model/alias.json")
    stage = {"deps": [{"path": "evidence/model/allowed.json"}], "outs": []}
    locked, gaps = project_tool._lock_inputs(root, stage, [])
    assert locked == []
    assert gaps == ["evidence/model/alias.json (hard link)"]
    assert os.access(allowed, os.R_OK)


def test_a_symlinked_derived_folder_is_a_gap(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.db").write_text("x", encoding="utf-8")
    (tmp_path / "evidence").symlink_to(elsewhere, target_is_directory=True)
    locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    assert (locked, gaps) == ([], ["evidence (symlinked folder)"])


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_a_file_an_acl_keeps_readable_is_a_gap(tmp_path: Path) -> None:
    import getpass
    import os
    import subprocess

    target = tmp_path / "evidence/model/acl.json"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    subprocess.run(["chmod", "+a", f"user:{getpass.getuser()} allow read", str(target)], check=True)
    locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    assert locked == ["evidence/model/acl.json"]
    assert gaps == ["evidence/model/acl.json (still readable, for example through an ACL)"]
    project_tool._restore_locks(tmp_path)
    assert os.access(target, os.R_OK)


@pytest.mark.parametrize("journal", ["[]", '{"a": 420}', '{"a": {"mode": 420}}', "not json"])
def test_a_malformed_journal_is_kept_and_reported(tmp_path: Path, journal: str) -> None:
    path = tmp_path / ".cache/input-lock.json"
    path.parent.mkdir()
    path.write_text(journal, encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed"):
        project_tool._restore_locks(tmp_path)
    assert path.exists()


def test_restore_leaves_a_replaced_file_alone(tmp_path: Path) -> None:
    import os
    import stat

    target = tmp_path / "evidence/model/metrics.json"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    target.unlink()
    target.write_text("new", encoding="utf-8")
    os.chmod(target, 0o600)
    skipped = project_tool._restore_locks(tmp_path)
    assert skipped == ["evidence/model/metrics.json"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not (tmp_path / ".cache/input-lock.json").exists()


def test_a_permission_change_that_fails_is_a_gap_and_the_rest_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    for name in ("a.json", "b.json"):
        path = tmp_path / "evidence/model" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    real_chmod = os.chmod

    def chmod(path: Any, mode: int, **kwargs: Any) -> None:
        if str(path).endswith("b.json") and not mode & 0o444:
            raise PermissionError("denied")
        real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", chmod)
    locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    assert locked == ["evidence/model/a.json"]
    assert gaps == ["evidence/model/b.json (could not change permissions)"]
    monkeypatch.setattr(os, "chmod", real_chmod)
    project_tool._restore_locks(tmp_path)
    assert os.access(tmp_path / "evidence/model/a.json", os.R_OK)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_a_file_readable_only_through_an_acl_is_a_gap(tmp_path: Path) -> None:
    import getpass
    import os
    import subprocess

    target = tmp_path / "evidence/model/acl-only.json"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    os.chmod(target, 0o200)
    subprocess.run(["chmod", "+a", f"user:{getpass.getuser()} allow read", str(target)], check=True)
    locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    assert locked == []
    assert gaps == ["evidence/model/acl-only.json (still readable, for example through an ACL)"]


def test_a_symlinked_ancestor_folder_is_never_entered(tmp_path: Path) -> None:
    import os

    external = tmp_path / "external"
    (external / "processed/model").mkdir(parents=True)
    shared = external / "processed/model/shared.parquet"
    shared.write_text("x", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "data").symlink_to(external, target_is_directory=True)
    locked, gaps = project_tool._lock_inputs(project, {"deps": [], "outs": []}, [])
    assert locked == []
    assert "data/processed (through symlinked folder data)" in gaps
    assert os.access(shared, os.R_OK)


def test_a_restore_failure_keeps_the_stage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Could not restore read access on: evidence/x.json.")

    stage = {"name": "model", "frozen": False, "cmd": "python m.py", "deps": [], "outs": []}
    pipeline = {"status": {"model": ["changed"]}, "stages": [stage], "selected": None}
    monkeypatch.setattr(project_tool, "_dvc_pipeline", lambda root, targets=None: (pipeline, ""))
    monkeypatch.setattr(
        project_tool,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "ValueError: bad input"),
    )
    real_restore = project_tool._restore_locks
    calls = {"count": 0}

    def restore(root: Path) -> list[str]:
        calls["count"] += 1
        if calls["count"] > 1:  # The first call is the start-of-run repair.
            fail()
        return list(real_restore(root))

    monkeypatch.setattr(project_tool, "_restore_locks", restore)
    target = _project(tmp_path)
    with pytest.raises(RuntimeError) as raised:
        project_tool.run_checked_dvc_stages(target, force=True, targets=[], environment={})
    assert "Could not restore read access" in str(raised.value)
    assert "ValueError: bad input" in str(raised.value)


def test_a_folder_that_cannot_be_listed_is_a_gap(tmp_path: Path) -> None:
    import os

    folder = tmp_path / "evidence/model"
    folder.mkdir(parents=True)
    (folder / "hidden.json").write_text("x", encoding="utf-8")
    os.chmod(folder, 0o111)
    try:
        locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    finally:
        os.chmod(folder, 0o755)
    assert locked == []
    assert gaps == ["evidence/model (folder could not be listed)"]
    declared = {"deps": [{"path": "evidence/model"}], "outs": []}
    os.chmod(folder, 0o111)
    try:
        assert project_tool._lock_inputs(tmp_path, declared, []) == ([], [])
    finally:
        os.chmod(folder, 0o755)


def test_a_folder_that_cannot_be_entered_is_a_gap_not_a_crash(tmp_path: Path) -> None:
    import os

    folder = tmp_path / "evidence/model"
    folder.mkdir(parents=True)
    (folder / "hidden.json").write_text("x", encoding="utf-8")
    os.chmod(folder, 0o444)
    try:
        locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    finally:
        os.chmod(folder, 0o755)
    assert locked == []
    assert gaps == ["evidence/model/hidden.json (could not be inspected)"]


def test_a_derived_root_under_an_unenterable_folder_is_a_gap(tmp_path: Path) -> None:
    import os

    (tmp_path / "data/interim/model").mkdir(parents=True)
    os.chmod(tmp_path / "data", 0o444)
    try:
        locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    finally:
        os.chmod(tmp_path / "data", 0o755)
    assert locked == []
    assert "data/interim (could not be inspected)" in gaps
    assert "data/processed (could not be inspected)" in gaps


def test_an_unreachable_locked_file_keeps_the_journal(tmp_path: Path) -> None:
    import os

    target = tmp_path / "evidence/model/metrics.json"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    os.chmod(target.parent, 0o644)
    try:
        with pytest.raises(RuntimeError, match="evidence/model/metrics.json"):
            project_tool._restore_locks(tmp_path)
        assert (tmp_path / ".cache/input-lock.json").exists()
    finally:
        os.chmod(target.parent, 0o755)
    project_tool._restore_locks(tmp_path)
    assert os.access(target, os.R_OK)
    assert not (tmp_path / ".cache/input-lock.json").exists()


def test_an_unwritable_journal_locks_nothing_and_reports_gaps(tmp_path: Path) -> None:
    import os

    target = tmp_path / "evidence/model/metrics.json"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    (tmp_path / ".cache").mkdir()
    os.chmod(tmp_path / ".cache", 0o555)
    try:
        locked, gaps = project_tool._lock_inputs(tmp_path, {"deps": [], "outs": []}, [])
    finally:
        os.chmod(tmp_path / ".cache", 0o755)
    assert locked == []
    assert gaps == ["evidence/model/metrics.json (lock journal could not be written)"]
    assert os.access(target, os.R_OK)


def test_an_uninspectable_folder_does_not_stop_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess

    target = _project(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    (target / "data/interim/model").mkdir(parents=True, exist_ok=True)
    stage = {"name": "model", "frozen": False, "cmd": "python m.py", "deps": [], "outs": []}
    pipeline = {"status": {"model": ["changed"]}, "stages": [stage], "selected": None}
    monkeypatch.setattr(project_tool, "_dvc_pipeline", lambda root, targets=None: (pipeline, ""))
    ran: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> Any:
        ran.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(project_tool, "_run", run)
    os.chmod(target / "data", 0o444)
    try:
        result = project_tool.run_checked_dvc_stages(target, force=True, targets=[], environment={})
    finally:
        os.chmod(target / "data", 0o755)
    assert any("repro" in args for args in ran)
    check = result["stages"]["model"]
    assert check["inputs_locked"] is False
    assert "data/interim (could not be inspected)" in check["lock_gaps"]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACLs")
def test_a_folder_whose_metadata_is_hidden_is_a_write_gap(tmp_path: Path) -> None:
    import getpass
    import subprocess

    hidden = tmp_path / "configs/hidden"
    hidden.mkdir(parents=True)
    (hidden / "side.txt").write_text("x", encoding="utf-8")
    rule = f"user:{getpass.getuser()} deny readattr"
    subprocess.run(["chmod", "+a", rule, str(hidden)], check=True)
    gaps: list[str] = []
    try:
        project_tool._snapshot(tmp_path, gaps)
    finally:
        subprocess.run(["chmod", "-N", str(hidden)], check=True)
    assert any(gap.startswith("configs/hidden") for gap in gaps), gaps


def test_a_symlink_out_of_the_project_is_a_write_gap(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "data/interim").mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (project / "data/interim/shared").symlink_to(external, target_is_directory=True)
    (project / "data/interim/inside").symlink_to(project / "data", target_is_directory=True)
    raw_target = tmp_path / "shared-raw"
    raw_target.mkdir()
    (project / "data/raw").symlink_to(raw_target, target_is_directory=True)
    gaps: list[str] = []
    project_tool._snapshot(project, gaps)
    assert gaps == [
        "data/interim/shared (symlink out of the project; writes through it are not seen)"
    ]
