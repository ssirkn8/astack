# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anywidget>=0.9,<1",
#     "marimo>=0.20,<1",
#     "pyarrow>=15",
# ]
# ///
"""Read this workspace's pipeline step by step, and sign off what you have read.

Open with `just review`. Pull requests review each change. This notebook is where you read a step
as a whole. It only displays: every decision lives in astack's runtime (`review_status`,
`review_page`, `review_map`), and signing off or revoking are the only things it writes, under
`.astack/reviews/`. Keep it thin. Name every UI element with a callback as a cell-level variable,
and bind `_private` names into closures through default arguments, since marimo deletes them.
"""

import marimo

__generated_with = "0.20.4"
app = marimo.App(width="medium", app_title="Review")


@app.cell
def _():
    import importlib.util
    import os
    import sys
    from pathlib import Path

    import anywidget
    import marimo as mo
    import traitlets

    root = Path.cwd()
    helpers = str(root / ".astack")
    if helpers not in sys.path:
        sys.path.insert(0, helpers)
    spec = importlib.util.spec_from_file_location("astack_workflow", root / ".astack/workflow.py")
    assert spec is not None and spec.loader is not None
    workflow = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = workflow
    spec.loader.exec_module(workflow)

    class PipelineMap(anywidget.AnyWidget):
        _esm = Path(__file__).with_name("review_map.js")
        nodes = traitlets.List().tag(sync=True)
        edges = traitlets.List().tag(sync=True)
        colors = traitlets.Dict().tag(sync=True)
        picked = traitlets.Unicode("").tag(sync=True)

    class ScrollTop(anywidget.AnyWidget):
        _esm = workflow.REVIEW_SCROLL_ESM
        route = traitlets.Unicode("").tag(sync=True)

    class ReviewActions(anywidget.AnyWidget):
        _esm = workflow.REVIEW_ACTIONS_ESM
        token = traitlets.Unicode("").tag(sync=True)
        view = traitlets.Dict().tag(sync=True)
        event = traitlets.Dict().tag(sync=True)

    return PipelineMap, ReviewActions, ScrollTop, mo, os, root, workflow


@app.cell
def _(mo, os, workflow):
    # Anything that changes what the action bar means voids its current render first.
    action_gate = workflow.ReviewActionGate()
    get_revision, _set_revision = mo.state(0)
    set_revision = action_gate.voiding(_set_revision)
    _first = workflow.review_route(os.environ.get("ASTACK_REVIEW_PAGE", "overview"))
    get_page, _set_page = mo.state(_first, allow_self_loops=True)
    set_page = action_gate.voiding(_set_page)
    # What was on screen when each page was opened: bulk sign-off covers exactly that code.
    get_opened, set_opened = mo.state({})
    get_revoking, _set_revoking = mo.state(None, allow_self_loops=True)
    set_revoking = action_gate.voiding(_set_revoking)
    get_refused, _set_refused = mo.state(None)
    set_refused = action_gate.voiding(_set_refused)
    get_only_pending, set_only_pending = mo.state(True, allow_self_loops=True)
    return (
        action_gate,
        get_only_pending,
        get_opened,
        get_page,
        get_refused,
        get_revision,
        get_revoking,
        set_only_pending,
        set_opened,
        set_page,
        set_refused,
        set_revision,
        set_revoking,
    )


@app.cell
def _(get_revision, root, workflow):
    get_revision()
    status = workflow.review_status(root)
    labels = workflow.review_labels(root)
    return labels, status


@app.cell
def _(mo, root, set_refused, set_revision, status, workflow):
    def refresh():
        set_refused(None)
        set_revision(lambda value: value + 1)

    def sign_off(unit, checksum):
        """The notebook's one sign-off path. A refusal is shown and changes nothing."""
        try:
            workflow.record_review(root, unit, checksum)
            mo.status.toast("Signed off", unit)
        except (RuntimeError, OSError) as error:
            mo.status.toast("Not signed off", str(error), kind="danger")
            set_refused({"unit": unit, "text": str(error)})
            return False
        refresh()
        return True

    def revoke(unit, reason):
        try:
            workflow.revoke_review(root, unit, reason)
        except (RuntimeError, OSError) as error:
            mo.status.toast("Not revoked", str(error), kind="danger")
            return
        mo.status.toast("Sign-off revoked", unit)
        set_revision(lambda value: value + 1)

    next_unit = lambda after=None: workflow.next_to_sign_off(status, after)  # noqa: E731
    return next_unit, refresh, revoke, sign_off


@app.cell
def _(ScrollTop, get_page, mo):
    scroll_top = mo.ui.anywidget(ScrollTop(route=get_page()))
    mo.vstack([scroll_top])
    return (scroll_top,)


@app.cell
def _(mo, next_unit, refresh, set_page, status, workflow):
    _first = next_unit()
    nav_refresh = mo.ui.button(label="↻ Refresh", on_click=lambda _: refresh())
    nav_overview = mo.ui.button(label="Overview", on_click=lambda _: set_page("page:overview"))
    nav_report = mo.ui.button(label="Report", on_click=lambda _: set_page("page:report"))
    nav_start = mo.ui.button(
        label=f"Start reviewing → ({status['to_sign_off']})",
        kind="success",
        disabled=_first is None,
        on_click=lambda _, unit=_first: set_page(f"unit:{unit}") if unit else None,
    )
    _header = status["header"]
    _parts = [
        mo.hstack(
            [
                mo.md(
                    f"**Review** · `{_header['branch']}` at `{_header['commit']}` · "
                    f"**{status['to_sign_off']}** to sign off"
                ),
                mo.hstack([nav_refresh, nav_overview, nav_report, nav_start], justify="end"),
            ],
            justify="space-between",
            align="center",
        ),
        mo.Html(workflow.review_legend_html(status)),
    ]
    if status["problem"]:
        _parts.append(mo.callout(mo.plain_text(status["problem"]), kind="danger"))
    mo.vstack(_parts)
    return nav_overview, nav_refresh, nav_report, nav_start


