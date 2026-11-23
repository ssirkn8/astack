"""What a step's outputs look like, and which step backs each claim in a report.

Previews only read. A format with no reader installed shows its file facts instead, so a missing
library is never mistaken for an empty output. `review_draw` takes marimo as an argument, so the
notebook stays a thin display and this file needs no marimo to import.
"""

from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    _READ_ONLY,
    REPORT_URL_SCHEME,
    REVIEW_BAR_STYLE,
    REVIEW_CLAIMS_PER_PAGE,
    REVIEW_INLINE_CONTEXT_CHARS,
    REVIEW_PREVIEW_ROWS,
    REVIEW_PREVIEW_TEXT_BYTES,
    Any,
    Path,
    _open_regular_file,
    json,
    markdown_it,
    posixpath,
    secrets,
    stat,
    struct,
    unquote,
    yaml,
    zlib,
)
from .reports import (
    _check_report_text,
    _is_cited_value,
    _report_front_matter,
    _report_items,
    _report_parser,
    _report_paths,
)
from .review import (
    _review_output_status,
    review_file_title,
    review_heading_html,
    review_sections,
    review_table_html,
    review_title_html,
)
from .staleness import evidence_label


def _inside_project(root: Path, relative: str) -> Path | None:
    """The real path of an output, or None when it leads outside the project."""
    try:
        real = (root / relative).resolve()
        real.relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return None
    return real


def _file_facts(path: Path, note: str = "") -> dict[str, Any]:
    size = path.stat().st_size
    text = f"{path.suffix or 'No extension'} file, {size:,} bytes."
    return {"kind": "facts", "text": f"{text} {note}".strip()}


