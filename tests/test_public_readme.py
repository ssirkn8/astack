"""The public README stays thin, so what it does say must stay true."""

from __future__ import annotations

import re
from pathlib import Path

from astack import cli

# The source repository keeps the public README here. The public export ships it as README.md.
README = Path("publication/README.md")
if not README.exists():
    README = Path("README.md")
# Shared policy the other skills apply. Users never invoke it.
SHARED_SKILLS = {"human-decisions"}


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_relative_links_point_at_real_files_and_headings() -> None:
    text = README.read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)]+)\)", text)
    relative = [link for link in links if not re.match(r"[a-z]+:", link)]
    assert relative
    for link in relative:
        path, _, anchor = link.partition("#")
        target = Path(path)
        assert target.is_file(), link
        if anchor:
            headings = re.findall(r"^#+ (.+)$", target.read_text(encoding="utf-8"), re.M)
            assert anchor in {_slug(heading) for heading in headings}, link


def test_every_user_facing_authored_skill_is_listed() -> None:
    text = README.read_text(encoding="utf-8")
    skills = {path.parent.name for path in Path(".opencode/skills").glob("*/SKILL.md")}
    missing = {name for name in skills - SHARED_SKILLS if f"`{name}`" not in text}
    assert not missing, f"List these skills in {README}: {sorted(missing)}"


def test_commands_in_the_readme_exist() -> None:
    blocks = re.findall(r"```sh\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
    text = "".join(blocks)
    parser = cli._parser()
    commands = {
        name
        for action in parser._actions
        if hasattr(action, "choices") and isinstance(action.choices, dict)
        for name in action.choices
    }
    used = set(re.findall(r"^astack ([a-z-]+)", text, re.M))
    assert used and used <= commands, sorted(used - commands)
    recipes = set(
        re.findall(
            r"^([a-z][a-z0-9-]*)\b[^:\n]*:(?!=)",
            Path("templates/project/justfile").read_text(),
            re.M,
        )
    )
    just = set(re.findall(r"^just ([a-z-]+)", text, re.M))
    assert just and just <= recipes, sorted(just - recipes)
