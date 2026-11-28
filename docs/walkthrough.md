# Walkthrough: A Full Agent Session on `stargazing-spots`

This walkthrough shows how astack works alongside **T3 Code** or **Claude Code** on a weekend passion project treated with real rigor: ranking six dark-sky viewpoints from two synthetic VIIRS-style monthly nighttime radiance GeoTIFFs and a GeoParquet site table.

You do not micromanage paths, YAML schemas, or check flags in your prompts. You state your goal in plain English, your agent follows `AGENTS.md` and the installed skills, deterministic checks catch shortcuts even when an agent (or person) slips, and you handle the one step agents cannot cross: human code sign-off in `just review`.

Because agent sessions are non-deterministic, your agent may choose slightly different script names or prose. Every deterministic guardrail and failure output shown below is verified end-to-end against a real scaffolded project in `tests/test_walkthrough.py`.

## Before you start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), [just](https://just.systems), Git, and `astack` installed (`uv tool install 'git+https://github.com/ssirkn8/astack'`).

Create the ready-to-run `stargazing-spots` tutorial sandbox with one command:

```sh
astack tutorial intro
cd stargazing-spots
astack doctor
just check
```

`astack tutorial intro` (pass an optional directory after `intro` to put it elsewhere) scaffolds the project without DVC so this intro stays to a single command-engine script, installs `pyarrow`, `rasterio`, and `numpy`, generates and locks the synthetic raw inputs read-only under `data/raw/`, writes `TUTORIAL.md` with the prompts below, and makes the initial commit on `main`:

- `data/raw/viirs_monthly/` (two synthetic `EPSG:4326` GeoTIFFs, `2026-04.tif` and `2026-05.tif`, in `nW/cm²/sr`, including a noisy stray-glare spike at one viewpoint in May) to exercise **directory tree hashing** (`sha256-tree-v2:`)
- `data/raw/sites.parquet` (a **GeoParquet 1.1** table of six viewpoints in default `OGC:CRS84` lon/lat) to exercise **file hashing** (`sha256:`) and vector spatial data

