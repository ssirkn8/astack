from __future__ import annotations

import os
import re
import subprocess
import tempfile

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _git_baseline,  # noqa: F401
    _project,  # noqa: F401
    _set_capabilities,  # noqa: F401
    json,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    sys,  # noqa: F401
)


@pytest.mark.parametrize(
    "argv",
    [
        ["work-unit", "next-action"],
        ["work-unit", "packet", "--phase", "planning"],
        ["work-unit", "preflight", "--packet-file", "packet.json"],
        ["work-unit", "validate-result", "unit", "--result-file", "result.json"],
        ["work-unit", "child-runner", "capability", "--packet-file", "packet.json"],
        ["work-unit", "record-pending-decision", "unit", "--record-file", "record.json"],
        ["work-unit", "resolve-pending-decision", "unit", "--decision-id", "x"],
        ["work-unit", "record-action-failure", "unit", "--record-file", "record.json"],
        ["work-unit", "retry-action", "unit", "--action", "x", "--input-fingerprint", "y"],
        ["work-unit", "clear-action-failure", "unit", "--action", "x", "--input-fingerprint", "y"],
        ["controller-check"],
        ["work-unit", "list"],
        ["work-unit", "resume"],
        ["worktree", "list"],
        ["direct", "start"],
        ["control-plane", "status"],
    ],
)
def test_runtime_parser_rejects_retired_run_commands(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        project_tool._parser().parse_args(argv)
    assert error.value.code == 2


def test_runtime_parser_exposes_conservative_dvc_cache_operations() -> None:
    status = project_tool._parser().parse_args(["dvc-cache", "status"])
    preview = project_tool._parser().parse_args(["dvc-cache", "gc"])
    apply = project_tool._parser().parse_args(["dvc-cache", "gc", "--apply"])

    assert status.dvc_cache_command == "status"
    assert preview.apply is False
    assert apply.apply is True


def test_dvc_cache_status_is_not_configured_without_dvc(tmp_path: Path) -> None:
    target = _project(tmp_path)

    assert project_tool.run_dvc_cache_status(target) == {
        "status": "not-configured",
        "cache": None,
        "reason": "DVC is not configured for this project",
    }


def test_dvc_cache_configuration_refuses_symlinked_directory(monkeypatch, tmp_path: Path) -> None:
    target = _project(tmp_path)
    external = tmp_path / "external-dvc"
    external.mkdir()
    config = external / "config.local"
    config.write_text("private local state")
    (target / ".dvc").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(project_tool, "canonical_checkout", lambda root: target)
    monkeypatch.setattr(project_tool, "dvc_cache_root", lambda root: tmp_path / "cache")
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool._ensure_dvc_cache(target)
    assert config.read_text() == "private local state"


def test_dvc_cache_configuration_restricts_cache_and_tmp_permissions(
    monkeypatch, tmp_path: Path
) -> None:
    target = _project(tmp_path)
    _git_baseline(target)
    cache = tmp_path / "cache"
    cache.mkdir(mode=0o755)
    os.chmod(cache, 0o755)
    tmp_dir = target / ".dvc" / "tmp"
    tmp_dir.mkdir(parents=True)
    os.chmod(tmp_dir, 0o755)
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(cache))
    monkeypatch.delenv("DVC_SITE_CACHE_DIR", raising=False)

    configured = project_tool._ensure_dvc_cache(target)

    site_cache = tmp_dir / "site-cache"
    assert configured == cache.resolve()
    local_config = (target / ".dvc" / "config.local").read_text(encoding="utf-8")
    assert f'site_cache_dir = "{site_cache.resolve()}"' in local_config
    if os.name != "nt":
        assert cache.stat().st_mode & 0o777 == 0o700
        assert tmp_dir.stat().st_mode & 0o777 == 0o700
        assert site_cache.stat().st_mode & 0o777 == 0o700

    # Paths containing `#` or trailing spaces must round-trip cleanly through config.local.
    hash_cache = tmp_path / "dir#1" / "cache "
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(hash_cache))
    assert project_tool._ensure_dvc_cache(target) == hash_cache.resolve()
    assert project_tool._read_dvc_config_value(
        target / ".dvc" / "config.local", "cache", "dir"
    ) == str(hash_cache.resolve())
    resolved_cache, config_error = project_tool._dvc_cache_configuration(target)
    assert config_error is None
    assert resolved_cache == hash_cache.resolve()

    # Existing quoted/uppercase ["CORE"] and UTF-8 BOM files are updated cleanly in place.
    local_file = target / ".dvc" / "config.local"
    local_file.write_text('\ufeff["CORE"]\n    "site_cache_dir" = "/old"\n', encoding="utf-8")
    assert project_tool._ensure_dvc_cache(target) == hash_cache.resolve()
    assert project_tool._read_dvc_config_value(local_file, "core", "site_cache_dir") == str(
        site_cache.resolve()
    )

    # Duplicate quoted/case-variant sections are rejected before touching config.local.
    ambiguous = '[cache]\n    dir = "/a"\n["CACHE"]\n    type = hardlink\n'
    local_file.write_text(ambiguous, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Duplicate DVC configuration section: cache"):
        project_tool._ensure_dvc_cache(target)
    assert local_file.read_text(encoding="utf-8") == ambiguous
    _, dup_error = project_tool._dvc_cache_configuration(target)
    assert dup_error is not None and "Duplicate DVC configuration section: cache" in dup_error

    # `#` inside a section name is literal (not a comment), and trailing header garbage fails.
    archive = '[cache#archive]\n    dir = "/old"\n'
    local_file.write_text(archive, encoding="utf-8")
    assert project_tool._read_dvc_config_value(local_file, "cache", "dir") is None
    assert project_tool._ensure_dvc_cache(target) == hash_cache.resolve()
    assert "[cache#archive]" in local_file.read_text(encoding="utf-8")

    # Bracketed comments after valid section headers are allowed; trailing garbage is rejected.
    commented = (
        f'[cache] # see [note] here\ndir = "{hash_cache.resolve()}"\n'
        f'[core]\nsite_cache_dir = "{site_cache.resolve()}"\n'
    )
    local_file.write_text(commented, encoding="utf-8")
    assert project_tool._ensure_dvc_cache(target) == hash_cache.resolve()

    for bad_header in ("[cache] garbage", '["cache"] garbage]'):
        malformed = (
            f'{bad_header}\ndir = "{hash_cache.resolve()}"\n'
            f'[core]\nsite_cache_dir = "{site_cache.resolve()}"\n'
        )
        local_file.write_text(malformed, encoding="utf-8")
        with pytest.raises(RuntimeError, match="invalid"):
            project_tool._ensure_dvc_cache(target)
        assert local_file.read_text(encoding="utf-8") == malformed


def test_dvc_cache_configuration_refuses_symlinked_cache_and_tmp(
    monkeypatch, tmp_path: Path
) -> None:
    target = _project(tmp_path)
    _git_baseline(target)
    external = tmp_path / "external"
    external.mkdir()
    os.chmod(external, 0o755)
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(cache_link))
    monkeypatch.delenv("DVC_SITE_CACHE_DIR", raising=False)
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool._ensure_dvc_cache(target)
    if os.name != "nt":
        assert external.stat().st_mode & 0o777 == 0o755

    # Symlinked ancestor in ASTACK_DVC_CACHE_ROOT and previously group/other-writable cache.
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(cache_link / "nested"))
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool._ensure_dvc_cache(target)
    assert not (external / "nested").exists()

    if os.name != "nt":
        writable_cache = tmp_path / "writable-cache"
        writable_cache.mkdir()
        os.chmod(writable_cache, 0o777)
        monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(writable_cache))
        with pytest.raises(RuntimeError, match="writable by other users"):
            project_tool._ensure_dvc_cache(target)
        assert writable_cache.stat().st_mode & 0o777 == 0o777

        # A 0700 leaf inside a group/other-writable non-sticky parent must also be refused.
        writable_parent = tmp_path / "writable-parent"
        writable_parent.mkdir()
        os.chmod(writable_parent, 0o777)
        private_leaf = writable_parent / "private-leaf"
        private_leaf.mkdir(mode=0o700)
        os.chmod(private_leaf, 0o700)
        monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(private_leaf))
        with pytest.raises(RuntimeError, match="under a directory writable by other users"):
            project_tool._ensure_dvc_cache(target)

    real_cache = tmp_path / "real-cache"
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(real_cache))
    (target / ".dvc").mkdir(parents=True, exist_ok=True)
    (target / ".dvc" / "tmp").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool._ensure_dvc_cache(target)

    (target / ".dvc" / "tmp").unlink()
    (target / ".dvc" / "tmp").mkdir()
    (target / ".dvc" / "tmp" / "site-cache").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        project_tool._ensure_dvc_cache(target)


