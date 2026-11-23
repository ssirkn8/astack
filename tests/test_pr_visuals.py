"""PR image publishing must fail closed and never touch the working branch."""

from __future__ import annotations

import importlib.util
import stat
import struct
import subprocess
import zlib
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(".opencode/skills/pr-visuals/pr_visuals.py").resolve()


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pr_visuals", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


visuals = _load()


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(*extra: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    pixels = zlib.compress((b"\0" + b"\xff\x00\x00" * 2) * 2)
    return b"".join(
        [
            visuals.PNG_SIGNATURE,
            _chunk(b"IHDR", header),
            *extra,
            _chunk(b"IDAT", pixels),
            _chunk(b"IEND", b""),
        ]
    )


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, code: int = 0) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\nexit {code}\n", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "feat/demo", str(work)], check=True)
    _run(work, "git", "config", "user.name", "Test")
    _run(work, "git", "config", "user.email", "test@example.invalid")
    (work / "file.txt").write_text("x\n", encoding="utf-8")
    _run(work, "git", "add", ".")
    _run(work, "git", "commit", "-q", "-m", "init")
    _run(work, "git", "remote", "add", "origin", str(bare))
    monkeypatch.setattr(visuals, "github_repository", lambda _url: "acme/private-repo")
    monkeypatch.chdir(work)
    return work, bare


def test_strip_removes_metadata_and_keeps_rendering_chunks() -> None:
    dirty = _png(_chunk(b"tEXt", b"Comment\0home/someone/secret"), _chunk(b"eXIf", b"gps"))
    clean = visuals.strip_png_metadata(dirty)
    assert clean == _png()
    assert b"secret" not in clean


@pytest.mark.parametrize(
    "data",
    [
        b"GIF89a",
        _png()[:-3],
        _png() + b"trailing",
        visuals.PNG_SIGNATURE + _chunk(b"IDAT", b"x") + _chunk(b"IEND", b""),
        _png().replace(b"IDAT", b"IDAX", 1),
    ],
)
def test_strip_refuses_malformed_images(data: bytes) -> None:
    with pytest.raises(visuals.VisualsError):
        visuals.strip_png_metadata(data)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/acme/repo.git", "acme/repo"),
        ("https://github.com/acme/repo", "acme/repo"),
        ("git@github.com:acme/repo.git", "acme/repo"),
        ("ssh://git@github.com/acme/repo.git", "acme/repo"),
    ],
)
def test_parses_github_remotes(url: str, expected: str) -> None:
    assert visuals.github_repository(url) == expected


@pytest.mark.parametrize(
    "url", ["https://gitlab.com/acme/repo.git", "https://github.com.evil.test/acme/repo.git"]
)
def test_refuses_other_hosts(url: str) -> None:
    with pytest.raises(visuals.VisualsError):
        visuals.github_repository(url)


def test_publish_appends_clean_image_without_touching_worktree(
    repo: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, bare = repo
    _fake_gh(tmp_path, monkeypatch, '{"visibility":"PRIVATE"}')
    image = tmp_path / "after shot.png"
    image.write_bytes(_png(_chunk(b"tEXt", b"Comment\0secret")))
    head = _run(work, "git", "rev-parse", "HEAD")

    first = visuals.publish([image], allow_public=False, cwd=work)
    second_image = tmp_path / "before.png"
    second_image.write_bytes(_png(_chunk(b"pHYs", b"\0\0\0\1\0\0\0\1\1")))
    second = visuals.publish([second_image], allow_public=False, cwd=work)

    assert first[0].startswith("https://github.com/acme/private-repo/blob/")
    assert first[0].endswith("-after-shot.png?raw=true")
    commit = second[0].split("/blob/")[1].split("/")[0]
    tip = _run(bare, "git", "rev-parse", "refs/heads/pr-evidence")
    assert tip == commit
    files = _run(bare, "git", "ls-tree", "-r", "--name-only", tip).splitlines()
    assert len(files) == 2
    assert all(name.startswith(f"feat-demo/{head[:12]}/") for name in files)
    stored = subprocess.run(
        ["git", "show", f"{tip}:{first[0].split(tip)[-1]}"], cwd=bare, capture_output=True
    )
    assert b"secret" not in stored.stdout
    assert _run(work, "git", "rev-parse", "HEAD") == head
    assert _run(work, "git", "status", "--short") == ""


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ('{"visibility":"PUBLIC"}', 0),
        ('{"visibility":"INTERNAL"}', 0),
        ("{}", 0),
        ("not json", 0),
        ('{"visibility":"PRIVATE"}', 1),
    ],
)
def test_publish_refuses_unconfirmed_privacy(
    repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    code: int,
) -> None:
    work, bare = repo
    _fake_gh(tmp_path, monkeypatch, output, code)
    image = tmp_path / "shot.png"
    image.write_bytes(_png())
    with pytest.raises(visuals.VisualsError):
        visuals.publish([image], allow_public=False, cwd=work)
    assert _run(bare, "git", "for-each-ref") == ""


def test_public_repository_needs_explicit_approval(
    repo: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, _ = repo
    _fake_gh(tmp_path, monkeypatch, '{"visibility":"PUBLIC"}')
    image = tmp_path / "shot.png"
    image.write_bytes(_png())
    assert visuals.publish([image], allow_public=True, cwd=work)


def test_publish_refuses_symlink(
    repo: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, bare = repo
    _fake_gh(tmp_path, monkeypatch, '{"visibility":"PRIVATE"}')
    image = tmp_path / "real.png"
    image.write_bytes(_png())
    link = tmp_path / "link.png"
    link.symlink_to(image)
    with pytest.raises(visuals.VisualsError):
        visuals.publish([link], allow_public=False, cwd=work)
    assert _run(bare, "git", "for-each-ref") == ""


def test_annotate_marks_exact_pixels_and_rejects_out_of_bounds(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "in.png"
    target = tmp_path / "out.png"
    Image.new("RGB", (200, 200), "white").save(source)
    visuals.annotate(source, target, [(20, 20, 100, 50)], [(190, 190, 150, 150)])
    with Image.open(target) as marked:
        rgba = marked.convert("RGBA")
        assert rgba.getpixel((20, 20)) == visuals.ANNOTATION_COLOR
        assert rgba.getpixel((150, 150)) == visuals.ANNOTATION_COLOR
        assert rgba.getpixel((70, 45)) == (255, 255, 255, 255)
    with pytest.raises(visuals.VisualsError):
        visuals.annotate(source, target, [(190, 190, 50, 50)], [])