def _png(rows: list[bytes], width: int, height: int) -> bytes:
    """A grayscale PNG, written directly so a preview needs no imaging library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + kind + data + crc

    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _thumbnail(values: Any) -> tuple[bytes | None, str]:
    """A 2-D array as a PNG scaled to its finite range, and that range in words."""
    import numpy as np

    array = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(values, dtype="float64")), np.nan)
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return None, "no valid values"
    low, high = float(finite.min()), float(finite.max())
    scaled = np.nan_to_num((array - low) / ((high - low) or 1.0), nan=0.0)
    pixels = (scaled * 255).astype("uint8")
    rows = [row.tobytes() for row in pixels]
    return _png(rows, pixels.shape[1], pixels.shape[0]), f"{low:.4g} to {high:.4g}"


def _raster_preview(path: Path) -> dict[str, Any]:
    try:
        import rasterio
    except ImportError:
        return _file_facts(path, "Install rasterio to preview it.")
    with rasterio.open(path) as source:
        scale = max(1, max(source.width, source.height) // 240)
        shape = (max(1, source.height // scale), max(1, source.width // scale))
        band = source.read(1, out_shape=shape, masked=True)
        facts = [
            (
                f"size: {source.width} × {source.height} px, "
                f"{source.count} band(s), {source.dtypes[0]}"
            ),
            f"CRS: {source.crs}",
            "bounds: " + ", ".join(f"{value:.6g}" for value in source.bounds),
            f"nodata: {source.nodata}",
        ]
    image, value_range = _thumbnail(band)
    facts += [f"band 1 range: {value_range}", f"thumbnail: 1/{scale} scale, grayscale"]
    return {"kind": "raster", "png": image, "facts": facts}


def _netcdf_preview(path: Path) -> dict[str, Any]:
    try:
        import xarray
    except ImportError:
        return _file_facts(path, "Install xarray to preview it.")
    with xarray.open_dataset(path, decode_times=False) as dataset:
        facts = ["dimensions: " + ", ".join(f"{k}={v}" for k, v in dataset.sizes.items())]
        facts += [
            f"{name}: {tuple(item.dims)}, {item.dtype}" for name, item in dataset.data_vars.items()
        ]
        field = next(
            (item for item in dataset.data_vars.values() if item.ndim >= 2 and item.size), None
        )
        image = None
        if field is not None:
            first = field.isel({dim: 0 for dim in field.dims[:-2]})
            step = max(1, max(first.shape) // 240)
            sampled = first.isel({dim: slice(None, None, step) for dim in first.dims})
            image, value_range = _thumbnail(sampled.values)
            facts.append(f"thumbnail: {field.name}, first slice, 1/{step} scale, {value_range}")
    return {"kind": "raster", "png": image, "facts": facts}


def _limited_note(shown: int, total: int | None, limit: int) -> str:
    if total is not None and total > shown:
        return f"First {shown:,} of {total:,} rows"
    if total is None and shown >= limit:
        return f"First {shown:,} rows. The file may have more"
    return f"{shown:,} rows"


def _parquet_preview(path: Path, limit: int) -> dict[str, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return _file_facts(path, "Install pyarrow to preview it.")
    with pq.ParquetFile(path) as parquet:
        total = parquet.metadata.num_rows
        batches, count = [], 0
        for batch in parquet.iter_batches(batch_size=min(limit, 10_000)):
            batches.append(batch.slice(0, limit - count))
            count += batches[-1].num_rows
            if count >= limit:
                break
        table = pa.Table.from_batches(batches, schema=parquet.schema_arrow)
    return {"kind": "data", "table": table, "note": _limited_note(table.num_rows, total, limit)}


def _delimited_text_preview(path: Path, delimiter: str, limit: int) -> dict[str, Any]:
    import csv
    from itertools import islice

    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        rows = list(islice(csv.DictReader(handle, delimiter=delimiter), limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    note = _limited_note(len(rows), None if more else len(rows), limit)
    return {"kind": "data", "table": rows, "note": f"{note}, read as text"}


def _delimited_preview(path: Path, limit: int) -> dict[str, Any]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        import pyarrow as pa
        import pyarrow.csv as pc
    except ImportError:
        return _delimited_text_preview(path, delimiter, limit)
    options = pc.ParseOptions(delimiter=delimiter)
    batches, count, more = [], 0, False
    try:
        with pc.open_csv(path, parse_options=options) as reader:
            schema = reader.schema
            for batch in reader:
                room = limit - count
                batches.append(batch.slice(0, room))
                count += batches[-1].num_rows
                if batch.num_rows > room:
                    more = True
                    break
    except pa.ArrowInvalid as error:
        if str(error).startswith("In CSV column #"):
            return _delimited_text_preview(path, delimiter, limit)
        raise
    table = pa.Table.from_batches(batches, schema=schema)
    note = _limited_note(table.num_rows, None if more else table.num_rows, limit)
    return {"kind": "data", "table": table, "note": note}


def _text_preview(path: Path) -> dict[str, Any]:
    with _open_regular_file(path) as handle:
        data = handle.read(REVIEW_PREVIEW_TEXT_BYTES + 1)
    cut = len(data) > REVIEW_PREVIEW_TEXT_BYTES
    text = data[:REVIEW_PREVIEW_TEXT_BYTES].decode("utf-8", errors="replace")
    if path.suffix.lower() == ".json" and not cut:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return {"kind": "json", "data": parsed}
    return {"kind": "text", "text": text + ("\n[Cut short]" if cut else "")}


def review_preview(root: Path, relative: str) -> dict[str, Any]:
    """One output, ready to show. Failing to read it says why and never looks like empty data."""
    path = _inside_project(root, relative)
    if path is None:
        return {"kind": "problem", "text": f"{relative} points outside the project."}
    suffix = path.suffix.lower()
    try:
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            names = sorted(item.relative_to(path).as_posix() for item in path.rglob("*"))
            return {"kind": "text", "text": "\n".join(names[:200]) or "Empty folder."}
        if not stat.S_ISREG(mode):
            return {"kind": "problem", "text": "Cannot preview a special file."}
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
            return {"kind": "image", "path": str(path)}
        if suffix in {".csv", ".tsv"}:
            return _delimited_preview(path, REVIEW_PREVIEW_ROWS)
        if suffix in {".parquet", ".pq"}:
            return _parquet_preview(path, REVIEW_PREVIEW_ROWS)
        if suffix in {".tif", ".tiff"}:
            return _raster_preview(path)
        if suffix in {".nc", ".nc4", ".netcdf"}:
            return _netcdf_preview(path)
        if suffix in {".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".log"}:
            return _text_preview(path)
        return _file_facts(path)
    except FileNotFoundError:
        return {"kind": "problem", "text": "Not produced yet."}
    except Exception as error:  # noqa: BLE001 - a preview must never break the review page.
        return {"kind": "problem", "text": f"Could not read it: {type(error).__name__}: {error}"}


def _chartable_table(table: Any) -> Any | None:
    if isinstance(table, list):
        return None
    try:
        import pyarrow as pa
    except ImportError:
        return None
    if not isinstance(table, pa.Table):
        return None
    columns = [field.name for field in table.schema if not pa.types.is_nested(field.type)]
    return table.select(columns) if columns else None


def _keep(keep: list[Any] | None, element: Any) -> Any:
    """Hold a strong reference to a marimo UI element so its weakref registry entry stays alive."""
    if keep is not None:
        keep.append(element)
    return element


def _draw_preview(mo: Any, root: Path, relative: str, keep: list[Any] | None = None) -> Any:
    shown = review_preview(root, relative)
    kind = shown["kind"]
    if kind == "image":
        return mo.image(src=shown["path"], width=520)
    if kind == "json":
        return mo.json(shown["data"])
    if kind == "data":
        table_ui = _keep(keep, mo.ui.table(shown["table"], selection=None, label=shown["note"]))
        parts = [table_ui]
        chartable = _chartable_table(shown["table"])
        if chartable is not None:
            explore = _keep(keep, mo.lazy(lambda t=chartable: _keep(keep, mo.ui.data_explorer(t))))
            parts.append(mo.accordion({"Explore with charts": explore}))
        return mo.vstack(parts)
    if kind == "raster":
        facts = mo.plain_text("\n".join(shown["facts"]))
        if shown["png"] is None:
            return facts
        return mo.hstack([mo.image(src=shown["png"], width=320), facts], justify="start", gap=2)
    if kind == "problem":
        return mo.callout(mo.plain_text(shown["text"]), kind="warn")
    return mo.plain_text(shown["text"])


def _draw_output(mo: Any, root: Path, output: dict[str, Any], keep: list[Any] | None = None) -> Any:
    """An output and its label. One that is not current says so before it can be judged."""
    label = str(output["label"])
    title = mo.Html(review_file_title(str(output["path"]), label))
    preview = _keep(
        keep,
        mo.lazy(
            lambda path=output["path"]: _draw_preview(mo, root, path, keep),
            show_loading_indicator=True,
        ),
    )
    if label == "current":
        return mo.vstack([title, preview])
    warning = mo.callout(
        mo.plain_text(
            f"This output is {label}, so don't judge it yet. Ask your agent to run just reproduce."
        ),
        kind="warn",
    )
    return mo.vstack([title, warning, mo.accordion({"Show this version anyway": preview})])


def _draw_links(mo: Any, group: dict[str, Any], links: Any) -> Any:
    """A title and its page buttons."""
    buttons = [links[unit] for unit in group["units"]] or [mo.md("_none_")]
    return mo.hstack([mo.md(f"**{group['title']}**"), *buttons], justify="start", wrap=True)


def review_draw(
    mo: Any,
    block: dict[str, Any],
    links: Any,
    root: Path,
    keep: list[Any] | None = None,
) -> Any:
    """Draw one typed block from `review_sections` or `review_report_sections`."""
    kind = block["type"]
    if kind == "html":
        return mo.Html(block["html"])
    if kind == "text":
        return mo.plain_text(block["text"])
    if kind == "callout":
        shown = mo.Html(block["html"]) if block.get("html") else mo.plain_text(block["text"])
        return mo.callout(shown, kind=block["kind"])
    if kind == "fold":
        return mo.accordion({block["title"]: mo.Html(block["html"])})
    if kind == "table":
        return mo.Html(review_table_html(block["rows"]))
    if kind == "outputs":
        drawn = [_draw_output(mo, root, output, keep) for output in block["outputs"]]
        return mo.vstack(drawn)
    if kind == "output_groups":
        return mo.accordion(
            {
                group["title"]: _keep(
                    keep,
                    mo.lazy(
                        lambda items=group["outputs"]: mo.vstack(
                            [_draw_output(mo, root, output, keep) for output in items]
                        )
                    ),
                )
                for group in block["groups"]
            }
        )
    if kind == "claim":
        buttons = [links[unit] for unit in block["units"]]
        return mo.hstack([mo.Html(block["html"]), *buttons], justify="space-between", widths=[5, 1])
    if kind == "link_row":
        row = [_draw_links(mo, group, links) for group in block["groups"]]
        return mo.hstack(row, justify="start", wrap=True, gap=1.5)
    return _draw_links(mo, block, links)


class ReviewActionGate:
    """Accepts one click per render of the action bar.

    marimo gives a re-created control the same id, so a click sent from the old page would run the
    new page's callback. Each render gets a fresh token, and only that token is honored, once.
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._allowed: frozenset[str] = frozenset()

    def issue(self, allowed: Any) -> str:
        """Start a render. Any token from an earlier render stops working."""
        self._token = secrets.token_urlsafe(16)
        self._allowed = frozenset(allowed)
        return self._token

    def void(self) -> None:
        """The page is about to change, so nothing from the current render may act."""
        self._token = None
        self._allowed = frozenset()

    def voiding(self, setter: Any) -> Any:
        """A state setter that voids the render first, even if a click is already queued."""

        def guarded(update: Any) -> None:
            self.void()
            setter(update)

        return guarded

    def accept(self, event: Any) -> tuple[str, str] | None:
        """The action and reason of a valid click. Stale, replayed or malformed events are None."""
        if not isinstance(event, dict) or self._token is None:
            return None
        token, action, reason = event.get("token"), event.get("action"), event.get("reason")
        if not isinstance(token, str) or not secrets.compare_digest(
            token.encode(), self._token.encode()
        ):
            return None
        if not isinstance(reason, str):
            return None
        if not isinstance(action, str) or action not in self._allowed:
            return None
        self.void()
        return action, reason if action == "confirm" else ""