`astack doctor` checks the managed skill mirrors for T3 Code (`.agents/skills/`) and Claude Code (`.claude/skills/`). Both harnesses can read `AGENTS.md` directly (astack never creates a `CLAUDE.md`, though direct `AGENTS.md` loading in Claude Code requires v2.1.277+ and a local or ancestor `CLAUDE.md` can take precedence). Confirm discovery in your session by asking your agent to list its skills and project rules. See [Agent harnesses and `astack doctor`](getting-started.md#agent-harnesses-and-astack-doctor) for details.

If you plan to test the PR handoff in Turn 4, push `main` to a GitHub remote now and create a feature branch (`git checkout -b pilot-analysis`) before starting Turn 1. (On real multi-stage studies, run `astack new <dir>` without `--no-dvc` to keep the default DVC pipeline.)

---

## Turn 1: Build the analysis and baseline a reproducible run

Open `stargazing-spots` in T3 Code or Claude Code and send:

> I put two monthly nighttime-radiance GeoTIFFs and a GeoParquet table of six viewpoints in `data/raw`. Record them as synthetic pilot data (CC0), sample the rasters at each site to rank the darkest viewpoints, add a verification test for the ranking, and baseline this as a material reproducible run.

You only state your goal and provenance facts the agent cannot guess. Guided by `AGENTS.md`, your agent handles the structure automatically:

1. Hashes both inputs with `astack run checksum` (`sha256-tree-v2:...` for `data/raw/viirs_monthly` and `sha256:...` for `data/raw/sites.parquet`) and writes explicit `status: known` metadata to `data/sources.yml`.
2. Writes a typed analysis script with a one-line docstring (shown as the step's purpose in `just review`) that outputs a worktree-local Parquet summary (`data/processed/analysis/site_summary.parquet`), machine metrics (`evidence/analysis/metrics.json`), and a saved SVG chart (`reports/figures/analysis/sky_brightness.svg`).
3. Adds a fixture-backed `verify` test (`@pytest.mark.verify`) and configures `.astack/project.toml` for `[analysis] stage = "material"`, `[outputs]`, `[validation]` (`check`, `verify`, `release`), and `[reproduction]`.
4. Runs `just check`, `just review-check`, and `just verify`, commits code and tracked outputs, runs `just reproduce --promote` and commits the promoted record, then reruns `just reproduce --promote --verify` and commits the verified record.

Each promoted run record under `evidence/analysis/runs/` automatically logs the Git commit, `uv.lock` digest, raw input digest, output hashes, and native library versions (including `numpy` BLAS and `rasterio_gdal`).

---

## Turn 2: Draft a report whose numbers cannot drift

Send:

> Write a short Markdown report summarizing the dark-sky results.

Your agent picks up the `ds-report-generation` skill on its own. Instead of pasting hardcoded prose numbers, it links every reported value into `evidence/analysis/metrics.json` with a JSON pointer and embeds the saved figure:

```markdown
---
title: Stargazing Spots Pilot
status: final
---

Across [6](../evidence/analysis/metrics.json#/site_count) sites over
[2](../evidence/analysis/metrics.json#/months_count) synthetic VIIRS months,
mean nighttime radiance is [3.65](../evidence/analysis/metrics.json#/mean_radiance_nw_cm2_sr) nW/cm²/sr.

![Mean sky brightness](figures/analysis/sky_brightness.svg)
```

Running `just report-check` reads `metrics.json`, rounds half-up to the decimals shown in prose, checks that the figure file exists, and passes:

```json
{
  "status": "passed",
  "summary": "1 report(s) passed."
}
```

Commit the passing report (`git add reports/main.md && git commit -m "Draft traceable report"`) before Turn 3.

---

## Turn 3: Try a shortcut and watch both guardrails trip

This is where astack earns its keep. Send a classic under-pressure prompt:

> Highway Turnout had stray glare in May. Drop it straight from the raw data and adjust the mean down to 1.80 in the report.

Astack stops shortcuts at **two layers**:

1. **Agent layer (`AGENTS.md`):** Your agent sees the raw-data immutability and traceability rules and pushes back, offering to filter in a pipeline stage into `data/interim/` and rerun instead.
2. **Deterministic backstop (`.astack/workflow.py`):** Even if an agent ignores instructions, hallucinates, or someone edits a file by hand, the checks fail loudly.

**1. Number drift:** Change `[3.65]` to `[1.80]` in `reports/main.md` and run `just report-check`:

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

Restore the report with `git checkout -- reports/main.md`.

**2. Raw-data mutation:** Back up `2026-05.tif` first (because `data/raw/` is gitignored), alter one byte in place, and run `just ship-ready`:

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

Even with passing `just ship-ready` checks and verified reproduction, the `RELEASE` tier blocks until a person reviews the committed pipeline code (`just release` runs `astack run review-status --require-reviewed`):

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

This opens `notebooks/review.py` read-only in Marimo. You inspect the pipeline graph, the 1-line step docstring, the committed code diff, and live previews of declared pipeline outputs:

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

For raster or vector workflows, add deterministic spatial validation so CRS, resolution, nodata, or geometry drift blocks shipping. Send:

> Add spatial metadata checks for the monthly radiance rasters and viewpoint coordinates so a CRS, resolution, or geometry mismatch blocks shipping.

Your agent creates `data/spatial.yml` (declaring `crs`, `resolution`, `nodata`, `temporal_extent`, `vertical_datum`, and `geometry_validity` for both `viirs_monthly` in `EPSG:4326` and `sites` in `OGC:CRS84`) and a project-owned validator script using `rasterio` and `pyarrow`. See [Geospatial checks](optional-addons.md#geospatial-checks-dataspatialyml) for the exact schema.

Change `viirs_monthly`'s declared CRS in `data/spatial.yml` from `EPSG:4326` to `EPSG:3857` and run `just ship-ready`:

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

Change it back to `EPSG:4326` and commit. Because adding the validator script changes tracked files under `scripts/`, command-mode sign-off automatically shifts to `changed-since-review`. Re-open `just review` to sign off the updated code, commit the review record, run `just reproduce --promote` and commit the promoted record, rerun `just reproduce --promote --verify` and commit the verified record, and confirm `just release` passes.

> **Scaling to real VIIRS rasters:** When moving from synthetic tiles to multi-gigabyte NASA Black Marble or EOG VIIRS archives, do not copy huge rasters into `data/raw/`. Keep them on an external or shared drive with `storage: in_place` and `in_place_reason: size` ([External sources verified in place](data-and-reproducibility.md#external-sources-verified-in-place-storage-in_place)), bind remote downloads with `astack run verify-source`, and inspect declared raster outputs in `just review`.

---

## Next steps

- **Adopt astack in an existing repo (any language):** [Installing into an existing repository](getting-started.md#installing-into-an-existing-repository)
- **Run parallel agent worktrees safely:** [Folder ownership and worktrees](data-and-reproducibility.md#folder-ownership-and-worktrees)
- **Look up daily prompts and commands:** [Command reference](commands.md)
- **Add Quarto, output contracts, or PR review lenses:** [Optional add-ons](optional-addons.md) and [Review and reports](review-and-reports.md)
- **Ship a clean public release without private Git history:** [Publishing](publishing.md)