def test_dvc_pipeline_verifies_and_isolates_site_cache_in_read_only_mode(
    monkeypatch, tmp_path: Path
) -> None:
    target = _project(tmp_path)
    shared = tmp_path / "shared-site-cache"
    shared.mkdir()
    os.chmod(shared, 0o755)
    monkeypatch.setenv("DVC_SITE_CACHE_DIR", str(shared))

    if os.name != "nt":
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "accessible to other users" in problem

        # Whitespace-padded DVC_SITE_CACHE_DIR is not stripped to match a different private dir.
        private_site = tmp_path / "private-site"
        private_site.mkdir(mode=0o700)
        os.chmod(private_site, 0o700)
        monkeypatch.setenv("DVC_SITE_CACHE_DIR", f" {private_site} ")
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "does not exist" in problem

        # Read-only probes refuse a 0700 site cache inside a writable non-sticky parent.
        writable_parent = tmp_path / "ro-writable-parent"
        writable_parent.mkdir()
        os.chmod(writable_parent, 0o777)
        nested_private = writable_parent / "site"
        nested_private.mkdir(mode=0o700)
        os.chmod(nested_private, 0o700)
        monkeypatch.setenv("DVC_SITE_CACHE_DIR", str(nested_private))
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "under a directory writable by other users" in problem

        # Read-only probes parse case-insensitive [CORE] and quoted keys/values,
        # and refuse an unsafe configured path instead of falling back to a temp dir.
        monkeypatch.delenv("DVC_SITE_CACHE_DIR", raising=False)
        (target / ".dvc").mkdir(parents=True, exist_ok=True)
        (target / ".dvc/config").write_text(
            f'[CORE]\n    "site_cache_dir" = "{shared}"\n', encoding="utf-8"
        )
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "accessible to other users" in problem

        # Duplicate case-insensitive sections in DVC config are rejected instead of ambiguous.
        (target / ".dvc/config").write_text(
            f'[core]\n    site_cache_dir = "{private_site}"\n'
            f'[CORE]\n    site_cache_dir = "{shared}"\n',
            encoding="utf-8",
        )
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "Duplicate DVC configuration" in problem

        (target / ".dvc/config").unlink()
        writable_tmp = target / ".dvc/tmp"
        writable_tmp.mkdir()
        os.chmod(writable_tmp, 0o777)
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "writable by other users" in problem
        os.chmod(writable_tmp, 0o700)

        # Read-only probes reject a writable non-sticky TMPDIR before creating a temp site cache.
        monkeypatch.setenv("TMPDIR", str(writable_parent))
        monkeypatch.setattr(tempfile, "tempdir", None)
        pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
        assert pipeline is None
        assert "under a directory writable by other users" in problem
        monkeypatch.delenv("TMPDIR", raising=False)
        monkeypatch.setattr(tempfile, "tempdir", None)

        # Read-only probes check ancestors even when .dvc/tmp does not exist yet.
        uninit_root = writable_parent / "uninit-repo"
        uninit_root.mkdir(mode=0o700)
        os.chmod(uninit_root, 0o700)
        pipeline, problem = project_tool._dvc_pipeline(uninit_root, read_only=True)
        assert pipeline is None
        assert "under a directory writable by other users" in problem

    monkeypatch.delenv("DVC_SITE_CACHE_DIR", raising=False)
    seen_env: list[dict[str, str] | None] = []

    def fake_run(args, *, cwd, timeout=None, env=None):
        seen_env.append(env)
        assert env is not None and "DVC_SITE_CACHE_DIR" in env
        temp_dir = Path(env["DVC_SITE_CACHE_DIR"])
        assert temp_dir.is_dir()
        if os.name != "nt":
            assert temp_dir.stat().st_mode & 0o777 == 0o700
        return subprocess.CompletedProcess(args, 0, json.dumps({"status": {}, "stages": []}), "")

    monkeypatch.setattr(project_tool, "_run", fake_run)
    pipeline, problem = project_tool._dvc_pipeline(target, read_only=True)
    assert problem == ""
    assert pipeline == {"status": {}, "stages": []}
    assert seen_env and seen_env[0] is not None
    assert not Path(seen_env[0]["DVC_SITE_CACHE_DIR"]).exists()


