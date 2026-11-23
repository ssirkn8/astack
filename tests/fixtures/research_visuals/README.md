# Research visuals example

This synthetic example uses the installed skill and F1 style with a draft report and standalone visuals.
The checked-in PNGs let reviewers see the options without plotting dependencies. The builder produces
editable SVGs, report figures, standalone examples, and accessibility review sheets on demand.

From a project root with the installed skill and Matplotlib, copy `build.py` to
`scripts/lake_visuals.py` and the draft to `reports/lake.md`. Run:

```sh
python scripts/lake_visuals.py figures
python scripts/lake_visuals.py prototypes
python scripts/lake_visuals.py qa
python scripts/lake_visuals.py standalone --out scratch/visuals/standalone
```

The draft contains a pending selection marker and the command that rebuilds the options, because
`scratch/` is disposable. The prototype and QA commands write only under
`scratch/visuals/`. The QA pass reviews the hybrid candidate and the F1 plot in grayscale and
three color-vision simulations. Contrast targets and simulations do not establish WCAG conformance
or replace accessibility review by a person.

Once the owner chooses a layout, render that option explicitly. For example, option A has no insets:

```sh
python scripts/lake_visuals.py diagram --selected A
```

Replace the pending marker with the selected diagram and a caption that describes that design.
For A:

```markdown
![Grouped workflow linking simulated inputs to trend and agreement panels](figures/lake/workflow.png)

**Figure 1. Simulated monthly series and oxygen pairs feed separate analyses and report panels.**
```

For C, describe the insets and link the full [figure and table in the example output](output/reports/).
The project's DVC stages should run `figures` and `diagram --selected C` separately. The diagram stage
declares the current `reports/figures/lake/validation-thumbnail.png` and
`evidence/lake/site_trends.csv` as dependencies and `workflow.png` and `workflow.svg` as outputs.
The readable Table 2 is `reports/tables/lake/site_trends.md`. Neither stage runs the prototype or QA commands. Run `just reproduce` and `just report-check` for
the material report, then verify that changing a declared inset makes the selected diagram stale.
