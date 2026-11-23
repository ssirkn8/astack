"""Deterministic checks that a report's figures and cited numbers point at real evidence.

Markdown is parsed with markdown-it-py (CommonMark), so links, images, code and entities are
read exactly as a renderer reads them. Reports are Markdown only: raw HTML fails, except
well-formed comments, because browsers and Python read HTML differently in too many ways.
Front matter is parsed with PyYAML.
"""

from __future__ import annotations

# Each module imports what it uses. The bundle builder strips these imports.
from .common import (
    REPORT_CITED_VALUE,
    REPORT_EVIDENCE_ROOTS,
    REPORT_HTML_COMMENTS,
    REPORT_SAFE_ATTRIBUTE,
    REPORT_UNRESOLVED,
    REPORT_URL_SCHEME,
    ROUND_HALF_UP,
    Any,
    Context,
    Decimal,
    InvalidOperation,
    Path,
    bisect_right,
    json,
    markdown_it,
    posixpath,
    re,
    unquote,
    yaml,
)
from .staleness import evidence_label, output_status, run_record_citation


def _report_paths(root: Path, requested: list[Path]) -> list[Path]:
    if requested:
        return [path if path.is_absolute() else root / path for path in requested]
    reports = root / "reports"
    if not reports.is_dir():
        return []
    found = [
        path
        for pattern in ("*.md", "*.qmd")
        for path in reports.rglob(pattern)
        if path.is_file()
        and not any(part in {"figures", "tables"} for part in path.relative_to(reports).parts)
        and path.name != "README.md"
    ]
    return sorted(found)


def _report_front_matter(lines: list[str]) -> tuple[int, dict[str, Any] | None]:
    """Return how many lines the front matter spans and its mapping (None when unreadable)."""
    if not lines or lines[0].rstrip() != "---":
        return 0, {}
    for index in range(1, len(lines)):
        if lines[index].rstrip() in {"---", "..."}:
            try:
                data = yaml.safe_load("\n".join(lines[1:index]))
            except yaml.YAMLError:
                return index + 1, None
            if data is None:
                return index + 1, {}
            return index + 1, data if isinstance(data, dict) else None
    return len(lines), None  # An opening delimiter with no closing one.


def _report_is_marker(text: str) -> bool:
    """`[UNRESOLVED: x](url)` renders its text without the bracket, so check both forms."""
    return bool(REPORT_UNRESOLVED.search(text)) or text.lstrip().startswith("UNRESOLVED")


def _report_marker_lines(segments: list[tuple[int, str]]) -> list[int]:
    """Join visible text segments, find UNRESOLVED markers, and map each to its start line."""
    text = "".join(data for _, data in segments)
    starts, offset = [], 0
    for line, data in segments:
        starts.append((offset, line, data))
        offset += len(data)
    lines = []
    for match in REPORT_UNRESOLVED.finditer(text):
        for begin, line, data in reversed(starts):
            if begin <= match.start():
                lines.append(line + data[: match.start() - begin].count("\n"))
                break
    return lines


def _report_html_problem(content: str) -> str | None:
    """Raw HTML may only be well-formed comments. Return why anything else is rejected."""
    if REPORT_HTML_COMMENTS.fullmatch(content):
        return None
    if content.lstrip().startswith("<!--"):
        return "a malformed or unclosed <!-- comment -->"
    tag = re.match(r"\s*<\s*(/?[A-Za-z][A-Za-z0-9-]*|[!?][^\s>]*)", content)
    return f"<{tag.group(1)}>" if tag else "raw HTML"


def _report_pandoc_problem(
    text: str,
    after_target: bool,
    escaped: frozenset[int] = frozenset(),
    attributes: str | None = None,
) -> str | None:
    """Pandoc/Quarto syntax CommonMark reads as text, but that changes what a reader sees.

    `escaped` holds the offsets of backslash-escaped characters, which stay literal text.
    """

    def plain(*offsets: int) -> bool:
        return not any(offset in escaped for offset in offsets)

    for match in re.finditer(r"\{\{[<%]", text):
        if plain(match.start(), match.start() + 1):
            return "a Quarto shortcode ({{< ... >}}), which pulls in content this check can't see"
    for match in re.finditer(r"\]\{", text):
        if plain(match.start(), match.start() + 1):
            return "a bracketed span ([text]{...}), which changes the visible text"
    if after_target and text.startswith("{") and plain(0):
        attributes = text if attributes is None else attributes  # may run onto later lines
        close = attributes.find("}")
        block = attributes[1:close] if close > 0 else attributes[1:]
        if close < 0 or not all(REPORT_SAFE_ATTRIBUTE.fullmatch(part) for part in block.split()):
            return (
                f"the attributes {{{block}}}. Only width, height, #id and .class are allowed "
                "after an image, link or inline code"
            )
    return None