def review_action_view(
    page: dict[str, Any],
    status: dict[str, Any],
    next_step: str | None,
    revoking: str | None,
    refused: dict[str, str] | None,
) -> dict[str, Any]:
    """What the action bar shows and which actions this render may send."""
    signed = page["status"] == "signed off"
    confirming = signed and revoking == page["unit"]
    why = refused["text"] if refused and refused["unit"] == page["unit"] else ""
    left = max(status["to_sign_off"] - (not signed), 0)
    label = "Commit the code first" if page["blocked"] else "Sign off"
    if page.get("stale"):
        label = "Refresh before signing off"
    allowed = [
        *(["sign"] if not signed and not page["blocked"] else []),
        *(["next"] if next_step is not None else []),
        *(["revoke"] if signed else []),
        *(["confirm", "cancel"] if confirming else []),
        *(["refresh"] if why else []),
    ]
    return {
        "status": page["status"],
        "sign": {"label": "✓ Signed off" if signed else label, "primary": not signed},
        "next": {"label": f"Next → ({left} left)", "primary": signed},
        "can_revoke": signed,
        "confirming": confirming,
        "refused": why,
        "allowed": allowed,
    }


def review_apply_action(
    accepted: tuple[str, str] | None,
    page: dict[str, Any],
    next_step: str | None,
    sign_off: Any,
    revoke: Any,
    set_page: Any,
    set_revoking: Any,
    refresh: Any,
) -> None:
    """Run one accepted click against the page it was rendered for."""
    if accepted is None:
        return
    action, reason = accepted
    if action == "sign":
        if page["checksum"] is not None:
            sign_off(page["unit"], page["checksum"])
    elif action == "next" and next_step is not None:
        set_page(f"unit:{next_step}")
    elif action == "revoke":
        set_revoking(page["unit"])
    elif action == "confirm":
        revoke(page["unit"], reason)
        set_revoking(None)
    elif action == "cancel":
        set_revoking(None)
    elif action == "refresh":
        refresh()


