"""Output previews and the Report page: they only read, and never pass off a failure as empty."""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import sys
import types
import zlib
from typing import Any

import pytest
from project_tool_test_support import Path, _git_baseline, _project, project_tool

HAS_PYARROW = importlib.util.find_spec("pyarrow") is not None


def _write(root: Path, relative: str, text: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require POSIX")
def test_preview_rejects_named_pipes_before_using_a_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.mkfifo(tmp_path / "result.txt")

    def unexpected_reader(path: Path) -> Any:
        pytest.fail(f"Preview tried to read {path}")

    monkeypatch.setattr(project_tool, "_text_preview", unexpected_reader)
    assert project_tool.review_preview(tmp_path, "result.txt") == {
        "kind": "problem",
        "text": "Cannot preview a special file.",
    }


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow is not installed")
def test_a_csv_keeps_its_types(tmp_path: Path) -> None:
    relative = _write(tmp_path, "evidence/a.csv", "site,rate\nx,1.5\ny,2.5\n")
    shown = project_tool.review_preview(tmp_path, relative)
    assert shown["kind"] == "data"
    assert shown["table"].schema.field("rate").type == "double"
    assert shown["note"] == "2 rows"


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow is not installed")
def test_a_long_table_says_it_was_cut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    monkeypatch.setattr(project_tool, "REVIEW_PREVIEW_ROWS", 5)
    path = tmp_path / "evidence/a.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"x": list(range(20))}), path)
    shown = project_tool.review_preview(tmp_path, "evidence/a.parquet")
    assert shown["table"].num_rows == 5
    assert shown["note"] == "First 5 of 20 rows"
    relative = _write(tmp_path, "evidence/b.csv", "x\n" + "1\n" * 20)
    shown = project_tool.review_preview(tmp_path, relative)
    assert shown["table"].num_rows == 5
    assert shown["note"].startswith("First 5 rows")


def test_a_missing_reader_shows_file_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = __import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] in {"pyarrow", "rasterio", "xarray"}:
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    for name, reader in (("a.parquet", "pyarrow"), ("a.tif", "rasterio"), ("a.nc", "xarray")):
        relative = _write(tmp_path, f"evidence/{name}", "not really")
        shown = project_tool.review_preview(tmp_path, relative)
        assert shown["kind"] == "facts"
        assert reader in shown["text"]
    relative = _write(tmp_path, "evidence/a.csv", "x\n1\n")
    shown = project_tool.review_preview(tmp_path, relative)
    assert shown["table"] == [{"x": "1"}]
    assert "read as text" in shown["note"]


def test_a_corrupt_file_is_a_problem_not_an_empty_table(tmp_path: Path) -> None:
    relative = _write(tmp_path, "evidence/a.parquet", "not parquet")
    shown = project_tool.review_preview(tmp_path, relative)
    assert shown["kind"] in {"problem", "facts"}


