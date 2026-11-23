# Scientific Audit Standard

Repository structure and passing tests do not establish scientific validity. The DS audit pairs
objective machine checks with an explicitly interpretive review.

## Verdicts

Every audit reports workflow conformance, software checks, computational reproducibility,
scientific credibility, and release readiness separately. Software checks report only whether the
configured check and integration commands ran and passed. Computational reproducibility stays
`not-assessed` until a material analysis has reproduction evidence, and release readiness stays
`not-assessed` while reproducibility has not passed. Interpretive evidence uses only:

```text
Supported | Concern | Missing evidence | Not applicable | Needs domain review
```

## Risk-Adaptive Depth

Routine audits run deterministic conformance and reproducibility checks. Scientific depth
increases for material analytical outputs, reports, model or validation changes, public release,
sensitive data, and decisions affecting health, safety, policy, resources, or vulnerable groups.

## Scientific Areas

- question, intended use, target population, analytical scope, unit of analysis, and outcomes
- collection process, fitness, coverage, representativeness, bias, and measurement error
- missingness, exclusions, duplicates, outliers, data defects, and leakage
- validation appropriate to temporal, grouped, repeated-measure, clustered, or other dependence
- assumptions, baselines, diagnostics, calibration, uncertainty, effect sizes, multiple testing,
  sensitivity, and robustness
- exploratory, predictive, descriptive, and causal claim boundaries
- stochastic and environment reproducibility
- external validity, limitations, privacy, fairness, and foreseeable misuse
- claim traceability to sources, code, parameters, QA, figures, and outputs

An audit identifies evidence and review needs. It does not replace a qualified statistician,
methodologist, domain expert, ethics review, or data steward when those are warranted.

## Output Evidence Locations

Keep evidence and outputs scoped to the analysis component that produced them:

- `artifacts/<component>/` for generated machine outputs such as models and predictions
- `evidence/<component>/` for durable QA, validation, and run records
- `reports/figures/<component>/` for report figures
- `reports/tables/<component>/` for report tables
- `scratch/` for disposable work that must not support a scientific claim

The component name should match the corresponding entry in `[outputs].components` in
`.astack/project.toml`. A report claim should cite the source, code, parameters, evidence, and
component-scoped output that support it.

## Raw Immutability Evidence

The deterministic audit compares committed modifications, deletions, renames, and type changes
under `data/raw/` with the pull request base merge-base. Generated CI retains full Git history so
that comparison remains available.

For a push, CI sets `ASTACK_AUDIT_BASE_SHA` from `github.event.before`, and the audit compares that
exact prior commit with `HEAD`. The value must be a full lowercase SHA that exists in the clone.
GitHub uses an all-zero prior SHA for an initial branch push. Because no prior tree exists, that
case is reported as unavailable blocking evidence rather than a pass. A malformed or unavailable
SHA also blocks strict audit.

## Evidence Basis

- Wilson et al. (2017), Good enough practices in scientific computing,
  https://doi.org/10.1371/journal.pcbi.1005510
- Wilkinson et al. (2016), FAIR Guiding Principles,
  https://doi.org/10.1038/sdata.2016.18
- Barker et al. (2022), FAIR Principles for research software,
  https://doi.org/10.1038/s41597-022-01710-x
- The Turing Way, Guide for Reproducible Research,
  https://the-turing-way.netlify.app/reproducible-research/reproducible-research.html
- UK Government Analysis Function, Quality assurance of code for analysis and research,
  https://best-practice-and-impact.github.io/qa-of-code-guidance/