def _action_bar(mo: Any, page: dict[str, Any], action_bar: Any) -> Any:
    """The step's one action bar, and why a sign-off was withdrawn."""
    bar = [action_bar]
    if page["revoked"]:
        gone = page["revoked"]
        text = f"Revoked by {gone['by']} at {gone['at']}. {gone['reason']}"
        bar.append(mo.callout(mo.plain_text(text), kind="danger"))
    return mo.vstack(bar).style(REVIEW_BAR_STYLE)


def review_page_view(
    mo: Any, root: Path, page: dict[str, Any], action_bar: Any, links: Any, keep: list[Any]
) -> Any:
    """A step page. The bar and body share one parent, because a stack would box in sticky."""
    body = [review_draw(mo, block, links, root, keep=keep) for block in review_sections(page)]
    bar = _action_bar(mo, page, action_bar)
    return mo.Html(review_title_html(page) + bar.text + mo.vstack(body).text)


def _owner(path: str, owners: dict[str, str]) -> str | None:
    """The step that produces a path, directly or through an output folder."""
    if path in owners:
        return owners[path]
    return next((unit for out, unit in owners.items() if path.startswith(out + "/")), None)


def _report_sentences(body: str) -> dict[int, str]:
    """Each source line's whole paragraph, so a line break can never cut a claim's meaning."""
    parser = _report_parser()
    sentences: dict[int, str] = {}
    for token in parser.parse(body):
        if token.type != "inline" or not token.children:
            continue
        parts: list[str] = []
        for child in token.children:
            if child.type in {"text", "code_inline"}:
                parts.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                parts.append(" ")
            elif child.type == "image":
                parts.append(parser.renderer.renderInlineAsText(child.children, parser.options, {}))
            elif child.type == "html_inline" and not child.content.startswith("<!--"):
                parts.append(child.content)
        text = "".join(parts).strip()
        if not text:
            continue
        first, last = token.map if token.map else (0, 1)
        for line in range(first + 1, last + 1):
            shown = [sentences.get(line, ""), text]
            sentences[line] = " ".join(part for part in shown if part)
    return sentences


