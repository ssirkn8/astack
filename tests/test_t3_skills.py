"""Pinned bytes and safe offline delivery, without a live project or GitHub mutation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from astack import upstream_skills as skills

ROOT = Path(__file__).resolve().parents[1]


def test_stdlib_wrapper_verifies_without_the_package():
    # Plain python3 with isolated site-packages: the clone-and-run path needs no dependencies.
    result = subprocess.run(
        [sys.executable, "-I", "scripts/t3_skills.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["verified_files"] == 24


def test_pinned_dependency_closure_and_helpers():
    lock = json.loads((ROOT / skills.LOCK).read_text())
    assert {s["repository"] for s in lock["sources"]} == {
        "https://github.com/t3dotgg/tstack",
        "https://github.com/mattpocock/skills",
    }
    files = skills.payload(ROOT)
    assert {p.split("/")[0] for p in files if p.endswith("/SKILL.md")} == {
        "full-send",
        "full-stop",
        "council",
        "sol-loop",
        "file-pr",
        "babysit-pr",
        "takeover",
        "modernize",
        "leaving-pr-comments",
        "grill-me",
        "grilling",
        "to-spec",
        "to-tickets",
    }
    assert files["babysit-pr/scripts/pr-wait"][1]
    for p in [
        ".astack-notices/tstack/LICENSE",
        ".astack-notices/matt-pocock/LICENSE",
    ]:
        assert p in files


@pytest.mark.parametrize("profile", ["General", "DS"])
def test_disposable_delivery_preview_apply_retry_preserves_project(tmp_path, profile):
    (tmp_path / "project.txt").write_text(profile)
    if profile == "DS":
        (tmp_path / "data/raw").mkdir(parents=True)
        (tmp_path / "data/raw/input.csv").write_text("immutable\n")
    dest = tmp_path / ".agents/skills"
    assert not skills.install(ROOT, dest)["applied"]
    assert not dest.exists()
    skills.install(ROOT, dest, apply=True)
    assert all(x["action"] == "unchanged" for x in skills.install(ROOT, dest)["files"])
    for relative, (data, executable) in skills.payload(ROOT).items():
        assert (dest / relative).read_bytes() == data
        assert bool((dest / relative).stat().st_mode & 0o111) == executable
    assert (tmp_path / "project.txt").read_text() == profile
    if profile == "DS":
        assert (tmp_path / "data/raw/input.csv").read_text() == "immutable\n"


def test_preflight_preserves_customized_owned_files_without_partial_writes(tmp_path):
    skills.install(ROOT, tmp_path, apply=True)
    changed = tmp_path / "grilling/SKILL.md"
    changed.write_text("owner change")
    missing = tmp_path / "full-send/SKILL.md"
    missing.unlink()
    with pytest.raises(ValueError, match="customized"):
        skills.install(ROOT, tmp_path, apply=True)
    assert not missing.exists()
    assert changed.read_text() == "owner change"


def _seed_retired(dest, *, edited=()):
    manifest = json.loads((dest / skills.MANIFEST).read_text())
    for relative in skills.RETIRED_SKILL_FILES:
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pinned " + relative.encode())
        manifest[relative] = {"sha256": skills.digest(path.read_bytes()), "executable": False}
        if relative in edited:
            path.write_text("owner edit")
    (dest / skills.MANIFEST).write_text(json.dumps(manifest))


def test_skill_update_retires_only_unedited_copies(tmp_path):
    skills.install(ROOT, tmp_path, apply=True)
    edited = "domain-modeling/SKILL.md"
    _seed_retired(tmp_path, edited={edited})
    (tmp_path / "grill-with-docs/notes.md").write_text("mine")

    preview = skills.install(ROOT, tmp_path)
    assert (tmp_path / "grill-with-docs/SKILL.md").exists()
    expected_kept = sorted(
        f for f in skills.RETIRED_SKILL_FILES if f.startswith("domain-modeling/")
    )
    assert preview["preserved_retired"] == expected_kept

    skills.install(ROOT, tmp_path, apply=True)
    assert (tmp_path / edited).read_text() == "owner edit"
    assert (tmp_path / "domain-modeling/ADR-FORMAT.md").is_file()
    assert (tmp_path / "domain-modeling/GLOSSARY-FORMAT.md").is_file()
    assert not (tmp_path / "grill-with-docs/SKILL.md").exists()
    assert not (tmp_path / "grill-with-docs/agents").exists()
    assert (tmp_path / "grill-with-docs/notes.md").read_text() == "mine"
    manifest = json.loads((tmp_path / skills.MANIFEST).read_text())
    assert not set(manifest) & skills.RETIRED_SKILL_FILES


def test_refuses_unknown_skill_and_symlink_destination(tmp_path):
    dest = tmp_path / "skills"
    (dest / "full-send").mkdir(parents=True)
    (dest / "full-send/SKILL.md").write_text("owner")
    with pytest.raises(ValueError, match="unknown"):
        skills.install(ROOT, dest, apply=True)
    link = tmp_path / "linked"
    link.symlink_to(dest, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        skills.install(ROOT, link, apply=True)


def test_updates_and_rollback_require_owned_unchanged_bytes(tmp_path):
    skills.install(ROOT, tmp_path, apply=True)
    relative = "full-stop/SKILL.md"
    original = (tmp_path / relative).read_bytes()
    replacement = b"reviewed replacement fixture"
    from unittest.mock import patch

    payload = skills.payload(ROOT)
    payload[relative] = (replacement, False)
    with patch.object(skills, "payload", return_value=payload):
        skills.install(ROOT, tmp_path, apply=True)
    assert (tmp_path / relative).read_bytes() == replacement
    skills.install(ROOT, tmp_path, apply=True)
    assert (tmp_path / relative).read_bytes() == original


def test_fails_closed_on_tampered_source(tmp_path):
    import shutil

    shutil.copytree(ROOT / "integrations/t3", tmp_path / "integrations/t3")
    shutil.copytree(ROOT / ".agents/skills", tmp_path / ".agents/skills")
    (tmp_path / ".agents/skills/full-send/SKILL.md").write_text("modified upstream")
    with pytest.raises(ValueError, match="integrity"):
        skills.payload(tmp_path)


def test_preflight_rejects_parent_file_before_any_copy(tmp_path):
    (tmp_path / "full-stop").write_text("owner file")
    with pytest.raises(ValueError, match="non-directory ancestor"):
        skills.install(ROOT, tmp_path, apply=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["full-stop"]


def test_preserves_custom_executable_mode(tmp_path):
    skills.install(ROOT, tmp_path, apply=True)
    script = tmp_path / "babysit-pr/scripts/pr-wait"
    script.chmod(0o644)
    with pytest.raises(ValueError, match="customized executable mode"):
        skills.install(ROOT, tmp_path, apply=True)
    assert not script.stat().st_mode & 0o111


def test_interrupted_initial_copy_is_not_claimed_complete(tmp_path, monkeypatch):
    replace = skills.os.replace
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(skills.os, "replace", fail_second)
        with pytest.raises(OSError, match="interruption"):
            skills.install(ROOT, tmp_path, apply=True)
    assert not (tmp_path / skills.MANIFEST).exists()
    with pytest.raises(ValueError, match="unknown"):
        skills.install(ROOT, tmp_path, apply=True)


def test_retired_skill_preserves_whole_unit_on_edit_or_mode_change(tmp_path):
    skills.install(ROOT, tmp_path, apply=True)
    record = json.loads((tmp_path / skills.MANIFEST).read_text())
    for relative in (
        "domain-modeling/SKILL.md",
        "domain-modeling/ADR-FORMAT.md",
        "domain-modeling/GLOSSARY-FORMAT.md",
        "grill-with-docs/SKILL.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = b"pinned " + relative.encode()
        target.write_bytes(data)
        target.chmod(0o644)
        record[relative] = {"sha256": skills.digest(data), "executable": False}
    (tmp_path / skills.MANIFEST).write_text(json.dumps(record))
    (tmp_path / "domain-modeling/SKILL.md").write_text("edited\n")
    (tmp_path / "grill-with-docs/SKILL.md").chmod(0o755)

    result = skills.install(ROOT, tmp_path, apply=True)

    assert (tmp_path / "domain-modeling/ADR-FORMAT.md").is_file()
    assert (tmp_path / "domain-modeling/GLOSSARY-FORMAT.md").is_file()
    assert (tmp_path / "grill-with-docs/SKILL.md").is_file()
    assert "domain-modeling/ADR-FORMAT.md" in result["preserved_retired"]
    assert "grill-with-docs/SKILL.md" in result["preserved_retired"]
    updated = json.loads((tmp_path / skills.MANIFEST).read_text())
    assert "domain-modeling/ADR-FORMAT.md" not in updated
    assert "grill-with-docs/SKILL.md" not in updated


def test_retired_skill_mid_update_change_restores_unlinked_files(tmp_path, monkeypatch):
    skills.install(ROOT, tmp_path, apply=True)
    _seed_retired(tmp_path)
    adr = tmp_path / "domain-modeling/ADR-FORMAT.md"
    skill_md = tmp_path / "domain-modeling/SKILL.md"
    expected_adr = adr.read_bytes()
    real_unlink = Path.unlink

    def edit_sibling_after_first_unlink(self, *args, **kwargs):
        real_unlink(self, *args, **kwargs)
        if self == adr:
            skill_md.write_text("edited mid-update\n")

    monkeypatch.setattr(Path, "unlink", edit_sibling_after_first_unlink)
    with pytest.raises(ValueError, match="changed during update"):
        skills.install(ROOT, tmp_path, apply=True)
    assert adr.read_bytes() == expected_adr
    assert skill_md.read_text() == "edited mid-update\n"


def test_retired_skill_rollback_leaves_recreated_file_intact(tmp_path, monkeypatch):
    skills.install(ROOT, tmp_path, apply=True)
    _seed_retired(tmp_path)
    adr = tmp_path / "domain-modeling/ADR-FORMAT.md"
    skill_md = tmp_path / "domain-modeling/SKILL.md"
    real_unlink = Path.unlink

    def recreate_and_edit_after_first_unlink(self, *args, **kwargs):
        real_unlink(self, *args, **kwargs)
        if self == adr:
            adr.write_text("recreated by owner\n")
            skill_md.write_text("edited mid-update\n")

    monkeypatch.setattr(Path, "unlink", recreate_and_edit_after_first_unlink)
    with pytest.raises(ValueError, match="changed during update"):
        skills.install(ROOT, tmp_path, apply=True)
    assert adr.read_text() == "recreated by owner\n"
    assert skill_md.read_text() == "edited mid-update\n"
