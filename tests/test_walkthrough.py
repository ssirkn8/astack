"""End-to-end verification of the stargazing-spots guardrails documented in docs/walkthrough.md."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from project_tool_test_support import (
    no_ci_push_base,  # noqa: F401
    project_tool,
)

from astack.tutorial import create_tutorial

# End-to-end reproduction and audit walkthrough: integration tier, matching other repro tests.
pytestmark = pytest.mark.integration

ANALYZE_SCRIPT = r'''"""Sample monthly nighttime-light rasters at each stargazing site."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import TypedDict

import pyarrow as pa
import pyarrow.parquet as pq
import rasterio


class SiteRow(TypedDict):
    site_id: str
    name: str
    elevation_m: int
    months_sampled: int
    mean_radiance_nw_cm2_sr: float


def _unpack_wkb_point(blob: bytes) -> tuple[float, float]:
    endian, geom_type, lon, lat = struct.unpack("<BIdd", blob)
    if endian != 1 or geom_type != 1:
        raise ValueError(f"Expected little-endian WKB Point, got ({endian}, {geom_type})")
    return lon, lat


def _write_svg(rows: list[SiteRow], out_path: Path) -> None:
    max_val = max(row["mean_radiance_nw_cm2_sr"] for row in rows) or 1.0
    width, height = 560, 40 + 36 * len(rows)
    bars: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="16" y="24" font-family="sans-serif" font-size="14" font-weight="bold" '
        'fill="#111827">Mean synthetic VIIRS radiance (nW/cm2/sr, lower is darker)</text>',
    ]
    for index, row in enumerate(rows):
        y = 44 + index * 36
        value = row["mean_radiance_nw_cm2_sr"]
        bar_w = max(4.0, (value / max_val) * 280.0)
        bars.append(
            f'<text x="16" y="{y + 16}" font-family="sans-serif" font-size="12" '
            f'fill="#1f2937">{row["name"]}</text>'
            f'<rect x="165" y="{y + 3}" width="{bar_w:.1f}" height="18" fill="#1d4ed8"/>'
            f'<text x="{173 + bar_w:.1f}" y="{y + 16}" font-family="sans-serif" '
            f'font-size="12" fill="#111827">{value:.2f}</text>'
        )
    bars.append("</svg>\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(bars), encoding="utf-8")


def main() -> None:
    sites_table = pq.read_table("data/raw/sites.parquet")
    raster_paths = sorted(Path("data/raw/viirs_monthly").glob("*.tif"))
    if not raster_paths:
        raise RuntimeError("No monthly GeoTIFFs found under data/raw/viirs_monthly")

    coords = [_unpack_wkb_point(blob) for blob in sites_table["geometry"].to_pylist()]
    per_site: list[list[float]] = [[] for _ in coords]
    for raster_path in raster_paths:
        with rasterio.open(raster_path) as src:
            for idx, sample in enumerate(src.sample(coords)):
                val = float(sample[0])
                if src.nodata is not None and val == src.nodata:
                    continue
                per_site[idx].append(val)

    rows: list[SiteRow] = []
    for idx, site_id in enumerate(sites_table["site_id"].to_pylist()):
        values = per_site[idx]
        if not values:
            raise ValueError(f"No valid raster samples for site {site_id}")
        rows.append(
            {
                "site_id": str(site_id),
                "name": str(sites_table["name"][idx].as_py()),
                "elevation_m": int(sites_table["elevation_m"][idx].as_py()),
                "months_sampled": len(values),
                "mean_radiance_nw_cm2_sr": round(sum(values) / len(values), 4),
            }
        )

    processed = Path("data/processed/analysis/site_summary.parquet")
    processed.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), processed)

    darkest = min(rows, key=lambda r: r["mean_radiance_nw_cm2_sr"])
    brightest = max(rows, key=lambda r: r["mean_radiance_nw_cm2_sr"])
    mean_all = sum(r["mean_radiance_nw_cm2_sr"] for r in rows) / len(rows)

    metrics = {
        "site_count": len(rows),
        "months_count": len(raster_paths),
        "mean_radiance_nw_cm2_sr": round(mean_all, 4),
        "darkest_site": darkest["site_id"],
        "darkest_radiance_nw_cm2_sr": darkest["mean_radiance_nw_cm2_sr"],
        "brightest_site": brightest["site_id"],
        "brightest_radiance_nw_cm2_sr": brightest["mean_radiance_nw_cm2_sr"],
    }
    evidence = Path("evidence/analysis/metrics.json")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    _write_svg(rows, Path("reports/figures/analysis/sky_brightness.svg"))


