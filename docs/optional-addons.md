# Optional add-ons

astack keeps the default project lean so you do not maintain machinery you do not use. Turn on any of the add-ons below only when your project actually needs it.

## Geospatial checks (`data/spatial.yml`)

**When to use:** any project reading or writing raster or vector geospatial data. astack adds no GIS dependencies, so you keep GDAL, rasterio, pyproj, GeoPandas, or whatever tools your project already uses.

**How to enable:**

1. Create `data/spatial.yml` (`schema_version: 1`) declaring your validator commands, spatial sources (using source IDs from `data/sources.yml`), and spatial outputs:

```yaml
schema_version: 1
validators:
  - ["uv", "run", "--locked", "python", "scripts/validate_spatial.py"]
sources:
  landcover:
    crs: {status: known, value: {id: "EPSG:32633", axis_order: "easting,northing", units: metre}}
    vertical_datum: {status: not_applicable, reason: "Two-dimensional land cover"}
    temporal_extent: {status: known, value: {start: "2020-01-01", end: "2020-12-31"}}
    resolution: {status: known, value: {x: 10, y: 10, units: metre}}
    nodata: {status: known, value: 255}
    geometry_validity: {status: not_applicable, reason: "Raster data"}
    reprojection:
      - {from: "EPSG:4326", to: "EPSG:32633", method: nearest, step: scripts/warp.py}
outputs:
  data/processed/analysis/landcover.tif:
    crs: {status: known, value: {id: "EPSG:32633", axis_order: "easting,northing", units: metre}}
    vertical_datum: {status: not_applicable, reason: "Two-dimensional land cover"}
    temporal_extent: {status: known, value: {start: "2020-01-01", end: "2020-12-31"}}
    resolution: {status: known, value: {x: 10, y: 10, units: metre}}
    nodata: {status: known, value: 255}
    geometry_validity: {status: not_applicable, reason: "Raster data"}
    reprojection: []
```

Every record requires `crs`, `vertical_datum`, `temporal_extent`, `resolution`, `nodata`, and `geometry_validity` using the standard states (`known` with value, `not_applicable` with reason, `unknown`, or `not_yet_verified`).

2. Write your validator script (`scripts/validate_spatial.py`) to read `data/spatial.yml`, open each dataset, and exit non-zero if actual CRS, axis order, resolution, nodata, temporal extent, or geometry validity disagrees with the metadata.
3. Run `just ship-ready`. The audit automatically validates `data/spatial.yml` structure, runs every command in `validators:`, and blocks release on missing/unknown fields, undeclared source IDs, unsafe output paths, missing validators, or non-zero exit codes.
4. Optionally wire the same validator into `[validation]` in `.astack/project.toml` so `just verify` and `astack run validate check` catch spatial drift during everyday work:

```toml
[validation]
verify = [
  ["uv", "run", "--locked", "pytest", "-m", "verify"],
  ["uv", "run", "--locked", "python", "scripts/validate_spatial.py"],
]
```

Automated spatial validators check metadata consistency, not whether your projection, resolution, or resampling method is scientifically appropriate for your question.

## Experimental PR review lenses (`pr-review-pass`)

**When to use:** when you want a focused, non-blocking review pass alongside `sol-loop` on high-risk pull requests without adding ceremony to routine low/medium PRs.

`pr-review-pass` gates deterministically using `astack run review-policy --base origin/main` (for untrusted PRs or runtime diffs, invoke `python3 -B <trusted-runtime>/.astack/workflow.py review-policy --root <candidate-root> --base <base>` so untrusted candidate code is never executed). Routine PRs that trigger neither lens skip immediately.

Two lenses activate automatically when relevant paths change:

- **Security lens** (`protected_paths` non-empty): checks symlink/path traversal, unsafe permissions or shared temp/cache folders, secret or PII leaks, CI/dependency/install tampering, and fail-closed checks.
- **Scientific robustness lens** (`scientific_credibility` in `required_axes`): checks train/validation leakage, dependence-aware splits, unit/CRS mismatch, silent `dropna` or filtering, unseeded stochasticity, multiple testing, and unsupported causal claims.

**How to enable:**

1. Invoke `pr-review-pass` directly in your agent alongside `sol-loop` on a PR, or add one rule to your project's `AGENTS.md`:

```markdown
- Run `pr-review-pass` alongside `sol-loop` before opening a PR. Skip it when `review-policy` activates no lenses.
```

2. Optionally declare project-specific scientific paths in `.astack/project.toml` (especially useful in hybrid General projects):

```toml
[review]
scientific = true
scientific_paths = ["exports/scientific/**"]
```

3. Optionally create a project-owned `REVIEW.md` at the repository root with repo-specific review checks. `pr-review-pass` reads `REVIEW.md` from the trusted base commit (never from an unmerged PR diff, where edits to `REVIEW.md` are themselves audited under the security lens).

## Quarto reports

**When to use:** when you want rendered PDF, HTML, or manuscript outputs from `.qmd` files alongside Markdown report checks.

**How to enable:**

- **New project:** pass `--tool quarto` when scaffolding:
  ```sh
  astack new my-study --tool quarto
  ```
  This adds `reports/report.qmd`, a `just report` recipe (`quarto render reports/report.qmd`), and the Quarto setup action in `.github/workflows/checks.yml`.
- **Existing project:** install [Quarto](https://quarto.org), add `"quarto"` to `tools` in `.astack/project.toml`, create `reports/report.qmd`, add a `report:` recipe to `justfile`, and add the Quarto setup action to your CI workflow if you want CI rendering.

Save every figure to `reports/figures/<component>/` and embed it rather than relying on live `.qmd` code cells to generate figures, because `just report-check` validates file-backed figures and evidence pointers.

## Optional DVC remote storage

**When to use:** by default, astack uses DVC only to execute pipeline stages and label stale outputs (`cache: false` on stage outputs) without copying large files into a DVC object store. Enable a DVC remote only when dataset size, volatility, or multi-machine collaboration justifies remote artifact storage.

**How to enable:**

1. Configure your remote with standard DVC commands:
   ```sh
   uv run --locked dvc remote add -d storage s3://my-bucket/my-study
   ```
2. Remove `cache: false` only on the specific `dvc.yaml` outputs you intentionally want DVC to cache and push.
3. Inspect or garbage-collect the machine-local DVC cache with:
   ```sh
   astack run dvc-cache status
   astack run dvc-cache gc          # dry-run preview
   astack run dvc-cache gc --apply  # clean unused cache objects
   ```
   (Generated DVC projects also provide `just dvc-cache-status` and `just dvc-cache-gc`.)
4. Keep local cache directories user-private (`0700` on POSIX), as documented in [SECURITY.md](../SECURITY.md#upstream-dvc-cache-boundary).

astack never runs `dvc pull` or `dvc push` automatically. Remote data transfers always remain explicit human-authorized commands.

## Publication metadata (`CITATION.cff` and availability statements)

**When to use:** when preparing a journal submission, replication archive, or Zenodo/Figshare/Dryad deposit. Generated projects include inert templates under `docs/publication/` that do nothing until you opt in.

**How to enable:**

1. Choose a code license and add it at the repository root as `LICENSE` (astack never picks a license for you, and source data licenses remain governed by `data/sources.yml`).
2. Copy `docs/publication/CITATION.cff` to the repository root as `CITATION.cff` and complete it.
3. Fill in `docs/publication/data-availability.md` and `docs/publication/code-availability.md`, listing every source in `data/sources.yml`, its license, and its access route (for restricted data, describe how qualified researchers request access rather than including restricted files). Remove all `TODO(publication)` markers.
4. Enable `[publication]` in `.astack/project.toml`:

```toml
[publication]
license = "LICENSE"
citation = "CITATION.cff"
data_availability = "docs/publication/data-availability.md"
code_availability = "docs/publication/code-availability.md"
# Add only after your archive issues a DOI for this exact exported release:
# doi = "10.5281/zenodo.0000000"
```

Once `[publication]` is declared, `just ship-ready` verifies that every referenced file exists, is complete, and contains no `TODO(publication)` markers.

5. Build the public release with `publish-public` (see [Publishing](publishing.md)), tag it, and deposit that exact release with your archive. Only after the archive issues a real DOI, record it in `[publication].doi`, `CITATION.cff`, and your availability statements. Never invent a placeholder DOI, and manually verify that the DOI resolves to your release because the audit checks syntax only.