def _report_claims(
    root: Path,
    report: Path,
    source: str,
    state: dict[str, Any],
    labels: dict[str, str],
    owners: dict[str, str],
) -> list[dict[str, Any]]:
    """Each figure and cited number, its evidence, the evidence's label and its step."""
    lines = source.split("\n")
    skip, _ = _report_front_matter(lines)
    body = "\n".join([""] * skip + lines[skip:])
    sentences = _report_sentences(body)
    claims: list[dict[str, Any]] = []
    for item, (kind, line, destination, text) in enumerate(_report_items(body)):
        if kind not in {"figure", "link"} or (kind == "link" and not _is_cited_value(text)):
            continue
        target = str(destination or "")
        location = unquote(target.partition("#")[0].split("?", 1)[0])
        relative = None
        if location and not REPORT_URL_SCHEME.match(target) and not target.startswith("//"):
            joined = posixpath.normpath(posixpath.join(report.parent.as_posix(), location))
            try:
                relative = Path(joined).resolve().relative_to(root.resolve()).as_posix()
            except (OSError, ValueError):
                relative = None
        covered = evidence_label(state, relative) if relative else None
        if covered is not None:
            label = str(covered.get("label", "unknown"))
            matched = str(covered.get("path", relative))
            unit = _owner(matched, owners) or (_owner(relative, owners) if relative else None)
        else:
            label = labels.get(relative or "", "unknown")
            unit = _owner(relative, owners) if relative else None
        claims.append(
            {
                "item": item,
                "line": line,
                "kind": "figure" if kind == "figure" else "number",
                "text": text,
                "context": sentences.get(line, ""),
                "evidence": relative or target,
                "label": label,
                "unit": unit,
            }
        )
    return claims