if __name__ == "__main__":
    main()
'''

VERIFY_TEST_SCRIPT = '''"""Verify expected dark-sky site rankings from the pipeline outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest


@pytest.mark.verify
def test_site_rankings_match_expected_extremes() -> None:
    metrics = json.loads(Path("evidence/analysis/metrics.json").read_text(encoding="utf-8"))
    table = pq.read_table("data/processed/analysis/site_summary.parquet")

    assert metrics["site_count"] == 6
    assert metrics["months_count"] == 2
    assert metrics["darkest_site"] == "cedar-ridge"
    assert metrics["brightest_site"] == "highway-turnout"
    assert table.num_rows == 6
'''

VALIDATE_SPATIAL_SCRIPT = '''"""Verify GeoTIFF and GeoParquet metadata against data/spatial.yml."""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import pyarrow.parquet as pq
import rasterio
import yaml


def main() -> int:
    doc = yaml.safe_load(Path("data/spatial.yml").read_text(encoding="utf-8"))
    sources = doc.get("sources", {})

    raster_meta = sources["viirs_monthly"]
    expected_crs = raster_meta["crs"]["value"]["id"]
    expected_res = raster_meta["resolution"]["value"]
    expected_nodata = raster_meta["nodata"]["value"]
    for tif in sorted(Path("data/raw/viirs_monthly").glob("*.tif")):
        with rasterio.open(tif) as src:
            actual_crs = src.crs.to_string() if src.crs else None
            if actual_crs != expected_crs:
                print(f"{tif}: CRS mismatch (expected {expected_crs}, got {actual_crs})")
                return 1
            res_x, res_y = src.res
            if not (
                math.isclose(res_x, expected_res["x"]) and math.isclose(res_y, expected_res["y"])
            ):
                print(f"{tif}: resolution mismatch (expected {expected_res}, got {src.res})")
                return 1
            if src.nodata != expected_nodata:
                print(f"{tif}: nodata mismatch (expected {expected_nodata}, got {src.nodata})")
                return 1

    vector_meta = sources["sites"]
    expected_vector_crs = vector_meta["crs"]["value"]["id"]
    pf = pq.ParquetFile("data/raw/sites.parquet")
    geo_raw = (pf.schema_arrow.metadata or {}).get(b"geo")
    if not geo_raw:
        print("data/raw/sites.parquet: missing GeoParquet 'geo' metadata")
        return 1
    geo = json.loads(geo_raw.decode("utf-8"))
    crs_meta = geo["columns"][geo["primary_column"]].get("crs")
    if crs_meta is None:
        crs_id = "OGC:CRS84"
    elif isinstance(crs_meta, dict) and isinstance(crs_meta.get("id"), dict):
        crs_id = f"{crs_meta['id'].get('authority')}:{crs_meta['id'].get('code')}"
    else:
        crs_id = str(crs_meta)
    if crs_id != expected_vector_crs:
        print(
            f"data/raw/sites.parquet: CRS mismatch (expected {expected_vector_crs}, got {crs_id})"
        )
        return 1
    for idx, blob in enumerate(pf.read(["geometry"])["geometry"].to_pylist()):
        endian, geom_type, lon, lat = struct.unpack("<BIdd", blob)
        if endian != 1 or geom_type != 1 or not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            print(f"data/raw/sites.parquet: invalid point geometry at row {idx}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

PROJECT_TOML_MATERIAL = """[analysis]
stage = "material"

[validation]
check = [["just", "check"], ["just", "review-check"]]
verify = [["uv", "run", "--locked", "pytest", "-m", "verify"]]
release = [
  ["just", "reproduce", "--verify"],
  [
    "uv", "run", "--locked", "python", ".astack/workflow.py",
    "audit", "--format", "text", "--run-tools", "--strict",
  ],
  ["uv", "run", "--locked", "python", ".astack/workflow.py", "review-status", "--require-reviewed"],
]

[outputs]
layout = "component"
components = ["analysis"]

