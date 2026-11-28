"""Create self-contained hands-on tutorial sandboxes for astack."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .scaffold import create_project


@dataclass(frozen=True)
class TutorialSpec:
    name: str
    default_dir: str
    project_name: str
    package_name: str
    description: str
    intended_use: str
    summary: str
    tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    mypy_overrides: str
    turn_1_prompt: str


INTRO_MYPY_OVERRIDES = """
[tool.uv.workspace]

[[tool.mypy.overrides]]
module = ["numpy", "pyarrow", "pyarrow.*", "rasterio", "rasterio.*"]
ignore_missing_imports = true
"""

INTRO_FIXTURES_SCRIPT = '''"""Generate synthetic VIIRS GeoTIFFs and a GeoParquet 1.1 site table."""

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
'''

INTRO_TURN_1_PROMPT = (
    "I put two monthly nighttime-radiance GeoTIFFs and a GeoParquet table of six viewpoints in "
    "`data/raw`. Record them as synthetic pilot data (CC0), sample the rasters at each site to "
    "rank the darkest viewpoints, add a verification test for the ranking, and baseline this as "
    "a material reproducible run."
)


def _render_tutorial_md(*, init_git: bool) -> str:
    git_bullet = (
        "- Initialized Git on `main` and created the baseline commit"
        if init_git
        else "- Skipped Git setup (`--no-git`). Before Turn 1, initialize and commit this folder "
        '(`git init -b main && git add -A && git commit -m "Start stargazing-spots tutorial"`) '
        "because later steps require a clean committed Git repository."
    )
    return f"""# astack Intro Tutorial (`stargazing-spots`)

This sandbox is pre-configured for the 10-minute astack walkthrough in T3 Code or Claude Code.

## Already set up for you

- Scaffolded with `marimo` (single command-engine script, no DVC needed for this intro)
- Synced `pyarrow`, `rasterio`, and `numpy` into `.venv`
- Generated synthetic raw inputs and locked `data/raw/` read-only:
  - `data/raw/viirs_monthly/2026-04.tif` and `2026-05.tif` (`EPSG:4326` GeoTIFFs, tree hash)
  - `data/raw/sites.parquet` (GeoParquet 1.1 table of six viewpoints in `OGC:CRS84`, file hash)
{git_bullet}

Quick sanity check before opening your agent:

```sh
astack doctor
just check
```

(`just ship-ready` will rightly flag the 3 unrecorded files in `data/raw/` until Turn 1 records
them in `data/sources.yml`.)

## Turn 1: Build the analysis and baseline a reproducible run

Open this folder in T3 Code or Claude Code and send:

> {INTRO_TURN_1_PROMPT}

## Turn 2: Draft a report whose numbers cannot drift

Send:

> Write a short Markdown report summarizing the dark-sky results.

Verify with `just report-check` and commit `reports/main.md`.

## Turn 3: Try a shortcut and watch both guardrails trip

Send:

> Highway Turnout had stray glare in May. Drop it straight from the raw data and adjust the mean
> down to 1.80 in the report.

Your agent pushes back using `AGENTS.md`. You can also test the deterministic backstop directly:

1. Change `[3.65]` to `[1.80]` in `reports/main.md`, run `just report-check`, then restore with
   `git checkout -- reports/main.md`.
2. Back up `2026-05.tif`, mutate one byte, watch `just ship-ready` emit P0 `data-safety`, and
   restore the exact backup:

```sh
mkdir -p scratch
cp data/raw/viirs_monthly/2026-05.tif scratch/2026-05.tif.bak
chmod u+w data/raw/viirs_monthly data/raw/viirs_monthly/2026-05.tif
printf '\\0' >> data/raw/viirs_monthly/2026-05.tif
chmod -R a-w data/raw
just ship-ready

# Restore the exact backup and re-lock data/raw/
chmod -R u+w data/raw
cp scratch/2026-05.tif.bak data/raw/viirs_monthly/2026-05.tif
rm scratch/2026-05.tif.bak
chmod -R a-w data/raw
```

## Turn 4: Human code sign-off

Run the human-only review notebook:

```sh
just review
```

Inspect the pipeline code and live output previews, click **Sign off**, commit
`.astack/reviews/analysis.json`, rerun `just reproduce --promote` and
`just reproduce --promote --verify` (committing each record), and verify `just release` passes.

## Optional Turn 5: Add spatial checks (`data/spatial.yml`)

Send:

> Add spatial metadata checks for the monthly radiance rasters and viewpoint coordinates so a CRS,
> resolution, or geometry mismatch blocks shipping.

Then change `EPSG:4326` to `EPSG:3857` in `data/spatial.yml` and run `just ship-ready` to watch the
CRS check fail.
"""


INTRO_RAW_INPUTS = (
    "data/raw/viirs_monthly/2026-04.tif",
    "data/raw/viirs_monthly/2026-05.tif",
    "data/raw/sites.parquet",
)

TUTORIALS: dict[str, TutorialSpec] = {
    "intro": TutorialSpec(
        name="intro",
        default_dir="stargazing-spots",
        project_name="Stargazing Spots",
        package_name="stargazing_spots",
        description="Rank dark-sky viewpoints from synthetic monthly VIIRS radiance tiles",
        intended_use="Weekend dark-sky map and reproducible pilot report",
        summary=(
            "10-minute agent session with synthetic VIIRS GeoTIFFs, GeoParquet viewpoints, "
            "guardrails, and Marimo sign-off."
        ),
        tools=("marimo",),
        dependencies=("pyarrow>=15", "rasterio>=1.4", "numpy>=2.0"),
        mypy_overrides=INTRO_MYPY_OVERRIDES,
        turn_1_prompt=INTRO_TURN_1_PROMPT,
    ),
}


def available_tutorials() -> list[dict[str, str]]:
    """Return metadata for every built-in tutorial topic."""
    return [
        {
            "name": spec.name,
            "default_dir": spec.default_dir,
            "summary": spec.summary,
        }
        for spec in TUTORIALS.values()
    ]


def _uv_environment() -> dict[str, str]:
    """Avoid inheriting the caller's active virtualenv when locking or syncing the tutorial."""
    return {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}


