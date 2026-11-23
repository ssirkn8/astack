"""Combined consumer scenarios, not live human or scientific-validity certification."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from astack.scaffold import create_project

pytestmark = pytest.mark.integration


def _command(root: Path, *args: str) -> str:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        args, cwd=root, env=env, capture_output=True, text=True, check=True
    ).stdout


def _summary(root: Path) -> dict[str, Any]:
    # Each CLI process reconstructs the contract from the fresh consumer's own files.
    result = json.loads(
        _command(
            root, sys.executable, ".astack/workflow.py", "output-contract", "--analysis", "plot"
        )
    )
    assert isinstance(result, dict)
    return result


def _consumer(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    create_project(
        source,
        project_name="Acceptance",
        package_name="acceptance",
        description="Synthetic integration acceptance",
        intended_use="Workflow testing only",
        lock=False,
    )
    with (source / ".astack/project.toml").open("a") as stream:
        stream.write(
            '\n[scientific_outputs]\npath = "docs/scientific-outputs.yml"\n'
            'evidence_path = "evidence/scientific-outputs.yml"\n'
        )
    (source / "evidence").mkdir(exist_ok=True)
    definition = {
        "schema_version": 2,
        "research_question": "Does the synthetic identity comparison reproduce its inputs?",
        "outputs": {
            "comparison": {
                "type": "figure",
                "role": "headline",
                "description": "Synthetic identity comparison, not a scientific conclusion",
                "validation": ["identity-qa"],
                "communication": {"key_message": "Synthetic identity only"},
            },
            "future": {"type": "table", "role": "supplementary", "description": "Future work"},
        },
        "validation": {"identity-qa": {"description": "Verify exact synthetic output bytes"}},
        "analyses": {"plot": {"outputs": ["comparison"]}},
    }
    (source / "docs/scientific-outputs.yml").write_text(yaml.safe_dump(definition))
    (source / "evidence/scientific-outputs.yml").write_text(
        yaml.safe_dump({"schema_version": 2, "outputs": {}, "validation": {}, "confirmations": {}})
    )
    _command(source, "git", "init", "-b", "main")
    _command(source, "git", "add", ".")
    _command(
        source,
        "git",
        "-c",
        "user.name=Acceptance Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "Synthetic consumer fixture",
    )
    clone = tmp_path / "fresh-consumer"
    _command(tmp_path, "git", "clone", "--no-local", str(source), str(clone))
    return clone


def test_fresh_consumer_scientific_evidence_and_definition_reconfirmation(tmp_path: Path) -> None:
    root = _consumer(tmp_path)
    initial = _summary(root)
    assert initial["confirmation"] == "unconfirmed"
    assert initial["analysis"] == "plot"
    assert [item["id"] for item in initial["required_outputs"]] == ["comparison"]

    # Simulated confirmation tests binding only. It is never recorded as live owner consent.
    evidence_path = root / "evidence/scientific-outputs.yml"
    progress = yaml.safe_load(evidence_path.read_text())
    progress["confirmations"]["plot"] = {
        "definition_digest": initial["definition_digest"],
        "evidence": "SIMULATED test confirmation, not human acceptance evidence",
    }
    output = root / "evidence/comparison.svg"
    output.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Synthetic only</text></svg>\n')
    qa = root / "evidence/qa.txt"
    qa.write_text("Exact synthetic SVG bytes checked by the integration fixture\n")
    progress["outputs"]["comparison"] = {
        "status": "complete",
        "evidence": ["evidence/comparison.svg"],
    }
    progress["validation"]["identity-qa"] = {"status": "complete", "evidence": ["evidence/qa.txt"]}
    evidence_path.write_text(yaml.safe_dump(progress))
    complete = _summary(root)
    assert complete["status"] == "complete"
    assert complete["confirmation"] == "confirmed"
    assert complete["definition_digest"] == initial["definition_digest"]
    assert complete["blockers"] == []

    # A new process must not accept status alone when actual evidence disappears.
    output.unlink()
    missing = _summary(root)
    assert missing["status"] != "complete"
    assert missing["blockers"]
    output.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Synthetic only</text></svg>\n')
    definition_path = root / "docs/scientific-outputs.yml"
    definition = yaml.safe_load(definition_path.read_text())
    definition["outputs"]["comparison"]["description"] = "Changed headline interpretation"
    definition_path.write_text(yaml.safe_dump(definition))
    changed = _summary(root)
    assert changed["confirmation"] == "reconfirmation_required"
    assert changed["definition_digest"] != initial["definition_digest"]
    assert changed["blockers"]


def test_fresh_consumer_has_local_runtime_but_no_reconstructed_authority(tmp_path: Path) -> None:
    root = _consumer(tmp_path)
    # Inspect import provenance, not merely generated-file presence.
    probe = (
        "import pathlib,sys; sys.path.insert(0,str(pathlib.Path('.astack').resolve())); "
        "import output_contract,git_state; "
        "assert pathlib.Path(output_contract.__file__).parent == "
        "pathlib.Path('.astack').resolve(); "
        "assert pathlib.Path(git_state.__file__).parent == pathlib.Path('.astack').resolve(); "
        "print('vendored-runtime-local')"
    )
    assert _command(root, sys.executable, "-c", probe).strip() == "vendored-runtime-local"
    assert not (root / ".opencode/commands/ds-run.md").exists()
    assert not (root / ".opencode/skills/ds-run").exists()
    assert not (root / ".astack/work-unit.json").exists()
    assert not (root / ".astack/worktrees.py").exists()
    assert _summary(root)["confirmation"] == "unconfirmed"
    # Git checkout did not invent completion, receipt, or scientific confirmation.
    assert _command(root, "git", "status", "--porcelain") == ""