[reproduction]
engine = "command"
component = "analysis"
command = ["uv", "run", "--locked", "python", "scripts/analyze.py"]
outputs = [
  "data/processed/analysis/site_summary.parquet",
  "evidence/analysis/metrics.json",
  "reports/figures/analysis/sky_brightness.svg",
]
"""

SOURCES_YAML_TEMPLATE = """schema_version: 2
sources:
  viirs_monthly:
    title: Synthetic monthly VIIRS nighttime radiance tiles
    path: data/raw/viirs_monthly
    origin: Synthetic fixture modeled on VIIRS radiance units
    provider: Project author (synthetic)
    access_date: "2026-06-01"
    version: 1.0-synthetic
    license: {status: known, value: CC0-1.0}
    raw_checksum: VIIRS_DIGEST
    sensitivity: {status: known, value: public}
    allowed_outputs: [internal-analysis, derived-figures, public-export]
    redistribution: {status: known, value: allowed}
    citation: {status: known, value: "Synthetic fixture for stargazing-spots walkthrough (2026)"}
  sites:
    title: Synthetic stargazing viewpoint coordinates
    path: data/raw/sites.parquet
    origin: Synthetic GeoParquet fixture
    provider: Project author (synthetic)
    access_date: "2026-06-01"
    version: 1.0-synthetic
    license: {status: known, value: CC0-1.0}
    raw_checksum: SITES_DIGEST
    sensitivity: {status: known, value: public}
    allowed_outputs: [internal-analysis, derived-figures, public-export]
    redistribution: {status: known, value: allowed}
    citation: {status: known, value: "Synthetic fixture for stargazing-spots walkthrough (2026)"}
"""

SPATIAL_YAML = """schema_version: 1
validators:
  - ["uv", "run", "--locked", "python", "scripts/validate_spatial.py"]
sources:
  viirs_monthly:
    crs: {status: known, value: {id: "EPSG:4326", axis_order: "longitude,latitude", units: degree}}
    vertical_datum: {status: not_applicable, reason: "2D surface radiance"}
    temporal_extent: {status: known, value: {start: "2026-04-01", end: "2026-05-31"}}
    resolution: {status: known, value: {x: 0.05, y: 0.05, units: degree}}
    nodata: {status: known, value: -9999.0}
    geometry_validity: {status: not_applicable, reason: "Raster data"}
    reprojection: []
  sites:
    crs: {status: known, value: {id: "OGC:CRS84", axis_order: "longitude,latitude", units: degree}}
    vertical_datum: {status: not_applicable, reason: "2D horizontal point coordinates"}
    temporal_extent: {status: known, value: {start: "2026-04-01", end: "2026-05-31"}}
    resolution: {status: not_applicable, reason: "Point vector geometry"}
    nodata: {status: not_applicable, reason: "Point vector geometry"}
    geometry_validity: {status: known, value: {method: wkb-point-bounds, result: valid}}
    reprojection: []
outputs: {}
"""

REPORT_MD = """---
title: Stargazing Spots Pilot
status: final
---