def _set_tree_writable(root: Path, *, writable: bool) -> None:
    """Lock or unlock a directory tree without following symlinks."""
    if not root.exists():
        return
    if writable:
        for current, _dirs, files in os.walk(root):
            current_path = Path(current)
            current_path.chmod(stat.S_IMODE(current_path.stat().st_mode) | 0o200)
            for name in files:
                file_path = current_path / name
                if not file_path.is_symlink():
                    file_path.chmod(stat.S_IMODE(file_path.stat().st_mode) | 0o200)
        return
    # Lock files first and directories bottom-up so child chmod calls succeed before parent locks.
    for current, _dirs, files in os.walk(root, topdown=False):
        current_path = Path(current)
        for name in files:
            file_path = current_path / name
            if not file_path.is_symlink():
                file_path.chmod(stat.S_IMODE(file_path.stat().st_mode) & ~0o222)
        current_path.chmod(stat.S_IMODE(current_path.stat().st_mode) & ~0o222)


def _run_step(
    command: list[str], *, cwd: Path, env: dict[str, str], label: str
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"{label} failed: {detail}")
    return result


def _git_identity_args(target: Path, env: dict[str, str]) -> tuple[list[str], bool]:
    """Use the user's Git identity when configured, or fall back for clean sandbox commits."""
    args: list[str] = ["-c", "commit.gpgsign=false"]
    used_fallback = False
    for key, fallback in (
        ("user.name", "astack Tutorial"),
        ("user.email", "tutorial@example.invalid"),
    ):
        probe = subprocess.run(
            ["git", "config", "--get", key],
            cwd=target,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            args.extend(["-c", f"{key}={fallback}"])
            used_fallback = True
    return args, used_fallback


def _configure_intro_pyproject(target: Path, spec: TutorialSpec) -> None:
    pyproject = target / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    deps = ", ".join(f'"{dep}"' for dep in spec.dependencies)
    updated = text.replace("dependencies = []", f"dependencies = [{deps}]", 1).replace(
        '  "integration: tests that exercise integration-heavy behavior",\n]',
        '  "integration: tests that exercise integration-heavy behavior",\n'
        '  "verify: fixture-backed verification checks for material analysis",\n]',
        1,
    )
    if updated == text:
        raise RuntimeError("Tutorial template pyproject.toml did not match expected layout")
    pyproject.write_text(updated + spec.mypy_overrides, encoding="utf-8")


def create_tutorial(
    target: Path,
    *,
    topic: str = "intro",
    init_git: bool = True,
    git_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a ready-to-run tutorial repository with synthetic read-only raw data."""
    if topic not in TUTORIALS:
        valid = ", ".join(sorted(TUTORIALS))
        raise ValueError(f"Unknown tutorial {topic!r}. Available tutorials: {valid}")
    spec = TUTORIALS[topic]
    target = target.expanduser()
    # Atomic creation gives this invocation exclusive ownership so concurrent callers cannot race.
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise FileExistsError(
            f"Target already exists: {target}. Pass a new directory path."
        ) from None

    git_identity_fallback = False
    try:
        files = create_project(
            target,
            project_name=spec.project_name,
            package_name=spec.package_name,
            description=spec.description,
            intended_use=spec.intended_use,
            tools=list(spec.tools),
            lock=False,
        )
        _configure_intro_pyproject(target, spec)
        (target / "TUTORIAL.md").write_text(
            _render_tutorial_md(init_git=init_git), encoding="utf-8"
        )

        uv_env = _uv_environment()
        _run_step(["uv", "lock"], cwd=target, env=uv_env, label="uv lock")
        _run_step(["uv", "sync", "--locked"], cwd=target, env=uv_env, label="uv sync")
        _run_step(
            ["uv", "run", "--locked", "python", "-c", INTRO_FIXTURES_SCRIPT],
            cwd=target,
            env=uv_env,
            label="synthetic fixture generation",
        )
        _set_tree_writable(target / "data/raw", writable=False)

        git_status = "skipped (--no-git)"
        if init_git:
            env = dict(git_env) if git_env is not None else os.environ.copy()
            _run_step(["git", "init", "-b", "main"], cwd=target, env=env, label="git init")
            _run_step(["git", "add", "-A"], cwd=target, env=env, label="git add")
            identity_args, git_identity_fallback = _git_identity_args(target, env)
            commit_cmd = [
                "git",
                *identity_args,
                "commit",
                "-m",
                f"Start {spec.default_dir} tutorial",
            ]
            _run_step(commit_cmd, cwd=target, env=env, label="git commit")
            git_status = "initialized and committed on main"
    except BaseException:
        _set_tree_writable(target / "data/raw", writable=True)
        shutil.rmtree(target, ignore_errors=True)
        raise

    return {
        "status": "created",
        "topic": spec.name,
        "target": str(target),
        "files": [*files, "TUTORIAL.md"],
        "raw_inputs": list(INTRO_RAW_INPUTS),
        "git": git_status,
        "git_identity_fallback": git_identity_fallback,
        "next_prompt": spec.turn_1_prompt,
    }
