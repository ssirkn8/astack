# ruff: noqa: F401 -- selected names are re-exported to focused test modules.
import atexit
import functools
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from astack.scaffold import create_project

spec = importlib.util.spec_from_file_location("project_tool", "workflow/project_tool.py")
assert spec is not None and spec.loader is not None
project_tool: Any = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = project_tool
spec.loader.exec_module(project_tool)


@pytest.fixture(autouse=True)
def no_ci_push_base(monkeypatch) -> None:
    monkeypatch.delenv(project_tool.RAW_BASE_SHA_ENV, raising=False)


@functools.cache
def _project_template() -> Path:
    """Scaffold once per test process. Each test still gets its own full copy."""
    parent = Path(tempfile.mkdtemp(prefix="astack-project-template-"))
    atexit.register(shutil.rmtree, parent, ignore_errors=True)
    template = parent / "project"
    create_project(
        template,
        project_name="Audit Study",
        package_name="audit_study",
        description="Exercise deterministic audit behavior.",
        intended_use="Validate audit behavior.",
        lock=False,
    )
    (template / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return template


def _project(tmp_path: Path) -> Path:
    target = tmp_path / "project"
    # shutil.copy keeps file modes but gives each test fresh modification times.
    shutil.copytree(_project_template(), target, symlinks=True, copy_function=shutil.copy)
    return target


def _set_capabilities(target: Path, capabilities: list[str]) -> None:
    path = target / ".astack/project.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("tools = []", f"tools = {json.dumps(capabilities)}"),
        encoding="utf-8",
    )


def _append_validation(target: Path, tier: str, commands: list[list[str]]) -> None:
    """Set one validation tier inside the generated project's single [validation] table."""
    import re

    config = target / ".astack/project.toml"
    text = config.read_text(encoding="utf-8")
    line = f"{tier} = {json.dumps(commands)}"
    if re.search(rf"(?m)^{tier} = ", text):
        text = re.sub(rf"(?m)^{tier} = .*$", lambda _: line, text, count=1)
    else:
        text = text.replace("[validation]\n", f"[validation]\n{line}\n", 1)
    config.write_text(text, encoding="utf-8")


def _declare_scientific_outputs(target: Path) -> tuple[Path, Path]:
    """Declare a complete, human-confirmed scientific output registry with real evidence."""
    from astack.output_contract import output_contract_summary

    config = target / ".astack/project.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[scientific_outputs]\npath = "docs/scientific-outputs.yml"\n'
        + 'evidence_path = "evidence/scientific-outputs.yml"\n',
        encoding="utf-8",
    )
    definitions = target / "docs/scientific-outputs.yml"
    progress_path = target / "evidence/scientific-outputs.yml"
    definitions.parent.mkdir(parents=True, exist_ok=True)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    definitions.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "research_question": "Does the declared analysis answer the research question?",
                "outputs": {
                    "depth-figure": {
                        "type": "figure",
                        "role": "headline",
                        "description": "Observed against modeled depth in meters.",
                        "validation": ["depth-qa"],
                    },
                    "clean-depths": {
                        "type": "dataset",
                        "role": "supplementary",
                        "description": "Cleaned depth observations.",
                    },
                },
                "validation": {"depth-qa": {"description": "Check units and sampling."}},
                "analyses": {
                    "main": {
                        "outputs": ["depth-figure", "clean-depths"],
                        "machine_evidence": [{"id": "run", "description": "Run record."}],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    def done(name: str) -> dict[str, Any]:
        evidence = target / f"reports/qa/{name}.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("evidence\n", encoding="utf-8")
        return {"status": "complete", "evidence": [f"reports/qa/{name}.txt"]}

    progress: dict[str, Any] = {
        "schema_version": 2,
        "outputs": {"depth-figure": done("figure"), "clean-depths": done("data")},
        "validation": {"depth-qa": done("qa")},
        "machine_evidence": {"main": {"run": done("run")}},
        "confirmations": {},
    }
    progress_path.write_text(yaml.safe_dump(progress), encoding="utf-8")
    progress["confirmations"]["main"] = {
        "definition_digest": output_contract_summary(target, "main")["definition_digest"],
        "evidence": "Human explicitly confirmed this definition in the test fixture.",
    }
    progress_path.write_text(yaml.safe_dump(progress), encoding="utf-8")
    return definitions, progress_path


def _set_progress(progress_path: Path, section: str, key: str, value: object) -> None:
    progress = yaml.safe_load(progress_path.read_text(encoding="utf-8"))
    progress[section][key] = value
    progress_path.write_text(yaml.safe_dump(progress), encoding="utf-8")


def _git_baseline(target: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
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


def _append_private_history_policy(
    target: Path, private: list[str], safe: list[str] | None = None
) -> None:
    config = target / ".astack/project.toml"
    lines = ["", "[privacy]", f"private_history_paths = {json.dumps(private)}"]
    if safe is not None:
        lines.append(f"private_history_safe_paths = {json.dumps(safe)}")
    config.write_text(
        config.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8"
    )


__all__ = [name for name in globals() if not name.startswith("__")]
