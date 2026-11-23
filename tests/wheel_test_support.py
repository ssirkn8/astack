"""Build and install a real candidate wheel for public and private tests."""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def installed_wheel(tmp_path: Path) -> tuple[Path, Path, Path]:
    distribution = tmp_path / "dist"
    environment = tmp_path / "wheel-venv"
    source = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "tracked-checkout"
    checkout.mkdir()
    # Include new nonignored files so generated package assets are tested before checkpointing.
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for relative in filter(None, tracked.split("\0")):
        original = source / relative
        if not original.is_file():
            continue
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    assert not (checkout / ".opencode/agents").exists()
    assert not (checkout / ".venv").exists()
    # CI installs the project editable before release checks. Verify that path as well as wheels.
    editable = subprocess.run(
        ["uv", "sync", "--locked"], cwd=checkout, capture_output=True, text=True
    )
    assert editable.returncode == 0, editable.stderr
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(distribution)],
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    wheels = list(distribution.glob("*.whl"))
    assert len(wheels) == 1
    # Exactly the locked upstream files ship, plus lock and guide: no retained or extra skills.
    lock = json.loads((source / "integrations/t3/upstream.lock.json").read_text(encoding="utf-8"))
    prefix = "astack/assets/"
    expected = {item["path"] for entry in lock["sources"] for item in entry["files"]} | {
        "integrations/t3/upstream.lock.json",
        "integrations/t3/PROJECT-GUIDE.md",
    }
    with zipfile.ZipFile(wheels[0]) as archive:
        members = {
            name.removeprefix(prefix)
            for name in archive.namelist()
            if name.startswith((prefix + ".agents/", prefix + "integrations/"))
        }
    assert members == expected
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(environment)],
        check=True,
        capture_output=True,
    )
    python = environment / "bin/python"
    cli = environment / "bin/astack"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheels[0])],
        check=True,
        capture_output=True,
    )

    return python, cli, source
