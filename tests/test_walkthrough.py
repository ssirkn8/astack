"""End-to-end verification of the stargazing-spots walkthrough in docs/walkthrough.md."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from project_tool_test_support import (
    _git_baseline,
    no_ci_push_base,  # noqa: F401
    project_tool,
)

from astack.scaffold import create_project

# End-to-end reproduction and audit walkthrough: integration tier, matching other repro tests.
pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
WALKTHROUGH_DOC = REPO_ROOT / "docs/walkthrough.md"


def _clean_env() -> dict[str, str]:
    """Drop the parent repo's VIRTUAL_ENV so `uv` targets the generated project's own .venv."""
    return {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}


def _extract_doc_block(label: str) -> str:
    """Extract the fenced code block immediately following a bold **`<label>`** heading."""
    text = WALKTHROUGH_DOC.read_text(encoding="utf-8")
    pattern = rf"\*\*`{re.escape(label)}`[^\n]*\n\n```[a-zA-Z0-9_-]*\n(.*?)```"
    match = re.search(pattern, text, flags=re.DOTALL)
    assert match is not None, f"Missing fenced block for {label} in {WALKTHROUGH_DOC}"
    return match.group(1)


def _chmod_tree(path: Path, *, writable: bool) -> None:
    for current, dirs, files in os.walk(path):
        cur_path = Path(current)
        if writable:
            cur_path.chmod(cur_path.stat().st_mode | stat.S_IWUSR)
        for name in files:
            file_path = cur_path / name
            mode = file_path.stat().st_mode
            if writable:
                file_path.chmod(mode | stat.S_IWUSR)
            else:
                file_path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        if not writable:
            for d in dirs:
                dp = cur_path / d
                dp.chmod(dp.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    if not writable:
        path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Observer",
            "-c",
            "user.email=observer@example.com",
            "commit",
            "-qm",
            message,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _run_cmd(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=root,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_wf(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run_cmd(root, "uv", "run", "--locked", "python", ".astack/workflow.py", *args)


def _promote_and_verify(root: Path) -> None:
    """Promote a clean baseline run and verify an identical rerun via `just reproduce`."""
    promoted = _run_cmd(root, "just", "reproduce", "--promote")
    assert promoted.returncode == 0, promoted.stdout + promoted.stderr
    _commit(root, "Promote baseline reproduction")

    verified = _run_cmd(root, "just", "reproduce", "--promote", "--verify")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    payload = json.loads(verified.stdout)
    assert payload["status"] == "verified", payload
    _commit(root, "Record verified reproduction")


def test_stargazing_spots_walkthrough_end_to_end(tmp_path: Path) -> None:
    """Execute the exact scripts, configs, and public commands from docs/walkthrough.md."""
    # Match `astack new stargazing-spots --no-dvc`, which enables Marimo by default.
    target = tmp_path / "stargazing-spots"
    create_project(
        target,
        project_name="Stargazing Spots",
        package_name="stargazing_spots",
        description="Rank dark-sky viewpoints from synthetic monthly VIIRS radiance tiles",
        intended_use="Weekend dark-sky map and reproducible pilot report",
        tools=["marimo"],
        lock=False,
    )
    try:
        # Add walkthrough dependencies, verify marker, and mypy overrides, then lock and sync.
        pyproject = target / "pyproject.toml"
        pyproject_text = pyproject.read_text(encoding="utf-8")
        pyproject_text = pyproject_text.replace(
            "dependencies = []",
            'dependencies = ["pyarrow>=15", "rasterio>=1.4", "numpy>=2.0"]',
        ).replace(
            '  "integration: tests that exercise integration-heavy behavior",\n]',
            '  "integration: tests that exercise integration-heavy behavior",\n'
            '  "verify: fixture-backed verification checks for material analysis",\n]',
        )
        pyproject_text += "\n" + _extract_doc_block("pyproject.toml")
        pyproject.write_text(pyproject_text, encoding="utf-8")

        lock_res = _run_cmd(target, "uv", "lock")
        assert lock_res.returncode == 0, lock_res.stdout + lock_res.stderr
        sync_res = _run_cmd(target, "uv", "sync", "--locked")
        assert sync_res.returncode == 0, sync_res.stdout + sync_res.stderr

        # Before you start: extract and run the exact fixture generator from docs/walkthrough.md.
        (target / "scripts").mkdir(exist_ok=True)
        (target / "scripts/make_fixtures.py").write_text(
            _extract_doc_block("scripts/make_fixtures.py"), encoding="utf-8"
        )
        gen_res = _run_cmd(target, "uv", "run", "--locked", "python", "scripts/make_fixtures.py")
        assert gen_res.returncode == 0, gen_res.stdout + gen_res.stderr
        _chmod_tree(target / "data/raw", writable=False)

        viirs_check = _run_wf(target, "checksum", "data/raw/viirs_monthly")
        sites_check = _run_wf(target, "checksum", "data/raw/sites.parquet")
        assert viirs_check.returncode == 0, viirs_check.stderr
        assert sites_check.returncode == 0, sites_check.stderr
        viirs_checksum = json.loads(viirs_check.stdout)["checksum"]
        sites_checksum = json.loads(sites_check.stdout)["checksum"]
        assert viirs_checksum.startswith("sha256-tree-v2:")
        assert sites_checksum.startswith("sha256:")

        # Turn 1: extract exact analyze.py, verify test, sources.yml, and project.toml verbatim.
        sources_text = (
            _extract_doc_block("data/sources.yml")
            .replace("sha256-tree-v2:<digest>", viirs_checksum)
            .replace("sha256:<digest>", sites_checksum)
        )
        (target / "data/sources.yml").write_text(sources_text, encoding="utf-8")
        (target / "scripts/analyze.py").write_text(
            _extract_doc_block("scripts/analyze.py"), encoding="utf-8"
        )
        (target / "tests/test_verify_walkthrough.py").write_text(
            _extract_doc_block("tests/test_verify_walkthrough.py"), encoding="utf-8"
        )

        config = target / ".astack/project.toml"
        base_config = config.read_text(encoding="utf-8")
        header = base_config[: base_config.index("[analysis]")]
        data_block = base_config[base_config.index("[data]") : base_config.index("\n[outputs]")]
        config.write_text(
            header + data_block + "\n\n" + _extract_doc_block(".astack/project.toml") + "\n",
            encoding="utf-8",
        )

        # Generate initial tracked outputs so `reproduce --promote` only adds the run record.
        run_res = _run_cmd(target, "uv", "run", "--locked", "python", "scripts/analyze.py")
        assert run_res.returncode == 0, run_res.stdout + run_res.stderr

        # Turn 2: extract and commit the exact Markdown report from docs/walkthrough.md.
        report_path = target / "reports/main.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        good_report = _extract_doc_block("reports/main.md")
        report_path.write_text(good_report, encoding="utf-8")

        _git_baseline(target)
        subprocess.run(["git", "config", "user.name", "Observer"], cwd=target, check=True)

        # Real unmodified `just check`, `just review-check`, and `just verify` must pass.
        for recipe in ("check", "review-check", "verify"):
            res = _run_cmd(target, "just", recipe)
            assert res.returncode == 0, f"just {recipe} failed:\n{res.stdout}\n{res.stderr}"

        _promote_and_verify(target)

        # Verify declared outputs preview cleanly (Parquet, JSON, SVG) and raster preview works.
        parquet_preview = project_tool.review_preview(
            target, "data/processed/analysis/site_summary.parquet"
        )
        assert parquet_preview["kind"] == "data"
        assert parquet_preview["table"].num_rows == 6

        json_preview = project_tool.review_preview(target, "evidence/analysis/metrics.json")
        assert json_preview["kind"] == "json"

        svg_preview = project_tool.review_preview(
            target, "reports/figures/analysis/sky_brightness.svg"
        )
        assert svg_preview["kind"] == "image"

        tif_preview = project_tool.review_preview(target, "data/raw/viirs_monthly/2026-04.tif")
        assert tif_preview["kind"] == "raster"
        assert tif_preview["png"] is not None
        assert any("EPSG:4326" in fact for fact in tif_preview["facts"])

        # Turn 2: `just report-check` passes on the unmodified report.
        rep_ok = _run_cmd(target, "just", "report-check", "reports/main.md")
        assert rep_ok.returncode == 0, rep_ok.stdout + rep_ok.stderr
        assert json.loads(rep_ok.stdout)["status"] == "passed"

        # Turn 3a: fudging a cited number fails report-check; git checkout recovers the report.
        report_path.write_text(good_report.replace("[3.65]", "[1.80]"), encoding="utf-8")
        rep_bad = _run_cmd(target, "just", "report-check", "reports/main.md")
        assert rep_bad.returncode != 0
        bad_payload = json.loads(rep_bad.stdout)
        kinds = [p["kind"] for r in bad_payload["reports"] for p in r["problems"]]
        assert "value-mismatch" in kinds
        subprocess.run(["git", "checkout", "--", "reports/main.md"], cwd=target, check=True)
        assert report_path.read_text(encoding="utf-8") == good_report

        # Turn 3b: back up, mutate a raw GeoTIFF in place, observe P0 failure, and restore.
        backup = target / "scratch/2026-05.tif.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        may_tif = target / "data/raw/viirs_monthly/2026-05.tif"
        shutil.copy2(may_tif, backup)
        _chmod_tree(target / "data/raw", writable=True)
        may_tif.write_bytes(may_tif.read_bytes() + b"\x00")
        _chmod_tree(target / "data/raw", writable=False)

        mutated = _run_cmd(target, "just", "ship-ready")
        assert mutated.returncode != 0
        assert "P0 [data-safety]" in mutated.stdout
        assert "viirs_monthly" in mutated.stdout

        _chmod_tree(target / "data/raw", writable=True)
        shutil.copy2(backup, may_tif)
        backup.unlink()
        _chmod_tree(target / "data/raw", writable=False)

        # Turn 4: RELEASE blocks on unreviewed code until human sign-off is recorded.
        unreviewed = _run_wf(target, "review-status", "--require-reviewed")
        assert unreviewed.returncode != 0
        assert json.loads(unreviewed.stdout)["pending"] == ["analysis: unreviewed"]

        release_blocked = _run_cmd(target, "just", "release")
        assert release_blocked.returncode != 0

        project_tool.record_review(target, "analysis")
        _commit(target, "Record human review sign-off")
        _promote_and_verify(target)

        release_ok = _run_cmd(target, "just", "release")
        assert release_ok.returncode == 0, release_ok.stdout + release_ok.stderr

        # Optional Turn 5: extract spatial.yml and validate_spatial.py verbatim and test CRS catch.
        (target / "scripts/validate_spatial.py").write_text(
            _extract_doc_block("scripts/validate_spatial.py"), encoding="utf-8"
        )
        spatial_yml = _extract_doc_block("data/spatial.yml")
        (target / "data/spatial.yml").write_text(spatial_yml, encoding="utf-8")
        _commit(target, "Add spatial metadata and validator")

        # Adding scripts/validate_spatial.py must invalidate command-mode sign-off.
        stale_review = _run_wf(target, "review-status", "--require-reviewed")
        assert stale_review.returncode != 0
        assert json.loads(stale_review.stdout)["pending"] == ["analysis: changed-since-review"]

        # Break CRS declaration on purpose and verify `just ship-ready` catches the mismatch.
        (target / "data/spatial.yml").write_text(
            spatial_yml.replace('id: "EPSG:4326"', 'id: "EPSG:3857"', 1), encoding="utf-8"
        )
        spatial_bad = _run_cmd(target, "just", "ship-ready")
        assert spatial_bad.returncode != 0
        assert "CRS mismatch (expected EPSG:3857, got EPSG:4326)" in spatial_bad.stdout

        # Restore valid CRS, re-sign off, verify reproduction, and confirm `just release` passes.
        (target / "data/spatial.yml").write_text(spatial_yml, encoding="utf-8")
        project_tool.record_review(target, "analysis")
        _commit(target, "Re-sign off after adding spatial validator")
        _promote_and_verify(target)

        final_release = _run_cmd(target, "just", "release")
        assert final_release.returncode == 0, final_release.stdout + final_release.stderr
    finally:
        if (target / "data/raw").exists():
            _chmod_tree(target / "data/raw", writable=True)
