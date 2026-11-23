#!/usr/bin/env python3
"""Annotate PR images and publish them to a commit-pinned evidence branch."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any

EVIDENCE_BRANCH = "pr-evidence"
REMOTE = "origin"
MAX_BYTES = 10 * 1024 * 1024
PUSH_ATTEMPTS = 3
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Everything else, including text, EXIF, and timestamp chunks, is dropped.
KEPT_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"pHYs"}
ANNOTATION_COLOR = (220, 38, 38, 255)


class VisualsError(Exception):
    """A refusal the user can act on."""


def strip_png_metadata(data: bytes) -> bytes:
    """Return the PNG with only rendering chunks, refusing anything malformed."""
    if not data.startswith(PNG_SIGNATURE):
        raise VisualsError("not a PNG file")
    kept = [PNG_SIGNATURE]
    offset = len(PNG_SIGNATURE)
    first = True
    while offset < len(data):
        if offset + 12 > len(data):
            raise VisualsError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise VisualsError("truncated PNG chunk")
        crc = struct.unpack(">I", data[end - 4 : end])[0]
        if zlib.crc32(data[offset + 4 : end - 4]) != crc:
            raise VisualsError("corrupt PNG chunk")
        if first and kind != b"IHDR":
            raise VisualsError("PNG does not start with IHDR")
        first = False
        if kind in KEPT_CHUNKS:
            kept.append(data[offset:end])
        offset = end
        if kind == b"IEND":
            if offset != len(data):
                raise VisualsError("data after PNG end")
            return b"".join(kept)
    raise VisualsError("PNG has no end chunk")


def _git(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, input=stdin, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise VisualsError(f"git {args[0]} failed: {detail}")
    return result


def _text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8").strip()


def github_repository(remote_url: str) -> str:
    """Parse owner/name from a github.com remote, refusing other hosts."""
    match = re.fullmatch(
        r"(?:https://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?",
        remote_url,
    )
    if match is None:
        raise VisualsError("the origin remote is not a github.com repository")
    return f"{match.group(1)}/{match.group(2)}"


def require_visibility(repository: str, *, allow_public: bool) -> None:
    """Treat unknown visibility as a refusal."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", repository, "--json", "visibility"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as error:
        raise VisualsError("cannot confirm repository visibility: gh is unavailable") from error
    if result.returncode != 0:
        raise VisualsError("cannot confirm repository visibility with gh")
    try:
        visibility = json.loads(result.stdout).get("visibility")
    except (ValueError, AttributeError) as error:
        raise VisualsError("cannot confirm repository visibility with gh") from error
    if visibility == "PRIVATE" or (visibility == "PUBLIC" and allow_public):
        return
    if visibility == "PUBLIC":
        raise VisualsError("repository is public. Pass --allow-public only with human approval")
    raise VisualsError(f"refusing repository visibility: {visibility!r}")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return slug or "image"


def _prepare(paths: list[Path], prefix: str) -> dict[str, bytes]:
    prepared: dict[str, bytes] = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise VisualsError(f"{path}: not a regular file")
        if path.stat().st_size > MAX_BYTES:
            raise VisualsError(f"{path}: larger than 10 MB")
        try:
            clean = strip_png_metadata(path.read_bytes())
        except VisualsError as error:
            raise VisualsError(f"{path}: {error}") from error
        digest = hashlib.sha256(clean).hexdigest()[:12]
        prepared[f"{prefix}/{digest}-{_slug(path.stem)}.png"] = clean
    return prepared


