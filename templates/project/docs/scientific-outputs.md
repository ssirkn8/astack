# Scientific outputs and reviewable research

Keep proposal questions in your project's research proposal and link to them from GitHub issues rather than duplicating them. GitHub defines work scope, while stable scientific output IDs define deliverables. Several analyses may contribute to one output.

## Project registry

For material analytical work, opt into a project-owned definition registry in `.astack/project.toml`:

```toml
[scientific_outputs]
path = "docs/scientific-outputs.yml"
evidence_path = "evidence/scientific-outputs.yml"
```

Definition example (`docs/scientific-outputs.yml`):

```yaml
schema_version: 2
research_question: How well do modeled depths agree with observations?
context: research/proposal.md#validation
outputs:
  depth-validation:
    type: figure
    role: headline
    description: Compare observed and modeled depth in meters with uncertainty.
    validation: [depth-qa]
    communication:
      key_message: Agreement, bias, and uncertainty across independent samples.
      caption_requirements: Units, sample size, uncertainty, and methods reference.
      references: [research/visualization/references/example.png]
validation:
  depth-qa:
    description: Verify units, sampling independence, and sensitivity to exclusions.
analyses:
  prepare-depths:
    outputs: [depth-validation]
  plot-depths:
    outputs: [depth-validation]
    machine_evidence:
      - id: figure-run
        description: Reproducible figure generation and parameter evidence.
```

Output types are `figure`, `map`, `table`, `dataset`, `metric`, `report`, or `other`. Roles are `headline`, `supplementary`, `review`, or `diagnostic`. Required validation uses stable IDs. Optional communication intent belongs to the output, not a per-figure contract.

Keep mutable progress and explicit scientific confirmation separate (`evidence/scientific-outputs.yml`):

```yaml
schema_version: 2
outputs:
  depth-validation:
    status: planned
    evidence: []
validation:
  depth-qa:
    status: planned
    evidence: []
machine_evidence:
  plot-depths:
    figure-run:
      status: planned
      evidence: []
confirmations: {}
```

Run `astack run output-contract --analysis plot-depths` to inspect the derived Scientific Output Contract. Present outputs, validation, and the implementation plan before asking for approval. One explicit decision may cover both, while still recording the scientific confirmation in `evidence/scientific-outputs.yml`.

After explicit confirmation, save the returned `definition_digest` and human decision under `confirmations.plot-depths` as `{definition_digest: ..., evidence: ...}`. Unchanged definitions stay confirmed across sessions, while material definition changes force reconfirmation. Updating progress or evidence paths does not invalidate confirmation. Missing evidence never proves completion or scientific validity.

Issues and PRs that create or change declared outputs include one `Analysis: plot-depths` line next to `Closes #N`. Run `just ship-ready plot-depths` before opening the PR. It fails if the key is missing from `analyses` or its confirmation is stale. Plain `just ship-ready` checks the whole project, and engineering-only work omits the `Analysis:` line.

Running `astack run output-contract` without `--analysis` assembles the project-wide summary for reporting. Trace publication claims to actual artifacts, validation, provenance, parameters, and limitations, and separate planned or blocked outputs from completed ones.

Upgrades never rewrite scientific definitions or invent confirmation, and projects without `[scientific_outputs]` stay fully supported.

## Human reading path

Organize reusable computation under `src/` around scientific concepts. Keep scripts and the Marimo review notebook thin, never the production pipeline or sole QA record. Use clear inputs, outputs, and bounded responsibilities. Document non-obvious assumptions, including units, CRS, temporal basis, thresholds, transformations, exclusions, and methodological choices.

For spatial sources and outputs, record CRS, axis order, units, vertical datum, temporal extent, resolution, nodata, geometry validity, and reprojection steps in the optional `data/spatial.yml` described in [spatial.md](spatial.md).

Material PRs should expose a concise review map traced to changed code and real evidence:
research question -> data -> transformations/model -> validation -> output generation -> artifacts.
Check whether important methodology is opaque or hard to trace. That is scientific judgment, not a lint score.

## Publication figure and map principles

Priority order: scientific correctness and methodological validity first, then venue requirements, then accessibility and readability, then project preferences and resemblance to references. Do not force one palette, font, plotting library, or house style.

Use appropriate encodings, meaningful units and scales, uncertainty, defensible baselines, restrained academic design, and readable text at final display size. Avoid misleading emphasis, clutter, clipping, overlapping labels, or distinctions encoded only by color. Review captions, panel order, axis alignment, labels, and shared legends.

Raster publication figures default to at least 300 DPI at intended physical dimensions (higher venue requirements win). Vector output helps line and text quality where appropriate, but is not universally mandatory.

Optional visual guidance may live in `research/visualization/guidance.md` and references in `research/visualization/references/`. Missing references never block work. Infer useful tendencies instead of cloning examples.

Deterministic QA checks file presence, dimensions or resolution where applicable, traceable generation, and expected caption or evidence paths. Scientific communication review separately judges visual encodings, uncertainty, axes, smoothing, normalization, bins, projections, and scales. Missing judgment-based review is never a pass.
