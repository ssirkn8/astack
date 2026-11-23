"""The explainer's command reference must name every command a user can run."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from astack import cli

EXPLAINER = Path("docs/how-astack-works.md")
# `astack release` releases astack itself. The public export leaves it out, so the public
# explainer does not document it.
PRIVATE_COMMANDS = {"release"}


def _documented() -> str:
    text = EXPLAINER.read_text(encoding="utf-8")
    return text[text.index("## 12. Command reference") :]


def _subcommands(parser: argparse.ArgumentParser) -> set[str]:
    return {
        name
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name, choice in action.choices.items()
        if choice.description is not argparse.SUPPRESS
    }


def _runtime_parser() -> argparse.ArgumentParser:
    spec = importlib.util.spec_from_file_location("project_tool_docs", "workflow/project_tool.py")
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    parser: argparse.ArgumentParser = module._parser()
    return parser


def _help_listed(parser: argparse.ArgumentParser) -> set[str]:
    """Subcommands shown in --help, so hidden aliases are not required in the docs."""
    help_text = parser.format_help()
    return {name for name in _subcommands(parser) if re.search(rf"^\s+{name}\b", help_text, re.M)}


def test_every_astack_command_is_in_the_reference() -> None:
    reference = _documented()
    missing = {
        name
        for name in _help_listed(cli._parser()) - PRIVATE_COMMANDS
        if f"`astack {name}" not in reference
    }
    assert not missing, f"Add these astack commands to {EXPLAINER}: {sorted(missing)}"


def test_every_project_runtime_command_is_in_the_reference() -> None:
    reference = _documented()
    justfile = Path("templates/project/justfile").read_text(encoding="utf-8")
    # A command the generated justfile wraps is documented through its `just` target.
    wrapped = set(re.findall(r"\.astack/workflow\.py ([a-z][a-z-]*)", justfile))
    named = set(re.findall(r"`python \.astack/workflow\.py ([a-z][a-z-]*)", reference))
    named |= set(re.findall(r"`([a-z][a-z-]*)`", reference))
    missing = _help_listed(_runtime_parser()) - wrapped - named
    assert not missing, f"Add these runtime commands to {EXPLAINER}: {sorted(missing)}"


def test_every_project_just_recipe_is_in_the_reference() -> None:
    justfile = Path("templates/project/justfile").read_text(encoding="utf-8")
    recipes = set(re.findall(r"^([a-z][a-z0-9-]*)\b[^:\n]*:(?!=)", justfile, re.M))
    reference = _documented()
    missing = {name for name in recipes if not re.search(rf"`{re.escape(name)}[` ]", reference)}
    assert recipes and not missing, f"Add these just targets to {EXPLAINER}: {sorted(missing)}"
