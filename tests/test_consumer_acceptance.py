"""Independent consumers exercise installed tooling without this repository's dependencies."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from astack import installation


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node unavailable, consumer acceptance unverified"
)
def test_installed_general_node_runtime_without_python_site_packages(tmp_path: Path) -> None:
    from test_installation import _commit, _repo

    root = _repo(tmp_path)
    original = {
        p.relative_to(root): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    commands = [["node", "--test"]]
    plan = installation.install_project(root, candidate=True, commands=commands)
    installation.install_project(
        root, candidate=True, commands=commands, apply=True, approve_plan=plan["plan_digest"]
    )
    _commit(root)
    runtime = root / ".astack/workflow.py"
    # -S removes all site packages, including PyYAML and the editable astack package.
    # Success must come from the delivered core and this application's own Node check.
    result = subprocess.run(
        [sys.executable, "-S", str(runtime), "validate", "check"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["status"] == "passed"
    retired = subprocess.run(
        [sys.executable, "-S", str(runtime), "work-unit", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert retired.returncode != 0  # The lifecycle engine is not delivered.
    assert not (root / ".astack/worktrees.py").exists()
    assert not (root / "pyproject.toml").exists()
    assert all((root / path).read_bytes() == data for path, data in original.items())
