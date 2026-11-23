"""The README's first run, through the real commands a new user types.

CI once stayed green while this path was broken, because in-process audit calls used a project
without the tools `astack new` adds by default. These tests run the installed CLI and the
generated project's own recipes instead.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ASTACK = Path(sys.executable).with_name("astack")
TIMEOUT_SECONDS = 300
CHECK_LEAVES_MARKER = "from pathlib import Path; Path('.git/first-run-check').write_text('ran')"


def _uv_directory(*command: str) -> str:
    result = subprocess.run(["uv", *command], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def first_run_env(tmp_path: Path) -> dict[str, str]:
    """A clean user environment that still reuses the machine's uv downloads."""
    cache = _uv_directory("cache", "dir")
    pythons = _uv_directory("python", "dir")
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COV_", "ASTACK_", "GIT_"))
        and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    }
    return env | {
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "First Run",
        "GIT_AUTHOR_EMAIL": "first-run@example.invalid",
        "GIT_COMMITTER_NAME": "First Run",
        "GIT_COMMITTER_EMAIL": "first-run@example.invalid",
        "UV_CACHE_DIR": cache,
        "UV_PYTHON_INSTALL_DIR": pythons,
        # Test temp space can be memory-backed on CI, so link packages instead of copying them.
        "UV_LINK_MODE": "symlink",
        # The outer test run already uses every CPU, so nested runs must not start their own pool.
        "PYTEST_XDIST_AUTO_NUM_WORKERS": "1",
    }


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SECONDS,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"{' '.join(command)} failed:\n{output[-4000:]}"
    return output


def _run_in_terminal(
    command: list[str], cwd: Path, env: dict[str, str], answers: list[tuple[str, str]]
) -> str:
    """Run a command on a real terminal, answering each prompt when it appears."""
    pending = list(answers)
    output = ""
    searched_to = 0
    deadline = time.monotonic() + TIMEOUT_SECONDS
    primary, secondary = pty.openpty()
    try:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=secondary,
                stdout=secondary,
                stderr=secondary,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(secondary)
        try:
            while True:
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"Timed out in a terminal:\n{output[-4000:]}"
                if not select.select([primary], [], [], min(remaining, 1))[0]:
                    if process.poll() is not None:
                        break
                    continue
                try:
                    chunk = os.read(primary, 65536)
                except OSError:  # Linux reports EIO once the command closes its terminal.
                    break
                if not chunk:
                    break
                output += chunk.decode(errors="replace")
                if pending and (found := output.find(pending[0][0], searched_to)) >= 0:
                    searched_to = found + len(pending[0][0])
                    os.write(primary, (pending.pop(0)[1] + "\n").encode())
            returncode = process.wait(timeout=max(deadline - time.monotonic(), 1))
        finally:
            if process.poll() is None:
                # The command leads its own session, so this also stops its `uv lock` child.
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    finally:
        os.close(primary)
    assert not pending, f"Never asked for {pending[0][0]!r}:\n{output[-4000:]}"
    assert returncode == 0, f"{' '.join(command)} failed:\n{output[-4000:]}"
    return output


def _assert_tests_ran(output: str) -> None:
    """A recipe reduced to `true` would exit 0 without running the starter tests."""
    assert re.search(r"\b[1-9]\d* passed\b", output), output[-4000:]


def _assert_honest_verdict(output: str) -> None:
    assert "computational_reproducibility: not-assessed" in output
    assert "computational_reproducibility: pass" not in output
    assert "release_readiness: fail" not in output


def test_readme_first_run_on_a_new_data_science_project(
    tmp_path: Path, first_run_env: dict[str, str]
) -> None:
    # The README's exact command. An empty answer accepts the suggested default.
    _run_in_terminal(
        [str(ASTACK), "new", "my-study"],
        tmp_path,
        first_run_env,
        [
            ("Project name", ""),
            ("Package name", ""),
            ("Description:", "First-run acceptance"),
            ("Intended use:", "Automated test"),
        ],
    )
    project = tmp_path / "my-study"
    assert (project / "src/my_study").is_dir(), "The suggested package name was not used"
    readme = (project / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# My Study"), "The suggested project name was not used"
    assert (project / ".git").is_dir(), "`astack new` must initialize Git"
    # The public README includes this step. Repeating it after `astack new` is harmless.
    _run(["git", "init", "-b", "main"], project, first_run_env)
    _run(["git", "add", "-A"], project, first_run_env)
    _run(["git", "commit", "-m", "Start from astack"], project, first_run_env)

    _assert_tests_ran(_run(["just", "check"], project, first_run_env))
    ship_ready = _run(["just", "ship-ready"], project, first_run_env)
    _assert_tests_ran(ship_ready)
    _assert_honest_verdict(ship_ready)


def test_first_run_on_an_existing_general_repository(
    tmp_path: Path, first_run_env: dict[str, str]
) -> None:
    project = tmp_path / "application"
    project.mkdir()
    (project / "README.md").write_text("# Application\n", encoding="utf-8")
    _run(["git", "init", "-b", "main"], project, first_run_env)
    _run(["git", "add", "-A"], project, first_run_env)
    _run(["git", "commit", "-m", "Start"], project, first_run_env)
    install = [
        str(ASTACK),
        "install",
        str(project),
        "--candidate",
        "--check-argv",
        json.dumps([sys.executable, "-c", CHECK_LEAVES_MARKER]),
    ]
    preview = _run(install, tmp_path, first_run_env)
    digest = re.search(r'"plan_digest": "([0-9a-f]{64})"', preview)
    assert digest, preview[-2000:]
    _run([*install, "--apply", "--approve-plan", digest.group(1)], tmp_path, first_run_env)
    _run(["git", "add", "-A"], project, first_run_env)
    _run(["git", "commit", "-m", "Add astack"], project, first_run_env)

    # The marker lives in .git so the project's own check leaves the worktree clean.
    marker = project / ".git/first-run-check"
    workflow = [sys.executable, ".astack/workflow.py"]
    _run([*workflow, "validate", "check"], project, first_run_env)
    assert marker.exists(), "`validate check` did not run the project's check"
    marker.unlink()
    audit = [*workflow, "audit", "--format", "text", "--run-tools", "--strict"]
    _assert_honest_verdict(_run(audit, project, first_run_env))
    assert marker.exists(), "The strict audit did not run the project's check"
