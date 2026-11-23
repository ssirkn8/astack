---
name: research-visuals
description: Create or revise research diagrams, scientific figures, and tables, inside or outside a report. Use for methodology, pipeline, conceptual, or work-package diagrams, multi-panel plots, captions, and source or evidence tables.
metadata:
  permission-level: workspace-write
---

# Research Visuals

For human-response decisions, apply `.opencode/skills/human-decisions/SKILL.md`.

## Use when

Use when someone asks for a diagram, scientific figure, caption, or table, or when
`ds-report-generation` needs one. Each capability works alone. None of them needs a report.

## Goal

Produce a legible, editable visual whose content traces to real sources, without inventing data,
steps, dates, resolutions, or results.

## Side effects and boundaries

- Write only inside the workspace. Keep prototypes in `scratch/visuals/<name>/`, which a report must
  not cite. Put a selected report figure in `reports/figures/<component>/` and a readable table,
  such as Markdown, in `reports/tables/<component>/`. Keep CSV, JSON, and other machine formats in
  `evidence/<component>/` or `artifacts/<component>/`, because component-layout reproduction rejects
  them under `reports/`. For standalone work, use the path the requester names.
- `scratch/` is disposable. When a methodology choice may wait, keep a small prototype builder in a
  Git-tracked path, such as `scripts/<name>_visuals.py`. It reads inputs from their normal paths and
  embeds no data, so the options can be rebuilt without saved images.
- Do not edit a canonical document only because it supplied the content.
- Do not copy private, licensed, or uncleared images into a visual meant for a wider audience.
- Keep an editable source for every delivered visual: SVG with live text, or the script and data
  that rebuild it. Deliver a 300 dpi or higher PNG when a raster is needed.

## Shared procedure

1. Ground the visual. Read the source, and list its real inputs, operations, products, dates, and
   units. Write `[UNRESOLVED: what is missing]` for a gap in a report, or name the gap when working
   standalone.
2. Keep meaning fixed. Treat nodes, dependencies, plotted values, and table facts as invariants
   during visual revisions.
3. Render the actual output at final size and look at it. Fix clipping, overlap, and unreadable
   text, including text inside insets and table excerpts. In a diagram, confirm that each connector
   attaches to the right nodes and crosses no other node, and that section titles keep clear space
   from frames, nodes, and connectors.
4. Review contrast and color vision using the criteria below, then report what ran and its limits.

## Contrast and color vision