def test_validation_skips_dvc_commands_when_cache_setup_fails(monkeypatch, tmp_path: Path) -> None:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    (target / ".dvc").mkdir()
    (target / ".dvc/config").write_text("[core]\n    analytics = false\n", encoding="utf-8")
    (target / "dvc.yaml").write_text("stages: {}\n", encoding="utf-8")
    _git_baseline(target)
    external = tmp_path / "external"
    external.mkdir()
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(external, target_is_directory=True)
    monkeypatch.setenv("ASTACK_DVC_CACHE_ROOT", str(cache_link))
    monkeypatch.delenv("DVC_SITE_CACHE_DIR", raising=False)

    ran: list[list[str]] = []

    def fake_run(args, *, cwd, input_text=None, timeout=None, env=None):
        ran.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(project_tool, "_run", fake_run)
    result = project_tool.audit(target, run_tools=True)

    assert any(item["check"] == "dvc-cache-configuration" for item in result["findings"])
    assert not any("dvc" in args for args in ran)


def test_dvc_cache_status_blocks_an_unavailable_cache(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    (target / ".dvc/config").parent.mkdir(parents=True, exist_ok=True)
    (target / ".dvc/config").write_text("[core]\n    analytics = false\n", encoding="utf-8")
    _git_baseline(target)

    result = project_tool.run_dvc_cache_status(target)
    assert result["status"] == "blocked"
    assert result["cache_exists"] is False
    assert result["reason"] == "DVC cache is unavailable"

    applied = project_tool.run_dvc_cache_gc(target, apply=True)
    assert applied["applied"] is False
    assert applied["reason"] == "DVC cache is unavailable"


def test_dvc_cache_status_and_gc_protect_referenced_objects(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    (target / ".dvc/config").parent.mkdir(parents=True, exist_ok=True)
    (target / ".dvc/config").write_text("[core]\n    analytics = false\n", encoding="utf-8")
    (target / ".astack/local.toml").write_text(
        '[dvc]\ncache_dir = "../shared-dvc-cache"\n', encoding="utf-8"
    )
    cache = target.parent / "shared-dvc-cache/files/md5"
    cache.mkdir(parents=True)
    referenced = cache / ("a" * 32)
    reclaimable = cache / ("b" * 32)
    directory = cache / "cc" / (("c" * 32)[2:] + ".dir")
    directory.parent.mkdir(parents=True)
    referenced.write_text("referenced\n", encoding="utf-8")
    reclaimable.write_text("reclaimable\n", encoding="utf-8")
    directory.write_text(json.dumps([{"md5": "a" * 32, "relpath": "result.txt"}]), encoding="utf-8")
    (target / "dvc.lock").write_text(
        "stages:\n"
        "  build:\n"
        "    outs:\n"
        f"      - md5: {'a' * 32}\n"
        "        path: data/processed/result.txt\n"
        f"      - md5: {'c' * 32}\n"
        "        path: data/processed/result-dir\n",
        encoding="utf-8",
    )
    _git_baseline(target)

    before = sorted(path.relative_to(target.parent).as_posix() for path in cache.rglob("*"))
    result = project_tool.run_dvc_cache_status(target)
    after = sorted(path.relative_to(target.parent).as_posix() for path in cache.rglob("*"))

    assert result["status"] == "ok"
    assert result["object_count"] == 3
    assert result["protected_objects"] == 2
    assert result["reclaimable_objects"] == 1
    assert result["reclaimable_bytes"] == len("reclaimable\n")
    assert before == after

    preview = project_tool.run_dvc_cache_gc(target)
    assert preview["dry_run"] is True
    assert reclaimable.exists()

    applied = project_tool.run_dvc_cache_gc(target, apply=True)
    assert applied["applied"] is True
    assert not reclaimable.exists()
    assert referenced.exists()
    assert directory.exists()


def test_dvc_cache_gc_refuses_while_a_native_linked_worktree_is_active(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _set_capabilities(target, ["dvc"])
    (target / ".dvc/config").parent.mkdir(parents=True, exist_ok=True)
    (target / ".dvc/config").write_text("[core]\n    analytics = false\n", encoding="utf-8")
    (target / ".astack/local.toml").write_text(
        '[dvc]\ncache_dir = "../shared-dvc-cache"\n', encoding="utf-8"
    )
    cache = target.parent / "shared-dvc-cache/files/md5"
    cache.mkdir(parents=True)
    reclaimable = cache / ("b" * 32)
    reclaimable.write_text("reclaimable\n", encoding="utf-8")
    (target / "dvc.lock").write_text("stages: {}\n", encoding="utf-8")
    _git_baseline(target)
    linked = tmp_path / "linked"
    import subprocess

    subprocess.run(
        ["git", "worktree", "add", "-b", "stage", str(linked)],
        cwd=target,
        check=True,
        capture_output=True,
    )

    status = project_tool.run_dvc_cache_status(target)
    assert str(linked.resolve()) in [
        str(Path(path).resolve()) for path in status["active_worktrees"]
    ]
    refused = project_tool.run_dvc_cache_gc(target, apply=True)
    assert refused["applied"] is False
    assert refused["reason"] == "refusing cache deletion while linked worktrees are active"
    assert reclaimable.exists()


def test_runtime_parser_supports_acquisition_and_evidence_promotion() -> None:
    reproduce = project_tool._parser().parse_args(["reproduce", "--promote"])
    acquire = project_tool._parser().parse_args(["acquire"])
    dvc_status = project_tool._parser().parse_args(["dvc-status"])
    verify = project_tool._parser().parse_args(["validate", "verify"])

    assert reproduce.promote is True
    assert acquire.command == "acquire"
    assert dvc_status.command == "dvc-status"
    assert verify.tier == "verify"


def test_audit_analysis_is_an_explicit_input_not_a_registry_lookup(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = project_tool._parser()
    scoped = parser.parse_args(["audit", "--strict", "--analysis", "plot"])
    # `--output-scope` is the option's old name and keeps working.
    legacy = parser.parse_args(["audit", "--strict", "--output-scope", "plot"])
    unscoped = parser.parse_args(["audit", "--strict"])
    assert scoped.analysis == legacy.analysis == "plot"
    assert unscoped.analysis is None
    with pytest.raises(SystemExit):
        parser.parse_args(["audit", "--help"])
    shown = capsys.readouterr().out
    assert "--analysis" in shown and "--output-scope" not in shown
    assert "resolve_work_unit" not in vars(project_tool)


def test_every_runtime_command_has_help_and_file_audit_replaces_publish() -> None:
    parser = project_tool._parser()
    commands = next(a for a in parser._actions if a.dest == "command")
    listed = {choice.dest: choice.help for choice in commands._choices_actions}
    assert "file-audit" in listed and "publish" not in listed
    assert all(listed.values()), listed
    assert not re.search(r"\bpublish\b", parser.format_help())
    subcommands = next(a for a in parser._actions if a.dest == "command").choices
    assert "--publish" not in subcommands["file-audit"].format_help()
    common = ["--report", "r.md", "--commit", "a" * 40, "--scope", "full"]
    common += ["--actionable-count", "0"]
    new = parser.parse_args(["file-audit", *common, "--to", "local"])
    old = parser.parse_args(["publish", *common, "--publish", "local"])
    assert new.publish == old.publish == "local"
    assert parser.parse_args(["file-audit", *common]).publish == "auto"


def test_file_audit_runs_the_report_filer(tmp_path, monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake(root, report, **kwargs):
        captured.update(kwargs)
        return {"status": kwargs["publish"], "report": str(report), "url": None}

    monkeypatch.setattr(project_tool, "publish_audit", fake)
    monkeypatch.chdir(tmp_path)
    argv = ["workflow.py", "file-audit", "--report", "r.md", "--commit", "a" * 40]
    argv += ["--scope", "full", "--to", "local", "--actionable-count", "2"]
    monkeypatch.setattr(sys, "argv", argv)
    project_tool.main()
    assert captured["publish"] == "local" and captured["actionable_count"] == 2
    assert json.loads(capsys.readouterr().out)["status"] == "local"