def _report_join_text(state: Any) -> None:
    """Join adjacent text like markdown-it's text_join, but remember escaped characters.

    Pandoc reads `\\{` and `&#123;` as literal braces, so `[x]\\{.a}` is text, not a span.
    markdown-it drops that difference when it joins text, so record each literal offset.
    """
    for block in state.tokens:
        if block.type == "inline" and block.children:
            block.children = _report_joined(block.children)


def _report_joined(tokens: list[Any]) -> list[Any]:
    children: list[Any] = []
    for child in tokens:
        if child.type == "image" and child.children:
            child.children = _report_joined(child.children)  # alt text
        if child.type not in {"text", "text_special"}:
            children.append(child)
            continue
        # Backslash escapes and entities (`&#123;`) both decode to literal characters.
        literal = child.type == "text_special"
        previous = children[-1] if children else None
        if previous is not None and previous.type == "text":
            offset = len(previous.content)
            previous.content += child.content
        else:
            child.type, offset = "text", 0
            child.meta["escaped"] = frozenset()
            children.append(child)
            previous = child
        if literal:
            span = range(offset, offset + len(child.content))
            previous.meta["escaped"] = previous.meta["escaped"] | set(span)
    return children


def _report_attribute_text(children: list[Any], index: int) -> str:
    """Return the text at `index`, joined with the next lines when a `{` block wraps.

    Pandoc lets `{.python` and `#fit}` sit on two lines, so read on to the closing brace.
    """
    text: str = children[index].content
    if not text.startswith("{") or "}" in text:
        return text
    for child in children[index + 1 :]:
        if child.type == "softbreak":
            text += "\n"
        elif child.type == "text":
            text += child.content
            if "}" in child.content:
                break
        else:
            break
    return text


