"""The opt-in experimental pr-review-pass skill and review-policy CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from project_tool_test_support import _git_baseline, project_tool

from astack.adapters import skill_wrapper
from astack.installation import MANIFEST, install_project
from astack.scaffold import create_project

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ".opencode/skills/pr-review-pass/SKILL.md"
WRAPPERS = (".agents/skills/pr-review-pass/SKILL.md", ".claude/skills/pr-review-pass/SKILL.md")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _create_project(tmp_path: Path, *, name: str, profile: str) -> Path:
    project = tmp_path / name
    if profile == "data-science":
        create_project(
            project,
            project_name=name,
            package_name=name.replace("-", "_"),
            description="Exercise pr-review-pass and review-policy.",
            lock=False,
        )
        return project
    project.mkdir(parents=True, exist_ok=True)
    commands = [["python3", "-c", "pass"]]
    plan = install_project(project, candidate=True, initialize=True, commands=commands)
    install_project(
        project,
        candidate=True,
        initialize=True,
        commands=commands,
        apply=True,
        approve_plan=plan["plan_digest"],
    )
    return project


def test_pr_review_pass_skill_and_wrappers_stay_opt_in_and_in_sync() -> None:
    source_bytes = (ROOT / CANONICAL).read_bytes()
    canonical = source_bytes.decode("utf-8")
    expected_wrapper = skill_wrapper(source_bytes, CANONICAL)

    assert "name: pr-review-pass" in canonical
    assert "permission-level: read-only" in canonical
    assert "experimental" in canonical.lower()
    assert "opt-in" in canonical.lower()
    assert "non-blocking" in canonical.lower()
    assert "review-policy" in canonical
    assert "REVIEW.md" in canonical
    assert "bug-class" in canonical.lower()
    assert "Retro (experimental pr-review-pass):" in canonical
    assert "workflow/project_tool.py review-policy" in canonical
    assert "python3 -B" in canonical
    assert "--root <candidate-root>" in canonical
    assert "uv run --locked" not in canonical
    assert (
        "git --no-replace-objects -c core.fsmonitor=false ls-tree <merge_base> -- REVIEW.md"
        in canonical
    )
    assert (
        "git --no-replace-objects -c core.fsmonitor=false show <merge_base>:REVIEW.md" in canonical
    )
    assert "--no-ext-diff --no-textconv --no-renames --ignore-submodules=none" in canonical
    assert (
        "diff --cached --no-ext-diff --no-textconv --no-renames --ignore-submodules=none"
        in canonical
    )
    assert "report the gate as unavailable" in canonical
    assert '`source: "explicit"`' in canonical
    assert "pending" in canonical
    assert not (ROOT / ".opencode/commands/pr-review-pass.md").exists()

    for relative in WRAPPERS:
        assert (ROOT / relative).read_bytes() == expected_wrapper


@pytest.mark.parametrize("profile", ["general", "data-science"])
def test_scaffold_delivers_pr_review_pass_without_review_md_or_agents_noise(
    tmp_path: Path, profile: str
) -> None:
    project = _create_project(tmp_path, name=f"demo-{profile}", profile=profile)
    source_bytes = (ROOT / CANONICAL).read_bytes()
    expected_wrapper = skill_wrapper(source_bytes, CANONICAL)

    assert (project / CANONICAL).read_bytes() == source_bytes
    for relative in WRAPPERS:
        assert (project / relative).read_bytes() == expected_wrapper
    assert not (project / "REVIEW.md").exists()
    assert not (project / ".opencode/commands/pr-review-pass.md").exists()
    if (project / "AGENTS.md").exists():
        assert "pr-review-pass" not in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "pr-review-pass" not in (project / ".astack/project.toml").read_text(encoding="utf-8")


@pytest.mark.integration
def test_upgrade_adds_pr_review_pass_while_preserving_owner_agents_and_review_md(
    tmp_path: Path,
) -> None:
    project = _create_project(tmp_path, name="upgrade-target", profile="general")
    paths = (CANONICAL, *WRAPPERS)
    manifest_path = project / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["managed_components"] = {
        key: value
        for key, value in manifest["managed_components"].items()
        if value["path"] not in paths
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for relative in paths:
        (project / relative).unlink()
        (project / relative).parent.rmdir()
    agents_note = "# Project rules\n\nKeep my own AGENTS.md untouched.\n"
    review_note = "# Local Review Rules\n\nFlag any unpinned random seed.\n"
    (project / "AGENTS.md").write_text(agents_note, encoding="utf-8")
    (project / "REVIEW.md").write_text(review_note, encoding="utf-8")
    _git_baseline(project)

    plan = install_project(project, candidate=True)
    assert not plan["conflicts"]
    actions = {action["path"]: action["action"] for action in plan["actions"]}
    assert all(actions[relative] != "unchanged" for relative in paths)
    install_project(project, candidate=True, apply=True, approve_plan=plan["plan_digest"])

    source_bytes = (ROOT / CANONICAL).read_bytes()
    assert (project / CANONICAL).read_bytes() == source_bytes
    for relative in WRAPPERS:
        assert (project / relative).read_bytes() == skill_wrapper(source_bytes, CANONICAL)
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == agents_note
    assert (project / "REVIEW.md").read_text(encoding="utf-8") == review_note


def test_review_policy_explicit_paths_and_lenses_for_general_and_ds(tmp_path: Path) -> None:
    general = _create_project(tmp_path, name="gen", profile="general")
    ds = _create_project(tmp_path, name="sci", profile="data-science")

    routine_docs = project_tool.run_review_policy(general, paths=["docs/notes.md"])
    assert routine_docs["source"] == "explicit"
    assert routine_docs["base"] is None
    assert routine_docs["merge_base"] is None
    assert routine_docs["risk_floor"] == "low"
    assert routine_docs["protected_paths"] == []
    assert routine_docs["scientific_paths"] == []
    assert routine_docs["required_axes"] == ["acceptance", "engineering_reproducibility"]
    assert routine_docs["lenses"] == []

    routine_code = project_tool.run_review_policy(general, paths=["src/app.py"])
    assert routine_code["risk_floor"] == "medium"
    assert routine_code["lenses"] == []

    protected = project_tool.run_review_policy(general, paths=["./.github/workflows/ci.yml"])
    assert protected["risk_floor"] == "high"
    assert protected["protected_paths"] == [".github/workflows/ci.yml"]
    assert protected["paths"] == [".github/workflows/ci.yml"]
    assert protected["lenses"] == ["security"]

    science = project_tool.run_review_policy(general, paths=["analysis/model.py"])
    assert science["risk_floor"] == "high"
    assert science["scientific_paths"] == ["analysis/model.py"]
    assert "scientific_credibility" in science["required_axes"]
    assert science["lenses"] == ["scientific-robustness"]

    ds_both = project_tool.run_review_policy(ds, paths=["src/app.py", ".astack/project.toml"])
    assert ds_both["risk_floor"] == "high"
    assert ds_both["protected_paths"] == [".astack/project.toml"]
    assert "scientific_credibility" in ds_both["required_axes"]
    assert ds_both["lenses"] == ["security", "scientific-robustness"]


def test_review_policy_git_branch_diff_worktree_renames_and_base_override(tmp_path: Path) -> None:
    project = _create_project(tmp_path, name="git-gen", profile="general")
    (project / "src").mkdir(exist_ok=True)
    (project / "src/plain.py").write_text("x = 1\n", encoding="utf-8")
    (project / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (project / ".github/workflows/ci.yml").write_text("name: ci\n", encoding="utf-8")
    _git_baseline(project)
    base_sha = _git(project, "rev-parse", "HEAD")

    # Clean branch returns empty paths and conservative high risk floor (unknown evidence).
    clean = project_tool.run_review_policy(project)
    assert clean["source"] == "git"
    assert clean["paths"] == []
    assert clean["risk_floor"] == "high"
    assert clean["lenses"] == []

    # Feature branch with a routine committed edit -> medium risk, no extra lenses.
    _git(project, "checkout", "-b", "feat/routine")
    (project / "src/plain.py").write_text("x = 2\n", encoding="utf-8")
    _git(project, "add", "src/plain.py")
    _git(project, "commit", "-m", "routine change")

    routine = project_tool.run_review_policy(project)
    assert routine["source"] == "git"
    assert routine["merge_base"] == base_sha
    assert routine["paths"] == ["src/plain.py"]
    assert routine["risk_floor"] == "medium"
    assert routine["lenses"] == []

    # Staged rename of a protected file must still inspect the old protected path (--no-renames).
    (project / "docs").mkdir(exist_ok=True)
    _git(project, "mv", ".github/workflows/ci.yml", "docs/retired-ci.txt")
    # Also add an unstaged edit and an untracked file with space and non-ASCII characters.
    (project / "src/plain.py").write_text("x = 3\n", encoding="utf-8")
    (project / "analysis").mkdir(exist_ok=True)
    (project / "analysis/café summary.py").write_text("y = 1\n", encoding="utf-8")

    combined = project_tool.run_review_policy(project, base=base_sha)
    assert combined["base"] == base_sha
    assert combined["base_commit"] == base_sha
    assert combined["merge_base"] == base_sha
    assert ".github/workflows/ci.yml" in combined["paths"]
    assert "docs/retired-ci.txt" in combined["paths"]
    assert "analysis/café summary.py" in combined["paths"]
    assert combined["protected_paths"] == [".github/workflows/ci.yml"]
    assert combined["scientific_paths"] == ["analysis/café summary.py"]
    assert combined["lenses"] == ["security", "scientific-robustness"]


def test_review_policy_fails_closed_on_invalid_inputs_and_non_git_dir(tmp_path: Path) -> None:
    project = tmp_path / "no-git"
    (project / ".astack").mkdir(parents=True)
    (project / ".astack/project.toml").write_text('profile = "general"\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="not in a Git worktree"):
        project_tool.run_review_policy(project)
    with pytest.raises(ValueError, match="cannot be combined"):
        project_tool.run_review_policy(project, base="main", paths=["src/app.py"])

    _git_baseline(project)
    with pytest.raises(ValueError, match="Invalid Git base ref"):
        project_tool.run_review_policy(project, base="--all")
    with pytest.raises(ValueError, match="Could not resolve base ref"):
        project_tool.run_review_policy(project, base="nonexistent-branch-ref")

    # Symlinked project config is rejected fail-closed.
    outside = tmp_path / "outside.toml"
    outside.write_text('profile = "general"\n', encoding="utf-8")
    config_path = project / ".astack/project.toml"
    config_path.unlink()
    config_path.symlink_to(outside)
    with pytest.raises(OSError, match="symlink"):
        project_tool.run_review_policy(project, paths=["docs/notes.md"])


def test_review_policy_does_not_run_git_clean_filters_and_handles_edge_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _create_project(tmp_path, name="hardened", profile="general")
    (project / "src").mkdir(exist_ok=True)
    (project / "src/plain.py").write_text("x = 1\n", encoding="utf-8")
    marker = tmp_path / "filter-ran.txt"
    (project / ".gitattributes").write_text("*.py filter=pwn\n", encoding="utf-8")
    _git_baseline(project)
    _git(project, "config", "filter.pwn.clean", f"sh -c 'echo pwned > {marker}; cat'")

    # S1: Unstaged worktree edit must be detected via raw bytes without running Git clean filters.
    (project / "src/plain.py").write_text("x = 2\n", encoding="utf-8")
    policy = project_tool.run_review_policy(project)
    assert not marker.exists()
    assert policy["paths"] == ["src/plain.py"]
    assert policy["lenses"] == []

    # S2: Unresolvable GITHUB_BASE_REF must fail closed instead of falling back to main.
    monkeypatch.setenv("GITHUB_BASE_REF", "missing-ci-base")
    with pytest.raises(RuntimeError, match="GITHUB_BASE_REF"):
        project_tool.run_review_policy(project)
    monkeypatch.delenv("GITHUB_BASE_REF")

    # S4 (part 1): Running from a subdirectory fails closed even if a nested project.toml exists.
    nested_cfg = project / "src/.astack/project.toml"
    nested_cfg.parent.mkdir(parents=True, exist_ok=True)
    nested_cfg.write_text('profile = "general"\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="Git repository root"):
        project_tool.run_review_policy(project / "src")
    with pytest.raises(RuntimeError, match="Git repository root"):
        project_tool.run_review_policy(project / "src", paths=["plain.py"])
    nested_cfg.unlink()
    nested_cfg.parent.rmdir()

    # S4 (part 2): Committed gitlink update under release/ is caught even with ignoreSubmodules=all.
    sub_origin = tmp_path / "sub-origin"
    sub_origin.mkdir()
    (sub_origin / "a.txt").write_text("v1\n", encoding="utf-8")
    _git_baseline(sub_origin)
    sub_v1 = _git(sub_origin, "rev-parse", "HEAD")
    (sub_origin / "a.txt").write_text("v2\n", encoding="utf-8")
    _git(sub_origin, "add", "a.txt")
    _git(sub_origin, "commit", "-qm", "v2")
    sub_v2 = _git(sub_origin, "rev-parse", "HEAD")

    _git(project, "checkout", "--", "src/plain.py")
    (project / "release/sub").mkdir(parents=True, exist_ok=True)
    _git(project, "update-index", "--add", "--cacheinfo", f"160000,{sub_v1},release/sub")
    _git(project, "commit", "-qm", "add gitlink")
    base_with_sub = _git(project, "rev-parse", "HEAD")
    assert project_tool.run_review_policy(project, base=base_with_sub)["paths"] == []
    _git(project, "checkout", "-qb", "feat/sub-bump")
    _git(project, "config", "diff.ignoreSubmodules", "all")
    _git(project, "update-index", "--cacheinfo", f"160000,{sub_v2},release/sub")
    _git(project, "commit", "-qm", "bump gitlink")

    sub_policy = project_tool.run_review_policy(project, base=base_with_sub)
    assert "release/sub" in sub_policy["paths"]
    assert "release/sub" in sub_policy["protected_paths"]
    assert sub_policy["lenses"] == ["security"]

    # S5: Literal CR and backslash in POSIX Git filenames stay intact in reported paths.
    if sys.platform != "win32":
        cr_name = "src/line\rname.py"
        bs_name = r"src/slash\name.py"
        (project / cr_name).write_text("a = 1\n", encoding="utf-8")
        (project / bs_name).write_text("b = 1\n", encoding="utf-8")
        literal_policy = project_tool.run_review_policy(project, base=base_with_sub)
        assert cr_name in literal_policy["paths"]
        assert bs_name in literal_policy["paths"]
        assert "src/line\nname.py" not in literal_policy["paths"]
        assert "src/slash/name.py" not in literal_policy["paths"]


def test_review_policy_dirty_submodule_base_floor_review_md_and_sparse_checkout(
    tmp_path: Path,
) -> None:
    # S9 & S10: Base scientific_paths cannot be suppressed by candidate config edits, and root
    # REVIEW.md alone activates the security lens while docs/REVIEW.md stays docs-only.
    project = _create_project(tmp_path, name="trust-boundaries", profile="general")
    config_path = project / ".astack/project.toml"
    original_config = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original_config + '\n[review]\nscientific_paths = ["models/**"]\n',
        encoding="utf-8",
    )
    (project / "models").mkdir(exist_ok=True)
    (project / "models/fit.py").write_text("x = 1\n", encoding="utf-8")
    (project / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (project / ".github/workflows/ci.yml").write_text("name: ci\n", encoding="utf-8")
    _git_baseline(project)
    base_sha = _git(project, "rev-parse", "HEAD")

    # S10: Standalone root REVIEW.md edit triggers security lens; docs/REVIEW.md does not.
    assert project_tool.run_review_policy(project, paths=["REVIEW.md"])["lenses"] == ["security"]
    assert project_tool.run_review_policy(project, paths=["docs/REVIEW.md"])["lenses"] == []

    # S9: Removing scientific_paths in candidate config cannot drop scientific-robustness lens.
    _git(project, "checkout", "-qb", "feat/drop-science")
    config_path.write_text(original_config, encoding="utf-8")
    (project / "models/fit.py").write_text("x = 2\n", encoding="utf-8")
    _git(project, "add", ".astack/project.toml", "models/fit.py")
    _git(project, "commit", "-qm", "drop science config and edit model")

    tampered = project_tool.run_review_policy(project, base=base_sha)
    assert ".astack/project.toml" in tampered["protected_paths"]
    assert "models/fit.py" in tampered["matched_scientific_paths"]
    assert "scientific_credibility" in tampered["required_axes"]
    assert tampered["lenses"] == ["security", "scientific-robustness"]

    # S8: Trusted external classifier can inspect candidate root via `--root` without executing
    # candidate Python files (and S3: running `workflow/project_tool.py` writes no repo `.pyc`
    # files, while `python3 -B` writes zero `.pyc` files anywhere).
    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    pycache_no_b = tmp_path / "pycache-no-b"
    pycache_no_b.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "workflow/project_tool.py"),
            "review-policy",
            "--root",
            str(project),
            "--base",
            base_sha,
        ],
        cwd=tmp_path,
        env={**clean_env, "PYTHONPYCACHEPREFIX": str(pycache_no_b)},
        capture_output=True,
        text=True,
        check=True,
    )
    external_payload = json.loads(proc.stdout)
    assert external_payload["lenses"] == ["security", "scientific-robustness"]
    repo_pyc = [
        p
        for p in pycache_no_b.rglob("*.pyc")
        if "/workflow/" in p.as_posix() or "/src/astack/" in p.as_posix()
    ]
    assert repo_pyc == []

    pycache_with_b = tmp_path / "pycache-with-b"
    pycache_with_b.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "workflow/project_tool.py"),
            "review-policy",
            "--root",
            str(project),
            "--base",
            base_sha,
        ],
        cwd=tmp_path,
        env={**clean_env, "PYTHONPYCACHEPREFIX": str(pycache_with_b)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert list(pycache_with_b.rglob("*.pyc")) == []

    # S9 (HEAD & staged index snapshots): A scientific_paths declaration added in HEAD or staged
    # in the index stays active even when the worktree holds a different copy.
    head_staged_proj = _create_project(tmp_path, name="head-staged", profile="general")
    hs_cfg = head_staged_proj / ".astack/project.toml"
    hs_base_cfg = hs_cfg.read_text(encoding="utf-8")
    (head_staged_proj / "models").mkdir(exist_ok=True)
    (head_staged_proj / "models/fit.py").write_text("x = 1\n", encoding="utf-8")
    _git_baseline(head_staged_proj)
    hs_base = _git(head_staged_proj, "rev-parse", "HEAD")

    _git(head_staged_proj, "checkout", "-qb", "feat/staged-science")
    hs_cfg.write_text(
        hs_base_cfg + '\n[review]\nscientific_paths = ["models/**"]\n',
        encoding="utf-8",
    )
    (head_staged_proj / "models/fit.py").write_text("x = 2\n", encoding="utf-8")
    _git(head_staged_proj, "add", ".astack/project.toml", "models/fit.py")
    # Overwrite worktree config back to general without staging; staged index must still apply.
    hs_cfg.write_text(hs_base_cfg, encoding="utf-8")
    staged_policy = project_tool.run_review_policy(head_staged_proj, base=hs_base)
    assert "models/fit.py" in staged_policy["matched_scientific_paths"]
    assert staged_policy["lenses"] == ["security", "scientific-robustness"]

    # Commit the staged scientific declaration into HEAD, then clear both index and worktree.
    _git(head_staged_proj, "commit", "-qm", "declare science in HEAD")
    _git(head_staged_proj, "add", ".astack/project.toml")
    head_policy = project_tool.run_review_policy(head_staged_proj, base=hs_base)
    assert "models/fit.py" in head_policy["matched_scientific_paths"]
    assert head_policy["lenses"] == ["security", "scientific-robustness"]

    # S4: Dirty initialized submodule at the same HEAD (staged or unstaged) is detected filter-free.
    sub_repo = tmp_path / "dirty-sub-origin"
    sub_repo.mkdir()
    (sub_repo / "file.py").write_text("v = 1\n", encoding="utf-8")
    _git_baseline(sub_repo)
    _git(project, "checkout", "-q", "main")
    _git(project, "checkout", "-qb", "feat/submodule")
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_repo), "release/s"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    _git(project, "commit", "-qm", "add submodule")
    sub_base = _git(project, "rev-parse", "HEAD")
    assert project_tool.run_review_policy(project, base=sub_base)["paths"] == []

    # Unstaged edit inside submodule at identical HEAD -> detected as changed release/s.
    (project / "release/s/file.py").write_text("v = 2\n", encoding="utf-8")
    unstaged_sub = project_tool.run_review_policy(project, base=sub_base)
    assert "release/s" in unstaged_sub["paths"]
    assert unstaged_sub["lenses"] == ["security"]

    # Staged edit inside submodule at identical HEAD -> also detected.
    _git(project / "release/s", "add", "file.py")
    staged_sub = project_tool.run_review_policy(project, base=sub_base)
    assert "release/s" in staged_sub["paths"]
    assert staged_sub["lenses"] == ["security"]

    # S11: Clean sparse checkout does not flag intentionally omitted files as deleted/changed.
    sparse_proj = _create_project(tmp_path, name="sparse-proj", profile="general")
    (sparse_proj / "src").mkdir(exist_ok=True)
    (sparse_proj / "src/app.py").write_text("a = 1\n", encoding="utf-8")
    (sparse_proj / ".github/workflows").mkdir(parents=True, exist_ok=True)
    (sparse_proj / ".github/workflows/ci.yml").write_text("name: ci\n", encoding="utf-8")
    _git_baseline(sparse_proj)
    sparse_base = _git(sparse_proj, "rev-parse", "HEAD")

    _git(sparse_proj, "sparse-checkout", "init", "--cone")
    _git(sparse_proj, "sparse-checkout", "set", "src", ".astack")
    assert not (sparse_proj / ".github/workflows/ci.yml").exists()
    clean_sparse = project_tool.run_review_policy(sparse_proj, base=sparse_base)
    assert clean_sparse["paths"] == []
    assert clean_sparse["lenses"] == []

    # Modifying a checked-out file in the sparse checkout is still detected cleanly.
    (sparse_proj / "src/app.py").write_text("a = 2\n", encoding="utf-8")
    edited_sparse = project_tool.run_review_policy(sparse_proj, base=sparse_base)
    assert edited_sparse["paths"] == ["src/app.py"]
    assert edited_sparse["lenses"] == []


@pytest.mark.integration
def test_vendored_workflow_cli_review_policy_runs_end_to_end(tmp_path: Path) -> None:
    project = _create_project(tmp_path, name="cli-e2e", profile="general")
    (project / "src").mkdir(exist_ok=True)
    (project / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    _git_baseline(project)

    _git(project, "checkout", "-b", "feat/security-touch")
    (project / "src/auth.py").write_text("TOKEN = 'placeholder'\n", encoding="utf-8")
    _git(project, "add", "src/auth.py")
    _git(project, "commit", "-m", "touch auth")

    completed = subprocess.run(
        [sys.executable, ".astack/workflow.py", "review-policy"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["source"] == "git"
    assert payload["risk_floor"] == "high"
    assert payload["protected_paths"] == ["src/auth.py"]
    assert payload["lenses"] == ["security"]

    explicit = subprocess.run(
        [sys.executable, ".astack/workflow.py", "review-policy", "docs/readme.md"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    explicit_payload = json.loads(explicit.stdout)
    assert explicit_payload["source"] == "explicit"
    assert explicit_payload["risk_floor"] == "low"
    assert explicit_payload["lenses"] == []
