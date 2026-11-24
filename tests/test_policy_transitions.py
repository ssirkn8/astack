"""Profile changes reuse installer ownership, snapshot, journal and recovery controls."""

import json
import sys
from pathlib import Path

import pytest
from git_tree_cache_test_support import copy_cached_git_tree
from test_installation import _apply, _commit, _files, _repo

from astack import installation as install
from astack import project_policy

pytestmark = pytest.mark.integration
CAPS = ["scientific-review", "scientific-validation"]


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    def build(parent: Path) -> Path:
        root = _repo(parent)
        _apply(root)
        config = root / ".astack/project.toml"
        config.write_text(
            config.read_text() + "\nverify = " + json.dumps([[sys.executable, "-c", "pass"]]) + "\n"
        )
        _commit(root)
        return root

    return copy_cached_git_tree("policy-consumer", tmp_path, build)


def _change(root: Path, profile: str, capabilities: list[str]) -> dict:
    plan = install.transition_project_policy(
        root, profile=profile, capabilities=capabilities, candidate=True
    )
    assert not plan["conflicts"], plan["conflicts"]
    return install.transition_project_policy(
        root,
        profile=profile,
        capabilities=capabilities,
        candidate=True,
        apply=True,
        approve_plan=plan["plan_digest"],
    )


def test_both_profile_directions_preserve_shared_runtime_checks_and_history(consumer: Path) -> None:
    root = consumer
    before = _files(root)
    _change(root, "data-science", CAPS)
    assert project_policy.project_profile(root) == "data-science"
    assert (
        project_policy.review_policy(root, ["exports/result.json"])["scientific_applicability"]
        == "required"
    )
    assert project_policy.validation_commands(root, "check")[0][0] == sys.executable
    assert project_policy.run_project_validation(root, "check")["commands"] == [
        [sys.executable, "-c", "pass"],
        [sys.executable, "-c", "pass"],
    ]
    changed = {path for path, data in _files(root).items() if before.get(path) != data}
    assert changed == {".astack/project.toml", install.MANIFEST}
    _commit(root)
    ds_history = _files(root)
    _change(root, "general", CAPS)
    assert project_policy.project_profile(root) == "general"
    assert (
        project_policy.review_policy(root, ["exports/result.json"])["scientific_applicability"]
        == "required"
    )
    assert all(
        (root / path).read_bytes() == data
        for path, data in ds_history.items()
        if path not in {".astack/project.toml", install.MANIFEST}
    )
    _commit(root)
    _change(root, "general", [])
    _commit(root)
    first = install.transition_project_policy(
        root, profile="general", capabilities=[], candidate=True
    )
    second = install.transition_project_policy(
        root, profile="general", capabilities=[], candidate=True
    )
    assert first == second and not first["actions"]


def test_scientific_validation_is_composed_with_the_owner_toolchain(consumer: Path) -> None:
    root = consumer
    config = root / ".astack/project.toml"
    _change(root, "general", CAPS)
    text = config.read_text()
    text = text.replace(
        "verify = " + json.dumps([[sys.executable, "-c", "pass"]]),
        "verify = " + json.dumps([[sys.executable, "-c", "raise SystemExit(23)"]]),
    )
    config.write_text(text)
    with pytest.raises(RuntimeError, match="23"):
        project_policy.run_project_validation(root, "check")


def test_stale_state_and_modified_managed_bytes_block_apply(consumer: Path) -> None:
    root = consumer
    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )
    (root / "README.md").write_text("Independent owner work")
    with pytest.raises(ValueError, match="exact conflict-free"):
        install.transition_project_policy(
            root,
            profile="data-science",
            capabilities=CAPS,
            candidate=True,
            apply=True,
            approve_plan=plan["plan_digest"],
        )
    assert (root / "README.md").read_text() == "Independent owner work"
    _commit(root)
    (root / ".astack/git_state.py").write_text("Owner-managed runtime customization")
    _commit(root)
    blocked = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )
    assert ".astack/git_state.py" in blocked["conflicts"]