def _attach_findings(
    claims: list[dict[str, Any]], problems: list[dict[str, Any]], notes: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Put each finding on the one claim it came from and sort failures, notes, then passing.

    Findings carry the parsed item that produced them, so several claims on one line stay apart.
    Anything without a claim is returned, so the page can show it at report level.
    """
    by_item = {claim["item"]: claim for claim in claims}
    loose: dict[str, list[dict[str, Any]]] = {"problems": [], "notes": []}
    for claim in claims:
        claim["problems"], claim["notes"] = [], []
    for key, findings in (("problems", problems), ("notes", notes)):
        for finding in findings:
            owner = by_item.get(finding.get("item"))
            (owner[key] if owner else loose[key]).append(finding)
    claims.sort(
        key=lambda c: (0 if c["problems"] else 1 if c["notes"] else 2, c["line"], c["item"])
    )
    return loose


def _claim_verdict(claim: dict[str, Any]) -> str:
    """A claim with a finding names it, so a wrong number can never read as `current`."""
    kinds = [str(item["kind"]).replace("-", " ") for item in [*claim["problems"], *claim["notes"]]]
    return ", ".join(dict.fromkeys(kinds)) or str(claim["label"])


def review_reports(root: Path, status: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """Every report's claims with their live labels. Report files are only ever read."""
    from io import TextIOWrapper

    if markdown_it is None or yaml is None:
        return {"problem": "Reading reports needs markdown-it-py and PyYAML.", "reports": []}
    owners = {
        path: str(row["unit"])
        for row in status["units"]
        for item in row["items"]
        for path in item["outputs"]
    }
    previous = _READ_ONLY["on"]
    _READ_ONLY["on"] = True
    try:
        state = _review_output_status(root)
        reports = []
        for path in _report_paths(root, []):
            try:
                # One read, so a finding's item always indexes the claims parsed from it.
                resolved = path.resolve()
                with TextIOWrapper(_open_regular_file(resolved), encoding="utf-8") as handle:
                    source = handle.read()
                checked = _check_report_text(root, resolved, source, state)
                claims = _report_claims(root, resolved, source, state, labels, owners)
            except (OSError, UnicodeDecodeError) as error:
                try:
                    display = path.relative_to(root).as_posix()
                except ValueError:
                    display = path.as_posix()
                checked = {
                    "path": display,
                    "final": False,
                    "problems": [{"line": 0, "kind": "unreadable", "message": str(error)}],
                    "evidence_notes": [],
                }
                claims = []
            notes = checked.get("evidence_notes", [])
            loose = _attach_findings(claims, checked["problems"], notes)
            reports.append(
                {
                    "path": checked["path"],
                    "final": checked["final"],
                    "problems": checked["problems"],
                    "notes": notes,
                    "claims": claims,
                    "loose_problems": loose["problems"],
                    "loose_notes": loose["notes"],
                    "attention": sum(bool(c["problems"] or c["notes"]) for c in claims),
                }
            )
    finally:
        _READ_ONLY["on"] = previous
    return {"problem": None, "reports": reports}


def review_report_picker(mo: Any, found: dict[str, Any]) -> Any:
    options = {report["path"]: i for i, report in enumerate(found["reports"])}
    if len(options) > 1:
        return mo.ui.dropdown(options, value=next(iter(options)), label="Report file")
    return None


def review_claim_page_picker(mo: Any, found: dict[str, Any], report_index: int) -> Any:
    reports = found["reports"]
    count = len(reports[report_index]["claims"]) if reports else 0
    size = REVIEW_CLAIMS_PER_PAGE
    if count <= size:
        return None
    options = {
        f"{start + 1}–{min(start + size, count)} of {count}": page
        for page, start in enumerate(range(0, count, size))
    }
    return mo.ui.dropdown(options, value=next(iter(options)), label="Claims")


def review_report_sections(
    root: Path, status: dict[str, Any], labels: dict[str, str]
) -> list[dict[str, Any]]:
    """The unpaged entry point used by review notebooks copied into existing projects."""
    found = review_reports(root, status, labels)
    intro = "Each cited figure and number, its evidence's label now, and the step that made it."
    blocks: list[dict[str, Any]] = [
        {
            "type": "html",
            "html": (
                f"{review_heading_html('Report', 2)}"
                f"<p style='margin:0 0 .35rem'><em>{intro} Reports are never changed.</em></p>"
            ),
        }
    ]
    if found["problem"] or not found["reports"]:
        return [*blocks, *review_report_page(found)]
    for index, report in enumerate(found["reports"]):
        blocks.extend(_review_report_blocks(report, report["claims"], index))
    return blocks


def review_report_page(
    found: dict[str, Any], report_index: int = 0, claim_page: int = 0
) -> list[dict[str, Any]]:
    """Render one page of the selected report, with zero-based report and page indices."""
    if found["problem"]:
        return [{"type": "callout", "kind": "warn", "text": found["problem"]}]
    if not found["reports"]:
        return [{"type": "text", "text": "No reports under reports/."}]
    report = found["reports"][report_index]
    start = claim_page * REVIEW_CLAIMS_PER_PAGE
    claims = report["claims"][start : start + REVIEW_CLAIMS_PER_PAGE]
    return _review_report_blocks(report, claims, report_index)


def _review_report_blocks(
    report: dict[str, Any], claims: list[dict[str, Any]], report_index: int
) -> list[dict[str, Any]]:
    import html

    state = "final" if report["final"] else "draft"
    blocks: list[dict[str, Any]] = [
        {"type": "html", "html": review_file_title(report["path"], state, 3)}
    ]
    loose = len(report["loose_problems"]) + len(report["loose_notes"])
    if report["claims"]:
        count = f"{report['attention']} of {len(report['claims'])} need attention"
        more = f" · {loose} more not tied to one claim" if loose else ""
        blocks.append({"type": "html", "html": f"<p><strong>{count}</strong>{more}</p>"})
    for key, kind in (("loose_problems", "danger"), ("loose_notes", "info")):
        if report[key]:
            text = "\n".join(f"Line {p['line']}: {p['message']}" for p in report[key])
            blocks.append({"type": "callout", "kind": kind, "text": text})
    if not report["claims"]:
        blocks.append({"type": "text", "text": "No cited figures or numbers."})

    contexts: dict[str, int] = {}
    for claim in claims:
        findings = "".join(
            f"<br><strong>{name}:</strong> {html.escape(str(item['message']))}"
            for name, key in (("Problem", "problems"), ("Note", "notes"))
            for item in claim[key]
        )
        cited = claim["text"] or claim["kind"]
        context = claim["context"]
        sentence = ""
        if context and context != cited:
            if len(context) > REVIEW_INLINE_CONTEXT_CHARS:
                number = contexts.setdefault(context, len(contexts) + 1)
                sentence = (
                    f'<button type="button" popovertarget="review-context-{report_index}-{number}" '
                    f'style="text-decoration:underline">Full context {number}</button><br>'
                )
            else:
                sentence = f"{html.escape(context)}<br>"
        body = (
            f"<p style='margin:0'><strong>{html.escape(cited)}</strong>"
            f" · line {claim['line']}<br>{sentence}"
            f"<code>{html.escape(claim['evidence'])}</code> · "
            f"<strong>{html.escape(_claim_verdict(claim))}</strong>{findings}</p>"
        )
        units = [claim["unit"]] if claim["unit"] else []
        blocks.append({"type": "claim", "html": body, "units": units})
    for context, number in contexts.items():
        label = f"Full context {number}"
        body = (
            f'<div id="review-context-{report_index}-{number}" popover="auto" tabindex="0" '
            f'role="region" aria-label="{label}" '
            f'style="max-width:min(72ch, 90vw); max-height:45vh; overflow:auto; '
            f"overflow-wrap:anywhere; padding:1rem; background:Canvas; color:CanvasText; "
            f'border:1px solid GrayText">{html.escape(context)}</div>'
        )
        blocks.append({"type": "html", "html": body})
    return blocks


def review_block_units(blocks: list[dict[str, Any]]) -> list[str]:
    """Every unit these blocks link to, once, so the notebook can make one button for each."""
    return list(dict.fromkeys(unit for block in blocks for unit in block.get("units", [])))


def review_route(target: str) -> str:
    """Normalize an initial page name into a non-colliding `page:` or `unit:` route."""
    if target.startswith(("page:", "unit:")):
        return target
    return f"page:{target}" if target in {"overview", "report"} else f"unit:{target}"
