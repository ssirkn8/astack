# Scientific outputs and reviewable research

Keep proposal questions in a project-owned research proposal. Reference relevant questions
from GitHub issues rather than copying the proposal. GitHub defines work scope. Stable
scientific output IDs define deliverables. Several analyses may contribute to one output.

## Project registry

For material analytical work, declare one project-owned definition registry:

```toml
[scientific_outputs]
path = "docs/scientific-outputs.yml"
evidence_path = "evidence/scientific-outputs.yml"
```

Definition example:

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

Output types are `figure`, `map`, `table`, `dataset`, `metric`, `report`, or `other`.
Roles are `headline`, `supplementary`, `review`, or `diagnostic`. Required validation uses
stable IDs. Optional communication intent belongs to the output, not a per-figure contract.

Keep mutable progress and explicit scientific confirmation separate:

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

Run `uv run --locked python .astack/workflow.py output-contract --analysis plot-depths`
to derive the relevant Scientific Output Contract. Present scientific outputs, validation,
and the implementation plan before asking for approval. One explicit decision may cover both,
but preserve distinct scientific confirmation and Resume execution receipts.

After explicit scientific confirmation, persist the returned `definition_digest` and human
decision under `confirmations.plot-depths` as `{definition_digest: ..., evidence: ...}`.
Unchanged definitions retain confirmation across fresh sessions. Relevant definition changes
require reconfirmation. Progress and evidence-path updates do not change scientific definitions.
Missing evidence never demonstrates completion or scientific validity.

Issues and PRs that create or change declared outputs name their analysis with one line, such as
`Analysis: plot-depths`, next to `Closes #N`. Run `just ship-ready plot-depths` before opening the
PR. It fails when the key is not in `analyses` or its confirmation no longer matches the
definitions. Plain `just ship-ready` checks the whole project. Engineering-only work names no
analysis.

The summary without `--analysis` assembles project-level outputs across contributors for
reporting. Trace publication claims to actual artifacts, validation, provenance, parameters,
and limitations. Distinguish planned/blocked outputs from completed outputs.

Upgrades never rewrite scientific definitions or invent confirmation.
Projects with no declared capability remain supported.

## Human reading path

Organize reusable computation under `src/` around scientific concepts. Keep scripts and
optional Marimo review notebooks thin, never the production pipeline or sole QA record.
Use meaningful inputs, outputs, names, and bounded responsibilities. Explain scientific
rationale and non-obvious assumptions, including units, CRS, temporal basis, thresholds,
transformations, exclusions, and methodological choices. Avoid arbitrary line-count gates.
For spatial sources and outputs, record CRS, axis order, units, vertical datum, temporal extent,
resolution, nodata, geometry validity, and reprojection steps in the optional structured
`data/spatial.yml` described in [spatial.md](spatial.md).

Material PRs expose a concise review map derived from changed code and actual evidence:
research question → data → transformations/model → validation → output generation → artifacts.
Review whether important methodology is opaque or difficult to trace. This is scientific
judgment, not a lint score.

## Publication figure and map principles

Scientific correctness and methodological validity take precedence over venue requirements,
which take precedence over accessibility/readability, project visual preferences, and resemblance
to references. Do not impose one palette, font, plotting library, or house style.

Use appropriate encodings, meaningful units/scales, uncertainty, defensible baselines and
classifications, restrained academic design, and readable text at intended display size.
Avoid misleading emphasis, clutter, clipping, overlapping labels, or distinctions encoded
only by color. Review captions and, where relevant, panel ordering, axis compatibility,
alignment, labels, and shared legends.

Raster publication outputs default to at least 300 DPI at intended physical dimensions.
Higher figure-specific or venue requirements take precedence. Vector output is useful for
line/text quality where appropriate, not universally mandatory.

Optional project-owned guidance may live in `research/visualization/guidance.md` and references
in `research/visualization/references/`. Missing/inaccessible references never block generation
or lifecycle work. Infer useful tendencies, do not blindly clone examples.

Deterministic QA checks artifact presence, dimensions/resolution where applicable, traceable
generation, and expected caption/evidence paths. Scientific communication review separately
assesses appropriate visualization, uncertainty, readable encodings, and scientifically
defensible axes, smoothing, normalization, bins, projections, and scales. Use the workflow's
evidence-state vocabulary. Missing judgment-based review is not a pass.
