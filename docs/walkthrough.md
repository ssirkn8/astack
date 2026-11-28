# Walkthrough: A Full Agent Session on `stargazing-spots`

This 10-minute walkthrough shows how astack works alongside **T3 Code** or **Claude Code** on a weekend passion project treated with real rigor: ranking six dark-sky viewpoints from two synthetic VIIRS-style monthly nighttime radiance GeoTIFFs and a GeoParquet site table.

You do not micromanage paths, YAML schemas, or check flags in your prompts. You state your goal in plain English, your agent follows `AGENTS.md` and the installed skills, deterministic checks catch shortcuts even when an agent (or person) slips, and you handle the one step agents cannot cross: human code sign-off in `just review`.

In CI, `tests/test_walkthrough.py` extracts and runs the scripts, configs, reproduction, `report-check`, raw-checksum, review-status, and spatial-check steps from this page against a generated project. Agent chat interactions and clicking **Sign off** inside the Marimo notebook UI remain human and harness steps.

## Before you start (2 minutes)

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), [just](https://just.systems), Git, and `astack` installed (`uv tool install 'git+https://github.com/ssirkn8/astack'`).

Scaffold the project, add `pyarrow` and `rasterio` (for GeoParquet and GeoTIFF), and verify that your agent harness can see the installed skills. This walkthrough passes `--no-dvc` to keep the demo to a single command-engine script (omit `--no-dvc` on real multi-stage studies to use the default DVC pipeline):

```sh
astack new stargazing-spots --no-dvc \
  --name "Stargazing Spots" \
  --package stargazing_spots \
  --description "Rank dark-sky viewpoints from synthetic monthly VIIRS radiance tiles" \
  --intended-use "Weekend dark-sky map and reproducible pilot report"

cd stargazing-spots
uv add pyarrow rasterio numpy
astack doctor
```

`astack doctor` confirms the managed skill mirrors exist for T3 Code (`.agents/skills/`) and Claude Code (`.claude/skills/`). Both harnesses can read `AGENTS.md` directly (astack never creates a `CLAUDE.md`, though direct `AGENTS.md` loading in Claude Code requires v2.1.277+ and a local or ancestor `CLAUDE.md` can take precedence). Confirm discovery in your session by asking your agent to list its skills and project rules. See [Agent harnesses and `astack doctor`](getting-started.md#agent-harnesses-and-astack-doctor) for details.

Next, drop two synthetic raw inputs into `data/raw/` and lock them read-only:

- `data/raw/viirs_monthly/` (2 synthetic `EPSG:4326` GeoTIFFs, `2026-04.tif` and `2026-05.tif`, in `nW/cm²/sr`, including a noisy stray-glare spike at Highway Turnout in May) to exercise **directory tree hashing** (`sha256-tree-v2:`)
- `data/raw/sites.parquet` (a spec-valid **GeoParquet 1.1** table of 6 viewpoints with WKB point geometries) to exercise **file hashing** (`sha256:`) and vector spatial data

```sh
uv run --locked python scripts/make_fixtures.py
chmod -R a-w data/raw
git add -A && git commit -m "Start stargazing-spots from astack"
```

If you plan to test the PR handoff in Turn 4, push `main` to a GitHub remote now and create a feature branch (`git checkout -b pilot-analysis`) before starting Turn 1.

<details>
<summary>Copy-paste fixture generator (<code>scripts/make_fixtures.py</code>)</summary>

**`scripts/make_fixtures.py`**

```python
"""Generate synthetic VIIRS-style monthly GeoTIFFs and a GeoParquet 1.1 site table."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
from rasterio.transform import from_origin

SITES = [
    # site_id, name, elevation_m, lon, lat, apr_radiance, may_radiance
    ("cedar-ridge", "Cedar Ridge", 1820, -118.425, 34.425, 0.40, 0.44),
    ("pine-saddle", "Pine Saddle", 1650, -118.375, 34.425, 0.62, 0.66),
    ("granite-knoll", "Granite Knoll", 1490, -118.325, 34.375, 1.10, 1.18),
    ("hawk-overlook", "Hawk Overlook", 1210, -118.275, 34.375, 2.20, 2.36),
    ("valley-rim", "Valley Rim", 940, -118.225, 34.325, 4.10, 4.30),
    ("highway-turnout", "Highway Turnout", 780, -118.175, 34.275, 6.80, 19.60),
]


def main() -> None:
    viirs_dir = Path("data/raw/viirs_monthly")
    viirs_dir.mkdir(parents=True, exist_ok=True)
    transform = from_origin(-118.45, 34.45, 0.05, 0.05)

    for month_name, value_col in (("2026-04.tif", 5), ("2026-05.tif", 6)):
        grid = np.full((4, 6), 1.5, dtype=np.float32)
        for row in SITES:
            r, c = rasterio.transform.rowcol(transform, row[3], row[4])
            grid[r, c] = np.float32(row[value_col])
        with rasterio.open(
            viirs_dir / month_name,
            "w",
            driver="GTiff",
            height=grid.shape[0],
            width=grid.shape[1],
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999.0,
        ) as dst:
            dst.write(grid, 1)

    geometries = [struct.pack("<BIdd", 1, 1, row[3], row[4]) for row in SITES]
    lons = [row[3] for row in SITES]
    lats = [row[4] for row in SITES]
    # Omitting `crs` in GeoParquet 1.1 metadata denotes default OGC:CRS84 (WGS 84 lon/lat).
    geo_metadata = {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Point"],
                "bbox": [min(lons), min(lats), max(lons), max(lats)],
            }
        },
    }
    schema = pa.schema(
        [
            pa.field("site_id", pa.string()),
            pa.field("name", pa.string()),
            pa.field("elevation_m", pa.int32()),
            pa.field("geometry", pa.binary()),
        ],
        metadata={b"geo": json.dumps(geo_metadata).encode("utf-8")},
    )
    table = pa.table(
        {
            "site_id": [row[0] for row in SITES],
            "name": [row[1] for row in SITES],
            "elevation_m": [row[2] for row in SITES],
            "geometry": geometries,
        },
        schema=schema,
    )
    pq.write_table(table, Path("data/raw/sites.parquet"))


if __name__ == "__main__":
    main()
```

</details>

---

## Turn 1: Build the analysis and baseline a reproducible run

Open `stargazing-spots` in T3 Code or Claude Code and send:

> I put two monthly nighttime-radiance GeoTIFFs and a GeoParquet table of six viewpoints in `data/raw`. Record them as synthetic pilot data (CC0), sample the rasters at each site to rank the darkest viewpoints, add a verification test for the ranking, and baseline this as a material reproducible run.

Notice you only stated your goal and provenance facts the agent cannot guess. Because `AGENTS.md` is already in the repo, the agent automatically:

1. Hashes both raw sources with `astack run checksum` (`sha256-tree-v2:...` for `data/raw/viirs_monthly` and `sha256:...` for `data/raw/sites.parquet`) and records explicit `status: known` metadata in `data/sources.yml`.
2. Writes a typed pipeline script (`scripts/analyze.py`) with a one-line docstring (which `just review` displays as the step's purpose) that outputs:
   - `data/processed/analysis/site_summary.parquet` (worktree-local Parquet table)
   - `evidence/analysis/metrics.json` (summary metrics)
   - `reports/figures/analysis/sky_brightness.svg` (editable SVG chart)
3. Adds a fixture-backed `verify` test (`tests/test_verify_walkthrough.py`) plus `mypy` overrides for untyped `pyarrow` and `rasterio` imports in `pyproject.toml`, so `just check` passes cleanly.
4. Switches `.astack/project.toml` to `[analysis] stage = "material"`, declares `[outputs]`, `[validation]` (`check`, `verify`, and `release`), and `[reproduction]`, runs the pipeline, commits code and tracked outputs, runs `just reproduce --promote`, commits the promoted record, reruns `just reproduce --promote --verify`, and commits the verified record.

Every promoted run record under `evidence/analysis/runs/` automatically captures the Git commit, `uv.lock` digest, raw input digest, output hashes, and native library versions (including `numpy` BLAS and `rasterio_gdal`).

<details>
<summary>Exact files produced in Turn 1 (for running without an agent)</summary>

**`scripts/analyze.py`**

```python
"""Sample monthly nighttime-light rasters at each stargazing site."""

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
```

**`tests/test_verify_walkthrough.py`**

```python
"""Verify expected dark-sky site rankings from the pipeline outputs."""

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
```

**`pyproject.toml` additions** (register the `verify` marker and ignore missing stubs for untyped spatial packages):

```toml
# Add under [tool.pytest.ini_options] markers:
# "verify: fixture-backed verification checks for material analysis"

[[tool.mypy.overrides]]
module = ["numpy", "pyarrow", "pyarrow.*", "rasterio", "rasterio.*"]
ignore_missing_imports = true
```

**`.astack/project.toml` updates** (set `stage = "material"`, `components = ["analysis"]`, validation tiers, and reproduction):

```toml
[analysis]
stage = "material"

[validation]
check = [["just", "check"], ["just", "review-check"]]
verify = [["uv", "run", "--locked", "pytest", "-m", "verify"]]
release = [
  ["just", "reproduce", "--verify"],
  ["uv", "run", "--locked", "python", ".astack/workflow.py", "audit", "--format", "text", "--run-tools", "--strict"],
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
```

**`data/sources.yml`** (replace `<digest>` with the output of `astack run checksum`):

```yaml
schema_version: 2
sources:
  viirs_monthly:
    title: Synthetic monthly VIIRS nighttime radiance tiles
    path: data/raw/viirs_monthly
    origin: Synthetic fixture modeled on VIIRS radiance units
    provider: Project author (synthetic)
    access_date: "2026-06-01"
    version: 1.0-synthetic
    license: {status: known, value: CC0-1.0}
    raw_checksum: sha256-tree-v2:<digest>
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
    raw_checksum: sha256:<digest>
    sensitivity: {status: known, value: public}
    allowed_outputs: [internal-analysis, derived-figures, public-export]
    redistribution: {status: known, value: allowed}
    citation: {status: known, value: "Synthetic fixture for stargazing-spots walkthrough (2026)"}
```

</details>

---

## Turn 2: Draft a report whose numbers cannot drift

Send:

> Write a short Markdown report summarizing the dark-sky results.

Your agent picks up the `ds-report-generation` skill on its own. Instead of pasting hardcoded prose numbers, it links every reported figure directly into `evidence/analysis/metrics.json` with a JSON pointer and embeds the saved SVG:

**`reports/main.md`**

```markdown
---
title: Stargazing Spots Pilot
status: final
---

# Stargazing Spots Pilot

Across [6](../evidence/analysis/metrics.json#/site_count) sites over
[2](../evidence/analysis/metrics.json#/months_count) synthetic VIIRS months,
mean nighttime radiance is [3.65](../evidence/analysis/metrics.json#/mean_radiance_nw_cm2_sr) nW/cm²/sr.
The darkest viewpoint is Cedar Ridge at
[0.42](../evidence/analysis/metrics.json#/darkest_radiance_nw_cm2_sr) nW/cm²/sr,
while Highway Turnout spikes to
[13.20](../evidence/analysis/metrics.json#/brightest_radiance_nw_cm2_sr) nW/cm²/sr.

![Mean sky brightness](figures/analysis/sky_brightness.svg)
```

Running `just report-check` reads `metrics.json`, rounds half-up to the decimals shown in the report, verifies the figure file exists, and passes:

```json
{
  "status": "passed",
  "summary": "1 report(s) passed."
}
```

Commit the passing report so Git tracks your clean baseline before Turn 3:

```sh
git add reports/main.md && git commit -m "Draft traceable report"
```

---

## Turn 3: Try a shortcut and watch both guardrails trip

This is where astack earns its keep. Send a classic under-pressure prompt:

> Highway Turnout had stray glare in May. Drop it straight from the raw data and adjust the mean down to 1.80 in the report.

Astack stops shortcuts at **two layers**:

1. **Agent layer (`AGENTS.md`):** Your agent sees the raw-data immutability and traceability rules and pushes back, offering to filter in a pipeline stage into `data/interim/` and rerun instead.
2. **Deterministic backstop (`.astack/workflow.py`):** Even if an agent ignores instructions, hallucinates, or someone edits a file by hand, the checks fail loudly.

Try forcing both mistakes yourself to see the exact errors:

**1. Edit `[3.65]` to `[1.80]` in `reports/main.md` and run `just report-check`:**

```json
{
  "reports": [
    {
      "path": "reports/main.md",
      "problems": [
        {
          "kind": "value-mismatch",
          "line": 6,
          "message": "The report shows 1.80, but evidence/analysis/metrics.json#/mean_radiance_nw_cm2_sr is 3.6467"
        }
      ],
      "status": "failed"
    }
  ],
  "status": "failed",
  "summary": "1 of 1 report(s) failed."
}
```

Revert `reports/main.md` (`git checkout -- reports/main.md`).

**2. Back up the raw GeoTIFF first (because `data/raw/` is gitignored), alter one byte in place, and run `just ship-ready`:**

```sh
mkdir -p scratch
cp data/raw/viirs_monthly/2026-05.tif scratch/2026-05.tif.bak
chmod u+w data/raw/viirs_monthly data/raw/viirs_monthly/2026-05.tif
printf '\0' >> data/raw/viirs_monthly/2026-05.tif
chmod -R a-w data/raw
just ship-ready
```

```text
Workflow audit
- workflow_conformance: pass
- software_checks: pass
- computational_reproducibility: fail
- scientific_credibility: needs-interpretive-review
- release_readiness: fail
- P0 [data-safety] A local source directory does not match its tree checksum. (viirs_monthly)
```

Notice `scientific_credibility: needs-interpretive-review`. Even when every automated check is green, astack never pretends automation validated your scientific judgment.

Restore the exact original bytes from your backup and lock `data/raw/` again:

```sh
chmod -R u+w data/raw
cp scratch/2026-05.tif.bak data/raw/viirs_monthly/2026-05.tif
rm scratch/2026-05.tif.bak
chmod -R a-w data/raw
```

---

## Turn 4: Human code sign-off and PR handoff

Even with green `just ship-ready` checks and verified reproduction, the `RELEASE` tier blocks until a person reviews the committed pipeline code (`just release` runs `astack run review-status --require-reviewed`):

```sh
astack run review-status --require-reviewed
```

```json
{
  "pending": [
    "analysis: unreviewed"
  ],
  "status": "failed"
}
```

Agents are forbidden from writing under `.astack/reviews/`. You run the one human-only step:

```sh
just review
```

This opens `notebooks/review.py` read-only in Marimo. You see the pipeline graph, the 1-line step docstring, the committed code diff, and live previews of declared pipeline outputs:

- `data/processed/analysis/site_summary.parquet` renders as an interactive table (via `pyarrow`)
- `evidence/analysis/metrics.json` renders as structured JSON, and `reports/figures/analysis/sky_brightness.svg` displays inline
- When a pipeline declares `.tif` or `.nc` outputs, `just review` also renders grayscale raster thumbnails with CRS, bounds, and nodata facts (via `rasterio`) or NetCDF dimensions and slices (via `xarray`), falling back to honest file facts when an optional reader is absent

Click **Sign off** in the notebook, commit `.astack/reviews/analysis.json`, run `just reproduce --promote` and commit the promoted record, rerun `just reproduce --promote --verify` and commit the verified record, and confirm `just release` passes cleanly.

If you pushed `main` to a GitHub remote (with `gh` authenticated) and worked on a feature branch, hand the PR back to your agent:

> Review sign-off is done. Full stop.

- In **T3 Code**, `full-stop` runs `sol-loop` cross-model review against a second model provider and leaves a clean PR open for you to merge.
- In **Claude Code**, `full-stop` prepares the PR and stops without merging. If no second model is configured for `sol-loop`, it reports that plainly instead of faking a review.

---

## Optional Turn 5: Add spatial checks (`data/spatial.yml`)

If your project works with rasters or vector geometries, add deterministic spatial validation so CRS, resolution, nodata, or geometry drift blocks shipping. Send:

> Add spatial metadata checks for the monthly radiance rasters and viewpoint coordinates so a CRS, resolution, or geometry mismatch blocks shipping.

Your agent creates `data/spatial.yml` (declaring `crs`, `resolution`, `nodata`, `temporal_extent`, `vertical_datum`, and `geometry_validity` for both `viirs_monthly` and `sites`) and a project-owned `scripts/validate_spatial.py` validator using `rasterio` and `pyarrow`.

To watch it catch a real spatial bug, change `viirs_monthly`'s declared CRS in `data/spatial.yml` from `EPSG:4326` to `EPSG:3857` and run `just ship-ready`:

```text
data/raw/viirs_monthly/2026-04.tif: CRS mismatch (expected EPSG:3857, got EPSG:4326)
Workflow audit
- workflow_conformance: fail
- software_checks: pass
- computational_reproducibility: fail
- scientific_credibility: needs-interpretive-review
- release_readiness: fail
- P1 [reproducibility] A configured validation command failed. (uv run --locked python scripts/validate_spatial.py)
```

Change it back to `EPSG:4326` and commit. Because adding `scripts/validate_spatial.py` changes tracked files under `scripts/`, command-mode sign-off automatically shifts to `changed-since-review`. Re-open `just review` to sign off the updated script set, commit the review record, run `just reproduce --promote` and commit the promoted record, rerun `just reproduce --promote --verify` and commit the verified record, and confirm `just release` passes.

<details>
<summary>Exact <code>data/spatial.yml</code> and <code>scripts/validate_spatial.py</code></summary>

**`data/spatial.yml`**

```yaml
schema_version: 1
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
```

**`scripts/validate_spatial.py`**

```python
"""Verify GeoTIFF and GeoParquet metadata against data/spatial.yml."""

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
```

</details>

> **Scaling to real VIIRS rasters:** When moving from synthetic tiles to multi-gigabyte NASA Black Marble or EOG VIIRS archives, do not copy huge rasters into `data/raw/`. Keep them on an external or shared drive with `storage: in_place` and `in_place_reason: size` ([External sources verified in place](data-and-reproducibility.md#external-sources-verified-in-place-storage-in_place)), bind remote downloads with `astack run verify-source`, and inspect raster thumbnails directly in `just review`.

---

## Next steps

- **Adopt astack in an existing repo (any language):** [Installing into an existing repository](getting-started.md#installing-into-an-existing-repository)
- **Run parallel agent worktrees safely:** [Folder ownership and worktrees](data-and-reproducibility.md#folder-ownership-and-worktrees)
- **Look up daily prompts and commands:** [Command reference](commands.md)
- **Add Quarto, output contracts, or PR review lenses:** [Optional add-ons](optional-addons.md) and [Review and reports](review-and-reports.md)
- **Ship a clean public release without private Git history:** [Publishing](publishing.md)
