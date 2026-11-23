---
name: ds-repo-audit
description: Audit workflow conformance, computational reproducibility, scientific credibility, and release readiness, then publish a sanitized GitHub issue with local fallback.
metadata:
  permission-level: read-mostly-with-audit-output
---

# DS Repository Audit

Scientific review includes a human reading path from question through data, reusable methodology,
validation, output generation, and artifacts. Flag opaque scientifically important logic using
the existing evidence-state vocabulary, not arbitrary function/file size metrics.
Inspect units, CRS, temporal basis, transformations, exclusions, and methodological rationale.

For figures/maps, separate deterministic artifact/resolution/provenance evidence from judgment
about scientific appropriateness, uncertainty, accessible encodings, readable captions, and panel
composition. Raster publication outputs default to at least 300 DPI at intended physical size,
with venue requirements taking precedence. Vector output is useful, not universally mandatory.
Scientific correctness overrides styling and visual-reference similarity. Optional project visual
guidance and references never block work. Use `docs/scientific-outputs.md` when available.

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## Use when

Use after major analytical changes, before a report or release, after workflow upgrades, or when research credibility is uncertain. Run only when explicitly invoked.

## Goal

Produce five distinct evidence-based verdicts:

1. Workflow conformance
2. Software checks: the configured check and integration commands ran and passed
3. Computational reproducibility: only reproduction evidence for a material analysis can pass it;
   passing software checks never do
4. Scientific credibility
5. Release readiness

Do not convert a missing or unperformed check into a pass.

## Arguments

- `scope`: `auto | workflow | science | full | public-release`, default `auto`
- `risk`: `auto | low | medium | high | critical`, default `auto`
- `publish`: `auto | github | local | none`, default `auto`

`auto` always runs deterministic workflow checks. It adds scientific review when reports, models, material analytical outputs, high-impact decisions, sensitive populations, or public release are involved.

## Deterministic checks

Run `just audit-json` when available. Inspect its structured output rather than improvising equivalent commands. Check:

- installed workflow version and managed-file drift
- Git state and raw-data immutability
- likely secrets and large-file risks
- provenance schema and data-source coverage
- package, tests, CI, and strict shipping targets
- optional DVC, Quarto, and Marimo capabilities only when configured
- configured validation-tier declarations and whether optional VERIFY or RELEASE work was assessed
- lifecycle path classification, declared component association, and legacy placement warnings
- durable machine-readable evidence under `evidence/<component>/` and claim traceability
- human-facing figures and tables under component-scoped `reports/` destinations
- durable QA artifacts and claim traceability
- declared Scientific Output Contract structure, completion states, and evidence paths
  Include the runtime `output_contract` summary in results, keeping required incomplete or unknown
  output/validation evidence separate from optional deferrals. Report undeclared capability as
  `not_declared`, not a failure. Verify definition-bound human confirmation without inferring it from
  a boolean flag, plan approval, or silence.

## Scientific review

Assess applicable evidence for:

- research question, intended use, target population, unit of analysis, outcomes, and impact
- data fitness, collection process, coverage, representativeness, selection bias, and measurement error
- missingness, duplicates, exclusions, outliers, data defects, and leakage
- validation splits appropriate to temporal, grouped, repeated-measure, clustered, or other dependence in the project
- model assumptions, baselines, diagnostics, calibration, uncertainty, effect sizes, multiple testing, sensitivity, and robustness
- exploratory versus confirmatory analysis and whether causal language is justified
- stochastic reproducibility beyond merely setting a random seed
- limitations, external validity, privacy, fairness, and foreseeable misuse
- direct traceability from claims to code, parameters, source records, QA, figures, and outputs
- delivery of declared required outputs and validation, with optional diagnostics assessed separately
- human confirmation of headline outputs before substantial optional visualization work

When headline outputs or another material scientific choice remain unresolved, use the native
question tool with the scientific context and a free-form response available. If unavailable or
canceled, ask in text and stop the affected optional or interpretive work. A declined answer remains
unresolved, not a pass. Record required decisions in the Scientific Output Contract or other
authoritative project record.

Use only these evidence states:

```text
Supported | Concern | Missing evidence | Not applicable | Needs domain review
```

Never certify scientific validity from repository structure alone. Escalate consequential health, safety, policy, resource-allocation, or vulnerable-population work to deeper review.
Deadline pressure may narrow optional analysis or presentation work, but it never excuses missing
required correctness, validation, evidence, or reproducibility.

Routine audit and `just ship-ready` cover CHECK-level workflow validation. With scientific
validation enabled, `just ship-ready` also runs declared VERIFY, and an undeclared VERIFY blocks it
once the analysis is material. VERIFY and RELEASE are explicit project-owned tiers. Run them only when the project declares applicable commands and the
requested review scope warrants the cost. An unconfigured, skipped, unavailable, or failed tier is
not a pass.

## Publication

Write an untracked, Git-ignored pending Markdown report under `reports/audits/`, then use the managed
workflow tool to publish it. Never publish a tracked or unignored audit report, even to a private
repository.

Wrap details that must not appear in a public issue with:

```text
<!-- sensitive-details:start -->
...
<!-- sensitive-details:end -->
```

For a public repository, also provide exactly one deliberately sanitized block:

```text
<!-- public-summary:start -->
Only non-sensitive verdicts, counts, and remediation categories
<!-- public-summary:end -->
```

Publication fails closed to the local report when this block is absent or contains a likely
credential or private absolute path.

File the report with:

```text
python .astack/workflow.py file-audit --report <path> --commit <sha> --scope <scope> \
  --to <mode> --actionable-count <count> --remove-on-success
```

- One issue represents one audited commit and scope.
- Publish only when the supplied full commit SHA equals `HEAD` and the worktree is otherwise clean.
- Match issues with a hidden schema, commit, and scope marker across open and closed states.
- Reuse an exact match and skip an identical rerun.
- Keep actionable audits open and close clean audits as completed.
- Do not auto-close older audits with unresolved findings.
- On public repositories, publish only a sanitized summary and keep sensitive remediation details in the local report.
- If GitHub is unavailable, issues are disabled, authentication fails, or publication is unsafe, retain the local report.
- Remove the untracked, ignored local pending report after successful private-safe publication
  unless `publish=local` was requested.

## Forbidden actions

Do not fix findings, modify research code, commit, push, create a PR, expose sensitive details, or claim checks that were not performed.

## Output

Return all five verdicts, finding counts by priority, checks and scientific areas not assessed, issue URL or fallback path, publication status, and next action.
