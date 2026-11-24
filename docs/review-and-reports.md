# Review and reports

Code review on pull requests catches bugs inside a single change, but several clean PRs can still contradict each other across a pipeline (for example, two steps converting the same unit twice). astack pairs pull-request review with a whole-pipeline human sign-off notebook, optional scientific output contracts, and reports whose cited numbers and figures are verified against repository files.

## Human code sign-off (`just review`)

`just review` opens `notebooks/review.py` read-only with code cells hidden, letting a person inspect the entire pipeline step by step and sign off committed code. Output labels (`just outputs`) and JSON audits (`just audit-json`) are for agents. The review notebook is for people.

### What gets signed off

- **DVC stages.** Each stage is one step. `foreach` and `matrix` items collapse into one step because they run the same code, with a table listing every item and a warning if any item's command differs.
- **Shared code.** Any code file used by two or more steps gets its own shared-code page, so changing it requires one review instead of one per step.
- **Step scope.** A step's checksum covers its declared non-data `deps`, its resolved commands, and its declared parameter values. Editing another step's parameter does not reset sign-off. `uv.lock` is excluded.
- **Non-DVC projects.** Projects without DVC have one step covering the declared reproduction command plus tracked files under `src/` and `scripts/`.

### Reading and signing off

- **Overview page.** Displays a clickable pipeline graph built from DVC alongside a table of items needing sign-off. **Sign off selected** covers only steps opened during the current session, locked to the exact bytes loaded when opened.
- **Step page.** Shows the script's one-line docstring as its purpose, upstream and downstream links, inputs, parameters, commands, code, and each output's currentness label. Previously reviewed steps show contextual changes since the last sign-off (with the full file one click away) plus lines referencing changed parameters. Upstream or shared-code changes appear as notes without resetting the step.
- **Statuses.** Recomputed on every load: `reviewed`, `changed-since-review`, `unreviewed`, `revoked`, `invalid-record`, or `unknown`. Missing dependencies, missing parameters, symlinked folders, or undeclared reads mark a step `unknown`, which never counts as signed off.
- **Signing off.** Writes `.astack/reviews/<step>.json` recording the git user, full commit SHA, covered paths, and checksum. Sign-off refuses uncommitted code, symlinked code files, or files modified after the page loaded.
- **Revoking.** Untick a signed-off step, optionally record a reason, and confirm. The record retains the prior sign-off inside a revocation entry, marks the step `revoked`, and blocks RELEASE until signed off again.

### Diff integrity and human-only boundaries

Changes replace a full file read only when the review record lists every covered file, the reviewed code was committed, that commit is in this branch's history, and every file is tracked and not a symlink. astack builds the diff directly from Git's object store against disk (ignoring Git filters, diff drivers, and replace objects). Trailing spaces (`␣`), tabs (`⇥`), and carriage returns (`␍`) are shown explicitly, invisible or control characters force a full read with code points displayed, all code is escaped, and files longer than 1,500 lines are clearly marked as truncated.

Only a human signs off code. Agents are prohibited from calling `record_review` or `revoke_review` or writing under `.astack/reviews/`. Any agent edit under `.astack/reviews/` shows up in the next PR diff. Viewing writes nothing into the repository (DVC may still use gitignored `.dvc/tmp/`).

Missing sign-offs are human work, so they block only the RELEASE tier via `astack run review-status --require-reviewed` in `[validation] release`. Routine CHECK runs `just review-check` (`marimo check`).

### Keeping the review notebook thin

All review logic lives in the managed runtime (`.astack/workflow.py`), not inside `notebooks/review.py`. That keeps `notebooks/review.py` thin so astack upgrades improve the review workflow without overwriting user files, and an unmodified `notebooks/review.py` never triggers notebook-only analysis warnings.

**Notes for astack contributors maintaining `notebooks/review.py`:**

| Change | Where | Verified by |
| --- | --- | --- |
| Step definition, status, or diff logic | `workflow/project_runtime/review.py` | `tests/test_review_steps.py` |
| Page sections and ordering | `review_sections` in `review.py` | `tests/test_review_steps.py` |
| Pipeline graph rendering | `templates/capabilities/marimo/notebooks/review_map.js` | Map tests |
| Controls, navigation, and page state | `templates/capabilities/marimo/notebooks/review.py` | Headless tests plus manual clicks in `marimo run` |

Rules enforced by maintainer tests:

- Keep `notebooks/review.py` within its line budget by moving logic into `workflow/project_runtime/review.py`.
- Never read a `_private` cell variable inside a Marimo callback (bind it through a default argument instead), and bind interactive elements with callbacks to named cell variables or a named `mo.ui.dictionary`.
- Because Marimo reuses control IDs across re-renders, the step action bar uses one AnyWidget (`REVIEW_ACTIONS_ESM`) guarded by one-use tokens from `ReviewActionGate`. Any new state that changes action meaning must use a setter wrapped by `voiding`.
- After editing `templates/capabilities/marimo/notebooks/review.py`, run `uv run ruff format templates/capabilities/marimo/notebooks/review.py`, update `VIEWER_NOTEBOOK_SHA256` in `workflow/project_runtime/common.py`, rebuild bundles with `python scripts/build_runtime_bundles.py`, and manually click through sign-off, revoke, and bulk sign-off in `marimo run`. When raising Marimo's version range, update `src/astack/scaffold.py` and the notebook header together and confirm native buttons still close the action bar.