def test_a_link_out_of_the_project_is_never_read(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    (root / "out.txt").symlink_to(secret)
    shown = project_tool.review_preview(root, "out.txt")
    assert shown["kind"] == "problem"
    assert "private" not in json.dumps(shown)


def test_missing_output_and_long_text_say_so(tmp_path: Path, monkeypatch) -> None:
    assert project_tool.review_preview(tmp_path, "evidence/none.csv")["text"] == "Not produced yet."
    monkeypatch.setattr(project_tool, "REVIEW_PREVIEW_TEXT_BYTES", 4)
    relative = _write(tmp_path, "evidence/a.txt", "abcdefgh")
    assert project_tool.review_preview(tmp_path, relative)["text"] == "abcd\n[Cut short]"


def test_the_thumbnail_is_a_valid_png() -> None:
    data = project_tool._png([bytes([0, 255]), bytes([128, 64])], 2, 2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    length = struct.unpack(">I", data[33:37])[0]
    assert zlib.decompress(data[41 : 41 + length]) == b"\x00\x00\xff\x00\x80\x40"


def _report_status() -> dict[str, Any]:
    item = {"outputs": ["evidence/model/metrics.json"]}
    return {"units": [{"unit": "train", "items": [item]}]}


def _report_project(tmp_path: Path) -> Path:
    target = _project(tmp_path)
    _write(target, "evidence/model/metrics.json", json.dumps({"auc": 0.83}))
    _write(target, "evidence/model/plot.png", "x")
    _write(
        target,
        "reports/study.md",
        "---\nstatus: draft\n---\n\n# Study\n\n"
        "Accuracy was [0.83](../evidence/model/metrics.json#/auc) overall.\n\n"
        "![Map <b>x</b>](../evidence/model/plot.png)\n\n"
        "See [the docs](https://example.com).\n",
    )
    _git_baseline(target)
    return target


def test_a_report_lists_each_claim_with_its_step_and_live_label(tmp_path: Path) -> None:
    target = _report_project(tmp_path)
    labels = {"evidence/model/metrics.json": "outdated"}
    report = project_tool.review_reports(target, _report_status(), labels)["reports"][0]
    number, figure = report["claims"]
    assert (number["text"], number["label"], number["unit"]) == ("0.83", "outdated", "train")
    assert "Accuracy was" in number["context"]
    assert (figure["kind"], figure["label"], figure["unit"]) == ("figure", "unknown", None)
    assert len(report["claims"]) == 2


def test_the_report_page_escapes_report_text_and_never_writes(tmp_path: Path) -> None:
    target = _report_project(tmp_path)
    before = (target / "reports/study.md").read_bytes()
    blocks = project_tool.review_report_sections(target, _report_status(), {})
    shown = " ".join(str(block.get("html", "")) for block in blocks)
    assert "<b>x</b>" not in shown
    assert project_tool.review_block_units(blocks) == ["train"]
    assert (target / "reports/study.md").read_bytes() == before


@pytest.mark.skipif(importlib.util.find_spec("marimo") is None, reason="marimo is not installed")
def test_every_block_type_draws_in_marimo(tmp_path: Path) -> None:
    import marimo as mo

    for name, text in (("a.csv", "x\n1\n"), ("a.json", "{}"), ("a.txt", "t"), ("a.bin", "b")):
        _write(tmp_path, f"evidence/{name}", text)
    outputs = [
        {"item": None, "path": f"evidence/{name}", "label": label}
        for name, label in (("a.csv", "current"), ("a.json", "outdated"), ("a.txt", "current"))
    ]
    for output in [*outputs, {"item": None, "path": "evidence/a.bin", "label": "current"}]:
        assert project_tool._draw_preview(mo, tmp_path, output["path"]) is not None
    blocks = [
        {"type": "html", "html": "<p>x</p>"},
        {"type": "text", "text": "x"},
        {"type": "callout", "kind": "warn", "text": "x"},
        {"type": "fold", "title": "t", "html": "<p>x</p>"},
        {"type": "table", "rows": [{"a": 1}]},
        {"type": "outputs", "outputs": outputs},
        {"type": "output_groups", "groups": [{"title": "north", "outputs": outputs}]},
        {"type": "claim", "html": "<p>x</p>", "units": []},
        {"type": "links", "title": "Upstream", "units": []},
    ]
    for block in blocks:
        assert project_tool.review_draw(mo, block, {}, tmp_path) is not None
    refs: list[Any] = []
    project_tool.review_draw(mo, {"type": "outputs", "outputs": outputs}, {}, tmp_path, keep=refs)
    assert len(refs) == len(outputs)
    project_tool._draw_preview(mo, tmp_path, "evidence/a.csv", keep=refs)
    assert len(refs) >= len(outputs) + 1


def test_a_report_uses_output_status_for_directory_and_alias_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _project(tmp_path)
    _write(target, "evidence/plots/map.png", "x")
    _write(target, "evidence/real/map.png", "x")
    _write(
        target,
        "reports/study.md",
        "---\nstatus: draft\n---\n\n"
        "![Direct](../evidence/plots/map.png)\n\n"
        "![Alias](../evidence/real/map.png)\n",
    )
    _git_baseline(target)
    state = {
        "status": "current",
        "outputs": [
            {
                "path": "evidence/plots",
                "label": "current",
                "files": ["evidence/plots/map.png"],
                "aliases": {"evidence/real/map.png": "evidence/plots/map.png"},
            }
        ],
    }
    monkeypatch.setattr(project_tool, "output_status", lambda _root: state)
    status = {"units": [{"unit": "plot", "items": [{"outputs": ["evidence/plots"]}]}]}
    report = project_tool.review_reports(target, status, {})["reports"][0]
    assert [(claim["label"], claim["unit"]) for claim in report["claims"]] == [
        ("current", "plot"),
        ("current", "plot"),
    ]


def test_a_masked_raster_excludes_finite_nodata_from_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Arr:
        def __init__(self, rows: list[list[float]], mask: list[list[bool]] | None = None) -> None:
            self.rows = rows
            self.mask = mask
            self.shape = (len(rows), len(rows[0]))
            self.size = sum(len(row) for row in rows)

        def __getitem__(self, mask_arr: _Arr) -> _Arr:
            flat = [
                v
                for row, mrow in zip(self.rows, mask_arr.rows, strict=True)
                for v, keep in zip(row, mrow, strict=True)
                if keep
            ]
            out = _Arr([flat])
            out.size = len(flat)
            return out

        def min(self) -> float:
            return min(v for row in self.rows for v in row)

        def max(self) -> float:
            return max(v for row in self.rows for v in row)

        def __sub__(self, other: float) -> _Arr:
            return _Arr([[v - other for v in row] for row in self.rows])

        def __truediv__(self, other: float) -> _Arr:
            return _Arr([[v / other for v in row] for row in self.rows])

        def __mul__(self, other: float) -> _Arr:
            return _Arr([[v * other for v in row] for row in self.rows])

        def astype(self, _dtype: str) -> _Arr:
            return _Arr([[int(v) for v in row] for row in self.rows])

        def __iter__(self):
            for row in self.rows:
                yield types.SimpleNamespace(tobytes=lambda r=row: bytes(int(v) for v in r))

    class _MA:
        @staticmethod
        def asarray(values: _Arr, dtype: str = "float64") -> _Arr:
            return _Arr(values.rows, values.mask)

        @staticmethod
        def masked_invalid(arr: _Arr) -> _Arr:
            mask = [
                [(arr.mask[r][c] if arr.mask else False) or (v != v) for c, v in enumerate(row)]
                for r, row in enumerate(arr.rows)
            ]
            return _Arr(arr.rows, mask)

        @staticmethod
        def filled(arr: _Arr, fill: float) -> _Arr:
            rows = [
                [fill if (arr.mask and arr.mask[r][c]) else v for c, v in enumerate(row)]
                for r, row in enumerate(arr.rows)
            ]
            return _Arr(rows)

    fake_np = types.ModuleType("numpy")
    fake_np.nan = float("nan")  # type: ignore[attr-defined]
    fake_np.ma = _MA  # type: ignore[attr-defined]
    # Plain asarray drops the mask, matching real NumPy.
    fake_np.asarray = lambda values, dtype="float64": _Arr(values.rows, None)  # type: ignore[attr-defined]
    fake_np.isfinite = lambda arr: _Arr([[v == v for v in row] for row in arr.rows])  # type: ignore[attr-defined]
    fake_np.nan_to_num = lambda arr, nan=0.0: _Arr(  # type: ignore[attr-defined]
        [[nan if v != v else v for v in row] for row in arr.rows]
    )
    monkeypatch.setitem(sys.modules, "numpy", fake_np)
    band = _Arr([[-9999.0, 2.0], [4.0, 6.0]], [[True, False], [False, False]])
    png, value_range = project_tool._thumbnail(band)
    assert png is not None and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert value_range == "2 to 6"


@pytest.mark.skipif(importlib.util.find_spec("marimo") is None, reason="marimo is not installed")
def test_scalar_json_renders_as_plain_text(tmp_path: Path) -> None:
    import marimo as mo

    for name, body in (
        ("n.json", "1"),
        ("null.json", "null"),
        ("s.json", '"sample"'),
        ("b.json", '"true"'),
    ):
        relative = _write(tmp_path, f"evidence/{name}", body)
        shown = project_tool.review_preview(tmp_path, relative)
        assert shown == {"kind": "text", "text": body}
        assert project_tool._draw_preview(mo, tmp_path, relative) is not None


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow is not installed")
def test_a_csv_with_late_text_ids_falls_back_to_text_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_tool, "REVIEW_PREVIEW_ROWS", 130_000)
    relative = _write(tmp_path, "evidence/ids.csv", "id\n" + "10000000\n" * 120_000 + "abc\n")
    shown = project_tool.review_preview(tmp_path, relative)
    assert shown["kind"] == "data"
    assert isinstance(shown["table"], list)
    assert shown["table"][-1] == {"id": "abc"}
    assert "read as text" in shown["note"]


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow is not installed")
def test_a_malformed_csv_row_is_a_problem_not_a_table_crash(tmp_path: Path) -> None:
    for name, body in (
        ("bad.csv", "a,b\n1,2,3\n"),
        ("spoof.csv", "a,b\n1,2,CSV conversion error\n"),
    ):
        relative = _write(tmp_path, f"evidence/{name}", body)
        shown = project_tool.review_preview(tmp_path, relative)
        assert shown["kind"] == "problem"
        assert "CSV parse error" in shown["text"]


def test_netcdf_thumbnail_strides_via_isel_before_loading_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    class _Var:
        def __init__(self, shape: tuple[int, ...], dims: tuple[str, ...], strided: bool = False):
            self.name = "temp"
            self.shape = shape
            self.dims = dims
            self.ndim = len(shape)
            self.size = 1
            self.dtype = "float32"
            self._strided = strided

        def isel(self, indexers: dict[str, Any]) -> _Var:
            calls.append(indexers)
            if any(isinstance(v, slice) for v in indexers.values()):
                return _Var((240, 240), ("y", "x"), strided=True)
            return _Var((480, 480), ("y", "x"), strided=False)

        @property
        def values(self) -> list[list[float]]:
            if not self._strided:
                raise AssertionError("full slice loaded before striding")
            return [[0.0, 1.0]]

    class _Dataset:
        sizes = {"t": 2, "y": 480, "x": 480}
        data_vars = {"temp": _Var((2, 480, 480), ("t", "y", "x"))}

        def __enter__(self) -> _Dataset:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    fake_xr = types.ModuleType("xarray")
    fake_xr.open_dataset = lambda path, decode_times=False: _Dataset()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xarray", fake_xr)
    monkeypatch.setattr(project_tool, "_thumbnail", lambda _values: (b"\x89PNG", "0 to 1"))
    relative = _write(tmp_path, "evidence/grid.nc", "placeholder")
    shown = project_tool.review_preview(tmp_path, relative)
    assert shown["kind"] == "raster"
    assert {"y": slice(None, None, 2), "x": slice(None, None, 2)} in calls
    assert any("1/2 scale" in line for line in shown["facts"])


@pytest.mark.skipif(
    not HAS_PYARROW or importlib.util.find_spec("marimo") is None,
    reason="pyarrow and marimo are required",
)
def test_a_parquet_with_list_column_charts_only_scalar_columns(tmp_path: Path) -> None:
    import marimo as mo
    import pyarrow as pa
    import pyarrow.parquet as pq

    mixed = tmp_path / "evidence/mixed.parquet"
    mixed.parent.mkdir(parents=True)
    pq.write_table(pa.table({"x": [1, 2], "tags": [["a"], ["b"]]}), mixed)
    shown = project_tool.review_preview(tmp_path, "evidence/mixed.parquet")
    assert shown["table"].column_names == ["x", "tags"]
    chartable = project_tool._chartable_table(shown["table"])
    assert chartable is not None and chartable.column_names == ["x"]
    assert mo.ui.data_explorer(chartable) is not None
    assert project_tool._draw_preview(mo, tmp_path, "evidence/mixed.parquet") is not None

    nested_only = tmp_path / "evidence/nested.parquet"
    pq.write_table(pa.table({"tags": [["a"], ["b"]]}), nested_only)
    shown_nested = project_tool.review_preview(tmp_path, "evidence/nested.parquet")
    assert project_tool._chartable_table(shown_nested["table"]) is None


def test_an_unreadable_report_does_not_abort_other_reports(tmp_path: Path) -> None:
    target = _project(tmp_path)
    bad = target / "reports/a_bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe")
    _write(target, "evidence/model/metrics.json", json.dumps({"auc": 0.83}))
    _write(
        target,
        "reports/b_good.md",
        "---\nstatus: draft\n---\n\nAccuracy was [0.83](../evidence/model/metrics.json#/auc).\n",
    )
    _git_baseline(target)
    reports = project_tool.review_reports(target, _report_status(), {})["reports"]
    by_path = {item["path"]: item for item in reports}
    assert by_path["reports/a_bad.md"]["problems"][0]["kind"] == "unreadable"
    assert len(by_path["reports/b_good.md"]["claims"]) == 1


