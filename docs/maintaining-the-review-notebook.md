# Maintaining the review notebook

The notebook is a display. Keep it that way, and it stays cheap to change.

| Change | Where | Proved by |
|---|---|---|
| What counts as a step, a status, or a change | `workflow/project_runtime/review.py` | `tests/test_review_steps.py` |
| What a page shows, and in what order | `review_sections` in `review.py` | `tests/test_review_steps.py` |
| How the pipeline map draws | `templates/capabilities/marimo/notebooks/review_map.js` | map tests |
| Buttons, navigation and page state | `templates/capabilities/marimo/notebooks/review.py` | headless test, then a real click |

Rules the tests enforce:

- The notebook stays within its line budget. If it grows, move logic into `review.py`.
- A callback never reads a `_private` cell variable, because marimo deletes those after the cell
  runs. Bind it through a default argument.
- A UI element with a callback is a named cell-level variable, or sits in a named
  `mo.ui.dictionary`.

After editing the notebook:

1. Run `uv run ruff format templates/capabilities/marimo/notebooks/review.py`.
2. Update `VIEWER_NOTEBOOK_SHA256` in `common.py`. A test fails until it matches.
3. Run `python scripts/build_runtime_bundles.py`.
4. Click the flow you changed in a real `marimo run`. Headless tests prove cells run, not that a
   button works. Typed input and duplicated elements have both broken in ways only a click found.

Pitfall: marimo reuses a re-created control's id, so a click from the old page can run the new
page's callback. The step's action bar is therefore one AnyWidget (`REVIEW_ACTIONS_ESM`). Each
render gets a one-use token from `ReviewActionGate`, and the revoke reason stays in the widget until
Confirm. Any new state that changes what the bar means must use a setter wrapped by `voiding`.

Upgrading marimo: raise the range in `src/astack/scaffold.py` and the notebook header together,
then run the headless test and click through sign-off, revoke and bulk sign-off. Also check that
clicking a native button closes the action bar, because the widget matches the `MARIMO-BUTTON` tag.