def _commit(root: Path, parent: str | None, files: dict[str, bytes], source: str) -> str:
    with tempfile.TemporaryDirectory(prefix="pr-visuals-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        if parent:
            _git(["read-tree", parent], cwd=root, env=env)
        for name, data in files.items():
            blob = _text(_git(["hash-object", "-w", "--stdin"], cwd=root, stdin=data))
            _git(
                ["update-index", "--add", "--cacheinfo", f"100644,{blob},{name}"],
                cwd=root,
                env=env,
            )
        tree = _text(_git(["write-tree"], cwd=root, env=env))
    command = ["commit-tree", tree, "-m", f"evidence: images for {source[:12]}"]
    if parent:
        command[2:2] = ["-p", parent]
    return _text(_git(command, cwd=root))


def _remote_tip(root: Path) -> str | None:
    ref = f"refs/heads/{EVIDENCE_BRANCH}"
    listed = _text(_git(["ls-remote", REMOTE, ref], cwd=root))
    if not listed:
        return None
    tip = listed.split()[0]
    _git(["fetch", "--quiet", "--no-tags", REMOTE, tip], cwd=root)
    return tip


def publish(paths: list[Path], *, allow_public: bool, cwd: Path | None = None) -> list[str]:
    """Push cleaned images to the evidence branch and return commit-pinned URLs."""
    start = cwd or Path.cwd()
    root = Path(_text(_git(["rev-parse", "--show-toplevel"], cwd=start)))
    repository = github_repository(_text(_git(["remote", "get-url", "--push", REMOTE], cwd=root)))
    require_visibility(repository, allow_public=allow_public)
    source = _text(_git(["rev-parse", "HEAD"], cwd=root))
    branch = _text(_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root))
    prefix = f"{_slug(branch)}/{source[:12]}"
    files = _prepare(paths, prefix)
    for _ in range(PUSH_ATTEMPTS):
        commit = _commit(root, _remote_tip(root), files, source)
        pushed = _git(
            ["push", "--quiet", REMOTE, f"{commit}:refs/heads/{EVIDENCE_BRANCH}"],
            cwd=root,
            check=False,
        )
        if pushed.returncode == 0:
            return [
                f"https://github.com/{repository}/blob/{commit}/{name}?raw=true" for name in files
            ]
    detail = pushed.stderr.decode("utf-8", "replace").strip()
    raise VisualsError(f"could not push {EVIDENCE_BRANCH}: {detail}")


def _numbers(value: str, count: int) -> tuple[int, ...]:
    parts = value.split(",")
    if len(parts) != count or not all(re.fullmatch(r"\d+", part) for part in parts):
        raise argparse.ArgumentTypeError(f"expected {count} comma-separated pixel values")
    return tuple(int(part) for part in parts)


def _box(value: str) -> tuple[int, ...]:
    return _numbers(value, 4)


def _arrow(value: str) -> tuple[int, ...]:
    return _numbers(value, 4)


def annotate(
    source: Path,
    target: Path,
    boxes: list[tuple[int, ...]],
    arrows: list[tuple[int, ...]],
) -> None:
    """Draw boxes (x,y,width,height) and arrows (x1,y1,x2,y2) at exact pixels."""
    if not boxes and not arrows:
        raise VisualsError("provide at least one --box or --arrow")
    try:
        image_module: Any = importlib.import_module("PIL.Image")
        draw_module: Any = importlib.import_module("PIL.ImageDraw")
    except ImportError as error:
        raise VisualsError("annotate needs Pillow. Run it with uv --with 'pillow>=10'") from error
    with image_module.open(source) as opened:
        image = opened.convert("RGBA")
    width, height = image.size
    stroke = max(2, round(min(width, height) / 200))
    draw = draw_module.Draw(image)
    for x, y, box_width, box_height in boxes:
        if box_width < 1 or box_height < 1 or x + box_width > width or y + box_height > height:
            raise VisualsError(f"box {x},{y},{box_width},{box_height} is outside the image")
        draw.rectangle(
            (x, y, x + box_width - 1, y + box_height - 1), outline=ANNOTATION_COLOR, width=stroke
        )
    for x1, y1, x2, y2 in arrows:
        if max(x1, x2) >= width or max(y1, y2) >= height or (x1, y1) == (x2, y2):
            raise VisualsError(f"arrow {x1},{y1},{x2},{y2} is invalid for the image")
        draw.line((x1, y1, x2, y2), fill=ANNOTATION_COLOR, width=stroke)
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        head = stroke * 5
        base = (x2 - ux * head, y2 - uy * head)
        draw.polygon(
            [
                (x2, y2),
                (base[0] - uy * head / 2, base[1] + ux * head / 2),
                (base[0] + uy * head / 2, base[1] - ux * head / 2),
            ],
            fill=ANNOTATION_COLOR,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    marks = commands.add_parser("annotate", help="draw boxes and arrows on an image")
    marks.add_argument("source", type=Path)
    marks.add_argument("target", type=Path)
    marks.add_argument("--box", type=_box, action="append", default=[], metavar="X,Y,W,H")
    marks.add_argument("--arrow", type=_arrow, action="append", default=[], metavar="X1,Y1,X2,Y2")
    send = commands.add_parser("publish", help="push images and print commit-pinned URLs")
    send.add_argument("images", type=Path, nargs="+")
    send.add_argument("--allow-public", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "annotate":
            annotate(args.source, args.target, args.box, args.arrow)
            print(args.target)
        else:
            for url in publish(args.images, allow_public=args.allow_public):
                print(url)
    except (VisualsError, OSError) as error:
        print(f"pr-visuals: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
