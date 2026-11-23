---
name: ds-report-generation
description: Create or update a report whose scientific claims are traceable to actual provenance, parameters, QA, figures, and outputs.
metadata:
  permission-level: workspace-write
---

# DS Report

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## Use when

Use when a project has selected a report capability and needs a draft or revision.

## Goal

Produce a report from repository evidence without inventing findings, values, citations, certainty, or completed validation.

## Procedure

When `[scientific_outputs]` is declared, read the project-level `output-contract` summary to
assemble stable output identities across contributing analyses. Distinguish headline, supplementary,
review/diagnostic outputs, required validation, and unresolved planned/blocked outputs.
Trace each claim to actual evidence, not confirmation or workflow status alone. Do not copy
scientific definitions into new per-analysis contracts. Missing evidence is not a finding.

1. Inspect `README.md`, `data/sources.yml`, any declared Scientific Output Contract, durable decisions and data issues, parameters, pipeline code, QA artifacts, figures, and existing report content.
2. For a new report, copy `.opencode/skills/ds-report-generation/report-template.md` to
   `reports/<name>.md`. Keep its sections: question, data and provenance, methods, main results,
   limitations, reproducibility, and an appendix of supplementary results. Main results are the
   headline outputs and the appendix holds supplementary ones. Review and diagnostic outputs stay
   in evidence/, but Limitations mentions any that change how the main results read. Delete a
   section that does not apply rather than padding it.
3. Link every reported number to the file it came from, as `[0.82](../evidence/<component>/metrics.json#/auc)`.
   The part after `#` is a JSON pointer, and the check compares the stored value, rounded half-up to the
   decimals shown, with the number in the text. Embed figures from `reports/figures/<component>/`.
   A number you cannot link is not reported. Write the report in Markdown only. Raw HTML other than
   comments fails the check, so use Markdown links and images, and Unicode such as `m²`
   instead of `<sup>`. Link evidence as `../evidence/...`, without a `..` after a folder name.
   Pandoc bracketed spans, raw blocks and attributes such as `{=html}`, and Quarto shortcodes
   fail too. Attributes after an image, link or inline code are limited to `width`, `height`,
   `#id` and `.class`. Escape a literal brace as `\{`. Save every figure as a file under
   `reports/figures/<component>/` and embed it. A figure drawn by running a `.qmd` code cell
   is not a file, so the check can't see it.
   For diagrams, figures, captions, and tables, follow `.opencode/skills/research-visuals/SKILL.md`.
   A full report with external or multiple sources gets a compact source table in Data and
   provenance, a provenance appendix, and a methods diagram where the workflow benefits from one.
   A new or materially redesigned methodology diagram waits for the owner's choice among prototypes.
   Omit what has no meaningful role, and say why in one line when a full report omits one of them.
4. Keep machine QA and provenance in `evidence/<component>/`, generated diagnostics in `artifacts/<component>/`, and human-facing figures and tables in `reports/figures/<component>/` and `reports/tables/<component>/`.
5. Describe sampling, missingness, exclusions, uncertainty, validation design, sensitivity, and applicable domain limitations.
6. Write `[UNRESOLVED: what is missing]` wherever evidence is missing, never plausible filler text.
7. Preserve meaningful custom content.
8. Prioritize declared, confirmed human-facing outputs over incidental diagnostics. Required QA may
   remain evidence-only when it does not materially affect interpretation.
9. Run `just report-check`. It fails when a figure or linked number points at a missing file, a file
   outside `evidence/`, `artifacts/`, `reports/figures/` or `reports/tables/`, a value that does
   not match, or an outdated or missing output (`just outputs` shows why). Rerun the pipeline
   instead of editing the number. Set `status: final` in the front matter only when it passes, because a final report
   also fails on any `[UNRESOLVED` marker. If the project uses Quarto, also render the report.

The check proves that cited values and figures trace to files. It does not prove that unlinked prose
is supported, or that the science is right. Say so in the output.

## Stop conditions

Stop when requested claims lack evidence, provenance is missing, rendering would expose sensitive data, or causal language exceeds the study design.

## Output

Return files changed, evidence used, unsupported claims omitted, unresolved scientific gaps, the `report-check` result, and render status.
