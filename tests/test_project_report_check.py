"""report-check ties report figures and cited numbers to real evidence (issue #270)."""

from __future__ import annotations

import json
import sys

import pytest
from project_tool_test_support import (
    Path,  # noqa: F401
    _project,  # noqa: F401
    no_ci_push_base,  # noqa: F401
    project_tool,  # noqa: F401
    subprocess,  # noqa: F401
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".opencode/skills/ds-report-generation/report-template.md"


def _evidence(root: Path) -> None:
    metrics = root / "evidence/model/metrics.json"
    metrics.parent.mkdir(parents=True, exist_ok=True)
    metrics.write_text(
        '{"auc": 0.8213, "n": 12450, "folds": [{"rmse": 3.14159}], "tiny": 1.234e-5,'
        ' "half": 2.675, "big": 9007199254740992, "a b": 7}',
        encoding="utf-8",
    )
    figure = root / "reports/figures/model/roc.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    figure.write_bytes(b"png")
    (figure.parent / "a(b).png").write_bytes(b"png")
    (figure.parent / "with space.png").write_bytes(b"png")
    (root / "scratch").mkdir(exist_ok=True)
    (root / "scratch/draft.png").write_bytes(b"png")


def _current(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat the fixture evidence as current outputs of a recorded pipeline run."""
    outputs = [
        {"path": path, "stage": "model", "label": "current", "reason": "matches dvc.lock"}
        for path in ("evidence/model", "reports/figures/model")
    ]
    monkeypatch.setattr(
        project_tool, "output_status", lambda root: {"status": "available", "outputs": outputs}
    )


def _report(root: Path, body: str, *, status: str = "draft") -> Path:
    path = root / "reports/study.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: Study\nstatus: {status}\n---\n\n{body}\n", encoding="utf-8")
    return path


def _kinds(result: dict) -> list[str]:
    return [problem["kind"] for report in result["reports"] for problem in report["problems"]]


@pytest.mark.parametrize(
    "body",
    [
        "AUC was [0.82](../evidence/model/metrics.json#/auc).",
        "AUC was [0.8213](../evidence/model/metrics.json#/auc).",
        "We used [12,450](../evidence/model/metrics.json#/n) records.",
        "RMSE was [3.1](../evidence/model/metrics.json#/folds/0/rmse).",
        "Tiny was [1.23e-5](../evidence/model/metrics.json#/tiny).",
        "See [the metrics](../evidence/model/metrics.json) and ![ROC](figures/model/roc.png).",
        "Total [12450](../evidence/model/metrics.json) without a pointer only needs the file.",
        "Code like `[1](missing.json)` and comments <!-- [2](missing.json) --> are ignored.",
        "Half-up rounding: [2.68](../evidence/model/metrics.json#/half).",
        "Exact integers: [9007199254740992](../evidence/model/metrics.json#/big).",
        "Encoded pointer: [7](../evidence/model/metrics.json#/a%20b).",
        "Query ignored: [0.82](../evidence/model/metrics.json?raw=1#/auc).",
        "![Parens](figures/model/a(b).png) and ![Space](<figures/model/with space.png>).",
        "![Encoded](figures/model/with%20space.png 'single-quoted title')",
        "![ROC][roc] and [0.82][auc] and [roc]\n\n[roc]: figures/model/roc.png\n"
        "[auc]: ../evidence/model/metrics.json#/auc",
        "[![ROC](figures/model/roc.png)](figures/model/roc.png)",
        "````\n```\n[1](missing.json)\n````",
    ],
)
def test_traced_values_and_figures_pass(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body)

    result = project_tool.report_check(root, [])

    assert result["status"] == "passed", result
    assert _kinds(result) == []


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("AUC was [0.83](../evidence/model/metrics.json#/auc).", "value-mismatch"),
        ("AUC was [0.82](../evidence/model/metrics.json#/missing).", "missing-value"),
        ("AUC was [0.82](../evidence/model/other.json#/auc).", "missing-file"),
        ("![ROC](figures/model/absent.png)", "missing-file"),
        ("![Draft](../scratch/draft.png)", "not-evidence"),
        ("AUC was [0.82](../data/processed/x.json).", "not-evidence"),
        ("AUC was [0.82](../../outside.json).", "outside-project"),
        ("Read [the notes](../docs/absent.md).", "missing-file"),
        ("Table [5](figures/model/roc.png#/x).", "unsupported-pointer"),
        ("![plot](https://example.org/missing.png)", "not-evidence"),
        ("Rate [5%](https://example.org/rate.json)", "not-evidence"),
        ("![inline](data:image/png;base64,AAAA)", "not-evidence"),
        ("![anchor](#figure-1)", "not-evidence"),
        ("![plot][p]\n\n[p]: figures/model/absent.png", "missing-file"),
        ("![plot](figures/model/absent.png 'title')", "missing-file"),
        ("Exact: [9007199254740993](../evidence/model/metrics.json#/big)", "value-mismatch"),
    ],
)
def test_untraced_values_and_figures_fail(tmp_path: Path, body: str, kind: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body)

    result = project_tool.report_check(root, [])

    assert result["status"] == "failed"
    assert _kinds(result) == [kind]
    assert result["reports"][0]["problems"][0]["line"] == 6


def test_numbers_without_a_mantissa_are_plain_links(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, "See [e3](../evidence/model/metrics.json#/auc).")

    result = project_tool.report_check(root, [])

    assert result["status"] == "passed"
    assert result["reports"][0]["cited_values"] == 0


def test_comment_opener_inside_code_does_not_hide_a_marker(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(
        root,
        "```html\n<!-- example\n```\n\n[UNRESOLVED: real gap]\n\n<!-- later comment -->",
        status="final",
    )

    result = project_tool.report_check(root, [])

    assert _kinds(result) == ["unresolved"]
    assert result["reports"][0]["problems"][0]["line"] == 10


@pytest.mark.parametrize(
    "front_matter", ['"status": final', "status: 'final'", "status: FINAL  # done"]
)
def test_any_yaml_spelling_of_final_is_final(tmp_path: Path, front_matter: str) -> None:
    root = _project(tmp_path)
    report = root / "reports/study.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"---\n{front_matter}\n---\n\n[UNRESOLVED: x]\n", encoding="utf-8")

    result = project_tool.report_check(root, [])

    assert result["reports"][0]["final"] is True
    assert _kinds(result) == ["unresolved"]


def test_unreadable_front_matter_fails_closed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = root / "reports/study.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("---\nstatus: [final\n---\n\nText.\n", encoding="utf-8")

    result = project_tool.report_check(root, [])

    assert _kinds(result) == ["invalid-front-matter"]


def test_symlink_out_of_the_evidence_folders_is_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    (root / "evidence/model/escape.png").symlink_to(root / "scratch/draft.png")
    _report(root, "![Escape](../evidence/model/escape.png)")

    result = project_tool.report_check(root, [])

    assert _kinds(result) == ["not-evidence"]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("Before `x` ![plot](https://example.org/missing.png)", "not-evidence"),
        ("\\<!--\n![plot](https://example.org/missing.png)", "not-evidence"),
        ("- ````\n  ```\n  ````\n\n![plot](https://example.org/missing.png)", "not-evidence"),
        ("![plot][p]\n\n[p]:\n  https://example.org/missing.png", "not-evidence"),
        ('![plot](https://example.org/missing.png "say \\"hello\\"")', "not-evidence"),
        ('<img src="https://example.org/missing.png">', "unsupported-html"),
        ('Inline <img src="figures/model/absent.png"> figure.', "unsupported-html"),
        ("<picture><source srcset='a.png'></picture>", "unsupported-html"),
        ("[0&#46;99](../evidence/model/metrics.json#/auc)", "value-mismatch"),
    ],
)
def test_round_two_bypasses_are_caught(tmp_path: Path, body: str, kind: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body)

    result = project_tool.report_check(root, [])

    assert kind in _kinds(result), result


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("[`0.99`](../evidence/model/metrics.json#/auc)", "value-mismatch"),
        ('<a href="../evidence/model/metrics.json#/auc">0.99</a>', "unsupported-html"),
        ('Inline <a href="../evidence/model/metrics.json#/auc">0.99</a> link.', "unsupported-html"),
        (
            '<a href="https://example.com">0.99<a href="https://example.org">notes</a>',
            "unsupported-html",
        ),
        ('<div><a href="https://example.com">\n\n0.99</a></div>', "unsupported-html"),
        ('<img src="figures/model/absent.png" src="figures/model/roc.png">', "unsupported-html"),
    ],
)
def test_round_three_bypasses_are_caught(tmp_path: Path, body: str, kind: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body)

    assert kind in _kinds(project_tool.report_check(root, []))


@pytest.mark.parametrize(
    "body",
    [
        "<div>\n[UNRESOLVED: missing data]\n</div>",
        "[UNRESOLVED: missing data](https://example.com)",
        "<div>[UN<strong>RESOLVED</strong>: missing data]</div>",
        "<div>[UN<!-- split -->RESOLVED: missing data]</div>",
        "Inline [UN<em></em>RESOLVED: missing data] text.",
    ],
)
def test_markers_in_html_and_link_text_block_a_final_report(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _report(root, body, status="final")

    kinds = _kinds(project_tool.report_check(root, []))
    assert "unresolved" in kinds or "unsupported-html" in kinds


@pytest.mark.parametrize(
    "body",
    [
        '<input type="image" src="https://example.org/unverified.png">',
        "<div style=\"background-image: url('https://example.org/x.png')\">x</div>",
        '<base href="https://example.org/">\n\n![plot](figures/model/roc.png)',
        "[0.83<span hidden>note</span>](../README.md#/auc)",
        "[UNRE<span hidden>note</span>SOLVED: missing]",
        '<img src="figures/model/roc.png" onerror="x()">',
        "<link rel=stylesheet href=s.css>",
    ],
)
def test_round_five_html_is_rejected(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body, status="final")

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


@pytest.mark.parametrize(
    "body",
    [
        '<!--><img src="https://example.org/a.png">-->',
        '<!---><img src="https://example.org/a.png">-->',
        "<![CDATA[>[UNRESOLVED: gap]]]>",
        "<!DOCTYPE html>",
        "<?php echo 1; ?>",
    ],
)
def test_comments_and_declarations_browsers_read_differently_are_rejected(
    tmp_path: Path, body: str
) -> None:
    root = _project(tmp_path)
    _report(root, body, status="final")

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


def test_numeric_link_with_an_empty_segment_is_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, "[0.82](../evidence/model//../metrics.json#/auc)")

    assert _kinds(project_tool.report_check(root, [])) == ["unsupported-path"]


def test_parent_step_after_a_folder_name_is_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    (root / "evidence/linked").symlink_to(root / "reports/figures/model")
    # A browser reads evidence/linked/../model/metrics.json as evidence/model/metrics.json,
    # but the filesystem follows the symlink first. A `..` after a folder name is rejected.
    _report(root, "[0.82](../evidence/linked/../model/metrics.json#/auc)")

    assert _kinds(project_tool.report_check(root, [])) == ["unsupported-path"]


@pytest.mark.parametrize(
    "body",
    ["<div>[UN<!-->RESOLVED: gap]</div>", "<div>[UN<!----!>RESOLVED: gap]</div>"],
)
def test_unclosed_comments_cannot_hide_a_marker(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _report(root, body, status="final")

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


def test_inline_short_comment_does_not_hide_a_following_image(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(root, 'Text <!--> <img src="https://example.org/a.png"> -->')

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


@pytest.mark.parametrize(
    "label",
    ["**0.83**", "0.<!-- note -->83", "`0.83`"],
)
def test_formatting_inside_a_link_label_keeps_the_number_checked(
    tmp_path: Path, label: str
) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, f"[{label}](../evidence/model/metrics.json#/auc)")

    result = project_tool.report_check(root, [])

    assert _kinds(result) == ["value-mismatch"]
    assert result["reports"][0]["cited_values"] == 1


def test_marker_after_a_multiline_code_span_is_on_its_own_line(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(root, "`a\nb` [UNRESOLVED: gap]\n\nText [UNRESOLVED: two] `x`")

    assert project_tool.report_check(root, [])["reports"][0]["unresolved_lines"] == [7, 9]


@pytest.mark.parametrize(
    "destination",
    [
        "figures\\model\\roc.png",
        "figures/model/..%5C..%5Cscratch%5Cunchecked.png",
        "figures/model/roc%09.png",
        "figures/model/roc.png%20",
        "figures/model//../roc.png",
        "figures%2Fmodel%2Froc.png",
        "figures/model/..%2F..%2Fscratch/draft.png",
    ],
)
def test_figure_paths_browsers_rewrite_are_rejected(tmp_path: Path, destination: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, f"![x](<{destination}>)")

    assert _kinds(project_tool.report_check(root, [])) == ["unsupported-path"]


@pytest.mark.parametrize(
    "image",
    ["![0.83](figures/model/roc.png)", "![**0.83**](figures/model/roc.png)"],
)
def test_numeric_alt_text_in_a_link_is_value_checked(tmp_path: Path, image: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, f"[{image}](../evidence/model/metrics.json#/auc)")

    result = project_tool.report_check(root, [])

    assert _kinds(result) == ["value-mismatch"]


@pytest.mark.parametrize(
    "image",
    [
        "![[UNRESOLVED: gap]](figures/model/roc.png)",
        "![[UN*RESOLVED*: gap]](figures/model/roc.png)",
    ],
)
def test_marker_in_alt_text_blocks_a_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, image: str
) -> None:
    _current(monkeypatch)
    root = _project(tmp_path)
    _evidence(root)
    _report(root, image, status="final")

    assert _kinds(project_tool.report_check(root, [])) == ["unresolved"]


def test_rendered_alt_text_is_value_checked(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, "[![**0.83**](figures/model/roc.png)](../evidence/model/metrics.json#/auc)")

    assert _kinds(project_tool.report_check(root, [])) == ["value-mismatch"]


@pytest.mark.parametrize(
    "body",
    [
        "![[UN**RESOLVED**: gap]](figures/model/roc.png)",
        "Text [UN![RESOLVED: gap](figures/model/roc.png)]",
    ],
)
def test_markers_through_alt_text_block_a_final_report(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body, status="final")

    assert "unresolved" in _kinds(project_tool.report_check(root, []))


@pytest.mark.parametrize(
    "body",
    [
        "[0.83</a>notes](../evidence/model/metrics.json#/auc)",
        "[0.83<p>notes</p>](../evidence/model/metrics.json#/auc)",
        "[0.83<div>notes</div>](../evidence/model/metrics.json#/auc)",
        "Text </iframe> here.",
        *(
            f"<{tag}>[0.83</{tag}>notes](../evidence/model/metrics.json#/auc)"
            for tag in ["b", "em", "i", "small", "span", "strong", "sub", "sup", "u"]
        ),
        "| a |\n|---|\n| <b>[0.83</b>notes](../evidence/model/metrics.json#/auc) |",
    ],
)
def test_html_that_can_split_a_link_is_rejected(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body, status="final")

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


@pytest.mark.parametrize(
    "tag",
    [
        '<img src="figures/model/a&notit;.png">',
        '<img src="figures/model/roc.png" alt="&#48;.83">',
        '<img src="figures/model/a&#38;b.png">',
    ],
)
def test_ambiguous_character_references_in_img_are_rejected(tmp_path: Path, tag: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, tag)

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


def test_balanced_formatting_inside_a_link_is_fine(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, "[**0.82**](../evidence/model/metrics.json#/auc) in m² <!-- note -->")

    assert project_tool.report_check(root, [])["status"] == "passed"


def test_every_marker_line_is_reported(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(root, "[UNRESOLVED: a]\nm²\n[UNRESOLVED: b] and [UNRESOLVED: c]")

    assert project_tool.report_check(root, [])["reports"][0]["unresolved_lines"] == [6, 8]


@pytest.mark.parametrize(
    "body",
    ["m<sup>2</sup>", "Line<br>break", "<div>\nA note.\n</div>", "<span>x</span>", "</p>"],
)
def test_any_raw_html_tag_fails(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _report(root, body)

    assert "unsupported-html" in _kinds(project_tool.report_check(root, []))


@pytest.mark.parametrize(
    "body",
    [
        '![plot](figures/model/roc.png){srcset="https://example.org/unchecked.png 1x"}',
        '![plot](figures/model/roc.png){fig-alt="[UNRESOLVED: gap]"}',
        "[[0.99]{.number}](../evidence/model/metrics.json#/auc)",
        "[UN[RESOLVED]{.gap}: missing data]",
        "{{< include other.md >}}",
        "![plot](figures/model/roc.png){width=50%",
        '`<img src="https://example.org/unchecked.png">`{=html}',
        "[UN`RESOLVED`{=html}: gap]",
        '```{=html}\n<img src="https://example.org/unchecked.png">\n```',
        "{{% include other.md %}}",
        "`x`{.python\n=html}",
        '``` { =html }\n<img src="https://example.org/unchecked.png">\n```',
        "~~~{=latex}\n\\input{other.tex}\n~~~",
    ],
)
def test_pandoc_syntax_that_changes_what_is_shown_fails(tmp_path: Path, body: str) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body, status="final")

    assert "unsupported-syntax" in _kinds(project_tool.report_check(root, []))


def test_safe_pandoc_image_attributes_pass(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, '![ROC](figures/model/roc.png){#fig-roc .wide width="50%" height=300}')

    assert project_tool.report_check(root, [])["status"] == "passed"


@pytest.mark.parametrize(
    "body",
    [
        "[x]\\{.class}",
        "![ROC](figures/model/roc.png)\\{srcset=x}",
        "\\{{< include other.md >}}",
        "`fit()`&#123;=html}",
        "[x]&#x7B;.class}",
        "Code `[x]{.a}` and `{{< x >}}` stay code.",
        "```\n[x]{.a} {{< include other.md >}}\n```",
        "Call `fit()`{.python} first.",
        "Ratio $\\frac{a}{b}$ holds.",
        "## Results {#sec-results}",
        "Call `fit()`{.python\n#fit} first.",
        '![ROC](figures/model/roc.png){#fig-roc\nwidth="50%"}',
    ],
)
def test_literal_or_harmless_braces_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _current(monkeypatch)
    root = _project(tmp_path)
    _evidence(root)
    _report(root, body, status="final")

    assert project_tool.report_check(root, [])["status"] == "passed"


def test_html_comments_are_allowed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(root, "Area in m². <!-- inline note -->\n\n<!--\nA block note.\n-->")

    assert project_tool.report_check(root, [])["status"] == "passed"


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("Code `spans\ntwo lines` then ![x](figures/model/absent.png)", 7),
        ("Text *one\ntwo* then\n![x](figures/model/absent.png)", 8),
        ("\\` earlier `multi\nline` ![x](figures/model/absent.png)", 7),
    ],
)
def test_problem_lines_follow_multiline_tokens(tmp_path: Path, body: str, line: int) -> None:
    root = _project(tmp_path)
    _evidence(root)
    (root / "reports/figures/model/x.png").write_bytes(b"png")
    _report(root, body)

    problems = project_tool.report_check(root, [])["reports"][0]["problems"]

    assert [(p["kind"], p["line"]) for p in problems] == [("missing-file", line)]


def test_character_references_in_destinations_are_decoded(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    (root / "reports/figures/model/a&b.png").write_bytes(b"png")
    _report(root, "![amp](figures/model/a&amp;b.png) and ![num](figures/model/a&#38;b.png)")

    result = project_tool.report_check(root, [])

    assert result["status"] == "passed", result
    assert result["reports"][0]["figures"] == 2


def test_code_spans_across_lines_are_code(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(root, "See `code that\n[1](missing.json)` here.")

    assert project_tool.report_check(root, [])["status"] == "passed"


def test_large_bracket_heavy_report_is_fast(tmp_path: Path) -> None:
    import time

    root = _project(tmp_path)
    _report(root, "[" * 8000 + "\n" + "[a](figures/model/x.png) " * 2000)
    started = time.perf_counter()
    project_tool.report_check(root, [])
    assert time.perf_counter() - started < 5


def test_unterminated_front_matter_fails_closed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = root / "reports/study.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("---\nstatus: draft\n\nText.\n", encoding="utf-8")

    assert "invalid-front-matter" in _kinds(project_tool.report_check(root, []))


def test_missing_parser_is_unavailable_not_passed(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    _report(root, "Text.")
    monkeypatch.setattr(project_tool, "markdown_it", None)

    result = project_tool.report_check(root, [])

    assert result["status"] == "unavailable"
    assert "markdown-it-py" in result["summary"]


def test_unresolved_marker_blocks_only_a_final_report(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _report(root, "[UNRESOLVED: the sample size]")
    draft = project_tool.report_check(root, [])
    assert draft["status"] == "passed"
    assert draft["reports"][0]["unresolved_lines"] == [6]

    _report(root, "[UNRESOLVED: the sample size]", status="final")
    final = project_tool.report_check(root, [])
    assert final["status"] == "failed"
    assert _kinds(final) == ["unresolved"]


def test_front_matter_is_not_scanned_and_line_numbers_stay_true(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = root / "reports/study.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        '---\nstatus: final\nnote: "[UNRESOLVED: in front matter]"\n---\n\nText.\n'
        "[0.5](../evidence/absent.json)\n",
        encoding="utf-8",
    )

    result = project_tool.report_check(root, [])

    assert [(p["line"], p["kind"]) for p in result["reports"][0]["problems"]] == [
        (7, "missing-file")
    ]


def test_no_reports_is_reported_not_passed(tmp_path: Path) -> None:
    root = _project(tmp_path)

    result = project_tool.report_check(root, [])

    assert result["status"] == "no-reports"


def test_named_missing_report_fails(tmp_path: Path) -> None:
    root = _project(tmp_path)

    result = project_tool.report_check(root, [Path("reports/absent.md")])

    assert result["status"] == "failed"
    assert _kinds(result) == ["missing-report"]


def test_default_scan_skips_figure_and_table_folders(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, "Fine.")
    (root / "reports/tables/model").mkdir(parents=True, exist_ok=True)
    (root / "reports/tables/model/notes.md").write_text("[1](missing.json)\n", encoding="utf-8")

    result = project_tool.report_check(root, [])

    assert [report["path"] for report in result["reports"]] == ["reports/study.md"]


def test_template_is_a_valid_draft_and_blocked_as_final(tmp_path: Path) -> None:
    root = _project(tmp_path)
    shipped = root / ".opencode/skills/ds-report-generation/report-template.md"
    assert shipped.read_bytes() == TEMPLATE.read_bytes()
    report = root / "reports/study.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(TEMPLATE.read_bytes())

    assert project_tool.report_check(root, [])["status"] == "passed"

    report.write_text(
        TEMPLATE.read_text(encoding="utf-8").replace("status: draft", "status: final"),
        encoding="utf-8",
    )
    final = project_tool.report_check(root, [])
    assert final["status"] == "failed"
    assert set(_kinds(final)) == {"unresolved"}


def test_report_check_runs_from_the_cli_and_fails_with_exit_1(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _evidence(root)
    _report(root, "AUC was [0.83](../evidence/model/metrics.json#/auc).")

    completed = subprocess.run(
        [sys.executable, ".astack/workflow.py", "report-check"],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["reports"][0]["problems"][0]["kind"] == "value-mismatch"
    assert "report-check" in (root / "justfile").read_text(encoding="utf-8")
