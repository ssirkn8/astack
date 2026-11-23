# Spatial evidence (optional)

Use this only when sources or outputs are spatial. astack adds no GIS dependency. Your project
chooses its own tools (for example GDAL, rasterio, pyproj, or GeoPandas) and owns the validators.

Create `data/spatial.yml`:

```yaml
schema_version: 1
# Project-defined checks that compare this metadata with the actual data. They run with
# `just ship-ready` (audit --run-tools) and must exit 0. They are never skipped silently.
validators:
  - ["uv", "run", "--locked", "python", "scripts/validate_spatial.py"]
sources:
  landcover:            # a source id from data/sources.yml
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
    # the same fields, describing the derived product
```

Every record declares `crs`, `vertical_datum`, `temporal_extent`, `resolution`, `nodata`, and
`geometry_validity` with the usual states: `known` with a value, `not_applicable` with a reason,
or `unknown` / `not_yet_verified`. A `known` CRS states its identifier, axis order, and units. A
known resolution states positive x and y and their units. Known geometry validity states the method
and result. `reprojection` lists each step's source and target CRS and resampling method, and is
empty when nothing was reprojected.

The audit checks structure only. Unknown or unverified fields, missing fields, unknown source ids,
unsafe output paths, and metadata with no validator all block release. Whether a CRS, resolution,
or resampling method suits the question is scientific review, not an automated check.

A validator reads `data/spatial.yml`, opens each source or output, and fails on any disagreement,
for example a different CRS or axis order, resolution, nodata value, temporal coverage, or invalid
geometries. Areas and distances should be computed in an appropriate projected or geodesic system.
Evaluation of spatial models should account for spatial dependence, and sensitivity to support and
resolution should be reported where it matters.