def test_active_work_and_scientific_downgrades_fail_without_mutating_evidence(
    consumer: Path, tmp_path: Path
) -> None:
    import subprocess

    root = consumer
    linked = tmp_path / "linked/pending"
    subprocess.run(
        ["git", "worktree", "add", "-b", "pending", str(linked)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    before = _files(root)
    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )
    assert any("Active linked worktrees" in conflict for conflict in plan["conflicts"])
    assert plan["state"]["active_worktrees"][0]["branch"] == "pending"
    with pytest.raises(ValueError, match="conflict-free"):
        install.transition_project_policy(
            root,
            profile="data-science",
            capabilities=CAPS,
            candidate=True,
            apply=True,
            approve_plan=plan["plan_digest"],
        )
    assert _files(root) == before
    assert linked.is_dir()


@pytest.mark.parametrize(
    "declaration",
    [
        '\n[output_contract]\npath = "contracts/outputs.yml"\n',
        "\n[review]\nscientific = true\n",
    ],
)
def test_declared_science_cannot_be_disabled(consumer: Path, declaration: str) -> None:
    root = consumer
    _change(root, "data-science", CAPS)
    config = root / ".astack/project.toml"
    config.write_text(config.read_text() + declaration)
    _commit(root)
    before = _files(root)
    plan = install.transition_project_policy(
        root, profile="general", capabilities=[], candidate=True
    )
    assert any("Scientific obligations" in conflict for conflict in plan["conflicts"])
    with pytest.raises(ValueError, match="conflict-free"):
        install.transition_project_policy(
            root,
            profile="general",
            capabilities=[],
            candidate=True,
            apply=True,
            approve_plan=plan["plan_digest"],
        )
    assert _files(root) == before


def test_policy_failure_restores_owner_configuration_and_manifest(
    consumer: Path, monkeypatch
) -> None:
    root = consumer
    before = _files(root)
    original = install._write_install_file

    def fail_manifest(path, data, expected):
        if path == root / install.MANIFEST:
            raise OSError("Injected policy manifest failure")
        original(path, data, expected)

    monkeypatch.setattr(install, "_write_install_file", fail_manifest)
    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )
    with pytest.raises(OSError, match="Injected"):
        install.transition_project_policy(
            root,
            profile="data-science",
            capabilities=CAPS,
            candidate=True,
            apply=True,
            approve_plan=plan["plan_digest"],
        )
    assert _files(root) == before


def test_interrupted_policy_transaction_recovers_through_installer_journal(
    consumer: Path, monkeypatch
) -> None:
    root = consumer
    before = _files(root)
    original_write = install._write_install_file
    original_restore = install._restore

    def fail_manifest(path, data, expected):
        if path == root / install.MANIFEST:
            raise OSError("Simulated policy interruption")
        original_write(path, data, expected)

    def interrupted_restore(*args, **kwargs):
        raise RuntimeError("Process stopped before rollback")

    monkeypatch.setattr(install, "_write_install_file", fail_manifest)
    monkeypatch.setattr(install, "_restore", interrupted_restore)
    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )
    with pytest.raises(RuntimeError, match="stopped before rollback"):
        install.transition_project_policy(
            root,
            profile="data-science",
            capabilities=CAPS,
            candidate=True,
            apply=True,
            approve_plan=plan["plan_digest"],
        )
    monkeypatch.setattr(install, "_restore", original_restore)
    journal = json.loads((root / install.TRANSACTION).read_text())
    assert journal["purpose"] == "policy-transition"
    recovery = install.recover_installation(root)
    assert not recovery["conflicts"]
    install.recover_installation(root, apply=True, approve_plan=recovery["plan_digest"])
    assert _files(root) == before