def _report_positioned(rule: Any) -> Any:
    """Wrap an inline rule so each token it creates records where in the source it began."""

    def wrapped(state: Any, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        found = bool(rule(state, silent))
        if found and not silent:
            for token in state.tokens[count:]:
                token.meta.setdefault("pos", start)
                token.meta.setdefault("end", state.pos)
        return found

    return wrapped


def _report_parser() -> Any:
    parser = markdown_it("commonmark").enable(["table", "strikethrough"])
    ruler = parser.inline.ruler
    for rule in list(ruler.__rules__):
        ruler.at(rule.name, _report_positioned(rule.fn), {"alt": list(rule.alt)})
    parser.core.ruler.at("text_join", _report_join_text)
    return parser


def _report_items(text: str) -> list[tuple[str, int, Any, str]]:
    """Return (kind, line, destination, text) for figures, links, markers and raw HTML."""
    items: list[tuple[str, int, Any, str]] = []
    parser = _report_parser()
    for token in parser.parse(text):
        start = token.map[0] + 1 if token.map else 1
        if token.type == "html_block":
            problem = _report_html_problem(token.content)
            if problem is not None:
                items.append(("unsupported", start, problem, ""))
            continue
        if token.type == "fence" and re.match(r"\s*\{\s*=", token.info):
            # ```{=html} is a Pandoc raw block, which renders its content unchecked.
            problem = f"a raw block ```{token.info.strip()}, which renders unchecked content"
            items.append(("unsupported-syntax", start, problem, ""))
            continue
        if token.type != "inline" or not token.children:
            continue
        source = token.content
        breaks = [index for index, character in enumerate(source) if character == "\n"]

        def line_at(offset: int) -> int:
            return start + bisect_right(breaks, offset - 1)  # noqa: B023

        line, last_end, previous = start, 0, ""
        prose: list[tuple[int, str]] = []  # (line, visible text) between line breaks
        link: list[Any] | None = None  # [line, href, text parts]
        for index, child in enumerate(token.children):
            # Text flushed at the end of a paragraph has no rule of its own, so it starts
            # where the previous token ended.
            line = line_at(child.meta.get("pos", last_end))
            last_end = child.meta.get("end", last_end)
            if child.type in {"softbreak", "hardbreak"}:
                items.extend(("marker", found, None, "") for found in _report_marker_lines(prose))
                prose = []
                if link is not None:
                    link[2].append(" ")
            elif child.type in {"text", "code_inline"}:
                if child.type == "text":
                    prose.append((line, child.content))
                    problem = _report_pandoc_problem(
                        child.content,
                        previous in {"image", "link_close", "code_inline"},
                        child.meta.get("escaped", frozenset()),
                        _report_attribute_text(token.children, index),
                    )
                    if problem is not None:
                        items.append(("unsupported-syntax", line, problem, ""))
                if link is not None:
                    link[2].append(child.content)
            elif child.type == "image":
                # Alt text shows when the image can't load, so it counts as visible text.
                # Render it the way markdown-it writes the alt attribute.
                alt = parser.renderer.renderInlineAsText(child.children, parser.options, {})
                items.append(("figure", line, child.attrs.get("src", ""), alt))
                prose.append((line, alt))
                if _report_is_marker(alt):
                    items.append(("marker", line, None, ""))
                if link is not None:
                    link[2].append(alt)
            elif child.type == "html_inline":
                # Only comments are allowed. They show nothing, so [0.<!-- x -->83] reads 0.83.
                problem = _report_html_problem(child.content)
                if problem is not None:
                    items.append(("unsupported", line, problem, ""))
            elif child.type == "link_open":
                link = [line, child.attrs.get("href", ""), []]
            elif child.type == "link_close" and link is not None:
                label = "".join(link[2])
                items.append(("link", link[0], link[1], label))
                if _report_is_marker(label):
                    items.append(("marker", link[0], None, ""))
                link = None
            previous = child.type
        items.extend(("marker", found, None, "") for found in _report_marker_lines(prose))
    return items


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdigit():
                raise KeyError(pointer)
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def _is_cited_value(text: str) -> bool:
    return bool(REPORT_CITED_VALUE.fullmatch(text.strip()))


def _cited_value_matches(displayed: str, value: Any) -> bool:
    """Round the stored value half-up to the last digit shown, then compare exactly."""
    text = displayed.strip().replace(",", "").replace("\u2212", "-").removesuffix("%")
    shown = Decimal(text)  # InvalidOperation is reported by the caller.
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return False
    exponent = shown.as_tuple().exponent
    if not isinstance(exponent, int):  # NaN or infinity
        raise InvalidOperation(text)
    step = Decimal((0, (1,), exponent))
    context = Context(prec=1000, rounding=ROUND_HALF_UP)
    return Decimal(value).quantize(step, context=context) == shown


def _problem(line: int, kind: str, message: str) -> dict[str, Any]:
    return {"line": line, "kind": kind, "message": message}


def _report_inner_parent(location: str) -> bool:
    """True when `..` follows a named folder, as in `figures/../x.png`.

    Leading `../` steps (from reports/ to evidence/) resolve the same way in a browser and on
    disk. A later `..` can resolve differently once a folder is a symlink, so it is rejected.
    """
    segments = location.split("/")
    leading = 0
    while leading < len(segments) and segments[leading] == "..":
        leading += 1
    return ".." in segments[leading:]


def _evidence_problems(
    evidence: tuple[dict[str, Any], bool, list[dict[str, Any]], Path] | None,
    line: int,
    kind: str,
    relative: str,
) -> list[dict[str, Any]]:
    """A report may cite only current pipeline outputs.

    Outdated or missing outputs always fail. Files no run produced, and outputs whose state
    could not be read, fail a final report and are listed for a draft.
    """
    if evidence is None:
        return []
    status, final, notes, root = evidence
    record = run_record_citation(root, relative)
    if record is not None:
        if record and kind != "figure":
            return []
        return [
            _problem(
                line,
                "outdated-evidence",
                f"The {kind} cites {relative}, which is not a current run record",
            )
        ]
    entry = evidence_label(status, relative)
    if entry is not None and entry["label"] == "current":
        # Declared deps still decide staleness. A stage whose reads could not be checked, such
        # as an R or shell stage, is disclosed, never failed.
        if entry.get("dependency_check") in {"untraced", "not-run", "inputs-not-locked"}:
            notes.append(
                _problem(
                    line,
                    "reads-not-checked",
                    f"The {kind} cites {relative}, whose stage's reads were not fully checked "
                    f"({entry['dependency_check']}). Its declared deps still decide staleness.",
                )
            )
        return []
    if entry is not None and entry["label"] in {"outdated", "missing"} and entry["stage"]:
        return [
            _problem(
                line,
                "outdated-evidence",
                f"The {kind} cites {relative}, which is {entry['label']}: {entry['reason']}",
            )
        ]
    if entry is not None and entry["label"] == "unknown":
        problem = _problem(
            line,
            "unverified-evidence",
            f"The {kind} cites {relative}, whose state could not be checked: {entry['reason']}",
        )
    elif status.get("status") == "unknown":
        problem = _problem(
            line,
            "unverified-evidence",
            f"The {kind} cites {relative}, but output state could not be checked: "
            f"{status.get('problem')}",
        )
    else:
        problem = _problem(
            line,
            "unrecorded-evidence",
            f"The {kind} cites {relative}, which no recorded pipeline run produced",
        )
    if final:
        return [problem]
    notes.append(problem)
    return []


def _check_report_link(
    root: Path,
    report: Path,
    line: int,
    text: str,
    destination: str,
    image: bool,
    evidence: tuple[dict[str, Any], bool, list[dict[str, Any]], Path] | None = None,
) -> list[dict[str, Any]]:
    cited = _is_cited_value(text)
    kind = "figure" if image else "cited value" if cited else None
    if not destination or destination.startswith("#") or destination.startswith("//"):
        external = True
    else:
        external = bool(REPORT_URL_SCHEME.match(destination))
    if external:
        if kind is None:
            return []
        return [
            _problem(
                line,
                "not-evidence",
                f"The {kind} points at {destination or 'nothing'}, not a file in the project",
            )
        ]
    location, _, fragment = destination.partition("#")
    raw_path = location.split("?", 1)[0]
    location = unquote(raw_path)
    fragment = unquote(fragment)
    if kind is not None and (
        destination != destination.strip(" \t\n\f\r")
        or location != location.strip(" \t\n\f\r")
        or any(
            character == "\\" or ord(character) < 0x20 or ord(character) == 0x7F
            for character in destination + location
        )
        # Browsers keep empty segments and never split on an encoded separator, while the
        # filesystem collapses `//` and decoding turns %2F into "/".
        or "//" in raw_path
        or re.search(r"%(2f|5c)", raw_path, re.IGNORECASE)
        or _report_inner_parent(location)
    ):
        # Browsers read a backslash as "/", drop control characters and trim surrounding
        # spaces, so the file they load isn't the one a literal path names.
        return [
            _problem(
                line,
                "unsupported-path",
                f"The {kind} path has a backslash, control character, surrounding space, `//`, "
                "encoded separator or a `..` after a folder name: "
                f"{destination!r}",
            )
        ]
    # Resolve `..` the way a browser does, by the text of the path, before the filesystem
    # follows any symlink.
    resolved = Path(posixpath.normpath(posixpath.join(report.parent.as_posix(), location)))
    resolved = resolved.resolve()
    if kind is None:
        if not resolved.exists():
            return [_problem(line, "missing-file", f"Link target does not exist: {location}")]
        return []
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return [
            _problem(line, "outside-project", f"The {kind} points outside the project: {location}")
        ]
    if not any(relative.startswith(prefix) for prefix in REPORT_EVIDENCE_ROOTS):
        allowed = ", ".join(REPORT_EVIDENCE_ROOTS)
        return [
            _problem(
                line,
                "not-evidence",
                f"The {kind} points at {relative}, which is not under {allowed}",
            )
        ]
    if not resolved.is_file():
        return [_problem(line, "missing-file", f"The {kind} points at a missing file: {relative}")]
    problems = _evidence_problems(evidence, line, kind, relative)
    if image or not fragment:
        return problems
    if resolved.suffix.lower() != ".json":
        return problems + [
            _problem(
                line, "unsupported-pointer", f"A value pointer needs a JSON file, not {relative}"
            )
        ]
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"), parse_float=Decimal)
        value = _json_pointer(document, fragment)
    except (KeyError, IndexError, ValueError, UnicodeDecodeError):
        return problems + [_problem(line, "missing-value", f"No value at {relative}#{fragment}")]
    try:
        matches = _cited_value_matches(text, value)
    except InvalidOperation:
        return problems + [_problem(line, "invalid-number", f"Not a number: {text.strip()}")]
    if not matches:
        return problems + [
            _problem(
                line,
                "value-mismatch",
                f"The report shows {text.strip()}, but {relative}#{fragment} is {value}",
            )
        ]
    return problems


def check_report(
    root: Path, report: Path, output_state: dict[str, Any] | None = None
) -> dict[str, Any]:
    report = report.resolve()
    relative = report.relative_to(root).as_posix() if report.is_relative_to(root) else str(report)
    if not report.is_file():
        return {
            "path": relative,
            "status": "failed",
            "final": False,
            "problems": [_problem(0, "missing-report", f"Report not found: {relative}")],
        }
    result = _check_report_text(root, report, report.read_text(encoding="utf-8"), output_state)
    for finding in [*result["problems"], *result["evidence_notes"]]:
        finding.pop("item", None)
    return result


def _check_report_text(
    root: Path, report: Path, text: str, output_state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Check one already-read report. A finding from a link or figure names its parsed `item`."""
    relative = report.relative_to(root).as_posix() if report.is_relative_to(root) else str(report)
    lines = text.split("\n")
    skip, front_matter = _report_front_matter(lines)
    problems: list[dict[str, Any]] = []
    if front_matter is None:
        problems.append(
            _problem(1, "invalid-front-matter", "Front matter is not a closed YAML mapping")
        )
    status = str((front_matter or {}).get("status", "")).strip().casefold()
    final = front_matter is None or status == "final"
    # Blank the front matter so parser line numbers stay true to the file.
    body = "\n".join([""] * skip + lines[skip:])
    figures = cited = 0
    unresolved: list[int] = []
    notes: list[dict[str, Any]] = []
    evidence = None if output_state is None else (output_state, final, notes, root)
    for index, (kind, line, destination, label) in enumerate(_report_items(body)):
        if kind in {"figure", "link"}:
            figures += kind == "figure"
            cited += kind == "link" and _is_cited_value(label)
            noted = len(notes)
            found = _check_report_link(
                root, report, line, label, destination, kind == "figure", evidence
            )
            for finding in [*found, *notes[noted:]]:
                finding["item"] = index
            problems.extend(found)
        elif kind == "unsupported-syntax":
            problems.append(_problem(line, "unsupported-syntax", f"The report uses {destination}"))
        elif kind == "unsupported":
            problems.append(
                _problem(
                    line,
                    "unsupported-html",
                    f"Raw HTML ({destination}) isn't checked, so reports can't use it. "
                    "Use Markdown links and images. HTML comments are fine",
                )
            )
        elif (kind == "marker" or REPORT_UNRESOLVED.search(label)) and line not in unresolved:
            unresolved.append(line)
    unresolved.sort()
    if final:
        problems.extend(
            _problem(number, "unresolved", "A final report still has an UNRESOLVED marker")
            for number in unresolved
        )
    problems.sort(key=lambda problem: problem["line"])
    return {
        "path": relative,
        "status": "failed" if problems else "passed",
        "final": final,
        "figures": figures,
        "cited_values": cited,
        "unresolved_lines": unresolved,
        "problems": problems,
        "evidence_notes": notes,
    }


def report_check(root: Path, requested: list[Path]) -> dict[str, Any]:
    """Check reports, or every report under `reports/` when none is named."""
    if markdown_it is None or yaml is None:
        return {
            "status": "unavailable",
            "summary": (
                'report-check needs markdown-it-py and PyYAML. Add "markdown-it-py>=3.0" and '
                '"pyyaml>=6.0" to [dependency-groups] dev in pyproject.toml, then run uv sync.'
            ),
            "reports": [],
        }
    paths = _report_paths(root, requested)
    if not paths:
        return {
            "status": "no-reports",
            "summary": "No reports found under reports/. Nothing was checked.",
            "reports": [],
        }
    try:
        outputs = output_status(root)
    except (OSError, RuntimeError, ValueError) as exc:
        outputs = {"status": "unknown", "problem": str(exc), "outputs": []}
    results = [check_report(root, path, outputs) for path in paths]
    failed = [result for result in results if result["status"] == "failed"]
    return {
        "status": "failed" if failed else "passed",
        "summary": (
            f"{len(failed)} of {len(results)} report(s) failed."
            if failed
            else f"{len(results)} report(s) passed."
        ),
        "reports": results,
    }