@app.cell
def _(
    PipelineMap,
    get_only_pending,
    get_opened,
    get_page,
    labels,
    mo,
    root,
    set_only_pending,
    set_page,
    status,
    workflow,
):
    mo.stop(get_page() != "page:overview")
    pipeline_map = mo.ui.anywidget(PipelineMap(**workflow.review_map(status)))
    only_pending = mo.ui.switch(
        value=get_only_pending(),
        label="Only items that need a sign-off",
        on_change=set_only_pending,
    )
    item_rows = workflow.review_overview(
        root, status, labels, opened=frozenset(get_opened()), only_pending=get_only_pending()
    )
    item_links = workflow.review_item_links(mo, status, item_rows, set_page)
    # Drawn here, so ticking a box does not rerun this cell and reset the table.
    picker = workflow.review_items_table(mo, item_rows, item_links)
    workflow.review_overview_view(mo, status, item_rows, pipeline_map, only_pending, picker)
    return item_links, item_rows, only_pending, picker, pipeline_map


@app.cell
def _(get_opened, item_rows, mo, picker, sign_off, status, workflow):
    mo.stop(not item_rows)

    def _sign_selected(_):
        _allowed, _skipped = workflow.review_bulk_plan(status, picker.value or [], get_opened())
        _skipped += [unit for unit, checksum in _allowed if not sign_off(unit, checksum)]
        if _skipped:
            mo.status.toast("Not signed off", ", ".join(_skipped), kind="danger")

    _count = len(picker.value or [])
    sign_selected = mo.ui.button(
        label=f"Sign off selected ({_count})",
        kind="success",
        disabled=not _count,
        on_click=_sign_selected,
    )
    _note = mo.md("_Only items you opened this session can be signed off here._")
    mo.hstack([sign_selected, _note], justify="start")
    return (sign_selected,)


@app.cell
def _(pipeline_map, set_page):
    if pipeline_map.value.get("picked"):
        set_page(f"unit:{pipeline_map.value['picked']}")
    return


@app.cell
def _(get_opened, get_page, labels, mo, root, set_opened, status, workflow):
    _page = get_page()
    _unit = _page.removeprefix("unit:") if _page.startswith("unit:") else ""
    _known = {row["unit"] for row in status["units"]}
    mo.stop(_unit not in _known)
    page = workflow.review_page(root, _unit, status, labels)
    _opened = get_opened()
    _checksum = page["checksum"]
    if _checksum != _opened.get(_unit):
        _updated = {**_opened, _unit: _checksum}
        if _checksum is None:
            del _updated[_unit]
        set_opened(_updated)
    return (page,)


@app.cell
def _(
    ReviewActions,
    action_gate,
    get_refused,
    get_revoking,
    mo,
    next_unit,
    page,
    set_page,
    status,
    workflow,
):
    next_step = next_unit(page["unit"])
    _view = workflow.review_action_view(page, status, next_step, get_revoking(), get_refused())
    action_bar = mo.ui.anywidget(
        ReviewActions(token=action_gate.issue(_view["allowed"]), view=_view)
    )
    _names = [*page["upstream"], *page["downstream"], *page["used_by"]]
    _names += [item["unit"] for item in page["shared"]]
    links = workflow.review_link_buttons(mo, _names, set_page)
    return action_bar, links, next_step


@app.cell
def _(
    action_bar,
    action_gate,
    next_step,
    page,
    refresh,
    revoke,
    set_page,
    set_revoking,
    sign_off,
    workflow,
):
    workflow.review_apply_action(
        action_gate.accept(action_bar.value.get("event")),
        page,
        next_step,
        sign_off,
        revoke,
        set_page,
        set_revoking,
        refresh,
    )
    return


@app.cell
def _(action_bar, links, mo, page, root, workflow):
    page_refs = []
    workflow.review_page_view(mo, root, page, action_bar, links, page_refs)
    return (page_refs,)


@app.cell
def _(get_page, labels, mo, root, status, workflow):
    mo.stop(get_page() != "page:report")
    reports_found = workflow.review_reports(root, status, labels)
    report_picker = workflow.review_report_picker(mo, reports_found)
    _heading = mo.Html(workflow.review_heading_html("Report", 2))
    mo.vstack([_heading, report_picker] if report_picker is not None else [_heading])
    return report_picker, reports_found


@app.cell
def _(mo, report_picker, reports_found, workflow):
    report_index = report_picker.value if report_picker is not None else 0
    claim_page_picker = workflow.review_claim_page_picker(mo, reports_found, report_index)
    mo.vstack([claim_page_picker] if claim_page_picker is not None else [])
    return claim_page_picker, report_index


@app.cell
def _(claim_page_picker, mo, report_index, reports_found, root, set_page, workflow):
    _claim_page = claim_page_picker.value if claim_page_picker is not None else 0
    report_blocks = workflow.review_report_page(reports_found, report_index, _claim_page)
    _units = workflow.review_block_units(report_blocks)
    report_links = workflow.review_link_buttons(mo, _units, set_page, prefix="Open ")
    mo.vstack([workflow.review_draw(mo, block, report_links, root) for block in report_blocks])
    return report_blocks, report_links


if __name__ == "__main__":
    app.run()