These are authoring targets drawn from W3C WCAG 2.2
[Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html),
[Contrast (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), and
[Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html). Meeting
them is not a conformance claim, and no palette is mandatory.

- Never carry an essential distinction by hue alone. Add a label, shape, marker, dash, pattern, or
  direct annotation.
- Keep ordered scales visibly ordered in lightness, with a labeled legend or color bar. Add a text
  or data fallback when readers need exact values.
- Check text at final size against its actual background, including insets and table labels. Aim
  for 4.5:1, or 3:1 only for genuinely large text (about 18 pt, or 14 pt bold).
- Aim for 3:1 for essential graphic cues against adjacent colors, such as lines, markers, node
  boundaries, and arrows. A pale fill can sit below that when a darker outline carries the shape.
- Review a representative final-size render, with insets, in grayscale and in simulated protanopia,
  deuteranopia, and tritanopia. Fix any distinction that disappears.
- Say that simulations and computed ratios do not replace review by people with color-vision
  deficiencies.

## Diagrams

No diagram layout is the default. Choose the family from the meaning: pipeline, conceptual
framework, work package, decision flow, or another fit.

### Methodology diagram selection

Every **new or materially redesigned** methodology diagram needs owner selection before it is final.
A material redesign changes the layout family, the grouping logic, or which visual evidence appears.

Different research suits different styles. Offer the range that fits this method, and skip any
style that does not.

| Family | Offer it when |
|---|---|
| Grouped spine | The method reads mainly in sequence, with meaningful stages. |
| Phase lanes | Parallel tracks or phases benefit from side-by-side comparison. |
| Figure-enhanced flow | An existing figure or table excerpt clarifies a method step. |
| Map-led columns | Real input maps each need to be seen, with their steps aligned beneath them. |
| Layer stack | Inputs truly share a grid or footprint. Mark a shared cell only if it is the same cell. |
| Transformation strip | Real intermediate outputs show how the data changes at each step. |

This list is a starting point. If `docs/diagram-styles.md` exists, read it first. Its families and
preferences add to or override this menu, but not the evidence, selection, or accessibility rules.
Add another family when the method calls for one.

To save a style for one project, add a row to that file using the same two columns. Describe its
structure, not study content. Do not edit this installed skill, because upgrades stop on an edited
skill.

1. Build one grounded prototype of the same graph for each family that fits, with stable IDs such
   as `A`, `B`, and `C`. Aim for at least three. If the list yields fewer, look for another honest
   structure, such as a decision flow or conceptual framework. If fewer than three still fit, offer
   those and say why. Build at most four unless the owner asks for more, choosing the most different
   and naming the other suitable families. Options must differ in structure. A palette or font
   change alone does not count, and a past pick does not narrow the range for a new diagram.
2. Include one plain option with no embedded images. Include a **figure-enhanced option** when an
   existing, provenance-backed image or plot genuinely clarifies a method step. Say so when no such
   asset exists, and do not make a decorative one.
3. Add a small table excerpt only when it helps explain that step. Keep it to about three rows and
   three columns, readable at print size, marked as an excerpt, with omitted rows shown.
4. Show the rendered options side by side at a readable size, each with a one-line tradeoff, and a
   recommendation. Keep a full-size file for each.
5. Stop and let the owner pick or combine options, now or later. In a report, keep
   `[UNRESOLVED: methodology diagram selection]` and the command that rebuilds the options in visible
   Methods text, not an HTML comment, which `just report-check` skips. After the choice, replace
   both with the selected diagram and caption. Do not promote a prototype.
6. Refine the selected design without asking again for routine changes to labels, spacing, routing,
   or ports. Ask again only for a material redesign.

Skip the prototype round only when the owner already named the exact design for this diagram. A
preference from another diagram does not count.

### Embedded visuals

- Build thumbnails and excerpts from the current full asset or its data, never by retyping values
  or pasting a screenshot. Label each with its figure, table, or source.
- Do not shrink a figure until its text is unreadable. Re-render a text-free thumbnail from the same
  data and label it at final size.
- Link each full asset from the caption, and keep cited numbers in report text linked to evidence.
- When the diagram is a report output, build it in a pipeline stage that declares every embedded
  asset as a dep. `just report-check` cannot read inside an image, so this is what labels the
  diagram outdated when an embedded asset changes.
- Fall back to a plain readable diagram when no useful, safe visual exists, or when an embedded
  visual would be illegible at final size.

## Scientific figures

Start from the owner-selected **F1 journal serif** style in
`.opencode/skills/research-visuals/journal-serif.mplstyle`: serif type, a full thin axes box, faint
guides, outward ticks, and restrained color where color carries data.

```python
from pathlib import Path
import matplotlib.pyplot as plt

plt.style.use(Path(".opencode/skills/research-visuals/journal-serif.mplstyle"))
```

Venue rules, a requested task style, and scientific legibility override F1. Non-Python plots should
carry over the same conventions. Say when the Times New Roman font is unavailable and a fallback was
used.

- **Panels:** Use `(a)`, `(b)`, `(c)` only with two or more panels, in reading order, for example
  `ax.set_title("(a) Observed series", loc="left")`. A single-panel figure has no letter. If a
  venue requires another format, use it in both the figure and caption.
- **Captions:** Start with one bold, descriptive sentence. Add detail after it only when the reader
  needs it. Refer to each panel by its letter.

  ```markdown
  **Figure 2. Oxygen trends differ across monitoring sites.** (a) Monthly series for two sites.
  (b) Decadal trends with 95% confidence intervals.
  ```

- **Encoding:** One hue means one thing across a figure. Missing, zero, and out-of-scope values must
  look different. Share a legend or color bar when panels share an encoding.
- **Restraint:** Every panel must add something. Keep axis labels and units readable, and move
  support details to the caption or appendix.

## Tables

Choose the schema from the task: source inventory, variable definitions, method comparison, evidence
synthesis, or project information. Do not force a source inventory onto another task. Split rows
when dates, units, definitions, provenance, or uncertainty differ.

For a **full research report** with external or multiple data sources, use both:

- **T1 compact inventory, in Data and provenance:** `Dataset | Year | Data type / resolution | Source`.
- **T4 provenance appendix:** one row per `data/sources.yml` entry, with `Source ID | Observed |
  Released | Source resolution | Analysis or display resolution | Identity | Open gap`. Add a linked
  record count when it helps. Drop a column only when it adds nothing for every row.

Keep source resolution separate from analysis or display resolution, and observation dates separate
from release dates, wherever the difference is material. Write `Unknown` or an `[UNRESOLVED: ...]`
marker for a missing value, and never infer one.

A shorter report may use T1 alone, or a sentence for a single source. State the reason in one line
when you omit T4 from a report that would otherwise have it, such as "One internal source, fully
described in Table 1."

## Stop conditions

Stop when the source cannot support the visual, an embedded asset is private or uncleared for the
audience, or a methodology diagram is waiting on owner selection.

## Output

Return the files created, editable sources, options offered or selected, checks run, what was visually
inspected, and unresolved gaps.
