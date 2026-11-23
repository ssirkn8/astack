---
title: "Report title"
# Set to final only when `just report-check` passes and every UNRESOLVED marker is gone.
status: draft
---

# Report title

<!--
How to use this template:
- Copy it to reports/<name>.md. Delete sections that don't apply instead of filling them.
- Link every reported number to the file it came from, so `just report-check` can verify it:
  [0.82](../evidence/<component>/metrics.json#/auc)
  The part after # is a JSON pointer. The check rounds the stored value to the decimals you show.
  Without a pointer, the check only confirms that the file exists.
- Embed figures from reports/figures/<component>/ and link tables in reports/tables/<component>/.
- Use Markdown only. Raw HTML other than comments fails the check.
- Review and diagnostic outputs, exploratory runs and failed attempts stay out of the report and
  remain in evidence/. Limitations says when one of them changes how the results should be read.
- Write [UNRESOLVED: what is missing] wherever evidence is missing. Never fill the gap with a
  plausible guess.
-->

## Question

[UNRESOLVED: the research question or decision this report informs, in one or two sentences]

## Data and provenance

<!-- Compact inventory for a full report. Split rows when dates, units, definitions, or provenance
differ. A shorter report may use one sentence for a single source. Full details go in Appendix:
source provenance. -->

**Table 1. Data sources used in this report.** Year is the observation period.

| Dataset | Year | Data type / resolution | Source |
|---|---|---|---|
| [UNRESOLVED: dataset] | [UNRESOLVED: observation years] | [UNRESOLVED: type and source resolution] | [UNRESOLVED: provider] |

## Methods

<!-- What was done, in order, and the command that reproduces it. Take parameters from files.
Add a workflow diagram when it helps, using the research-visuals skill. A new or materially
redesigned methodology diagram needs the owner's choice among distinct prototypes first. Until
then, put this in visible Methods text, outside this comment:
[UNRESOLVED: methodology diagram selection] Rebuild the options with `<command>`.
After the choice, replace both with the diagram and caption:
![Workflow description](figures/<component>/workflow.png)
**Figure 1. One bold sentence that says what the workflow does.** Add detail only if needed,
and link the full version of any embedded figure or table. -->

[UNRESOLVED: methods]

## Main results

<!-- The publishable findings: outputs with the `headline` role in the Scientific Output
Contract. Without a contract, choose the main outputs yourself and say so here.
Each number and figure links to evidence. Use (a), (b), (c) only for multiple panels:
![Figure description](figures/<component>/<figure>.png)
**Figure 2. One bold sentence stating the finding.** (a) First panel. (b) Second panel. -->

[UNRESOLVED: main results]

## Limitations

<!-- Sampling, missingness, exclusions, uncertainty, validation design, sensitivity, and the
claims the design cannot support, such as causal ones.
Give a one-line mention, linked to its evidence, of any excluded output that changes how the
main results should be read: a failed validation, a sensitivity check that reverses an effect,
or how many models or specifications were tried. -->

[UNRESOLVED: limitations]

## Reproducibility

<!-- Commit, run record under evidence/<component>/runs/, and the command: just reproduce. -->

[UNRESOLVED: commit and run record]

## Appendix: supplementary results

<!-- Outputs with the `supplementary` role, such as robustness and sensitivity checks and extra
figures or tables, linked to evidence the same way. Delete this section if there are none. -->

[UNRESOLVED: supplementary results, or delete this section]

## Appendix: source provenance

<!-- One row per entry in data/sources.yml. Keep source and analysis or display resolution apart,
and observation and release dates apart. Write Unknown or UNRESOLVED, never a guess. If a shorter
report omits this appendix, delete it and say why in one line under Data and provenance. -->

**Table A1. Provenance of each data source.**

| Source ID | Observed | Released | Source resolution | Analysis or display resolution | Identity | Open gap |
|---|---|---|---|---|---|---|
| `<source-id>` | [UNRESOLVED: observation dates] | [UNRESOLVED: release date or version] | [UNRESOLVED: source resolution] | [UNRESOLVED: analysis or display resolution] | [UNRESOLVED: verified or unverified] | [UNRESOLVED: open gap, or None] |
