"""Disposable consumers and mocked release evidence, never live repository mutations."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from astack import installation as install
from astack import release_upgrade


def _run(root: Path, *argv: str) -> str:
    return subprocess.run(argv, cwd=root, check=True, capture_output=True, text=True).stdout


def _commit(root: Path) -> None:
    _run(root, "git", "add", ".")
    _run(
        root,
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Fixture state",
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "application"
    shutil.copytree(Path("tests/fixtures/compatibility/existing-node"), root)
    _run(root, "git", "init", "-b", "main")
    _commit(root)
    return root


def _checks() -> list[list[str]]:
    return [[sys.executable, "-c", "pass"]]


def _apply(root: Path) -> dict:
    plan = install.install_project(root, candidate=True, commands=_checks())
    assert not plan["conflicts"]
    return install.install_project(
        root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("node") is None, reason="Node consumer execution unavailable")
def test_existing_node_adoption_is_dry_run_first_and_preserves_application(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _run(root, "node", "--test")
    before = _files(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    assert plan["status"] == "dry-run"
    assert _files(root) == before
    assert plan["source"]["kind"] == "candidate" and not plan["source"]["published"]
    with pytest.raises(ValueError, match="exact current"):
        install.install_project(root, candidate=True, commands=_checks(), apply=True)
    _apply(root)
    assert all((root / p).read_bytes() == data for p, data in before.items())
    _run(root, "node", "--test")
    assert (root / "AGENTS.md").is_file() and not (root / "CLAUDE.md").exists()
    assert not (root / "pyproject.toml").exists()
    assert not (root / "data").exists()
    output = _run(root, sys.executable, ".astack/workflow.py", "validate", "check")
    assert json.loads(output)["status"] == "passed"
    assert (root / ".agents/skills/astack-upgrade/SKILL.md").exists()
    assert not list((root / ".opencode/skills").glob("workflow-*"))
    assert not (root / ".astack/worktrees.py").exists()
    assert "astack-upgrade" in (root / ".opencode/commands/astack-upgrade.md").read_text()
    catalog = json.loads((root / ".astack/components.json").read_text())
    assert all((root / component["target"]).is_file() for component in catalog["components"])
    _commit(root)
    repeat = install.install_project(root, candidate=True)
    assert not repeat["conflicts"]
    assert all(a["action"] == "unchanged" for a in repeat["actions"])


@pytest.mark.integration
def test_empty_creation_does_not_force_application_template(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    plan = install.install_project(root, candidate=True, commands=_checks(), initialize=True)
    assert not root.exists()
    install.install_project(
        root,
        candidate=True,
        commands=_checks(),
        initialize=True,
        apply=True,
        approve_plan=plan["plan_digest"],
    )
    assert (root / ".git").is_dir()
    assert (root / ".astack/project.toml").is_file()
    assert not (root / "src").exists() and not (root / "pyproject.toml").exists()


@pytest.mark.integration
def test_owner_conflicts_and_changed_approval_are_preserved(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    owned = root / ".opencode/skills/astack-upgrade/SKILL.md"
    owned.parent.mkdir(parents=True)
    owned.write_text("Owner-authored skill")
    _commit(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    assert owned.relative_to(root).as_posix() in plan["conflicts"]
    with pytest.raises(ValueError, match="conflicts"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert owned.read_text() == "Owner-authored skill"
    owned.unlink()
    _commit(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    (root / "README.md").write_text("Changed owner work")
    with pytest.raises(ValueError, match="exact current"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert (root / "README.md").read_text() == "Changed owner work"


@pytest.mark.integration
def test_modified_managed_file_blocks_upgrade_and_owner_config_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _apply(root)
    runtime = root / ".astack/git_state.py"
    runtime.write_text(runtime.read_text() + "\n# owner customization\n")
    config = root / ".astack/project.toml"
    config.write_text(config.read_text() + "\n# owner policy\n")
    _commit(root)
    before = _files(root)
    plan = install.install_project(root, candidate=True)
    assert ".astack/git_state.py" in plan["conflicts"]
    assert all(a["path"] != ".astack/project.toml" for a in plan["actions"])
    with pytest.raises(ValueError, match="conflicts"):
        install.install_project(root, candidate=True, apply=True, approve_plan=plan["plan_digest"])
    assert _files(root) == before


@pytest.mark.integration
def test_failed_apply_restores_managed_bytes_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    before = _files(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    original = install._write_install_file

    def fail_manifest(path: Path, data: bytes, expected: str | None) -> None:
        if path == root / install.MANIFEST:
            raise OSError("Injected manifest failure")
        original(path, data, expected)

    monkeypatch.setattr(install, "_write_install_file", fail_manifest)
    with pytest.raises(OSError, match="Injected"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert _files(root) == before
    assert not (root / ".astack/version").exists()


def test_published_install_rejects_unverified_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        install, "resolve_release", lambda tag: {"tag": tag, "commit": "a" * 40, "release_id": "42"}
    )
    monkeypatch.setattr(install, "_source_commit", lambda root: "b" * 40)
    with pytest.raises(ValueError, match="exact published|modified source"):
        install._source_identity(Path.cwd(), "v2.0.1", False)


@pytest.mark.integration
def test_symlinked_managed_path_is_never_followed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".astack").symlink_to(outside, target_is_directory=True)
    _commit(root)
    with pytest.raises(ValueError, match="symlink"):
        install.install_project(root, candidate=True, commands=_checks())
    assert not list(outside.iterdir())


def test_generic_release_upgrade_uses_exact_release_tool_and_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".astack").mkdir()
    (tmp_path / ".astack/manifest.json").write_text("{}")
    release = {"tag": "v3.0.0", "commit": "a" * 40, "release_id": "42"}
    monkeypatch.setattr(release_upgrade, "resolve_release", lambda _: release)
    calls = []

    def run(command, target):
        calls.append(command)
        assert command[4].endswith("@" + "a" * 40)
        assert command[6] == "install"
        payload: dict[str, object] = {
            "status": "applied" if "--apply" in command else "dry-run",
            "profile": "general",
            "source": {"kind": "release", **release},
            "conflicts": [],
            "plan_digest": "b" * 64,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(release_upgrade, "_run", run)
    plan = release_upgrade.upgrade_release(tmp_path, "v3.0.0")
    assert plan["status"] == "dry-run"
    with pytest.raises(ValueError, match="exact conflict-free"):
        release_upgrade.upgrade_release(tmp_path, "v3.0.0", apply=True)
    result = release_upgrade.upgrade_release(
        tmp_path, "v3.0.0", apply=True, approve_plan=plan["plan_digest"]
    )
    assert result["status"] == "applied"
    assert "--approve-plan" in calls[-1]


@pytest.mark.integration
def test_interrupted_install_recovers_only_known_workflow_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    before = _files(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    original_write = install._write_install_file
    original_restore = install._restore

    def fail_manifest(path: Path, data: bytes, expected: str | None) -> None:
        if path == root / install.MANIFEST:
            raise OSError("Simulated interruption")
        original_write(path, data, expected)

    def interrupted_restore(*args, **kwargs):
        raise RuntimeError("Process interrupted before rollback")

    monkeypatch.setattr(install, "_write_install_file", fail_manifest)
    monkeypatch.setattr(install, "_restore", interrupted_restore)
    with pytest.raises(RuntimeError, match="interrupted before rollback"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    monkeypatch.setattr(install, "_write_install_file", original_write)
    monkeypatch.setattr(install, "_restore", original_restore)
    assert (root / install.TRANSACTION).exists()
    # Independent application edits stay outside rollback ownership.
    (root / "README.md").write_text("Owner work after interruption")
    recovery = install.recover_installation(root)
    assert not recovery["conflicts"]
    with pytest.raises(ValueError, match="exact conflict-free"):
        install.recover_installation(root, apply=True, approve_plan="wrong")
    runtime = root / ".astack/git_state.py"
    installed_bytes = runtime.read_bytes()
    runtime.write_bytes(installed_bytes + b"\n# independent change\n")
    conflict = install.recover_installation(root)
    assert ".astack/git_state.py" in conflict["conflicts"]
    with pytest.raises(ValueError, match="exact conflict-free"):
        install.recover_installation(root, apply=True, approve_plan=conflict["plan_digest"])
    runtime.write_bytes(installed_bytes)
    recovery = install.recover_installation(root)
    install.recover_installation(root, apply=True, approve_plan=recovery["plan_digest"])
    before["README.md"] = b"Owner work after interruption"
    assert _files(root) == before


@pytest.mark.integration
def test_recovery_rejects_application_paths_and_active_lock(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    directory = root / ".astack"
    directory.mkdir()
    journal = directory / "install-transaction.json"
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {"src/identity.js": {"bytes": None, "after_sha256": "a" * 64}},
                "created_dirs": [],
            }
        )
    )
    with pytest.raises(ValueError, match="outside workflow ownership"):
        install.recover_installation(root)
    journal.write_text(json.dumps({"schema_version": 1, "files": {}, "created_dirs": []}))
    lock = directory / "install.lock"
    lock.write_text(str(os.getpid()))
    recovery = install.recover_installation(root)
    assert recovery["conflicts"]
    with pytest.raises(ValueError, match="exact conflict-free"):
        install.recover_installation(root, apply=True, approve_plan=recovery["plan_digest"])
    assert lock.exists() and journal.exists()


@pytest.mark.integration
def test_owner_instructions_are_never_copied_or_overwritten(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "AGENTS.md").write_text("Owner AGENTS instructions")
    (root / "CLAUDE.md").write_text("Different owner instructions")
    _commit(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    assert not plan["conflicts"]
    assert not {"AGENTS.md", "CLAUDE.md"} & {action["path"] for action in plan["actions"]}
    install.install_project(
        root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
    )
    assert (root / "AGENTS.md").read_text() == "Owner AGENTS instructions"
    assert (root / "CLAUDE.md").read_text() == "Different owner instructions"


@pytest.mark.integration
def test_a_claude_only_repository_gets_agents_guidance_not_a_copy(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "CLAUDE.md").write_text("Owner Claude instructions")
    _commit(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    created = {action["path"] for action in plan["actions"] if action["action"] == "create"}
    assert "AGENTS.md" in created and "CLAUDE.md" not in created
    install.install_project(
        root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
    )
    assert "Owner Claude instructions" not in (root / "AGENTS.md").read_text()
    assert (root / "CLAUDE.md").read_text() == "Owner Claude instructions"


def test_verified_release_identity_is_bound_to_exact_source_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A wheel-like source with observed immutable VCS identity, with all GitHub evidence mocked.
    release = {"tag": "v2.0.1", "commit": "a" * 40, "release_id": "42"}
    monkeypatch.setattr(install, "resolve_release", lambda _: release)
    monkeypatch.setattr(install, "_source_commit", lambda _: "a" * 40)
    monkeypatch.setattr(install, "workflow_version", lambda _: "2.0.1")
    identity = install._source_identity(tmp_path, "v2.0.1", False)
    assert identity["kind"] == "release" and identity["published"]
    assert identity["commit"] == release["commit"]


@pytest.mark.integration
def test_recorded_newer_version_cannot_be_downgraded(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _apply(root)
    manifest = root / install.MANIFEST
    record = json.loads(manifest.read_text())
    record["workflow_version"] = "99.0.0"
    manifest.write_text(json.dumps(record))
    _commit(root)
    before = _files(root)
    plan = install.install_project(root, candidate=True)
    assert any("newer astack" in conflict for conflict in plan["conflicts"])
    with pytest.raises(ValueError, match="conflicts"):
        install.install_project(root, candidate=True, apply=True, approve_plan=plan["plan_digest"])
    assert _files(root) == before


@pytest.mark.integration
def test_first_journal_failure_releases_lock_and_preserves_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    before = _files(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    original = install._atomic_write

    def fail_journal(path: Path, data: bytes) -> None:
        if path == root / install.TRANSACTION:
            raise OSError("Journal unavailable")
        original(path, data)

    monkeypatch.setattr(install, "_atomic_write", fail_journal)
    with pytest.raises(OSError, match="Journal unavailable"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert _files(root) == before
    assert not (root / ".astack/install.lock").exists()


@pytest.mark.integration
def test_state_changed_at_lock_boundary_is_preserved_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    plan = install.install_project(root, candidate=True, commands=_checks())
    original_open = install.os.open

    def concurrent_owner(path, flags, mode=0o777, *args, **kwargs):
        fd = original_open(path, flags, mode, *args, **kwargs)
        if Path(path) == root / ".astack/install.lock":
            (root / "AGENTS.md").write_text("Independent owner instructions at lock boundary")
        return fd

    monkeypatch.setattr(install.os, "open", concurrent_owner)
    with pytest.raises(ValueError, match="changed"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert (root / "AGENTS.md").read_text() == "Independent owner instructions at lock boundary"
    assert not (root / install.MANIFEST).exists()
    assert not (root / ".astack/install.lock").exists()


def test_install_cli_renders_empty_target_plan_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from astack import cli

    target = tmp_path / "empty-target"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "astack",
            "install",
            str(target),
            "--candidate",
            "--initialize",
            "--check-argv",
            json.dumps(_checks()[0]),
        ],
    )
    cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry-run" and payload["profile"] == "general"
    assert payload["source"]["kind"] == "candidate"
    assert not target.exists()


@pytest.mark.integration
def test_owner_file_created_after_journal_survives_and_owned_writes_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    before = _files(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    original = install._atomic_write

    def owner_after_journal(path: Path, data: bytes) -> None:
        original(path, data)
        if path == root / install.TRANSACTION:
            (root / "AGENTS.md").write_text("Independent instructions after journal creation")

    monkeypatch.setattr(install, "_atomic_write", owner_after_journal)
    with pytest.raises(RuntimeError, match="independently changed"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert (root / "AGENTS.md").read_text() == "Independent instructions after journal creation"
    assert not (root / install.MANIFEST).exists()
    assert not (root / ".astack/version").exists()
    assert not (root / ".astack/install.lock").exists()
    assert (root / install.TRANSACTION).is_file()
    remaining = _files(root)
    assert set(remaining) == set(before) | {"AGENTS.md", install.TRANSACTION}
    assert all(remaining[path] == data for path, data in before.items())
    recovery = install.recover_installation(root)
    assert recovery["conflicts"] == ["AGENTS.md"]


@pytest.mark.integration
def test_exclusive_creation_preserves_owner_file_at_publish_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    before = _files(root)
    plan = install.install_project(root, candidate=True, commands=_checks())
    original = install.os.link

    def owner_at_publish(source, destination, *args, **kwargs):
        if Path(destination) == root / "AGENTS.md":
            Path(destination).write_text("Owner wins exclusive creation race")
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(install.os, "link", owner_at_publish)
    with pytest.raises(RuntimeError, match="independently changed"):
        install.install_project(
            root, candidate=True, commands=_checks(), apply=True, approve_plan=plan["plan_digest"]
        )
    assert (root / "AGENTS.md").read_text() == "Owner wins exclusive creation race"
    assert set(_files(root)) == set(before) | {"AGENTS.md", install.TRANSACTION}


@pytest.mark.integration
def test_managed_owner_edit_after_journal_is_preserved_and_other_updates_roll_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    _apply(root)
    _commit(root)
    before = _files(root)
    original_payload = install._payload
    runtime = ".astack/git_state.py"
    wrapper = ".agents/skills/astack-upgrade/SKILL.md"

    def changed_payload(source: Path):
        files, inventory = original_payload(source)
        for relative in (runtime, wrapper):
            files[relative] += b"\n# candidate update\n"
            for entry in inventory.values():
                if entry["path"] == relative:
                    entry["sha256"] = install._hash(files[relative])
        return files, inventory

    monkeypatch.setattr(install, "_payload", changed_payload)
    plan = install.install_project(root, candidate=True)
    assert not plan["conflicts"]
    original_write = install._atomic_write

    def owner_after_journal(path: Path, data: bytes) -> None:
        original_write(path, data)
        if path == root / install.TRANSACTION:
            (root / runtime).write_text("Independent runtime customization after journal")

    monkeypatch.setattr(install, "_atomic_write", owner_after_journal)
    with pytest.raises(RuntimeError, match="independently changed"):
        install.install_project(root, candidate=True, apply=True, approve_plan=plan["plan_digest"])
    assert (root / runtime).read_text() == "Independent runtime customization after journal"
    remaining = _files(root)
    assert set(remaining) == set(before) | {install.TRANSACTION}
    assert all(remaining[path] == data for path, data in before.items() if path != runtime)
    assert install.recover_installation(root)["conflicts"] == [runtime]


def test_retired_component_is_released_and_the_audit_still_passes_the_manifest(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _apply(root)
    retired = root / ".opencode/skills/command-results/SKILL.md"
    retired.parent.mkdir(parents=True)
    retired.write_bytes(b"---\nname: command-results\ndescription: Retired.\n---\n")
    manifest_path = root / ".astack/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["managed_components"]["skill.command-results"] = {
        "path": ".opencode/skills/command-results/SKILL.md",
        "sha256": install._hash(retired.read_bytes()),
    }
    manifest_path.write_text(json.dumps(manifest))
    _commit(root)

    plan = install.install_project(root, candidate=True)
    assert plan["released"] == [".opencode/skills/command-results/SKILL.md"]
    assert not plan["conflicts"]
    install.install_project(root, candidate=True, apply=True, approve_plan=plan["plan_digest"])

    upgraded = json.loads(manifest_path.read_text())
    assert "skill.command-results" not in upgraded["managed_components"]
    assert retired.is_file()  # Released, never deleted.
    report = subprocess.run(
        [sys.executable, ".astack/workflow.py", "audit", "--format", "json"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    findings = json.loads(report.stdout)["findings"]
    assert not [f for f in findings if f["check"] == "managed-manifest"], findings


def test_recovery_accepts_a_journal_written_before_a_component_was_retired(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    retired = root / ".opencode/skills/command-results/SKILL.md"
    retired.parent.mkdir(parents=True)
    retired.write_bytes(b"half-written")
    journal = root / install.TRANSACTION
    journal.parent.mkdir(exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": {
                    ".opencode/skills/command-results/SKILL.md": {
                        "bytes": None,
                        "after_sha256": install._hash(b"half-written"),
                    }
                },
                "created_dirs": [],
            }
        )
    )

    recovery = install.recover_installation(root)
    assert not recovery["conflicts"]
    install.recover_installation(root, apply=True, approve_plan=recovery["plan_digest"])

    assert not retired.exists()  # The interrupted install created it, so recovery removes it.
    assert not journal.exists()


def test_retired_paths_name_no_current_component() -> None:
    current = set(install._payload(install.repository_root())[0])
    assert not install.RETIRED_MANAGED_PATHS & current