def test_a_report_with_carriage_return_line_endings_keeps_its_findings(tmp_path: Path) -> None:
    target = _project(tmp_path)
    _write(target, "evidence/model/metrics.json", '{"auc": 0.83}')
    _write(
        target,
        "reports/study.md",
        "---\rstatus: final\r---\r\rAccuracy was [0.83](../evidence/model/metrics.json#/auc).\r",
    )
    report = project_tool.review_reports(target, _report_status(), {})["reports"][0]
    assert report["final"] is True
    assert report["problems"]


def _findings_project(tmp_path: Path) -> Path:
    target = _project(tmp_path)
    _write(target, "evidence/model/metrics.json", json.dumps({"auc": 0.83, "bias": -0.6}))
    _write(target, "evidence/model/plot.png", "x")
    _write(
        target,
        "reports/study.md",
        "---\nstatus: draft\n---\n\n# Study\n\n"
        "Good [0.83](../evidence/model/metrics.json#/auc) and "
        "bad [-0.9](../evidence/model/metrics.json#/bias) on one line.\n\n"
        "![Map](../evidence/model/plot.png)\n\n"
        "A [broken link](missing.md) is not a claim.\n",
    )
    _git_baseline(target)
    return target


def _current_state(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = [
        {"path": path, "stage": "train", "label": "current", "reason": ""}
        for path in ("evidence/model/metrics.json", "evidence/model/plot.png")
    ]
    outputs[1]["dependency_check"] = "untraced"
    state = {"status": "available", "problem": None, "outputs": outputs}
    monkeypatch.setattr(project_tool, "output_status", lambda root: state)
    monkeypatch.setitem(
        vars(sys.modules[project_tool.review_reports.__module__]),
        "output_status",
        lambda root: state,
    )


def test_each_finding_lands_on_its_own_claim_even_on_a_shared_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _findings_project(tmp_path)
    _current_state(monkeypatch)
    report = project_tool.review_reports(target, _report_status(), {})["reports"][0]
    bad, noted, good = report["claims"]
    assert [claim["text"] for claim in (bad, noted, good)] == ["-0.9", "Map", "0.83"]
    assert [item["kind"] for item in bad["problems"]] == ["value-mismatch"]
    assert [item["kind"] for item in noted["notes"]] == ["reads-not-checked"]
    assert good["problems"] == [] and good["notes"] == []
    assert report["attention"] == 2


def test_a_finding_without_one_claim_stays_visible_at_report_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _findings_project(tmp_path)
    _current_state(monkeypatch)
    report = project_tool.review_reports(target, _report_status(), {})["reports"][0]
    assert [item["kind"] for item in report["loose_problems"]] == ["missing-file"]
    blocks = project_tool.review_report_sections(target, _report_status(), {})
    assert any("missing.md" in block.get("text", "") for block in blocks)
    shown = " ".join(str(block.get("html", "")) for block in blocks)
    assert "2 of 3 need attention" in shown and "1 more not tied to one claim" in shown


def test_a_wrong_number_never_reads_as_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _findings_project(tmp_path)
    _current_state(monkeypatch)
    blocks = project_tool.review_report_sections(target, _report_status(), {})
    claims = [block["html"] for block in blocks if block["type"] == "claim"]
    assert "<strong>value mismatch</strong>" in claims[0]
    assert "<strong>current</strong>" not in claims[0]
    assert "The report shows -0.9" in claims[0]
    assert "<strong>current</strong>" in claims[2]


def test_the_public_report_check_keeps_its_shape(tmp_path: Path) -> None:
    target = _findings_project(tmp_path)
    checked = project_tool.check_report(target, target / "reports/study.md", {"outputs": []})
    assert checked["problems"]
    assert all("item" not in item for item in [*checked["problems"], *checked["evidence_notes"]])


def test_a_report_line_reads_as_a_sentence() -> None:
    sentences = project_tool._report_sentences
    line = "RMSE of [3.4](../evidence/a.json#/rmse) mm/yr, see ![Map](figures/map.png)."
    assert sentences(line) == {1: "RMSE of 3.4 mm/yr, see Map."}
    assert sentences("- **Bias** is [`-0.6`](a.json)") == {1: "Bias is -0.6"}


def test_a_reference_citation_and_an_embedded_comment_read_cleanly() -> None:
    body = (
        "Score [0.83][score] and [0.<!-- checked -->91](b.json).\nNext line.\n\n[score]: a.json\n"
    )
    whole = "Score 0.83 and 0.91. Next line."
    assert project_tool._report_sentences(body) == {1: whole, 2: whole}


def test_a_wrapped_line_keeps_the_whole_claim() -> None:
    body = "The model was not\nbetter at [0.83](a.json) than chance.\n"
    whole = "The model was not better at 0.83 than chance."
    assert project_tool._report_sentences(body) == {1: whole, 2: whole}


def test_a_report_claim_keeps_its_value_and_line_without_raw_syntax(tmp_path: Path) -> None:
    target = _report_project(tmp_path)
    blocks = project_tool.review_report_sections(target, _report_status(), {})
    claims = " ".join(block["html"] for block in blocks if block["type"] == "claim")
    assert "](" not in claims and "![" not in claims
    assert "<strong>0.83</strong>" in claims and " · line " in claims


def test_report_pages_show_only_the_selected_file_and_claim_range(tmp_path: Path) -> None:
    target = _report_project(tmp_path)
    _write(
        target,
        "reports/large.md",
        "\n".join(f"\n![Figure {i}](../evidence/model/plot.png)\n" for i in range(101)),
    )
    found = project_tool.review_reports(target, _report_status(), {})
    size = project_tool.REVIEW_CLAIMS_PER_PAGE
    assert size == 25
    for page in range(5):
        blocks = project_tool.review_report_page(found, claim_page=page)
        claims = [block["html"] for block in blocks if block["type"] == "claim"]
        expected = range(page * size, min((page + 1) * size, 101))
        assert len(claims) == len(expected)
        assert all(
            f"<strong>Figure {i}</strong>" in claim
            for i, claim in zip(expected, claims, strict=True)
        )
        assert any("of 101 need attention" in block.get("html", "") for block in blocks)
    other = project_tool.review_report_page(found, report_index=1)
    assert sum(block["type"] == "claim" for block in other) == 2
    assert all("Figure " not in block.get("html", "") for block in other)
    # Projects with the previous notebook still read every claim using the old call.
    legacy = project_tool.review_report_sections(target, _report_status(), {})
    assert sum(block["type"] == "claim" for block in legacy) == 103


def test_long_wrapped_context_is_shared_without_moving_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _findings_project(tmp_path)
    _current_state(monkeypatch)
    path = target / "reports/study.md"
    path.write_text(
        "Opening qualification " + ("<word> " * 14_000) + "\n"
        "Good [0.83](../evidence/model/metrics.json#/auc) and "
        "bad [-0.9](../evidence/model/metrics.json#/bias)\n"
        + "\n".join("also [0.83](../evidence/model/metrics.json#/auc)" for _ in range(1198))
        + "\nClosing qualification.\n",
        encoding="utf-8",
    )
    found = project_tool.review_reports(target, _report_status(), {})
    assert len(found["reports"][0]["claims"]) == 1200
    for page in (0, 47):
        blocks = project_tool.review_report_page(found, claim_page=page)
        claims = [block["html"] for block in blocks if block["type"] == "claim"]
        shown = "".join(block.get("html", "") for block in blocks)
        assert len(claims) == 25
        assert all('popovertarget="review-context-0-1"' in claim for claim in claims)
        assert shown.count('id="review-context-0-1"') == 1
        assert shown.count("Opening qualification") == shown.count("Closing qualification.") == 1
        assert "&lt;word&gt;" in shown and "<word>" not in shown
        assert len(shown.encode()) < 250_000
        assert sum("value mismatch" in claim for claim in claims) == (1 if page == 0 else 0)
        assert all("value mismatch" not in claim for claim in claims if ">0.83</strong>" in claim)


def test_the_outdated_warning_has_no_literal_backticks(tmp_path: Path) -> None:
    import marimo as mo

    _write(tmp_path, "evidence/a.txt", "x")
    output = {"item": None, "path": "evidence/a.txt", "label": "outdated"}
    shown = project_tool._draw_output(mo, tmp_path, output).text
    assert "`" not in shown and "just reproduce" in shown


def test_facts_draw_as_static_html_without_table_chrome(tmp_path: Path) -> None:
    import marimo as mo

    block = {"type": "table", "rows": [{"a": "<b>x</b>"}]}
    shown = project_tool.review_draw(mo, block, {}, tmp_path).text
    assert "marimo-table" not in shown and "<b>x</b>" not in shown