## Scientific output contracts (`ds-output-contract`)

For material work heading toward a paper or report, opt into a project-level output registry in `.astack/project.toml`:

```toml
[scientific_outputs]
path = "docs/scientific-outputs.yml"
evidence_path = "evidence/scientific-outputs.yml"
```

Keep definitions separate from progress and human confirmations:

- `docs/scientific-outputs.yml` defines the research question, stable output IDs (`type`: `figure`, `map`, `table`, `dataset`, `metric`, `report`, or `other`, `role`: `headline`, `supplementary`, `review`, or `diagnostic`), required validation IDs, and an `analyses` map linking each analysis key to the outputs and machine evidence it produces.
- `evidence/scientific-outputs.yml` tracks mutable status (`planned`, `in_progress`, `completed`, `blocked`), evidence paths, and human `confirmations`.

Workflow with `ds-output-contract`:

1. Before building, inspect the derived contract for an analysis:
   ```sh
   astack run output-contract --analysis plot-depths
   ```
   Omit `--analysis` to assemble the project-wide summary across all analyses.
2. Review and amend the proposed headline outputs and required validation with a person.
3. Persist the returned `definition_digest` and explicit decision under `confirmations.<analysis>` in `evidence/scientific-outputs.yml`. Updating progress or evidence paths keeps confirmation valid. Changing output definitions or required validation changes the digest and forces reconfirmation.
4. Put one `Analysis: <key>` line on every issue and PR that creates or modifies declared outputs, and run `just ship-ready <key>` before opening the PR. It fails if `<key>` is undeclared or its confirmation is stale. Engineering-only PRs omit the `Analysis:` line.
5. Record new ideas as follow-up issues rather than silently expanding scope. Deadline pressure never permits skipping required validation.

## Traceable reports (`ds-report-generation` and `just report-check`)

The `ds-report-generation` skill drafts Markdown reports under `reports/<name>.md` from a fixed structure (question, data and provenance, methods, main results, limitations, reproducibility, and supplementary appendix) where every quantitative claim traces to files in the repository.

- **Link every cited number with a JSON pointer:** write `[0.82](../evidence/model/metrics.json#/auc)`. `just report-check` (`astack run report-check`) reads the JSON value, rounds half-up to the decimals shown in the report, and fails on any mismatch.
- **Embed saved figures:** place figures under `reports/figures/<component>/` and tables under `reports/tables/<component>/`. Figures generated dynamically by `.qmd` code cells cannot be verified by `report-check`.
- **Mark gaps honestly:** write `[UNRESOLVED: what is missing]` wherever evidence is missing instead of filler prose.
- **Strict Markdown rules:** `report-check` rejects raw HTML (other than HTML comments), Pandoc bracketed spans, raw `{=html}` blocks or attributes, Quarto shortcodes, attributes other than `width`, `height`, `#id`, and `.class`, and evidence paths containing `//`, encoded separators, or mid-path `..`.
- **Draft vs. final:** citing an outdated or missing output fails any report (`just outputs` shows why). Setting `status: final` in report front matter also fails on any `[UNRESOLVED` marker, any file not produced by a recorded run, or any `unknown` DVC output state.

Run the check across all reports (or specific files):

```sh
just report-check
astack run report-check reports/main.md
```

`report-check` proves cited values and figures trace to current repository files. It does not verify unlinked prose or judge scientific validity.

## Research visuals (`research-visuals`)

The `research-visuals` skill creates or revises methodology diagrams, multi-panel figures, captions, and tables, inside a report or standalone.

- **Clean storage boundaries:** exploratory prototypes live in `scratch/visuals/<name>/` (never cited in reports). Selected figures go to `reports/figures/<component>/` with editable sources (SVG with live text, or the script and data that rebuild them) and at least 300 DPI for raster deliverables. Readable tables go to `reports/tables/<component>/` while machine formats (CSV, JSON) stay in `evidence/<component>/` or `artifacts/<component>/`.
- **Methodology diagram selection:** every new or materially redesigned methodology diagram starts with structurally distinct prototypes (for example grouped spine, phase lanes, figure-enhanced flow, map-led columns, layer stack, or transformation strip) so a person can pick or combine options before finalizing. Save project-specific diagram styles in `docs/diagram-styles.md` rather than editing installed skills.
- **Contrast and color-vision review:** never carry essential distinctions by hue alone (pair color with labels, shapes, dashes, or patterns), target 4.5:1 contrast for body text and 3:1 for large text and essential graphic cues, and inspect final-size renders in grayscale and simulated color-vision deficiencies while remembering simulations do not replace human review.