def test_obligation_changes_at_lock_boundary_block_policy_apply(
    consumer: Path, monkeypatch
) -> None:
    root = consumer
    before = _files(root)
    original_state = install._policy_state
    count = 0

    def changed_state(target):
        nonlocal count
        count += 1
        state = original_state(target)
        if count > 2:
            state["digest"] = "b" * 64
        return state

    # First plan observes state once, apply's current plan observes it again, then the locked
    # transaction rechecks it. No configuration or journal is written after that mismatch.
    monkeypatch.setattr(install, "_policy_state", changed_state)
    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )
    with pytest.raises(ValueError, match="worktrees changed"):
        install.transition_project_policy(
            root,
            profile="data-science",
            capabilities=CAPS,
            candidate=True,
            apply=True,
            approve_plan=plan["plan_digest"],
        )
    assert _files(root) == before


def test_policy_based_ds_uses_installed_runtime_without_scaffold(
    consumer: Path, tmp_path: Path
) -> None:
    import subprocess

    root = consumer
    _change(root, "data-science", CAPS)
    _commit(root)
    for tier in ("check", "verify"):
        result = subprocess.run(
            [sys.executable, ".astack/workflow.py", "validate", tier],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(result.stdout)["status"] == "passed"
    linked = tmp_path / "linked/after-policy"
    subprocess.run(
        ["git", "worktree", "add", "-b", "after-policy", str(linked)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    assert not (root / "data").exists() and not (linked / "data").exists()
    assert not (root / "pyproject.toml").exists()
    assert not (root / ".astack/worktrees.py").exists()
    assert project_policy.project_profile(linked) == "data-science"


def test_policy_based_ds_upgrade_preserves_its_selected_profile(
    consumer: Path, monkeypatch
) -> None:
    import subprocess

    from astack import release_upgrade

    root = consumer
    _change(root, "data-science", CAPS)
    _commit(root)
    release = {"tag": "v3.0.0", "commit": "a" * 40, "release_id": "42"}
    monkeypatch.setattr(release_upgrade, "resolve_release", lambda _: release)

    def run(command, target):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "status": "dry-run",
                    "profile": "data-science",
                    "conflicts": [],
                    "source": {"kind": "release", **release},
                    "plan_digest": "b" * 64,
                }
            ),
            "",
        )

    monkeypatch.setattr(release_upgrade, "_run", run)
    plan = release_upgrade.upgrade_release(root, "v3.0.0")
    assert plan["profile"] == "data-science" and plan["status"] == "dry-run"


def test_enabling_scientific_validation_without_verify_is_allowed_and_disclosed(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _apply(root)
    _commit(root)

    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )

    assert not plan["conflicts"], plan["conflicts"]
    assert plan["validation"]["scientific_verify"] is None
    assert any("validation.verify is not declared" in item for item in plan["limitations"])


def test_a_malformed_declared_verify_still_blocks_enabling_scientific_validation(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _apply(root)
    config = root / ".astack/project.toml"
    config.write_text(config.read_text() + "\nverify = []\n")
    _commit(root)

    plan = install.transition_project_policy(
        root, profile="data-science", capabilities=CAPS, candidate=True
    )

    assert any("validation.verify" in item for item in plan["conflicts"])


def test_change_policy_cli_approve_plan_implies_apply_end_to_end(
    consumer: Path, monkeypatch, capsys
) -> None:
    from astack import cli

    root = consumer
    base = ["astack", "change-policy", str(root), "--profile", "data-science", "--candidate"]
    monkeypatch.setattr(sys, "argv", base)
    cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "dry-run"
    digest = plan["plan_digest"]

    for bad_flags in (["--apply"], ["--approve-plan", ""]):
        monkeypatch.setattr(sys, "argv", [*base, *bad_flags])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 1
        assert "exact conflict-free" in capsys.readouterr().err
        assert project_policy.project_profile(root) == "general"

    monkeypatch.setattr(sys, "argv", [*base, "--approve-plan", digest])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert project_policy.project_profile(root) == "data-science"

    _commit(root)
    back = ["astack", "change-policy", str(root), "--profile", "general", "--candidate"]
    monkeypatch.setattr(sys, "argv", back)
    cli.main()
    back_digest = json.loads(capsys.readouterr().out)["plan_digest"]
    monkeypatch.setattr(sys, "argv", [*back, "--apply", "--approve-plan", back_digest])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert project_policy.project_profile(root) == "general"