Across [6](../evidence/analysis/metrics.json#/site_count) sites over
[2](../evidence/analysis/metrics.json#/months_count) synthetic VIIRS months,
mean nighttime radiance is
[3.65](../evidence/analysis/metrics.json#/mean_radiance_nw_cm2_sr) nW/cm²/sr.

![Mean sky brightness](figures/analysis/sky_brightness.svg)
"""


def _clean_env() -> dict[str, str]:
    """Drop the parent repo's VIRTUAL_ENV so `uv` targets the generated project's own .venv."""
    return {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}


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
    """Verify every guardrail and failure output described in docs/walkthrough.md."""
    # Match `astack tutorial intro`, which sets up data/raw, dependencies, TUTORIAL.md, and Git.
    target = tmp_path / "stargazing-spots"
    result = create_tutorial(target, topic="intro")
    assert result["status"] == "created"
    assert (target / "TUTORIAL.md").is_file()
    try:
        # Fresh tutorial sandbox passes `just check`, while `just ship-ready` flags the 3
        # unrecorded files in `data/raw/` until Turn 1 records them in `data/sources.yml`.
        initial_check = _run_cmd(target, "just", "check")
        assert initial_check.returncode == 0, initial_check.stdout + initial_check.stderr
        initial_ship = _run_cmd(target, "just", "ship-ready")
        assert initial_ship.returncode != 0
        assert "P1 [provenance]" in initial_ship.stdout
        assert "raw_files=3" in initial_ship.stdout

        viirs_check = _run_wf(target, "checksum", "data/raw/viirs_monthly")
        sites_check = _run_wf(target, "checksum", "data/raw/sites.parquet")
        assert viirs_check.returncode == 0, viirs_check.stderr
        assert sites_check.returncode == 0, sites_check.stderr
        viirs_checksum = json.loads(viirs_check.stdout)["checksum"]
        sites_checksum = json.loads(sites_check.stdout)["checksum"]
        assert viirs_checksum.startswith("sha256-tree-v2:")
        assert sites_checksum.startswith("sha256:")

        # Turn 1: register sources, add typed pipeline + verify test, configure material stage.
        (target / "data/sources.yml").write_text(
            SOURCES_YAML_TEMPLATE.replace("VIIRS_DIGEST", viirs_checksum).replace(
                "SITES_DIGEST", sites_checksum
            ),
            encoding="utf-8",
        )
        (target / "scripts/analyze.py").write_text(ANALYZE_SCRIPT, encoding="utf-8")
        (target / "tests/test_verify_walkthrough.py").write_text(
            VERIFY_TEST_SCRIPT, encoding="utf-8"
        )

        config = target / ".astack/project.toml"
        base_config = config.read_text(encoding="utf-8")
        header = base_config[: base_config.index("[analysis]")]
        data_block = base_config[base_config.index("[data]") : base_config.index("\n[outputs]")]
        config.write_text(header + data_block + "\n\n" + PROJECT_TOML_MATERIAL, encoding="utf-8")

        # Generate initial tracked outputs so `reproduce --promote` only adds the run record.
        run_res = _run_cmd(target, "uv", "run", "--locked", "python", "scripts/analyze.py")
        assert run_res.returncode == 0, run_res.stdout + run_res.stderr

        # Turn 2: write and commit the traceable Markdown report.
        report_path = target / "reports/main.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(REPORT_MD, encoding="utf-8")

        subprocess.run(["git", "config", "user.name", "Observer"], cwd=target, check=True)
        _commit(target, "Complete Turn 1 and Turn 2")

        # Unmodified `just check`, `just review-check`, and `just verify` must pass.
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
        report_path.write_text(REPORT_MD.replace("[3.65]", "[1.80]"), encoding="utf-8")
        rep_bad = _run_cmd(target, "just", "report-check", "reports/main.md")
        assert rep_bad.returncode != 0
        bad_payload = json.loads(rep_bad.stdout)
        kinds = [p["kind"] for r in bad_payload["reports"] for p in r["problems"]]
        assert "value-mismatch" in kinds
        subprocess.run(["git", "checkout", "--", "reports/main.md"], cwd=target, check=True)
        assert report_path.read_text(encoding="utf-8") == REPORT_MD

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

        # Optional Turn 5: add spatial.yml + validator and verify CRS mismatch and stale review.
        (target / "scripts/validate_spatial.py").write_text(
            VALIDATE_SPATIAL_SCRIPT, encoding="utf-8"
        )
        (target / "data/spatial.yml").write_text(SPATIAL_YAML, encoding="utf-8")
        _commit(target, "Add spatial metadata and validator")

        # Adding scripts/validate_spatial.py must invalidate command-mode sign-off.
        stale_review = _run_wf(target, "review-status", "--require-reviewed")
        assert stale_review.returncode != 0
        assert json.loads(stale_review.stdout)["pending"] == ["analysis: changed-since-review"]

        # Break CRS declaration on purpose and verify `just ship-ready` catches the mismatch.
        (target / "data/spatial.yml").write_text(
            SPATIAL_YAML.replace('id: "EPSG:4326"', 'id: "EPSG:3857"', 1), encoding="utf-8"
        )
        spatial_bad = _run_cmd(target, "just", "ship-ready")
        assert spatial_bad.returncode != 0
        assert "CRS mismatch (expected EPSG:3857, got EPSG:4326)" in spatial_bad.stdout

        # Restore valid CRS, re-sign off, verify reproduction, and confirm `just release` passes.
        (target / "data/spatial.yml").write_text(SPATIAL_YAML, encoding="utf-8")
        project_tool.record_review(target, "analysis")
        _commit(target, "Re-sign off after adding spatial validator")
        _promote_and_verify(target)

        final_release = _run_cmd(target, "just", "release")
        assert final_release.returncode == 0, final_release.stdout + final_release.stderr
    finally:
        if (target / "data/raw").exists():
            _chmod_tree(target / "data/raw", writable=True)
